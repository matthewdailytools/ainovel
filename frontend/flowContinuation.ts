import type { components } from './api-schema'
import type { Account, Money, Task } from './api'
import { canonical, object, sourceSignature } from './planRestart'

export { canonical, sourceSignature }
export const FLOW_VERSION = 'source-flow-entailment/1'
export const FLOW_CONTINUATION_VERSION = 'workflow-flow-policy-continuation/1'
export type FlowContinuationInput = components['schemas']['FlowContinuationInput']
type ForeignCall = {
  id: string
  task_id: string
  account: string
  provider: string
  currency: string
  reserved: number
  charged: number | null
  status: string
  has_complete_step: boolean
  scope: string
}
export type FlowBudget = {
  provider: string
  source_run_limit: number | null
  source_task_settled: number
  remaining_run_limit: number | null
  effective_remaining_run_limit: number | null
  auxiliary_settled: number
  unknown_reserve: 0
  foreign_history_calls: ForeignCall[]
  foreign_unknown_calls: ForeignCall[]
  [key: string]: unknown
}
export type FlowPreview = {
  version: typeof FLOW_CONTINUATION_VERSION
  flow_source_entailment_version: typeof FLOW_VERSION
  source_task_id: string
  branch_id: string
  story_revision: number
  source_flow_policy: 'legacy'
  source_policies: Record<string, unknown>
  next_chapter: number
  plan_sha256: string
  preview_basis_sha256: string
  account: string
  budget: FlowBudget
  notice: string
}
export type FlowContext = {
  preview: FlowPreview
  account: Account
  shared: Money
}
export type PendingFlow = {
  request: FlowContinuationInput
  context: FlowContext
  source: Task
}
const hash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
const text = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0
const money = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0
const nullableMoney = (value: unknown): value is number | null => value === null || money(value)
const currency = (provider: string) =>
  provider === 'deepseek' ? 'CNY' : provider === 'openai' ? 'USD' : undefined

export function flowUnavailableReason(task: Task): string {
  if (
    task.kind !== 'workflow' ||
    task.payload.plot_contract_check !== true ||
    task.result.plot_contract_version !== 'author-plot-audit/2'
  )
    return '本入口只支持已冻结v2情节核对的整书任务。'
  if (task.result.flow_source_entailment_version !== undefined)
    return '本任务已冻结新版流向政策，无需重复创建政策任务。'
  if (task.result.continued_to) return '原任务已有续跑任务，请打开已有子任务，不能重复创建。'
  if (task.status !== 'paused')
    return task.status === 'needs_review'
      ? '当前仍待审阅：先明确处理待审正文并完成维护，再在章节检查点暂停。'
      : '当前不是已暂停的安全章节检查点。'
  if (
    task.result.waiting_artifact ||
    task.result.waiting_structure ||
    task.result.pending_structure ||
    object(task.result)?.pause_after_maintenance_requested
  )
    return '正文或维护仍待处理，请先在原流程完成维护后暂停。'
  const next = task.result.next_chapter
  if (!integer(next) || next < 2 || !integer(task.payload.chapters) || next > task.payload.chapters)
    return '没有尚未开始的后续章节检查点。'
  return ''
}

export function readFlowBudget(value: unknown): FlowBudget | undefined {
  const row = object(value)
  if (
    !row ||
    !text(row.provider) ||
    !currency(row.provider) ||
    !nullableMoney(row.source_run_limit) ||
    !money(row.source_task_settled) ||
    !nullableMoney(row.remaining_run_limit) ||
    !nullableMoney(row.effective_remaining_run_limit) ||
    !money(row.auxiliary_settled) ||
    row.unknown_reserve !== 0
  )
    return undefined
  if (
    (row.remaining_run_limit !== null &&
      (row.remaining_run_limit <= 0 ||
        row.effective_remaining_run_limit === null ||
        row.effective_remaining_run_limit > row.remaining_run_limit)) ||
    (row.effective_remaining_run_limit !== null && row.effective_remaining_run_limit <= 0)
  )
    return undefined
  for (const key of ['foreign_history_calls', 'foreign_unknown_calls']) {
    const calls = row[key]
    if (
      !Array.isArray(calls) ||
      !calls.every((raw: unknown) => {
        const call = object(raw)
        return (
          call &&
          text(call.id) &&
          text(call.task_id) &&
          text(call.account) &&
          text(call.provider) &&
          call.provider !== row.provider &&
          currency(call.provider) === call.currency &&
          money(call.reserved) &&
          nullableMoney(call.charged) &&
          text(call.status) &&
          typeof call.has_complete_step === 'boolean' &&
          text(call.scope)
        )
      })
    )
      return undefined
  }
  return row as FlowBudget
}

export function readFlowPreview(value: unknown, task: Task): FlowPreview | undefined {
  const row = object(value)
  if (
    !row ||
    row.version !== FLOW_CONTINUATION_VERSION ||
    row.flow_source_entailment_version !== FLOW_VERSION ||
    row.source_task_id !== task.id ||
    row.branch_id !== task.branch_id ||
    row.account !== task.payload.account ||
    row.source_flow_policy !== 'legacy' ||
    !object(row.source_policies) ||
    canonical(row.source_policies) !==
      canonical({
        flow_source_entailment_version: null,
        ...Object.fromEntries(Object.entries(task.result).filter(([key]) => key.endsWith('_version'))),
      }) ||
    !integer(row.story_revision) ||
    row.story_revision !== task.result.expected_story_revision ||
    row.next_chapter !== task.result.next_chapter ||
    !hash(row.preview_basis_sha256) ||
    !hash(row.plan_sha256) ||
    !text(row.notice) ||
    !readFlowBudget(row.budget)
  )
    return undefined
  return row as FlowPreview
}

export function readFlowContext(preview: FlowPreview, value: unknown): FlowContext | undefined {
  const row = object(value)
  if (!Array.isArray(row?.accounts) || !Array.isArray(row?.budget)) return undefined
  const account = row.accounts.map(object).find((entry) => entry?.id === preview.account)
  const shared = row.budget.map(object).find((entry) => entry?.provider === preview.budget.provider)
  if (
    !account ||
    account.kind !== preview.budget.provider ||
    !text(account.model) ||
    account.currency !== currency(account.kind) ||
    typeof account.configured !== 'boolean' ||
    typeof account.priced !== 'boolean' ||
    !shared ||
    shared.currency !== account.currency ||
    !text(shared.limit) ||
    !money(shared.settled) ||
    !money(shared.reserved_unknown) ||
    !money(shared.remaining)
  )
    return undefined
  return { preview, account: account as Account, shared: shared as Money }
}

function inherited(value: unknown) {
  const row = object(value)
  return (
    row &&
    Object.fromEntries(
      Object.entries(row).filter(([key]) => !['client_request_id', 'budget_limit'].includes(key)),
    )
  )
}

export function readFlowChild(value: unknown, pending: PendingFlow): Task | undefined {
  const row = object(value),
    payload = object(row?.payload),
    progress = object(row?.result)
  const receipt = object(progress?.flow_policy_continuation),
    source = pending.source,
    preview = pending.context.preview
  const budget = readFlowBudget(receipt?.budget)
  if (
    !row ||
    !text(row.id) ||
    row.id === source.id ||
    row.kind !== 'workflow' ||
    row.novel_id !== source.novel_id ||
    row.branch_id !== source.branch_id ||
    !text(row.status) ||
    !money(row.created) ||
    !integer(row.cancel) ||
    typeof row.error !== 'string' ||
    !payload ||
    !progress ||
    !receipt ||
    !budget ||
    canonical(inherited(payload)) !== canonical(inherited(source.payload)) ||
    payload.client_request_id !== pending.request.client_request_id ||
    payload.budget_limit !== budget.remaining_run_limit ||
    budget.provider !== preview.budget.provider ||
    progress.continued_from !== source.id ||
    progress.flow_source_entailment_version !== FLOW_VERSION ||
    Object.entries(source.result).some(
      ([key, value]) =>
        key.endsWith('_version') &&
        key !== 'flow_source_entailment_version' &&
        canonical(progress[key]) !== canonical(value),
    ) ||
    canonical(progress.plan) !== canonical(source.result.plan) ||
    receipt.version !== FLOW_CONTINUATION_VERSION ||
    receipt.source_task_id !== source.id ||
    receipt.source_flow_policy !== 'legacy' ||
    !hash(receipt.source_payload_sha256) ||
    !hash(receipt.source_progress_sha256) ||
    receipt.plan_sha256 !== preview.plan_sha256 ||
    receipt.next_chapter !== preview.next_chapter ||
    canonical(receipt.request) !== canonical(pending.request)
  )
    return undefined
  return row as Task
}

export function readPendingFlow(value: unknown, owner: string): PendingFlow | undefined {
  const row = object(value),
    request = object(row?.request),
    source = object(row?.source),
    context = object(row?.context)
  if (
    !row ||
    !request ||
    !source ||
    !context ||
    canonical([source.novel_id, source.branch_id, source.id]) !== owner ||
    !object(source.payload) ||
    !object(source.result)
  )
    return undefined
  const preview = readFlowPreview(context.preview, source as Task)
  if (
    !preview ||
    !readFlowContext(preview, {
      accounts: [context.account],
      budget: [context.shared],
    }) ||
    !text(request.client_request_id) ||
    request.client_request_id.length < 8 ||
    request.client_request_id.length > 128 ||
    request.preview_basis_sha256 !== preview.preview_basis_sha256 ||
    request.flow_source_entailment_version !== FLOW_VERSION ||
    !text(request.reason) ||
    request.reason.length > 2000 ||
    Object.keys(request).sort().join(',') !==
      'client_request_id,flow_source_entailment_version,preview_basis_sha256,reason'
  )
    return undefined
  return row as PendingFlow
}
