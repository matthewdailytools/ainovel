import type { LocalChapter, LocalProject, LocalPublication, LocalRevision } from './offlineStore'
import type { OutlineVersion } from './outlineSyncModel'
import {
  lengthTargetKey,
  type PortableCheckpointFact,
  type PortableCheckpointLengthTarget,
  type PortableRestoreState,
} from './portableCheckpoint'
import {
  buildRestoreStateVersion,
  factSetSlot,
  groupFactsByChapter,
  isRestoreStateVersion,
  restoreStateHead,
  restoreStateObjectId,
  restoreStateObjectSignature,
  restoreStateSlot,
  type RestoreStateDraft,
  type RestoreStateHead,
  type RestoreStateSyncObject,
  type RestoreStateVersion,
} from './restoreStateSyncModel'
import { compactJSON, verifyStoryEvidence } from './storySyncModel'

export const restoreStateSyncStores = ['restoreSyncHeads', 'restoreSyncVersions'] as const
const stores = [
  ...restoreStateSyncStores,
  'restoreStates',
  'projects',
  'chapters',
  'revisions',
  'sourceRevisions',
  'storyChapterAliases',
  'outlineHeads',
  'outlineVersions',
  'publications',
] as const

type Tx = IDBTransaction
type Head = {
  branchId: string
  slot: string
  objectId?: string
  head?: RestoreStateHead
  pending?: { payload: RestoreStateDraft; publish: boolean }
}
type Version = { branchId: string; object: RestoreStateVersion }
type Options = { publicationId?: string | null; consumePublication?: boolean }
type TargetStaging = {
  outline?: OutlineVersion
  seedsOnly?: boolean
  removedScopeClears?: { changed: ReadonlySet<string>; historicalOutlines: readonly OutlineVersion[] }
}
type Access = {
  transact<T>(
    names: (typeof stores)[number][],
    mode: IDBTransactionMode,
    fn: (tx: Tx) => Promise<T>,
  ): Promise<T>
  result<T>(request: IDBRequest<T>): Promise<T>
  readProject(tx: Tx, id: string): Promise<LocalProject>
  sourceRevision(tx: Tx, branchId: string, revisionId: string): Promise<LocalRevision | undefined>
  chapterHead(tx: Tx, chapter: LocalChapter): Promise<LocalRevision | undefined>
  readRestoreState(tx: Tx, branchId: string): Promise<PortableRestoreState | undefined>
  writeRestoreState(tx: Tx, branchId: string, state: PortableRestoreState): Promise<void>
  readPublication(tx: Tx, branchId: string, objectId: string): Promise<LocalPublication | undefined>
  markPublication(tx: Tx, branchId: string, ...objects: string[]): Promise<void>
  digest(text: string): Promise<string>
  fail(code: 'format' | 'ownership' | 'missing', message: string): never
}

const emptyState = (): PortableRestoreState => ({ facts: [], length_targets: [] })

export function createRestoreStateOfflineStore({
  transact,
  result,
  readProject,
  sourceRevision,
  chapterHead,
  readRestoreState,
  writeRestoreState,
  readPublication,
  markPublication,
  digest,
  fail,
}: Access) {
  const getHead = (tx: Tx, branchId: string, slot: string) =>
    result<Head | undefined>(tx.objectStore('restoreSyncHeads').get([branchId, slot]))
  const getVersion = (tx: Tx, branchId: string, id: string) =>
    result<Version | undefined>(tx.objectStore('restoreSyncVersions').get([branchId, id]))
  const heads = (tx: Tx, branchId: string) =>
    result<Head[]>(tx.objectStore('restoreSyncHeads').index('byBranch').getAll(branchId))
  const getChapter = (tx: Tx, id: string) =>
    result<LocalChapter | undefined>(tx.objectStore('chapters').get(id))
  const outlineVersionId = async (tx: Tx, branchId: string) =>
    (await result<{ versionId: string } | undefined>(tx.objectStore('outlineHeads').get(branchId)))?.versionId
  const getOutline = async (tx: Tx, branchId: string, outlineId: string) =>
    (
      await result<{ version: OutlineVersion } | undefined>(
        tx.objectStore('outlineVersions').get([branchId, outlineId]),
      )
    )?.version

  async function stage(tx: Tx, saved: Head, payload: RestoreStateDraft, publish: boolean) {
    const pending = { payload, publish: publish || !!saved.pending?.publish }
    if (compactJSON(saved.pending ?? null) === compactJSON(pending)) return
    await result(tx.objectStore('restoreSyncHeads').put({ ...saved, pending }))
  }

  async function stageFacts(
    tx: Tx,
    branchId: string,
    revision: LocalRevision,
    facts: PortableCheckpointFact[],
    publish: boolean,
    knownFacts = false,
  ) {
    const slot = factSetSlot(revision.chapter_id)
    const saved = await getHead(tx, branchId, slot)
    if (!saved && !facts.length && !knownFacts) return
    const row = saved ?? { branchId, slot }
    const draft = {
      parent_id: row.head?.revision_id ?? null,
      chapter_id: revision.chapter_id,
      chapter_revision_id: revision.id,
      facts,
    }
    const current = row.head && (await getVersion(tx, branchId, row.head.revision_id))?.object
    if (
      !row.pending &&
      current?.kind === 'fact-set-version' &&
      current.version.chapter_revision_id === draft.chapter_revision_id &&
      compactJSON(current.version.facts) === compactJSON(facts)
    )
      return
    await stage(tx, row, { kind: 'fact-set-version', draft }, publish)
  }

  async function stageTargets(
    tx: Tx,
    branchId: string,
    targets: PortableCheckpointLengthTarget[],
    publish: boolean,
    { outline: selectedOutline, seedsOnly, removedScopeClears }: TargetStaging = {},
  ) {
    const activeId = selectedOutline?.id ?? (await outlineVersionId(tx, branchId))
    const outline = selectedOutline ?? (activeId && (await getOutline(tx, branchId, activeId)))
    const volumes = new Set(outline ? outline.plan.volumes.map(({ id }) => id) : [])
    for (const target of targets) {
      const slot = lengthTargetKey(target)
      const removedVolume = target.scope === 'volume' && !volumes.has(target.scope_id!)
      if (removedVolume && (target.targets !== null || !removedScopeClears?.changed.has(slot))) continue
      const saved = await getHead(tx, branchId, slot)
      if (seedsOnly && saved) continue
      const row = saved ?? { branchId, slot }
      const current = row.head && (await getVersion(tx, branchId, row.head.revision_id))?.object
      let outline_version_id = target.scope === 'volume' ? (activeId ?? null) : null
      if (removedVolume) {
        const pending = row.pending?.payload
        outline_version_id =
          (current?.kind === 'length-target-version' && current.version.outline_version_id) ||
          (pending?.kind === 'length-target-version' && pending.draft.outline_version_id) ||
          removedScopeClears?.historicalOutlines.find(({ plan }) =>
            plan.volumes.some(({ id }) => id === target.scope_id),
          )?.id ||
          null
        if (!outline_version_id) continue
      }
      if (
        !row.pending &&
        current?.kind === 'length-target-version' &&
        current.version.outline_version_id === outline_version_id &&
        compactJSON(current.version.targets) === compactJSON(target.targets)
      )
        continue
      await stage(
        tx,
        row,
        {
          kind: 'length-target-version',
          draft: { ...target, parent_id: row.head?.revision_id ?? null, outline_version_id },
        },
        publish,
      )
    }
  }

  async function clearChapterFacts(tx: Tx, branchId: string, revision: LocalRevision) {
    const state = await readRestoreState(tx, branchId)
    const facts = state?.facts.filter(({ evidence }) => evidence.chapter_id !== revision.chapter_id)
    const hadFacts = !!(state && facts && facts.length !== state.facts.length)
    if (state && facts && hadFacts) await writeRestoreState(tx, branchId, { ...state, facts })
    await stageFacts(tx, branchId, revision, [], true, hadFacts)
  }

  async function stageOutlineTargets(tx: Tx, branchId: string, outline: OutlineVersion) {
    const state = await readRestoreState(tx, branchId)
    await stageTargets(
      tx,
      branchId,
      state?.length_targets.filter(({ scope }) => scope === 'volume') ?? [],
      true,
      { outline },
    )
  }

  // File imports and workspaces updated from the local-only format begin with clean sync heads.
  async function seed(tx: Tx, branchId: string) {
    const state = await readRestoreState(tx, branchId)
    if (!state) return
    for (const [id, rows] of groupFactsByChapter(state.facts)) {
      if (await getHead(tx, branchId, factSetSlot(id))) continue
      const chapter = await getChapter(tx, id)
      if (chapter?.branch_id !== branchId || chapter.archived) continue
      const revision = await chapterHead(tx, chapter)
      if (revision)
        await stageFacts(
          tx,
          branchId,
          revision,
          rows.filter(({ evidence }) => evidence.revision_id === revision.id),
          false,
        )
    }
    await stageTargets(tx, branchId, state.length_targets, false, { seedsOnly: true })
  }

  async function seal(projectId: string) {
    const captured = await transact([...stores], 'readwrite', async (tx) => {
      const { branch } = await readProject(tx, projectId)
      await seed(tx, branch.id)
      return (await heads(tx, branch.id)).filter((row) => row.pending)
    })
    if (!captured.length) return
    const sealed = await Promise.all(
      captured.map(async (row) => {
        const object = await buildRestoreStateVersion(row.branchId, row.pending!.payload, digest)
        const head = restoreStateHead(object)
        return { row, object, head, objectId: await restoreStateObjectId(head, digest) }
      }),
    )
    await transact([...restoreStateSyncStores, 'publications'], 'readwrite', async (tx) => {
      for (const { row, object, head, objectId } of sealed) {
        const saved = await getHead(tx, row.branchId, row.slot)
        if (compactJSON(saved ?? null) !== compactJSON(row)) continue
        await result(
          tx.objectStore('restoreSyncVersions').put({ branchId: row.branchId, object } satisfies Version),
        )
        await result(
          tx
            .objectStore('restoreSyncHeads')
            .put({ branchId: row.branchId, slot: row.slot, head, objectId } satisfies Head),
        )
        if (row.pending?.publish) await markPublication(tx, row.branchId, objectId)
      }
    })
  }

  async function readSync(tx: Tx, branchId: string) {
    const [versions, selected] = await Promise.all([
      result<Version[]>(tx.objectStore('restoreSyncVersions').index('byBranch').getAll(branchId)),
      heads(tx, branchId),
    ])
    return {
      versions: versions.map(({ object }) => object),
      heads: selected.flatMap(({ head, objectId, pending }) =>
        head && objectId && !pending ? [[objectId, head] as const] : [],
      ),
    }
  }

  async function unpublishedCount(tx: Tx, branchId: string, alreadyCounted: ReadonlySet<string>) {
    return (await heads(tx, branchId)).filter(
      ({ pending, objectId }) => pending?.publish && (!objectId || !alreadyCounted.has(objectId)),
    ).length
  }

  async function resolve(tx: Tx, object: RestoreStateVersion, adopting: boolean) {
    const branch = object.branch_id
    const version = object.version
    if (version.parent_id) {
      const parent = (await getVersion(tx, branch, version.parent_id))?.object
      if (!parent) return fail('missing', '等待正式事实或篇幅目标的上一个修订。')
      if (parent.kind !== object.kind || restoreStateSlot(parent) !== restoreStateSlot(object))
        return fail('format', '正式事实或篇幅目标的父修订属于其他范围。')
    }
    if (object.kind === 'length-target-version') {
      const { scope, scope_id, outline_version_id, targets } = object.version
      if (outline_version_id) {
        const outline = await getOutline(tx, branch, outline_version_id)
        if (!outline) return fail('missing', '等待篇幅目标所属的大纲版本。')
        if (!outline.plan.volumes.some(({ id }) => id === scope_id))
          return fail('format', '篇幅目标指定的卷不属于引用的大纲版本。')
        if (adopting && targets !== null && (await outlineVersionId(tx, branch)) !== outline_version_id)
          return fail('missing', '等待采用篇幅目标所属的准确大纲。')
      }
      if (adopting && scope === 'chapter' && (await getChapter(tx, scope_id!))?.branch_id !== branch)
        return fail('missing', '等待篇幅目标所属的章节目录。')
      return {}
    }
    const { chapter_id, chapter_revision_id, facts } = object.version
    const original = await sourceRevision(tx, branch, chapter_revision_id)
    if (!original) return fail('missing', '等待正式事实的准确章稿修订。')
    if (original.chapter_id !== chapter_id) return fail('format', '正式事实的章稿属于其他章节。')
    if (!adopting) return { original }
    const chapter = await getChapter(tx, chapter_id)
    if (chapter?.branch_id !== branch || (chapter.archived && facts.length))
      return fail('missing', '等待章稿目录和正式事实的当前状态一致。')
    if (chapter.archived) return { original }
    const native = await chapterHead(tx, chapter)
    if (!native) return fail('missing', '等待章稿目录和正式事实的当前状态一致。')
    if (native.id !== original.id) {
      const alias = await result<{ chapterAdopted?: true } | undefined>(
        tx.objectStore('storyChapterAliases').get([branch, original.id, native.id]),
      )
      if (!alias?.chapterAdopted || native.sha256 !== original.sha256 || native.content !== original.content)
        return fail('missing', '等待正式事实引用的章稿修订正式采用。')
    }
    return { original, native }
  }

  async function apply(
    projectId: string,
    objectId: string,
    object: RestoreStateSyncObject,
    expected: string,
    options: Options,
  ) {
    const immutable = isRestoreStateVersion(object)
    const slot = restoreStateSlot(object)
    const captured = await transact([...stores], 'readonly', async (tx) => {
      const { branch } = await readProject(tx, projectId)
      if (object.branch_id !== branch.id) return fail('ownership', '云端正式事实或篇幅目标属于另一工作版本。')
      const version = immutable ? object : (await getVersion(tx, branch.id, object.revision_id))?.object
      if (!version) return fail('missing', '等待正式事实或篇幅目标的确切版本。')
      if (restoreStateSlot(version) !== slot) return fail('format', '采用头指向了其他范围的正式版本。')
      return { version, sources: await resolve(tx, version, !immutable) }
    })
    if (captured.version.kind === 'fact-set-version') {
      for (const { evidence } of captured.version.version.facts) {
        const quote = await verifyStoryEvidence(evidence, captured.sources.original)
        if ([...quote].length > 2000) return fail('format', '正式事实的原文选区超出允许范围。')
      }
    }
    return transact([...stores], 'readwrite', async (tx) => {
      const project = await readProject(tx, projectId)
      const branchId = project.branch.id
      if (branchId !== object.branch_id) return false
      const { version } = captured
      const verified = await resolve(tx, version, !immutable)
      if (
        verified.original?.id !== captured.sources.original?.id ||
        verified.original?.sha256 !== captured.sources.original?.sha256 ||
        verified.native?.id !== captured.sources.native?.id
      )
        return false
      if (immutable) {
        const existing = (await getVersion(tx, branchId, version.version.id))?.object
        if (existing) return restoreStateObjectSignature(existing) === restoreStateObjectSignature(version)
        if (expected !== 'null') return false
        await result(
          tx.objectStore('restoreSyncVersions').add({ branchId, object: version } satisfies Version),
        )
        return true
      }
      const current = await getHead(tx, branchId, slot)
      const publication = await readPublication(tx, branchId, objectId)
      const signature = current?.head ? restoreStateObjectSignature(current.head) : 'null'
      if (
        current?.pending ||
        signature !== expected ||
        (options.publicationId !== undefined && options.publicationId !== (publication?.id ?? null))
      )
        return false
      if (current?.head?.revision_id !== object.revision_id) {
        const state = (await readRestoreState(tx, branchId)) ?? emptyState()
        if (version.kind === 'fact-set-version') {
          const { chapter_id, facts } = version.version
          const nativeRevisionId = verified.native?.id
          if (facts.length && !nativeRevisionId)
            return fail('missing', '等待正式事实引用的章稿修订正式采用。')
          state.facts = [
            ...state.facts.filter(({ evidence }) => evidence.chapter_id !== chapter_id),
            ...(nativeRevisionId
              ? facts.map((fact) => ({
                  ...fact,
                  evidence: { ...fact.evidence, revision_id: nativeRevisionId },
                }))
              : []),
          ]
        } else {
          const { scope, scope_id, targets } = version.version
          state.length_targets = [
            ...state.length_targets.filter((row) => lengthTargetKey(row) !== slot),
            { scope, scope_id, targets },
          ]
        }
        await writeRestoreState(tx, branchId, state)
        await result(
          tx.objectStore('restoreSyncHeads').put({ branchId, slot, head: object, objectId } satisfies Head),
        )
        project.branch.story_revision++
        await result(tx.objectStore('projects').put(project))
      }
      if (options.consumePublication && publication)
        await result(tx.objectStore('publications').delete([branchId, objectId]))
      return true
    })
  }

  return {
    stageFacts,
    stageTargets,
    clearChapterFacts,
    stageOutlineTargets,
    seal,
    readSync,
    unpublishedCount,
    apply,
  }
}
