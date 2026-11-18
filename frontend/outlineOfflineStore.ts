import type { LocalChapter, LocalProject, LocalPublication } from './offlineStore'
import type { ProjectSource } from './projectSyncModel'
import type { SettingsVersion } from './settingsSyncModel'
import {
  outlineObjectSignature,
  prepareOutlineVersion,
  requiredOutlineStoryRecord,
  validateOutlineActiveBasis,
  validateOutlineVersionSources,
  type OutlineArchivePending,
  type OutlineHeadObject,
  type OutlineHistory,
  type OutlinePromiseSeed,
  type OutlineSourceLookup,
  type OutlineVersion,
  type OutlineVersionObject,
  type OutlineVersionRecord,
} from './outlineSyncModel'

type OutlineHead = { branchId: string; versionId?: string; deferred?: OutlineArchivePending }
type StoredOutline = OutlineVersionRecord & { branchId: string }
type StoredSeed = { branchId: string; id: string; seed: OutlinePromiseSeed }
type ApplyOptions = { source?: ProjectSource; publicationId?: string | null; consumePublication?: boolean }
type FailCode = 'format' | 'ownership' | 'conflict' | 'missing'

export const outlineReadStores = [
  'projects',
  'chapters',
  'projectHeads',
  'settingsHeads',
  'settingsVersions',
  'outlineHeads',
  'outlineVersions',
  'outlinePromiseSeeds',
] as const
export const outlineWriteStores = [...outlineReadStores, 'projectVersions', 'publications'] as const
type StoreName = (typeof outlineWriteStores)[number]
type Tx = IDBTransaction
type Access = {
  transact<T>(stores: StoreName[], mode: IDBTransactionMode, action: (tx: Tx) => Promise<T>): Promise<T>
  result<T>(request: IDBRequest<T>): Promise<T>
  readProject(tx: Tx, projectId: string): Promise<LocalProject>
  listChaptersTx(tx: Tx, branchId: string): Promise<LocalChapter[]>
  getProjectHead(tx: Tx, branchId: string): Promise<{ versionId: string } | undefined>
  getProjectVersion(tx: Tx, branchId: string, id: string): Promise<unknown>
  getSettingsHead(tx: Tx, branchId: string): Promise<{ versionId: string } | undefined>
  getSettingsVersion(tx: Tx, branchId: string, id: string): Promise<{ version: SettingsVersion } | undefined>
  readPublication(tx: Tx, branchId: string, objectId: string): Promise<LocalPublication | undefined>
  markPublication(tx: Tx, branchId: string, ...objects: string[]): Promise<void>
  fail(code: FailCode, message: string): never
}

export function createOutlineOfflineStore({
  transact,
  result,
  readProject,
  listChaptersTx,
  getProjectHead,
  getProjectVersion,
  getSettingsHead,
  getSettingsVersion,
  readPublication,
  markPublication,
  fail,
}: Access) {
  const getHead = (tx: Tx, branchId: string) =>
    result<OutlineHead | undefined>(tx.objectStore('outlineHeads').get(branchId))
  const getVersion = (tx: Tx, branchId: string, id: string) =>
    result<StoredOutline | undefined>(tx.objectStore('outlineVersions').get([branchId, id]))
  const getSeed = (tx: Tx, branchId: string, id: string) =>
    result<StoredSeed | undefined>(tx.objectStore('outlinePromiseSeeds').get([branchId, id]))
  const listVersions = (tx: Tx, branchId: string) =>
    result<StoredOutline[]>(tx.objectStore('outlineVersions').index('byBranch').getAll(branchId))

  async function readOutlineTx(tx: Tx, project: LocalProject) {
    const branch = project.branch.id
    const [head, versions, source, settingsHead, chapters, seeds] = await Promise.all([
      getHead(tx, branch),
      listVersions(tx, branch),
      getProjectHead(tx, branch),
      getSettingsHead(tx, branch),
      listChaptersTx(tx, branch),
      result<StoredSeed[]>(tx.objectStore('outlinePromiseSeeds').index('byBranch').getAll(branch)),
    ])
    return {
      current: versions.find(({ version }) => version.id === head?.versionId),
      versions,
      projectVersionId: source?.versionId ?? null,
      settings: settingsHead
        ? ((await getSettingsVersion(tx, branch, settingsHead.versionId))?.version ?? null)
        : null,
      chapters,
      promises: seeds.map(({ id, seed }) => ({ id, title: seed.title, seed })),
      restorePending: head?.deferred,
    }
  }

  function readLocalOutline(projectId: string) {
    return transact([...outlineReadStores], 'readonly', async (tx) =>
      readOutlineTx(tx, await readProject(tx, projectId)),
    )
  }

  async function readOutlineHistoryTx(tx: Tx, project: LocalProject): Promise<OutlineHistory | undefined> {
    const branch = project.branch.id
    const [head, stored] = await Promise.all([getHead(tx, branch), listVersions(tx, branch)])
    if (!stored.length) return undefined
    const selectedId = head?.versionId ?? head?.deferred?.revisionId ?? null
    const selected = stored.find((entry) => entry.version.id === selectedId)?.version
    if (selectedId && !selected) return fail('missing', '找不到采用大纲的原始修订，无法导出。')
    const required = head?.versionId
      ? (selected?.plan.promises
          .filter(({ promise_id }) => !Object.hasOwn(selected.promise_sources, promise_id))
          .map(({ promise_id }) => promise_id) ?? [])
      : (head?.deferred?.requiredStoryRecords ?? [])
    const source_devices = Object.fromEntries(
      stored.flatMap(({ version, source, recordedDeviceId }) => {
        const device = source?.device_id ?? recordedDeviceId
        return device ? [[version.id, device]] : []
      }),
    )
    const versions: OutlineVersion[] = []
    const byId = new Map(stored.map(({ version }) => [version.id, version]))
    const seen = new Set<string>()
    for (const { version } of stored) {
      const path: OutlineVersion[] = []
      let next: OutlineVersion | undefined = version
      while (next && !seen.has(next.id)) {
        seen.add(next.id)
        path.push(next)
        next = next.parent_id ? byId.get(next.parent_id) : undefined
      }
      versions.push(...path.reverse())
    }
    return {
      adopted_revision_id: selectedId,
      versions,
      ...(Object.keys(source_devices).length ? { source_devices } : {}),
      ...(required.length ? { required_story_records: required } : {}),
    }
  }

  async function validateSources(tx: Tx, branch: string, version: OutlineVersion, adopting: boolean) {
    const lookup: OutlineSourceLookup = {
      project: (id) => getProjectVersion(tx, branch, id),
      settings: async (id) => (await getSettingsVersion(tx, branch, id))?.version,
      outline: (id) => getVersion(tx, branch, id),
    }
    if (adopting) {
      const chapters = new Map((await listChaptersTx(tx, branch)).map((chapter) => [chapter.id, chapter]))
      lookup.chapter = (id) => chapters.get(id)
      lookup.promise = async (id) => {
        const existing = await getSeed(tx, branch, id)
        return existing && { id, title: existing.seed.title, seed: existing.seed }
      }
    }
    try {
      await validateOutlineVersionSources(version, lookup)
    } catch (error) {
      if (error instanceof DOMException) throw error
      fail('missing', error instanceof Error ? error.message : '大纲依据尚未齐备。')
    }
  }

  async function assertCurrentBasis(tx: Tx, branchId: string, version: OutlineVersion) {
    const [project, settings] = await Promise.all([
      getProjectHead(tx, branchId),
      getSettingsHead(tx, branchId),
    ])
    try {
      validateOutlineActiveBasis(version, {
        projectVersionId: project?.versionId ?? null,
        settingsVersionId: settings?.versionId ?? null,
      })
    } catch (error) {
      fail('conflict', error instanceof Error ? error.message : '大纲的立项或设定依据已变化。')
    }
  }

  async function moveHead(
    tx: Tx,
    project: LocalProject,
    version: OutlineVersion,
    previous?: OutlineHead,
    recordChange = true,
  ) {
    const branchId = project.branch.id
    for (const [id, seed] of Object.entries(version.promise_sources)) {
      if (!(await getSeed(tx, branchId, id))) {
        await result(tx.objectStore('outlinePromiseSeeds').add({ branchId, id, seed } satisfies StoredSeed))
      }
    }
    const deferred = previous?.deferred?.revisionId !== version.id ? previous?.deferred : undefined
    await result(
      tx.objectStore('outlineHeads').put({
        branchId,
        versionId: version.id,
        ...(deferred ? { deferred } : {}),
      } satisfies OutlineHead),
    )
    if (recordChange) {
      await result(
        tx.objectStore('projects').put({
          ...project,
          branch: { ...project.branch, story_revision: project.branch.story_revision + 1 },
        } satisfies LocalProject),
      )
    }
  }

  function checkArchivedPromises(previous: OutlineHead | undefined, version: OutlineVersion) {
    const required = requiredOutlineStoryRecord(version, previous?.deferred)
    if (required) fail('missing', `等待原伏笔记录 ${required} 的实际进展或历史恢复。`)
  }

  async function restoreOutlineHistoryTx(
    tx: Tx,
    project: LocalProject,
    history: OutlineHistory,
  ): Promise<OutlineArchivePending | undefined> {
    const branchId = project.branch.id
    for (const version of history.versions) {
      const device =
        history.source_devices && Object.hasOwn(history.source_devices, version.id)
          ? history.source_devices[version.id]
          : undefined
      await result(
        tx.objectStore('outlineVersions').add({
          branchId,
          version,
          ...(device ? { recordedDeviceId: device } : {}),
        } satisfies StoredOutline),
      )
    }
    const revisionId = history.adopted_revision_id
    if (!revisionId) return undefined
    const selected = history.versions.find(({ id }) => id === revisionId)
    if (!selected) return fail('missing', '稿件包缺少采用的大纲修订。')
    const requiredStoryRecords = history.required_story_records ?? []
    let reason = requiredStoryRecords.length
      ? `等待原伏笔记录 ${requiredStoryRecords[0]} 的实际进展或历史恢复。`
      : ''
    if (!reason) {
      try {
        await assertCurrentBasis(tx, branchId, selected)
        await validateSources(tx, branchId, selected, true)
      } catch (error) {
        if (
          !(error instanceof Error) ||
          !('code' in error) ||
          (error.code !== 'conflict' && error.code !== 'missing')
        )
          throw error
        reason = error.message
      }
    }
    if (!reason) {
      await moveHead(tx, project, selected, undefined, false)
      return undefined
    }
    const deferred = { revisionId, requiredStoryRecords, reason }
    await result(tx.objectStore('outlineHeads').add({ branchId, deferred } satisfies OutlineHead))
    return deferred
  }

  async function confirmLocalOutline(projectId: string, base: string | null, draft: OutlineVersion) {
    const version = prepareOutlineVersion(draft)
    if (version.parent_id !== base) return fail('conflict', '大纲父版本和当前确认依据不一致。')
    return transact([...outlineWriteStores], 'readwrite', async (tx) => {
      const project = await readProject(tx, projectId)
      const branch = project.branch.id
      const previous = await getHead(tx, branch)
      if ((previous?.versionId ?? null) !== base) fail('conflict', '本机已有另一版确认大纲，请重新核对。')
      checkArchivedPromises(previous, version)
      await assertCurrentBasis(tx, branch, version)
      await validateSources(tx, branch, version, true)
      await result(
        tx.objectStore('outlineVersions').add({ branchId: branch, version } satisfies StoredOutline),
      )
      await moveHead(tx, project, version, previous)
      await markPublication(tx, branch, `outline-version:${version.id}`, `outline:${branch}`)
    })
  }

  function applyOutlineObject(
    projectId: string,
    object: OutlineHeadObject | OutlineVersionObject,
    expected: string,
    options: ApplyOptions,
  ) {
    return transact([...outlineWriteStores], 'readwrite', async (tx) => {
      const project = await readProject(tx, projectId)
      const branch = project.branch.id
      if (object.branch_id !== branch) fail('ownership', '大纲范围与工作版本不符。')
      if (object.kind === 'outline-version') {
        const version = object.version
        const existing = await getVersion(tx, branch, version.id)
        if (
          existing &&
          outlineObjectSignature({ ...object, version: existing.version }) !== outlineObjectSignature(object)
        )
          fail('conflict', '大纲同一源修订保存了不同内容。')
        await validateSources(tx, branch, version, false)
        const { source } = options
        if (source && (!existing?.source || source.sequence < existing.source.sequence)) {
          await result(
            tx
              .objectStore('outlineVersions')
              .put({ ...existing, branchId: branch, version, source } satisfies StoredOutline),
          )
        }
        return !!existing || !!source
      }
      const head = await getHead(tx, branch)
      const before = head?.versionId ? { ...object, revision_id: head.versionId } : null
      const publication = await readPublication(tx, branch, `outline:${branch}`)
      if (
        outlineObjectSignature(before) !== expected ||
        (options.publicationId !== undefined && options.publicationId !== (publication?.id ?? null))
      )
        return false
      const chosen = await getVersion(tx, branch, object.revision_id)
      if (!chosen?.source) return fail('missing', '缺少云端采用的大纲来源。')
      checkArchivedPromises(head, chosen.version)
      await assertCurrentBasis(tx, branch, chosen.version)
      await validateSources(tx, branch, chosen.version, true)
      if (head?.versionId !== chosen.version.id) await moveHead(tx, project, chosen.version, head)
      if (options.consumePublication && publication)
        await result(tx.objectStore('publications').delete([branch, publication.objectId]))
      return true
    })
  }

  return {
    readLocalOutline,
    confirmLocalOutline,
    readOutlineTx,
    applyOutlineObject,
    readOutlineHistoryTx,
    restoreOutlineHistoryTx,
  }
}
