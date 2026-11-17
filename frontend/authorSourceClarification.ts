import type { Account, Money, Task } from './api'
import type { components } from './api-schema'
import { readFlowBudget, type FlowBudget } from './flowContinuation'
import { canonical, object } from './planRestart'
import { returnedProof, type ReturnedObservationProof } from './authorSourceObservationRetry'

export const AUTHOR_CLARIFICATION_VERSION = 'author-source-clarification/1'
export const AUTHORED_SCOPE_VERSION = 'authored-source-scope/1'
export const AUTHORED_SCOPE_CURRENT_VERSION = 'authored-source-scope/2'
export type ClarificationInput = Required<components['schemas']['ClarificationInput']>
export type AuthorSourceEntry = {
  key: string
  number: number
  kind: 'author_plot'
  text: string
  sha256: string
}
export type ClarificationPreview = {
  version: typeof AUTHOR_CLARIFICATION_VERSION
  source_task_id: string
  novel_id: string
  branch_id: string
  chapter_id: string
  chapter: number
  request_basis: { preview_basis_sha256: string }
  baseline: Record<string, unknown> & {
    preview_basis_sha256: string
    story_revision: number
    chapter_revision: 0
    chapter_content_sha256: string
    plan_sha256: string
    payload_sha256: string
    progress_sha256: string
  }
  original_source: AuthorSourceEntry
  current_source: AuthorSourceEntry
  source_review: {
    state: 'unverified' | 'pending'
    contract: unknown
    bindings: unknown
    inventory: unknown
    observation_failure?: {
      kind: 'known_returned_invalid_observation'
      proof: ReturnedObservationProof
      raw_returns: (ReturnedObservationProof['steps'][number] & { text: string })[]
      notice: string
    }
  }
  plan: unknown
  policies: Record<string, unknown>
  adoption: 'manual' | 'automatic'
  repair_round: number
  account: Account
  budget: FlowBudget
  shared_budget: Money[]
  author_literary_approval: 'unverified'
}
export type PendingClarification = {
  source: Task
  preview: ClarificationPreview
  request: ClarificationInput
}

const text = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0
const hash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0
const money = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0
const same = (a: unknown, b: unknown) => canonical(a) === canonical(b)
const policies = (task: Task) =>
  Object.fromEntries(Object.entries(task.result).filter(([key]) => key.endsWith('_version')))
const emptyHash = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'

function pendingObservation(task: Task) {
  const result = object(task.result),
    record = object(object(result?.authored_source_scope_receipts)?.[`chapter:${result?.next_chapter}`]),
    phases = object(record?.phases),
    observe = object(phases?.observe)
  return (
    task.status === 'blocked_runtime' &&
    result?.authored_source_scope_version === AUTHORED_SCOPE_CURRENT_VERSION &&
    result.authored_source_observation_execution_version === 'authored-source-observation-execution/2' &&
    record?.state === 'pending' &&
    !!phases &&
    Object.keys(phases).join(',') === 'observe' &&
    observe?.status === 'started' &&
    observe.owner_task_id === task.id &&
    !('response' in observe) &&
    !('response_sha256' in observe) &&
    object(result.author_source_clarification)?.chapter !== result.next_chapter
  )
}

export function clarificationUnavailableReason(task: Task) {
  const result = object(task.result),
    record = object(object(result?.authored_source_scope_receipts)?.[`chapter:${result?.next_chapter}`]),
    phases = object(record?.phases)
  if (
    task.kind !== 'workflow' ||
    task.payload.mode !== 'assisted' ||
    task.payload.plot_contract_check !== true
  )
    return '仅半自动情节核对批次可使用本入口。'
  const sourcePolicy = result?.authored_source_scope_version
  if (!result || (sourcePolicy !== AUTHORED_SCOPE_VERSION && sourcePolicy !== AUTHORED_SCOPE_CURRENT_VERSION))
    return '本批次未冻结可核实的作者来源政策，不能自动升级旧任务。'
  if (result.continued_to) return '已有续接任务，请打开已有子任务查看。'
  if (
    !['blocked_runtime', 'needs_review', 'paused', 'cancelled'].includes(task.status) ||
    result.waiting_artifact ||
    result.waiting_structure ||
    result.pending_structure ||
    result.pause_after_maintenance_requested
  )
    return '先处理活动任务、待审正文或维护，再核对来源停点。'
  // This only enables a read. The server must prove all three complete failures.
  if (pendingObservation(task)) return ''
  if (
    record?.state !== 'unverified' ||
    !phases ||
    !object(phases.observe) ||
    Object.keys(phases).some(
      (key) =>
        !(
          sourcePolicy === AUTHORED_SCOPE_CURRENT_VERSION
            ? ['observe', 'interpret', 'preserve']
            : ['observe', 'interpret']
        ).includes(key),
    ) ||
    Object.values(phases).some((value) => object(value)?.status !== 'completed')
  )
    return '仅已完整返回的来源待核停点可补充，未知派发或格式失败不能借此重发。'
  return ''
}

function failedObservationReview(
  review: Record<string, unknown>,
  source: Task,
  account: Record<string, unknown>,
) {
  const failure = object(review.observation_failure),
    proof = failure?.proof,
    raw = failure?.raw_returns
  return (
    pendingObservation(source) &&
    review.state === 'pending' &&
    review.inventory === null &&
    review.contract === null &&
    review.bindings === null &&
    failure?.kind === 'known_returned_invalid_observation' &&
    text(failure.notice) &&
    typeof account.kind === 'string' &&
    typeof account.currency === 'string' &&
    returnedProof(proof, source, account.kind, account.currency) &&
    Array.isArray(raw) &&
    raw.length === 3 &&
    raw.every((value, index) => {
      const row = object(value)
      if (!row || !text(row.text)) return false
      const { text: _text, ...step } = row
      return same(step, proof.steps[index])
    })
  )
}

function sourceEntry(value: unknown, chapter: number): value is AuthorSourceEntry {
  const row = object(value)
  return (
    !!row &&
    row.key === `chapter:${chapter}` &&
    row.number === chapter &&
    row.kind === 'author_plot' &&
    text(row.text) &&
    hash(row.sha256)
  )
}

export function readClarificationPreview(value: unknown, source: Task): ClarificationPreview | undefined {
  const row = object(value),
    basis = object(row?.request_basis),
    baseline = object(row?.baseline),
    review = object(row?.source_review),
    account = object(row?.account),
    result = object(source.result),
    snapshot = object(result?.authored_source_scope_snapshot),
    record = object(object(result?.authored_source_scope_receipts)?.[`chapter:${result?.next_chapter}`]),
    phases = object(record?.phases),
    observe = object(phases?.observe),
    shared = Array.isArray(row?.shared_budget) ? row.shared_budget.map(object) : [],
    budget = readFlowBudget(row?.budget)
  const original = Array.isArray(snapshot?.sources)
    ? snapshot.sources.find((entry) => object(entry)?.number === source.result.next_chapter)
    : undefined
  if (
    clarificationUnavailableReason(source) ||
    !row ||
    !basis ||
    !baseline ||
    !review ||
    !account ||
    !budget ||
    row.version !== AUTHOR_CLARIFICATION_VERSION ||
    row.source_task_id !== source.id ||
    row.novel_id !== source.novel_id ||
    row.branch_id !== source.branch_id ||
    !text(row.chapter_id) ||
    !integer(row.chapter) ||
    row.chapter < 1 ||
    row.chapter !== source.result.next_chapter ||
    !sourceEntry(row.original_source, row.chapter) ||
    !sourceEntry(row.current_source, row.chapter) ||
    !same(row.original_source, original) ||
    !same(row.current_source, record?.source) ||
    Object.keys(basis).join(',') !== 'preview_basis_sha256' ||
    !hash(basis.preview_basis_sha256) ||
    baseline.preview_basis_sha256 !== basis.preview_basis_sha256 ||
    baseline.story_revision !== source.result.expected_story_revision ||
    baseline.chapter_revision !== 0 ||
    baseline.chapter_content_sha256 !== emptyHash ||
    ['plan_sha256', 'source_receipt_sha256', 'payload_sha256', 'progress_sha256', 'manuscript_sha256'].some(
      (key) => !hash(baseline[key]),
    ) ||
    !Array.isArray(baseline.manuscript_versions) ||
    review.state !== record?.state ||
    (review.state !== 'unverified' && !failedObservationReview(review, source, account)) ||
    (review.state === 'unverified' && review.observation_failure !== undefined) ||
    !same(review.contract, record?.contract ?? null) ||
    !same(review.bindings, record?.bindings ?? null) ||
    !same(review.inventory, observe?.response ?? null) ||
    !same(row.plan, source.result.plan) ||
    !same(row.policies, policies(source)) ||
    row.adoption !== source.payload.adoption ||
    row.repair_round !== (source.result.repair_round ?? 0) ||
    account.id !== source.payload.account ||
    account.kind !== budget.provider ||
    account.currency !== (budget.provider === 'deepseek' ? 'CNY' : 'USD') ||
    !text(account.model) ||
    typeof account.configured !== 'boolean' ||
    typeof account.priced !== 'boolean' ||
    !shared.some(
      (item) =>
        item?.provider === budget.provider &&
        item.currency === account.currency &&
        text(item.limit) &&
        money(item.settled) &&
        money(item.reserved_unknown) &&
        money(item.remaining),
    ) ||
    row.author_literary_approval !== 'unverified'
  )
    return undefined
  return row as ClarificationPreview
}

export function readPendingClarification(value: unknown, owner: string): PendingClarification | undefined {
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
    return undefined
  const preview = readClarificationPreview(row.preview, source as Task)
  if (
    !preview ||
    Object.keys(request).sort().join(',') !==
      'actor,clarification,client_request_id,preview_basis_sha256,reason,version' ||
    request.version !== AUTHOR_CLARIFICATION_VERSION ||
    request.actor !== 'author' ||
    !text(request.client_request_id) ||
    request.client_request_id.length < 8 ||
    request.client_request_id.length > 128 ||
    request.preview_basis_sha256 !== preview.request_basis.preview_basis_sha256 ||
    !text(request.clarification) ||
    Array.from(request.clarification).length > 8000 ||
    typeof request.reason !== 'string' ||
    Array.from(request.reason).length > 2000
  )
    return undefined
  return row as PendingClarification
}

const inherited = (value: unknown) =>
  Object.fromEntries(
    Object.entries(object(value) || {}).filter(
      ([key]) => !['client_request_id', 'budget_limit'].includes(key),
    ),
  )
export function readClarificationChild(value: unknown, pending: PendingClarification): Task | undefined {
  const row = object(value),
    payload = object(row?.payload),
    result = object(row?.result),
    receipt = object(result?.author_source_clarification),
    source = pending.source,
    preview = pending.preview,
    entry = object(receipt?.source_entry),
    budget = readFlowBudget(receipt?.budget)
  const combined = `${preview.current_source.text}\n\n【作者本章补充澄清；与原话分开保存】\n${pending.request.clarification}`
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
    !result ||
    !receipt ||
    !entry ||
    !budget ||
    result.author_source_clarification_version !== AUTHOR_CLARIFICATION_VERSION ||
    result.continued_from !== source.id ||
    !same(inherited(payload), inherited(source.payload)) ||
    payload.client_request_id !== pending.request.client_request_id ||
    payload.budget_limit !== preview.budget.remaining_run_limit ||
    !same(budget, preview.budget) ||
    !same(result.plan, source.result.plan) ||
    !integer(result.repair_round ?? 0) ||
    Number(result.repair_round ?? 0) < (source.result.repair_round ?? 0) ||
    ['author_contract_snapshot', 'genre_profile_snapshot'].some(
      (key) => !same(result[key], object(source.result)?.[key]),
    ) ||
    Object.entries(policies(source)).some(([key, value]) => !same(result[key], value)) ||
    receipt.version !== AUTHOR_CLARIFICATION_VERSION ||
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
    (preview.source_review.observation_failure !== undefined &&
      !same(receipt.observation_failure, preview.source_review.observation_failure.proof)) ||
    !same(receipt.request, pending.request) ||
    !same(receipt.policies, preview.policies) ||
    !same(receipt.account, preview.account) ||
    !same(receipt.original_source_entry, preview.original_source) ||
    !same(receipt.previous_source_entry, preview.current_source) ||
    !same(
      receipt.previous_source_receipt,
      object(object(source.result)?.authored_source_scope_receipts)?.[`chapter:${preview.chapter}`],
    ) ||
    !sourceEntry(entry, preview.chapter) ||
    entry.text !== combined ||
    receipt.author_literary_approval !== 'unverified'
  )
    return undefined
  return row as Task
}

export function readClarificationLookup(
  value: unknown,
  pending: PendingClarification,
): Task | null | undefined {
  const row = object(value)
  if (
    !row ||
    row.source_task_id !== pending.source.id ||
    row.client_request_id !== pending.request.client_request_id
  )
    return undefined
  return row.task === null ? null : readClarificationChild(row.task, pending)
}
