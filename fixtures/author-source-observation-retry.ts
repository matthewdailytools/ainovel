import type { Task } from '../frontend/api'
import { sourceRecheckFixture } from './author-source-recheck'
import { object } from '../frontend/planRestart'
import {
  AUTHOR_OBSERVATION_RETRY_VERSION as V,
  AUTHORED_SOURCE_POLICY_VERSION as P,
  OBSERVATION_EXECUTION_LEGACY as OLD,
  OBSERVATION_EXECUTION_VERSION as NEW,
  EXECUTION_KEY,
  RETRY_KEY,
  type ObservationRetryPreview,
  type ObservationRetryInput,
  type ReturnedObservationProof,
} from '../frontend/authorSourceObservationRetry'

/** Original anonymous structural fixture. No real manuscript, returned model text, or paid evidence. */
export function observationRetryFixture(
  id = 'clock-observation-parent',
  branch = 'clock-observation-branch',
) {
  const old = sourceRecheckFixture(id, branch),
    source = structuredClone(old.source)
  const text = '乔禾在钟楼登记当天误差，把未签字的校时单留在值班室；值班员只记录来访，不交出钥匙。'
  const entry = { ...old.preview.original_source, text, sha256: 'a'.repeat(64) }
  source.status = 'blocked_runtime'
  source.cancel = 0
  source.error = '三次完整返回仍未通过引用校验。'
  Object.assign(source.payload, {
    ...source.payload,
    brief: '原创钟楼校时调查',
    plots: [{ number: entry.number, title: '钟楼值班', plot: text, fixed: ['不交出钥匙'] }],
    fixed: ['不得取得钥匙'],
    adoption: 'automatic',
  })
  Object.assign(source.result, {
    authored_source_scope_version: P,
    plan: { chapters: object(source.payload)!.plots, ending: '校时误差得到有出处的解释' },
    repair_round: 0,
  })
  const snapshot = object(object(source.result)!.authored_source_scope_snapshot)!
  snapshot.version = P
  snapshot.sources = [entry]
  const record = {
    version: P,
    state: 'pending',
    source: entry,
    source_sha256: entry.sha256,
    phases: {
      observe: {
        owner_task_id: id,
        status: 'started',
        attempt: 'clock-attempt',
        before_steps: [],
        before_calls: [],
        not_sent_history: [],
      },
    },
  }
  object(source.result)!.authored_source_scope_receipts = { [entry.key]: record }
  const policies = Object.fromEntries(Object.entries(source.result).filter(([k]) => k.endsWith('_version')))
  const proof: ReturnedObservationProof = {
    version: V,
    owner_task_id: id,
    attempt: 'clock-attempt',
    steps: [1, 2, 3].map((n) => ({
      fingerprint: `clock-step-${n}`,
      call_id: `clock-call-${n}`,
      raw_sha256: 'c'.repeat(64),
      dispatch_event_id: 100 + n,
      issues: ['原句引用未完整命中'],
    })),
    calls: [1, 2, 3].map((n) => ({
      id: `clock-call-${n}`,
      task_id: id,
      account: source.payload.account,
      provider: 'deepseek',
      currency: 'CNY',
      reserved: 0.04,
      charged: 0.04,
      status: 'settled',
      input_tokens: 10,
      output_tokens: 10,
      has_complete_step: true,
      scope: 'source_observation',
    })),
    call_ids: [1, 2, 3].map((n) => `clock-call-${n}`),
    errors: [['原句引用未完整命中'], ['原句引用未完整命中'], ['原句引用未完整命中']],
    stopped_event_id: 104,
    sha256: 'd'.repeat(64),
  }
  const { old_policy: _old, new_policy: _new, source_review: _review, ...base } = old.preview
  const preview: ObservationRetryPreview = {
    ...base,
    version: V,
    source_policy: P,
    old_execution_version: OLD,
    new_execution_version: NEW,
    original_source: entry,
    current_source: entry,
    plan: source.result.plan,
    policies,
    adoption: 'automatic',
    repair_round: 0,
    known_returned_proof: proof,
    notice:
      '三次已完整返回并结算。创建后将按原自动采纳方式继续写作和收费，仍须独立原意保持；原因不加入情节。',
  }
  const request: ObservationRetryInput = {
    version: V,
    client_request_id: 'clock-retry+/字%2F?=once',
    preview_basis_sha256: preview.request_basis.preview_basis_sha256,
    reason: '仅按新执行模板核对直接引用，原情节不变。',
  }
  function child(input = request): Task {
    const next = structuredClone(old.child())
    next.novel_id = source.novel_id
    next.payload = structuredClone(source.payload)
    Object.assign(next.payload, {
      ...structuredClone(source.payload),
      client_request_id: input.client_request_id,
      budget_limit: preview.budget.remaining_run_limit,
    })
    next.result = structuredClone(source.result)
    Object.assign(next.result, {
      ...structuredClone(source.result),
      continued_from: id,
      [RETRY_KEY]: V,
      [EXECUTION_KEY]: NEW,
    })
    object(next.result)!.authored_source_scope_receipts = {}
    object(next.result)!.authored_source_observation_execution_snapshot = {
      version: NEW,
      task_id: next.id,
      novel_id: source.novel_id,
      branch_id: branch,
      kind: 'workflow',
      payload_sha256: 'a'.repeat(64),
      source_policy_version: P,
      source_snapshot_sha256: 'b'.repeat(64),
      legacy_receipts: {},
      creation: 'explicit_retry',
    }
    object(next.result)!.author_source_observation_retry = structuredClone({
      version: V,
      task_id: next.id,
      source_task_id: id,
      novel_id: source.novel_id,
      branch_id: branch,
      chapter: preview.chapter,
      chapter_id: preview.chapter_id,
      source_policy: P,
      old_execution_version: OLD,
      new_execution_version: NEW,
      source_unchanged: true,
      starts_child: true,
      source_payload_sha256: preview.baseline.payload_sha256,
      source_progress_sha256: preview.baseline.progress_sha256,
      previous_source_receipt: structuredClone(record),
      original_source_entry: entry,
      source_entry: entry,
      plan_sha256: preview.baseline.plan_sha256,
      repair_round: preview.repair_round,
      adoption: 'automatic',
      previous_policies: policies,
      policies: { ...policies, [EXECUTION_KEY]: NEW, [RETRY_KEY]: V },
      baseline: preview.baseline,
      request: input,
      account: preview.account,
      budget: preview.budget,
      known_returned_proof: proof,
      author_literary_approval: 'unverified',
    })
    return next
  }
  return { source, record, preview, request, child }
}
