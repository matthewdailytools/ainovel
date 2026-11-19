import {
  applySyncedChapterDirectory,
  applySyncedObject,
  acknowledgeLocalPublications,
  checkLocalStorySelection,
  countLocalPendingPublications,
  forkLocalManuscript,
  hasLocalChapterAdoption,
  manuscriptHash,
  readLocalOutline,
  readLocalPublications,
  readLocalSyncSnapshot,
  syncManuscriptObjects,
  syncObjectSignature,
  validateSyncedObject,
  type LocalPublication,
  type LocalProject,
  type SyncedObject,
} from './offlineStore'
import { settingUseVersionSlot, type SettingUseSyncObject } from './settingUseSyncModel'
import {
  outlineChapterPlans,
  requiredOutlineStoryRecord,
  validateOutlineActiveBasis,
  validateOutlineVersionSources,
  type OutlineVersion,
} from './outlineSyncModel'
import {
  allStoryEvidence,
  orderStoryHeads,
  orderStoryVersions,
  validateStoryHeadSelection,
  type StoryVersion,
} from './storySyncModel'
import { workflowSyncEntry } from './workflowArchive'
import { workflowTransferRestrictions } from './workflowPrivacy'
import type { SettingsConflictMerge } from './settingsSyncModel'
import type { ChapterOrderConflict, ChapterOrderResolution } from './ChapterOrderConflicts'
import { referenceCardKey, referenceHeadObjectId } from './referenceArchive'
import {
  isRestoreStateObjectId,
  isRestoreStateSyncObject,
  isRestoreStateVersion,
  restoreStateObjectId,
  restoreStatePublicationSources,
  restoreStateSlot,
} from './restoreStateSyncModel'
import {
  approveSyncResolution,
  CHAPTER_DIRECTORY_PREFIX,
  enableSyncLedger,
  listSyncHeads,
  markChapterDirectory,
  markSyncBaseline,
  MAX_DIRECTORY_CHAPTERS,
  pendingPayloads,
  pullSyncPage,
  pushSyncPending,
  queueSyncChange,
  queueSyncResolution,
  readChapterDirectory,
  readChapterBeforeArchive,
  readPublicationReceipts,
  readSyncConflict,
  readSyncProgress,
  readSyncState,
  recordChapterDirectoryRejection,
  requireSyncLedger,
  stageChapterDirectory,
  SyncChapterDirectoryRejected,
  SyncLedgerChanged,
  SyncResolutionChanged,
  SyncRestoreStateRejected,
  verifyChapterDirectoryReceipt,
  type ChapterDirectoryRequest,
  type SyncPayload,
  type SyncScope,
  type SyncBaseline,
  type SyncTransport,
} from './syncClient'

export function cloudScope(service: string, workId: string, deviceId: string): SyncScope {
  const url = new URL(service.trim())
  if (
    url.username ||
    url.password ||
    url.pathname !== '/' ||
    url.search ||
    url.hash ||
    (url.protocol !== 'https:' &&
      !(url.protocol === 'http:' && ['127.0.0.1', '[::1]'].includes(url.hostname))) ||
    !/^[A-Za-z0-9._:-]{1,128}$/.test(deviceId)
  )
    throw new Error('请填写 HTTPS 或回环 HTTP 服务地址，以及有效的设备编号。')
  return { service: url.origin, workId, deviceId }
}

export function httpSyncTransport(scope: SyncScope, token: string) {
  if (!/^[A-Za-z0-9_-]{32,128}$/.test(token)) throw new Error('请输入同步服务签发的设备凭据。')
  const endpoint = `${scope.service}/api/sync/works/${encodeURIComponent(scope.workId)}`
  const deviceQuery = new URLSearchParams({ device_id: scope.deviceId })
  function send(path: string, method: 'GET' | 'POST' | 'DELETE', payload?: unknown, ledgerId?: string) {
    return fetch(endpoint + path, {
      method,
      credentials: 'omit',
      redirect: 'error',
      cache: 'no-store',
      headers: {
        Authorization: `Bearer ${token}`,
        ...(ledgerId ? { 'X-Ainovel-Ledger': ledgerId } : {}),
        ...(payload === undefined ? {} : { 'Content-Type': 'application/json' }),
      },
      signal: AbortSignal.timeout(15_000),
      ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
    })
  }
  async function request(path: string, payload?: unknown, optional = false, boundLedger?: string) {
    const ledgerId = path === '/checkpoint' ? undefined : (boundLedger ?? (await requireSyncLedger(scope)))
    const response = await send(path, payload === undefined ? 'GET' : 'POST', payload, ledgerId)
    if (optional && response.status === 404) return null
    const result = await response.json().catch(() => {
      if (response.ok) throw new Error('同步服务返回了无法核对的响应。')
      return null
    })
    if (!response.ok) {
      const code = result?.code
      const message = typeof result?.message === 'string' ? result.message : undefined
      if (response.status === 409 && code === 'sync_ledger_replaced') throw new SyncLedgerChanged()
      if (response.status === 409 && ['sync_head_changed', 'sync_conflict_resolved'].includes(code)) {
        throw new SyncResolutionChanged('云端版本已更新。请再次同步并重新比较。')
      }
      if (
        path.startsWith('/chapter-order-batches?') &&
        [409, 413].includes(response.status) &&
        [
          'sync_chapter_order_changed',
          'sync_chapter_order_limit',
          'sync_object_size',
          'sync_request_size',
          'sync_replay',
          'sync_reserved',
        ].includes(code)
      )
        throw new SyncChapterDirectoryRejected(message || '云端目录已变化，请先核对原章位和采用头。')
      if (
        response.status === 409 &&
        ['sync_chapter_order_changed', 'sync_chapter_order_scheduled', 'sync_chapter_order_limit'].includes(
          code,
        )
      ) {
        throw new SyncResolutionChanged(message || '章位已变化，请刷新后重新核对。')
      }
      if (
        code === 'sync_restore_state_dependency' ||
        code === 'sync_restore_state_integrity' ||
        code === 'sync_object_size'
      )
        throw new SyncRestoreStateRejected(code, message || '正式事实或篇幅目标暂未提交。')
      throw new Error(message || `同步请求失败（HTTP ${response.status}）。`)
    }
    return result
  }
  return {
    async enable() {
      await enableSyncLedger(scope, await request('/checkpoint'))
    },
    async verifyLedger() {
      await requireSyncLedger(scope, await request('/checkpoint'))
    },
    async chapterOrderConflicts(): Promise<ChapterOrderConflict[]> {
      const result = await request(`/chapter-order-conflicts?${deviceQuery}`)
      const conflicts: unknown = result?.conflicts
      if (
        !Array.isArray(conflicts) ||
        conflicts.some(
          (conflict) =>
            !Number.isSafeInteger(conflict?.position) ||
            conflict.position < 1 ||
            !Array.isArray(conflict.chapters) ||
            conflict.chapters.length < 2 ||
            conflict.chapters.some(
              (chapter: ChapterOrderConflict['chapters'][number]) =>
                typeof chapter?.chapter_id !== 'string' ||
                typeof chapter.title !== 'string' ||
                typeof chapter.head_revision !== 'string' ||
                typeof chapter.scheduled !== 'boolean',
            ),
        )
      ) {
        throw new Error('云端章位冲突数据无法核对。')
      }
      return conflicts
    },
    applyChapterDirectory: (batch) => request(`/chapter-order-batches?${deviceQuery}`, batch),
    async resolveChapterOrder(
      selection: ChapterOrderResolution,
      ledgerId: string,
      checkUnsubmitted: () => Promise<void>,
    ) {
      const heads = Object.keys(selection.heads)
        .sort()
        .map((id) => [id, selection.heads[id]])
      const changeId = await manuscriptHash(
        JSON.stringify([
          'chapter-order',
          ledgerId,
          scope.workId,
          scope.deviceId,
          selection.chapter_id,
          selection.position,
          heads,
        ]),
      )
      const prior = await request(`/receipts/${changeId}`, undefined, true, ledgerId)
      if (prior === null) await checkUnsubmitted()
      const receipt = await request(
        `/chapter-order-conflicts/resolve?${deviceQuery}`,
        { ...selection, change_id: changeId },
        false,
        ledgerId,
      )
      if (
        receipt?.status !== 'applied' ||
        receipt.change_id !== changeId ||
        receipt.head_revision !== changeId ||
        receipt.device_id !== scope.deviceId ||
        receipt.object_id !== `chapter:${selection.chapter_id}` ||
        receipt.parent_revision !== selection.heads[selection.chapter_id] ||
        receipt.tombstone !== false ||
        receipt.payload?.kind !== 'chapter' ||
        receipt.payload.chapter?.id !== selection.chapter_id ||
        !Number.isSafeInteger(receipt.payload.chapter.position) ||
        receipt.payload.chapter.position <= selection.position
      ) {
        throw new Error('尚未取得可核对的章位调整回执。')
      }
    },
    push: (change) => request('/push', change),
    pull: (after) => request(`/pull?after=${after}&limit=50`),
    receipt: (id) => request('/receipts/' + encodeURIComponent(id), undefined, true),
    resolve: (id, data) => request('/conflicts/' + encodeURIComponent(id) + '/resolve', data),
    async revokeDevice() {
      const response = await send(`/device-session?${deviceQuery}`, 'DELETE').catch(() => undefined)
      if (response?.status === 204) return
      if (response?.status === 401) throw new Error('凭据无效或已被更换，请核对设备凭据。')
      if (response?.status === 403) throw new Error('设备编号与凭据不匹配，请核对后重试。')
      throw new Error('尚未取得云端撤销回执，请用同一设备凭据重试。作品与待同步记录已保留。')
    },
  } satisfies SyncTransport & Record<string, unknown>
}

async function pull(scope: SyncScope, transport: SyncTransport) {
  for (let page = 0; page < 8; page++) {
    if (!(await pullSyncPage(scope, transport)).hasMore) return
  }
  throw new Error('尚有云端历史待读取，已保留游标，请再次同步。')
}

function orderVersions<T extends { version: { id: string; parent_id: string | null } }>(versions: T[]) {
  const byId = new Map(versions.map((entry) => [entry.version.id, entry]))
  const seen = new Set<string>()
  const ordered: T[] = []
  for (const entry of versions) {
    const path: T[] = []
    let revision: T | undefined = entry
    while (revision && !seen.has(revision.version.id)) {
      path.push(revision)
      seen.add(revision.version.id)
      revision = revision.version.parent_id ? byId.get(revision.version.parent_id) : undefined
    }
    ordered.push(...path.reverse())
  }
  return ordered
}

async function localObjects(project: LocalProject) {
  const {
    manuscript,
    brief: { versions, current },
    settings,
    outline,
    story,
    workflow,
    reference,
    settingUse,
    restoreStateSync,
    publications,
  } = await readLocalSyncSnapshot(project.novel.id)
  const objects = syncManuscriptObjects(manuscript)
  const format = 'ainovel-manuscript-sync/1'
  const branch_id = project.branch.id
  for (const { version } of orderVersions(versions))
    objects.set(`project-version:${version.id}`, { format, kind: 'project-version', branch_id, version })
  if (current)
    objects.set(`project:${branch_id}`, {
      format,
      kind: 'project-head',
      branch_id,
      revision_id: current.version.id,
    })
  if (reference) {
    for (const version of [...reference.versions].sort((left, right) => left.revision - right.revision))
      objects.set(`reference-card-version:${version.id}`, {
        format,
        kind: 'reference-card-version',
        branch_id,
        version,
      })
    for (const head of reference.adopted_heads)
      objects.set(await referenceHeadObjectId(branch_id, head, manuscriptHash), {
        format,
        kind: 'reference-card-head',
        branch_id,
        ...head,
      })
  }
  for (const { version } of orderVersions(settings.versions))
    objects.set(`settings-version:${version.id}`, { format, kind: 'settings-version', branch_id, version })
  if (settings.current)
    objects.set(`settings:${branch_id}`, {
      format,
      kind: 'settings-head',
      branch_id,
      revision_id: settings.current.version.id,
    })
  for (const version of orderStoryVersions(
    new Map(story.versions.map(({ version }) => [version.id, version])),
  ))
    objects.set(`story-version:${version.id}`, { format, kind: 'story-version', branch_id, version })
  for (const version of orderStoryHeads(
    new Map(story.records.map(({ version }) => [version.record_id, version])),
  ))
    objects.set(`story:${version.record_id}`, {
      format,
      kind: 'story-head',
      branch_id,
      record_id: version.record_id,
      revision_id: version.id,
    })
  for (const { version } of orderVersions(outline.versions))
    objects.set(`outline-version:${version.id}`, { format, kind: 'outline-version', branch_id, version })
  if (outline.current)
    objects.set(`outline:${branch_id}`, {
      format,
      kind: 'outline-head',
      branch_id,
      revision_id: outline.current.version.id,
    })
  if (settingUse) {
    for (const { version } of orderVersions(settingUse.versions))
      objects.set(`setting-use-version:${version.id}`, {
        format,
        kind: 'setting-use-version',
        branch_id,
        version,
      })
    for (const [slot_id, revision_id] of Object.entries(settingUse.adopted_heads))
      objects.set(`setting-use:${slot_id}`, {
        format,
        kind: 'setting-use-head',
        branch_id,
        slot_id,
        revision_id,
      })
  }
  for (const [id, object] of workflow) objects.set(id, object)
  for (const object of orderVersions(restoreStateSync.versions))
    objects.set(await restoreStateObjectId(object, manuscriptHash), object)
  for (const [id, object] of restoreStateSync.heads) objects.set(id, object)
  return {
    objects,
    versions,
    settings,
    outline,
    story,
    reference,
    settingUse,
    restoreStateSync,
    publications: new Map(publications.map((record) => [record.objectId, record])),
  }
}

function publicationChangeId(scope: SyncScope, record: LocalPublication) {
  return manuscriptHash(
    JSON.stringify([scope.service, scope.workId, scope.deviceId, record.objectId, record.id]),
  )
}

async function acknowledgePublications(scope: SyncScope) {
  const pending = await readLocalPublications(scope.workId)
  if (!pending.length) return
  const ids = await Promise.all(pending.map((record) => publicationChangeId(scope, record)))
  const receipts = new Map(
    (await readPublicationReceipts(scope, ids)).map((receipt) => [receipt.change_id, receipt]),
  )
  await acknowledgeLocalPublications(
    pending.filter(({ objectId }, index) => receipts.get(ids[index])?.object_id === objectId),
  )
}

type SyncHead = Awaited<ReturnType<typeof listSyncHeads>>[number]
type DirectoryMove = ChapterDirectoryRequest['moves'][number]

async function reconcileChapterDirectories(
  project: LocalProject,
  scope: SyncScope,
  heads: SyncHead[],
  deferred: Map<string, string>,
  pending: Awaited<ReturnType<typeof readSyncState>>['directory'],
) {
  const chapters = new Map(
    heads.filter(({ directoryParts }) => directoryParts?.length).map((head) => [head.id, head]),
  )
  const directories = heads
    .filter(
      (head) =>
        head.id.startsWith(CHAPTER_DIRECTORY_PREFIX) &&
        head.receipt &&
        head.baseline?.revision !== head.revision,
    )
    .sort((left, right) => left.sequence - right.sequence)
  for (const directory of directories) {
    const manifest = directory.receipt!
    try {
      const own = pending?.request.change_id === manifest.change_id ? pending : undefined
      if (own) verifyChapterDirectoryReceipt(scope, manifest, own.request)
      const moves = await readChapterDirectory(scope, manifest)
      const checked = await Promise.all(
        moves.map(async ({ chapterId, from, to, parent, child }) => {
          const id = child.object_id
          const head = chapters.get(id)
          if (head?.directoryParts?.[0] !== child.change_id) throw new Error('此章还有较早的目录批次待采用。')
          const [before, after, sourceDigest] = await Promise.all([
            validateSyncedObject(project, id, parent.payload),
            validateSyncedObject(project, id, child.payload),
            manuscriptHash(JSON.stringify([scope.workId, manifest.change_id, chapterId])),
          ])
          if (
            child.change_id !== `chapter-order-part-${sourceDigest}` ||
            before?.kind !== 'chapter' ||
            after?.kind !== 'chapter' ||
            before.chapter.position !== from ||
            after.chapter.position !== to ||
            JSON.stringify(before) !==
              JSON.stringify({ ...after, chapter: { ...after.chapter, position: from } })
          )
            throw new Error('目录批次的子回执必须保留原章稿和章节信息。')
          const savedBasis = head.baseline
          const base =
            savedBasis &&
            (savedBasis.revision === parent.change_id ||
              (savedBasis.sequence !== undefined && savedBasis.sequence < parent.sequence))
              ? savedBasis
              : undefined
          return {
            before,
            after,
            child,
            head,
            base,
            previousSignature: base?.signature,
            parentWasAdopted:
              base?.revision === parent.change_id && base.signature === syncObjectSignature(before),
          }
        }),
      )
      const adopted = await applySyncedChapterDirectory(project.novel.id, checked, own)
      if (!adopted) throw new Error('目录批次与本机章位或稿件依据不一致，已保留本机目录。')
      const members = checked.map(({ after, base, child }) => {
        const result = adopted.get(after.chapter.id)
        let baseline: SyncBaseline | undefined
        if (result?.matchesRemote) {
          baseline = {
            revision: child.change_id,
            signature: syncObjectSignature(after),
            sequence: child.sequence,
          }
        } else if (result?.projectedBaseline && base) {
          // Position changed. Competing prose still compares against the accepted older head.
          baseline = { ...base, signature: result.projectedBaseline }
        }
        return { objectId: child.object_id, receiptId: child.change_id, baseline }
      })
      await markChapterDirectory(scope, manifest, members, !!own)
      for (const { objectId, receiptId, baseline } of members) {
        const head = chapters.get(objectId)!
        head.directoryParts = head.directoryParts!.filter((id) => id !== receiptId)
        if (baseline) {
          head.baseline = baseline
          head.approval = undefined
        }
      }
    } catch (error) {
      deferred.set(directory.id, error instanceof Error ? error.message : '目录批次待核对。')
    }
  }
  for (const head of chapters.values()) {
    if (head.directoryParts?.length) deferred.set(head.id, '等待整组云端目录调整。')
  }
}

async function reconcile(project: LocalProject, scope: SyncScope) {
  const { blockedObjects, directory } = await readSyncState(scope)
  const heads = await listSyncHeads(scope)
  const deferred = new Map<string, string>()
  await reconcileChapterDirectories(project, scope, heads, deferred, directory)
  const { objects: local, publications } = await localObjects(project)
  const priority = (id: string) => {
    if (id.startsWith('revision:') || id.startsWith('project-version:')) return 0
    if (id.startsWith('settings-version:')) return 2
    if (id.startsWith('settings:')) return 3
    if (id.startsWith('story-version:')) return 4
    if (id.startsWith('reference-card-version:')) return 4
    if (id.startsWith('story:')) return 5
    if (id.startsWith('reference-card:')) return 5
    if (id.startsWith('outline-version:')) return 6
    if (id.startsWith('outline:')) return 7
    if (id.startsWith('evidence:')) return 8
    if (id.startsWith('workflow-inspection:')) return 9
    if (id.startsWith('workflow-completion:')) return 10
    if (id.startsWith('setting-use-version:')) return 8
    if (id.startsWith('setting-use:')) return 9
    if (id.startsWith('fact-set-version:') || id.startsWith('length-target-version:')) return 8
    if (id.startsWith('fact-set-head:') || id.startsWith('length-target-head:')) return 9
    return 1
  }
  const depth = (payload: SyncPayload | null | undefined) => {
    const revision = payload?.revision
    return revision &&
      typeof revision === 'object' &&
      !Array.isArray(revision) &&
      typeof revision.revision === 'number'
      ? revision.revision
      : 0
  }
  heads.sort(
    (left, right) =>
      priority(left.id) - priority(right.id) ||
      depth(left.receipt?.payload) - depth(right.receipt?.payload) ||
      (left.source?.sequence ?? left.receipt?.sequence ?? 0) -
        (right.source?.sequence ?? right.receipt?.sequence ?? 0),
  )
  for (const head of heads) {
    if (head.id.startsWith(CHAPTER_DIRECTORY_PREFIX) || head.directoryParts?.length) continue
    const missingArchive = head.receipt?.tombstone && head.id.startsWith('chapter:') && !local.has(head.id)
    const current = local.get(head.id)
    const raw = head.receipt?.payload?.head
    const possibleAlias =
      current?.kind === 'chapter' &&
      raw &&
      typeof raw === 'object' &&
      !Array.isArray(raw) &&
      typeof raw.id === 'string' &&
      raw.id !== current.head.id &&
      raw.sha256 === current.head.sha256
    const sameReceipt = head.baseline?.revision === head.revision
    if (
      !head.receipt ||
      (sameReceipt && !missingArchive && !possibleAlias) ||
      blockedObjects.includes(head.id)
    )
      continue
    try {
      const object = await validateSyncedObject(project, head.id, head.receipt.payload)
      const target = syncObjectSignature(object)
      const signature = syncObjectSignature(current ?? null)
      const resolution = head.approval?.changeId === head.revision ? head.approval : undefined
      const safeBase = resolution?.signature ?? head.baseline?.signature ?? 'null'
      const publicationId = publications.get(head.id)?.id ?? null
      const recordChapterAdoption =
        !publicationId &&
        signature === target &&
        current?.kind === 'chapter' &&
        object?.kind === 'chapter' &&
        current.head.id !== object.head.id &&
        !(await hasLocalChapterAdoption(project.branch.id, object.head.id, current.head.id))
      if (sameReceipt && !missingArchive && !recordChapterAdoption) continue
      if (
        object?.kind === 'project-version' ||
        object?.kind === 'settings-version' ||
        object?.kind === 'outline-version' ||
        object?.kind === 'story-version' ||
        object?.kind === 'setting-use-version' ||
        object?.kind === 'reference-card-version' ||
        object?.kind === 'workflow-inspection' ||
        object?.kind === 'workflow-completion' ||
        (isRestoreStateSyncObject(object) && isRestoreStateVersion(object))
      ) {
        const { device_id, change_id, sequence } = head.source ?? head.receipt
        const saved = await applySyncedObject(project.novel.id, head.id, object, signature, {
          source: { device_id, change_id, sequence },
        })
        if (!saved) {
          deferred.set(head.id, '本机内容已变化，下次同步会重新核对。')
          continue
        }
        await markSyncBaseline(scope, head.id, {
          revision: head.revision,
          signature: target,
          sequence: head.sequence,
        })
        continue
      }
      if (object?.kind === 'revision' && signature !== 'null' && signature !== target) {
        deferred.set(head.id, '云端和本机以同一来源编号保存了不同修订，待人工核查。')
        continue
      }
      const adoptsHead =
        head.id.startsWith('chapter:') ||
        head.id.startsWith('project:') ||
        head.id.startsWith('settings:') ||
        head.id.startsWith('outline:') ||
        head.id.startsWith('story:') ||
        head.id.startsWith('setting-use:') ||
        head.id.startsWith('reference-card:') ||
        head.id.startsWith('fact-set-head:') ||
        head.id.startsWith('length-target-head:')
      if (
        resolution &&
        ((resolution.publicationId ?? null) !== publicationId || resolution.signature !== signature)
      )
        continue
      if (adoptsHead && publicationId && !resolution) {
        if (head.lastPublication?.id === head.revision)
          await markSyncBaseline(scope, head.id, {
            revision: head.revision,
            signature: target,
            sequence: head.sequence,
          })
        // An earlier accepted send becomes the CAS base. The newer local save remains pending.
        continue
      }
      const previous = missingArchive ? await readChapterBeforeArchive(scope, head.receipt) : null
      const archiveSource = previous && (await validateSyncedObject(project, head.id, previous.payload))
      if (archiveSource && archiveSource.kind !== 'chapter')
        throw new Error('待归档章节的云端父版本格式无效。')
      if (
        archiveSource ||
        recordChapterAdoption ||
        (signature !== target && signature === safeBase) ||
        (resolution && publicationId)
      ) {
        if (
          !(await applySyncedObject(project.novel.id, head.id, object, signature, {
            publicationId,
            consumePublication: !!resolution,
            archiveSource: archiveSource ?? undefined,
          }))
        ) {
          deferred.set(head.id, '本机内容已变化，下次同步会重新核对。')
          continue
        }
      } else if (signature !== target && (resolution || head.receipt.device_id !== scope.deviceId)) {
        // A resolution acknowledges exactly the local version compared by its author.
        // If the author kept writing, submit their newer version against its prior baseline.
        continue
      }
      await markSyncBaseline(scope, head.id, {
        revision: head.revision,
        signature: target,
        sequence: head.sequence,
      })
    } catch (error) {
      deferred.set(head.id, error instanceof Error ? error.message : '云端对象待核对。')
    }
  }
  return deferred
}

function knownChapterPosition(payload: SyncPayload | null | undefined, workId: string, chapterId: string) {
  const chapter = payload?.chapter
  if (
    payload?.kind === 'chapter' &&
    chapter &&
    typeof chapter === 'object' &&
    !Array.isArray(chapter) &&
    chapter.id === chapterId &&
    chapter.branch_id === workId &&
    chapter.archived === 0 &&
    typeof chapter.position === 'number' &&
    Number.isSafeInteger(chapter.position) &&
    chapter.position > 0
  )
    return chapter.position
  return undefined
}

function closedDirectoryMoves(moves: DirectoryMove[]) {
  const remaining = new Map(moves.map((move) => [move.position, move]))
  const closed: DirectoryMove[] = []
  while (remaining.size) {
    const path = new Map<number, DirectoryMove>()
    let position = remaining.keys().next().value!
    for (let move = remaining.get(position); move; move = remaining.get(position)) {
      remaining.delete(position)
      path.set(position, move)
      position = move.next_position
    }
    for (let move = path.get(position); move; move = path.get(position)) {
      path.delete(position)
      closed.push(move)
      position = move.next_position
    }
  }
  return closed
}

function recognizesDirectoryPosition(head: SyncHead, workId: string, move: DirectoryMove) {
  const signature = head.baseline?.signature
  if (!signature) return false
  const fields: unknown = JSON.parse(signature)
  return (
    Array.isArray(fields) &&
    fields[0] === 'chapter' &&
    fields[1] === move.chapter_id &&
    fields[2] === workId &&
    fields[3] === move.position
  )
}

async function prepareChapterDirectory(
  project: LocalProject,
  scope: SyncScope,
  deferred: Map<string, string>,
) {
  const [snapshot, heads, state] = await Promise.all([
    localObjects(project),
    listSyncHeads(scope),
    readSyncState(scope),
  ])
  const active = new Map(
    [...snapshot.objects.values()].flatMap((value) =>
      value?.kind === 'chapter' ? [[value.chapter.id, value.chapter.position] as const] : [],
    ),
  )
  const cloud = new Map(
    heads.flatMap((head) => {
      if (!head.id.startsWith('chapter:') || !head.receipt || head.receipt.tombstone) return []
      const id = head.id.slice('chapter:'.length)
      const position = knownChapterPosition(head.receipt.payload, scope.workId, id)
      return position === undefined ? [] : [[id, { head, position }] as const]
    }),
  )
  const desired = [...cloud].flatMap(([id, { head, position }]) => {
    const next = active.get(id)
    return next !== undefined && next !== position
      ? [{ chapter_id: id, position, next_position: next, head_revision: head.revision }]
      : []
  })
  let staged = state.directory
  if (
    staged?.error &&
    staged.request.moves.every(({ chapter_id }) => active.get(chapter_id) === cloud.get(chapter_id)?.position)
  ) {
    await recordChapterDirectoryRejection(scope, staged.request.change_id, null)
    staged = undefined
  }
  const knownPositions = new Set([...cloud.values()].map(({ position }) => position))
  const sameMembership = cloud.size === active.size && [...active.keys()].every((id) => cloud.has(id))
  const moves = sameMembership || knownPositions.size !== cloud.size ? desired : closedDirectoryMoves(desired)
  moves.sort((left, right) => left.position - right.position)
  const participants = new Set(
    moves.length >= 2 ? moves.map(({ chapter_id }) => `chapter:${chapter_id}`) : [],
  )
  const alreadyQueued = state.pending.filter((row) =>
    participants.has(row.kind === 'push' ? row.request.object_id : row.object_id),
  )
  if (
    state.enabled &&
    !staged &&
    participants.size >= 2 &&
    moves.length <= MAX_DIRECTORY_CHAPTERS &&
    knownPositions.size === cloud.size &&
    !alreadyQueued.length &&
    !state.blockedObjects.some((id) => participants.has(id)) &&
    moves.every((move) => {
      const { head } = cloud.get(move.chapter_id)!
      return (
        !deferred.has(head.id) &&
        !head.directoryParts?.length &&
        recognizesDirectoryPosition(head, scope.workId, move)
      )
    })
  ) {
    for (const move of moves) {
      const { head } = cloud.get(move.chapter_id)!
      const checked = await validateSyncedObject(project, head.id, head.receipt!.payload)
      if (checked?.kind !== 'chapter' || checked.chapter.position !== move.position)
        throw new Error('云端目录的原章稿依据无法核对。')
    }
    staged = await stageChapterDirectory(scope, {
      request: { change_id: crypto.randomUUID(), moves },
      publications: [...participants].flatMap((id) => {
        const marker = snapshot.publications.get(id)
        return marker ? [{ objectId: id, id: marker.id }] : []
      }),
    })
  }
  const frozen = new Set(staged?.request.moves.map(({ chapter_id }) => `chapter:${chapter_id}`))
  const held = new Map(
    [...new Set([...participants, ...frozen])].map((id) => [
      id,
      frozen.has(id) && staged?.error ? staged.error : '章位调整等待整组云端回执。',
    ]),
  )
  const prepared = new Set<string>()
  if (!staged) {
    const sourceSends = new Map(
      state.pending.flatMap((row) =>
        row.kind === 'push' && row.request.object_id.startsWith('revision:')
          ? [[row.request.object_id, row.request] as const]
          : [],
      ),
    )
    for (const row of alreadyQueued) {
      if (row.kind !== 'push') continue
      const { object_id, change_id, payload } = row.request
      const id = object_id.slice('chapter:'.length)
      if (knownChapterPosition(payload, scope.workId, id) !== cloud.get(id)?.position) continue
      prepared.add(change_id)
      const source = payload?.head
      let revision = source && typeof source === 'object' && !Array.isArray(source) ? source.id : undefined
      while (typeof revision === 'string') {
        const prior = sourceSends.get(`revision:${revision}`)
        if (!prior || prepared.has(prior.change_id)) break
        const details = prior.payload?.revision
        if (!details || typeof details !== 'object' || Array.isArray(details) || details.chapter_id !== id)
          break
        prepared.add(prior.change_id)
        revision = details.parent_id
      }
    }
  }
  return { staged, held, snapshot, outbox: { objects: new Set(held.keys()), prepared } }
}

async function exchangeChapterDirectory(
  project: LocalProject,
  scope: SyncScope,
  transport: SyncTransport,
  deferred: Map<string, string>,
) {
  let plan = await prepareChapterDirectory(project, scope, deferred)
  for (let attempt = 0; attempt < 2; attempt++) {
    const batch = plan.staged
    if (!batch || batch.error || !transport.applyChapterDirectory) break
    const active = await readSyncState(scope)
    if (!active.enabled || active.directory?.request.change_id !== batch.request.change_id) break
    try {
      const response = await transport.applyChapterDirectory(batch.request)
      verifyChapterDirectoryReceipt(scope, response, batch.request)
    } catch (error) {
      if (!(error instanceof SyncChapterDirectoryRejected)) throw error
      await recordChapterDirectoryRejection(scope, batch.request.change_id, error.message)
      plan = await prepareChapterDirectory(project, scope, deferred)
      break
    }
    await pull(scope, transport)
    await acknowledgePublications(scope)
    deferred = await reconcile(project, scope)
    plan = await prepareChapterDirectory(project, scope, deferred)
    if (plan.staged?.request.change_id === batch.request.change_id) break
  }
  return { ...plan, deferred }
}

function settingPublicationSources(object: SyncedObject | null): string[] {
  if (object?.kind === 'settings-head') return [`settings-version:${object.revision_id}`]
  if (object?.kind !== 'settings-version') return []
  const version = object.version
  const origins = Object.values(version.origins)
  const projects = new Set([
    version.project_version_id,
    ...origins.map(({ project_version_id }) => project_version_id),
  ])
  const revisions = new Set(origins.flatMap(({ evidence }) => (evidence ? [evidence.revision_id] : [])))
  return [
    ...(version.parent_id ? [`settings-version:${version.parent_id}`] : []),
    ...[...projects].flatMap((id) => (id ? [`project-version:${id}`] : [])),
    ...[...revisions].map((id) => `revision:${id}`),
  ]
}

function outlinePublicationSources(
  object: SyncedObject | null,
  current: OutlineVersion | undefined,
): string[] {
  if (object?.kind === 'outline-head' && current) {
    return [
      `outline-version:${object.revision_id}`,
      ...outlineChapterPlans(current.plan).map(({ chapter_id }) => `chapter:${chapter_id}`),
    ]
  }
  if (object?.kind !== 'outline-version') return []
  const { parent_id, project_version_id, settings_version_id, reference_cards } = object.version
  return [
    ...(parent_id ? [`outline-version:${parent_id}`] : []),
    ...(project_version_id ? [`project-version:${project_version_id}`] : []),
    ...(settings_version_id ? [`settings-version:${settings_version_id}`] : []),
    ...reference_cards.flatMap(({ version_id }) =>
      version_id ? [`reference-card-version:${version_id}`] : [],
    ),
  ]
}

function storyPublicationSources(object: SyncedObject | null, current: StoryVersion | undefined): string[] {
  if (object?.kind === 'story-head') {
    return [
      `story-version:${object.revision_id}`,
      ...Object.keys(current?.dependencies ?? {}).map((id) => `story:${id}`),
    ]
  }
  if (object?.kind !== 'story-version') return []
  const version = object.version
  return [
    ...(version.parent_id ? [`story-version:${version.parent_id}`] : []),
    ...Object.values(version.dependencies).map((id) => `story-version:${id}`),
    ...allStoryEvidence(version).map(({ revision_id }) => `revision:${revision_id}`),
  ]
}

function workflowPublicationSources(id: string, object: SyncedObject | null): string[] {
  if (object?.kind !== 'workflow-inspection' && object?.kind !== 'workflow-completion') return []
  const { report, revisionIds, proofIds } = workflowSyncEntry(object.branch_id, id, object)
  return [
    ...Array.from(revisionIds, (revisionId) => `revision:${revisionId}`),
    ...Array.from(proofIds, (proofId) => `setting-use-version:${proofId}`),
    ...(report ? [`workflow-inspection:${report}`] : []),
  ]
}

function settingUsePublicationSources(object: SettingUseSyncObject): string[] {
  if (object.kind === 'setting-use-head') return [`setting-use-version:${object.revision_id}`]
  const version = object.version
  return [
    ...(version.parent_id ? [`setting-use-version:${version.parent_id}`] : []),
    `revision:${version.evidence.revision_id}`,
    `settings-version:${version.settings_version_id}`,
    `outline-version:${version.outline_version_id}`,
  ]
}

function referencePublicationSource(object: SyncedObject | null) {
  const revision =
    object?.kind === 'reference-card-head'
      ? object.revision_id
      : object?.kind === 'reference-card-version'
        ? object.version.parent_id
        : null
  return revision ? `reference-card-version:${revision}` : null
}

function assertOutlineSources(
  snapshot: Pick<Awaited<ReturnType<typeof localObjects>>, 'outline' | 'settings' | 'versions' | 'reference'>,
  version: OutlineVersion,
) {
  const { outline, settings, versions, reference } = snapshot
  const advanced = new Set(
    outline.promises.filter(({ advanced, archived }) => advanced && !archived).map(({ id }) => id),
  )
  const required = requiredOutlineStoryRecord(version, outline.restorePending, advanced)
  if (required) throw new Error(`等待原伏笔记录 ${required} 的实际进展或历史恢复。`)
  validateOutlineActiveBasis(version, {
    projectVersionId: outline.projectVersionId,
    settingsVersionId: outline.settings?.id ?? null,
  })
  return validateOutlineVersionSources(version, {
    project: (id) => versions.some(({ version }) => version.id === id),
    settings: (id) => settings.versions.find(({ version }) => version.id === id)?.version,
    outline: (id) => outline.versions.some(({ version }) => version.id === id),
    chapter: (id) => outline.chapters.find((chapter) => chapter.id === id),
    promise: (id) => outline.promises.find((promise) => promise.id === id),
    reference: (id) => reference?.versions.find((card) => card.id === id),
  })
}

async function draftChanges(
  scope: SyncScope,
  deferred: Map<string, string>,
  directoryHeld: Map<string, string>,
  snapshot: Awaited<ReturnType<typeof localObjects>>,
) {
  const unsent = new Map<string, string>()
  const state = await readSyncState(scope)
  const pending = new Set(
    state.pending.map((entry) => (entry.kind === 'push' ? entry.request.object_id : entry.object_id)),
  )
  const heads = new Map((await listSyncHeads(scope)).map((head) => [head.id, head]))
  const { objects, publications, settings, outline, story } = snapshot
  const privacy = workflowTransferRestrictions([...objects, ...pendingPayloads(state.pending)])
  const storyHeads = new Map(story.records.map(({ version }) => [version.record_id, version]))
  for (const [id, object] of objects) {
    if (privacy.deferred.has(id)) continue
    const directoryReason = directoryHeld.get(id)
    if (directoryReason) {
      unsent.set(id, directoryReason)
      continue
    }
    if (object?.kind === 'revision') {
      const member = `chapter:${object.revision.chapter_id}`
      if (heads.get(member)?.directoryParts?.length || directoryHeld.has(member)) continue
    }
    if (object && new TextEncoder().encode(JSON.stringify(object)).length > 1024 * 1024 - 4096) {
      unsent.set(id, '本机对象超过单项同步限制，可导出稿件包供独立工作区检查。')
      continue
    }
    const marker = publications.get(id)
    const head = heads.get(id)
    const base = head?.baseline
    const signature = syncObjectSignature(object)
    if (
      (!marker && signature === (base?.signature ?? 'null')) ||
      pending.has(id) ||
      deferred.has(id) ||
      state.blockedObjects.includes(id)
    )
      continue
    if (
      object?.kind === 'settings-head' &&
      settings.current?.version.project_version_id !== settings.projectVersionId
    ) {
      unsent.set(id, '当前设定依据的立项已变化，请复核并重新确认后同步。')
      continue
    }
    if (settingPublicationSources(object).some((source) => !heads.has(source) && !pending.has(source))) {
      unsent.set(id, '立项、正文或父级设定来源尚未排到云端，当前设定继续保留。')
      continue
    }
    if (object?.kind === 'outline-head' && outline.current) {
      try {
        await assertOutlineSources(snapshot, outline.current.version)
      } catch (error) {
        unsent.set(id, error instanceof Error ? error.message : '当前大纲尚待核对。')
        continue
      }
    }
    if (
      outlinePublicationSources(object, outline.current?.version).some(
        (source) => !heads.has(source) && !pending.has(source),
      )
    ) {
      unsent.set(id, '立项、设定、章节或父级大纲来源尚未排到云端，当前大纲继续保留。')
      continue
    }
    const storyHead = object?.kind === 'story-head' ? storyHeads.get(object.record_id) : undefined
    if (storyHead) {
      try {
        validateStoryHeadSelection(storyHead, storyHeads)
      } catch (error) {
        unsent.set(id, error instanceof Error ? error.message : '当前故事记录待复检。')
        continue
      }
    }
    if (
      storyPublicationSources(object, storyHead).some((source) => !heads.has(source) && !pending.has(source))
    ) {
      unsent.set(id, '关联记录、准确原稿或父故事修订尚未排到云端，当前记录继续保留。')
      continue
    }
    if (workflowPublicationSources(id, object).some((source) => !heads.has(source) && !pending.has(source))) {
      unsent.set(id, '历史报告引用的准确章稿或全书体检尚未排到云端。')
      continue
    }
    if (
      (object?.kind === 'setting-use-head' || object?.kind === 'setting-use-version') &&
      settingUsePublicationSources(object).some((source) => !heads.has(source) && !pending.has(source))
    ) {
      unsent.set(id, '设定证据仍等待准确章稿、确认设定、大纲或父修订。')
      continue
    }
    if (
      isRestoreStateSyncObject(object) &&
      restoreStatePublicationSources(object).some((source) => !heads.has(source) && !pending.has(source))
    ) {
      unsent.set(id, '正式事实或篇幅目标仍等待准确的章稿、大纲或父修订。')
      continue
    }
    const cardSource = referencePublicationSource(object)
    if (cardSource && !heads.has(cardSource) && !pending.has(cardSource)) {
      unsent.set(id, '手法卡的确切源修订尚未排到云端。')
      continue
    }
    const acknowledged = head?.lastPublication
    const parent =
      marker && acknowledged && acknowledged.sequence > (base?.sequence ?? 0)
        ? acknowledged.id
        : base?.revision
    await queueSyncChange(
      scope,
      {
        object_id: id,
        parent_revision: parent ?? null,
        payload: object as SyncPayload | null,
        tombstone: object === null,
      },
      marker ? await publicationChangeId(scope, marker) : undefined,
    )
    pending.add(id)
  }
  return { unsent, privacy }
}

export async function readWebManuscriptSyncState(scope: SyncScope) {
  const [state, localPending] = await Promise.all([
    readSyncProgress(scope),
    countLocalPendingPublications(scope.workId),
  ])
  return { ...state, localPending }
}

function withWebSyncLock<T>(scope: SyncScope, exchange: () => Promise<T>) {
  if (typeof navigator !== 'undefined' && navigator.locks) {
    return navigator.locks.request(`ainovel-sync/${scope.workId}`, { ifAvailable: true }, (lock) => {
      if (!lock) throw new Error('另一标签页正在同步此作品，请稍后重试。')
      return exchange()
    })
  }
  return exchange()
}

type ChapterOrderTransport = Pick<
  ReturnType<typeof httpSyncTransport>,
  keyof SyncTransport | 'resolveChapterOrder'
>

async function locallyScheduledChapters(project: LocalProject) {
  const { current } = await readLocalOutline(project.novel.id)
  return new Set(current ? outlineChapterPlans(current.version.plan).map(({ chapter_id }) => chapter_id) : [])
}

export async function readWebChapterOrderConflicts(
  project: LocalProject,
  transport: Pick<ReturnType<typeof httpSyncTransport>, 'chapterOrderConflicts'>,
) {
  const conflicts = await transport.chapterOrderConflicts()
  if (conflicts.length === 0) return conflicts
  const scheduled = await locallyScheduledChapters(project)
  return conflicts.map((conflict) => ({
    ...conflict,
    chapters: conflict.chapters.map((chapter) => ({
      ...chapter,
      scheduled: chapter.scheduled || scheduled.has(chapter.chapter_id),
    })),
  }))
}

export async function resolveWebChapterOrder(
  project: LocalProject,
  scope: SyncScope,
  transport: ChapterOrderTransport,
  selection: ChapterOrderResolution,
  ledgerId: string,
) {
  if (scope.workId !== project.branch.id) throw new Error('同步范围与当前工作版本不一致。')
  return withWebSyncLock(scope, async () => {
    const state = await readSyncState(scope)
    if (!state.enabled) throw new Error('请先为当前作品启用同步。')
    if (state.ledgerId !== ledgerId) throw new SyncLedgerChanged()
    await transport.verifyLedger()
    await transport.resolveChapterOrder(selection, ledgerId, async () => {
      const [scheduled, publications, latest] = await Promise.all([
        locallyScheduledChapters(project),
        readLocalPublications(scope.workId),
        readSyncState(scope),
      ])
      if (scheduled.has(selection.chapter_id))
        throw new SyncResolutionChanged('该章已编入本机正式大纲，请保留其章位。')
      const affected = new Set(Object.keys(selection.heads).map((id) => `chapter:${id}`))
      if (
        publications.some(({ objectId }) => affected.has(objectId)) ||
        latest.blockedObjects.some((id) => affected.has(id)) ||
        latest.pending.some((row) =>
          affected.has(row.kind === 'push' ? row.request.object_id : row.object_id),
        )
      )
        throw new SyncResolutionChanged('这些章节有待同步的本机修改或分歧，请先核对正文与目录。')
    })
    await pull(scope, transport)
    await acknowledgePublications(scope)
    let deferred = await reconcile(project, scope)
    // Another received chapter may have been held at the position just freed by this receipt.
    if ([...deferred.keys()].some((id) => id.startsWith('chapter:')))
      deferred = await reconcile(project, scope)
    return {
      ...(await readWebManuscriptSyncState(scope)),
      deferred: [...deferred].map(([object_id, reason]) => ({ object_id, reason })),
    }
  })
}

export async function syncWebManuscript(project: LocalProject, scope: SyncScope, transport: SyncTransport) {
  if (scope.workId !== project.branch.id) throw new Error('同步范围与当前工作版本不一致。')
  async function exchange() {
    await transport.verifyLedger?.()
    await pull(scope, transport)
    await acknowledgePublications(scope)
    let directory = await exchangeChapterDirectory(project, scope, transport, await reconcile(project, scope))
    let outgoing = await draftChanges(scope, directory.deferred, directory.held, directory.snapshot)
    const rejected = new Map<string, string>()
    let superseded = false
    const onRestoreRejected: NonNullable<Parameters<typeof pushSyncPending>[4]> = async (change, error) => {
      const id = change.kind === 'push' ? change.request.object_id : change.object_id
      if (!isRestoreStateObjectId(id)) return false
      if (change.kind === 'push' && error.code === 'sync_restore_state_dependency') {
        const sent = change.request.payload
        if (sent?.kind === 'fact-set-head' || sent?.kind === 'length-target-head') {
          const { objects, publications } = await localObjects(project)
          const current = objects.get(id) ?? null
          if (
            publications.has(id) &&
            isRestoreStateSyncObject(current) &&
            !isRestoreStateVersion(current) &&
            current.revision_id !== sent.revision_id
          ) {
            superseded = true
            return 'discard'
          }
        }
      }
      rejected.set(id, error.message)
      return 'retain'
    }
    await pushSyncPending(
      scope,
      transport,
      undefined,
      outgoing.privacy.inspectionIds,
      onRestoreRejected,
      directory.outbox,
    )
    if (superseded) {
      await acknowledgePublications(scope)
      directory = await exchangeChapterDirectory(project, scope, transport, await reconcile(project, scope))
      outgoing = await draftChanges(scope, directory.deferred, directory.held, directory.snapshot)
      await pushSyncPending(
        scope,
        transport,
        undefined,
        outgoing.privacy.inspectionIds,
        onRestoreRejected,
        directory.outbox,
      )
    }
    await pull(scope, transport)
    await acknowledgePublications(scope)
    const remaining = await reconcile(project, scope)
    if (rejected.size) {
      const stillPending = new Set(
        (await readSyncState(scope)).pending.map((change) =>
          change.kind === 'push' ? change.request.object_id : change.object_id,
        ),
      )
      for (const id of rejected.keys()) if (!stillPending.has(id)) rejected.delete(id)
    }
    return {
      ...(await readWebManuscriptSyncState(scope)),
      deferred: [
        ...new Map([...outgoing.unsent, ...remaining, ...outgoing.privacy.deferred, ...rejected]),
      ].map(([object_id, reason]) => ({
        object_id,
        reason,
      })),
    }
  }
  return withWebSyncLock(scope, exchange)
}

export async function prepareWebResolution(project: LocalProject, scope: SyncScope, id: string) {
  const compared = await readSyncConflict(scope, id)
  if (!compared?.current || compared.resolvedBy) throw new Error('冲突已变化，请再次同步。')
  const {
    objects,
    publications,
    versions,
    settings,
    outline,
    story,
    reference,
    settingUse,
    restoreStateSync,
  } = await localObjects(project)
  const object = objects.get(compared.incoming.object_id) ?? null
  const publicationId = publications.get(compared.incoming.object_id)?.id ?? null
  return {
    ...compared,
    current: compared.current,
    object,
    versions,
    settings,
    outline,
    story,
    reference,
    settingUse,
    restoreStateSync,
    signature: syncObjectSignature(object),
    publicationId,
  }
}

type WebComparison = Awaited<ReturnType<typeof prepareWebResolution>>
export type WebForkChoice = 'local' | 'current' | 'incoming' | 'base'
const forkLabels = {
  local: '本机当前版',
  current: '云端当前版',
  incoming: '另一提交版',
  base: '共同依据',
} as const

function comparisonSource(compared: WebComparison, choice: WebForkChoice) {
  switch (choice) {
    case 'local':
      return compared.object
    case 'current':
      return compared.current.payload
    case 'incoming':
      return compared.incoming.payload
    case 'base':
      return compared.base?.payload
  }
}

export function webForkChoices(compared: WebComparison) {
  if (!compared.incoming.object_id.startsWith('chapter:')) return []
  const choices: WebForkChoice[] = ['local', 'current', 'incoming', 'base']
  return choices.flatMap((choice) =>
    comparisonSource(compared, choice)?.kind === 'chapter' ? [{ choice, label: forkLabels[choice] }] : [],
  )
}

function exactChapterHead(value: WebComparison['object']) {
  return value?.kind === 'chapter' ? value.head.id : null
}

function checkWebComparison(compared: WebComparison, fresh: WebComparison) {
  if (
    fresh.current.change_id !== compared.current.change_id ||
    fresh.signature !== compared.signature ||
    exactChapterHead(fresh.object) !== exactChapterHead(compared.object) ||
    fresh.publicationId !== compared.publicationId
  )
    throw new Error('本机或云端版本已变化，请重新比较。')
}

export async function prepareWebSettingsMerge(
  project: LocalProject,
  scope: SyncScope,
  compared: WebComparison,
): Promise<SettingsConflictMerge> {
  const conflictId = compared.incoming.conflict_id
  if (!conflictId || scope.workId !== project.branch.id) throw new Error('当前作品找不到这次设定冲突。')
  const fresh = await prepareWebResolution(project, scope, conflictId)
  checkWebComparison(compared, fresh)
  const local = fresh.settings.current
  const cloudCurrent = fresh.current.payload
  const cloudIncoming = fresh.incoming.payload
  if (
    !local ||
    fresh.object?.kind !== 'settings-head' ||
    cloudCurrent?.kind !== 'settings-head' ||
    cloudIncoming?.kind !== 'settings-head'
  )
    throw new Error('合写需要本机采用版和两份有效设定来源。')
  const current = fresh.settings.versions.find(({ version }) => version.id === cloudCurrent.revision_id)
  const incoming = fresh.settings.versions.find(({ version }) => version.id === cloudIncoming.revision_id)
  if (!current?.source || !incoming?.source) throw new Error('还缺设定原修订，请同步后再合写。')
  if (
    [local, current, incoming].some(
      (row) => row.version.project_version_id !== fresh.settings.projectVersionId,
    )
  )
    throw new Error('各版设定使用的立项不同，请先核对并统一立项依据。')
  return { conflictId, localRevisionId: local.version.id, current, incoming }
}

export async function forkWebConflict(
  project: LocalProject,
  scope: SyncScope,
  compared: WebComparison,
  choice: WebForkChoice,
  name: string,
) {
  const id = compared.incoming.conflict_id
  if (!id || scope.workId !== project.branch.id) throw new Error('当前作品找不到可另存的冲突比较。')
  const fresh = await prepareWebResolution(project, scope, id)
  checkWebComparison(compared, fresh)
  if (!webForkChoices(fresh).some((option) => option.choice === choice))
    throw new Error('请选择仍有正文的比较版本。')
  const source = await validateSyncedObject(
    project,
    fresh.incoming.object_id,
    comparisonSource(fresh, choice),
  )
  if (source?.kind !== 'chapter') throw new Error('尚未取得所选章节的准确来源。')
  return forkLocalManuscript(
    project.novel.id,
    {
      chapterId: source.chapter.id,
      revisionId: source.head.id,
      content: source.head.content,
      sha256: source.head.sha256,
    },
    name,
    {
      branchId: project.branch.id,
      currentChapter: fresh.signature,
      currentRevisionId: exactChapterHead(fresh.object),
      publicationId: fresh.publicationId,
    },
  )
}

export async function resolveWebManuscript(
  project: LocalProject,
  scope: SyncScope,
  transport: SyncTransport,
  compared: WebComparison,
  choice: 'current' | 'incoming' | 'local' | { choice: 'merge_prose'; content: string },
) {
  // A comparison can be opened offline. Only a current ledger may receive its approval.
  await transport.verifyLedger?.()
  const id = compared.incoming.conflict_id!
  if (typeof choice === 'object') {
    const waiting = (await readSyncState(scope)).pending.find(
      (entry) => entry.kind === 'resolve' && entry.conflict_id === id,
    )
    if (waiting?.kind === 'resolve') {
      if (waiting.request.choice !== 'merge_prose' || waiting.request.content !== choice.content)
        throw new Error('已有一份合并请求等待回执，请先同步并核对结果。')
      return syncWebManuscript(project, scope, transport)
    }
  }
  const fresh = await prepareWebResolution(project, scope, id)
  checkWebComparison(compared, fresh)
  if (choice === 'local' && fresh.object?.kind === 'project-head') {
    const selected = fresh.object.revision_id
    if (!fresh.versions.some(({ version, source }) => source && version.id === selected))
      throw new Error('本机立项刚有新确认，请先同步源修订，再比较冲突。')
  }
  if (fresh.incoming.object_id.startsWith('settings:')) {
    const chosen =
      choice === 'local'
        ? fresh.object
        : choice === 'current'
          ? fresh.current.payload
          : fresh.incoming.payload
    const selection = chosen?.kind === 'settings-head' && 'revision_id' in chosen ? chosen.revision_id : null
    const entry = fresh.settings.versions.find(({ version }) => version.id === selection)
    if (!entry?.source) throw new Error('尚未取得所选设定的云端来源，请先同步源修订，再比较冲突。')
    if (entry.version.project_version_id !== fresh.settings.projectVersionId)
      throw new Error('所选设定依据的立项与本机不同，请先采用对应立项，再解决冲突。')
  }
  if (fresh.incoming.object_id.startsWith('outline:')) {
    const chosen =
      choice === 'local'
        ? fresh.object
        : choice === 'current'
          ? fresh.current.payload
          : fresh.incoming.payload
    const selection = chosen?.kind === 'outline-head' ? chosen.revision_id : null
    const entry = fresh.outline.versions.find(({ version }) => version.id === selection)
    if (!entry?.source) throw new Error('尚未取得所选大纲的云端来源，请先同步源修订，再比较冲突。')
    await assertOutlineSources(fresh, entry.version)
  }
  if (fresh.incoming.object_id.startsWith('story:')) {
    const chosen =
      choice === 'local'
        ? fresh.object
        : choice === 'current'
          ? fresh.current.payload
          : fresh.incoming.payload
    if (
      chosen?.kind !== 'story-head' ||
      typeof chosen.record_id !== 'string' ||
      typeof chosen.revision_id !== 'string'
    )
      throw new Error('无法采用未经核验的故事版本。')
    await checkLocalStorySelection(project.novel.id, chosen.record_id, chosen.revision_id)
  }
  if (fresh.incoming.object_id.startsWith('reference-card:')) {
    const chosen =
      choice === 'local'
        ? fresh.object
        : choice === 'current'
          ? fresh.current.payload
          : fresh.incoming.payload
    const selected = await validateSyncedObject(project, fresh.incoming.object_id, chosen)
    if (selected?.kind !== 'reference-card-head') throw new Error('无法采用未核验的手法卡版本。')
    const source = fresh.reference?.versions.find((entry) => entry.id === selected.revision_id)
    if (
      !source ||
      referenceCardKey(source) !== referenceCardKey(selected) ||
      !(await listSyncHeads(scope)).some(({ id }) => id === `reference-card-version:${source.id}`)
    )
      throw new Error('尚未取得该手法卡与云端对应的源修订，请先同步再比较。')
  }
  if (fresh.incoming.object_id.startsWith('setting-use:')) {
    const chosen =
      choice === 'local'
        ? fresh.object
        : choice === 'current'
          ? fresh.current.payload
          : fresh.incoming.payload
    const selected = await validateSyncedObject(project, fresh.incoming.object_id, chosen)
    const source =
      selected?.kind === 'setting-use-head' &&
      fresh.settingUse?.versions.find(({ version }) => version.id === selected.revision_id)
    if (
      !source ||
      selected?.kind !== 'setting-use-head' ||
      selected.slot_id !== (await settingUseVersionSlot(project.branch.id, source.version, manuscriptHash)) ||
      !(await listSyncHeads(scope)).some(({ id }) => id === `setting-use-version:${source.version.id}`)
    )
      throw new Error('尚未取得该证据对应的源修订，请先同步后再比较。')
  }
  if (
    fresh.incoming.object_id.startsWith('fact-set-head:') ||
    fresh.incoming.object_id.startsWith('length-target-head:')
  ) {
    const chosen =
      choice === 'local'
        ? fresh.object
        : choice === 'current'
          ? fresh.current.payload
          : fresh.incoming.payload
    const selected = await validateSyncedObject(project, fresh.incoming.object_id, chosen)
    const source =
      isRestoreStateSyncObject(selected) &&
      !isRestoreStateVersion(selected) &&
      fresh.restoreStateSync.versions.find(({ version }) => version.id === selected.revision_id)
    if (
      !source ||
      !isRestoreStateSyncObject(selected) ||
      restoreStateSlot(source) !== restoreStateSlot(selected)
    )
      throw new Error('等待所选事实或篇幅目标的准确修订后，再处理冲突。')
    const id = await restoreStateObjectId(source, manuscriptHash)
    if (!(await listSyncHeads(scope)).some((entry) => entry.id === id))
      throw new Error('请先同步所选事实或篇幅目标的准确修订。')
  }
  if (
    typeof choice === 'object' &&
    (!fresh.incoming.object_id.startsWith('chapter:') ||
      fresh.object?.kind !== 'chapter' ||
      fresh.current.payload?.kind !== 'chapter' ||
      fresh.incoming.payload?.kind !== 'chapter')
  )
    throw new Error('手动合并需要本机稿及两份仍可用的冲突正文。')
  const resolutionId = crypto.randomUUID()
  const objectId = compared.incoming.object_id
  await approveSyncResolution(scope, objectId, {
    changeId: resolutionId,
    signature: compared.signature,
    publicationId: compared.publicationId,
  })
  const parent_revision = compared.current.change_id
  await queueSyncResolution(
    scope,
    objectId,
    id,
    typeof choice === 'object'
      ? { ...choice, parent_revision }
      : choice === 'local'
        ? {
            choice: 'merge',
            parent_revision,
            payload: fresh.object as SyncPayload | null,
            tombstone: !fresh.object,
          }
        : { choice, parent_revision },
    resolutionId,
  )
  return syncWebManuscript(project, scope, transport)
}
