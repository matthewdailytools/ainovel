import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { IDBFactory, IDBObjectStore } from 'fake-indexeddb'
import {
  acknowledgeLocalPublications,
  applySyncedObject,
  confirmLocalOutline,
  confirmLocalProjectBrief,
  createLocalChapter,
  createLocalProject,
  exportLocalManuscript,
  importLocalManuscript,
  manuscriptHash,
  readLocalChapterRestore,
  readLocalCheckpointRestore,
  readLocalHistory,
  readLocalPublications,
  readLocalRulePreview,
  readLocalWorkflowHistory,
  restoreLocalChapter,
  restoreLocalCheckpoint,
  saveLocalChapter,
  syncManuscriptObjects,
  syncObjectSignature,
  validateSyncedObject,
} from './offlineStore'
import {
  httpSyncTransport,
  readWebChapterOrderConflicts,
  resolveWebChapterOrder,
  syncWebManuscript,
} from './manuscriptSync'
import {
  listSyncHeads,
  markSyncBaseline,
  pullSyncPage,
  pushSyncPending,
  queueSyncChange,
  queueSyncResolution,
  readSyncConflict,
  readSyncState,
  setSyncEnabled,
  SyncRestoreStateRejected,
  type ChapterDirectoryRequest,
  type PushChange,
  type SyncReceipt,
  type SyncTransport,
} from './syncClient'
import { inspectionExcerptTransferReason } from './workflowPrivacy'
import { buildRestoreStateVersion, restoreStateHead, restoreStateObjectId } from './restoreStateSyncModel'
import { captureStoryEvidence, compactJSON } from './storySyncModel'
import type { PortableCheckpoint } from './portableCheckpoint'

beforeEach(() => {
  globalThis.indexedDB = new IDBFactory()
})
afterEach(() => vi.restoreAllMocks())

test.each([false, true])(
  'replacing the ledger preserves an old queued submit, lost response=%s',
  async (lost) => {
    const scope = { service: 'http://127.0.0.1:8888', workId: 'work', deviceId: 'old-device' }
    const original = 'a'.repeat(32)
    const recovered = 'b'.repeat(32)
    let ledger = original
    const requests: { path: string; ledger: string | null }[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
      const path = new URL(String(url)).pathname.split(`/works/${scope.workId}`)[1]
      const header = new Headers(init?.headers).get('X-Ainovel-Ledger')
      requests.push({ path, ledger: header })
      if (path === '/checkpoint') return Response.json({ ledger_id: ledger, cursor: 9 })
      if (header !== ledger) return Response.json({ code: 'sync_ledger_replaced' }, { status: 409 })
      expect(path).toBe('/push')
      ledger = recovered
      throw new Error('原服务写入后响应丢失')
    })
    const transport = httpSyncTransport(scope, 'a'.repeat(32))
    await transport.enable()
    await queueSyncChange(
      scope,
      {
        object_id: 'chapter:old',
        parent_revision: null,
        payload: { content: '保留原请求' },
        tombstone: false,
      },
      'original-request',
    )
    const before = await readSyncState(scope)
    if (!lost) ledger = recovered
    await expect(pushSyncPending(scope, transport)).rejects.toMatchObject({ code: 'ledger' })
    expect(await readSyncState(scope)).toEqual(before)
    expect(requests).toEqual([
      { path: '/checkpoint', ledger: null },
      { path: '/push', ledger: original },
      ...(lost ? [{ path: '/receipts/original-request', ledger: original }] : []),
    ])
    await expect(transport.verifyLedger?.()).rejects.toMatchObject({ code: 'ledger' })
    const other = httpSyncTransport({ ...scope, deviceId: 'replacement-device' }, 'b'.repeat(32))
    await expect(other.enable()).rejects.toMatchObject({ code: 'ledger' })
  },
)

function service(device: string, objectId: string, acceptedBeforeDisconnect: boolean) {
  const submitted: PushChange[] = []
  const log: SyncReceipt[] = []
  const latest = new Map<string, SyncReceipt>()
  let disconnect = true
  async function commit(request: PushChange, actor = device) {
    submitted.push(request)
    const lost = disconnect && request.object_id === objectId
    if (lost) {
      disconnect = false
      if (!acceptedBeforeDisconnect) throw new Error('请求发送中断')
    }
    const old = latest.get(request.object_id)
    const conflict = (old?.change_id ?? null) !== request.parent_revision
    const receipt: SyncReceipt = {
      ...request,
      sequence: log.length + 1,
      status: conflict ? 'conflict' : 'applied',
      device_id: actor,
      head_revision: conflict ? old!.change_id : request.change_id,
      conflict_id: conflict ? request.change_id : null,
    }
    log.push(receipt)
    if (!conflict) latest.set(receipt.object_id, receipt)
    if (lost) throw new Error('响应丢失')
    return receipt
  }
  const transport: SyncTransport = {
    push: commit,
    async receipt() {
      return null
    },
    async pull(after) {
      return { changes: log.slice(after), cursor: log.length, has_more: false }
    },
    async resolve() {
      throw new Error('没有请求决议')
    },
  }
  return { transport, commit, latest, submitted, log }
}

async function pushDirectoryFixture(
  cloud: ReturnType<typeof service>,
  workId: string,
  ordered: { id: string; position: number }[],
  changeId = 'swapped-directory',
  invalidChild = false,
  actor = 'desktop',
) {
  const moves = []
  for (const [index, chapter] of ordered.entries()) {
    const objectId = `chapter:${chapter.id}`
    const parent = cloud.latest.get(objectId)!
    const payload = JSON.parse(JSON.stringify(parent.payload))
    const position = payload.chapter.position as number
    const receiptId = `chapter-order-part-${await manuscriptHash(JSON.stringify([workId, changeId, chapter.id]))}`
    payload.chapter.position = chapter.position
    if (index === 0 && invalidChild) payload.chapter.title = '不是单纯换序'
    await cloud.commit(
      {
        object_id: objectId,
        change_id: receiptId,
        parent_revision: parent.change_id,
        payload,
        tombstone: false,
      },
      actor,
    )
    moves.push({
      chapter_id: chapter.id,
      position,
      next_position: chapter.position,
      head_revision: parent.change_id,
      receipt_id: receiptId,
    })
  }
  return cloud.commit(
    {
      object_id: `chapter-directory:${changeId}`,
      change_id: changeId,
      parent_revision: null,
      payload: {
        format: 'ainovel-manuscript-sync/1',
        kind: 'chapter-directory',
        branch_id: workId,
        moves,
      },
      tombstone: false,
    },
    actor,
  )
}

test.each([
  'committed-before-disconnect',
  'committed-then-newer-directory',
  'disconnected-before-commit',
  'stale-comparison',
  'archive-outside-closed-cycle',
  'prepared-prose-before-directory',
] as const)('outgoing Web directory publishes its entire checkpoint order, outcome=%s', async (outcome) => {
  const created = await createLocalProject('浏览器恢复目录后同步')
  const key = created.novel.id
  const first = await createLocalChapter(key, '初章')
  const second = await createLocalChapter(key, '次章')
  const [initial] = await readLocalHistory(key, first.id)
  const original = await saveLocalChapter(key, first.id, initial.id, '共同采用的章稿。')
  const pack = await exportLocalManuscript(key)
  const checkpoint: PortableCheckpoint = {
    version: 'ainovel-portable-checkpoint/1',
    id: 'checkpoint-swapped-directory',
    branch_id: created.branch.id,
    label: '次章在前的检查点',
    scope: 'book',
    scope_id: null,
    purpose: 'manual',
    created: 1,
    source_manifest_sha256: await manuscriptHash('独立书稿包目录'),
    story_revision: pack.branch.story_revision,
    scoped_chapter_ids: [second.id, first.id],
    chapters: [...pack.chapters].reverse().map((chapter, index) => {
      const revision = pack.revisions.find(
        (record) => record.chapter_id === chapter.id && record.revision === chapter.revision,
      )!
      return {
        id: chapter.id,
        position: index + 1,
        title: chapter.title,
        revision: chapter.revision,
        revision_id: revision.id,
        sha256: revision.sha256,
        archived: chapter.archived,
        stale: chapter.stale,
      }
    }),
    shared: { project_id: null, settings_id: null, outline_id: null },
    reports: {},
    restore_sources: {
      version: 'ainovel-portable-restore-sources/1',
      outline_live: false,
      story_heads: {},
      length_targets: [],
      facts: [],
      scene_spans_for_review: 0,
    },
  }
  const body = JSON.stringify(checkpoint)
  globalThis.indexedDB = new IDBFactory()
  const project = await importLocalManuscript(
    new Blob([
      JSON.stringify({
        ...pack,
        snapshot_history: { entries: [{ id: checkpoint.id, sha256: await manuscriptHash(body), body }] },
      }),
    ]),
  )
  const archived =
    outcome === 'archive-outside-closed-cycle' ? await createLocalChapter(key, '检查点之后的章') : null
  const archiveHistory = archived ? await readLocalHistory(key, archived.id) : []
  const scope = {
    service: 'http://127.0.0.1:8888',
    workId: project.branch.id,
    deviceId: 'browser-publisher',
  }
  const cloud = service(scope.deviceId, 'unused', false)
  const token = 'b'.repeat(32)
  const ledger = 'a'.repeat(32)
  const attempts: ChapterDirectoryRequest[] = []
  const ordinary: PushChange[] = []
  const accepted: SyncReceipt[] = []
  const stale = outcome === 'stale-comparison'
  const prepared = outcome === 'prepared-prose-before-directory'
  const newerDirectory = outcome === 'committed-then-newer-directory'
  const expectedOrder = newerDirectory ? [first.id, second.id] : [second.id, first.id]
  const firstPosition = newerDirectory ? 1 : 2
  let disconnected = false
  let stopPreparedSource = prepared
  let newest: typeof original | undefined
  let recoveryId: string
  async function saveDraft(content: string) {
    const head = (await readLocalHistory(key, first.id)).at(-1)!
    newest = await saveLocalChapter(key, first.id, head.id, content)
  }

  vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
    if (disconnected) throw new Error('同步传输中断')
    const target = new URL(String(url))
    const path = target.pathname.split(`/works/${scope.workId}`)[1]
    if (path === '/checkpoint') return Response.json({ ledger_id: ledger, cursor: cloud.log.length })
    expect(new Headers(init?.headers).get('X-Ainovel-Ledger')).toBe(ledger)
    if (path === '/pull') {
      return Response.json(await cloud.transport.pull(Number(target.searchParams.get('after'))))
    }
    if (path.startsWith('/receipts/') || path.startsWith('/changes/')) {
      const receipt = cloud.log.find(
        ({ change_id, device_id }) =>
          change_id === path.split('/')[2] && (path.startsWith('/changes/') || device_id === scope.deviceId),
      )
      return receipt ? Response.json(receipt) : new Response(null, { status: 404 })
    }
    if (path === '/push') {
      const request: PushChange = JSON.parse(String(init?.body))
      ordinary.push(request)
      if (stopPreparedSource && request.object_id === `revision:${newest?.id}`) {
        stopPreparedSource = false
        throw new Error('已排队的原章位稿件尚未送达')
      }
      return Response.json(await cloud.commit(request))
    }
    expect(path).toBe('/chapter-order-batches')
    expect(init?.method).toBe('POST')
    expect(target.searchParams.get('device_id')).toBe(scope.deviceId)
    const request: ChapterDirectoryRequest = JSON.parse(String(init?.body))
    attempts.push(request)
    if (stale && attempts.length === 1) {
      const parent = cloud.latest.get(`chapter:${first.id}`)!
      await cloud.commit(
        {
          object_id: parent.object_id,
          change_id: 'advanced-cloud-chapter',
          parent_revision: parent.change_id,
          payload: parent.payload,
          tombstone: false,
        },
        'desktop',
      )
    }
    if (stale) {
      if (attempts.length === 1) {
        expect(
          request.moves.some(
            (move) => cloud.latest.get(`chapter:${move.chapter_id}`)?.change_id !== move.head_revision,
          ),
        ).toBe(true)
      }
      return Response.json(
        { code: 'sync_chapter_order_changed', message: '章位比较以后已有新章头。' },
        { status: 409 },
      )
    }
    if (attempts.length > 1 || outcome !== 'disconnected-before-commit') {
      for (const move of request.moves) {
        expect(cloud.latest.get(`chapter:${move.chapter_id}`)?.change_id).toBe(move.head_revision)
      }
      accepted.push(
        await pushDirectoryFixture(
          cloud,
          scope.workId,
          request.moves.map((move) => ({ id: move.chapter_id, position: move.next_position })),
          request.change_id,
          false,
          scope.deviceId,
        ),
      )
    }
    if (attempts.length === 1 && !archived && !prepared) {
      if (newerDirectory) {
        const recovery = await readLocalCheckpointRestore(key, recoveryId)
        expect(recovery.blockers).toEqual([])
        await restoreLocalCheckpoint(key, recoveryId, recovery.basis, 'author-restores-newer-order')
      }
      await saveDraft('等待目录回执时另写的新稿。')
      disconnected = true
      throw new Error('目录提交时连接中断')
    }
    return Response.json(accepted.at(-1))
  })

  const transport = httpSyncTransport(scope, token)
  await transport.enable()
  expect((await syncWebManuscript(project, scope, transport)).deferred).toEqual([])
  const before = cloud.log.length
  const chapterHeads = new Map(
    [first.id, second.id].map((id) => [id, cloud.latest.get(`chapter:${id}`)!.change_id]),
  )
  let preparedChapter: PushChange | undefined
  if (prepared) {
    await saveDraft('已针对原章位排队的正文。')
    await expect(syncWebManuscript(project, scope, transport)).rejects.toThrow('已排队的原章位稿件尚未送达')
    const { pending } = await readSyncState(scope)
    const chapter = pending.find(
      (row) => row.kind === 'push' && row.request.object_id === `chapter:${first.id}`,
    )
    expect(chapter).toMatchObject({
      kind: 'push',
      request: { payload: { chapter: { position: 1 }, head: newest } },
    })
    if (chapter?.kind !== 'push') throw new Error('缺少已准备好的普通章稿请求')
    preparedChapter = chapter.request
    chapterHeads.set(first.id, preparedChapter.change_id)
  }
  ordinary.length = 0
  const preview = await readLocalCheckpointRestore(key, checkpoint.id)
  expect(preview.blockers).toEqual([])
  if (archived)
    expect(preview.changes).toContainEqual(expect.objectContaining({ id: archived.id, action: 'archive' }))
  recoveryId = (await restoreLocalCheckpoint(key, checkpoint.id, preview.basis, 'author-restores-order'))
    .recoveryCheckpointId
  const restoredHistory = await readLocalHistory(key, first.id)
  expect(restoredHistory.slice(0, 2)).toEqual([initial, original])
  if (stale) await saveDraft('比较失效仍保留的本机正文。')

  let firstSync = await syncWebManuscript(project, scope, transport).catch((error: unknown) => error)
  if (prepared && !attempts.length) firstSync = await syncWebManuscript(project, scope, transport)
  expect(attempts).toHaveLength(1)
  const request = attempts[0]
  expect(request.moves).toEqual(
    expect.arrayContaining([
      { chapter_id: first.id, position: 1, next_position: 2, head_revision: chapterHeads.get(first.id) },
      { chapter_id: second.id, position: 2, next_position: 1, head_revision: chapterHeads.get(second.id) },
    ]),
  )
  expect(request.moves).toHaveLength(2)
  if (archived) {
    expect(firstSync).toMatchObject({ deferred: [], conflicts: [] })
    expect(accepted).toHaveLength(1)
    const tombstone = cloud.latest.get(`chapter:${archived.id}`)!
    expect(tombstone).toMatchObject({ payload: null, tombstone: true, status: 'applied' })
    expect(tombstone.sequence).toBeGreaterThan(accepted[0].sequence)
    expect(ordinary.filter(({ tombstone: deleted }) => deleted)).toEqual([
      expect.objectContaining({ object_id: `chapter:${archived.id}` }),
    ])
    const current = await exportLocalManuscript(key)
    expect(current.chapters.filter(({ archived }) => !archived).map(({ id }) => id)).toEqual(expectedOrder)
    expect(current.chapters.find(({ id }) => id === archived.id)).toMatchObject({ archived: 1, position: 3 })
    expect(current.revisions.filter(({ chapter_id }) => chapter_id === archived.id)).toEqual(archiveHistory)
    return
  }
  if (preparedChapter) {
    expect(firstSync).toMatchObject({ deferred: [], conflicts: [] })
    expect(accepted).toHaveLength(1)
    const manifest = accepted[0]
    const cloudHistory = cloud.log.slice(before)
    expect(
      cloudHistory.filter(
        ({ sequence, object_id, change_id }) =>
          sequence < manifest.sequence &&
          object_id.startsWith('chapter:') &&
          !change_id.startsWith('chapter-order-part-'),
      ),
    ).toEqual([expect.objectContaining(preparedChapter)])
    const preparedSource = cloud.latest.get(`revision:${newest!.id}`)!
    const publishedPrepared = cloudHistory.find(({ change_id }) => change_id === preparedChapter.change_id)!
    const restoredSource = restoredHistory.at(-1)!
    expect(preparedSource.sequence).toBeLessThan(publishedPrepared.sequence)
    expect(cloud.latest.get(`revision:${restoredSource.id}`)?.sequence).toBeGreaterThan(manifest.sequence)
    expect(cloud.latest.get(`chapter:${first.id}`)?.payload).toMatchObject({
      chapter: { position: 2 },
      head: { id: restoredSource.id, content: original.content },
    })
    expect((await exportLocalManuscript(key)).chapters.map(({ id }) => id)).toEqual(expectedOrder)
    expect(await readLocalHistory(key, first.id)).toEqual(restoredHistory)
    return
  }
  expect((await readSyncState(scope)).directory?.request).toEqual(request)
  expect(ordinary.filter(({ object_id }) => object_id.startsWith('chapter:'))).toEqual([])
  expect((await exportLocalManuscript(key)).chapters.map(({ id }) => id)).toEqual(expectedOrder)
  const exactHistory = await readLocalHistory(key, first.id)
  const otherHistory = await readLocalHistory(key, second.id)
  expect(exactHistory.slice(0, restoredHistory.length)).toEqual(restoredHistory)
  expect(exactHistory.at(-1)).toEqual(newest)

  vi.resetModules()
  const restarted = await import('./manuscriptSync')
  disconnected = false
  const secondSync = await restarted
    .syncWebManuscript(project, scope, restarted.httpSyncTransport(scope, token))
    .catch((error: unknown) => error)
  if (stale) {
    for (const report of [firstSync, secondSync]) {
      expect(report).toMatchObject({
        deferred: expect.arrayContaining([expect.objectContaining({ reason: '章位比较以后已有新章头。' })]),
      })
    }
    expect(attempts).toEqual([request])
    expect((await readSyncState(scope)).directory?.request).toEqual(request)
    expect(accepted).toEqual([])
    expect(ordinary.filter(({ object_id }) => object_id.startsWith('chapter:'))).toEqual([])
    expect(cloud.log.slice(before).some(({ object_id }) => object_id.startsWith('chapter-directory:'))).toBe(
      false,
    )
    expect(cloud.latest.get(`chapter:${first.id}`)?.payload?.chapter).toMatchObject({ position: 1 })
    expect(cloud.latest.get(`chapter:${second.id}`)?.payload?.chapter).toMatchObject({ position: 2 })
  } else {
    expect(firstSync).toBeInstanceOf(Error)
    expect(secondSync).toMatchObject({ deferred: [], conflicts: [] })
    expect((await readSyncState(scope)).directory).toBeUndefined()
    if (newerDirectory) {
      expect(attempts).toHaveLength(2)
      expect(accepted).toHaveLength(2)
      expect(attempts[1].change_id).not.toBe(request.change_id)
      expect(attempts[1].moves).toEqual(
        expect.arrayContaining(
          request.moves.map((move) => ({
            chapter_id: move.chapter_id,
            position: move.next_position,
            next_position: move.position,
            head_revision: cloud.log.find(
              (receipt) =>
                receipt.object_id === `chapter:${move.chapter_id}` &&
                receipt.change_id.startsWith('chapter-order-part-'),
            )!.change_id,
          })),
        ),
      )
    } else {
      expect(attempts).toEqual(outcome === 'disconnected-before-commit' ? [request, request] : [request])
      expect(accepted).toHaveLength(1)
    }
    expect(accepted[0].change_id).toBe(request.change_id)
    const manifest = accepted.at(-1)!
    expect(
      cloud.log
        .slice(before, manifest.sequence - 1)
        .filter(
          ({ object_id, change_id }) =>
            object_id.startsWith('chapter:') && !change_id.startsWith('chapter-order-part-'),
        ),
    ).toEqual([])
    const moved = cloud.log
      .filter(
        (receipt) =>
          receipt.object_id === `chapter:${first.id}` && receipt.change_id.startsWith('chapter-order-part-'),
      )
      .at(-1)!
    const latest = cloud.latest.get(`chapter:${first.id}`)!
    expect(latest.sequence).toBeGreaterThan(manifest.sequence)
    expect(latest.parent_revision).toBe(moved.change_id)
    expect(latest.payload).toMatchObject({
      chapter: { position: firstPosition },
      head: { id: newest!.id, content: newest!.content },
    })
    expect(cloud.latest.get(`revision:${newest!.id}`)?.payload).toMatchObject({ revision: newest })
    expect(cloud.latest.get(`chapter:${second.id}`)?.payload?.chapter).toMatchObject({
      position: 3 - firstPosition,
    })
  }
  expect((await exportLocalManuscript(key)).chapters.map(({ id }) => id)).toEqual(expectedOrder)
  expect(await readLocalHistory(key, first.id)).toEqual(exactHistory)
  expect(await readLocalHistory(key, second.id)).toEqual(otherHistory)
})

test('directory adoption waits for the whole group and a later ordinary head stays applicable', async () => {
  const project = await createLocalProject('换序后接续正文')
  const key = project.novel.id
  const first = await createLocalChapter(key, '初章')
  const second = await createLocalChapter(key, '次章')
  const final = await createLocalChapter(key, '末章')
  const [initial] = await readLocalHistory(key, first.id)
  const prior = await saveLocalChapter(key, first.id, initial.id, '正式第一稿。')
  const original = await exportLocalManuscript(key)
  const scope = { service: 'http://127.0.0.1:8888', workId: project.branch.id, deviceId: 'receiver' }
  const cloud = service('desktop', 'unused', false)
  for (const [object_id, payload] of syncManuscriptObjects(original)) {
    await cloud.transport.push({
      object_id,
      change_id: object_id,
      parent_revision: null,
      payload,
      tombstone: false,
    })
  }
  await acknowledgeLocalPublications(await readLocalPublications(scope.workId))
  await setSyncEnabled(scope, true)
  await syncWebManuscript(project, scope, cloud.transport)
  const base = cloud.log.length
  await pushDirectoryFixture(cloud, scope.workId, [
    { id: first.id, position: 2 },
    { id: second.id, position: 1 },
  ])
  const moved = cloud.latest.get(`chapter:${first.id}`)!
  const head = {
    ...prior,
    id: 'later-remote-body',
    revision: prior.revision + 1,
    parent_id: prior.id,
    content: '换序后保存的正文。',
    sha256: await manuscriptHash('换序后保存的正文。'),
  }
  await cloud.transport.push({
    object_id: `revision:${head.id}`,
    change_id: 'later-source',
    parent_revision: null,
    payload: {
      format: 'ainovel-manuscript-sync/1',
      kind: 'revision',
      branch_id: scope.workId,
      revision: head,
    },
    tombstone: false,
  })
  const newest = JSON.parse(JSON.stringify(moved.payload))
  newest.head = head
  newest.chapter.revision = head.revision
  await cloud.transport.push({
    object_id: `chapter:${first.id}`,
    change_id: 'later-chapter-head',
    parent_revision: moved.change_id,
    payload: newest,
    tombstone: false,
  })
  const submitted = cloud.submitted.length
  const cut = {
    ...cloud.transport,
    async pull(after: number) {
      return { changes: cloud.log.slice(after, base + 1), cursor: base + 1, has_more: false }
    },
  }
  const partiallyReceived = await syncWebManuscript(project, scope, cut)
  expect(partiallyReceived.cursor).toBe(base + 1)
  expect(partiallyReceived.deferred.map(({ object_id }) => object_id)).toContain(`chapter:${first.id}`)
  expect((await exportLocalManuscript(key)).chapters).toEqual(original.chapters)
  expect(cloud.submitted).toHaveLength(submitted)

  const put = IDBObjectStore.prototype.put
  const failSecondWrite = vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (
    this: IDBObjectStore,
    value,
    id,
  ) {
    if (this.name === 'chapters' && value?.id === second.id && value?.position === 1)
      throw new DOMException('synthetic disk exhaustion', 'QuotaExceededError')
    return put.call(this, value, id)
  })
  const rejected = await syncWebManuscript(project, scope, cloud.transport)
  expect(rejected.deferred.map(({ object_id }) => object_id)).toContain('chapter-directory:swapped-directory')
  expect((await exportLocalManuscript(key)).chapters).toEqual(original.chapters)
  expect(cloud.submitted).toHaveLength(submitted)
  failSecondWrite.mockRestore()

  const failBaselineWrite = vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (
    this: IDBObjectStore,
    value,
    id,
  ) {
    if (this.name === 'heads' && value?.id === 'chapter-directory:swapped-directory' && value?.baseline)
      throw new DOMException('synthetic ledger exhaustion', 'QuotaExceededError')
    return put.call(this, value, id)
  })
  const unacknowledged = await syncWebManuscript(project, scope, cloud.transport)
  expect(unacknowledged.deferred.map(({ object_id }) => object_id)).toContain(
    'chapter-directory:swapped-directory',
  )
  expect((await exportLocalManuscript(key)).chapters.map(({ id }) => id)).toEqual([
    second.id,
    first.id,
    final.id,
  ])
  const waiting = await listSyncHeads(scope)
  expect(waiting.find((entry) => entry.id === `chapter:${first.id}`)?.directoryParts).toHaveLength(1)
  expect(waiting.find((entry) => entry.id === `chapter:${second.id}`)?.directoryParts).toHaveLength(1)
  expect(await readLocalHistory(key, first.id)).toEqual([initial, prior])
  expect(cloud.submitted).toHaveLength(submitted)
  failBaselineWrite.mockRestore()

  const complete = await syncWebManuscript(project, scope, cloud.transport)
  expect(complete.deferred).toEqual([])
  expect(complete.receivedUnapplied).toBe(0)
  expect((await exportLocalManuscript(key)).chapters.map(({ id }) => id)).toEqual([
    second.id,
    first.id,
    final.id,
  ])
  expect(await readLocalHistory(key, first.id)).toEqual([initial, prior, head])
  expect(await readLocalHistory(key, second.id)).toEqual(
    original.revisions.filter(({ chapter_id }) => chapter_id === second.id),
  )
  await syncWebManuscript(project, scope, cloud.transport)
  expect(cloud.submitted).toHaveLength(submitted)

  globalThis.indexedDB = new IDBFactory()
  const { format, novel, branch } = original
  const fresh = await importLocalManuscript(
    new Blob([JSON.stringify({ format, novel, branch, chapters: [], revisions: [] })]),
  )
  await setSyncEnabled({ ...scope, deviceId: 'fresh' }, true)
  const cold = await syncWebManuscript(fresh, { ...scope, deviceId: 'fresh' }, cloud.transport)
  expect(cold.deferred).toEqual([])
  expect((await exportLocalManuscript(key)).chapters.map(({ id }) => id)).toEqual([
    second.id,
    first.id,
    final.id,
  ])
  expect(await readLocalHistory(key, first.id)).toEqual([initial, prior, head])
})

test('an author save awaiting upload keeps its revision and follows the newly adopted directory position', async () => {
  const project = await createLocalProject('换序时另保存正文')
  const key = project.novel.id
  const first = await createLocalChapter(key, '初章')
  const second = await createLocalChapter(key, '次章')
  const [initial] = await readLocalHistory(key, first.id)
  const prior = await saveLocalChapter(key, first.id, initial.id, '旧正文。')
  const original = await exportLocalManuscript(key)
  const scope = { service: 'http://127.0.0.1:8888', workId: project.branch.id, deviceId: 'receiver' }
  const cloud = service(scope.deviceId, 'unused', false)
  await setSyncEnabled(scope, true)
  await syncWebManuscript(project, scope, cloud.transport)
  const savedMeanwhile = await saveLocalChapter(key, first.id, prior.id, '应保留的新正文。')
  await pushDirectoryFixture(cloud, scope.workId, [
    { id: first.id, position: 2 },
    { id: second.id, position: 1 },
  ])
  const directoryReceipt = cloud.latest.get(`chapter:${first.id}`)?.change_id
  const result = await syncWebManuscript(project, scope, cloud.transport)
  expect(result.deferred).toEqual([])
  expect(result.conflicts).toEqual([])
  const savedRemotely = cloud.latest.get(`chapter:${first.id}`)
  expect(savedRemotely?.parent_revision).toBe(directoryReceipt)
  expect(savedRemotely?.payload).toMatchObject({
    chapter: { position: 2 },
    head: { id: savedMeanwhile.id, content: savedMeanwhile.content },
  })
  expect(await readLocalHistory(key, first.id)).toEqual([initial, prior, savedMeanwhile])
  expect(await readLocalHistory(key, second.id)).toEqual(
    original.revisions.filter((row) => row.chapter_id === second.id),
  )
})

test('competing remote and local prose keep the new positions and become a normal chapter conflict', async () => {
  const project = await createLocalProject('换序夹着两端不同正文')
  const key = project.novel.id
  const first = await createLocalChapter(key, '初章')
  const second = await createLocalChapter(key, '次章')
  const [initial] = await readLocalHistory(key, first.id)
  const prior = await saveLocalChapter(key, first.id, initial.id, '共同原稿。')
  const scope = { service: 'http://127.0.0.1:8888', workId: project.branch.id, deviceId: 'receiver' }
  const cloud = service(scope.deviceId, 'unused', false)
  await setSyncEnabled(scope, true)
  await syncWebManuscript(project, scope, cloud.transport)
  const id = `chapter:${first.id}`
  const priorReceipt = cloud.latest.get(id)!
  const previous = (await listSyncHeads(scope)).find((entry) => entry.id === id)!.baseline!
  const local = await saveLocalChapter(key, first.id, prior.id, '本机继续写的稿。')
  const remoteHead = {
    ...prior,
    id: 'desktop-body-before-directory',
    revision: prior.revision + 1,
    parent_id: prior.id,
    content: '桌面继续写的稿。',
    sha256: await manuscriptHash('桌面继续写的稿。'),
  }
  await cloud.commit(
    {
      object_id: `revision:${remoteHead.id}`,
      change_id: 'desktop-body-source',
      parent_revision: null,
      payload: {
        format: 'ainovel-manuscript-sync/1',
        kind: 'revision',
        branch_id: scope.workId,
        revision: remoteHead,
      },
      tombstone: false,
    },
    'desktop',
  )
  const payload = JSON.parse(JSON.stringify(priorReceipt.payload))
  payload.head = remoteHead
  payload.chapter.revision = remoteHead.revision
  await cloud.commit(
    {
      object_id: id,
      change_id: 'desktop-revised-chapter',
      parent_revision: priorReceipt.change_id,
      payload,
      tombstone: false,
    },
    'desktop',
  )
  await pushDirectoryFixture(cloud, scope.workId, [
    { id: first.id, position: 2 },
    { id: second.id, position: 1 },
  ])
  const result = await syncWebManuscript(project, scope, cloud.transport)
  expect(result.deferred).toEqual([])
  expect(result.conflicts).toHaveLength(1)
  const compared = await readSyncConflict(scope, result.conflicts[0])
  expect(compared?.incoming.payload).toMatchObject({
    chapter: { position: 2 },
    head: { id: local.id, content: local.content },
  })
  expect(compared?.current?.payload).toMatchObject({
    chapter: { position: 2 },
    head: { id: remoteHead.id, content: remoteHead.content },
  })
  expect(compared?.base?.payload?.head).toMatchObject({ id: prior.id, content: prior.content })
  expect((await exportLocalManuscript(key)).chapters.map(({ id }) => id)).toEqual([second.id, first.id])
  expect(await readLocalHistory(key, first.id)).toEqual([initial, prior, local])
  const baseline = (await listSyncHeads(scope)).find((entry) => entry.id === id)!.baseline!
  expect(baseline.revision).toBe(previous.revision)
  expect(baseline.sequence).toBe(previous.sequence)
  expect(JSON.parse(baseline.signature)).toEqual([
    'chapter',
    first.id,
    scope.workId,
    2,
    first.title,
    prior.sha256,
  ])
})

test('directory child with rewritten chapter metadata remains blocked even with another child present', async () => {
  const project = await createLocalProject('拒绝目录改稿')
  const key = project.novel.id
  const first = await createLocalChapter(key, '初章')
  const second = await createLocalChapter(key, '次章')
  const original = await exportLocalManuscript(key)
  const scope = { service: 'http://127.0.0.1:8888', workId: project.branch.id, deviceId: 'receiver' }
  const cloud = service('desktop', 'unused', false)
  await acknowledgeLocalPublications(await readLocalPublications(scope.workId))
  for (const [object_id, payload] of syncManuscriptObjects(original)) {
    await cloud.transport.push({
      object_id,
      change_id: object_id,
      parent_revision: null,
      payload,
      tombstone: false,
    })
  }
  await setSyncEnabled(scope, true)
  await syncWebManuscript(project, scope, cloud.transport)
  await pushDirectoryFixture(
    cloud,
    scope.workId,
    [
      { id: first.id, position: 2 },
      { id: second.id, position: 1 },
    ],
    'invalid-directory',
    true,
  )
  const sent = cloud.submitted.length
  const result = await syncWebManuscript(project, scope, cloud.transport)
  expect(result.deferred).toContainEqual({
    object_id: 'chapter-directory:invalid-directory',
    reason: '目录批次的子回执必须保留原章稿和章节信息。',
  })
  expect((await exportLocalManuscript(key)).chapters).toEqual(original.chapters)
  expect(cloud.submitted).toHaveLength(sent)
})

test.each([false, true])(
  'a lost chapter-order receipt is replayed and does not send or replace a newer local save=%s',
  async (newer) => {
    const project = await createLocalProject('反转目录')
    const key = project.novel.id
    const chapter = await createLocalChapter(key, '铜铃')
    const [initial] = await readLocalHistory(key, chapter.id)
    const original = await saveLocalChapter(key, chapter.id, initial.id, '只调整章位的原稿')
    const objectId = `chapter:${chapter.id}`
    const scope = { service: 'http://127.0.0.1:8888', workId: project.branch.id, deviceId: 'web-order' }
    const cloud = service(scope.deviceId, 'unused', false)
    const ledger = 'c'.repeat(32)
    const attempts: string[] = []
    let applied: SyncReceipt | undefined
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
      const { pathname, searchParams } = new URL(String(url))
      const path = pathname.split(`/works/${scope.workId}`)[1]
      if (path === '/checkpoint') return Response.json({ ledger_id: ledger, cursor: cloud.log.length })
      expect(new Headers(init?.headers).get('X-Ainovel-Ledger')).toBe(ledger)
      if (path === '/pull')
        return Response.json(await cloud.transport.pull(Number(searchParams.get('after'))))
      if (path === '/push') return Response.json(await cloud.transport.push(JSON.parse(String(init?.body))))
      if (path.startsWith('/receipts/')) {
        return applied && path === `/receipts/${applied.change_id}`
          ? Response.json(applied)
          : new Response(null, { status: 404 })
      }
      expect(path).toBe('/chapter-order-conflicts/resolve')
      expect(searchParams.get('device_id')).toBe(scope.deviceId)
      const request = JSON.parse(String(init?.body))
      attempts.push(request.change_id)
      expect(request.chapter_id).toBe(chapter.id)
      expect(request.position).toBe(chapter.position)
      if (applied) return Response.json(applied)
      const old = cloud.latest.get(objectId)
      expect(request.heads[chapter.id]).toBe(old?.change_id)
      const payload = JSON.parse(JSON.stringify(old?.payload))
      payload.chapter.position = chapter.position + 2
      applied = (await cloud.transport.push({
        change_id: request.change_id,
        object_id: objectId,
        parent_revision: old!.change_id,
        payload,
        tombstone: false,
      })) as SyncReceipt
      throw new Error('章位已写入但响应中断')
    })
    const transport = httpSyncTransport(scope, 'a'.repeat(32))
    await transport.enable()
    await syncWebManuscript(project, scope, transport)
    const selection = {
      chapter_id: chapter.id,
      position: chapter.position,
      heads: { unrelated: 'other-chapter-head', [chapter.id]: cloud.latest.get(objectId)!.change_id },
    }
    const before = cloud.submitted.length
    await expect(resolveWebChapterOrder(project, scope, transport, selection, ledger)).rejects.toThrow(
      '章位已写入但响应中断',
    )
    const saved = newer
      ? await saveLocalChapter(key, chapter.id, original.id, '断线后继续保存的正文。')
      : original
    const resolved = await resolveWebChapterOrder(project, scope, transport, selection, ledger)
    expect(attempts).toHaveLength(2)
    expect(attempts[0]).toBe(attempts[1])
    expect(cloud.submitted).toHaveLength(before + 1)
    const pack = await exportLocalManuscript(key)
    expect(pack.chapters.find(({ id }) => id === chapter.id)?.position).toBe(
      newer ? chapter.position : chapter.position + 2,
    )
    const history = await readLocalHistory(key, chapter.id)
    expect(history).toHaveLength(newer ? 3 : 2)
    expect(history.at(-1)).toMatchObject({
      id: saved.id,
      content: saved.content,
    })
    expect(resolved.receivedUnapplied).toBe(newer ? 1 : 0)
    expect(resolved.localPending > 0).toBe(newer)
    if (newer) {
      const continued = await syncWebManuscript(project, scope, transport)
      expect(continued.conflicts).toHaveLength(1)
      expect(cloud.latest.get(objectId)?.payload).toMatchObject({
        chapter: { position: chapter.position + 2 },
        head: { id: original.id },
      })
      expect((await readLocalHistory(key, chapter.id)).at(-1)).toMatchObject({
        id: saved.id,
        content: saved.content,
      })
    }
  },
)

test('an adopted local outline protects its chapter before that outline reaches the cloud', async () => {
  const project = await createLocalProject('未同步的正式大纲')
  const chapter = await createLocalChapter(project.novel.id, '仍在大纲里的章节')
  const scope = { service: 'http://127.0.0.1:8888', workId: project.branch.id, deviceId: 'web-order' }
  const ledger = 'c'.repeat(32)
  let missing = false
  let submitted = false
  const cloudChapters = [
    { chapter_id: chapter.id, head_revision: 'local-head', title: chapter.title, scheduled: false },
    { chapter_id: 'remote', head_revision: 'remote-head', title: '其他章', scheduled: false },
  ]
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
    const { pathname, searchParams } = new URL(String(url))
    if (pathname.endsWith('/checkpoint')) return Response.json({ ledger_id: ledger, cursor: 0 })
    expect(new Headers(init?.headers).get('X-Ainovel-Ledger')).toBe(ledger)
    if (pathname.endsWith('/chapter-order-conflicts')) {
      expect(searchParams.get('device_id')).toBe(scope.deviceId)
      return missing
        ? Response.json({ code: 'sync_work_missing', message: '云端工作版本不存在。' }, { status: 404 })
        : Response.json({ conflicts: [{ position: chapter.position, chapters: cloudChapters }] })
    }
    if (pathname.includes('/receipts/')) return new Response(null, { status: 404 })
    submitted = true
    throw new Error('未获准提交')
  })
  const transport = httpSyncTransport(scope, 'a'.repeat(32))
  await transport.enable()
  expect((await readWebChapterOrderConflicts(project, transport))[0].chapters).toEqual(cloudChapters)
  await confirmLocalOutline(project.novel.id, null, {
    id: 'web-outline',
    parent_id: null,
    project_version_id: null,
    settings_version_id: null,
    plan: {
      summary: '见到铜铃',
      promises: [],
      volumes: [
        {
          id: 'first-volume',
          title: '第一卷',
          summary: '旧城线索',
          units: [
            {
              id: 'first-unit',
              title: '寻找铜铃',
              summary: '比对铸记',
              chapters: [
                {
                  chapter_id: chapter.id,
                  goal: '拿到铜铃',
                  obstacle: '桥已截断',
                  turn: '发现摆渡人',
                  result: '找到新线索',
                  setting_ids: [],
                },
              ],
            },
          ],
        },
      ],
    },
    reference_cards: [],
    promise_sources: {},
  })
  expect((await readWebChapterOrderConflicts(project, transport))[0].chapters).toEqual([
    { ...cloudChapters[0], scheduled: true },
    cloudChapters[1],
  ])
  await expect(
    resolveWebChapterOrder(
      project,
      scope,
      transport,
      {
        chapter_id: chapter.id,
        position: chapter.position,
        heads: { [chapter.id]: cloudChapters[0].head_revision, remote: cloudChapters[1].head_revision },
      },
      ledger,
    ),
  ).rejects.toThrow('该章已编入本机正式大纲')
  expect(submitted).toBe(false)
  missing = true
  await expect(readWebChapterOrderConflicts(project, transport)).rejects.toThrow('云端工作版本不存在')
})

test('formal fact and length heads publish their checked versions first and chapter edit publishes an empty set once', async () => {
  const original = await createLocalProject('正式依据同步')
  const chapter = await createLocalChapter(original.novel.id, '渡口')
  const [root] = await readLocalHistory(original.novel.id, chapter.id)
  const saved = await saveLocalChapter(original.novel.id, chapter.id, root.id, '门内响铃𠮷😀。\r\n铜铃落地。')
  const pack = await exportLocalManuscript(original.novel.id)
  const evidence = await captureStoryEvidence(saved, 4, 9)
  const facts = [
    { subject: '旧铃', kind: 'event', statement: '已经落地。', truth: 'fact', story_time: '', evidence },
  ]
  globalThis.indexedDB = new IDBFactory()
  const project = await importLocalManuscript(
    new Blob([
      JSON.stringify({
        ...pack,
        restore_state: { facts, length_targets: [{ scope: 'book', scope_id: null, targets: null }] },
      }),
    ]),
  )
  const scope = { service: 'http://127.0.0.1:8888', workId: project.branch.id, deviceId: 'author-web' }
  const cloud = service(scope.deviceId, 'unused', false)
  await setSyncEnabled(scope, true)
  await syncWebManuscript(project, scope, cloud.transport)
  const factKey = `fact-set-head:${await manuscriptHash(compactJSON([scope.workId, chapter.id]))}`
  const lengthKey = `length-target-head:${await manuscriptHash(compactJSON([scope.workId, 'book', null]))}`
  const first = cloud.latest.get(factKey)!
  const length = cloud.latest.get(lengthKey)!
  const factVersion = cloud.latest.get(`fact-set-version:${first.payload?.revision_id}`)!
  const limitVersion = cloud.latest.get(`length-target-version:${length.payload?.revision_id}`)!
  expect(factVersion.payload?.version).toMatchObject({
    facts,
    chapter_revision_id: saved.id,
    parent_id: null,
  })
  expect(limitVersion.payload?.version).toMatchObject({
    scope: 'book',
    scope_id: null,
    outline_version_id: null,
    targets: null,
  })
  expect(factVersion.sequence).toBeLessThan(first.sequence)
  expect(cloud.latest.get(`revision:${saved.id}`)!.sequence).toBeLessThan(factVersion.sequence)
  expect(limitVersion.sequence).toBeLessThan(length.sequence)
  expect((await readLocalRulePreview(project.novel.id)).facts).toHaveLength(1)

  const put = IDBObjectStore.prototype.put
  const failure = vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (
    this: IDBObjectStore,
    value,
    key,
  ) {
    if (this.name === 'restoreSyncHeads') throw new DOMException('Storage full', 'QuotaExceededError')
    return put.call(this, value, key)
  })
  try {
    await expect(
      saveLocalChapter(project.novel.id, chapter.id, saved.id, '后来改稿。'),
    ).rejects.toMatchObject({
      code: 'quota',
    })
  } finally {
    failure.mockRestore()
  }
  expect((await readLocalHistory(project.novel.id, chapter.id)).at(-1)).toEqual(saved)
  expect((await readLocalRulePreview(project.novel.id)).facts).toHaveLength(1)
  expect(await readLocalPublications(project.branch.id)).toEqual([])

  const amended = await saveLocalChapter(project.novel.id, chapter.id, saved.id, '后来改稿。')
  await syncWebManuscript(project, scope, cloud.transport)
  const empty = cloud.latest.get(factKey)!
  const emptyVersion = cloud.latest.get(`fact-set-version:${empty.payload?.revision_id}`)!
  expect(empty.parent_revision).toBe(first.change_id)
  expect(emptyVersion.payload?.version).toMatchObject({
    facts: [],
    chapter_revision_id: amended.id,
    parent_id: first.payload?.revision_id,
  })
  expect((await readLocalRulePreview(project.novel.id)).facts).toEqual([])
  const cursor = cloud.log.length
  await syncWebManuscript(project, scope, cloud.transport)
  expect(cloud.log).toHaveLength(cursor)

  const interrupted = await saveLocalChapter(
    project.novel.id,
    chapter.id,
    amended.id,
    '服务器先拒绝这份稿的清除。',
  )
  let rejected: string | undefined
  const awaitingChapter: SyncTransport = {
    ...cloud.transport,
    async push(request) {
      if (request.object_id === factKey) {
        rejected ??= request.change_id
        if (request.change_id === rejected)
          throw new SyncRestoreStateRejected('sync_restore_state_dependency', '等待该章稿正式采用。')
      }
      return cloud.transport.push(request)
    },
  }
  const pending = await syncWebManuscript(project, scope, awaitingChapter)
  expect(pending.deferred).toContainEqual({ object_id: factKey, reason: '等待该章稿正式采用。' })
  expect(cloud.latest.get(factKey)).toEqual(empty)
  const freshest = await saveLocalChapter(project.novel.id, chapter.id, interrupted.id, '后来正式保存的稿。')
  await syncWebManuscript(project, scope, awaitingChapter)
  const completed = cloud.latest.get(factKey)!
  expect(completed.change_id).not.toBe(rejected)
  expect(completed.parent_revision).toBe(empty.change_id)
  expect(
    cloud.latest.get(`fact-set-version:${completed.payload?.revision_id}`)!.payload?.version,
  ).toMatchObject({
    chapter_revision_id: freshest.id,
    facts: [],
  })
})

test.each([false, true])(
  'formal facts wait for exact same-text chapter adoption, explicit=%s',
  async (explicit) => {
    const project = await createLocalProject('云端正确原稿')
    const key = project.novel.id
    const branch = project.branch.id
    const chapter = await createLocalChapter(key, '老城')
    const [root] = await readLocalHistory(key, chapter.id)
    const text = '窗外𠮷😀，铃响。'
    const revision = {
      ...root,
      revision: 1,
      id: 'received-revision',
      parent_id: root.id,
      content: text,
      sha256: await manuscriptHash(text),
    }
    const source = {
      format: 'ainovel-manuscript-sync/1',
      kind: 'revision',
      branch_id: branch,
      revision,
    } as const
    const version = await buildRestoreStateVersion(
      branch,
      {
        kind: 'fact-set-version',
        draft: {
          chapter_id: chapter.id,
          chapter_revision_id: revision.id,
          parent_id: null,
          facts: [
            {
              subject: '旧铃',
              kind: 'event',
              statement: '听见旧铃。',
              truth: 'fact',
              story_time: '',
              evidence: await captureStoryEvidence(revision, 2, 6),
            },
          ],
        },
      },
      manuscriptHash,
    )
    const head = restoreStateHead(version)
    const versionKey = await restoreStateObjectId(version, manuscriptHash)
    const headKey = await restoreStateObjectId(head, manuscriptHash)
    expect(await validateSyncedObject(project, versionKey, version)).toEqual(version)
    await expect(validateSyncedObject(project, headKey + 'x', head)).rejects.toThrow('摘要')
    await expect(applySyncedObject(key, versionKey, version, 'null')).rejects.toMatchObject({
      code: 'missing',
    })
    expect(await applySyncedObject(key, `revision:${revision.id}`, source, 'null')).toBe(true)
    expect(await applySyncedObject(key, versionKey, version, 'null')).toBe(true)
    await expect(applySyncedObject(key, headKey, head, 'null')).rejects.toMatchObject({ code: 'missing' })
    expect((await readLocalRulePreview(key)).facts).toEqual([])
    const sameText = await saveLocalChapter(key, chapter.id, root.id, text)
    expect(sameText.id).not.toBe(revision.id)
    await expect(applySyncedObject(key, headKey, head, 'null')).rejects.toMatchObject({ code: 'missing' })
    const exact = {
      format: 'ainovel-manuscript-sync/1',
      kind: 'chapter',
      chapter: { ...chapter, revision: 1 },
      head: revision,
    } as const
    if (explicit) {
      const previous = syncManuscriptObjects(await exportLocalManuscript(key)).get(`chapter:${chapter.id}`)!
      expect(
        await applySyncedObject(key, `chapter:${chapter.id}`, exact, syncObjectSignature(previous)),
      ).toBe(true)
      expect(await applySyncedObject(key, headKey, head, 'stale')).toBe(false)
      expect((await readLocalRulePreview(key)).facts).toEqual([])
      expect(await applySyncedObject(key, headKey, head, 'null')).toBe(true)
    }
    const cloud = service('receiver', 'unused', false)
    const scope = { service: 'http://127.0.0.1:8888', workId: branch, deviceId: 'receiver' }
    await setSyncEnabled(scope, true)
    await acknowledgeLocalPublications(await readLocalPublications(branch))
    for (const [objectId, payload] of [
      [`revision:${revision.id}`, source],
      [`chapter:${chapter.id}`, exact],
      [versionKey, version],
      [headKey, head],
    ] as const) {
      await cloud.transport.push({
        object_id: objectId,
        change_id: objectId,
        parent_revision: null,
        payload,
        tombstone: false,
      })
    }
    await syncWebManuscript(project, scope, cloud.transport)
    expect((await readLocalRulePreview(key)).facts[0]).toMatchObject({
      quote: '𠮷😀',
      evidence: { revision_id: sameText.id, start: 2, end: 6 },
    })
    expect(cloud.submitted.filter(({ object_id }) => object_id === headKey)).toHaveLength(1)
  },
)

test.each([false, true])(
  'chapter keeps a later save across an interrupted send, server accepted=%s',
  async (accepted) => {
    const project = await createLocalProject('待同步章稿')
    const scope = { service: 'http://127.0.0.1:8888', workId: project.branch.id, deviceId: 'web' }
    const key = project.novel.id
    const chapter = await createLocalChapter(key, '铜铃')
    const id = `chapter:${chapter.id}`
    const [root] = await readLocalHistory(key, chapter.id)
    const original = await saveLocalChapter(key, chapter.id, root.id, '较早稿')
    const cloud = service(scope.deviceId, id, accepted)
    await setSyncEnabled(scope, true)
    await expect(syncWebManuscript(project, scope, cloud.transport)).rejects.toThrow()
    const prepared = (await readSyncState(scope)).pending.find(
      (item) => item.kind === 'push' && item.request.object_id === id,
    )
    expect(prepared?.request.change_id).toBe(
      cloud.submitted.find((request) => request.object_id === id)?.change_id,
    )
    const previous = (await readLocalPublications(scope.workId)).find(({ objectId }) => objectId === id)
    const next = await saveLocalChapter(key, chapter.id, original.id, '中断后继续写的下一稿')
    const fresh = (await readLocalPublications(scope.workId)).find(({ objectId }) => objectId === id)
    expect(fresh?.id).not.toBe(previous?.id)

    const firstRetry = await syncWebManuscript(project, scope, cloud.transport)
    if (!accepted) expect(firstRetry.localPending).toBeGreaterThan(0)
    await syncWebManuscript(project, scope, cloud.transport)
    expect(cloud.submitted.filter(({ change_id }) => change_id === prepared?.request.change_id)).toHaveLength(
      accepted ? 1 : 2,
    )
    const heads = cloud.log.filter(({ object_id }) => object_id === id)
    expect(heads.map(({ status }) => status)).toEqual(['applied', 'applied'])
    expect(heads[1].parent_revision).toBe(heads[0].change_id)
    expect(cloud.latest.get(id)?.payload?.head).toMatchObject({ id: next.id, content: next.content })
    expect(await readLocalPublications(scope.workId)).toEqual([])

    await saveLocalChapter(key, chapter.id, next.id, '短暂的第三稿')
    const preview = await readLocalChapterRestore(key, chapter.id, next.id)
    const restore = await restoreLocalChapter(key, chapter.id, next.id, preview.basis)
    expect(restore.sha256).toBe(next.sha256)
    await syncWebManuscript(project, scope, cloud.transport)
    expect(cloud.latest.get(id)?.payload?.head).toMatchObject({ id: restore.id })
  },
)

test('an old Web replica that accepted an empty archive head retries until the exact chapter history is available', async () => {
  const source = await createLocalProject('同步已归档章节')
  const chapter = await createLocalChapter(source.novel.id, '曾经的开篇')
  const [root] = await readLocalHistory(source.novel.id, chapter.id)
  const final = await saveLocalChapter(source.novel.id, chapter.id, root.id, '后来归档的正文。')
  const pack = await exportLocalManuscript(source.novel.id)
  const objects = syncManuscriptObjects(pack)
  const id = `chapter:${chapter.id}`
  const scope = { service: 'http://127.0.0.1:8888', workId: source.branch.id, deviceId: 'new-web' }
  const cloud = service('desktop', 'unused', false)
  const write = (object_id: string, change_id: string, parent_revision: string | null = null) => {
    const payload = objects.get(object_id) ?? null
    return cloud.transport.push({
      object_id,
      change_id,
      parent_revision,
      payload: JSON.parse(JSON.stringify(payload)),
      tombstone: payload === null,
    })
  }
  await write(`branch:${scope.workId}`, 'directory')
  await write(`revision:${root.id}`, 'initial-contents')
  await write(id, 'visible-chapter')
  objects.delete(id)
  await write(id, 'archive', 'visible-chapter')
  await write(id, 'confirm-archive', 'archive')

  globalThis.indexedDB = new IDBFactory()
  const project = await importLocalManuscript(
    new Blob([JSON.stringify({ ...pack, chapters: [], revisions: [] })]),
  )
  await setSyncEnabled(scope, true)
  await pullSyncPage(scope, cloud.transport)
  await markSyncBaseline(scope, id, { revision: 'confirm-archive', signature: 'null' })
  const missing = await syncWebManuscript(project, scope, cloud.transport)
  expect(missing.deferred).toEqual([{ object_id: id, reason: expect.stringContaining('连续正文修订') }])
  expect((await exportLocalManuscript(project.novel.id)).chapters).toEqual([])

  await write(`revision:${final.id}`, 'last-contents')
  expect((await syncWebManuscript(project, scope, cloud.transport)).deferred).toEqual([])
  const restored = await exportLocalManuscript(project.novel.id)
  expect(restored.chapters).toEqual([{ ...pack.chapters[0], archived: 1 }])
  expect(restored.revisions).toEqual([root, final])
  expect(restored.source_revisions).toBeUndefined()
  const cursor = cloud.log.length
  await syncWebManuscript(project, scope, cloud.transport)
  expect(await exportLocalManuscript(project.novel.id)).toEqual(restored)
  expect(cloud.log).toHaveLength(cursor)
})

test('M1 workspace retains its later adoption when the prior head push must be retried', async () => {
  const project = await createLocalProject('待同步立项')
  const scope = { service: 'http://127.0.0.1:8888', workId: project.branch.id, deviceId: 'web' }
  const id = `project:${scope.workId}`
  const brief = {
    position: '追查旧铃案',
    track: '线索式悬疑',
    genre_ids: ['xianxia'],
    audience: '偏好悬疑的读者',
    distinction: '只用首章证物',
    length: '三卷十万字',
    cadence: '',
    ending: '旧铃案告破',
    final_image: '',
  }
  const first = await confirmLocalProjectBrief(project.novel.id, null, { idea: '首版立项', brief })
  const cloud = service(scope.deviceId, id, false)
  await setSyncEnabled(scope, true)
  await expect(syncWebManuscript(project, scope, cloud.transport)).rejects.toThrow()
  const next = await confirmLocalProjectBrief(project.novel.id, first.id, { idea: '修订后的立项', brief })
  const firstRetry = await syncWebManuscript(project, scope, cloud.transport)
  expect(firstRetry.localPending).toBe(1)
  await syncWebManuscript(project, scope, cloud.transport)
  const adopted = cloud.log.filter(({ object_id }) => object_id === id)
  expect(adopted.map(({ status, payload }) => [status, payload?.revision_id])).toEqual([
    ['applied', first.id],
    ['applied', next.id],
  ])
  expect(adopted[1].parent_revision).toBe(adopted[0].change_id)
  expect(cloud.latest.get(`project-version:${next.id}`)?.payload?.version).toMatchObject({
    parent_id: first.id,
  })
  expect(await readLocalPublications(scope.workId)).toEqual([])
})

const externalQuote = '只留本机的外部原句。'
const format = 'ainovel-manuscript-sync/1'

async function privacyPackage(
  mode: 'card_references' | 'planned_card_references' | 'projected' | 'addresses',
) {
  const work = await createLocalProject('正文与历史体检')
  const chapter = await createLocalChapter(work.novel.id, '保存的一章')
  const [root] = await readLocalHistory(work.novel.id, chapter.id)
  const revision = await saveLocalChapter(work.novel.id, chapter.id, root.id, '作者自己的原稿。')
  const pack = await exportLocalManuscript(work.novel.id)
  const chapter_basis = {
    id: chapter.id,
    title: chapter.title,
    position: chapter.position,
    revision: revision.revision,
    sha256: revision.sha256,
  }
  const chapter_bindings = [
    { id: chapter.id, revision: revision.revision, revision_id: revision.id, sha256: revision.sha256 },
  ]
  const source = {
    start: 4,
    end: 4 + externalQuote.length,
    quote_sha256: await manuscriptHash(externalQuote),
  }
  const risk = [{ source: { ...source, quote: externalQuote } }]
  const reportId = 'original-inspection'
  const checks = await Promise.all(
    Array.from({ length: 10 }, async (_, index) => ({
      check_id: String(index + 1).padStart(2, '0'),
      status: 'green',
      title: '已核对',
      summary: '原历史记录',
      basis: index === 4 ? { rules: [] } : index === 7 ? { card_references: [{ source }] } : {},
      basis_sha256: await manuscriptHash(`basis-${index}`),
      can_mark_not_applicable: false,
    })),
  )
  const report = {
    version: 'm6-inspection/1',
    scope: 'book',
    scope_id: null,
    chapters: [{ ...chapter_basis, chars: revision.content.length, stale: false }],
    items: checks,
    basis_sha256: await manuscriptHash(JSON.stringify(['book', null, checks.map((row) => row.basis_sha256)])),
  }
  if (mode === 'card_references' || mode === 'planned_card_references')
    report.items[7].basis = { [mode]: risk }
  const inspection = { id: reportId, created: 1, chapter_bindings, report }
  const ownBody = `## ${chapter.title}\n\n${revision.content}\n`
  const title = work.novel.title
  const materials = {
    '正文.md': `# ${title}\n\n${ownBody}`,
    '设定册.md': `# ${title} · 设定册\n\n本作没有单独确认的世界设定条目。\n`,
    '简介.md': `# ${title} · 简介\n\n剧透范围：仅开篇\n\n试读简介。\n`,
    '试读.md': `# ${title} · 试读\n\n${ownBody}`,
  }
  const files = Object.fromEntries(
    await Promise.all(
      Object.entries(materials).map(async ([name, content]) => [name, await manuscriptHash(content)]),
    ),
  )
  const basis_sha256 = await manuscriptHash('完本依据')
  const closure = { note: '作者已经核对。', sources: [] }
  const completion = {
    id: 'original-delivery',
    created: 2,
    chapter_bindings,
    basis_sha256,
    manifest: {
      format: 'book-completion/1',
      title,
      branch_id: work.branch.id,
      story_revision: 1,
      chapters: [chapter_basis],
      inspection_id: reportId,
      promises: [],
      files,
      spoiler_scope: 'opening',
      sample_chapter_ids: [chapter.id],
    },
    review: {
      basis_sha256,
      read_note: '通读完成。',
      ending: closure,
      characters: closure,
      promises: closure,
      intro: '试读简介。',
      spoiler_scope: 'opening',
      sample_chapter_ids: [chapter.id],
      materials_reviewed: true,
    },
    checks: ['manuscript', 'ending', 'characters', 'promises', 'inspection', 'delivery'].map((id) => ({
      id,
      title: '完成核对',
      status: 'green',
      detail: '',
    })),
    materials,
  }
  const projected = JSON.stringify({
    scope: 'book',
    items: checks.map((item) =>
      mode === 'projected' && item.check_id === '08'
        ? { ...item, basis: { planned_card_references: risk } }
        : item,
    ),
  })
  const document = {
    id: reportId,
    kind: 'inspection',
    title: '只读体检',
    body: projected,
    sha256: await manuscriptHash(projected),
    chapters: [],
  }
  globalThis.indexedDB = new IDBFactory()
  const project = await importLocalManuscript(
    new Blob([
      JSON.stringify({
        ...pack,
        evidence: [document],
        workflow_history: { inspections: [inspection], completions: [completion] },
      }),
    ]),
  )
  return { project, inspection, completion, document, chapter, revision }
}

test.each([
  ['card_references', false],
  ['planned_card_references', true],
  ['projected', true],
] as const)('withholds %s excerpts from Web export and sync (persisted queue=%s)', async (mode, queued) => {
  const { project, inspection, completion, document, chapter, revision } = await privacyPackage(mode)
  const scope = { service: 'http://127.0.0.1:8888', workId: project.branch.id, deviceId: 'web' }
  const cloud = service(scope.deviceId, 'not-submitted-in-this-test', false)
  const original = await readLocalWorkflowHistory(project.novel.id)
  expect(original.inspections[0].report.basis_sha256).toBe(inspection.report.basis_sha256)
  expect(JSON.stringify({ original, document })).toContain(externalQuote)
  await expect(exportLocalManuscript(project.novel.id)).rejects.toThrow(inspectionExcerptTransferReason)
  await setSyncEnabled(scope, true)
  const inspectionId = `workflow-inspection:${inspection.id}`
  const completionId = `workflow-completion:${completion.id}`
  const documentId = `evidence:${document.id}`
  if (queued) {
    for (const [id, entry, kind] of [
      [completionId, completion, 'workflow-completion'],
      [inspectionId, inspection, 'workflow-inspection'],
    ] as const) {
      await queueSyncChange(scope, {
        object_id: id,
        parent_revision: null,
        tombstone: false,
        payload: { format, kind, branch_id: project.branch.id, entry_json: JSON.stringify(entry) },
      })
    }
    await queueSyncResolution(scope, documentId, 'conflict', {
      choice: 'merge',
      parent_revision: 'parent',
      tombstone: false,
      payload: { format, kind: 'evidence', document },
    })
  }
  const state = await syncWebManuscript(project, scope, cloud.transport)
  const withheld = new Map(state.deferred.map(({ object_id, reason }) => [object_id, reason]))
  expect(withheld.get(mode === 'projected' ? documentId : inspectionId)).toBe(inspectionExcerptTransferReason)
  expect(withheld.get(completionId)).toContain('引用了含外部资料原句的旧体检')
  expect(cloud.submitted.map(({ object_id }) => object_id)).not.toContain(completionId)
  expect(cloud.latest.has(inspectionId)).toBe(mode === 'projected')
  expect(JSON.stringify(cloud.submitted)).not.toContain(externalQuote)
  expect(cloud.latest.get(`chapter:${chapter.id}`)?.payload?.head).toMatchObject({
    content: revision.content,
  })
  expect(await readLocalWorkflowHistory(project.novel.id)).toEqual(original)
  expect(project.evidence?.[0].sha256).toBe(document.sha256)
  const expectedPending = queued
    ? mode === 'projected'
      ? [completionId, documentId]
      : [completionId, inspectionId, documentId]
    : []
  expect(
    (await readSyncState(scope)).pending.map((entry) =>
      entry.kind === 'push' ? entry.request.object_id : entry.object_id,
    ),
  ).toEqual(expectedPending)
})

test('frozen addresses go through Web export and sync with the dependent delivery', async () => {
  const { project, inspection, completion } = await privacyPackage('addresses')
  const scope = { service: 'http://127.0.0.1:8888', workId: project.branch.id, deviceId: 'web' }
  await setSyncEnabled(scope, true)
  const cloud = service(scope.deviceId, 'not-submitted-in-this-test', false)
  expect((await exportLocalManuscript(project.novel.id)).workflow_history?.completions[0].id).toBe(
    completion.id,
  )
  expect((await syncWebManuscript(project, scope, cloud.transport)).deferred).toEqual([])
  expect(cloud.latest.has(`workflow-inspection:${inspection.id}`)).toBe(true)
  expect(cloud.latest.has(`workflow-completion:${completion.id}`)).toBe(true)
})
