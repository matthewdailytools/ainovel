import { createHash } from 'node:crypto'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { expect, test } from 'vitest'
import { ApiError, type Artifact } from './api'
import {
  canonical,
  canAdvanceCorrectionPhase,
  correctionOwner,
  createCorrectionOperation,
  readCapsule,
  readCorrectionTask,
  readRecheckPreview,
  readSourcePreview,
  readSourceReceipt,
  sourceCorrectionEligibility,
  SOURCE_CORRECTION_VERSION,
  sourceReceiptTargetCurrent,
  capsuleMatchesCurrent,
  readCorrectedReport,
  CORRECTED_RECHECK_VERSION,
  ownerKey,
  type Capsule,
  type Owner,
} from './sourceCorrection'
import { SourceCorrectionArtifact, SourceReceiptView } from './SourceCorrectionPanel'
const sha = (value: string) => createHash('sha256').update(value).digest('hex')
const source = '先说明桥规。林芷走向旧桥，记下新的检修标志。😀𠮷'
const fixed = ['不得改变桥规数值。']
const owner: Owner = {
  novelId: 'novel',
  branchId: 'branch',
  artifactId: 'candidate',
  chapterId: 'chapter',
  baseRevision: 0,
  candidateRevision: 0,
  workflowId: 'parent',
}
const core = (actor = '', certainty = 'ambiguous', evidence = source) => ({
  author_evidence: evidence,
  actor,
  action: '说明',
  object: '桥规',
  direction: '',
  result: '原文交代',
  certainty,
  transfer_object: '',
  supplier: '',
  recipient: '',
  transfer_scope: 'none',
  recipient_scope: 'unspecified',
  scope_condition: '',
})
function rawPreview() {
  return {
    version: SOURCE_CORRECTION_VERSION,
    target: owner.artifactId,
    candidate_revision: 0,
    source_text: source,
    items: [
      { id: 'P01', ...core() },
      { id: 'P02', ...core('林芷', 'explicit') },
    ],
    source_text_sha256: sha(source),
    original_contract_sha256: 'b'.repeat(64),
    basis_sha256: 'c'.repeat(64),
    story_revision: 3,
    source_workflow_id: owner.workflowId,
    notice: '两个独立来源步骤，产生额外费用。',
  }
}
function sourceReceipt() {
  const position = { source_id: 'AUTHOR0001', start: 0, end: source.length }
  return {
    version: SOURCE_CORRECTION_VERSION,
    state: 'ready',
    snapshot: {
      source_kind: 'author_plot',
      artifact_id: owner.artifactId,
      candidate_revision: 0,
      chapter_id: 'chapter',
      chapter_revision: 0,
      story_revision: 3,
      source_workflow_id: 'parent',
      content_sha256: 'd'.repeat(64),
      source_text_sha256: sha(source),
      original_contract_sha256: 'b'.repeat(64),
      basis_sha256: 'c'.repeat(64),
      item_ids: ['P01'],
      fixed,
      plot: { number: 1, title: '桥头', plot: source },
      prepared_contract: { items: [core(), core('林芷', 'explicit')], note: '原解释保持' },
    },
    proposal: {
      items: [
        {
          item_id: 'P01',
          actor: '',
          statement_kind: 'narrative_requirement',
          certainty: 'explicit',
          interpretation_reason: '原话是在要求叙述交代，不指定角色发言。',
          evidence: source,
          evidence_source: position,
        },
        {
          item_id: 'P02',
          actor: '林芷',
          statement_kind: 'story_event',
          certainty: 'explicit',
          interpretation_reason: '原事项保持',
          evidence: source,
          evidence_source: position,
        },
      ],
    },
    corrected_contract: { items: [core('', 'explicit'), core('林芷', 'explicit')], note: '原解释保持' },
    preservation: {
      item_checks: ['P01', 'P02'].map((item_id) => ({
        item_id,
        result: 'pass',
        reason: '逐项保留全部事件字段',
        evidence: source,
        evidence_source: position,
      })),
      fixed_checks: [
        { condition_id: 'FIX001', result: 'pass', reason: '保留原约定，未判断正文履约', evidence: fixed[0] },
      ],
      summary: '全P及fixed保持核对',
    },
    note: '不读取正文，不改变原解释和事实。',
  }
}
function capsule(): Capsule {
  return {
    phase: 'source',
    owner,
    preview: readSourcePreview(rawPreview(), owner)!,
    request: {
      candidate_revision: 0,
      preview_basis_sha256: 'c'.repeat(64),
      item_ids: ['P01'],
      reason: '原解释把交代要求当成对话',
      account: 'deepseek',
      client_request_id: 'stable-source-nonce',
    },
  }
}
function task(input = capsule()) {
  return {
    id: 'new-task',
    kind:
      input.phase === 'source' ? 'source_interpretation_correction' : 'source_corrected_candidate_recheck',
    novel_id: owner.novelId,
    branch_id: owner.branchId,
    status: 'queued',
    created: 1,
    cancel: 0,
    error: '',
    payload: {
      ...input.request,
      novel_id: owner.novelId,
      branch_id: owner.branchId,
      chapter_id: owner.chapterId,
      base_revision: owner.baseRevision,
      source_candidate_id: owner.artifactId,
      source_identity_context: false,
      budget_limit: null,
    },
    result:
      input.phase === 'source'
        ? {
            source_correction_version: SOURCE_CORRECTION_VERSION,
            source_correction_snapshot: { ...sourceReceipt().snapshot, version: SOURCE_CORRECTION_VERSION },
          }
        : {
            source_corrected_recheck_version: CORRECTED_RECHECK_VERSION,
            source_corrected_recheck_snapshot: {
              ...sourceReceipt().snapshot,
              version: CORRECTED_RECHECK_VERSION,
              receipt_id: 'receipt',
              basis_sha256: 'e'.repeat(64),
            },
          },
  }
}
class Store {
  data = new Map<string, string>()
  getItem(key: string) {
    return this.data.get(key) ?? null
  }
  setItem(key: string, value: string) {
    this.data.set(key, value)
  }
  removeItem(key: string) {
    this.data.delete(key)
  }
}
const artifact: Artifact = {
  id: 'candidate',
  kind: 'chapter_draft',
  branch_id: 'branch',
  chapter_id: 'chapter',
  task_id: 'parent',
  base_revision: 0,
  candidate_revision: 0,
  status: 'candidate',
  payload: { text: '原候选正文' },
  basis: {},
}
const parent = {
  id: 'parent',
  kind: 'workflow',
  branch_id: 'branch',
  status: 'needs_review',
  payload: { mode: 'assisted', plot_contract_check: true },
  result: { waiting_artifact: 'candidate' },
}
const chapter = {
  id: 'chapter',
  branch_id: 'branch',
  position: 1,
  title: '本章',
  revision: 0,
  content: '',
  stale: 0,
}

test('仅匹配的半自动待审候选可操作，无模型也能保持手写', () => {
  expect(correctionOwner(artifact, 'novel', 'branch')).toEqual(owner)
  expect(sourceCorrectionEligibility(artifact, parent, chapter, 'branch')).toBeUndefined()
  for (const [row, book, current] of [
    [{ ...artifact, status: 'accepted' }, parent, chapter],
    [artifact, { ...parent, payload: { ...parent.payload, mode: 'automatic' } }, chapter],
    [artifact, parent, { ...chapter, revision: 1 }],
    [artifact, { ...parent, result: { waiting_artifact: 'other' } }, chapter],
  ] as const)
    expect(sourceCorrectionEligibility(row, book, current, 'branch')).toBeDefined()
  expect(sourceCorrectionEligibility(artifact, parent, chapter, 'branch', true)).toContain('先保存')
})
test('原情节预览保留全部编号与来源摘要，目标修订不符拒绝', () => {
  const preview = readSourcePreview(rawPreview(), owner)!
  expect(preview.items.map((row) => row.id)).toEqual(['P01', 'P02'])
  expect(readSourcePreview(rawPreview(), { ...owner, candidateRevision: 1 })).toBeUndefined()
  expect(
    readSourcePreview({ ...rawPreview(), items: [rawPreview().items[0], rawPreview().items[0]] }, owner),
  ).toBeUndefined()
})
test('只读ready回执保留原主体、性质、确定性、全部P与固定条件，原稿不作采用', () => {
  const receipt = readSourceReceipt(sourceReceipt())!
  expect(receipt.state).toBe('ready')
  expect(receipt.snapshot.prepared_contract.items[0].certainty).toBe('ambiguous')
  expect(receipt.proposal.items[0].statement_kind).toBe('narrative_requirement')
  const html = renderToStaticMarkup(
    createElement(SourceReceiptView, { receipt, reason: '<script>x</script>' }),
  )
  for (const text of [
    'P01',
    'P02',
    'FIX001',
    '不是正文已兑现',
    '模型解释不是作者新增条件',
    '回执就绪',
    '&lt;script&gt;',
  ])
    expect(html).toContain(text)
  expect(html).not.toContain('<script>')
  expect(html).not.toContain('<button')
})
test.each([
  {
    label: '状态对象',
    change: (v: ReturnType<typeof sourceReceipt>) => {
      Object.assign(v, { state: { toString: null } })
    },
  },
  {
    label: '缺P',
    change: (v: ReturnType<typeof sourceReceipt>) => {
      v.proposal.items.pop()
    },
  },
  {
    label: '遗漏fixed',
    change: (v: ReturnType<typeof sourceReceipt>) => {
      v.preservation.fixed_checks = []
    },
  },
  {
    label: 'ready实际unknown',
    change: (v: ReturnType<typeof sourceReceipt>) => {
      v.preservation.item_checks[0].result = 'unknown'
    },
  },
  {
    label: '篡改事件方向',
    change: (v: ReturnType<typeof sourceReceipt>) => {
      v.corrected_contract.items[0].direction = '相反方向'
    },
  },
  {
    label: '未选事项改主体',
    change: (v: ReturnType<typeof sourceReceipt>) => {
      v.corrected_contract.items[1].actor = '新增人'
      v.proposal.items[1].actor = '新增人'
    },
  },
  {
    label: '不连续引文',
    change: (v: ReturnType<typeof sourceReceipt>) => {
      v.proposal.items[0].evidence = '不存在的证据'
    },
  },
  {
    label: 'UTF16位置错误',
    change: (v: ReturnType<typeof sourceReceipt>) => {
      v.proposal.items[0].evidence_source.end--
    },
  },
  {
    label: '错误fixed编号',
    change: (v: ReturnType<typeof sourceReceipt>) => {
      v.preservation.fixed_checks[0].condition_id = 'FIX002'
    },
  },
])('非法来源回执不冒充可复检：$label', ({ change }) => {
  const value = sourceReceipt()
  change(value)
  expect(() => readSourceReceipt(value)).not.toThrow()
  expect(readSourceReceipt(value)).toBeUndefined()
})
test('真实未解决歧义作为unverified只读保留', () => {
  const value = sourceReceipt()
  value.state = 'unverified'
  value.proposal.items[0].certainty = 'ambiguous'
  value.corrected_contract.items[0].certainty = 'ambiguous'
  expect(readSourceReceipt(value)?.state).toBe('unverified')
})
test('ready后读取独立复检preview，回执/完整core或修订不符拒绝', () => {
  const receipt = readSourceReceipt(sourceReceipt())!
  const preview = {
    version: CORRECTED_RECHECK_VERSION,
    target: 'candidate',
    candidate_revision: 0,
    receipt_id: 'receipt',
    receipt_payload_sha256: 'f'.repeat(64),
    basis_sha256: 'e'.repeat(64),
    story_revision: 3,
    source_text: source,
    corrected_contract: sourceReceipt().corrected_contract,
    notice: '独立正文复检费用',
  }
  expect(readRecheckPreview(preview, owner, 'receipt', receipt)).toBeDefined()
  expect(readRecheckPreview({ ...preview, receipt_id: 'other' }, owner, 'receipt', receipt)).toBeUndefined()
  const modified = structuredClone(preview)
  modified.corrected_contract.items[0].result = '换了事件结果'
  expect(readRecheckPreview(modified, owner, 'receipt', receipt)).toBeUndefined()
})
test('稳定任务回执严格核对kind、owner、request、P范围与basis', () => {
  expect(readCorrectionTask(task(), capsule())?.id).toBe('new-task')
  for (const modified of [
    { ...task(), kind: 'candidate_recheck' },
    { ...task(), branch_id: 'other' },
    { ...task(), status: { toString: null } },
    { ...task(), payload: { ...task().payload, preview_basis_sha256: 'f'.repeat(64) } },
  ]) {
    expect(() => readCorrectionTask(modified, capsule())).not.toThrow()
    expect(readCorrectionTask(modified, capsule())).toBeUndefined()
  }
})
test('未知POST不重发，404仍保留原nonce，确认后显式释放才能下一步', async () => {
  const storage = new Store(),
    calls: [string, string | undefined][] = []
  let found = false
  const operation = createCorrectionOperation(
    ownerKey(owner),
    async (path, method) => {
      calls.push([path, method])
      if (method === 'POST') throw new Error('lost response')
      if (!found) throw new ApiError('not_found', '未找到', 404)
      return task()
    },
    storage,
    () => undefined,
  )
  await operation.start(capsule())
  expect(operation.state.status).toBe('uncertain')
  await operation.start({
    ...capsule(),
    request: { ...capsule().request, client_request_id: 'new-must-not-send' },
  })
  expect(calls.filter(([, method]) => method === 'POST')).toHaveLength(1)
  await operation.lookup()
  expect(operation.state.status).toBe('uncertain')
  expect(operation.releaseKnown()).toBe(false)
  found = true
  await operation.lookup()
  expect(operation.state.status).toBe('confirmed')
  expect(
    calls.slice(1).every(([path, method]) => method === 'GET' && path.endsWith('/stable-source-nonce')),
  ).toBe(true)
  expect(operation.releaseKnown()).toBe(true)
})
test('刷新或浏览器重开恢复原capsule，绝不自动创建新任务', async () => {
  const storage = new Store()
  const first = createCorrectionOperation(
    ownerKey(owner),
    async () => {
      throw new Error('lost')
    },
    storage,
    () => undefined,
  )
  await first.start(capsule())
  first.dispose()
  const calls: string[] = []
  const restored = createCorrectionOperation(
    ownerKey(owner),
    async (path, method) => {
      calls.push(method || 'GET')
      expect(path).toContain('stable-source-nonce')
      return task()
    },
    storage,
    () => undefined,
  )
  expect(restored.state.status).toBe('uncertain')
  expect(restored.state.pending?.request).toEqual(capsule().request)
  await restored.start(capsule())
  expect(calls).toEqual([])
  await restored.lookup()
  expect(calls).toEqual(['GET'])
  expect(restored.state.status).toBe('confirmed')
})
test('切换目标或卸载后的晚回执不发布，原请求仍可恢复查询', async () => {
  let finish!: (value: unknown) => void
  const wait = new Promise<unknown>((resolve) => {
    finish = resolve
  })
  const states: string[] = [],
    storage = new Store()
  const operation = createCorrectionOperation(
    ownerKey(owner),
    () => wait,
    storage,
    (state) => states.push(state.status),
  )
  const pending = operation.start(capsule())
  operation.dispose()
  finish(task())
  await pending
  expect(states).toEqual(['sending'])
  expect(storage.data.size).toBe(1)
})
test('409拒绝保留范围与原因，不能自动换nonce重投', async () => {
  let calls = 0
  const operation = createCorrectionOperation(
    ownerKey(owner),
    async () => {
      calls++
      throw new ApiError('source_correction_preview_changed', '依据改变', 409)
    },
    new Store(),
    () => undefined,
  )
  await operation.start(capsule())
  await operation.start(capsule())
  expect(calls).toBe(1)
  expect(operation.state.status).toBe('rejected')
  expect(operation.state.pending?.request).toEqual(capsule().request)
})
test('存储失败或损坏的原提交记录阻止新付费调用', async () => {
  let calls = 0
  const storage = new Store()
  storage.setItem = () => {
    throw new Error('quota')
  }
  const operation = createCorrectionOperation(
    ownerKey(owner),
    async () => {
      calls++
      return task()
    },
    storage,
    () => undefined,
  )
  await operation.start(capsule())
  expect(calls).toBe(0)
  expect(operation.state.status).toBe('unavailable')
  const corrupted = new Store()
  corrupted.data.set(`ainovel.source-correction.1:${ownerKey(owner)}`, '{broken')
  const old = createCorrectionOperation(
    ownerKey(owner),
    async () => {
      calls++
      return task()
    },
    corrupted,
    () => undefined,
  )
  await old.start(capsule())
  expect(calls).toBe(0)
  expect(old.state.status).toBe('unavailable')
})
test('原capsule可在同候选修订改变后仅查证，不能移到另一作品', () => {
  expect(readCapsule(capsule(), ownerKey(owner))).toBeDefined()
  expect(readCapsule(capsule(), ownerKey({ ...owner, branchId: 'other' }))).toBeUndefined()
  expect(canonical(capsule().request)).toContain('preview_basis_sha256')
})
test('两种回执格式异常仍只读，没有编辑/采纳/拒绝入口', () => {
  for (const kind of ['source_interpretation_correction', 'source_corrected_candidate_recheck']) {
    const html = renderToStaticMarkup(
      createElement(SourceCorrectionArtifact, {
        artifact: { ...artifact, id: 'receipt', kind, payload: {} },
        branchId: 'branch',
        onTarget: () => undefined,
      }),
    )
    expect(html).toContain('不能作为有效或通过结论')
    expect(html).not.toContain('<textarea')
    expect(html).not.toContain('采用此结果')
    expect(html).not.toContain('拒绝')
  }
})

test('800个Unicode码点的原引文含emoji与扩展字，UTF16位置大于800仍有效', () => {
  const value = sourceReceipt()
  const long = source + '𠮷'.repeat(800 - [...source].length)
  expect([...long]).toHaveLength(800)
  expect(long.length).toBeGreaterThan(800)
  value.snapshot.plot.plot = long
  value.snapshot.source_text_sha256 = sha(long)
  for (const row of [...value.snapshot.prepared_contract.items, ...value.corrected_contract.items])
    row.author_evidence = long
  for (const row of [...value.proposal.items, ...value.preservation.item_checks]) {
    row.evidence = long
    row.evidence_source.end = long.length
  }
  expect(readSourceReceipt(value)?.state).toBe('ready')
  const cut = structuredClone(value)
  cut.proposal.items[0].evidence = long.slice(0, long.length - 1)
  cut.proposal.items[0].evidence_source.end--
  expect(readSourceReceipt(cut)).toBeUndefined()
  const beyond = structuredClone(value)
  beyond.proposal.items[0].evidence_source.end++
  expect(readSourceReceipt(beyond)).toBeUndefined()
})
test('操作原因可为空，码点长度超限才拒绝，不增加作者必填任务', () => {
  const blank = capsule()
  if (blank.phase !== 'source' || !('reason' in blank.request)) throw new Error('fixture')
  blank.request.reason = ''
  expect(readCapsule(blank, ownerKey(owner))).toBeDefined()
  blank.request.reason = '𠮷'.repeat(2000)
  expect(readCapsule(blank, ownerKey(owner))).toBeDefined()
  blank.request.reason += '字'
  expect(readCapsule(blank, ownerKey(owner))).toBeUndefined()
})
test('未选环境事项原合法explicit空actor保持合法，不新增主体义务', () => {
  const value = sourceReceipt()
  value.snapshot.prepared_contract.items[1].actor = ''
  value.corrected_contract.items[1].actor = ''
  value.proposal.items[1].actor = ''
  expect(readSourceReceipt(value)?.state).toBe('ready')
})
test('同ID候选编辑或正文hash变化使ready来源回执成为历史，不可复检', async () => {
  const receipt = readSourceReceipt(sourceReceipt())!
  receipt.snapshot.content_sha256 = sha('原候选正文')
  expect(await sourceReceiptTargetCurrent(receipt, artifact, 'branch')).toBe(true)
  expect(await sourceReceiptTargetCurrent(receipt, { ...artifact, candidate_revision: 1 }, 'branch')).toBe(
    false,
  )
  expect(
    await sourceReceiptTargetCurrent(receipt, { ...artifact, payload: { text: '已经改稿' } }, 'branch'),
  ).toBe(false)
  expect(await sourceReceiptTargetCurrent(receipt, artifact, 'other')).toBe(false)
  const html = renderToStaticMarkup(createElement(SourceReceiptView, { receipt, historical: true }))
  expect(html).toContain('历史回执，当前依据已失效')
  expect(html).not.toContain('回执就绪')
})
test('提交时本地签名只绑定当时版本，同修订检查变化也不得刷新为当前', async () => {
  const record = { ...capsule(), client_signature: sha('old payload') }
  expect(await capsuleMatchesCurrent(record, 'old payload')).toBe(true)
  expect(await capsuleMatchesCurrent(record, 'new payload')).toBe(false)
  expect(readCapsule(record, ownerKey(owner))?.client_signature).toBe(record.client_signature)
})
test('正文复检阶段未知提交同样仅查原nonce，不混用旧candidate-recheck接口', async () => {
  const receipt = readSourceReceipt(sourceReceipt())!
  const raw = {
    version: CORRECTED_RECHECK_VERSION,
    target: 'candidate',
    candidate_revision: 0,
    receipt_id: 'receipt',
    receipt_payload_sha256: 'f'.repeat(64),
    basis_sha256: 'e'.repeat(64),
    story_revision: 3,
    source_text: source,
    corrected_contract: sourceReceipt().corrected_contract,
    notice: '额外模型费用',
  }
  const record: Capsule = {
    phase: 'recheck',
    owner,
    preview: readRecheckPreview(raw, owner, 'receipt', receipt)!,
    request: {
      candidate_revision: 0,
      preview_basis_sha256: 'e'.repeat(64),
      receipt_id: 'receipt',
      account: 'deepseek',
      client_request_id: 'stable-body-nonce',
    },
  }
  const calls: [string, string | undefined][] = []
  const operation = createCorrectionOperation(
    ownerKey(owner),
    async (path, method) => {
      calls.push([path, method])
      if (method === 'POST') throw new Error('unknown')
      return task(record)
    },
    new Store(),
    () => undefined,
  )
  await operation.start(record)
  await operation.start(record)
  await operation.lookup()
  expect(operation.state.status).toBe('confirmed')
  expect(calls).toEqual([
    ['/artifacts/candidate/recheck-source-correction', 'POST'],
    ['/artifacts/candidate/recheck-source-correction/stable-body-nonce', 'GET'],
  ])
})
test('未约定的请求字段不能从本地capsule发送给服务端', () => {
  const value = capsule()
  Object.assign(value.request, { source_identity_context: true })
  expect(readCapsule(value, ownerKey(owner))).toBeUndefined()
})
function correctedReport() {
  const bodyHash = sha('原候选正文')
  const plot = {
    version: 'author-plot-audit/2',
    source: { kind: 'author_plot', text_sha256: sha(source) },
    candidate_sha256: bodyHash,
    candidate_revision: 0,
    status: 'model_pass',
    summary: '本轮模型标一致',
    interpretation_note: '纠偏后原范围',
    policy: '不采纳',
    items: [{ id: 'P01', ...core('', 'explicit') }],
    checks: [
      {
        item_id: 'P01',
        result: 'pass',
        observed: '文本完成原交代要求',
        evidence: '原候选正文',
        reason: '本轮证据',
        repair: '',
      },
    ],
    findings: [],
  }
  return {
    version: CORRECTED_RECHECK_VERSION,
    target: {
      kind: 'candidate',
      artifact_id: 'candidate',
      revision: 0,
      chapter_id: 'chapter',
      base_revision: 0,
      content_sha256: bodyHash,
    },
    receipt_id: 'receipt',
    review: {
      checks: [{ condition: fixed[0], result: 'pass', evidence: '原候选正文' }],
      findings: [],
      summary: '独立正文复检',
      plot_audit: plot,
    },
    plot_audit: plot,
    review_passed: true,
    memory_generated: true,
    previous_candidate: { text: '原候选正文' },
    note: '不采用不恢复',
  }
}
test('新正文复检报告只接受一致的目标和结论，未知不被总pass掩盖', () => {
  expect(readCorrectedReport(correctedReport())).toBeDefined()
  const unknown = correctedReport()
  unknown.plot_audit.checks[0].result = 'unknown'
  unknown.plot_audit.status = 'unverified'
  expect(readCorrectedReport(unknown)).toBeUndefined()
  const noQuote = correctedReport()
  noQuote.review.checks[0].evidence = '  '
  expect(readCorrectedReport(noQuote)).toBeUndefined()
  const wrongTarget = correctedReport()
  wrongTarget.target.content_sha256 = 'a'.repeat(64)
  expect(readCorrectedReport(wrongTarget)).toBeUndefined()
})

function phaseAdvanceInput(): Parameters<typeof canAdvanceCorrectionPhase>[0] {
  const record = capsule()
  const rawTask = task(record)
  rawTask.status = 'succeeded'
  Object.assign(rawTask.result, { artifact_id: 'receipt' })
  const completed = readCorrectionTask(rawTask, record)!
  return {
    nextPhase: 'recheck',
    currentPhase: 'source',
    currentBasis: record.request.preview_basis_sha256,
    hasIncoming: false,
    inputAvailable: true,
    stale: false,
    operation: { status: 'confirmed', pending: record, task: completed, message: '' },
    task: completed,
    receipt: {
      artifact: { id: 'receipt', task_id: completed.id },
      data: readSourceReceipt(sourceReceipt())!,
    },
    receiptCurrent: true,
  }
}

test('同一来源成功任务的当前ready回执可直接准备首次正文复检阶段', () => {
  expect(canAdvanceCorrectionPhase(phaseAdvanceInput())).toBe(true)
})

test.each([
  { field: 'nextPhase', value: 'source' },
  { field: 'currentPhase', value: 'recheck' },
  { field: 'currentBasis', value: 'a'.repeat(64) },
  { field: 'hasIncoming', value: true },
  { field: 'inputAvailable', value: false },
  { field: 'stale', value: true },
  { field: 'receiptCurrent', value: false },
])('阶段切换保留同阶段、待确认或过期边界：$field', ({ field, value }) => {
  expect(canAdvanceCorrectionPhase({ ...phaseAdvanceInput(), [field]: value })).toBe(false)
})

test.each(['uncertain', 'rejected', 'sending'] as const)('原提交%s时不能跳过显式核对', (status) => {
  const input = phaseAdvanceInput()
  input.operation.status = status
  expect(canAdvanceCorrectionPhase(input)).toBe(false)
})

test('必须核同一来源capsule、成功任务、产物与basis，其他ready回执仍显式确认', () => {
  const changes: ((input: ReturnType<typeof phaseAdvanceInput>) => void)[] = [
    (input) => {
      input.task!.status = 'queued'
    },
    (input) => {
      input.operation.task = { ...input.task!, id: 'another-task' }
    },
    (input) => {
      input.receipt!.artifact.task_id = 'another-task'
    },
    (input) => {
      input.task!.result.artifact_id = 'another-receipt'
    },
    (input) => {
      input.receipt!.data.snapshot.basis_sha256 = 'f'.repeat(64)
    },
    (input) => {
      input.receipt!.data.state = 'unverified'
    },
    (input) => {
      input.operation.pending!.phase = 'recheck'
    },
  ]
  for (const change of changes) {
    const input = phaseAdvanceInput()
    change(input)
    expect(canAdvanceCorrectionPhase(input)).toBe(false)
  }
})

test('读取正文预览和释放已确认来源记录不派发新POST，下一阶段仍须显式提交', async () => {
  const input = phaseAdvanceInput()
  const calls: [string, string | undefined][] = []
  const store = new Store()
  const raw = {
    version: CORRECTED_RECHECK_VERSION,
    target: 'candidate',
    candidate_revision: 0,
    receipt_id: 'receipt',
    receipt_payload_sha256: 'f'.repeat(64),
    basis_sha256: 'e'.repeat(64),
    story_revision: 3,
    source_text: source,
    corrected_contract: sourceReceipt().corrected_contract,
    notice: '额外模型费用',
  }
  const call = async (path: string, method?: string) => {
    calls.push([path, method])
    return method === 'GET' ? raw : path.endsWith('recheck-source-correction') ? task(body) : input.task
  }
  const operation = createCorrectionOperation(ownerKey(owner), call, store, () => undefined)
  await operation.start(capsule())
  const preview = readRecheckPreview(
    await call('/artifacts/candidate/recheck-source-correction-preview?receipt_id=receipt', 'GET'),
    owner,
    'receipt',
    input.receipt!.data,
  )!
  expect(preview).toBeDefined()
  expect(canAdvanceCorrectionPhase({ ...input, operation: operation.state })).toBe(true)
  expect(operation.releaseKnown()).toBe(true)
  expect(operation.state.status).toBe('idle')
  expect(store.data.size).toBe(0)
  expect(calls.map((row) => row[1])).toEqual(['POST', 'GET'])
  const body: Capsule = {
    phase: 'recheck',
    owner,
    preview,
    request: {
      candidate_revision: 0,
      preview_basis_sha256: preview.basis_sha256,
      receipt_id: 'receipt',
      account: 'deepseek',
      client_request_id: 'explicit-new-body-nonce',
    },
  }
  await operation.start(body)
  expect(operation.state.status).toBe('confirmed')
  expect(calls).toEqual([
    ['/artifacts/candidate/source-interpretation-corrections', 'POST'],
    ['/artifacts/candidate/recheck-source-correction-preview?receipt_id=receipt', 'GET'],
    ['/artifacts/candidate/recheck-source-correction', 'POST'],
  ])
})

test('阶段释放存储失败保留已确认来源nonce，不能当作已进入正文阶段', async () => {
  const input = phaseAdvanceInput()
  const store = new Store()
  const operation = createCorrectionOperation(
    ownerKey(owner),
    async () => input.task,
    store,
    () => undefined,
  )
  await operation.start(capsule())
  expect(canAdvanceCorrectionPhase({ ...input, operation: operation.state })).toBe(true)
  store.removeItem = () => {
    throw new Error('storage locked')
  }
  expect(operation.releaseKnown()).toBe(false)
  expect(operation.state.status).toBe('confirmed')
  expect(operation.state.pending?.phase).toBe('source')
  expect(store.data.size).toBe(1)
})
