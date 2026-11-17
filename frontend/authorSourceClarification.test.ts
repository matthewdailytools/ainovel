import { describe, expect, it } from 'vitest'
import { clarificationFixture } from '../fixtures/author-source-clarification'
import { clarificationFormatFailureFixture } from '../fixtures/author-source-clarification-format-failure'
import { canonical, object } from './planRestart'
import {
  clarificationUnavailableReason,
  readClarificationPreview,
  readPendingClarification,
  readClarificationChild,
  readClarificationLookup,
} from './authorSourceClarification'

describe('作者来源补充边界', () => {
  it('新版来源的真实未知保持停点可显式补充，沿用对应政策而不重写旧版', () => {
    const { source, preview, request, child, record } = clarificationFixture()
    object(source.result)!.authored_source_scope_version = 'authored-source-scope/2'
    preview.policies.authored_source_scope_version = 'authored-source-scope/2'
    Object.assign(record.phases, {
      preserve: { status: 'completed', owner_task_id: source.id, response: { result: 'unknown' } },
    })
    expect(readClarificationPreview(preview, source)).toEqual(preview)
    expect(readClarificationChild(child(), { source, preview, request })).toBeDefined()
    object(record.phases)!.preserve = { status: 'started', owner_task_id: source.id }
    expect(readClarificationPreview(preview, source)).toBeUndefined()
  })
  it('旧版不会因新增保持阶段自动升级为可补充停点', () => {
    const { source, preview, record } = clarificationFixture()
    Object.assign(record.phases, { preserve: { status: 'completed', owner_task_id: source.id } })
    expect(readClarificationPreview(preview, source)).toBeUndefined()
  })
  it('已完整返回的首次语义unknown也可读取依据，不要求生成解释或补姓名', () => {
    const { source, preview, record } = clarificationFixture()
    const raw = object(record)!,
      phases = object(raw.phases)!
    delete phases.interpret
    delete raw.bindings
    raw.contract = { items: [], note: '限制范围归属未知，原话保留。' }
    object(phases.observe)!.response = { status: 'needs_review' }
    preview.source_review = {
      state: 'unverified',
      contract: raw.contract,
      bindings: null,
      inventory: { status: 'needs_review' },
    }
    expect(readClarificationPreview(preview, source)).toEqual(preview)
  })
  it('子任务已正常推进仍按冻结创建回执查证，不把动态章位或费用当作新创建请求', () => {
    const { source, preview, request, child } = clarificationFixture(),
      value = child()
    value.status = 'needs_review'
    value.result.next_chapter = 3
    value.result.expected_story_revision = 15
    value.result.repair_round = 2
    expect(
      readClarificationLookup(
        { source_task_id: source.id, client_request_id: request.client_request_id, task: value },
        { source, preview, request },
      ),
    ).toEqual(value)
  })
  it('同源原话、完整待核记录与费用可以预览，原nonce可恢复', () => {
    const { source, preview, request, child } = clarificationFixture()
    const pending = { source, preview, request },
      owner = canonical([source.novel_id, source.branch_id, source.id])
    expect(clarificationUnavailableReason(source)).toBe('')
    expect(readClarificationPreview(preview, source)).toEqual(preview)
    expect(readPendingClarification(structuredClone(pending), owner)).toEqual(pending)
    expect(readClarificationChild(child(), pending)).toEqual(child())
    const lookup = { source_task_id: source.id, client_request_id: request.client_request_id, task: null }
    expect(readClarificationLookup(lookup, pending)).toBeNull()
    expect(readClarificationLookup({ ...lookup, task: child() }, pending)).toEqual(child())
  })
  it.each([
    'old-policy',
    'unknown-policy',
    'active',
    'candidate',
    'continued',
    'ready-source',
    'unfinished-observe',
  ])('旧任务、活动或未知停点不开放提交：%s', (change) => {
    const { source, preview, record } = clarificationFixture(),
      result = object(source.result)!
    if (change === 'old-policy') delete result.authored_source_scope_version
    if (change === 'unknown-policy') result.authored_source_scope_version = 'future'
    if (change === 'active') source.status = 'running'
    if (change === 'candidate') source.result.waiting_artifact = 'candidate'
    if (change === 'continued') source.result.continued_to = 'already-created'
    if (change === 'ready-source') record.state = 'ready'
    if (change === 'unfinished-observe') record.phases.observe.status = 'started'
    expect(readClarificationPreview(preview, source)).toBeUndefined()
  })
  it.each([
    'branch',
    'original-text',
    'source-review',
    'plan',
    'policies',
    'empty-revision',
    'hash',
    'adoption',
    'unknown-cost',
    'literary',
  ])('预览不混用目标、原话、报告或费用：%s', (change) => {
    const { source, preview } = clarificationFixture(),
      value = structuredClone(preview)
    if (change === 'branch') value.branch_id = 'other'
    if (change === 'original-text') value.original_source.text = '其他原话'
    if (change === 'source-review') value.source_review.contract = {}
    if (change === 'plan') value.plan = {}
    if (change === 'policies') value.policies = {}
    if (change === 'empty-revision') Object.assign(value.baseline, { chapter_revision: 1 })
    if (change === 'hash') value.request_basis.preview_basis_sha256 = 'bad'
    if (change === 'adoption') value.adoption = 'automatic'
    if (change === 'unknown-cost') Object.assign(value.budget, { unknown_reserve: 1 })
    if (change === 'literary') Object.assign(value, { author_literary_approval: 'pass' })
    expect(readClarificationPreview(value, source)).toBeUndefined()
  })
  it.each(['extra', 'actor', 'nonce', 'basis', 'empty', 'long', 'long-reason'])(
    '原提交冻结精确六字段，非本作者提交或不合法输入不能恢复：%s',
    (change) => {
      const { source, preview, request } = clarificationFixture(),
        pending = { source, preview, request }
      if (change === 'extra') Object.assign(request, { account: 'other' })
      if (change === 'actor') request.actor = 'authorized_operator'
      if (change === 'nonce') request.client_request_id = 'bad'
      if (change === 'basis') request.preview_basis_sha256 = 'b'.repeat(64)
      if (change === 'empty') request.clarification = ' '
      if (change === 'long') request.clarification = '𠮷'.repeat(8001)
      if (change === 'long-reason') request.reason = '字'.repeat(2001)
      expect(
        readPendingClarification(pending, canonical([source.novel_id, source.branch_id, source.id])),
      ).toBeUndefined()
    },
  )
  it.each([
    'payload',
    'account',
    'round',
    'source',
    'request',
    'budget',
    'plan',
    'combined',
    'policy',
    'account-config',
  ])('子任务不替换原情节、不变更账户或回执来源：%s', (change) => {
    const { source, preview, request, child } = clarificationFixture(),
      value = child(),
      result = object(value.result)!,
      receipt = object(result.author_source_clarification)!
    if (change === 'payload') Object.assign(value.payload, { plots: [] })
    if (change === 'account') value.payload.account = 'openai_2'
    if (change === 'round') result.repair_round = 0
    if (change === 'source') receipt.source_task_id = 'other'
    if (change === 'request') receipt.request = { ...request, clarification: '不同补充' }
    if (change === 'budget') value.payload.budget_limit = 2
    if (change === 'plan') result.plan = {}
    if (change === 'combined') object(receipt.source_entry)!.text = '替换原话'
    if (change === 'policy') result.authored_source_scope_version = 'future'
    if (change === 'account-config') receipt.account = { ...preview.account, model: 'changed-model' }
    expect(readClarificationChild(value, { source, preview, request })).toBeUndefined()
  })
})

describe('三份已知完整格式失败的显式澄清边界', () => {
  it('只读预览保留pending现场，author补充及原nonce可查证，不制造有效观察', () => {
    const { source, preview, request, child, record } = clarificationFormatFailureFixture(),
      pending = { source, preview, request },
      owner = canonical([source.novel_id, source.branch_id, source.id]),
      before = structuredClone(record)
    expect(clarificationUnavailableReason(source)).toBe('')
    expect(readClarificationPreview(preview, source)).toEqual(preview)
    expect(preview.source_review).toMatchObject({
      state: 'pending',
      contract: null,
      bindings: null,
      inventory: null,
      observation_failure: { kind: 'known_returned_invalid_observation' },
    })
    expect(preview.source_review.observation_failure?.raw_returns).toHaveLength(3)
    expect(readPendingClarification(structuredClone(pending), owner)).toEqual(pending)
    expect(readClarificationChild(child(), pending)).toEqual(child())
    expect(
      readClarificationLookup(
        { source_task_id: source.id, client_request_id: request.client_request_id, task: child() },
        pending,
      ),
    ).toEqual(child())
    expect(record).toEqual(before)
    expect(record.phases.observe.status).toBe('started')
    expect(record.phases.observe).not.toHaveProperty('response')
    expect(record.phases.observe).not.toHaveProperty('response_sha256')
  })

  it.each([
    'missing-proof',
    'duplicate-call',
    'unsettled-call',
    'incomplete-call',
    'missing-dispatch',
    'zero-dispatch',
    'duplicate-dispatch',
    'missing-stop',
    'stop-before-last-dispatch',
    'wrong-attempt',
    'wrong-owner',
    'wrong-errors',
    'missing-raw',
    'empty-raw',
    'unbound-raw',
    'fake-inventory',
    'fake-unverified',
  ])('不把缺失、重复、未决或伪造现场当作可提交预览：%s', (change) => {
    const { source, preview } = clarificationFormatFailureFixture(),
      value = structuredClone(preview),
      failure = object(value.source_review.observation_failure)!,
      proof = object(failure.proof)!,
      steps = (proof.steps as unknown[]).map((step) => object(step)!),
      calls = (proof.calls as unknown[]).map((call) => object(call)!),
      raw = failure.raw_returns as Record<string, unknown>[]
    if (change === 'missing-proof') delete failure.proof
    if (change === 'duplicate-call') proof.call_ids = [steps[0].call_id, steps[0].call_id, steps[2].call_id]
    if (change === 'unsettled-call') Object.assign(calls[0], { status: 'unknown', charged: null })
    if (change === 'incomplete-call') calls[0].has_complete_step = false
    if (change === 'missing-dispatch') delete steps[0].dispatch_event_id
    if (change === 'zero-dispatch') steps[0].dispatch_event_id = 0
    if (change === 'duplicate-dispatch') steps[1].dispatch_event_id = steps[0].dispatch_event_id
    if (change === 'missing-stop') delete proof.stopped_event_id
    if (change === 'stop-before-last-dispatch') proof.stopped_event_id = steps[2].dispatch_event_id
    if (change === 'wrong-attempt') proof.attempt = 'other-attempt'
    if (change === 'wrong-owner') proof.owner_task_id = 'other-task'
    if (change === 'wrong-errors') proof.errors = [['另一次工程错误'], ['原错误'], ['原错误']]
    if (change === 'missing-raw') failure.raw_returns = raw.slice(0, 2)
    if (change === 'empty-raw') raw[0].text = ' '
    if (change === 'unbound-raw') raw[0].fingerprint = 'another-step'
    if (change === 'fake-inventory') value.source_review.inventory = { status: 'supported' }
    if (change === 'fake-unverified') value.source_review.state = 'unverified'
    expect(readClarificationPreview(value, source)).toBeUndefined()
  })

  it.each(['old-execution', 'fake-completed', 'prior-clarification'])(
    '只允许当前未写正文的首次格式失败现场读取：%s',
    (change) => {
      const { source, preview, record } = clarificationFormatFailureFixture()
      if (change === 'old-execution')
        object(source.result)!.authored_source_observation_execution_version =
          'authored-source-observation-execution/1'
      if (change === 'fake-completed') record.phases.observe.status = 'completed'
      if (change === 'prior-clarification')
        object(source.result)!.author_source_clarification = { chapter: source.result.next_chapter }
      expect(clarificationUnavailableReason(source)).not.toBe('')
      expect(readClarificationPreview(preview, source)).toBeUndefined()
    },
  )

  it('子任务须保留原格式失败证明，不能用新回执替换旧started现场', () => {
    const { source, preview, request, child } = clarificationFormatFailureFixture(),
      value = child(),
      receipt = object(object(value.result)!.author_source_clarification)!
    delete receipt.observation_failure
    expect(readClarificationChild(value, { source, preview, request })).toBeUndefined()
  })
})
