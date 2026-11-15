import { ApiError, type Artifact, type Chapter, type Task } from './api'
import { readPlotAudit, plotAuditState } from './plotAudit'
import { notTriggeredLabel } from './ConditionalReviewNotice'

export const SOURCE_CORRECTION_VERSION = 'source-interpretation-correction/1'
export const CORRECTED_RECHECK_VERSION = 'source-corrected-candidate-recheck/1'
export const SOURCE_CORRECTION_KIND = 'source_interpretation_correction'
export const CORRECTED_RECHECK_KIND = 'source_corrected_candidate_recheck'
export type Phase = 'source' | 'recheck'
export const correctionKind = (phase: Phase) =>
  phase === 'source' ? SOURCE_CORRECTION_KIND : CORRECTED_RECHECK_KIND
export const correctionPath = (phase: Phase) =>
  phase === 'source' ? 'source-interpretation-corrections' : 'recheck-source-correction'
export const correctionKindLabel = (kind: string) =>
  kind === SOURCE_CORRECTION_KIND
    ? '来源解释纠偏回执'
    : kind === CORRECTED_RECHECK_KIND
      ? '来源纠偏后的正文复检报告'
      : undefined
export const isCorrectionReceiptKind = (kind: string) =>
  kind === SOURCE_CORRECTION_KIND || kind === CORRECTED_RECHECK_KIND
export const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
const string = (value: unknown, max = 10000): value is string =>
  typeof value === 'string' && [...value].length <= max
const nonempty = (value: unknown, max = 10000): value is string => string(value, max) && !!value.trim()
const integer = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
const hash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/u.test(value)
const strings = (value: unknown, max = 100): value is string[] =>
  Array.isArray(value) && value.length <= max && value.every((v) => nonempty(v))
export const canonical = (value: unknown): string =>
  JSON.stringify(value, (_key, row: unknown) =>
    object(row)
      ? Object.fromEntries(
          Object.keys(row)
            .sort()
            .map((key) => [key, row[key]]),
        )
      : row,
  )
export async function digest(text: string) {
  if (!globalThis.crypto?.subtle) throw new Error('本窗口无法核对内容摘要，未提交模型任务。')
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)))]
    .map((v) => v.toString(16).padStart(2, '0'))
    .join('')
}
export type Owner = {
  novelId: string
  branchId: string
  artifactId: string
  chapterId: string
  baseRevision: number
  candidateRevision: number
  workflowId: string
}
export const ownerKey = (owner: Owner) => canonical([owner.novelId, owner.branchId, owner.artifactId])
export function correctionOwner(artifact: Artifact, novelId: string, branchId: string): Owner | undefined {
  if (
    artifact.kind !== 'chapter_draft' ||
    artifact.status !== 'candidate' ||
    artifact.branch_id !== branchId ||
    !artifact.chapter_id ||
    !artifact.task_id ||
    !integer(artifact.base_revision) ||
    !integer(artifact.candidate_revision) ||
    typeof artifact.payload.text !== 'string'
  )
    return
  return {
    novelId,
    branchId,
    artifactId: artifact.id,
    chapterId: artifact.chapter_id,
    baseRevision: artifact.base_revision,
    candidateRevision: artifact.candidate_revision,
    workflowId: artifact.task_id,
  }
}
export function sourceCorrectionEligibility(
  artifact: Artifact,
  source: unknown,
  chapter: Chapter | undefined,
  branchId: string,
  unsaved = false,
) {
  if (unsaved) return '请先保存当前原稿与候选编辑，再核对来源解释。'
  if (
    artifact.kind !== 'chapter_draft' ||
    artifact.status !== 'candidate' ||
    artifact.branch_id !== branchId ||
    !chapter ||
    chapter.id !== artifact.chapter_id ||
    chapter.revision !== artifact.base_revision
  )
    return '仅当前章节和原稿版本吻合的未采纳整章候选可核对来源解释。'
  if (
    !object(source) ||
    !object(source.payload) ||
    !object(source.result) ||
    source.id !== artifact.task_id ||
    source.branch_id !== branchId ||
    source.kind !== 'workflow' ||
    source.status !== 'needs_review' ||
    source.payload.mode !== 'assisted' ||
    source.payload.plot_contract_check !== true ||
    source.result.waiting_artifact !== artifact.id ||
    source.result.continued_to !== undefined
  )
    return '仅半自动任务当前待审候选可使用来源解释纠偏；全自动及旧来源不升级。'
  return undefined
}
export type CoreItem = {
  id: string
  author_evidence: string
  actor: string
  action: string
  object: string
  direction: string
  result: string
  certainty: 'explicit' | 'ambiguous'
  transfer_object: string
  supplier: string
  recipient: string
  transfer_scope: 'attempted' | 'completed' | 'planned' | 'none'
  recipient_scope: 'only' | 'unrestricted' | 'unspecified'
  scope_condition: string
}
const coreStrings = [
  'author_evidence',
  'actor',
  'action',
  'object',
  'direction',
  'result',
  'transfer_object',
  'supplier',
  'recipient',
  'scope_condition',
] as const
function coreItems(value: unknown, source: string, withId: boolean): CoreItem[] | undefined {
  if (!Array.isArray(value) || value.length < 1 || value.length > 12) return
  const output: CoreItem[] = []
  for (const [index, row] of value.entries()) {
    const id = `P${String(index + 1).padStart(2, '0')}`
    const allowed = [
      ...coreStrings,
      'certainty',
      'transfer_scope',
      'recipient_scope',
      ...(withId ? ['id'] : []),
    ]
    if (
      !object(row) ||
      Object.keys(row).some((key) => !allowed.includes(key)) ||
      (withId && row.id !== id) ||
      coreStrings.some(
        (key) =>
          !string(
            row[key],
            key === 'author_evidence' || key === 'scope_condition'
              ? 800
              : key === 'direction' || key === 'result'
                ? 400
                : 200,
          ),
      ) ||
      !nonempty(row.author_evidence, 800) ||
      !source.includes(row.author_evidence) ||
      (row.certainty !== 'explicit' && row.certainty !== 'ambiguous') ||
      !['attempted', 'completed', 'planned', 'none'].some((v) => row.transfer_scope === v) ||
      !['only', 'unrestricted', 'unspecified'].some((v) => row.recipient_scope === v)
    )
      return
    output.push({
      id,
      ...Object.fromEntries(coreStrings.map((key) => [key, row[key]])),
      certainty: row.certainty,
      transfer_scope: row.transfer_scope,
      recipient_scope: row.recipient_scope,
    } as CoreItem)
  }
  return output
}
export type SourcePreview = {
  version: typeof SOURCE_CORRECTION_VERSION
  target: string
  candidate_revision: number
  source_text: string
  items: CoreItem[]
  source_text_sha256: string
  original_contract_sha256: string
  basis_sha256: string
  story_revision: number
  source_workflow_id: string
  notice: string
}
export function readSourcePreview(value: unknown, owner: Owner): SourcePreview | undefined {
  if (
    !object(value) ||
    value.version !== SOURCE_CORRECTION_VERSION ||
    value.target !== owner.artifactId ||
    value.candidate_revision !== owner.candidateRevision ||
    value.source_workflow_id !== owner.workflowId ||
    !nonempty(value.source_text, 50000) ||
    !hash(value.source_text_sha256) ||
    !hash(value.original_contract_sha256) ||
    !hash(value.basis_sha256) ||
    !integer(value.story_revision) ||
    !nonempty(value.notice)
  )
    return
  const items = coreItems(value.items, value.source_text, true)
  return items ? ({ ...value, items } as SourcePreview) : undefined
}
export type RecheckPreview = {
  version: typeof CORRECTED_RECHECK_VERSION
  target: string
  candidate_revision: number
  receipt_id: string
  receipt_payload_sha256: string
  basis_sha256: string
  story_revision: number
  source_text: string
  corrected_contract: { items: CoreItem[]; note: string }
  notice: string
}
export type ProposalItem = {
  item_id: string
  actor: string
  statement_kind: 'story_event' | 'narrative_requirement'
  certainty: 'explicit' | 'ambiguous'
  interpretation_reason: string
  evidence: string
  evidence_source: { source_id: string; start: number; end: number }
}
type PreservationCheck = {
  item_id: string
  result: 'pass' | 'fail' | 'unknown'
  evidence: string
  reason: string
}
export type SourceReceipt = {
  version: typeof SOURCE_CORRECTION_VERSION
  state: 'ready' | 'unverified'
  snapshot: {
    artifact_id: string
    candidate_revision: number
    chapter_id: string
    chapter_revision: number
    story_revision: number
    source_workflow_id: string
    content_sha256: string
    source_text_sha256: string
    original_contract_sha256: string
    basis_sha256: string
    item_ids: string[]
    fixed: string[]
    plot: { plot: string; number: number; title: string }
    prepared_contract: { items: CoreItem[]; note: string }
  }
  proposal: { items: ProposalItem[] }
  corrected_contract: { items: CoreItem[]; note: string }
  preservation: {
    item_checks: PreservationCheck[]
    fixed_checks: {
      condition_id: string
      result: 'pass' | 'fail' | 'unknown'
      evidence: string
      reason: string
    }[]
    summary: string
  }
  note: string
}
function checkResult(value: unknown): value is 'pass' | 'fail' | 'unknown' {
  return value === 'pass' || value === 'fail' || value === 'unknown'
}
function utf16Boundary(text: string, position: number) {
  if (position < 0 || position > text.length) return false
  const before = text.charCodeAt(position - 1),
    after = text.charCodeAt(position)
  return !(before >= 0xd800 && before <= 0xdbff && after >= 0xdc00 && after <= 0xdfff)
}
function sourceEvidence(row: Record<string, unknown>, text: string) {
  const citation = row.evidence_source
  return (
    nonempty(row.evidence, 800) &&
    object(citation) &&
    nonempty(citation.source_id, 100) &&
    integer(citation.start) &&
    integer(citation.end) &&
    citation.end > citation.start &&
    utf16Boundary(text, citation.start) &&
    utf16Boundary(text, citation.end) &&
    text.slice(citation.start, citation.end) === row.evidence
  )
}
export function readSourceReceipt(value: unknown): SourceReceipt | undefined {
  if (
    !object(value) ||
    value.version !== SOURCE_CORRECTION_VERSION ||
    (value.state !== 'ready' && value.state !== 'unverified') ||
    !nonempty(value.note) ||
    !object(value.snapshot) ||
    !object(value.proposal) ||
    !object(value.corrected_contract) ||
    !object(value.preservation)
  )
    return
  const snapshot = value.snapshot,
    plot = snapshot.plot,
    original = snapshot.prepared_contract,
    preservation = value.preservation
  if (
    snapshot.source_kind !== 'author_plot' ||
    !nonempty(snapshot.artifact_id, 100) ||
    !integer(snapshot.candidate_revision) ||
    !nonempty(snapshot.chapter_id, 100) ||
    !integer(snapshot.chapter_revision) ||
    !integer(snapshot.story_revision) ||
    !nonempty(snapshot.source_workflow_id, 100) ||
    !hash(snapshot.content_sha256) ||
    !hash(snapshot.source_text_sha256) ||
    !hash(snapshot.original_contract_sha256) ||
    !hash(snapshot.basis_sha256) ||
    !strings(snapshot.item_ids, 12) ||
    snapshot.item_ids.length === 0 ||
    new Set(snapshot.item_ids).size !== snapshot.item_ids.length ||
    !strings(snapshot.fixed) ||
    !object(plot) ||
    !nonempty(plot.plot, 50000) ||
    !integer(plot.number) ||
    plot.number < 1 ||
    !string(plot.title) ||
    !object(original) ||
    !string(original.note, 2000) ||
    !string(value.corrected_contract.note, 2000) ||
    value.corrected_contract.note !== original.note ||
    !Array.isArray(value.proposal.items) ||
    !Array.isArray(preservation.item_checks) ||
    !Array.isArray(preservation.fixed_checks) ||
    !nonempty(preservation.summary, 2000)
  )
    return
  const old = coreItems(original.items, plot.plot, false),
    corrected = coreItems(value.corrected_contract.items, plot.plot, false)
  if (
    !old ||
    !corrected ||
    old.length !== corrected.length ||
    value.proposal.items.length !== old.length ||
    preservation.item_checks.length !== old.length ||
    preservation.fixed_checks.length !== snapshot.fixed.length ||
    snapshot.item_ids.some((id) => !old.some((item) => item.id === id))
  )
    return
  for (let i = 0; i < old.length; i++) {
    const before = old[i],
      after = corrected[i],
      proposal = value.proposal.items[i],
      check = preservation.item_checks[i]
    if (
      !object(proposal) ||
      proposal.item_id !== before.id ||
      proposal.actor !== after.actor ||
      proposal.certainty !== after.certainty ||
      (proposal.statement_kind !== 'story_event' && proposal.statement_kind !== 'narrative_requirement') ||
      !nonempty(proposal.interpretation_reason, 1200) ||
      !sourceEvidence(proposal, plot.plot) ||
      !object(check) ||
      check.item_id !== before.id ||
      !checkResult(check.result) ||
      !nonempty(check.reason, 1200) ||
      !sourceEvidence(check, plot.plot)
    )
      return
    for (const key of [...coreStrings, 'transfer_scope', 'recipient_scope'] as const)
      if (key !== 'actor' && before[key] !== after[key]) return
    if (
      !snapshot.item_ids.includes(before.id) &&
      (before.actor !== after.actor ||
        before.certainty !== after.certainty ||
        proposal.statement_kind !== 'story_event')
    )
      return
    if (proposal.statement_kind === 'narrative_requirement' && proposal.actor !== '') return
  }
  for (const [i, row] of preservation.fixed_checks.entries())
    if (
      !object(row) ||
      row.condition_id !== `FIX${String(i + 1).padStart(3, '0')}` ||
      !checkResult(row.result) ||
      row.evidence !== snapshot.fixed[i] ||
      !nonempty(row.reason, 1200)
    )
      return
  const ready =
    value.proposal.items.every((row) => object(row) && row.certainty === 'explicit') &&
    [...preservation.item_checks, ...preservation.fixed_checks].every(
      (row) => object(row) && row.result === 'pass',
    )
  if ((value.state === 'ready') !== ready) return
  return {
    ...value,
    snapshot: {
      ...snapshot,
      plot,
      prepared_contract: { items: old, note: original.note },
    },
    corrected_contract: { items: corrected, note: original.note },
  } as SourceReceipt
}
export function readRecheckPreview(
  value: unknown,
  owner: Owner,
  receiptId: string,
  receipt: SourceReceipt,
): RecheckPreview | undefined {
  if (
    !object(value) ||
    value.version !== CORRECTED_RECHECK_VERSION ||
    value.target !== owner.artifactId ||
    value.candidate_revision !== owner.candidateRevision ||
    value.receipt_id !== receiptId ||
    !hash(value.receipt_payload_sha256) ||
    !hash(value.basis_sha256) ||
    value.story_revision !== receipt.snapshot.story_revision ||
    value.source_text !== receipt.snapshot.plot.plot ||
    !object(value.corrected_contract) ||
    value.corrected_contract.note !== receipt.corrected_contract.note ||
    !nonempty(value.notice) ||
    receipt.state !== 'ready'
  )
    return
  const items = coreItems(value.corrected_contract.items, value.source_text, false)
  return items && canonical(items) === canonical(receipt.corrected_contract.items)
    ? ({
        ...value,
        corrected_contract: { items, note: receipt.corrected_contract.note },
      } as RecheckPreview)
    : undefined
}
export type SourceRequest = {
  candidate_revision: number
  preview_basis_sha256: string
  account: string
  client_request_id: string
  item_ids: string[]
  reason: string
  budget_limit?: number
}
export type RecheckRequest = {
  candidate_revision: number
  preview_basis_sha256: string
  receipt_id: string
  account: string
  client_request_id: string
  budget_limit?: number
}
export type Capsule = {
  client_signature?: string
  phase: Phase
  owner: Owner
  preview: SourcePreview | RecheckPreview
  request: SourceRequest | RecheckRequest
}
function readOwner(value: unknown): Owner | undefined {
  if (
    !object(value) ||
    !['novelId', 'branchId', 'artifactId', 'chapterId', 'workflowId'].every((key) =>
      nonempty(value[key], 100),
    ) ||
    !integer(value.baseRevision) ||
    !integer(value.candidateRevision)
  )
    return
  return value as Owner
}
export function readCapsule(value: unknown, scope: string): Capsule | undefined {
  if (
    !object(value) ||
    (value.phase !== 'source' && value.phase !== 'recheck') ||
    (value.client_signature !== undefined && !hash(value.client_signature))
  )
    return
  const owner = readOwner(value.owner),
    request = value.request
  if (
    !owner ||
    ownerKey(owner) !== scope ||
    !object(request) ||
    request.candidate_revision !== owner.candidateRevision ||
    !hash(request.preview_basis_sha256) ||
    !nonempty(request.account, 100) ||
    !nonempty(request.client_request_id, 128) ||
    request.client_request_id.length < 8 ||
    (request.budget_limit !== undefined &&
      (typeof request.budget_limit !== 'number' ||
        !Number.isFinite(request.budget_limit) ||
        request.budget_limit <= 0))
  )
    return
  const allowed = [
    'candidate_revision',
    'preview_basis_sha256',
    'account',
    'client_request_id',
    'budget_limit',
    ...(value.phase === 'source' ? ['item_ids', 'reason'] : ['receipt_id']),
  ]
  if (Object.keys(request).some((key) => !allowed.includes(key))) return
  if (value.phase === 'source') {
    const preview = readSourcePreview(value.preview, owner)
    if (
      !preview ||
      preview.basis_sha256 !== request.preview_basis_sha256 ||
      !strings(request.item_ids, 12) ||
      !request.item_ids.length ||
      new Set(request.item_ids).size !== request.item_ids.length ||
      request.item_ids.some((id) => !preview.items.some((item) => item.id === id)) ||
      !string(request.reason, 2000)
    )
      return
    return {
      phase: 'source',
      owner,
      preview,
      ...(hash(value.client_signature) ? { client_signature: value.client_signature } : {}),
      request: request as SourceRequest,
    }
  }
  const preview = value.preview
  if (
    !object(preview) ||
    preview.version !== CORRECTED_RECHECK_VERSION ||
    preview.target !== owner.artifactId ||
    preview.candidate_revision !== owner.candidateRevision ||
    preview.basis_sha256 !== request.preview_basis_sha256 ||
    !nonempty(request.receipt_id, 100) ||
    preview.receipt_id !== request.receipt_id ||
    !hash(preview.receipt_payload_sha256) ||
    !integer(preview.story_revision) ||
    !nonempty(preview.source_text, 50000) ||
    !nonempty(preview.notice) ||
    !object(preview.corrected_contract) ||
    !string(preview.corrected_contract.note, 2000)
  )
    return
  const items = coreItems(preview.corrected_contract.items, preview.source_text, true)
  return items
    ? {
        phase: 'recheck',
        ...(hash(value.client_signature) ? { client_signature: value.client_signature } : {}),
        owner,
        preview: {
          ...preview,
          corrected_contract: { items, note: preview.corrected_contract.note },
        } as RecheckPreview,
        request: request as RecheckRequest,
      }
    : undefined
}
export function readCorrectionTask(value: unknown, capsule: Capsule): Task | undefined {
  if (
    !object(value) ||
    !nonempty(value.id, 100) ||
    value.kind !== correctionKind(capsule.phase) ||
    value.novel_id !== capsule.owner.novelId ||
    value.branch_id !== capsule.owner.branchId ||
    !['queued', 'running', 'succeeded', 'blocked_runtime', 'cancelled'].some(
      (status) => value.status === status,
    ) ||
    typeof value.created !== 'number' ||
    !Number.isFinite(value.created) ||
    (value.cancel !== 0 && value.cancel !== 1) ||
    !string(value.error) ||
    !object(value.payload) ||
    !object(value.result)
  )
    return
  const payload = value.payload,
    result = value.result,
    request = capsule.request
  const snapshot =
    result[capsule.phase === 'source' ? 'source_correction_snapshot' : 'source_corrected_recheck_snapshot']
  if (
    payload.novel_id !== capsule.owner.novelId ||
    payload.branch_id !== capsule.owner.branchId ||
    payload.chapter_id !== capsule.owner.chapterId ||
    payload.base_revision !== capsule.owner.baseRevision ||
    payload.source_candidate_id !== capsule.owner.artifactId ||
    payload.candidate_revision !== capsule.owner.candidateRevision ||
    payload.client_request_id !== request.client_request_id ||
    payload.account !== request.account ||
    payload.preview_basis_sha256 !== request.preview_basis_sha256 ||
    (payload.budget_limit ?? undefined) !== request.budget_limit ||
    (payload.source_identity_context ?? false) !== false ||
    !object(snapshot) ||
    snapshot.artifact_id !== capsule.owner.artifactId ||
    snapshot.candidate_revision !== capsule.owner.candidateRevision ||
    snapshot.source_workflow_id !== capsule.owner.workflowId ||
    snapshot.basis_sha256 !== request.preview_basis_sha256 ||
    snapshot.version !== capsule.preview.version
  )
    return
  if (
    capsule.phase === 'source' &&
    (result.source_correction_version !== SOURCE_CORRECTION_VERSION ||
      !('item_ids' in request) ||
      canonical(payload.item_ids) !== canonical(request.item_ids) ||
      payload.reason !== request.reason ||
      canonical(snapshot.item_ids) !== canonical(request.item_ids))
  )
    return
  if (
    capsule.phase === 'recheck' &&
    (result.source_corrected_recheck_version !== CORRECTED_RECHECK_VERSION ||
      !('receipt_id' in request) ||
      payload.receipt_id !== request.receipt_id ||
      snapshot.receipt_id !== request.receipt_id)
  )
    return
  return value as Task
}
export type OperationState = {
  status: 'idle' | 'sending' | 'uncertain' | 'rejected' | 'confirmed' | 'unavailable'
  pending?: Capsule
  task?: Task
  message: string
}
export function canAdvanceCorrectionPhase({
  nextPhase,
  currentPhase,
  currentBasis,
  hasIncoming,
  inputAvailable,
  stale,
  operation,
  task,
  receipt,
  receiptCurrent,
}: {
  nextPhase: Phase
  currentPhase?: Phase
  currentBasis?: string
  hasIncoming: boolean
  inputAvailable: boolean
  stale: boolean
  operation: OperationState
  task?: Task
  receipt?: { artifact: Pick<Artifact, 'id' | 'task_id'>; data: SourceReceipt }
  receiptCurrent: boolean
}) {
  // Advancing a completed stage is different from confirming changed source input.
  return (
    nextPhase === 'recheck' &&
    currentPhase === 'source' &&
    !hasIncoming &&
    inputAvailable &&
    !stale &&
    receiptCurrent &&
    operation.status === 'confirmed' &&
    operation.pending?.phase === 'source' &&
    task?.kind === SOURCE_CORRECTION_KIND &&
    task.status === 'succeeded' &&
    task.id === operation.task?.id &&
    receipt?.artifact.task_id === task.id &&
    task.result.artifact_id === receipt.artifact.id &&
    receipt.data.state === 'ready' &&
    currentBasis === operation.pending.request.preview_basis_sha256 &&
    currentBasis === receipt.data.snapshot.basis_sha256
  )
}
export function createCorrectionOperation(
  scope: string,
  call: (path: string, method?: string, body?: unknown) => Promise<unknown>,
  storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>,
  publish: (state: OperationState) => void,
) {
  const key = `ainovel.source-correction.1:${scope}`
  let alive = true,
    flight = false,
    sequence = 0
  let state: OperationState = { status: 'idle', message: '' }
  const emit = (next: OperationState) => {
    state = next
    if (alive) publish(next)
  }
  try {
    const raw = storage.getItem(key)
    if (raw) {
      const pending = readCapsule(JSON.parse(raw), scope)
      if (!pending) throw new Error('invalid capsule')
      state = {
        status: 'uncertain',
        pending,
        message: '已恢复原提交标识；仅查询原提交，不重发模型请求。',
      }
    }
  } catch {
    state = {
      status: 'unavailable',
      message: '无法完整读取稳定提交记录，未创建新标识；请先核对任务历史。',
    }
  }
  async function send(pending: Capsule, lookup: boolean) {
    if (flight || !alive) return
    flight = true
    const epoch = ++sequence
    emit({
      status: 'sending',
      pending,
      message: lookup ? '正在查询原提交…' : '正在提交独立任务…',
    })
    try {
      const raw = await call(
        `/artifacts/${encodeURIComponent(pending.owner.artifactId)}/${correctionPath(pending.phase)}${lookup ? `/${encodeURIComponent(pending.request.client_request_id)}` : ''}`,
        lookup ? 'GET' : 'POST',
        lookup ? undefined : pending.request,
      )
      const task = readCorrectionTask(raw, pending)
      if (!task) throw new Error('任务回执未对应原提交；保留原标识继续查询，未创建另一任务。')
      if (alive && epoch === sequence)
        emit({
          status: 'confirmed',
          pending,
          task,
          message: '独立任务已确认；未采纳或恢复正文。',
        })
    } catch (failure) {
      if (!alive || epoch !== sequence) return
      const message = failure instanceof Error ? failure.message : '结果无法确认，原提交记录已保留。'
      const rejected =
        !lookup &&
        failure instanceof ApiError &&
        failure.status >= 400 &&
        failure.status < 500 &&
        failure.code !== 'invalid_response'
      emit({
        status: rejected ? 'rejected' : 'uncertain',
        pending,
        message:
          lookup && failure instanceof ApiError && failure.status === 404
            ? '暂未查到原提交，不代表它永未建立；保留标识继续查询。'
            : message,
      })
    } finally {
      flight = false
    }
  }
  return {
    get state() {
      return state
    },
    async start(capsule: Capsule) {
      if (!alive || flight || state.status !== 'idle' || !readCapsule(capsule, scope)) return
      try {
        storage.setItem(key, JSON.stringify(capsule))
      } catch {
        emit({
          status: 'unavailable',
          message: '无法持久保存稳定标识，未派发模型调用。',
        })
        return
      }
      await send(capsule, false)
    },
    async lookup() {
      if (state.pending && !['idle', 'unavailable'].includes(state.status)) await send(state.pending, true)
    },
    releaseKnown() {
      if (flight || !['confirmed', 'rejected', 'idle'].includes(state.status)) return false
      try {
        storage.removeItem(key)
      } catch {
        emit({ ...state, message: '无法清理已确认提交标记，仍保留原标识。' })
        return false
      }
      emit({ status: 'idle', message: '' })
      return true
    },
    dispose() {
      alive = false
      sequence++
    },
  }
}

export type CorrectedReport = {
  version: typeof CORRECTED_RECHECK_VERSION
  target: {
    kind: 'candidate'
    artifact_id: string
    revision: number
    chapter_id: string
    base_revision: number
    content_sha256: string
  }
  receipt_id: string
  review: Record<string, unknown>
  plot_audit: unknown
  review_passed: boolean
  memory_generated: boolean
  previous_candidate: Record<string, unknown>
  note: string
}
export function readCorrectedReport(value: unknown): CorrectedReport | undefined {
  if (
    !object(value) ||
    value.version !== CORRECTED_RECHECK_VERSION ||
    !object(value.target) ||
    value.target.kind !== 'candidate' ||
    !nonempty(value.target.artifact_id, 100) ||
    !integer(value.target.revision) ||
    !nonempty(value.target.chapter_id, 100) ||
    !integer(value.target.base_revision) ||
    !hash(value.target.content_sha256) ||
    !nonempty(value.receipt_id, 100) ||
    !object(value.review) ||
    !Array.isArray(value.review.checks) ||
    !Array.isArray(value.review.findings) ||
    !nonempty(value.review.summary) ||
    !object(value.previous_candidate) ||
    typeof value.previous_candidate.text !== 'string' ||
    !nonempty(value.note) ||
    typeof value.review_passed !== 'boolean' ||
    typeof value.memory_generated !== 'boolean' ||
    (!value.review_passed && value.memory_generated)
  )
    return
  const plot = readPlotAudit(value.plot_audit)
  if (
    !plot?.binding ||
    plot.binding.candidate_revision !== value.target.revision ||
    plot.binding.candidate_sha256 !== value.target.content_sha256 ||
    canonical(value.review.plot_audit) !== canonical(value.plot_audit)
  )
    return
  if (
    value.review.checks.some(
      (row) =>
        !object(row) ||
        !nonempty(row.condition) ||
        !['pass', 'fail', 'unknown', 'not_triggered'].some((v) => row.result === v) ||
        !string(row.evidence) ||
        (row.evidence !== '' && !row.evidence.trim()) ||
        ((row.result === 'pass' || row.result === 'fail') && !row.evidence.trim()),
    )
  )
    return
  if (
    value.review.findings.some(
      (row) =>
        !object(row) ||
        (row.severity !== 'blocker' && row.severity !== 'suggestion') ||
        !integer(row.chapter) ||
        row.chapter < 1 ||
        !string(row.quote) ||
        !nonempty(row.reason) ||
        !string(row.repair),
    )
  )
    return
  if (
    value.review_passed &&
    (plotAuditState(plot, {
      revision: value.target.revision,
      sha256: value.target.content_sha256,
    }) !== 'model_pass' ||
      value.review.findings.some((row) => object(row) && row.severity === 'blocker') ||
      value.review.checks.some(
        (row) =>
          !object(row) ||
          (row.result !== 'pass' &&
            !(
              row.result === 'not_triggered' &&
              notTriggeredLabel(row, value.review) === '本章未触发，整书待核对'
            )),
      ))
  )
    return
  return value as CorrectedReport
}

export async function sourceReceiptTargetCurrent(
  receipt: SourceReceipt,
  artifact: Artifact,
  branchId: string,
  checksum: (text: string) => Promise<string> = digest,
) {
  return (
    artifact.id === receipt.snapshot.artifact_id &&
    artifact.branch_id === branchId &&
    artifact.chapter_id === receipt.snapshot.chapter_id &&
    artifact.base_revision === receipt.snapshot.chapter_revision &&
    artifact.candidate_revision === receipt.snapshot.candidate_revision &&
    artifact.status === 'candidate' &&
    typeof artifact.payload.text === 'string' &&
    (await checksum(artifact.payload.text)) === receipt.snapshot.content_sha256
  )
}
export async function capsuleMatchesCurrent(
  capsule: Capsule,
  signature: string,
  checksum: (text: string) => Promise<string> = digest,
) {
  return capsule.client_signature !== undefined && (await checksum(signature)) === capsule.client_signature
}
