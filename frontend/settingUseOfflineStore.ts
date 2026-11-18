import type { LocalChapter, LocalProject, LocalPublication, LocalRevision } from './offlineStore'
import { outlineChapterPlans, type OutlineVersion } from './outlineSyncModel'
import type { ProjectSource } from './projectSyncModel'
import type { SettingsVersion } from './settingsSyncModel'
import { compactJSON, verifyStoryEvidence } from './storySyncModel'
import {
  buildSettingUseVersion,
  plannedSettingUseItems,
  settingUseObjectSignature,
  settingUseSlotKey,
  settingUseVersionSlot,
  validateSettingUseVersionSources,
  type SettingUseDraft,
  type SettingUseEntry,
  type SettingUseHistory,
  type SettingUseRecord,
  type SettingUseSyncObject,
  type SettingUseVersion,
} from './settingUseSyncModel'

type Tx = IDBTransaction
type Fail = 'format' | 'ownership' | 'missing' | 'conflict'
type Head = { branchId: string; chapterId: string; slotId: string; versionId: string }
type Stored = {
  branchId: string
  chapterId: string
  slotId: string
  entry: SettingUseEntry
  source?: ProjectSource
}
type ApplyOptions = { source?: ProjectSource; publicationId?: string | null; consumePublication?: boolean }
type ChapterBasis = Pick<LocalRevision, 'id' | 'chapter_id' | 'revision' | 'sha256'>
type PublicBasis = { outlineVersionId: string; settingsVersionId: string; revision: ChapterBasis }

export const settingUseStores = ['settingUseHeads', 'settingUseVersions'] as const
export const settingUseReadStores = [
  'projects',
  'chapters',
  'revisions',
  'sourceRevisions',
  'storyChapterAliases',
  'projectHeads',
  'settingsHeads',
  'settingsVersions',
  'outlineHeads',
  'outlineVersions',
  ...settingUseStores,
] as const
const writeStores = [...settingUseReadStores, 'publications'] as const

type Access = {
  transact<T>(
    stores: (typeof writeStores)[number][],
    mode: IDBTransactionMode,
    action: (tx: Tx) => Promise<T>,
  ): Promise<T>
  result<T>(request: IDBRequest<T>): Promise<T>
  readProject(tx: Tx, projectId: string): Promise<LocalProject>
  projectHead(tx: Tx, branch: string): Promise<{ versionId: string } | undefined>
  settingsHead(tx: Tx, branch: string): Promise<{ versionId: string } | undefined>
  outlineHead(tx: Tx, branch: string): Promise<{ versionId?: string } | undefined>
  settingsVersion(tx: Tx, branch: string, id: string): Promise<{ version: SettingsVersion } | undefined>
  outlineVersion(tx: Tx, branch: string, id: string): Promise<{ version: OutlineVersion } | undefined>
  sourceRevision(tx: Tx, branch: string, id: string): Promise<LocalRevision | undefined>
  adoptedRevision(
    tx: Tx,
    branch: string,
    id: string,
  ): Promise<{ original: LocalRevision; native: LocalRevision } | undefined>
  rememberAdoptedRevision(
    tx: Tx,
    branch: string,
    original: LocalRevision,
    native: LocalRevision,
  ): Promise<void>
  readPublication(tx: Tx, branchId: string, objectId: string): Promise<LocalPublication | undefined>
  markPublication(tx: Tx, branchId: string, ...objects: string[]): Promise<void>
  hash(text: string): Promise<string>
  fail(code: Fail, message: string): never
}

function hasProse(text: string) {
  return !!text.replace(/[\p{White_Space}\x1c-\x1f]/gu, '')
}

function sameRevision(left: LocalRevision | undefined, right: LocalRevision) {
  return (
    left?.id === right.id &&
    left.chapter_id === right.chapter_id &&
    left.sha256 === right.sha256 &&
    left.content === right.content
  )
}

function nativeMatch(original: LocalRevision, native?: LocalRevision) {
  return (
    native?.chapter_id === original.chapter_id &&
    native.sha256 === original.sha256 &&
    native.content === original.content
  )
}

export function createSettingUseOfflineStore(access: Access) {
  const { transact, result, fail, hash } = access
  const byBranch = <T>(tx: Tx, store: (typeof settingUseStores)[number], branch: string) =>
    result<T[]>(tx.objectStore(store).index('byBranch').getAll(branch))
  const byChapter = <T>(tx: Tx, store: (typeof settingUseStores)[number], branch: string, chapter: string) =>
    result<T[]>(tx.objectStore(store).index('byChapter').getAll([branch, chapter]))
  const entry = (tx: Tx, branch: string, id: string) =>
    result<Stored | undefined>(tx.objectStore('settingUseVersions').get([branch, id]))
  const head = (tx: Tx, branch: string, slot: string) =>
    result<Head | undefined>(tx.objectStore('settingUseHeads').get([branch, slot]))
  const chapter = (tx: Tx, id: string) => result<LocalChapter | undefined>(tx.objectStore('chapters').get(id))

  async function activeContext(tx: Tx, branch: string) {
    const [project, settingsHead, outlineHead] = await Promise.all([
      access.projectHead(tx, branch),
      access.settingsHead(tx, branch),
      access.outlineHead(tx, branch),
    ])
    if (!settingsHead || !outlineHead?.versionId) return undefined
    const [settings, outline] = await Promise.all([
      access.settingsVersion(tx, branch, settingsHead.versionId),
      access.outlineVersion(tx, branch, outlineHead.versionId),
    ])
    if (
      !settings ||
      !outline ||
      settings.version.project_version_id !== (project?.versionId ?? null) ||
      outline.version.project_version_id !== (project?.versionId ?? null) ||
      outline.version.settings_version_id !== settings.version.id
    )
      return undefined
    return { settings: settings.version, outline: outline.version }
  }

  async function currentRevision(tx: Tx, branch: string, chapterId: string) {
    const live = await chapter(tx, chapterId)
    if (live?.branch_id !== branch || live.archived || live.stale) return undefined
    const saved = await result<LocalRevision | undefined>(
      tx.objectStore('revisions').index('byRevision').get([chapterId, live.revision]),
    )
    return saved && hasProse(saved.content) ? saved : undefined
  }

  async function readPresentation(tx: Tx, branch: string, row: Stored) {
    const { version } = row.entry
    const [original, adopted, settings] = await Promise.all([
      access.sourceRevision(tx, branch, version.evidence.revision_id),
      access.adoptedRevision(tx, branch, version.evidence.revision_id),
      access.settingsVersion(tx, branch, version.settings_version_id),
    ])
    const title =
      settings?.version.categories.flatMap(({ items }) => items).find(({ id }) => id === version.item_id)
        ?.title ?? version.item_id
    return { entry: row.entry, original, adopted: adopted?.native, title }
  }

  type Context = Awaited<ReturnType<typeof activeContext>>
  type Presentation = Awaited<ReturnType<typeof readPresentation>>

  async function present(
    row: Presentation,
    selectedId: string | undefined,
    context: Context,
    live: LocalRevision | undefined,
  ): Promise<SettingUseRecord> {
    const { version } = row.entry
    let quote: string | null = null
    if (row.original?.sha256 === version.chapter_sha256) {
      try {
        const verified = await verifyStoryEvidence(version.evidence, row.original)
        if (hasProse(verified)) quote = verified
      } catch {
        // Leave the historical record readable while the original is missing or fails verification.
      }
    }
    const isHead = selectedId === version.id
    const correctBasis =
      context?.outline.id === version.outline_version_id &&
      context.settings.id === version.settings_version_id
    const current =
      !!row.original && nativeMatch(row.original, row.adopted) && row.adopted?.id === live?.id && correctBasis
    return {
      entry: row.entry,
      title: row.title,
      quote,
      canWithdraw: isHead && version.action === 'confirmed',
      status: !isHead
        ? 'superseded'
        : version.action === 'withdrawn'
          ? 'withdrawn'
          : quote === null
            ? 'missing'
            : current
              ? 'current'
              : 'outdated',
    }
  }

  async function readLocalSettingUse(projectId: string, chapterId: string) {
    const snapshot = await transact([...settingUseReadStores], 'readonly', async (tx) => {
      const branch = (await access.readProject(tx, projectId)).branch.id
      if ((await chapter(tx, chapterId))?.branch_id !== branch) fail('ownership', '章节不属于当前工作版本。')
      const [context, live, rows, heads] = await Promise.all([
        activeContext(tx, branch),
        currentRevision(tx, branch, chapterId),
        byChapter<Stored>(tx, 'settingUseVersions', branch, chapterId),
        byChapter<Head>(tx, 'settingUseHeads', branch, chapterId),
      ])
      const records = await Promise.all(
        rows
          .sort((a, b) => b.entry.created - a.entry.created)
          .map(async (row) => ({
            proof: await readPresentation(tx, branch, row),
            selectedId: heads.find(({ slotId }) => slotId === row.slotId)?.versionId,
          })),
      )
      const selectedByItem = new Map(
        rows
          .filter(({ slotId, entry }) =>
            heads.some((head) => head.slotId === slotId && head.versionId === entry.version.id),
          )
          .map(({ entry: { version } }) => [
            settingUseSlotKey(chapterId, version.settings_version_id, version.item_id),
            version.id,
          ]),
      )
      const options =
        context && live
          ? plannedSettingUseItems(context.settings, context.outline, chapterId).map((item) => ({
              id: item.id,
              title: item.title,
              category: item.category_title!,
              headId: selectedByItem.get(settingUseSlotKey(chapterId, context.settings.id, item.id)) ?? null,
            }))
          : []
      const basis: PublicBasis | undefined = context &&
        live && {
          outlineVersionId: context.outline.id,
          settingsVersionId: context.settings.id,
          revision: {
            id: live.id,
            chapter_id: live.chapter_id,
            revision: live.revision,
            sha256: live.sha256,
          },
        }
      return { context, live, records, basis, options }
    })
    return {
      basis: snapshot.basis,
      options: snapshot.options,
      records: await Promise.all(
        snapshot.records.map(({ proof, selectedId }) =>
          present(proof, selectedId, snapshot.context, snapshot.live),
        ),
      ),
    }
  }

  async function readLocalOutlineSettingUse(projectId: string) {
    const snapshot = await transact([...settingUseReadStores], 'readonly', async (tx) => {
      const branch = (await access.readProject(tx, projectId)).branch.id
      const [context, heads] = await Promise.all([
        activeContext(tx, branch),
        byBranch<Head>(tx, 'settingUseHeads', branch),
      ])
      if (!context) return undefined
      const selected = new Map<string, Stored>()
      for (const pointer of heads) {
        const row = await entry(tx, branch, pointer.versionId)
        if (row)
          selected.set(
            settingUseSlotKey(
              row.chapterId,
              row.entry.version.settings_version_id,
              row.entry.version.item_id,
            ),
            row,
          )
      }
      const rows = []
      for (const plan of outlineChapterPlans(context.outline.plan)) {
        const chapterId = plan.chapter_id
        const location = await chapter(tx, chapterId)
        if (location?.branch_id !== branch || location.archived) continue
        const live = await currentRevision(tx, branch, chapterId)
        for (const item of plannedSettingUseItems(context.settings, context.outline, chapterId)) {
          const row = selected.get(settingUseSlotKey(chapterId, context.settings.id, item.id))
          rows.push({
            chapterId,
            chapterTitle: location.title,
            itemId: item.id,
            title: item.title,
            live,
            proof: row ? await readPresentation(tx, branch, row) : undefined,
          })
        }
      }
      return { context, rows }
    })
    if (!snapshot) return []
    return Promise.all(
      snapshot.rows.map(async ({ proof, live, ...row }) => ({
        ...row,
        proof: proof ? await present(proof, proof.entry.version.id, snapshot.context, live) : undefined,
      })),
    )
  }

  async function gather(tx: Tx, branch: string, version: SettingUseVersion) {
    const [parent, revision, settings, outline] = await Promise.all([
      version.parent_id ? entry(tx, branch, version.parent_id) : undefined,
      access.sourceRevision(tx, branch, version.evidence.revision_id),
      access.settingsVersion(tx, branch, version.settings_version_id),
      access.outlineVersion(tx, branch, version.outline_version_id),
    ])
    return { parent: parent?.entry.version, revision, settings: settings?.version, outline: outline?.version }
  }

  type Gathered = Awaited<ReturnType<typeof gather>>

  async function verified(projectId: string, branch: string, version: SettingUseVersion) {
    const checked = await transact([...settingUseReadStores], 'readonly', async (tx) => {
      if ((await access.readProject(tx, projectId)).branch.id !== branch)
        fail('ownership', '证据不属于当前工作版本。')
      return gather(tx, branch, version)
    })
    try {
      await validateSettingUseVersionSources(version, {
        use: () => checked.parent,
        revision: () => checked.revision,
        settings: () => checked.settings,
        outline: () => checked.outline,
      })
    } catch (error) {
      fail('missing', error instanceof Error ? error.message : '设定使用证据的依赖尚未齐备。')
    }
    return checked
  }

  async function recheck(tx: Tx, branch: string, version: SettingUseVersion, checked: Gathered) {
    const current = await gather(tx, branch, version)
    if (
      !checked.revision ||
      !sameRevision(current.revision, checked.revision) ||
      current.parent?.id !== checked.parent?.id ||
      current.settings?.id !== checked.settings?.id ||
      current.outline?.id !== checked.outline?.id
    )
      fail('conflict', '证据依据已有变化，请重新读取。')
  }

  async function moveHead(tx: Tx, branch: string, slotId: string, version: SettingUseVersion) {
    await result(
      tx.objectStore('settingUseHeads').put({
        branchId: branch,
        slotId,
        chapterId: version.evidence.chapter_id,
        versionId: version.id,
      } satisfies Head),
    )
  }

  async function writeAuthor(
    projectId: string,
    chapterId: string,
    makeDraft: (tx: Tx, branch: string) => Promise<SettingUseDraft>,
  ) {
    const { branch, draft } = await transact([...settingUseReadStores], 'readonly', async (tx) => {
      const branch = (await access.readProject(tx, projectId)).branch.id
      if ((await chapter(tx, chapterId))?.branch_id !== branch) fail('ownership', '章节不属于当前工作版本。')
      return { branch, draft: await makeDraft(tx, branch) }
    })
    if (draft.evidence.chapter_id !== chapterId) fail('ownership', '证据选区不属于当前章节。')
    const version = await buildSettingUseVersion(branch, draft, hash)
    const slotId = await settingUseVersionSlot(branch, version, hash)
    const checked = await verified(projectId, branch, version)
    return transact([...writeStores], 'readwrite', async (tx) => {
      if (
        (await access.readProject(tx, projectId)).branch.id !== branch ||
        (await chapter(tx, chapterId))?.branch_id !== branch
      )
        fail('ownership', '章节不属于当前工作版本。')
      const selected = await head(tx, branch, slotId)
      if (selected?.versionId === version.id) return version
      if ((selected?.versionId ?? null) !== version.parent_id)
        fail('conflict', '这条使用证据已有新版本，请重新读取比较。')
      if (version.action === 'confirmed') {
        const [context, saved] = await Promise.all([
          activeContext(tx, branch),
          currentRevision(tx, branch, chapterId),
        ])
        if (
          context?.outline.id !== version.outline_version_id ||
          context.settings.id !== version.settings_version_id ||
          saved?.id !== version.evidence.revision_id ||
          saved.sha256 !== version.chapter_sha256
        )
          fail('conflict', '已保存章稿、确认设定或大纲已变化，请重新选择正文原文。')
      }
      await recheck(tx, branch, version, checked)
      const existing = await entry(tx, branch, version.id)
      if (!existing)
        await result(
          tx.objectStore('settingUseVersions').add({
            branchId: branch,
            chapterId,
            slotId,
            entry: { version, created: Date.now() / 1000 },
          } satisfies Stored),
        )
      await moveHead(tx, branch, slotId, version)
      await access.markPublication(
        tx,
        branch,
        ...(!existing?.source ? [`setting-use-version:${version.id}`] : []),
        `setting-use:${slotId}`,
      )
      return version
    })
  }

  function confirmLocalSettingUse(
    projectId: string,
    chapterId: string,
    input: Omit<SettingUseDraft, 'actor' | 'action'>,
  ) {
    return writeAuthor(projectId, chapterId, async () => ({ ...input, action: 'confirmed', actor: 'author' }))
  }

  function withdrawLocalSettingUse(projectId: string, chapterId: string, versionId: string) {
    return writeAuthor(projectId, chapterId, async (tx, branch) => {
      const parent = await entry(tx, branch, versionId)
      if (!parent || parent.chapterId !== chapterId || parent.entry.version.action !== 'confirmed')
        return fail('missing', '找不到本章可撤回的确认记录。')
      const { id, ...draft } = parent.entry.version
      return { ...draft, parent_id: id, action: 'withdrawn' }
    })
  }

  async function readSettingUseHistoryTx(tx: Tx, branch: string): Promise<SettingUseHistory | undefined> {
    const [heads, versions] = await Promise.all([
      byBranch<Head>(tx, 'settingUseHeads', branch),
      byBranch<Stored>(tx, 'settingUseVersions', branch),
    ])
    if (!versions.length) return undefined
    const bindings: [string, string][] = []
    for (const id of new Set(versions.map(({ entry }) => entry.version.evidence.revision_id))) {
      const proof = await access.adoptedRevision(tx, branch, id)
      if (proof && proof.native.id !== id && nativeMatch(proof.original, proof.native))
        bindings.push([id, proof.native.id])
    }
    return {
      adopted_heads: Object.fromEntries(heads.map(({ slotId, versionId }) => [slotId, versionId])),
      versions: versions.map(({ entry, source }) =>
        source ? { ...entry, source_device: source.device_id } : entry,
      ),
      ...(bindings.length ? { native_bindings: Object.fromEntries(bindings) } : {}),
    }
  }

  async function restoreSettingUseHistoryTx(
    tx: Tx,
    branch: string,
    history: SettingUseHistory,
    slots: ReadonlyMap<string, string>,
  ) {
    for (const [source, target] of Object.entries(history.native_bindings ?? {})) {
      const [original, native] = await Promise.all([
        access.sourceRevision(tx, branch, source),
        result<LocalRevision | undefined>(tx.objectStore('revisions').get(target)),
      ])
      if (!original || !native || !nativeMatch(original, native))
        return fail('format', '正文采用关系无法恢复。')
      await access.rememberAdoptedRevision(tx, branch, original, native)
    }
    for (const source of history.versions) {
      const slotId = slots.get(source.version.id)
      if (!slotId) return fail('format', '设定使用历史缺少版本归属。')
      await result(
        tx.objectStore('settingUseVersions').add({
          branchId: branch,
          chapterId: source.version.evidence.chapter_id,
          slotId,
          entry: source,
        } satisfies Stored),
      )
      if (history.adopted_heads[slotId] === source.version.id)
        await moveHead(tx, branch, slotId, source.version)
    }
  }

  async function applySettingUseObject(
    projectId: string,
    object: SettingUseSyncObject,
    expected: string,
    options: ApplyOptions,
  ) {
    const branch = object.branch_id
    const selected =
      object.kind === 'setting-use-version'
        ? object.version
        : await transact(
            [...settingUseReadStores],
            'readonly',
            async (tx) => (await entry(tx, branch, object.revision_id))?.entry.version,
          )
    if (!selected) return fail('missing', '设定使用采用头缺少云端正式版本，将延后接收。')
    const slot = await settingUseVersionSlot(branch, selected, hash)
    const checked = await verified(projectId, branch, selected)
    return transact([...writeStores], 'readwrite', async (tx) => {
      if ((await access.readProject(tx, projectId)).branch.id !== branch)
        fail('ownership', '证据不属于当前工作版本。')
      await recheck(tx, branch, selected, checked)
      const previous = await entry(tx, branch, selected.id)
      if (object.kind === 'setting-use-version') {
        if (previous && compactJSON(previous.entry.version) !== compactJSON(selected))
          fail('conflict', '同一设定使用修订的内容不同。')
        if (options.source && (!previous?.source || options.source.sequence < previous.source.sequence))
          await result(
            tx.objectStore('settingUseVersions').put({
              branchId: branch,
              chapterId: selected.evidence.chapter_id,
              slotId: slot,
              entry: previous?.entry ?? { version: selected, created: Date.now() / 1000 },
              source: options.source,
            } satisfies Stored),
          )
        return !!previous || !!options.source
      }
      if (slot !== object.slot_id) fail('ownership', '设定使用采用头与证据归属不一致。')
      if (!previous?.source) return fail('missing', '缺少云端正式版本的回执，将延后接收。')
      const pointer = await head(tx, branch, slot)
      const before = pointer ? { ...object, revision_id: pointer.versionId } : null
      const objectId = `setting-use:${slot}`
      const publication = await access.readPublication(tx, branch, objectId)
      if (
        settingUseObjectSignature(before) !== expected ||
        (options.publicationId !== undefined && options.publicationId !== (publication?.id ?? null))
      )
        return false
      await moveHead(tx, branch, slot, selected)
      if (options.consumePublication && publication)
        await result(tx.objectStore('publications').delete([branch, objectId]))
      return true
    })
  }

  return {
    readLocalSettingUse,
    readLocalOutlineSettingUse,
    confirmLocalSettingUse,
    withdrawLocalSettingUse,
    readSettingUseVersionTx: entry,
    readSettingUseHistoryTx,
    restoreSettingUseHistoryTx,
    applySettingUseObject,
  }
}
