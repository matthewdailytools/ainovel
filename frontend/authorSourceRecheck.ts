import type { Task } from './api'
import type { components } from './api-schema'
import {
  AUTHOR_CLARIFICATION_VERSION,
  AUTHORED_SCOPE_VERSION,
  clarificationUnavailableReason,
  readClarificationPreview,
  type ClarificationPreview,
} from './authorSourceClarification'
import { readFlowBudget } from './flowContinuation'
import { canonical, object } from './planRestart'

export const AUTHOR_SOURCE_RECHECK_VERSION = 'author-source-recheck/1'
export const AUTHORED_SOURCE_POLICY_VERSION = 'authored-source-scope/2'
export type SourceRecheckInput = Required<components['schemas']['RecheckInput']>
export type SourceRecheckPreview = Omit<ClarificationPreview, 'version'> & {
  version: typeof AUTHOR_SOURCE_RECHECK_VERSION
  old_policy: typeof AUTHORED_SCOPE_VERSION
  new_policy: typeof AUTHORED_SOURCE_POLICY_VERSION
  source_unchanged: true
  starts_child: true
  eligible: true
}
export type PendingSourceRecheck = {
  source: Task
  preview: SourceRecheckPreview
  request: SourceRecheckInput
}
const same = (a: unknown, b: unknown) => canonical(a) === canonical(b)
const text = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0
const hash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0
const strings = (value: unknown): value is string[] =>
  Array.isArray(value) && value.length > 0 && value.every(text) && new Set(value).size === value.length
const policies = (task: Task) =>
  Object.fromEntries(Object.entries(task.result).filter(([key]) => key.endsWith('_version')))
const inherited = (value: unknown) =>
  Object.fromEntries(
    Object.entries(object(value) || {}).filter(
      ([key]) => !['client_request_id', 'budget_limit'].includes(key),
    ),
  )
export const sourceRecheckPreviewPath = (taskId: string) =>
  `/tasks/${encodeURIComponent(taskId)}/author-source-recheck-preview`
export const sourceRecheckPath = (taskId: string, nonce?: string) =>
  `/tasks/${encodeURIComponent(taskId)}/author-source-rechecks${nonce === undefined ? '' : '?' + new URLSearchParams({ client_request_id: nonce })}`

export function sourceRecheckUnavailableReason(task: Task) {
  if (object(task.result)?.authored_source_scope_version === AUTHORED_SOURCE_POLICY_VERSION)
    return '本批次已使用新版来源核对；真实未知仍须核对或明确补充，不能重复创建同版子任务。'
  return clarificationUnavailableReason(task)
}

export function readSourceRecheckPreview(value: unknown, source: Task): SourceRecheckPreview | undefined {
  const row = object(value),
    budget = object(row?.budget),
    proof = object(budget?.source_steps),
    state = object(source.result)
  const record = object(
    object(state?.authored_source_scope_receipts)?.[`chapter:${source.result.next_chapter}`],
  )
  const owners = Object.values(object(record?.phases) || {}).map((phase) => object(phase)?.owner_task_id)
  if (
    sourceRecheckUnavailableReason(source) ||
    state?.authored_source_scope_version !== AUTHORED_SCOPE_VERSION ||
    !row ||
    row.version !== AUTHOR_SOURCE_RECHECK_VERSION ||
    row.old_policy !== AUTHORED_SCOPE_VERSION ||
    row.new_policy !== AUTHORED_SOURCE_POLICY_VERSION ||
    row.source_unchanged !== true ||
    row.starts_child !== true ||
    row.eligible !== true ||
    !proof ||
    !hash(proof.sha256) ||
    !strings(proof.owner_task_ids) ||
    !same(proof.owner_task_ids, [...new Set(owners)].sort()) ||
    !strings(proof.call_ids) ||
    !strings(proof.step_fingerprints)
  )
    return
  const parsed = readClarificationPreview({ ...row, version: AUTHOR_CLARIFICATION_VERSION }, source)
  return parsed ? (value as SourceRecheckPreview) : undefined
}

export function readPendingSourceRecheck(value: unknown, owner: string): PendingSourceRecheck | undefined {
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
  const preview = readSourceRecheckPreview(row.preview, source as Task)
  if (
    !preview ||
    Object.keys(request).sort().join(',') !== 'client_request_id,preview_basis_sha256,reason,version' ||
    request.version !== AUTHOR_SOURCE_RECHECK_VERSION ||
    !text(request.client_request_id) ||
    request.client_request_id.length < 8 ||
    request.client_request_id.length > 128 ||
    request.preview_basis_sha256 !== preview.request_basis.preview_basis_sha256 ||
    typeof request.reason !== 'string' ||
    !request.reason.trim() ||
    Array.from(request.reason).length > 2000
  )
    return
  return value as PendingSourceRecheck
}

export function readSourceRecheckChild(value: unknown, pending: PendingSourceRecheck): Task | undefined {
  const row = object(value),
    payload = object(row?.payload),
    result = object(row?.result),
    receipt = object(result?.author_source_recheck),
    source = pending.source,
    preview = pending.preview
  const expectedPolicies = {
    ...policies(source),
    authored_source_scope_version: AUTHORED_SOURCE_POLICY_VERSION,
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
    typeof row.created !== 'number' ||
    !Number.isFinite(row.created) ||
    (row.cancel !== 0 && row.cancel !== 1) ||
    typeof row.error !== 'string' ||
    !payload ||
    !result ||
    !receipt ||
    result.author_source_recheck_version !== AUTHOR_SOURCE_RECHECK_VERSION ||
    result.authored_source_scope_version !== AUTHORED_SOURCE_POLICY_VERSION ||
    result.continued_from !== source.id ||
    !same(inherited(payload), inherited(source.payload)) ||
    payload.client_request_id !== pending.request.client_request_id ||
    payload.budget_limit !== preview.budget.remaining_run_limit ||
    !same(result.plan, source.result.plan) ||
    !integer(result.repair_round ?? 0) ||
    Number(result.repair_round ?? 0) < (source.result.repair_round ?? 0) ||
    ['author_contract_snapshot', 'genre_profile_snapshot'].some(
      (key) => !same(result[key], object(source.result)?.[key]),
    ) ||
    Object.entries(expectedPolicies).some(([key, policy]) => !same(result[key], policy)) ||
    receipt.version !== AUTHOR_SOURCE_RECHECK_VERSION ||
    receipt.task_id !== row.id ||
    receipt.source_task_id !== source.id ||
    receipt.novel_id !== source.novel_id ||
    receipt.branch_id !== source.branch_id ||
    receipt.chapter !== preview.chapter ||
    receipt.chapter_id !== preview.chapter_id ||
    receipt.source_payload_sha256 !== preview.baseline.payload_sha256 ||
    receipt.source_progress_sha256 !== preview.baseline.progress_sha256 ||
    receipt.plan_sha256 !== preview.baseline.plan_sha256 ||
    !same(receipt.baseline, preview.baseline) ||
    !same(receipt.request, pending.request) ||
    !same(receipt.previous_policies, preview.policies) ||
    !same(receipt.policies, expectedPolicies) ||
    !same(receipt.account, preview.account) ||
    !same(readFlowBudget(receipt.budget), preview.budget) ||
    !same(receipt.original_source_entry, preview.original_source) ||
    !same(receipt.previous_source_entry, preview.current_source) ||
    !same(receipt.source_entry, preview.current_source) ||
    !same(
      receipt.previous_source_receipt,
      object(object(source.result)?.authored_source_scope_receipts)?.[`chapter:${preview.chapter}`],
    ) ||
    receipt.old_policy !== AUTHORED_SCOPE_VERSION ||
    receipt.new_policy !== AUTHORED_SOURCE_POLICY_VERSION ||
    receipt.source_unchanged !== true ||
    receipt.starts_child !== true ||
    receipt.author_literary_approval !== 'unverified'
  )
    return
  return value as Task
}

export function readSourceRecheckLookup(
  value: unknown,
  pending: PendingSourceRecheck,
): Task | null | undefined {
  const row = object(value)
  if (
    !row ||
    row.source_task_id !== pending.source.id ||
    row.client_request_id !== pending.request.client_request_id
  )
    return
  return row.task === null ? null : readSourceRecheckChild(row.task, pending)
}
