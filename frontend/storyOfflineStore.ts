import type { LocalChapter, LocalProject, LocalPublication, LocalRevision } from './offlineStore'
import type { OutlineExistingPromise, OutlinePromiseSeed } from './outlineSyncModel'
import type { ProjectSource } from './projectSyncModel'
import {
  allStoryEvidence,
  buildStoryVersion,
  orderStoryVersions,
  parseStoryRecord,
  storyObjectSignature,
  storyReferences,
  validateStoryHeadSelection,
  validateStoryNativeBindings,
  validateStoryVersion,
  validateStoryVersionSources,
  type StoryHeadObject,
  type StoryHistory,
  type StoryNativeRevision,
  type StoryVersion,
  type StoryVersionObject,
  type StoryVersionRecord,
  type StoryVersionDraft,
} from './storySyncModel'

export const storyReadStores = [
  'projects',
  'chapters',
  'revisions',
  'sourceRevisions',
  'storyVersions',
  'storyHeads',
  'storyHistory',
  'storyChapterAliases',
  'outlinePromiseSeeds',
] as const
export const storyWriteStores = [...storyReadStores, 'publications'] as const
type Store = (typeof storyWriteStores)[number]
type Tx = IDBTransaction
type StoredVersion = StoryVersionRecord & { branchId: string }
type StoredHistory = StoryNativeRevision & { branchId: string; recordId: string }
type Head = { branchId: string; recordId: string; versionId: string; revision: number }
type Alias = { branchId: string; sourceId: string; nativeId: string }
type Options = { source?: ProjectSource; publicationId?: string | null; consumePublication?: boolean }
type FailCode = 'format' | 'ownership' | 'conflict' | 'missing'
type Access = {
  transact<T>(stores: Store[], mode: IDBTransactionMode, action: (tx: Tx) => Promise<T>): Promise<T>
  result<T>(request: IDBRequest<T>): Promise<T>
  readProject(tx: Tx, projectId: string): Promise<LocalProject>
  sourceRevision(tx: Tx, branchId: string, id: string): Promise<LocalRevision | undefined>
  readPublication(tx: Tx, branchId: string, objectId: string): Promise<LocalPublication | undefined>
  markPublication(tx: Tx, branchId: string, ...objects: string[]): Promise<void>
  fail(code: FailCode, message: string): never
}
export type ConfirmOfflineStory = Pick<
  StoryVersionDraft,
  'record' | 'record_id' | 'archived' | 'evidence'
> & {
  baseStoryRevision: number
  baseVersionId: string | null
}

type NativeProof = { original: LocalRevision; native: LocalRevision }
const adoptedHead = (branch_id: string, record_id: string, revision_id: string): StoryHeadObject => ({
  format: 'ainovel-manuscript-sync/1',
  kind: 'story-head',
  branch_id,
  record_id,
  revision_id,
})

function plannedOnly(version: StoryVersion | undefined): OutlinePromiseSeed | undefined {
  if (
    !version ||
    version.archived ||
    version.record.kind !== 'promise' ||
    version.record.depends_on.length ||
    version.record.stages.length !== 1 ||
    allStoryEvidence(version).length
  )
    return
  const [stage] = version.record.stages
  if (stage.status === 'planned' && !stage.event_id) {
    return {
      ...version.record,
      depends_on: [],
      stages: [{ ...stage, status: 'planned', event_id: stage.event_id === '' ? '' : null }],
    }
  }
}

export function createStoryOfflineStore({
  transact,
  result,
  readProject,
  sourceRevision,
  readPublication,
  markPublication,
  fail,
}: Access) {
  const getSource = (tx: Tx, branch: string, id: string) =>
    result<StoredVersion | undefined>(tx.objectStore('storyVersions').get([branch, id]))
  const getHead = (tx: Tx, branch: string, recordId: string) =>
    result<Head | undefined>(tx.objectStore('storyHeads').get([branch, recordId]))
  const getSeed = (tx: Tx, branch: string, id: string) =>
    result<{ branchId: string; id: string; seed: OutlinePromiseSeed } | undefined>(
      tx.objectStore('outlinePromiseSeeds').get([branch, id]),
    )
  const byBranch = <T>(tx: Tx, name: 'storyVersions' | 'storyHeads' | 'storyHistory', branch: string) =>
    result<T[]>(tx.objectStore(name).index('byBranch').getAll(branch))
  const readChapter = (tx: Tx, id: string) =>
    result<LocalChapter | undefined>(tx.objectStore('chapters').get(id))

  async function readNative(tx: Tx, branch: string, id: string) {
    const revision = await result<LocalRevision | undefined>(tx.objectStore('revisions').get(id))
    if (!revision) return
    const chapter = await readChapter(tx, revision.chapter_id)
    if (chapter?.branch_id === branch) return { chapter, revision }
  }

  async function rememberStoryChapterAlias(
    tx: Tx,
    branch: string,
    source: LocalRevision,
    native: LocalRevision,
  ) {
    if (
      source.id === native.id ||
      source.chapter_id !== native.chapter_id ||
      source.sha256 !== native.sha256 ||
      source.content !== native.content
    )
      return
    const original = await sourceRevision(tx, branch, source.id)
    const adopted = await readNative(tx, branch, native.id)
    if (
      original?.sha256 !== source.sha256 ||
      original.content !== source.content ||
      adopted?.revision.sha256 !== native.sha256 ||
      adopted.revision.content !== native.content
    )
      return
    await result(
      tx.objectStore('storyChapterAliases').put({
        branchId: branch,
        sourceId: source.id,
        nativeId: native.id,
      } satisfies Alias),
    )
  }

  async function readNativeProof(tx: Tx, branch: string, sourceId: string): Promise<NativeProof | undefined> {
    const original = await sourceRevision(tx, branch, sourceId)
    if (!original) return
    const direct = await readNative(tx, branch, sourceId)
    if (direct) return { original, native: direct.revision }
    const rows = await result<Alias[]>(
      tx.objectStore('storyChapterAliases').index('bySource').getAll([branch, sourceId]),
    )
    let chosen: LocalRevision | undefined
    for (const row of rows) {
      const native = (await readNative(tx, branch, row.nativeId))?.revision
      if (
        native?.chapter_id === original.chapter_id &&
        native.sha256 === original.sha256 &&
        native.content === original.content &&
        (!chosen || native.revision > chosen.revision)
      )
        chosen = native
    }
    if (chosen) return { original, native: chosen }
  }

  async function recordsTx(tx: Tx, branch: string) {
    const heads = await byBranch<Head>(tx, 'storyHeads', branch)
    const records = new Map<string, StoryVersion>()
    for (const head of heads) {
      const row = await getSource(tx, branch, head.versionId)
      if (!row || row.version.record_id !== head.recordId)
        return fail('missing', '故事采用头缺少对应正式修订。')
      records.set(head.recordId, row.version)
    }
    return { heads, records }
  }

  async function readStoryTx(tx: Tx, project: LocalProject) {
    const branchId = project.branch.id
    const [versions, { heads, records }, history, planned] = await Promise.all([
      byBranch<StoredVersion>(tx, 'storyVersions', branchId),
      recordsTx(tx, branchId),
      byBranch<StoredHistory>(tx, 'storyHistory', branchId),
      result<{ id: string; seed: OutlinePromiseSeed }[]>(
        tx.objectStore('outlinePromiseSeeds').index('byBranch').getAll(branchId),
      ),
    ])
    const nativeIds = new Map(history.map((entry) => [`${entry.recordId}/${entry.revision}`, entry]))
    const results = []
    const invalid = new Map<string, string[]>()
    for (const head of heads) {
      const version = records.get(head.recordId)!
      const local = nativeIds.get(`${head.recordId}/${head.revision}`)
      if (!local || local.version_id !== version.id) return fail('missing', '故事采用头缺少本机历史。')
      const errors: string[] = []
      if (
        Object.entries(version.dependencies).some(
          ([id, frozen]) => records.get(id)?.id !== frozen || records.get(id)?.archived,
        )
      )
        errors.push('关联记录版本已变化或已归档。')
      for (const id of [local.evidence.record, ...local.evidence.stages].flat()) {
        const adopted = await readNative(tx, branchId, id)
        if (
          !adopted ||
          adopted.chapter.archived ||
          adopted.chapter.stale ||
          adopted.chapter.revision !== adopted.revision.revision
        ) {
          errors.push('来源章节已有变化或待复检。')
          break
        }
      }
      if (errors.length) invalid.set(head.recordId, errors)
      results.push({ head, version, native: local, invalidReasons: errors })
    }
    let discovered = true
    while (discovered) {
      discovered = false
      for (const result of results) {
        if (invalid.has(result.head.recordId)) continue
        if (Object.keys(result.version.dependencies).some((id) => invalid.has(id))) {
          result.invalidReasons.push('上游记录的来源或关联待复检。')
          invalid.set(result.head.recordId, result.invalidReasons)
          discovered = true
        }
      }
    }
    return {
      branchId,
      storyRevision: project.branch.story_revision,
      records: results,
      versions,
      history,
      planned: planned.filter(({ id }) => !records.has(id)),
    }
  }

  function readLocalStory(projectId: string) {
    return transact([...storyReadStores], 'readonly', async (tx) =>
      readStoryTx(tx, await readProject(tx, projectId)),
    )
  }

  async function readStoryOutlinePromisesTx(
    tx: Tx,
    branch: string,
  ): Promise<(OutlineExistingPromise & { advanced: boolean })[]> {
    const { heads, records } = await recordsTx(tx, branch)
    const resultRows = []
    for (const head of heads) {
      const version = records.get(head.recordId)!
      if (version.record.kind !== 'promise') continue
      const initial = await result<StoredHistory | undefined>(
        tx.objectStore('storyHistory').get([branch, head.recordId, 0]),
      )
      let sourceVersion = initial ? (await getSource(tx, branch, initial.version_id))?.version : undefined
      while (sourceVersion && !plannedOnly(sourceVersion) && sourceVersion.parent_id) {
        sourceVersion = (await getSource(tx, branch, sourceVersion.parent_id))?.version
      }
      const seed = plannedOnly(sourceVersion)
      resultRows.push({
        id: head.recordId,
        title: version.record.title,
        archived: version.archived,
        seed,
        advanced: !version.archived && (head.revision > 0 || !plannedOnly(version)),
      })
    }
    return resultRows
  }

  async function gather(tx: Tx, branch: string, version: StoryVersion, adopted: boolean) {
    const sources = new Map<string, StoryVersion>()
    const revisions = new Map<string, LocalRevision>()
    const proof = new Map<string, NativeProof>()
    const required = new Set([version.parent_id, ...Object.values(version.dependencies)])
    for (const id of required) {
      if (!id) continue
      const row = await getSource(tx, branch, id)
      if (row) sources.set(id, row.version)
    }
    for (const id of new Set(allStoryEvidence(version).map(({ revision_id }) => revision_id))) {
      const original = await sourceRevision(tx, branch, id)
      if (original) revisions.set(id, original)
      if (adopted) {
        const chosen = await readNativeProof(tx, branch, id)
        if (chosen) proof.set(id, chosen)
      }
    }
    return { branch, sources, revisions, proof }
  }

  type Gathered = Awaited<ReturnType<typeof gather>>

  function bindNative(version: StoryVersion, gathered: Gathered): StoryNativeRevision['evidence'] {
    const mapGroup = (items: StoryVersion['evidence']['record']) =>
      items.map(
        ({ revision_id }) =>
          gathered.proof.get(revision_id)?.native.id ?? fail('missing', '原始章稿尚未在本机采用。'),
      )
    return { record: mapGroup(version.evidence.record), stages: version.evidence.stages.map(mapGroup) }
  }

  async function verifyGathered(version: StoryVersion, gathered: Gathered, adopted: boolean) {
    try {
      await validateStoryVersionSources(version, {
        story: (id) => gathered.sources.get(id),
        revision: (id) => gathered.revisions.get(id),
      })
      if (adopted) {
        const evidence = bindNative(version, gathered)
        const native = new Map([...gathered.proof.values()].map((proof) => [proof.native.id, proof.native]))
        await validateStoryNativeBindings(
          version,
          {
            version_id: version.id,
            revision: 0,
            created: 0,
            dependencies: {},
            evidence,
          },
          { original: (id) => gathered.revisions.get(id), native: (id) => native.get(id) },
        )
      }
    } catch (error) {
      fail('missing', error instanceof Error ? error.message : '故事记录的来源尚未齐备。')
    }
  }

  async function recheck(tx: Tx, branch: string, gathered: Gathered, asAuthor: boolean) {
    if (branch !== gathered.branch) fail('ownership', '故事来源不属于当前工作版本。')
    for (const version of gathered.sources.values()) {
      const existing = await getSource(tx, branch, version.id)
      if (!existing) fail('missing', '故事记录依赖发生变化，请重新读取。')
    }
    for (const checked of gathered.revisions.values()) {
      const original = await sourceRevision(tx, branch, checked.id)
      if (
        original?.sha256 !== checked.sha256 ||
        original.chapter_id !== checked.chapter_id ||
        original.content !== checked.content
      )
        fail('missing', '故事记录的原文已无法核验。')
    }
    for (const { original, native } of gathered.proof.values()) {
      const row = await readNative(tx, branch, native.id)
      if (
        !row ||
        row.revision.sha256 !== original.sha256 ||
        row.revision.chapter_id !== original.chapter_id ||
        row.revision.content !== original.content ||
        (native.id !== original.id &&
          !(await result(tx.objectStore('storyChapterAliases').getKey([branch, original.id, native.id]))))
      )
        fail('missing', '缺少故事记录已采用的同一章、同全文修订。')
      if (asAuthor && row && (row.chapter.archived || row.chapter.revision !== row.revision.revision))
        fail('conflict', '引用章节已有新保存版本，请重新选取原文。')
    }
  }

  async function writeHead(
    tx: Tx,
    branch: string,
    version: StoryVersion,
    previous: Head | undefined,
    evidence: StoryNativeRevision['evidence'],
  ) {
    const { heads, records } = await recordsTx(tx, branch)
    try {
      validateStoryHeadSelection(version, records)
    } catch (error) {
      fail('conflict', error instanceof Error ? error.message : '故事记录与当前工作版本的关联不匹配。')
    }
    const revisions = new Map(heads.map((head) => [head.recordId, head.revision]))
    const dependencies = Object.fromEntries(
      Object.keys(version.dependencies).map((id) => [id, revisions.get(id)!]),
    )
    const revision = (previous?.revision ?? -1) + 1
    const history: StoredHistory = {
      branchId: branch,
      recordId: version.record_id,
      revision,
      version_id: version.id,
      created: Date.now() / 1000,
      dependencies,
      evidence,
    }
    const head: Head = { branchId: branch, recordId: version.record_id, versionId: version.id, revision }
    await result(tx.objectStore('storyHistory').add(history))
    await result(tx.objectStore('storyHeads').put(head))
    return head
  }

  async function updateProject(tx: Tx, project: LocalProject, amount = 1) {
    await result(
      tx.objectStore('projects').put({
        ...project,
        branch: { ...project.branch, story_revision: project.branch.story_revision + amount },
      } satisfies LocalProject),
    )
  }

  function captureAuthorBase(tx: Tx, project: LocalProject, draft: ConfirmOfflineStory) {
    if (project.branch.story_revision !== draft.baseStoryRevision)
      fail('conflict', '故事版本已变化，请重新读取后确认。')
    return getHead(tx, project.branch.id, draft.record_id)
  }

  async function confirmLocalStory(projectId: string, draft: ConfirmOfflineStory) {
    const record = parseStoryRecord(draft.record)
    const initial = await transact([...storyReadStores], 'readonly', async (tx) => {
      const project = await readProject(tx, projectId)
      const branch = project.branch.id
      const previous = await captureAuthorBase(tx, project, draft)
      if ((previous?.versionId ?? null) !== draft.baseVersionId)
        fail('conflict', '故事记录已从其他窗口更新。')
      if (!previous && draft.archived) fail('conflict', '新故事记录不能直接归档。')
      const dependencies = []
      for (const id of storyReferences(record)) {
        const head = await getHead(tx, branch, id)
        if (!head) return fail('missing', '缺少关联故事记录的本机确认。')
        dependencies.push([id, head.versionId] as const)
      }
      return {
        branch,
        previous,
        dependencies,
        seed: previous || record.kind !== 'promise' ? undefined : await getSeed(tx, branch, draft.record_id),
      }
    })
    const seed = initial.seed
      ? await buildStoryVersion(initial.branch, {
          record_id: draft.record_id,
          parent_id: null,
          archived: false,
          record: initial.seed.seed,
          evidence: { record: [], stages: [[]] },
          dependencies: {},
        })
      : undefined
    const version = await buildStoryVersion(initial.branch, {
      record_id: draft.record_id,
      parent_id: initial.previous?.versionId ?? seed?.id ?? null,
      archived: draft.archived,
      record,
      evidence: draft.evidence,
      dependencies: Object.fromEntries(initial.dependencies),
    })
    const checked = await transact([...storyReadStores], 'readonly', (tx) =>
      gather(tx, initial.branch, version, true),
    )
    if (seed) checked.sources.set(seed.id, seed)
    await verifyGathered(version, checked, true)
    return transact([...storyWriteStores], 'readwrite', async (tx) => {
      const project = await readProject(tx, projectId)
      const branch = project.branch.id
      let previous = await captureAuthorBase(tx, project, draft)
      if ((previous?.versionId ?? null) !== draft.baseVersionId)
        fail('conflict', '故事记录已从其他窗口更新。')
      const outgoing = []
      if (seed) {
        const currentSeed = await getSeed(tx, branch, draft.record_id)
        if (!currentSeed || JSON.stringify(currentSeed.seed) !== JSON.stringify(initial.seed?.seed))
          fail('conflict', '大纲中的伏笔计划已变化，请重新读取。')
        const existing = await getSource(tx, branch, seed.id)
        if (!existing)
          await result(
            tx.objectStore('storyVersions').add({ branchId: branch, version: seed } satisfies StoredVersion),
          )
        if (!existing?.source) outgoing.push(`story-version:${seed.id}`)
        previous = await writeHead(tx, branch, seed, undefined, { record: [], stages: [[]] })
      }
      await recheck(tx, branch, checked, true)
      const existing = await getSource(tx, branch, version.id)
      if (!existing)
        await result(
          tx.objectStore('storyVersions').add({ branchId: branch, version } satisfies StoredVersion),
        )
      if (!existing?.source) outgoing.push(`story-version:${version.id}`)
      await writeHead(tx, branch, version, previous, bindNative(version, checked))
      await updateProject(tx, project, seed ? 2 : 1)
      await markPublication(tx, branch, ...outgoing, `story:${version.record_id}`)
      return version
    })
  }

  async function applyStoryObject(
    projectId: string,
    object: StoryHeadObject | StoryVersionObject,
    expected: string,
    options: Options,
  ) {
    const observed = await transact([...storyReadStores], 'readonly', async (tx) => {
      const project = await readProject(tx, projectId)
      const branch = project.branch.id
      if (object.branch_id !== branch) return fail('ownership', '故事记录范围与工作版本不符。')
      const selected =
        object.kind === 'story-version'
          ? object.version
          : (await getSource(tx, branch, object.revision_id))?.version
      if (!selected || (object.kind === 'story-head' && selected.record_id !== object.record_id))
        return fail('missing', '缺少云端采用的对应故事版本。')
      return { selected, checked: await gather(tx, branch, selected, object.kind === 'story-head') }
    })
    const version = await validateStoryVersion(object.branch_id, observed.selected)
    await verifyGathered(version, observed.checked, object.kind === 'story-head')
    return transact([...storyWriteStores], 'readwrite', async (tx) => {
      const project = await readProject(tx, projectId)
      const branch = project.branch.id
      await recheck(tx, branch, observed.checked, false)
      const cached = await getSource(tx, branch, version.id)
      if (object.kind === 'story-version') {
        const { source } = options
        if (source && (!cached?.source || source.sequence < cached.source.sequence))
          await result(
            tx
              .objectStore('storyVersions')
              .put({ ...cached, branchId: branch, version, source } satisfies StoredVersion),
          )
        return !!cached || !!source
      }
      const head = await getHead(tx, branch, object.record_id)
      const publication = await readPublication(tx, branch, `story:${object.record_id}`)
      const before = head ? adoptedHead(branch, head.recordId, head.versionId) : null
      if (
        storyObjectSignature(before) !== expected ||
        (options.publicationId !== undefined && options.publicationId !== (publication?.id ?? null))
      )
        return false
      if (!cached?.source) return fail('missing', '缺少云端采用的故事来源记录。')
      if (head?.versionId !== version.id) {
        await writeHead(tx, branch, version, head, bindNative(version, observed.checked))
        await updateProject(tx, project)
      }
      if (options.consumePublication && publication)
        await result(tx.objectStore('publications').delete([branch, publication.objectId]))
      return true
    })
  }

  async function checkLocalStorySelection(projectId: string, recordId: string, versionId: string) {
    const { version, checked } = await transact([...storyReadStores], 'readonly', async (tx) => {
      const project = await readProject(tx, projectId)
      const branch = project.branch.id
      const source = await getSource(tx, branch, versionId)
      if (!source?.source || source.version.record_id !== recordId)
        return fail('missing', '尚未取得所选故事记录的云端来源。')
      const { records } = await recordsTx(tx, branch)
      try {
        validateStoryHeadSelection(source.version, records)
      } catch (error) {
        return fail('conflict', error instanceof Error ? error.message : '故事登记依赖尚待核对。')
      }
      return { version: source.version, checked: await gather(tx, branch, source.version, true) }
    })
    await verifyGathered(version, checked, true)
  }

  async function readStoryHistoryTx(tx: Tx, project: LocalProject): Promise<StoryHistory | undefined> {
    const branch = project.branch.id
    const [stored, heads, history] = await Promise.all([
      byBranch<StoredVersion>(tx, 'storyVersions', branch),
      byBranch<Head>(tx, 'storyHeads', branch),
      byBranch<StoredHistory>(tx, 'storyHistory', branch),
    ])
    if (!stored.length) return undefined
    const buckets = new Map<string, StoryNativeRevision[]>()
    for (const { branchId: _branch, recordId, ...entry } of history) {
      const rows = buckets.get(recordId) ?? []
      rows.push(entry)
      buckets.set(recordId, rows)
    }
    return {
      adopted_heads: Object.fromEntries(heads.map(({ recordId, versionId }) => [recordId, versionId])),
      versions: orderStoryVersions(new Map(stored.map(({ version }) => [version.id, version]))),
      native_history: Object.fromEntries(
        [...buckets].map(([id, rows]) => [id, rows.sort((a, b) => a.revision - b.revision)]),
      ),
      source_devices: Object.fromEntries(
        stored.flatMap(({ version, source, recordedDeviceId }) => {
          const device = recordedDeviceId ?? source?.device_id
          return device ? [[version.id, device]] : []
        }),
      ),
    }
  }

  async function restoreStoryHistoryTx(tx: Tx, project: LocalProject, archive: StoryHistory) {
    const branchId = project.branch.id
    const versions = new Map(archive.versions.map((version) => [version.id, version]))
    for (const version of archive.versions) {
      const recordedDeviceId = Object.hasOwn(archive.source_devices, version.id)
        ? archive.source_devices[version.id]
        : undefined
      await result(
        tx.objectStore('storyVersions').add({
          branchId,
          version,
          ...(recordedDeviceId ? { recordedDeviceId } : {}),
        } satisfies StoredVersion),
      )
    }
    for (const [recordId, history] of Object.entries(archive.native_history)) {
      for (const row of history) {
        const version = versions.get(row.version_id)!
        const wire = [version.evidence.record, ...version.evidence.stages]
        const bindings = [row.evidence.record, ...row.evidence.stages]
        for (const [groupIndex, group] of wire.entries()) {
          for (const [index, proof] of group.entries()) {
            const sourceId = proof.revision_id
            const nativeId = bindings[groupIndex][index]
            if (sourceId !== nativeId)
              await result(
                tx.objectStore('storyChapterAliases').put({ branchId, sourceId, nativeId } satisfies Alias),
              )
          }
        }
        await result(
          tx.objectStore('storyHistory').add({ branchId, recordId, ...row } satisfies StoredHistory),
        )
      }
      const current = history.at(-1)!
      await result(
        tx.objectStore('storyHeads').add({
          branchId,
          recordId,
          versionId: current.version_id,
          revision: current.revision,
        } satisfies Head),
      )
    }
  }

  return {
    readLocalStory,
    readStoryTx,
    applyStoryObject,
    confirmLocalStory,
    rememberStoryChapterAlias,
    readStoryOutlinePromisesTx,
    readStoryHistoryTx,
    restoreStoryHistoryTx,
    checkLocalStorySelection,
  }
}
