import { describe, expect, it } from 'vitest'
import type { Task } from './api'
import {
  canPreviewRestart,
  canonical,
  emptyChapterMatches,
  OPEN_DETAILS_VERSION,
  RECIPIENT_SCOPE_VERSION,
  object,
  readPendingRestart,
  readRestartChild,
  readRestartLookup,
  readRestartPreview,
  RESTART_VERSION,
  SOURCE_ROLES_VERSION,
  sourceSignature,
  type PendingRestart,
  type RestartPreview,
} from './planRestart'

function fixture() {
  const plot = '原创夹具：甲按安排交工作清单，收件人细节尚未明确；备用灯尚未试亮。'
  const source: Task = {
    id: 'source-task',
    novel_id: 'novel',
    branch_id: 'branch',
    kind: 'workflow',
    status: 'paused',
    created: 1,
    cancel: 0,
    error: '',
    payload: {
      account: 'configured',
      mode: 'automatic',
      chapters: 36,
      plot_contract_check: true,
      budget_limit: 2,
      fixed: ['备用灯尚未试亮'],
      adoption: 'automatic',
    },
    result: {
      next_chapter: 4,
      expected_story_revision: 38,
      workflow_plan_preparation_version: 'workflow-plan-preparation/1',
      plot_contract_version: 'author-plot-audit/2',
      plan: { chapters: [{ number: 4, plot }] },
      plot_preparations: {
        '4': {
          chapter_id: 'empty-chapter',
          original_plot: plot,
          original_plot_sha256: 'a'.repeat(64),
          status: 'interpreting',
          round: 0,
        },
      },
    },
  }
  const preview: RestartPreview = {
    version: RESTART_VERSION,
    workflow_plan_open_details_version: OPEN_DETAILS_VERSION,
    task_id: source.id,
    branch_id: source.branch_id,
    chapter_id: 'empty-chapter',
    request_basis: { chapter: 4, base_story_revision: 38, base_progress_sha256: 'b'.repeat(64) },
    original_plot: plot,
    original_plot_sha256: 'a'.repeat(64),
    old_preparation_sha256: 'c'.repeat(64),
    known_invalid_steps: {
      fingerprints: ['1'.repeat(64), '2'.repeat(64), '3'.repeat(64)],
      call_ids: ['known-call-1', 'known-call-2', 'known-call-3'],
    },
    budget: {
      provider: 'deepseek',
      source_run_limit: 2,
      source_task_settled: 0.5,
      remaining_run_limit: 1.5,
      unknown_reserve: 0,
      lineage_task_ids: [source.id],
    },
    policy: '原AI章纲与旧失败保持，只有新任务会开始新的写前核对。',
  }
  const pending: PendingRestart = {
    source,
    preview,
    request: {
      ...preview.request_basis,
      client_request_id: 'stable-test-nonce',
      reason: '按原工作规划补齐开放细节。',
    },
  }
  const child: Task = {
    ...source,
    id: 'new-child',
    status: 'queued',
    payload: {
      ...source.payload,
      budget_limit: 1.5,
      ...{ client_request_id: pending.request.client_request_id },
    },
    result: {
      ...source.result,
      continued_from: source.id,
      workflow_plan_open_details_version: OPEN_DETAILS_VERSION,
      plan_preparation_restart: {
        version: RESTART_VERSION,
        source_task_id: source.id,
        request: pending.request,
        previous_preparation_sha256: preview.old_preparation_sha256,
        known_invalid_steps: preview.known_invalid_steps,
        budget: preview.budget,
      },
    },
  }
  return { source, preview, pending, child }
}

describe('写前重核依据与原请求绑定', () => {
  it('允许已同步且无错误文案的paused旧失败，只由服务端判定可重做', () => {
    const { source, preview } = fixture()
    expect(canPreviewRestart(source)).toBe(true)
    expect(source.error).toBe('')
    expect(readRestartPreview(preview, source)).toEqual(preview)
  })
  it.each([
    'version',
    'task',
    'branch',
    'chapter',
    'plot',
    'plot-hash',
    'progress-hash',
    'extra-basis',
    'unknown-budget',
    'duplicate-call',
    'bad-fingerprint',
  ])('拒绝未对应原目标/来源的preview：%s', (change) => {
    const { source, preview } = fixture(),
      value = structuredClone(preview)
    if (change === 'version') Object.assign(value, { version: 'future' })
    if (change === 'task') value.task_id = 'other'
    if (change === 'branch') value.branch_id = 'other'
    if (change === 'chapter') value.chapter_id = 'other'
    if (change === 'plot') value.original_plot += '改了结果'
    if (change === 'plot-hash') value.original_plot_sha256 = 'd'.repeat(64)
    if (change === 'progress-hash') value.request_basis.base_progress_sha256 = 'bad'
    if (change === 'extra-basis') Object.assign(value.request_basis, { budget_limit: 99 })
    if (change === 'unknown-budget') Object.assign(value.budget, { unknown_reserve: 1 })
    if (change === 'duplicate-call')
      value.known_invalid_steps.call_ids[1] = value.known_invalid_steps.call_ids[0]
    if (change === 'bad-fingerprint') value.known_invalid_steps.fingerprints[0] = 'bad'
    expect(readRestartPreview(value, source)).toBeUndefined()
  })
  it('只接受原目标严格空修订0，不能把空白稿或其他章节当来源', () => {
    const { preview } = fixture(),
      row = { id: preview.chapter_id, branch_id: preview.branch_id, position: 4, revision: 0, content: '' }
    expect(emptyChapterMatches(row, preview)).toBe(true)
    for (const patch of [
      { revision: 1 },
      { content: ' ' },
      { branch_id: 'other' },
      { position: 5 },
      { archived: 1 },
    ])
      expect(emptyChapterMatches({ ...row, ...patch }, preview)).toBe(false)
  })
  it.each(['assisted', 'new-policy', 'writing', 'waiting', 'maintenance'])(
    '不允许不适用的源任务开启新入口：%s',
    (change) => {
      const { source } = fixture()
      if (change === 'assisted') source.payload.mode = 'assisted'
      if (change === 'new-policy') source.result.workflow_plan_open_details_version = OPEN_DETAILS_VERSION
      if (change === 'writing') source.status = 'running'
      if (change === 'waiting') source.result.waiting_artifact = 'pending'
      if (change === 'maintenance') source.result.pending_structure = { chapter_id: 'chapter3', revision: 1 }
      expect(canPreviewRestart(source)).toBe(false)
    },
  )
  it('不自行补齐server basis或重新计算预算，null仍表示共享上限', () => {
    const { source, preview } = fixture()
    preview.budget.source_run_limit = null
    preview.budget.remaining_run_limit = null
    expect(readRestartPreview(preview, source)?.request_basis).toEqual(preview.request_basis)
  })
})

describe('写前重核创建回执与未知结果', () => {
  it('确认新任务时逐项保持account/payload/原规划/请求/旧步骤与预算', () => {
    const { child, pending } = fixture()
    expect(readRestartChild(child, pending)).toEqual(child)
  })
  it.each([
    'parent',
    'branch',
    'account',
    'budget',
    'nonce',
    'reason',
    'basis',
    'plan',
    'old-phase',
    'old-calls',
    'payload-extra',
    'payload-missing',
  ])('拒绝有合法外形但不同意图的返回任务：%s', (change) => {
    const { child, pending } = fixture(),
      value = structuredClone(child)
    const receipt = value.result.plan_preparation_restart as Record<string, unknown>
    const request = receipt.request as Record<string, unknown>
    if (change === 'parent') value.result.continued_from = 'other'
    if (change === 'branch') value.branch_id = 'other'
    if (change === 'account') value.payload.account = 'other'
    if (change === 'budget') value.payload.budget_limit = 99
    if (change === 'nonce') request.client_request_id = 'different-nonce'
    if (change === 'reason') request.reason = 'changed'
    if (change === 'basis') request.base_story_revision = 39
    if (change === 'plan') value.result.plan = { chapters: [] }
    if (change === 'old-phase') receipt.previous_preparation_sha256 = 'd'.repeat(64)
    if (change === 'old-calls') receipt.known_invalid_steps = { fingerprints: [], call_ids: [] }
    if (change === 'payload-extra') Object.assign(value.payload, { unexpected: true })
    if (change === 'payload-missing') delete value.payload.fixed
    expect(readRestartChild(value, pending)).toBeUndefined()
  })
  it('null查询只表示尚未读到；异目标/异nonce不能当不存在', () => {
    const { child, pending } = fixture()
    const row = {
      source_task_id: pending.source.id,
      client_request_id: pending.request.client_request_id,
      task: null,
    }
    expect(readRestartLookup(row, pending)).toBeNull()
    expect(readRestartLookup({ ...row, task: child }, pending)).toEqual(child)
    expect(readRestartLookup({ ...row, source_task_id: 'other' }, pending)).toBeUndefined()
    expect(readRestartLookup({ ...row, client_request_id: 'other' }, pending)).toBeUndefined()
  })
  it('刷新后只能恢复同target的原nonce请求，不混入另一批次或新basis', () => {
    const { pending } = fixture(),
      owner = canonical([pending.source.novel_id, pending.source.branch_id, pending.source.id])
    expect(readPendingRestart(pending, owner)).toEqual(pending)
    expect(readPendingRestart(pending, canonical(['novel', 'branch', 'other']))).toBeUndefined()
    expect(
      readPendingRestart({ ...pending, request: { ...pending.request, base_story_revision: 40 } }, owner),
    ).toBeUndefined()
  })
  it('相同JSON键顺序变化不制造新来源，真实进度变化必须形成新依据', () => {
    const { source } = fixture(),
      copy = structuredClone(source)
    copy.payload = Object.fromEntries(Object.entries(copy.payload).reverse()) as Task['payload']
    expect(sourceSignature(copy)).toBe(sourceSignature(source))
    copy.result.expected_story_revision = 39
    expect(sourceSignature(copy)).not.toBe(sourceSignature(source))
  })
})

function openFixture(tagged = false) {
  const result = fixture(),
    { source, preview, pending, child } = result
  source.status = 'blocked_runtime'
  source.result.workflow_plan_open_details_version = OPEN_DETAILS_VERSION
  source.result.flow_source_entailment_version = 'source-flow-entailment/1'
  source.result.conditional_book_policy_version = 'conditional-book-check/1'
  source.result.plan = {
    chapters: Array.from({ length: 36 }, (_, index) => ({
      number: index + 1,
      plot: index === 3 ? preview.original_plot : `原创第${index + 1}章安排`,
    })),
  }
  const row = object(object(source.result.plot_preparations)?.['4'])
  if (!row) throw new Error('缺少初次解释夹具')
  row.phases = { 'interpret:0': { started: true, action: '严格核对AI工作章纲' } }
  if (tagged) {
    source.payload.workflow_plan_source_roles_version = SOURCE_ROLES_VERSION
    source.result.workflow_plan_source_roles_version = SOURCE_ROLES_VERSION
    row.workflow_plan_source_roles_version = SOURCE_ROLES_VERSION
    source.result.plan_preparation_restart = {
      version: RESTART_VERSION,
      workflow_plan_source_roles_version: SOURCE_ROLES_VERSION,
      request: { workflow_plan_source_roles_version: SOURCE_ROLES_VERSION },
    }
  }
  preview.workflow_plan_source_roles_version = SOURCE_ROLES_VERSION
  preview.request_basis.workflow_plan_source_roles_version = SOURCE_ROLES_VERSION
  pending.request = { ...pending.request, workflow_plan_source_roles_version: SOURCE_ROLES_VERSION }
  child.payload = {
    ...source.payload,
    ...{ client_request_id: pending.request.client_request_id },
    budget_limit: preview.budget.remaining_run_limit,
    workflow_plan_source_roles_version: SOURCE_ROLES_VERSION,
  }
  child.result = {
    ...source.result,
    continued_from: source.id,
    plot_preparations: {},
    workflow_plan_source_roles_version: SOURCE_ROLES_VERSION,
    plan_preparation_restart: {
      ...(child.result.plan_preparation_restart as Record<string, unknown>),
      workflow_plan_source_roles_version: SOURCE_ROLES_VERSION,
      request: pending.request,
      previous_preparation: structuredClone(row),
    },
  }
  return result
}

function scopeFixture() {
  const fixture = openFixture(true),
    row = preparationRow(fixture.source)
  row.workflow_plan_recipient_scope_version = RECIPIENT_SCOPE_VERSION
  row.effective_plot_sha256 = 'd'.repeat(64)
  const response = {
    version: RECIPIENT_SCOPE_VERSION,
    status: 'supported',
    source_sha256: row.effective_plot_sha256,
    input_sha256: 'f'.repeat(64),
    inventory: {
      input_sha256: 'f'.repeat(64),
      observations: [{ kind: 'named_destination' }],
      unit_checks: [{ status: 'covered' }],
    },
  }
  const phases = row.phases as Record<string, unknown>
  phases['recipient_scope:0'] = {
    status: 'completed',
    owner_task_id: fixture.source.id,
    attempt: 1,
    response_sha256: 'e'.repeat(64),
    response,
  }
  return { ...fixture, row, phases, response }
}

function preparationRow(task: Task) {
  const row = object(object(task.result.plot_preparations)?.['4'])
  if (!row) throw new Error('缺少准备行夹具')
  return row
}

describe('先行接收范围来源观察后的重新准备预览', () => {
  it('新行已完成来源观察、解释仍失败时可读取正式依据，原nonce保持', () => {
    const { source, preview, pending } = scopeFixture()
    expect(canPreviewRestart(source)).toBe(true)
    expect(readRestartPreview(preview, source)).toEqual(preview)
    const owner = canonical([source.novel_id, source.branch_id, source.id])
    expect(readPendingRestart(structuredClone(pending), owner)).toEqual(pending)
    expect(pending.request.client_request_id).toBe('stable-test-nonce')
    expect(preview.known_invalid_steps.call_ids).toHaveLength(3)
  })

  it.each([
    'missing-marker',
    'future-marker',
    'missing-inventory',
    'extra-phase',
    'unfinished',
    'bad-hash',
    'missing-owner',
    'unknown-receipt',
    'wrong-version',
    'wrong-source',
    'wrong-token',
    'unknown-observation',
    'unknown-unit',
    'empty-coverage',
    'interpret-response',
    'legacy-downgrade',
  ])('不放开未决、未知或不一致的预览：%s', (change) => {
    const { source, row, phases, response } = scopeFixture()
    const slot = phases['recipient_scope:0'] as Record<string, unknown>
    if (change === 'missing-marker') delete row.workflow_plan_recipient_scope_version
    if (change === 'future-marker') row.workflow_plan_recipient_scope_version = 'future'
    if (change === 'missing-inventory') delete phases['recipient_scope:0']
    if (change === 'extra-phase') phases['choose:1'] = { status: 'started' }
    if (change === 'unfinished') slot.status = 'started'
    if (change === 'bad-hash') slot.response_sha256 = 'bad'
    if (change === 'missing-owner') delete slot.owner_task_id
    if (change === 'unknown-receipt') response.status = 'needs_review'
    if (change === 'wrong-version') response.version = 'future'
    if (change === 'wrong-source') response.source_sha256 = 'b'.repeat(64)
    if (change === 'wrong-token') response.inventory.input_sha256 = 'a'.repeat(64)
    if (change === 'unknown-observation') response.inventory.observations[0].kind = 'unknown'
    if (change === 'unknown-unit') response.inventory.unit_checks[0].status = 'unknown'
    if (change === 'empty-coverage') response.inventory.unit_checks = []
    if (change === 'interpret-response') (phases['interpret:0'] as Record<string, unknown>).response = {}
    if (change === 'legacy-downgrade') {
      delete row.workflow_plan_recipient_scope_version
      delete row.workflow_plan_source_roles_version
      delete source.payload.workflow_plan_source_roles_version
      delete source.result.workflow_plan_source_roles_version
      delete source.result.workflow_plan_open_details_version
      delete source.result.plan_preparation_restart
    }
    expect(canPreviewRestart(source)).toBe(false)
  })
})

describe('当前开放章纲初次解释的显式新版重核', () => {
  it.each([false, true])('仅初次解释时允许读取server依据，来源已带同版marker=%s', (tagged) => {
    const { source, preview, pending, child } = openFixture(tagged)
    expect(canPreviewRestart(source)).toBe(true)
    expect(readRestartPreview(preview, source)).toEqual(preview)
    expect(readRestartChild(child, pending)).toEqual(child)
    expect((child.result.plan as { chapters: unknown[] }).chapters).toHaveLength(36)
    expect(child.payload.budget_limit).toBe(1.5)
  })
  it.each([
    'missing-phase',
    'response',
    'choice',
    'preservation',
    'round',
    'status',
    'unknown-task',
    'mismatched-payload',
    'missing-row-marker',
    'unknown-row',
    'missing-receipt-marker',
    'unknown-receipt-request',
    'whole-marker-downgrade',
  ])('不放开已有后续步骤或不一致冻结：%s', (change) => {
    const { source } = openFixture(true)
    const row = object(object(source.result.plot_preparations)?.['4'])
    if (!row) throw new Error('缺少初次解释夹具')
    const phases = row.phases as Record<string, Record<string, unknown>>
    if (change === 'missing-phase') delete row.phases
    if (change === 'response') phases['interpret:0'].response = { items: [] }
    if (change === 'choice') phases['choose:1'] = { started: true }
    if (change === 'preservation') phases['preserve:1'] = { started: true }
    if (change === 'round') row.round = 1
    if (change === 'status') row.status = 'choosing'
    if (change === 'unknown-task') source.result.workflow_plan_source_roles_version = 'future'
    if (change === 'mismatched-payload') delete source.payload.workflow_plan_source_roles_version
    if (change === 'missing-row-marker') delete row.workflow_plan_source_roles_version
    if (change === 'unknown-row') row.workflow_plan_source_roles_version = 'future'
    const receipt = source.result.plan_preparation_restart as Record<string, unknown>
    if (change === 'missing-receipt-marker') delete receipt.workflow_plan_source_roles_version
    if (change === 'unknown-receipt-request')
      (receipt.request as Record<string, unknown>).workflow_plan_source_roles_version = 'future'
    if (change === 'whole-marker-downgrade') {
      delete source.payload.workflow_plan_source_roles_version
      delete source.result.workflow_plan_source_roles_version
      delete row.workflow_plan_source_roles_version
    }
    expect(canPreviewRestart(source)).toBe(false)
  })
  it.each(['top-missing', 'basis-missing', 'future', 'null', 'legacy-source', 'extra-basis'])(
    '新preview必须上下marker与来源类型一致：%s',
    (change) => {
      const { source, preview } = openFixture(),
        value = structuredClone(preview)
      if (change === 'top-missing') delete value.workflow_plan_source_roles_version
      if (change === 'basis-missing') delete value.request_basis.workflow_plan_source_roles_version
      if (change === 'future') Object.assign(value, { workflow_plan_source_roles_version: 'future' })
      if (change === 'null') value.request_basis.workflow_plan_source_roles_version = null
      if (change === 'legacy-source') delete source.result.workflow_plan_open_details_version
      if (change === 'extra-basis') Object.assign(value.request_basis, { account: 'other' })
      expect(readRestartPreview(value, source)).toBeUndefined()
    },
  )
  it.each(['payload', 'result', 'receipt', 'request', 'preview', 'flow', 'condition'])(
    '新child不能丢失所选marker或原政策：%s',
    (change) => {
      const { pending, child } = openFixture(),
        value = structuredClone(child),
        receipt = value.result.plan_preparation_restart as Record<string, unknown>
      if (change === 'payload') delete value.payload.workflow_plan_source_roles_version
      if (change === 'result') delete value.result.workflow_plan_source_roles_version
      if (change === 'receipt') delete receipt.workflow_plan_source_roles_version
      if (change === 'request')
        (receipt.request as Record<string, unknown>).workflow_plan_source_roles_version = 'future'
      if (change === 'preview') delete pending.preview.workflow_plan_source_roles_version
      if (change === 'flow') delete value.result.flow_source_entailment_version
      if (change === 'condition') value.result.conditional_book_policy_version = 'future'
      expect(readRestartChild(value, pending)).toBeUndefined()
    },
  )
  it('legacy回执不能偷偷带新marker，原pending仍按原nonce读取', () => {
    const { pending, child } = fixture(),
      changed = structuredClone(child)
    changed.payload.workflow_plan_source_roles_version = SOURCE_ROLES_VERSION
    changed.result.workflow_plan_source_roles_version = SOURCE_ROLES_VERSION
    expect(readRestartChild(changed, pending)).toBeUndefined()
    const owner = canonical([pending.source.novel_id, pending.source.branch_id, pending.source.id])
    expect(readPendingRestart(pending, owner)).toEqual(pending)
    expect(readPendingRestart(pending, owner)?.request).not.toHaveProperty(
      'workflow_plan_source_roles_version',
    )
  })
  it('新版原提交刷新保留marker与nonce，404/null只表示尚未查到', () => {
    const { pending, child } = openFixture(),
      owner = canonical([pending.source.novel_id, pending.source.branch_id, pending.source.id]),
      stored = JSON.parse(JSON.stringify(pending))
    expect(readPendingRestart(stored, owner)).toEqual(pending)
    const lookup = {
      source_task_id: pending.source.id,
      client_request_id: pending.request.client_request_id,
      task: null,
    }
    expect(readRestartLookup(lookup, pending)).toBeNull()
    expect(readRestartLookup({ ...lookup, task: child }, pending)).toEqual(child)
    for (const patch of [
      { workflow_plan_source_roles_version: null },
      { workflow_plan_source_roles_version: 'future' },
      { account: 'other' },
    ])
      expect(
        readPendingRestart({ ...pending, request: { ...pending.request, ...patch } }, owner),
      ).toBeUndefined()
  })
})
