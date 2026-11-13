export const PLOT_AUDIT_VERSION = 'author-plot-audit/2'
export const PLOT_AUDIT_V1_VERSION = 'author-plot-audit/1'
export const isBoundPlotAuditVersion = (value: unknown) =>
  value === PLOT_AUDIT_VERSION || value === PLOT_AUDIT_V1_VERSION
type SourceKind = 'author_instruction' | 'author_plot' | 'workflow_plan'
type OriginalPlotCheck = {
  item_id: string
  result: 'pass' | 'fail' | 'unknown'
  observed: string
  evidence: string
  reason: string
  repair: string
}
type PlotFlowObservation = {
  object_id: string
  supplier_id: string
  recipient_id: string
  stage: 'offered' | 'requested' | 'completed' | 'planned' | 'refused' | 'unknown'
  certainty: 'explicit' | 'ambiguous'
  evidence: string
  note: string
}
type PlotGuard = {
  code: string
  expected_supplier: string
  expected_recipient: string
  object: string
  recipient_scope?: 'only'
  scope_condition?: string
  observations?: PlotFlowObservation[]
  policy?: string
}
export type PlotAudit = {
  status: 'model_pass' | 'failed' | 'unverified'
  summary: string
  policy: string
  interpretation_note: string
  items: {
    id: string
    author_evidence: string
    actor: string
    action: string
    object: string
    direction: string
    result: string
    certainty: 'explicit' | 'ambiguous'
    recipient_scope?: 'only' | 'unrestricted' | 'unspecified'
    scope_condition?: string
  }[]
  checks: {
    item_id: string
    result: 'pass' | 'fail' | 'unknown'
    observed: string
    evidence: string
    reason: string
    repair: string
    original_model_check?: OriginalPlotCheck
    guard_override?: PlotGuard
  }[]
  findings: {
    severity: 'blocker' | 'suggestion'
    chapter: number
    quote: string
    reason: string
    repair: string
  }[]
  binding?: {
    version: string
    source: { kind: SourceKind; text_sha256: string }
    candidate_sha256: string
    candidate_revision: number
  }
  target?: { kind: 'saved_chapter'; chapter_id: string; revision: number; content_sha256: string }
  original_model_summary?: string
}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}
const hash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value)
const strings = (value: Record<string, unknown>, keys: string[]) =>
  keys.every((key) => typeof value[key] === 'string')

function readOriginalCheck(value: unknown): OriginalPlotCheck | undefined {
  if (
    !object(value) ||
    !strings(value, ['item_id', 'observed', 'evidence', 'reason', 'repair']) ||
    (value.result !== 'pass' && value.result !== 'fail' && value.result !== 'unknown')
  )
    return undefined
  return {
    item_id: String(value.item_id),
    result: value.result,
    observed: String(value.observed),
    evidence: String(value.evidence),
    reason: String(value.reason),
    repair: String(value.repair),
  }
}

function readGuard(value: unknown): PlotGuard | undefined {
  if (!object(value) || !strings(value, ['code', 'expected_supplier', 'expected_recipient', 'object']))
    return undefined
  const guard: PlotGuard = {
    code: String(value.code),
    expected_supplier: String(value.expected_supplier),
    expected_recipient: String(value.expected_recipient),
    object: String(value.object),
  }
  if (value.code !== 'restricted_recipient_unverified') return guard
  if (
    value.recipient_scope !== 'only' ||
    typeof value.scope_condition !== 'string' ||
    typeof value.policy !== 'string' ||
    !Array.isArray(value.observations) ||
    value.observations.length > 24
  )
    return undefined
  const observations: PlotFlowObservation[] = []
  for (const row of value.observations) {
    if (
      !object(row) ||
      !strings(row, ['object_id', 'supplier_id', 'recipient_id', 'evidence', 'note']) ||
      (row.stage !== 'offered' &&
        row.stage !== 'requested' &&
        row.stage !== 'completed' &&
        row.stage !== 'planned' &&
        row.stage !== 'refused' &&
        row.stage !== 'unknown') ||
      (row.certainty !== 'explicit' && row.certainty !== 'ambiguous')
    )
      return undefined
    observations.push({
      object_id: String(row.object_id),
      supplier_id: String(row.supplier_id),
      recipient_id: String(row.recipient_id),
      stage: row.stage,
      certainty: row.certainty,
      evidence: String(row.evidence),
      note: String(row.note),
    })
  }
  return {
    ...guard,
    recipient_scope: value.recipient_scope,
    scope_condition: value.scope_condition,
    policy: value.policy,
    observations,
  }
}

export function readPlotAudit(value: unknown): PlotAudit | undefined {
  if (
    !object(value) ||
    (value.status !== 'model_pass' && value.status !== 'failed' && value.status !== 'unverified') ||
    !strings(value, ['summary', 'policy', 'interpretation_note']) ||
    !Array.isArray(value.items) ||
    value.items.length > 12 ||
    !Array.isArray(value.checks) ||
    value.checks.length > 12 ||
    !Array.isArray(value.findings) ||
    (value.version !== undefined && typeof value.version !== 'string') ||
    (value.original_model_summary !== undefined && typeof value.original_model_summary !== 'string')
  )
    return undefined
  const items: PlotAudit['items'] = []
  const checks: PlotAudit['checks'] = []
  const findings: PlotAudit['findings'] = []
  for (const item of value.items) {
    if (
      !object(item) ||
      !strings(item, ['id', 'author_evidence', 'actor', 'action', 'object', 'direction', 'result']) ||
      !item.id ||
      (item.certainty !== 'explicit' && item.certainty !== 'ambiguous') ||
      (value.version === PLOT_AUDIT_VERSION &&
        ((item.recipient_scope !== 'only' &&
          item.recipient_scope !== 'unrestricted' &&
          item.recipient_scope !== 'unspecified') ||
          typeof item.scope_condition !== 'string'))
    )
      return undefined
    items.push({
      id: String(item.id),
      author_evidence: String(item.author_evidence),
      actor: String(item.actor),
      action: String(item.action),
      object: String(item.object),
      direction: String(item.direction),
      result: String(item.result),
      certainty: item.certainty,
      ...(value.version === PLOT_AUDIT_VERSION
        ? {
            recipient_scope: item.recipient_scope as 'only' | 'unrestricted' | 'unspecified',
            scope_condition: String(item.scope_condition),
          }
        : {}),
    })
  }
  for (const check of value.checks) {
    if (
      !object(check) ||
      !strings(check, ['item_id', 'observed', 'evidence', 'reason', 'repair']) ||
      (check.result !== 'pass' && check.result !== 'fail' && check.result !== 'unknown') ||
      (typeof check.reason === 'string' && !check.reason.trim()) ||
      (check.result !== 'unknown' &&
        ((typeof check.evidence === 'string' && !check.evidence.trim()) ||
          (typeof check.observed === 'string' && !check.observed.trim())))
    )
      return undefined
    const original =
      check.original_model_check === undefined ? undefined : readOriginalCheck(check.original_model_check)
    const guard = check.guard_override === undefined ? undefined : readGuard(check.guard_override)
    if (
      (check.original_model_check !== undefined && (!original || original.item_id !== check.item_id)) ||
      (check.guard_override !== undefined && !guard) ||
      (guard?.code === 'restricted_recipient_unverified' &&
        (value.version !== PLOT_AUDIT_VERSION || check.result !== 'unknown' || !original))
    )
      return undefined
    checks.push({
      item_id: String(check.item_id),
      result: check.result,
      observed: String(check.observed),
      evidence: String(check.evidence),
      reason: String(check.reason),
      repair: String(check.repair),
      ...(original ? { original_model_check: original } : {}),
      ...(guard ? { guard_override: guard } : {}),
    })
  }
  const itemIds = new Set(items.map((item) => item.id))
  if (
    itemIds.size !== items.length ||
    checks.length !== items.length ||
    new Set(checks.map((check) => check.item_id)).size !== checks.length ||
    checks.some((check) => !itemIds.has(check.item_id))
  )
    return undefined
  for (const finding of value.findings) {
    if (
      !object(finding) ||
      !strings(finding, ['quote', 'reason', 'repair']) ||
      (finding.severity !== 'blocker' && finding.severity !== 'suggestion') ||
      typeof finding.chapter !== 'number' ||
      !Number.isSafeInteger(finding.chapter) ||
      finding.chapter < 1
    )
      return undefined
    findings.push({
      severity: finding.severity,
      chapter: finding.chapter,
      quote: String(finding.quote),
      reason: String(finding.reason),
      repair: String(finding.repair),
    })
  }
  let binding: PlotAudit['binding']
  let target: PlotAudit['target']
  if (value.target !== undefined) {
    if (
      !object(value.target) ||
      value.target.kind !== 'saved_chapter' ||
      typeof value.target.chapter_id !== 'string' ||
      !value.target.chapter_id ||
      typeof value.target.revision !== 'number' ||
      !Number.isSafeInteger(value.target.revision) ||
      value.target.revision < 0 ||
      !hash(value.target.content_sha256)
    )
      return undefined
    target = {
      kind: value.target.kind,
      chapter_id: value.target.chapter_id,
      revision: value.target.revision,
      content_sha256: value.target.content_sha256,
    }
  }
  if (['source', 'candidate_sha256', 'candidate_revision'].some((key) => key in value)) {
    if (
      typeof value.version !== 'string' ||
      !object(value.source) ||
      (value.source.kind !== 'author_instruction' &&
        value.source.kind !== 'author_plot' &&
        value.source.kind !== 'workflow_plan') ||
      !hash(value.source.text_sha256) ||
      !hash(value.candidate_sha256) ||
      typeof value.candidate_revision !== 'number' ||
      !Number.isSafeInteger(value.candidate_revision) ||
      value.candidate_revision < 0
    )
      return undefined
    binding = {
      version: value.version,
      source: { kind: value.source.kind, text_sha256: value.source.text_sha256 },
      candidate_sha256: value.candidate_sha256,
      candidate_revision: value.candidate_revision,
    }
  }
  return {
    status: value.status,
    summary: String(value.summary),
    policy: String(value.policy),
    interpretation_note: String(value.interpretation_note),
    items,
    checks,
    findings,
    binding,
    target,
    ...(typeof value.original_model_summary === 'string'
      ? { original_model_summary: value.original_model_summary }
      : {}),
  }
}

export function plotAuditState(
  audit: PlotAudit,
  target: {
    revision?: number
    sha256?: string
    edited?: boolean
    hashPending?: boolean
    expectSavedChapter?: boolean
    savedChapter?: { id: string; revision: number; sha256?: string }
    baseRevision?: number | null
    targetStatus?: 'ready' | 'historical' | 'loading' | 'unavailable'
    expectedSha256?: string
    reportSavedChapter?: { chapter_id: string; revision: number; content_sha256: string }
  },
) {
  if (!audit.binding || !isBoundPlotAuditVersion(audit.binding.version)) return 'legacy'
  if (
    target.edited ||
    target.targetStatus === 'historical' ||
    (target.revision !== undefined && target.revision !== audit.binding.candidate_revision) ||
    (target.sha256 !== undefined && target.sha256 !== audit.binding.candidate_sha256) ||
    (target.sha256 !== undefined &&
      target.expectedSha256 !== undefined &&
      target.sha256 !== target.expectedSha256)
  )
    return 'historical'
  if (target.targetStatus === 'loading') return 'pending'
  if (target.targetStatus === 'unavailable') return 'unverified'
  const savedTarget = audit.target ?? target.reportSavedChapter
  if (target.expectSavedChapter || savedTarget) {
    if (
      !savedTarget ||
      !target.savedChapter ||
      target.savedChapter.id !== savedTarget.chapter_id ||
      target.savedChapter.revision !== savedTarget.revision ||
      (target.baseRevision !== undefined && target.baseRevision !== savedTarget.revision) ||
      (target.savedChapter.sha256 !== undefined && target.savedChapter.sha256 !== savedTarget.content_sha256)
    )
      return 'historical'
    if (!target.savedChapter.sha256 && !target.hashPending) return 'unverified'
  }
  if (target.hashPending) return 'pending'
  if (
    audit.status === 'failed' ||
    audit.findings.some((finding) => finding.severity === 'blocker') ||
    audit.checks.some(
      (check) =>
        check.result === 'fail' &&
        audit.items.find((item) => item.id === check.item_id)?.certainty === 'explicit',
    )
  )
    return 'failed'
  if (
    audit.status !== 'model_pass' ||
    !audit.items.length ||
    audit.items.some((item) => item.certainty !== 'explicit') ||
    audit.checks.some((check) => check.result !== 'pass')
  )
    return 'unverified'
  return 'model_pass'
}

export function plotAuditFromPayload(payload: Record<string, unknown>) {
  if (payload.plot_audit !== undefined) return { value: payload.plot_audit, historical: false }
  if (object(payload.review) && payload.review.plot_audit !== undefined)
    return { value: payload.review.plot_audit, historical: false }
  if (payload.previous_plot_audit !== undefined)
    return { value: payload.previous_plot_audit, historical: true }
  if (object(payload.previous_review) && payload.previous_review.plot_audit !== undefined)
    return { value: payload.previous_review.plot_audit, historical: true }
  return { value: undefined, historical: false }
}

export const plotAuditEnabled = (action: string, enabled: unknown) =>
  enabled === true && ['writing.expand_plot', 'review.chapter', 'workflow'].includes(action)
