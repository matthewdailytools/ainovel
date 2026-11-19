import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import {
  confirmLocalProjectBrief,
  createLocalChapter,
  createLocalProject,
  exportLocalManuscript,
  importLocalManuscript,
  manuscriptHash,
  readLocalChapterRestore,
  readLocalHistory,
  readLocalPublications,
  readLocalWorkflowHistory,
  restoreLocalChapter,
  saveLocalChapter,
  syncManuscriptObjects,
} from './offlineStore'
import { httpSyncTransport, syncWebManuscript } from './manuscriptSync'
import {
  markSyncBaseline,
  pullSyncPage,
  pushSyncPending,
  queueSyncChange,
  queueSyncResolution,
  readSyncState,
  setSyncEnabled,
  type PushChange,
  type SyncReceipt,
  type SyncTransport,
} from './syncClient'
import { inspectionExcerptTransferReason } from './workflowPrivacy'

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
  const transport: SyncTransport = {
    async push(request) {
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
        device_id: device,
        head_revision: conflict ? old!.change_id : request.change_id,
        conflict_id: conflict ? request.change_id : null,
      }
      log.push(receipt)
      if (!conflict) latest.set(receipt.object_id, receipt)
      if (lost) throw new Error('响应丢失')
      return receipt
    },
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
  return { transport, latest, submitted, log }
}

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
