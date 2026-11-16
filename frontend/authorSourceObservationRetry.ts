import type { Account, Money, Task } from './api'
import type { components } from './api-schema'
import type { AuthorSourceEntry, ClarificationPreview } from './authorSourceClarification'
import { readFlowBudget, type FlowBudget } from './flowContinuation'
import { canonical, object } from './planRestart'

export const AUTHOR_OBSERVATION_RETRY_VERSION = 'author-source-observation-retry/1'
export const AUTHORED_SOURCE_POLICY_VERSION = 'authored-source-scope/2'
export const OBSERVATION_EXECUTION_LEGACY = 'authored-source-observation-execution/1'
export const OBSERVATION_EXECUTION_VERSION = 'authored-source-observation-execution/2'
export const EXECUTION_KEY = 'authored_source_observation_execution_version'
export const RETRY_KEY = 'author_source_observation_retry_version'
export type ObservationRetryInput = Required<components['schemas']['ObservationRetryInput']>
export type ReturnedObservationProof = {
  version: typeof AUTHOR_OBSERVATION_RETRY_VERSION
  owner_task_id: string
  attempt: string
  steps: {
    fingerprint: string
    call_id: string
    raw_sha256: string
    dispatch_event_id: number
    issues: string[]
  }[]
  calls: {
    id: string
    task_id: string
    account: string
    provider: string
    currency: string
    reserved: number
    charged: number
    status: 'settled'
    input_tokens: number | null
    output_tokens: number | null
    has_complete_step: true
    scope: 'source_observation'
  }[]
  call_ids: string[]
  errors: string[][]
  stopped_event_id: number
  sha256: string
}
export type ObservationRetryPreview = {
  version: typeof AUTHOR_OBSERVATION_RETRY_VERSION
  source_policy: typeof AUTHORED_SOURCE_POLICY_VERSION
  old_execution_version: typeof OBSERVATION_EXECUTION_LEGACY
  new_execution_version: typeof OBSERVATION_EXECUTION_VERSION
  source_task_id: string
  novel_id: string
  branch_id: string
  chapter_id: string
  chapter: number
  request_basis: { preview_basis_sha256: string }
  baseline: ClarificationPreview['baseline']
  original_source: AuthorSourceEntry
  current_source: AuthorSourceEntry
  plan: unknown
  policies: Record<string, unknown>
  adoption: 'manual' | 'automatic'
  repair_round: number
  account: Account
  budget: FlowBudget
  shared_budget: Money[]
  known_returned_proof: ReturnedObservationProof
  eligible: true
  starts_child: true
  source_unchanged: true
  author_literary_approval: 'unverified'
  notice: string
}
export type PendingObservationRetry = {
  source: Task
  preview: ObservationRetryPreview
  request: ObservationRetryInput
}
const same = (a: unknown, b: unknown) => canonical(a) === canonical(b)
const text = (v: unknown): v is string => typeof v === 'string' && !!v.trim()
const hash = (v: unknown): v is string => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v)
const integer = (v: unknown): v is number => Number.isSafeInteger(v) && Number(v) >= 0
const money = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v >= 0
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.length > 0 && v.every(text)
const policies = (task: Task) =>
  Object.fromEntries(Object.entries(task.result).filter(([key]) => key.endsWith('_version')))
const inherited = (v: unknown) =>
  Object.fromEntries(
    Object.entries(object(v) || {}).filter(([key]) => !['client_request_id', 'budget_limit'].includes(key)),
  )
const emptyHash = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
const entry = (v: unknown, chapter: number): v is AuthorSourceEntry => {
  const r = object(v)
  return (
    !!r &&
    r.key === `chapter:${chapter}` &&
    r.number === chapter &&
    r.kind === 'author_plot' &&
    text(r.text) &&
    hash(r.sha256)
  )
}
export const observationRetryPreviewPath = (id: string) =>
  `/tasks/${encodeURIComponent(id)}/author-source-observation-retry-preview`
export const observationRetryPath = (id: string, nonce?: string) =>
  `/tasks/${encodeURIComponent(id)}/author-source-observation-retries${nonce === undefined ? '' : '?' + new URLSearchParams({ client_request_id: nonce })}`

export function observationRetryUnavailableReason(task: Task) {
  const r = object(task.result),
    phases = object(
      object(object(r?.authored_source_scope_receipts)?.[`chapter:${r?.next_chapter}`])?.phases,
    ),
    observe = object(phases?.observe)
  if (
    task.kind !== 'workflow' ||
    task.payload.mode !== 'assisted' ||
    task.payload.plot_contract_check !== true
  )
    return '仅半自动情节核对批次可核对本停点。'
  if (r?.authored_source_scope_version !== AUTHORED_SOURCE_POLICY_VERSION)
    return '来源政策须已冻结为/2；旧版范围升级继续使用原入口。'
  if (r[EXECUTION_KEY] !== undefined && r[EXECUTION_KEY] !== OBSERVATION_EXECUTION_LEGACY)
    return '本批次已有新执行模板；不能重复同版恢复。'
  if (r[RETRY_KEY] || r.author_source_observation_retry) return '已有观察恢复回执，历史只读，不能再次恢复。'
  if (r.continued_to) return '已有子批次，请查看原标识回执。'
  if (
    task.status !== 'blocked_runtime' ||
    task.cancel !== 0 ||
    r.waiting_artifact ||
    r.waiting_structure ||
    r.pending_structure ||
    r.pause_after_maintenance_requested
  )
    return '先处理活动、取消、正文或维护状态；仅原来源停点可核对。'
  const record = object(object(r.authored_source_scope_receipts)?.[`chapter:${r.next_chapter}`])
  if (
    record?.version !== AUTHORED_SOURCE_POLICY_VERSION ||
    record.state !== 'pending' ||
    !phases ||
    Object.keys(phases).join(',') !== 'observe' ||
    !observe ||
    !['pending', 'started'].includes(String(observe.status)) ||
    'response' in observe ||
    'response_sha256' in observe
  )
    return '只读取尚未保存观察结果的原现场；能否恢复由服务端核对三次已返回及结算证明。'
  return ''
}
function returnedProof(
  v: unknown,
  source: Task,
  provider: string,
  currency: string,
): v is ReturnedObservationProof {
  const r = object(v),
    state = object(source.result),
    record = object(object(state?.authored_source_scope_receipts)?.[`chapter:${state?.next_chapter}`]),
    observe = object(object(record?.phases)?.observe)
  if (
    !r ||
    r.version !== AUTHOR_OBSERVATION_RETRY_VERSION ||
    r.owner_task_id !== source.id ||
    !text(r.attempt) ||
    r.attempt !== observe?.attempt ||
    observe?.status !== 'started' ||
    observe.owner_task_id !== source.id ||
    !hash(r.sha256) ||
    !integer(r.stopped_event_id) ||
    r.stopped_event_id < 1 ||
    !Array.isArray(r.steps) ||
    r.steps.length !== 3 ||
    !strings(r.call_ids) ||
    r.call_ids.length !== 3 ||
    new Set(r.call_ids).size !== 3 ||
    !Array.isArray(r.errors) ||
    r.errors.length !== 3 ||
    !r.errors.every(strings)
  )
    return false
  if (!Array.isArray(r.calls) || r.calls.length !== 3) return false
  const proofSteps = r.steps
  const auditCalls = r.calls.map(object)
  if (
    !auditCalls.every(
      (call, index) =>
        !!call &&
        call.id === object(proofSteps[index])?.call_id &&
        call.task_id === source.id &&
        call.account === source.payload.account &&
        call.status === 'settled' &&
        call.has_complete_step === true &&
        call.scope === 'source_observation' &&
        call.provider === provider &&
        call.currency === currency &&
        money(call.reserved) &&
        money(call.charged) &&
        (call.input_tokens === null || integer(call.input_tokens)) &&
        (call.output_tokens === null || integer(call.output_tokens)),
    )
  )
    return false
  const calls = r.call_ids,
    errors = r.errors
  const rows = r.steps.map(object)
  return (
    rows.every(
      (step, index) =>
        !!step &&
        text(step.fingerprint) &&
        text(step.call_id) &&
        calls.includes(step.call_id) &&
        hash(step.raw_sha256) &&
        integer(step.dispatch_event_id) &&
        step.dispatch_event_id > 0 &&
        strings(step.issues) &&
        same(step.issues, errors[index]),
    ) &&
    new Set(rows.map((x) => x?.fingerprint)).size === 3 &&
    new Set(rows.map((x) => x?.call_id)).size === 3 &&
    new Set(rows.map((x) => x?.dispatch_event_id)).size === 3 &&
    rows.every((x) => Number(x?.dispatch_event_id) < Number(r.stopped_event_id))
  )
}
export function readObservationRetryPreview(
  value: unknown,
  source: Task,
): ObservationRetryPreview | undefined {
  const r = object(value),
    basis = object(r?.request_basis),
    b = object(r?.baseline),
    a = object(r?.account),
    result = object(source.result),
    record = object(object(result?.authored_source_scope_receipts)?.[`chapter:${result?.next_chapter}`]),
    snapshot = object(result?.authored_source_scope_snapshot),
    budget = readFlowBudget(r?.budget),
    shared = Array.isArray(r?.shared_budget) ? r.shared_budget.map(object) : []
  const original = Array.isArray(snapshot?.sources)
    ? snapshot.sources.find((v) => object(v)?.number === result?.next_chapter)
    : undefined
  if (
    observationRetryUnavailableReason(source) ||
    !r ||
    !basis ||
    !b ||
    !a ||
    !budget ||
    r.version !== AUTHOR_OBSERVATION_RETRY_VERSION ||
    r.source_policy !== AUTHORED_SOURCE_POLICY_VERSION ||
    r.old_execution_version !== OBSERVATION_EXECUTION_LEGACY ||
    r.new_execution_version !== OBSERVATION_EXECUTION_VERSION ||
    r.source_task_id !== source.id ||
    r.novel_id !== source.novel_id ||
    r.branch_id !== source.branch_id ||
    !text(r.chapter_id) ||
    !integer(r.chapter) ||
    r.chapter < 1 ||
    r.chapter !== result?.next_chapter ||
    !entry(r.original_source, r.chapter) ||
    !entry(r.current_source, r.chapter) ||
    !same(r.original_source, original) ||
    !same(r.current_source, record?.source) ||
    Object.keys(basis).join(',') !== 'preview_basis_sha256' ||
    !hash(basis.preview_basis_sha256) ||
    b.preview_basis_sha256 !== basis.preview_basis_sha256 ||
    b.story_revision !== result?.expected_story_revision ||
    b.chapter_revision !== 0 ||
    b.chapter_content_sha256 !== emptyHash ||
    ['plan_sha256', 'source_receipt_sha256', 'payload_sha256', 'progress_sha256', 'manuscript_sha256'].some(
      (k) => !hash(b[k]),
    ) ||
    !Array.isArray(b.manuscript_versions) ||
    !object(r.plan) ||
    !same(r.plan, result?.plan) ||
    !same(r.policies, policies(source)) ||
    r.adoption !== source.payload.adoption ||
    !integer(r.repair_round) ||
    r.repair_round !== (result?.repair_round ?? 0) ||
    a.id !== source.payload.account ||
    a.kind !== budget.provider ||
    a.currency !== (budget.provider === 'deepseek' ? 'CNY' : 'USD') ||
    !text(a.model) ||
    typeof a.configured !== 'boolean' ||
    typeof a.priced !== 'boolean' ||
    !shared.some(
      (x) =>
        x?.provider === budget.provider &&
        x.currency === a.currency &&
        text(x.limit) &&
        money(x.settled) &&
        x.reserved_unknown === 0 &&
        money(x.remaining),
    ) ||
    !returnedProof(r.known_returned_proof, source, budget.provider, String(a.currency)) ||
    r.eligible !== true ||
    r.starts_child !== true ||
    r.source_unchanged !== true ||
    r.author_literary_approval !== 'unverified' ||
    !text(r.notice)
  )
    return
  const proven = r.known_returned_proof as ReturnedObservationProof
  const observationMicros = proven.calls.reduce((sum, call) => sum + Math.round(call.charged * 1_000_000), 0)
  if (observationMicros > Math.round(budget.source_task_settled * 1_000_000)) return
  return value as ObservationRetryPreview
}
export function readPendingObservationRetry(
  value: unknown,
  owner: string,
): PendingObservationRetry | undefined {
  const row = object(value),
    source = object(row?.source),
    request = object(row?.request)
  if (
    !row ||
    !source ||
    !request ||
    !object(source.payload) ||
    !object(source.result) ||
    canonical([source.novel_id, source.branch_id, source.id]) !== owner
  )
    return
  const preview = readObservationRetryPreview(row.preview, source as Task)
  if (
    !preview ||
    Object.keys(request).sort().join(',') !== 'client_request_id,preview_basis_sha256,reason,version' ||
    request.version !== AUTHOR_OBSERVATION_RETRY_VERSION ||
    !text(request.client_request_id) ||
    request.client_request_id.length < 8 ||
    request.client_request_id.length > 128 ||
    request.preview_basis_sha256 !== preview.request_basis.preview_basis_sha256 ||
    !text(request.reason) ||
    Array.from(request.reason).length > 2000
  )
    return
  return value as PendingObservationRetry
}
export function readObservationRetryChild(
  value: unknown,
  pending: PendingObservationRetry,
): Task | undefined {
  const row = object(value),
    payload = object(row?.payload),
    result = object(row?.result),
    receipt = object(result?.author_source_observation_retry),
    source = pending.source,
    p = pending.preview,
    execution = object(result?.authored_source_observation_execution_snapshot)
  const expectedPolicies = {
    ...policies(source),
    [EXECUTION_KEY]: OBSERVATION_EXECUTION_VERSION,
    [RETRY_KEY]: AUTHOR_OBSERVATION_RETRY_VERSION,
  }
  if (
    !row ||
    !text(row.id) ||
    row.id === source.id ||
    row.kind !== 'workflow' ||
    row.novel_id !== source.novel_id ||
    row.branch_id !== source.branch_id ||
    ![
      'queued',
      'running',
      'blocked_runtime',
      'needs_review',
      'paused',
      'cancelled',
      'continued',
      'blocked_creative',
      'segment_complete',
      'production_complete',
    ].includes(String(row.status)) ||
    !money(row.created) ||
    ![0, 1].includes(Number(row.cancel)) ||
    typeof row.cancel !== 'number' ||
    typeof row.error !== 'string' ||
    !payload ||
    !result ||
    !receipt ||
    result[RETRY_KEY] !== AUTHOR_OBSERVATION_RETRY_VERSION ||
    result[EXECUTION_KEY] !== OBSERVATION_EXECUTION_VERSION ||
    result.authored_source_scope_version !== AUTHORED_SOURCE_POLICY_VERSION ||
    result.continued_from !== source.id ||
    !execution ||
    execution.version !== OBSERVATION_EXECUTION_VERSION ||
    execution.task_id !== row.id ||
    execution.novel_id !== source.novel_id ||
    execution.branch_id !== source.branch_id ||
    execution.kind !== 'workflow' ||
    execution.creation !== 'explicit_retry' ||
    execution.source_policy_version !== AUTHORED_SOURCE_POLICY_VERSION ||
    !hash(execution.payload_sha256) ||
    !hash(execution.source_snapshot_sha256) ||
    !object(execution.legacy_receipts) ||
    !same(inherited(payload), inherited(source.payload)) ||
    payload.client_request_id !== pending.request.client_request_id ||
    payload.budget_limit !== p.budget.remaining_run_limit ||
    !same(result.plan, source.result.plan) ||
    !integer(result.repair_round ?? 0) ||
    !integer(result.next_chapter) ||
    result.next_chapter < p.chapter ||
    (result.next_chapter === p.chapter &&
      Number(result.repair_round ?? 0) < Number(source.result.repair_round ?? 0)) ||
    ['author_contract_snapshot', 'genre_profile_snapshot'].some(
      (k) => !same(result[k], object(source.result)?.[k]),
    ) ||
    Object.entries(expectedPolicies).some(([k, v]) => !same(result[k], v)) ||
    receipt.version !== AUTHOR_OBSERVATION_RETRY_VERSION ||
    receipt.task_id !== row.id ||
    receipt.source_task_id !== source.id ||
    receipt.novel_id !== source.novel_id ||
    receipt.branch_id !== source.branch_id ||
    receipt.chapter !== p.chapter ||
    receipt.chapter_id !== p.chapter_id ||
    receipt.source_payload_sha256 !== p.baseline.payload_sha256 ||
    receipt.source_progress_sha256 !== p.baseline.progress_sha256 ||
    receipt.plan_sha256 !== p.baseline.plan_sha256 ||
    !same(receipt.baseline, p.baseline) ||
    !same(receipt.request, pending.request) ||
    !same(receipt.previous_policies, p.policies) ||
    !same(receipt.policies, expectedPolicies) ||
    !same(receipt.account, p.account) ||
    !same(readFlowBudget(receipt.budget), p.budget) ||
    !same(receipt.known_returned_proof, p.known_returned_proof) ||
    !same(receipt.original_source_entry, p.original_source) ||
    !same(receipt.source_entry, p.current_source) ||
    !same(
      receipt.previous_source_receipt,
      object(object(source.result)?.authored_source_scope_receipts)?.[`chapter:${p.chapter}`],
    ) ||
    receipt.source_policy !== AUTHORED_SOURCE_POLICY_VERSION ||
    receipt.old_execution_version !== OBSERVATION_EXECUTION_LEGACY ||
    receipt.new_execution_version !== OBSERVATION_EXECUTION_VERSION ||
    receipt.repair_round !== p.repair_round ||
    receipt.adoption !== p.adoption ||
    receipt.source_unchanged !== true ||
    receipt.starts_child !== true ||
    receipt.author_literary_approval !== 'unverified'
  )
    return
  return value as Task
}
export function readObservationRetryLookup(
  value: unknown,
  pending: PendingObservationRetry,
): Task | null | undefined {
  const row = object(value)
  if (
    !row ||
    row.source_task_id !== pending.source.id ||
    row.client_request_id !== pending.request.client_request_id
  )
    return
  return row.task === null ? null : readObservationRetryChild(row.task, pending)
}
