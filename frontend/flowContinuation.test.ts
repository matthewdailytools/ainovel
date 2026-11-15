import { describe, expect, it } from 'vitest'
import type { Task } from './api'
import {
  canonical,
  FLOW_CONTINUATION_VERSION,
  FLOW_VERSION,
  FLOW_LEGACY_VERSION,
  flowUnavailableReason,
  readFlowBudget,
  readFlowPreview,
  readFlowContext,
  readFlowChild,
  readPendingFlow,
  type FlowPreview,
  type PendingFlow,
} from './flowContinuation'

function fixture() {
  const source: Task = {
    id: 'parent',
    novel_id: 'novel',
    branch_id: 'branch',
    kind: 'workflow',
    status: 'paused',
    created: 1,
    cancel: 0,
    error: '',
    payload: {
      account: 'ds',
      mode: 'automatic',
      chapters: 6,
      budget_limit: 5,
      plot_contract_check: true,
    },
    result: {
      next_chapter: 3,
      expected_story_revision: 12,
      plot_contract_version: 'author-plot-audit/2',
      conditional_book_policy_version: 'conditional-book-check/1',
      plan: {
        chapters: [1, 2, 3, 4, 5, 6].map((number) => ({
          number,
          plot: '林舟在山城核对修理账页。',
        })),
      },
    },
  }
  const foreign = {
    id: 'usd-old',
    task_id: 'ancestor',
    account: 'oa',
    provider: 'openai',
    currency: 'USD',
    reserved: 0.3,
    charged: null,
    status: 'unknown',
    has_complete_step: false,
    scope: 'workflow',
  }
  const preview: FlowPreview = {
    version: FLOW_CONTINUATION_VERSION,
    flow_source_entailment_version: FLOW_VERSION,
    source_task_id: source.id,
    branch_id: source.branch_id,
    story_revision: 12,
    source_flow_policy: 'legacy',
    source_policies: {
      flow_source_entailment_version: null,
      plot_contract_version: 'author-plot-audit/2',
      conditional_book_policy_version: 'conditional-book-check/1',
    },
    next_chapter: 3,
    plan_sha256: 'a'.repeat(64),
    preview_basis_sha256: 'b'.repeat(64),
    account: 'ds',
    notice: '新子任务后续使用新政策，旧记录保持。',
    budget: {
      provider: 'deepseek',
      source_run_limit: 5,
      source_task_settled: 1,
      remaining_run_limit: 4,
      effective_remaining_run_limit: 3.5,
      auxiliary_settled: 0.5,
      unknown_reserve: 0,
      foreign_history_calls: [foreign],
      foreign_unknown_calls: [foreign],
    },
  }
  const config = {
    accounts: [
      {
        id: 'ds',
        kind: 'deepseek',
        model: 'offline-mock',
        currency: 'CNY',
        configured: true,
        priced: true,
        input_rate: '2',
        output_rate: '8',
        price_source: 'toy',
      },
    ],
    budget: [
      {
        provider: 'deepseek',
        currency: 'CNY',
        limit: '80',
        settled: 3,
        reserved_unknown: 0,
        remaining: 77,
        calls: [],
      },
    ],
  }
  const context = readFlowContext(preview, config)!
  const pending: PendingFlow = {
    source,
    context,
    request: {
      client_request_id: 'nonce-12345678',
      preview_basis_sha256: preview.preview_basis_sha256,
      flow_source_entailment_version: FLOW_VERSION,
      reason: '  为后续保留问句与答案的独立范围。  ',
    },
  }
  const child: Task = {
    ...structuredClone(source),
    id: 'child',
    status: 'queued',
    payload: {
      ...source.payload,
      client_request_id: pending.request.client_request_id,
      budget_limit: 4,
    } as Task['payload'],
    result: {
      ...structuredClone(source.result),
      continued_from: source.id,
      flow_source_entailment_version: FLOW_VERSION,
      flow_policy_continuation: {
        version: FLOW_CONTINUATION_VERSION,
        source_task_id: source.id,
        source_flow_policy: 'legacy',
        source_payload_sha256: 'c'.repeat(64),
        source_progress_sha256: 'd'.repeat(64),
        plan_sha256: preview.plan_sha256,
        next_chapter: 3,
        request: structuredClone(pending.request),
        budget: structuredClone(preview.budget),
      },
    } as Task['result'],
  }
  return { source, preview, pending, child, config }
}
describe('FLOW explicit continuation protocol', () => {
  it('permits an explicit current-policy child from a frozen v1 checkpoint', () => {
    const f = fixture()
    f.source.result.flow_source_entailment_version = FLOW_LEGACY_VERSION
    f.preview.source_flow_policy = FLOW_LEGACY_VERSION
    f.preview.source_policies.flow_source_entailment_version = FLOW_LEGACY_VERSION
    const receipt = (f.child.result as Record<string, unknown>).flow_policy_continuation as Record<
      string,
      unknown
    >
    receipt.source_flow_policy = FLOW_LEGACY_VERSION
    expect(flowUnavailableReason(f.source)).toBe('')
    expect(readFlowPreview(f.preview, f.source)).toEqual(f.preview)
    expect(readPendingFlow(f.pending, canonical(['novel', 'branch', 'parent']))).toEqual(f.pending)
    expect(readFlowChild(f.child, f.pending)).toEqual(f.child)
    expect(f.source.result.flow_source_entailment_version).toBe(FLOW_LEGACY_VERSION)
  })
  it('restores an unresolved v1 nonce for receipt lookup without turning it into a fresh v2 decision', () => {
    const f = fixture()
    f.preview.flow_source_entailment_version = FLOW_LEGACY_VERSION
    f.pending.request.flow_source_entailment_version = FLOW_LEGACY_VERSION
    f.child.result.flow_source_entailment_version = FLOW_LEGACY_VERSION
    const receipt = (f.child.result as Record<string, unknown>).flow_policy_continuation as Record<
      string,
      unknown
    >
    receipt.request = structuredClone(f.pending.request)
    expect(readFlowPreview(f.preview, f.source)).toBeUndefined()
    expect(readPendingFlow(f.pending, canonical(['novel', 'branch', 'parent']))).toEqual(f.pending)
    expect(readFlowChild(f.child, f.pending)).toEqual(f.child)
    f.child.result.flow_source_entailment_version = FLOW_VERSION
    expect(readFlowChild(f.child, f.pending)).toBeUndefined()
  })
  it('rejects a preview that silently drops the independently frozen conditional policy', () => {
    const f = fixture()
    delete f.preview.source_policies.conditional_book_policy_version
    expect(readFlowPreview(f.preview, f.source)).toBeUndefined()
  })
  it('rejects a child that changes an existing policy while adding FLOW', () => {
    const f = fixture()
    f.child.result.conditional_book_policy_version = 'unexpected-policy'
    expect(readFlowChild(f.child, f.pending)).toBeUndefined()
  })
  it('reads exact server basis and keeps foreign unknown audit without spending it again', () => {
    const f = fixture()
    expect(readFlowPreview(f.preview, f.source)).toEqual(f.preview)
    expect(readFlowBudget(f.preview.budget)?.effective_remaining_run_limit).toBe(3.5)
    expect(f.preview.budget.foreign_unknown_calls[0].reserved).toBe(0.3)
    expect(flowUnavailableReason(f.source)).toBe('')
  })
  it.each(['needs_review', 'running', 'blocked_runtime', 'cancelled'])('blocks preview for %s', (status) => {
    const f = fixture()
    f.source.status = status
    expect(flowUnavailableReason(f.source)).not.toBe('')
  })
  it.each([
    'waiting_artifact',
    'waiting_structure',
    'pending_structure',
    'pause_after_maintenance_requested',
    'continued_to',
  ])('blocks pending %s', (key) => {
    const f = fixture()
    Object.assign(f.source.result, { [key]: 'pending' })
    expect(flowUnavailableReason(f.source)).not.toBe('')
  })
  it('does not conflate conditional policy with FLOW', () => {
    const f = fixture()
    expect(flowUnavailableReason(f.source)).toBe('')
    f.source.result.flow_source_entailment_version = FLOW_VERSION
    expect(flowUnavailableReason(f.source)).toContain('已冻结')
  })
  it.each([
    'source_task_id',
    'branch_id',
    'account',
    'preview_basis_sha256',
    'flow_source_entailment_version',
  ])('rejects malformed preview %s', (key) => {
    const f = fixture()
    expect(readFlowPreview({ ...f.preview, [key]: 'wrong' }, f.source)).toBeUndefined()
  })
  it('rejects mismatched story revision or next chapter', () => {
    const f = fixture()
    expect(readFlowPreview({ ...f.preview, story_revision: 13 }, f.source)).toBeUndefined()
    expect(readFlowPreview({ ...f.preview, next_chapter: 4 }, f.source)).toBeUndefined()
  })
  it('rejects current-provider unknown, negative and repeated-limit shapes', () => {
    const f = fixture()
    expect(readFlowBudget({ ...f.preview.budget, unknown_reserve: 0.2 })).toBeUndefined()
    expect(
      readFlowBudget({
        ...f.preview.budget,
        effective_remaining_run_limit: 4.1,
      }),
    ).toBeUndefined()
    expect(
      readFlowBudget({
        ...f.preview.budget,
        foreign_unknown_calls: [
          {
            ...f.preview.budget.foreign_unknown_calls[0],
            provider: 'deepseek',
          },
        ],
      }),
    ).toBeUndefined()
  })
  it('reads current shared cap and model but cannot invent a changed account', () => {
    const f = fixture()
    expect(readFlowContext(f.preview, f.config)?.shared.remaining).toBe(77)
    f.config.accounts[0].id = 'other'
    expect(readFlowContext(f.preview, f.config)).toBeUndefined()
  })
  it('keeps nominal child limit4 rather than double subtracting auxiliary to3.5', () => {
    const f = fixture()
    expect(readFlowChild(f.child, f.pending)).toEqual(f.child)
    f.child.payload.budget_limit = 3.5
    expect(readFlowChild(f.child, f.pending)).toBeUndefined()
  })
  it('recognizes a valid original receipt after the child advances', () => {
    const f = fixture()
    f.child.status = 'running'
    f.child.result.next_chapter = 4
    expect(readFlowChild(f.child, f.pending)).toEqual(f.child)
  })
  it.each(['branch_id', 'novel_id', 'kind', 'id'])('rejects substituted child %s', (key) => {
    const f = fixture()
    expect(
      readFlowChild({ ...f.child, [key]: key === 'id' ? f.source.id : 'other' }, f.pending),
    ).toBeUndefined()
  })
  it('rejects changed original plan or nonce instead of treating malformed200 as success', () => {
    const f = fixture()
    f.child.result.plan = { chapters: [] }
    expect(readFlowChild(f.child, f.pending)).toBeUndefined()
    const g = fixture()
    g.pending.request.client_request_id = 'different-nonce'
    expect(readFlowChild(g.child, g.pending)).toBeUndefined()
  })
  it('restores only exact owner/source/request capsules and original whitespace', () => {
    const f = fixture(),
      owner = canonical(['novel', 'branch', 'parent'])
    expect(readPendingFlow(f.pending, owner)?.request.reason).toBe(f.pending.request.reason)
    expect(readPendingFlow(f.pending, canonical(['novel', 'branch', 'other']))).toBeUndefined()
    expect(
      readPendingFlow(
        {
          ...f.pending,
          request: { ...f.pending.request, account: 'unexpected' },
        },
        owner,
      ),
    ).toBeUndefined()
  })
})
