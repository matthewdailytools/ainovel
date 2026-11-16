import { ApiError, type Artifact, type Chapter, type Task } from './api'
import { readPlotAudit } from './plotAudit'
import {
  canonical,
  object,
  ownerKey,
  readCoreItems,
  validSourceEvidence,
  readVersionedCorrectedReport,
  PREPARED_SCOPE_CORRECTION_VERSION as VERSION,
  PREPARED_SCOPE_CORRECTION_KIND as KIND,
  SCOPE_CORRECTED_RECHECK_VERSION as BODY_VERSION,
  CORRECTED_RECHECK_KIND,
  type Owner,
  type CoreItem,
  type SourcePreview,
  type SourceReceipt,
  type CorrectedReport,
} from './sourceCorrection'

export { VERSION as SCOPE_VERSION, KIND as SCOPE_KIND, BODY_VERSION as SCOPE_BODY_VERSION }
export type ScopePreview = Omit<SourcePreview, 'version'> & {
  version: typeof VERSION
  source_kind: 'workflow_plan'
  allowed_fields: ['recipient_scope', 'scope_condition']
}
type ScopeCheck = {
  item_id: string
  result: 'pass' | 'fail' | 'unknown'
  scope_only_fix_sufficient: 'yes' | 'no' | 'unknown'
  evidence: string
  reason: string
}
type ScopeObservation = {
  observation_id: string
  kind: string
  supplier: string
  recipient: string
  transfer_object: string
  transfer_scope: string
  evidence: string
  restriction_evidence: string
  condition: string
  reason: string
}
export type ScopeReceipt = {
  version: typeof VERSION
  source_kind: 'workflow_plan'
  state: 'ready' | 'unverified'
  snapshot: SourceReceipt['snapshot'] & { version: typeof VERSION; source_kind: 'workflow_plan' }
  inventory: {
    version: typeof VERSION
    input_sha256: string
    status: 'supported' | 'needs_review'
    sources: { source_id: string; text: string; start: number; end: number }[]
    inventory: {
      input_sha256: string
      observations: ScopeObservation[]
      unit_checks: { source_id: string; status: string; observation_ids: string[]; reason: string }[]
      note: string
    }
  }
  proposal: {
    items: {
      item_id: string
      recipient_scope: CoreItem['recipient_scope']
      scope_condition: string
      observation_ids: string[]
      status: 'supported' | 'unknown'
      reason: string
    }[]
  }
  corrected_contract: { items: CoreItem[]; note: string }
  preservation: null | {
    item_checks: ScopeCheck[]
    fixed_checks: SourceReceipt['preservation']['fixed_checks']
    summary: string
  }
  note: string
}
export type ScopeBodyPreview = {
  version: typeof BODY_VERSION
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
export type ScopeRequest = {
  candidate_revision: number
  preview_basis_sha256: string
  account: string
  client_request_id: string
  source_identity_context: false
  budget_limit?: number
} & ({ item_ids: string[]; reason: string } | { receipt_id: string })
export type ScopeCapsule = {
  phase: 'source' | 'recheck'
  owner: Owner
  preview: ScopePreview | ScopeBodyPreview
  request: ScopeRequest
  client_signature?: string
}
const hash = (v: unknown): v is string => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v)
const text = (v: unknown, max = 50000): v is string => typeof v === 'string' && [...v].length <= max
const nonempty = (v: unknown, max = 50000): v is string => text(v, max) && !!v.trim()
const integer = (v: unknown): v is number => Number.isSafeInteger(v) && Number(v) >= 0
const ids = (v: unknown): v is string[] =>
  Array.isArray(v) && v.every((x) => nonempty(x, 100)) && new Set(v).size === v.length
const check = (v: unknown) => v === 'pass' || v === 'fail' || v === 'unknown'
const scope = (v: unknown) => v === 'only' || v === 'unrestricted' || v === 'unspecified'
const same = (a: unknown, b: unknown) => canonical(a) === canonical(b)
const withoutScope = (item: CoreItem) =>
  Object.fromEntries(
    Object.entries(item).filter(([key]) => !['recipient_scope', 'scope_condition'].includes(key)),
  )

export function scopeEligibility(
  artifact: Artifact,
  source: unknown,
  chapter: Chapter | undefined,
  branchId: string,
  unsaved = false,
) {
  if (unsaved) return '请先保存原稿与候选编辑，再读取冻结AI章纲依据。'
  if (
    artifact.kind !== 'chapter_draft' ||
    artifact.status !== 'candidate' ||
    artifact.branch_id !== branchId ||
    !chapter ||
    chapter.id !== artifact.chapter_id ||
    chapter.revision !== artifact.base_revision
  )
    return '仅原稿版本吻合的当前未采纳整章候选可核对章纲范围。'
  if (
    !object(source) ||
    !object(source.payload) ||
    !object(source.result) ||
    source.id !== artifact.task_id ||
    source.branch_id !== branchId ||
    source.kind !== 'workflow' ||
    source.payload.mode !== 'automatic' ||
    source.payload.plot_contract_check !== true ||
    source.status !== 'needs_review' ||
    source.result.waiting_artifact !== artifact.id ||
    source.result.continued_to !== undefined
  )
    return '仅全自动任务当前待审候选可核对冻结AI章纲；不升级作者原话纠偏或其他旧来源。'
  return undefined
}
export function readScopePreview(value: unknown, owner: Owner): ScopePreview | undefined {
  if (
    !object(value) ||
    value.version !== VERSION ||
    value.source_kind !== 'workflow_plan' ||
    value.target !== owner.artifactId ||
    value.candidate_revision !== owner.candidateRevision ||
    value.source_workflow_id !== owner.workflowId ||
    !nonempty(value.source_text) ||
    !hash(value.source_text_sha256) ||
    !hash(value.original_contract_sha256) ||
    !hash(value.basis_sha256) ||
    !integer(value.story_revision) ||
    !nonempty(value.notice) ||
    !same(value.allowed_fields, ['recipient_scope', 'scope_condition'])
  )
    return
  const items = readCoreItems(value.items, value.source_text, true)
  return items ? ({ ...value, items } as ScopePreview) : undefined
}
export function readScopeReceipt(value: unknown): ScopeReceipt | undefined {
  if (
    !object(value) ||
    value.version !== VERSION ||
    value.source_kind !== 'workflow_plan' ||
    !['ready', 'unverified'].includes(String(value.state)) ||
    !object(value.snapshot) ||
    !object(value.inventory) ||
    !object(value.proposal) ||
    !object(value.corrected_contract) ||
    !text(value.note)
  )
    return
  const snapshot = value.snapshot,
    plot = object(snapshot.plot) ? snapshot.plot : undefined,
    original = object(snapshot.prepared_contract) ? snapshot.prepared_contract : undefined
  if (
    snapshot.version !== VERSION ||
    snapshot.source_kind !== 'workflow_plan' ||
    !plot ||
    !nonempty(plot.plot) ||
    !original ||
    !text(original.note) ||
    !integer(plot.number) ||
    !text(plot.title) ||
    !['artifact_id', 'chapter_id', 'source_workflow_id'].every((key) => nonempty(snapshot[key], 100)) ||
    !['candidate_revision', 'chapter_revision', 'story_revision'].every((key) => integer(snapshot[key])) ||
    !['content_sha256', 'source_text_sha256', 'original_contract_sha256', 'basis_sha256'].every((key) =>
      hash(snapshot[key]),
    ) ||
    !ids(snapshot.item_ids) ||
    !snapshot.item_ids.length ||
    !Array.isArray(snapshot.fixed) ||
    !snapshot.fixed.every((v) => nonempty(v))
  )
    return
  const selectedIds = snapshot.item_ids,
    fixed = snapshot.fixed
  const before = readCoreItems(original.items, plot.plot, false),
    after = readCoreItems(value.corrected_contract.items, plot.plot, false)
  if (
    !before ||
    !after ||
    before.length !== after.length ||
    value.corrected_contract.note !== original.note ||
    snapshot.item_ids.some((id) => !before.some((item) => item.id === id)) ||
    after.some(
      (item, index) =>
        !same(withoutScope(item), withoutScope(before[index])) ||
        (!selectedIds.includes(item.id) && !same(item, before[index])),
    )
  )
    return
  const inventory = value.inventory,
    raw = object(inventory.inventory) ? inventory.inventory : undefined
  if (
    inventory.version !== VERSION ||
    !hash(inventory.input_sha256) ||
    !raw ||
    raw.input_sha256 !== inventory.input_sha256 ||
    !['supported', 'needs_review'].includes(String(inventory.status)) ||
    !Array.isArray(inventory.sources) ||
    !inventory.sources.length ||
    !Array.isArray(raw.observations) ||
    raw.observations.length > 24 ||
    !Array.isArray(raw.unit_checks) ||
    !text(raw.note, 2000)
  )
    return
  const units = inventory.sources
  const sourceText = plot.plot
  if (
    units.some(
      (u, index) =>
        !object(u) ||
        u.source_id !== `SCOPE${String(index + 1).padStart(4, '0')}` ||
        !integer(u.start) ||
        !integer(u.end) ||
        u.start >= u.end ||
        !nonempty(u.text, 800) ||
        !validSourceEvidence(
          { evidence: u.text, evidence_source: { source_id: u.source_id, start: u.start, end: u.end } },
          sourceText,
        ),
    )
  )
    return
  const unitIds = units.map((u) => (u as Record<string, unknown>).source_id),
    observations = raw.observations
  if (
    observations.some(
      (o, index) =>
        !object(o) ||
        o.observation_id !== `O${String(index + 1).padStart(2, '0')}` ||
        ![
          'recipient_exclusivity',
          'named_destination',
          'specific_recipient_exclusion',
          'action_permission',
          'explicitly_unrestricted',
          'unknown',
        ].includes(String(o.kind)) ||
        !['supplier', 'recipient', 'transfer_object'].every((key) => text(o[key], 200)) ||
        !['restriction_evidence', 'condition'].every((key) => text(o[key], 800)) ||
        !nonempty(o.reason, 1200) ||
        !['attempted', 'completed', 'planned', 'none'].includes(String(o.transfer_scope)) ||
        !unitIds.includes(o.evidence) ||
        (o.restriction_evidence !== '' && !unitIds.includes(o.restriction_evidence)),
    )
  )
    return
  const observationIds = observations.map((o) => (o as Record<string, unknown>).observation_id)
  if (
    raw.unit_checks.length !== units.length ||
    raw.unit_checks.some(
      (u, index) =>
        !object(u) ||
        u.source_id !== unitIds[index] ||
        !['covered', 'not_scope_related', 'unknown'].includes(String(u.status)) ||
        !ids(u.observation_ids) ||
        u.observation_ids.some((id) => !observationIds.includes(id)) ||
        !nonempty(u.reason, 800) ||
        (u.status === 'covered') !== Boolean(u.observation_ids.length) ||
        u.observation_ids.some((id) => {
          const observation = observations.find((o) => object(o) && o.observation_id === id)
          return (
            !object(observation) ||
            (observation.evidence !== u.source_id && observation.restriction_evidence !== u.source_id)
          )
        }),
    )
  )
    return
  const referenced = new Set(
    raw.unit_checks.flatMap((u) => (object(u) && Array.isArray(u.observation_ids) ? u.observation_ids : [])),
  )
  if (observationIds.some((id) => !referenced.has(id))) return
  if (
    observations.some((o) => {
      if (!object(o)) return true
      const restriction = units.find((u) => object(u) && u.source_id === o.restriction_evidence)
      return (
        ([
          'recipient_exclusivity',
          'specific_recipient_exclusion',
          'action_permission',
          'explicitly_unrestricted',
        ].includes(String(o.kind)) &&
          !restriction) ||
        (o.kind === 'named_destination' && (o.restriction_evidence !== '' || o.condition !== '')) ||
        (o.condition !== '' &&
          (!object(restriction) ||
            typeof restriction.text !== 'string' ||
            typeof o.condition !== 'string' ||
            !restriction.text.includes(o.condition))) ||
        (o.kind === 'recipient_exclusivity' && o.transfer_scope === 'none')
      )
    })
  )
    return
  const supported =
    observations.every((o) => object(o) && o.kind !== 'unknown') &&
    raw.unit_checks.every((u) => object(u) && u.status !== 'unknown')
  if (
    inventory.status !== (supported ? 'supported' : 'needs_review') ||
    !Array.isArray(value.proposal.items) ||
    value.proposal.items.length !== before.length ||
    value.proposal.items.some(
      (p, index) =>
        !object(p) ||
        p.item_id !== before[index].id ||
        !scope(p.recipient_scope) ||
        !text(p.scope_condition, 800) ||
        !ids(p.observation_ids) ||
        p.observation_ids.some((id) => !observationIds.includes(id)) ||
        !['supported', 'unknown'].includes(String(p.status)) ||
        !nonempty(p.reason, 1200) ||
        p.recipient_scope !== after[index].recipient_scope ||
        p.scope_condition !== after[index].scope_condition,
    )
  )
    return
  const preservation = value.preservation
  if (preservation !== null) {
    if (
      !object(preservation) ||
      !Array.isArray(preservation.item_checks) ||
      preservation.item_checks.length !== before.length ||
      !Array.isArray(preservation.fixed_checks) ||
      preservation.fixed_checks.length !== snapshot.fixed.length ||
      !nonempty(preservation.summary, 2000)
    )
      return
    if (
      preservation.item_checks.some(
        (row, index) =>
          !object(row) ||
          row.item_id !== before[index].id ||
          !check(row.result) ||
          !['yes', 'no', 'unknown'].includes(String(row.scope_only_fix_sufficient)) ||
          !nonempty(row.reason, 1200) ||
          !validSourceEvidence(row, sourceText),
      )
    )
      return
    if (
      preservation.fixed_checks.some(
        (row, index) =>
          !object(row) ||
          row.condition_id !== `FIX${String(index + 1).padStart(3, '0')}` ||
          !check(row.result) ||
          !nonempty(row.reason, 1200) ||
          row.evidence !== fixed[index],
      )
    )
      return
  }
  const ready =
    supported &&
    value.proposal.items.every((p) => object(p) && p.status === 'supported') &&
    object(preservation) &&
    Array.isArray(preservation.item_checks) &&
    preservation.item_checks.every(
      (row) => object(row) && row.result === 'pass' && row.scope_only_fix_sufficient === 'yes',
    ) &&
    Array.isArray(preservation.fixed_checks) &&
    preservation.fixed_checks.every((row) => object(row) && row.result === 'pass')
  if ((value.state === 'ready') !== Boolean(ready)) return
  return {
    ...value,
    snapshot: { ...snapshot, plot, prepared_contract: { items: before, note: original.note } },
    corrected_contract: { items: after, note: original.note },
  } as ScopeReceipt
}
export function readScopeBodyPreview(
  value: unknown,
  owner: Owner,
  receiptId: string,
  receipt: ScopeReceipt,
): ScopeBodyPreview | undefined {
  if (
    !object(value) ||
    value.version !== BODY_VERSION ||
    value.target !== owner.artifactId ||
    value.candidate_revision !== owner.candidateRevision ||
    value.receipt_id !== receiptId ||
    !hash(value.receipt_payload_sha256) ||
    !hash(value.basis_sha256) ||
    !integer(value.story_revision) ||
    value.source_text !== receipt.snapshot.plot.plot ||
    !nonempty(value.notice) ||
    !object(value.corrected_contract)
  )
    return
  const items = readCoreItems(value.corrected_contract.items, value.source_text, false)
  const expected = receipt.corrected_contract.items
  return items &&
    expected &&
    same(items, expected) &&
    value.corrected_contract.note === receipt.corrected_contract.note
    ? (value as ScopeBodyPreview)
    : undefined
}
export function readScopeReport(value: unknown): CorrectedReport<typeof BODY_VERSION> | undefined {
  const report = readVersionedCorrectedReport(value, BODY_VERSION),
    audit = report && readPlotAudit(report.plot_audit)
  return report &&
    audit?.binding?.source.kind === 'workflow_plan' &&
    audit.source_flow_review?.version === 'source-flow-entailment/2'
    ? report
    : undefined
}
export function readScopeCapsule(value: unknown, key: string): ScopeCapsule | undefined {
  if (
    !object(value) ||
    !['source', 'recheck'].includes(String(value.phase)) ||
    !object(value.owner) ||
    !object(value.request) ||
    !object(value.preview)
  )
    return
  const owner = value.owner
  if (
    !['novelId', 'branchId', 'artifactId', 'chapterId', 'workflowId'].every((k) => nonempty(owner[k], 100)) ||
    !integer(owner.baseRevision) ||
    !integer(owner.candidateRevision) ||
    ownerKey(owner as Owner) !== key ||
    (value.client_signature !== undefined && !hash(value.client_signature))
  )
    return
  const request = value.request,
    preview = value.preview
  if (
    !nonempty(request.account, 100) ||
    !nonempty(request.client_request_id, 128) ||
    request.client_request_id.length < 8 ||
    request.candidate_revision !== owner.candidateRevision ||
    request.preview_basis_sha256 !== preview.basis_sha256 ||
    request.source_identity_context !== false ||
    (request.budget_limit !== undefined &&
      (typeof request.budget_limit !== 'number' ||
        !Number.isFinite(request.budget_limit) ||
        request.budget_limit <= 0))
  )
    return
  if (value.phase === 'source') {
    const parsed = readScopePreview(preview, owner as Owner)
    if (
      !parsed ||
      !ids(request.item_ids) ||
      !request.item_ids.length ||
      request.item_ids.some((id) => !parsed.items.some((item) => item.id === id)) ||
      !text(request.reason, 2000) ||
      Object.keys(request).some(
        (k) =>
          ![
            'candidate_revision',
            'preview_basis_sha256',
            'account',
            'client_request_id',
            'source_identity_context',
            'budget_limit',
            'item_ids',
            'reason',
          ].includes(k),
      )
    )
      return
  } else if (
    preview.version !== BODY_VERSION ||
    preview.target !== owner.artifactId ||
    preview.candidate_revision !== owner.candidateRevision ||
    !nonempty(request.receipt_id, 100) ||
    request.receipt_id !== preview.receipt_id ||
    !hash(preview.receipt_payload_sha256) ||
    !hash(preview.basis_sha256) ||
    !integer(preview.story_revision) ||
    !nonempty(preview.source_text) ||
    !nonempty(preview.notice) ||
    !object(preview.corrected_contract) ||
    !text(preview.corrected_contract.note) ||
    !readCoreItems(preview.corrected_contract.items, preview.source_text, false) ||
    Object.keys(request).some(
      (k) =>
        ![
          'candidate_revision',
          'preview_basis_sha256',
          'account',
          'client_request_id',
          'source_identity_context',
          'budget_limit',
          'receipt_id',
        ].includes(k),
    )
  )
    return
  return value as ScopeCapsule
}
export function readScopeTask(value: unknown, capsule: ScopeCapsule): Task | undefined {
  if (
    !object(value) ||
    !nonempty(value.id, 100) ||
    value.kind !== (capsule.phase === 'source' ? KIND : CORRECTED_RECHECK_KIND) ||
    value.novel_id !== capsule.owner.novelId ||
    value.branch_id !== capsule.owner.branchId ||
    !['queued', 'running', 'succeeded', 'blocked_runtime', 'cancelled'].includes(String(value.status)) ||
    typeof value.created !== 'number' ||
    !Number.isFinite(value.created) ||
    (value.cancel !== 0 && value.cancel !== 1) ||
    !text(value.error) ||
    !object(value.payload) ||
    !object(value.result)
  )
    return
  const payload = value.payload,
    result = value.result,
    request = capsule.request,
    snapshot =
      result[
        capsule.phase === 'source' ? 'prepared_source_scope_snapshot' : 'source_corrected_recheck_snapshot'
      ]
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
    payload.source_identity_context !== false ||
    !object(snapshot) ||
    snapshot.version !== capsule.preview.version ||
    snapshot.source_kind !== 'workflow_plan' ||
    snapshot.artifact_id !== capsule.owner.artifactId ||
    snapshot.candidate_revision !== capsule.owner.candidateRevision ||
    snapshot.chapter_id !== capsule.owner.chapterId ||
    snapshot.chapter_revision !== capsule.owner.baseRevision ||
    snapshot.story_revision !== capsule.preview.story_revision ||
    !object(snapshot.plot) ||
    snapshot.plot.plot !== capsule.preview.source_text ||
    snapshot.source_workflow_id !== capsule.owner.workflowId ||
    snapshot.basis_sha256 !== request.preview_basis_sha256
  )
    return
  if (
    capsule.phase === 'source' &&
    (payload.prepared_source_scope_version !== VERSION ||
      result.prepared_source_scope_version !== VERSION ||
      !('item_ids' in request) ||
      !same(payload.item_ids, request.item_ids) ||
      payload.reason !== request.reason ||
      !same(snapshot.item_ids, request.item_ids) ||
      !('source_text_sha256' in capsule.preview) ||
      snapshot.source_text_sha256 !== capsule.preview.source_text_sha256 ||
      snapshot.original_contract_sha256 !== capsule.preview.original_contract_sha256)
  )
    return
  if (
    capsule.phase === 'recheck' &&
    (result.source_corrected_recheck_version !== BODY_VERSION ||
      result.flow_source_entailment_version !== 'source-flow-entailment/2' ||
      snapshot.flow_source_entailment_version !== 'source-flow-entailment/2' ||
      !('receipt_id' in request) ||
      payload.receipt_id !== request.receipt_id ||
      snapshot.receipt_id !== request.receipt_id ||
      !('corrected_contract' in capsule.preview) ||
      !same(snapshot.prepared_contract, capsule.preview.corrected_contract) ||
      snapshot.receipt_payload_sha256 !== capsule.preview.receipt_payload_sha256)
  )
    return
  return value as Task
}
export type ScopeOperationState = {
  status: 'idle' | 'sending' | 'uncertain' | 'rejected' | 'confirmed' | 'unavailable'
  pending?: ScopeCapsule
  task?: Task
  message: string
}
export function createScopeOperation(
  key: string,
  call: (path: string, method?: string, body?: unknown) => Promise<unknown>,
  storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>,
  publish: (value: ScopeOperationState) => void,
) {
  const storageKey = 'ainovel.prepared-scope-correction.1:' + key
  let alive = true,
    flight = false,
    sequence = 0,
    state: ScopeOperationState = { status: 'idle', message: '' }
  const emit = (next: ScopeOperationState) => {
    state = next
    if (alive) publish(next)
  }
  try {
    const saved = storage.getItem(storageKey)
    if (saved) {
      const pending = readScopeCapsule(JSON.parse(saved), key)
      if (!pending) throw Error('invalid')
      state = { status: 'uncertain', pending, message: '已恢复原标识；仅查询，不重发模型请求。' }
    }
  } catch {
    state = { status: 'unavailable', message: '无法完整恢复稳定提交，原记录保留，不能新建标识。' }
  }
  async function send(pending: ScopeCapsule, lookup: boolean) {
    if (!alive || flight) return
    flight = true
    const token = ++sequence
    emit({ status: 'sending', pending, message: lookup ? '正在查询原回执…' : '正在提交独立任务…' })
    try {
      const target =
        pending.phase === 'source' ? 'prepared-source-scope-corrections' : 'recheck-source-correction'
      let raw = await call(
        `/artifacts/${encodeURIComponent(pending.owner.artifactId)}/${target}${lookup ? '?' + new URLSearchParams({ client_request_id: pending.request.client_request_id }) : ''}`,
        lookup ? 'GET' : 'POST',
        lookup ? undefined : pending.request,
      )
      if (lookup && pending.phase === 'source') {
        if (
          !object(raw) ||
          raw.source_candidate_id !== pending.owner.artifactId ||
          raw.client_request_id !== pending.request.client_request_id
        )
          throw Error('原回执目标不符。')
        if (raw.task === null) throw Error('此刻尚未查到原提交；保留原标识继续只读查询。')
        raw = raw.task
      }
      const task = readScopeTask(raw, pending)
      if (!task) throw Error('任务未对应冻结来源、请求或版本，原标识保留。')
      if (alive && token === sequence)
        emit({ status: 'confirmed', pending, task, message: '独立任务已确认；不采纳或恢复正文。' })
    } catch (error) {
      if (!alive || token !== sequence) return
      const rejected =
        !lookup &&
        error instanceof ApiError &&
        error.status >= 400 &&
        error.status < 500 &&
        error.code !== 'invalid_response'
      emit({
        status: rejected ? 'rejected' : 'uncertain',
        pending,
        message:
          error instanceof ApiError && lookup && error.status === 404
            ? '此刻尚未查到原提交，继续只查原标识。'
            : error instanceof Error
              ? error.message
              : '结果未知，原标识保留。',
      })
    } finally {
      flight = false
    }
  }
  return {
    get state() {
      return state
    },
    async start(pending: ScopeCapsule) {
      if (!alive || flight || state.status !== 'idle' || !readScopeCapsule(pending, key)) return
      try {
        storage.setItem(storageKey, JSON.stringify(pending))
      } catch {
        emit({ status: 'unavailable', message: '无法保存稳定标识，未派发。' })
        return
      }
      await send(pending, false)
    },
    async lookup() {
      if (state.pending && !['idle', 'unavailable'].includes(state.status)) await send(state.pending, true)
    },
    releaseKnown() {
      if (flight || !['idle', 'confirmed', 'rejected'].includes(state.status)) return false
      try {
        storage.removeItem(storageKey)
      } catch {
        emit({ ...state, message: '无法清理已知提交，原标识保留。' })
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
