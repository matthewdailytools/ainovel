import { createHash } from 'node:crypto'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { expect, test, vi } from 'vitest'
import type { Artifact, Chapter, Task } from './api'
import {
  CandidateRecheckAction,
  CandidateRecheckReport,
  CandidateHistory,
  acknowledgeRecheckNonce,
  candidateRecheckEligibility,
  createCandidateRecheckTargetLoader,
  loadCandidateRecheckTarget,
  parseRecheckBudget,
  readCandidateRecheck,
  readCandidateHistoryPage,
  readCandidateRecheckReceipt,
  recheckNonce,
  recheckRequestSignature,
} from './candidateRecheck'

const text = '沈砚只把旧尺还给老陈😀𠮷，未交给旁人。'
const sha = (value: string) => createHash('sha256').update(value).digest('hex')
const chapter: Chapter = {
  id: 'chapter-1',
  branch_id: 'branch-1',
  position: 1,
  title: '还尺',
  revision: 2,
  content: '尚未采纳的保存稿。',
  stale: 0,
}
function candidate(): Artifact {
  return {
    id: 'candidate-1',
    branch_id: 'branch-1',
    task_id: 'workflow-1',
    kind: 'chapter_draft',
    chapter_id: chapter.id,
    base_revision: chapter.revision,
    candidate_revision: 3,
    status: 'candidate',
    payload: { text, fixed: ['只还给老陈'] },
    basis: { story_revision: 5 },
  }
}
function source(): Task {
  return {
    id: 'workflow-1',
    created: 1,
    novel_id: 'novel-1',
    branch_id: 'branch-1',
    kind: 'workflow',
    status: 'needs_review',
    cancel: 0,
    error: '',
    payload: {
      account: 'source-account',
      mode: 'automatic',
      adoption: 'automatic',
      plot_contract_check: true,
    },
    result: {
      waiting_artifact: 'candidate-1',
      plot_contract_version: 'author-plot-audit/2',
      workflow_plan_preparation_version: 'workflow-plan-preparation/1',
    },
  }
}
const context = { branchId: 'branch-1', chapter, modelReady: true, unsaved: false, disabled: false }
function reportPayload() {
  return {
    version: 'candidate-recheck/1',
    target: {
      kind: 'candidate',
      artifact_id: 'candidate-1',
      revision: 3,
      chapter_id: chapter.id,
      base_revision: chapter.revision,
      content_sha256: sha(text),
    },
    source_workflow_id: 'workflow-1',
    review_passed: true,
    memory_generated: true,
    note: '只复核此候选，未改保存稿、未采纳候选、未恢复原流程。',
    review: { checks: [], findings: [], summary: '固定条件复核通过。' },
    plot_audit: {
      version: 'author-plot-audit/2',
      source: { kind: 'workflow_plan', text_sha256: sha('只把旧尺还给老陈。') },
      candidate_sha256: sha(text),
      candidate_revision: 3,
      status: 'model_pass',
      summary: '候选保留限定接收者。',
      policy: '模型辅助核对，作者决定采用。',
      interpretation_note: '复用原写前契约。',
      items: [
        {
          id: 'P01',
          author_evidence: '只把旧尺还给老陈。',
          actor: '沈砚',
          action: '归还',
          object: '旧尺',
          direction: '沈砚→老陈',
          result: '归还',
          certainty: 'explicit',
          recipient_scope: 'only',
          scope_condition: '本次归还',
        },
      ],
      checks: [
        {
          item_id: 'P01',
          result: 'pass',
          observed: '只还给老陈',
          evidence: text,
          reason: '方向与范围一致。',
          repair: '',
        },
      ],
      findings: [],
    },
  }
}
function parsed(value: unknown = reportPayload()) {
  const report = readCandidateRecheck(value)
  expect(report).toBeDefined()
  return report!
}
function reportArtifact(): Artifact {
  return {
    ...candidate(),
    id: 'report-1',
    task_id: 'recheck-task-1',
    kind: 'candidate_recheck',
    candidate_revision: 0,
    payload: reportPayload(),
    basis: { story_revision: 5 },
  }
}

test('只有原自动待审流程的当前未采纳候选可复检，保存后的新候选修订仍可申请', () => {
  expect(candidateRecheckEligibility(candidate(), source(), context)).toBeUndefined()
  expect(
    candidateRecheckEligibility({ ...candidate(), candidate_revision: 4 }, source(), context),
  ).toBeUndefined()
  expect(
    candidateRecheckEligibility(
      candidate(),
      { ...source(), payload: { ...source().payload, adoption: 'manual' } },
      context,
    ),
  ).toBeUndefined()
})

test.each([
  undefined,
  { ...source(), id: 'other-owner' },
  { ...source(), branch_id: 'other-branch' },
  { ...source(), kind: 'module' },
  { ...source(), status: 'running' },
  { ...source(), payload: { ...source().payload, mode: 'assisted' as const } },
  { ...source(), result: { ...source().result, continued_to: 'continued-workflow' } },
  { ...source(), result: { ...source().result, waiting_artifact: 'other-candidate' } },
  { ...source(), result: { ...source().result, plot_contract_version: 'author-plot-audit/1' } },
  { ...source(), result: { ...source().result, workflow_plan_preparation_version: undefined } },
])('来源身份、当前等待对象及冻结v2/prep1不符不能解锁复检（%#）', (owner) => {
  expect(candidateRecheckEligibility(candidate(), owner, context)).toBeTruthy()
})

test.each([
  { ...candidate(), kind: 'review' },
  { ...candidate(), status: 'accepted' },
  { ...candidate(), task_id: null },
  { ...candidate(), candidate_revision: -1 },
  { ...candidate(), chapter_id: 'other-chapter' },
  { ...candidate(), base_revision: 1 },
])('候选类型、保存状态、身份或保存稿基线不符不能复检（%#）', (artifact) => {
  expect(candidateRecheckEligibility(artifact, source(), context)).toBeTruthy()
})

test('本地未保存、模型不可用或忙碌时保留禁用原因', () => {
  for (const change of [{ unsaved: true }, { modelReady: false }, { disabled: true }, { chapter: undefined }])
    expect(candidateRecheckEligibility(candidate(), source(), { ...context, ...change })).toBeTruthy()
})

test('真实报告解析保留被检候选修订，报告自身rev0不替代目标rev3', () => {
  const report = parsed()
  expect(report.target).toEqual(reportPayload().target)
  expect(readCandidateRecheck(reportArtifact().payload)?.target.revision).toBe(3)
})

test.each([
  { ...reportPayload(), version: 'candidate-recheck/2' },
  { ...reportPayload(), target: null },
  { ...reportPayload(), target: { ...reportPayload().target, kind: 'saved_chapter' } },
  { ...reportPayload(), target: { ...reportPayload().target, artifact_id: '' } },
  { ...reportPayload(), target: { ...reportPayload().target, revision: '3' } },
  { ...reportPayload(), target: { ...reportPayload().target, revision: -1 } },
  { ...reportPayload(), target: { ...reportPayload().target, base_revision: 2.5 } },
  { ...reportPayload(), target: { ...reportPayload().target, content_sha256: 'not-a-hash' } },
  { ...reportPayload(), review_passed: 'true' },
])('异常版本、target或结果枚举不能冒充有效报告（%#）', (value) => {
  expect(readCandidateRecheck(value)).toBeUndefined()
})

test('实际GET被检候选并计算中文正文SHA，不使用报告artifact或报告SHA代替目标', async () => {
  const read = vi.fn().mockResolvedValue(candidate())
  const digest = vi.fn(async (value: string) => sha(value))
  const current = await loadCandidateRecheckTarget(parsed(), 'branch-1', read, digest)
  expect(read).toHaveBeenCalledWith('/artifacts/candidate-1')
  expect(digest).toHaveBeenCalledWith(text)
  expect(current).toMatchObject({ status: 'ready', sha256: sha(text), candidate: candidate() })
})

test.each([
  { ...candidate(), candidate_revision: 4 },
  { ...candidate(), payload: { text: '相同修订号下正文已被换掉。' } },
  { ...candidate(), base_revision: 3 },
])('候选修订、真实正文或保存稿基线变化后报告历史化（%#）', async (artifact) => {
  const state = await loadCandidateRecheckTarget(
    parsed(),
    'branch-1',
    async () => artifact,
    async (value) => sha(value),
  )
  expect(state.status).toBe('historical')
})

test.each([
  { ...candidate(), id: 'other-candidate' },
  { ...candidate(), branch_id: 'other-branch' },
  { ...candidate(), chapter_id: 'other-chapter' },
  { ...candidate(), kind: 'review' },
  { ...candidate(), task_id: 'other-workflow' },
])('不能把其它对象的正文作为当前复检目标（%#）', async (artifact) => {
  await expect(
    loadCandidateRecheckTarget(
      parsed(),
      'branch-1',
      async () => artifact,
      async (value) => sha(value),
    ),
  ).rejects.toThrow()
})

test('目标读取失败或hash失败不显示当前报告通过', async () => {
  await expect(
    loadCandidateRecheckTarget(parsed(), 'branch-1', async () => {
      throw new Error('offline')
    }),
  ).rejects.toThrow('offline')
  await expect(
    loadCandidateRecheckTarget(
      parsed(),
      'branch-1',
      async () => candidate(),
      async () => {
        throw new Error('digest unavailable')
      },
    ),
  ).rejects.toThrow('digest unavailable')
  const publish = vi.fn()
  const loader = createCandidateRecheckTargetLoader(async () => {
    throw new Error('offline')
  }, publish)
  await loader.load(parsed(), 'branch-1')
  expect(publish).toHaveBeenCalledWith(expect.objectContaining({ status: 'unavailable' }))
})

test('切换目标后晚到的读取不覆盖新目标，invalidate后请求同样失效', async () => {
  let finishOld: (value: unknown) => void = () => undefined
  const oldReply = new Promise<unknown>((resolve) => {
    finishOld = resolve
  })
  const read = vi.fn().mockReturnValueOnce(oldReply).mockResolvedValue(candidate())
  const publish = vi.fn()
  const loader = createCandidateRecheckTargetLoader(read, publish, async (value) => sha(value))
  const oldLoad = loader.load(parsed(), 'branch-1')
  const newLoad = loader.load(parsed(), 'branch-1')
  await newLoad
  finishOld({ ...candidate(), candidate_revision: 4 })
  await oldLoad
  expect(publish).toHaveBeenCalledTimes(1)
  expect(publish.mock.calls[0][0].status).toBe('ready')
  publish.mockClear()
  const pending = loader.load(parsed(), 'branch-1')
  loader.invalidate()
  await pending
  expect(publish).not.toHaveBeenCalled()
})

test('同输入失败重发复用持久nonce，输入变化更换且保留旧输入key', () => {
  const values = new Map<string, string>()
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => {
      values.set(key, value)
    },
  }
  const makeUuid = vi
    .fn()
    .mockReturnValueOnce('11111111-1111-4111-8111-111111111111')
    .mockReturnValueOnce('22222222-2222-4222-8222-222222222222')
  const artifact = { ...candidate(), id: 'nonce-candidate' }
  const firstInput = sha(recheckRequestSignature(artifact, 'account-1', 0.4))
  const first = recheckNonce(firstInput, storage, makeUuid)
  expect(recheckNonce(firstInput, storage, makeUuid)).toBe(first)
  const changedInput = sha(recheckRequestSignature({ ...artifact, candidate_revision: 4 }, 'account-1', 0.4))
  expect(recheckNonce(changedInput, storage, makeUuid)).not.toBe(first)
  expect(recheckNonce(firstInput, storage, makeUuid)).toBe(first)
  expect(makeUuid).toHaveBeenCalledTimes(2)
  expect(values.size).toBe(2)
  expect([...values.keys()].join()).not.toContain(text)
})

test('请求签名覆盖真实内容、身份、修订、基线、账户及预算', () => {
  const artifact = candidate()
  const initial = recheckRequestSignature(artifact, 'account-1', 0.4)
  for (const change of [
    { id: 'other' },
    { candidate_revision: 4 },
    { branch_id: 'other' },
    { chapter_id: 'other' },
    { base_revision: 3 },
    { payload: { text: `${text}补一句。` } },
  ])
    expect(recheckRequestSignature({ ...artifact, ...change }, 'account-1', 0.4)).not.toBe(initial)
  expect(recheckRequestSignature(artifact, 'account-2', 0.4)).not.toBe(initial)
  expect(recheckRequestSignature(artifact, 'account-1', 0.8)).not.toBe(initial)
  expect(recheckRequestSignature(structuredClone(artifact), 'account-1', 0.4)).toBe(initial)
})

test('存储不可用时同输入仍复用memory nonce，预算只接受有限正数', () => {
  const broken = {
    getItem: () => {
      throw new Error('denied')
    },
    setItem: () => {
      throw new Error('denied')
    },
  }
  const makeUuid = vi.fn(() => '33333333-3333-4333-8333-333333333333')
  const signature = sha(
    recheckRequestSignature({ ...candidate(), id: 'memory-nonce-candidate' }, 'account-1'),
  )
  expect(recheckNonce(signature, broken, makeUuid)).toBe(recheckNonce(signature, broken, makeUuid))
  expect(makeUuid).toHaveBeenCalledTimes(1)
  expect(parseRecheckBudget(' ').error).toBeUndefined()
  expect(parseRecheckBudget('0.4').value).toBe(0.4)
  for (const value of ['0', '-1', 'NaN', 'Infinity', 'bad'])
    expect(parseRecheckBudget(value).error).toBeTruthy()
})

test('SSR复检报告只读，不提供采纳、恢复或保存稿局部修订按钮，模型文字转义', () => {
  const artifact = reportArtifact()
  artifact.payload.note = '<script>只复检候选😀𠮷</script>'
  const onTarget = vi.fn()
  const html = renderToStaticMarkup(
    createElement(CandidateRecheckReport, { artifact, branchId: 'branch-1', onTarget }),
  )
  expect(html).toContain('候选')
  expect(html).toContain('&lt;script&gt;只复检候选😀𠮷&lt;/script&gt;')
  expect(html).not.toContain('<script>')
  expect(html).not.toMatch(/<button[^>]*>[^<]*(?:采纳|恢复原流程|局部修订)/)
  expect(onTarget).not.toHaveBeenCalled()
})

test('SSR申请入口保留额外模型费用说明且未保存状态禁用，不触发请求', () => {
  const perform = vi.fn(async (action: () => Promise<unknown>) => {
    await action()
  })
  const onStarted = vi.fn()
  const html = renderToStaticMarkup(
    createElement(CandidateRecheckAction, {
      artifact: candidate(),
      chapter,
      branchId: 'branch-1',
      account: 'account-1',
      modelReady: true,
      disabled: false,
      unsaved: true,
      perform,
      onStarted,
    }),
  )
  expect(html).toMatch(/模型调用|模型费用|额外费用/)
  expect(html).toContain('disabled')
  expect(perform).not.toHaveBeenCalled()
  expect(onStarted).not.toHaveBeenCalled()
})

function historyPage() {
  return {
    artifact_id: 'candidate-1',
    items: [
      {
        id: 17,
        kind: 'candidate_edited',
        payload: {
          artifact_id: 'candidate-1',
          candidate_revision: 4,
          previous_revision: 3,
          previous_content_sha256: sha(text),
          previous_candidate: { text, review: { summary: '编辑前的检查。' } },
        },
        created: 1700000000,
      },
    ],
    next_offset: 20,
  }
}

test('历史分页严格归属目标，保留完整旧payload与previous_revision而不冒充新版本', () => {
  const page = historyPage()
  expect(readCandidateHistoryPage(page, 'candidate-1')).toEqual({ items: page.items, next_offset: 20 })
  expect(readCandidateHistoryPage(page, 'candidate-1')?.items[0].payload).toMatchObject({
    previous_revision: 3,
    candidate_revision: 4,
    previous_candidate: page.items[0].payload.previous_candidate,
  })
  const second = {
    ...page,
    items: [{ ...page.items[0], id: 'legacy-event', kind: 'candidate_rechecked' }],
    next_offset: 40,
  }
  expect(readCandidateHistoryPage(second, 'candidate-1', 20)?.next_offset).toBe(40)
  expect(
    readCandidateHistoryPage({ artifact_id: 'candidate-1', items: [], next_offset: null }, 'candidate-1', 40),
  ).toEqual({ items: [], next_offset: null })
})

test('旧事件和旧报告缺完整快照仍可读，不捏造可恢复的候选内容', () => {
  const legacy = {
    artifact_id: 'candidate-1',
    items: [{ id: 'old', kind: 'candidate_rechecked', payload: { artifact_id: 'candidate-1' }, created: 1 }],
    next_offset: null,
  }
  expect(readCandidateHistoryPage(legacy, 'candidate-1')?.items[0].payload.previous_candidate).toBeUndefined()
  expect(parsed().previous_candidate).toBeUndefined()
  for (const previous_candidate of [null, [], 'old text'])
    expect(readCandidateRecheck({ ...reportPayload(), previous_candidate })).toBeUndefined()
})

test('拒绝历史错归属、非法事件类型、损坏快照和超过20项的页面', () => {
  const page = historyPage(),
    item = page.items[0]
  for (const value of [
    { ...page, artifact_id: 'other' },
    { ...page, artifact_id: undefined },
    { ...page, items: [{ ...item, kind: 'chapter_saved' }] },
    { ...page, items: [{ ...item, payload: { ...item.payload, artifact_id: 'other' } }] },
    { ...page, items: [{ ...item, payload: { ...item.payload, previous_revision: 3.5 } }] },
    { ...page, items: [{ ...item, payload: { ...item.payload, previous_content_sha256: 'bad' } }] },
    { ...page, items: [{ ...item, payload: { ...item.payload, previous_candidate: [] } }] },
    { ...page, items: [{ ...item, created: 'yesterday' }] },
    { ...page, items: Array.from({ length: 21 }, (_, id) => ({ ...item, id })) },
  ])
    expect(readCandidateHistoryPage(value, 'candidate-1')).toBeUndefined()
})

test('分页游标必须为严格前进的非负整数，损坏游标不能引起重复翻页', () => {
  for (const next_offset of [0, 19, 20, -1, 20.5, '40', Number.NaN])
    expect(readCandidateHistoryPage({ ...historyPage(), next_offset }, 'candidate-1', 20)).toBeUndefined()
  expect(readCandidateHistoryPage(historyPage(), 'candidate-1', -1)).toBeUndefined()
})

test('报告前快照为完整payload安全转义，按本轮目标修订标历史而不套嵌套旧报告版本', () => {
  const unsafe = '<script>旧候选😀𠮷</script>'
  const previous_candidate = {
    text: unsafe,
    review: { summary: unsafe },
    previous_plot_audit: { ...reportPayload().plot_audit, candidate_revision: 1 },
  }
  const artifact = reportArtifact()
  artifact.payload.previous_candidate = previous_candidate
  expect(parsed(artifact.payload).previous_candidate).toEqual(previous_candidate)
  const html = renderToStaticMarkup(
    createElement(CandidateRecheckReport, { artifact, branchId: 'branch-1', onTarget: () => undefined }),
  )
  expect(html).toContain('复检前候选与检查记录')
  expect(html).toContain('本轮复检前候选修订 3')
  expect(html).toContain('仅为历史依据，不能作为当前候选结论')
  expect(html).toContain('嵌套的旧报告可能来自更早检查')
  expect(html).toContain('&lt;script&gt;旧候选😀𠮷&lt;/script&gt;')
  expect(html).not.toContain('<script>')
})

test('历史SSR保持默认折叠和惰性读取，无API调用、恢复或采用入口', () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(() => {
    throw new Error('SSR must not fetch history')
  })
  try {
    const html = renderToStaticMarkup(createElement(CandidateHistory, { artifactId: 'candidate-1' }))
    expect(html).toContain('候选编辑与复检历史')
    expect(html).toContain('不代表当前结论')
    expect(html).toContain('旧事件未保留完整快照时会明确说明')
    expect(html).not.toMatch(/<details[^>]*\bopen(?:[=>\s])/)
    const buttons = [...html.matchAll(/<button\b[^>]*>([\s\S]*?)<\/button>/g)].map((match) =>
      match[1].replace(/<[^>]*>/g, ''),
    )
    expect(buttons.join()).not.toMatch(/恢复|采纳|采用/)
    expect(fetch).not.toHaveBeenCalled()
  } finally {
    fetch.mockRestore()
  }
})

function recheckReceipt(clientRequestId: string) {
  const artifact = candidate()
  return {
    id: 'recheck-receipt-1',
    kind: 'candidate_recheck',
    novel_id: 'novel-1',
    branch_id: 'branch-1',
    status: 'queued',
    created: 1,
    cancel: 0,
    error: '',
    payload: {
      novel_id: 'novel-1',
      branch_id: artifact.branch_id,
      chapter_id: artifact.chapter_id,
      base_revision: artifact.base_revision,
      source_candidate_id: artifact.id,
      candidate_revision: artifact.candidate_revision,
      account: 'account-1',
      client_request_id: clientRequestId,
      budget_limit: null,
    },
    result: {
      candidate_recheck_version: 'candidate-recheck/1',
      candidate_recheck_snapshot: {
        artifact_id: artifact.id,
        candidate_revision: artifact.candidate_revision,
        source_workflow_id: artifact.task_id,
      },
    },
  }
}

test('确认有效202回执后同输入可分配新nonce，重复旧确认不能删除新nonce', () => {
  const values = new Map<string, string>()
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => {
      values.set(key, value)
    },
    removeItem: (key: string) => {
      values.delete(key)
    },
  }
  const makeUuid = vi
    .fn()
    .mockReturnValueOnce('44444444-4444-4444-8444-444444444444')
    .mockReturnValueOnce('55555555-5555-4555-8555-555555555555')
  const signature = sha('successful receipt nonce acknowledgement')
  const first = recheckNonce(signature, storage, makeUuid)
  expect(
    readCandidateRecheckReceipt(recheckReceipt(first), {
      artifact: candidate(),
      account: 'account-1',
      clientRequestId: first,
    })?.id,
  ).toBe('recheck-receipt-1')
  expect(acknowledgeRecheckNonce(signature, first, storage)).toBe(true)
  expect(values.size).toBe(0)
  const second = recheckNonce(signature, storage, makeUuid)
  expect(second).not.toBe(first)
  expect(acknowledgeRecheckNonce(signature, first, storage)).toBe(false)
  expect(recheckNonce(signature, storage, makeUuid)).toBe(second)
  expect([...values.values()]).toEqual([second])
  expect(makeUuid).toHaveBeenCalledTimes(2)
})

test('确认后持久存储删除失败也不复活旧nonce，旧确认不清掉当前memory新值', () => {
  const old = '66666666-6666-4666-8666-666666666666'
  const next = '77777777-7777-4777-8777-777777777777'
  const storage = {
    getItem: () => old,
    setItem: () => {
      throw new Error('storage denied')
    },
    removeItem: () => {
      throw new Error('storage denied')
    },
  }
  const signature = sha('acknowledged nonce with undeletable storage')
  const makeUuid = vi.fn(() => next)
  expect(recheckNonce(signature, storage, makeUuid)).toBe(old)
  expect(acknowledgeRecheckNonce(signature, old, storage)).toBe(true)
  expect(recheckNonce(signature, storage, makeUuid)).toBe(next)
  acknowledgeRecheckNonce(signature, old, storage)
  expect(recheckNonce(signature, storage, makeUuid)).toBe(next)
  expect(makeUuid).toHaveBeenCalledTimes(1)
})

test('实际回执绑定候选和冻结来源，预算null与省略同义，显式预算须逐值对应', () => {
  const nonce = '88888888-8888-4888-8888-888888888888'
  const receipt = recheckReceipt(nonce)
  const expected = { artifact: candidate(), account: 'account-1', clientRequestId: nonce }
  expect(readCandidateRecheckReceipt(receipt, expected)).toEqual(receipt)
  const { budget_limit: omitted, ...withoutBudget } = receipt.payload
  expect(omitted).toBeNull()
  expect(readCandidateRecheckReceipt({ ...receipt, payload: withoutBudget }, expected)).toBeDefined()
  const withBudget = { ...receipt, payload: { ...receipt.payload, budget_limit: 0.4 } }
  expect(readCandidateRecheckReceipt(withBudget, { ...expected, budgetLimit: 0.4 })).toBeDefined()
  expect(readCandidateRecheckReceipt(withBudget, { ...expected, budgetLimit: 0.8 })).toBeUndefined()
})

test('错源、错nonce和不完整回执不能作为确认；未确认失败重试仍保留原nonce', () => {
  const signature = sha('invalid receipt must preserve retry nonce')
  const makeUuid = vi.fn(() => '99999999-9999-4999-8999-999999999999')
  const nonce = recheckNonce(signature, undefined, makeUuid)
  const receipt = recheckReceipt(nonce)
  const expected = { artifact: candidate(), account: 'account-1', clientRequestId: nonce }
  for (const value of [
    { ...receipt, id: '' },
    { ...receipt, kind: 'workflow' },
    { ...receipt, branch_id: 'other-branch' },
    { ...receipt, novel_id: '' },
    { ...receipt, status: '' },
    { ...receipt, created: '1' },
    { ...receipt, cancel: 2 },
    { ...receipt, error: null },
    { ...receipt, payload: { ...receipt.payload, source_candidate_id: 'other-candidate' } },
    { ...receipt, payload: { ...receipt.payload, candidate_revision: 4 } },
    { ...receipt, payload: { ...receipt.payload, branch_id: 'other-branch' } },
    { ...receipt, payload: { ...receipt.payload, chapter_id: 'other-chapter' } },
    { ...receipt, payload: { ...receipt.payload, base_revision: 3 } },
    { ...receipt, payload: { ...receipt.payload, account: 'other-account' } },
    { ...receipt, payload: { ...receipt.payload, client_request_id: 'other-nonce' } },
    { ...receipt, result: null },
    { ...receipt, result: { ...receipt.result, candidate_recheck_version: 'candidate-recheck/2' } },
    {
      ...receipt,
      result: {
        ...receipt.result,
        candidate_recheck_snapshot: {
          ...receipt.result.candidate_recheck_snapshot,
          source_workflow_id: 'other-workflow',
        },
      },
    },
  ])
    expect(readCandidateRecheckReceipt(value, expected)).toBeUndefined()
  expect(recheckNonce(signature, undefined, makeUuid)).toBe(nonce)
  expect(makeUuid).toHaveBeenCalledTimes(1)
})
