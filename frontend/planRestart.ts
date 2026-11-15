import type { components } from './api-schema'
import type { Task } from './api'

export const RESTART_VERSION = 'workflow-plan-restart/1'
export const OPEN_DETAILS_VERSION = 'workflow-plan-open-details/1'
export const SOURCE_ROLES_VERSION = 'workflow-plan-source-roles/1'
export const SOURCE_ROLES_CREATION_VERSION = 'workflow-plan-source-roles-creation/1'
export const RECIPIENT_SCOPE_VERSION = 'workflow-plan-recipient-scope/1'
export type RestartInput = components['schemas']['PlanRestartInput']
export type RestartBasis = Pick<
  RestartInput,
  'chapter' | 'base_story_revision' | 'base_progress_sha256' | 'workflow_plan_source_roles_version'
>
export type RestartPreview = {
  version: typeof RESTART_VERSION
  workflow_plan_open_details_version: typeof OPEN_DETAILS_VERSION
  workflow_plan_source_roles_version?: typeof SOURCE_ROLES_VERSION
  task_id: string
  branch_id: string
  chapter_id: string
  request_basis: RestartBasis
  original_plot: string
  original_plot_sha256: string
  old_preparation_sha256: string
  known_invalid_steps: { fingerprints: string[]; call_ids: string[] }
  budget: Record<string, unknown> & {
    provider: string
    source_run_limit: number | null
    source_task_settled: number
    remaining_run_limit: number | null
    unknown_reserve: 0
  }
  policy: string
}
export type PendingRestart = {
  request: RestartInput
  preview: RestartPreview
  source: Task
}

export function object(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined
}
const string = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0
const hash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
const money = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0
const nullableMoney = (value: unknown): value is number | null => value === null || money(value)
const ids = (value: unknown): value is string[] =>
  Array.isArray(value) && value.length === 3 && value.every(string) && new Set(value).size === 3

export function canonical(value: unknown): string {
  const normalize = (item: unknown): unknown => {
    if (Array.isArray(item)) return item.map(normalize)
    const row = object(item)
    if (!row) return item
    return Object.fromEntries(
      Object.keys(row)
        .sort()
        .map((key) => [key, normalize(row[key])]),
    )
  }
  return JSON.stringify(normalize(value))
}

export function sourceSignature(task: Task) {
  return canonical({
    id: task.id,
    novel_id: task.novel_id,
    branch_id: task.branch_id,
    status: task.status,
    cancel: task.cancel,
    error: task.error,
    payload: task.payload,
    result: task.result,
  })
}

export function preparation(task: Task) {
  return object(object(task.result.plot_preparations)?.[String(task.result.next_chapter)])
}

export type SourceRolesCreation = {
  version: typeof SOURCE_ROLES_CREATION_VERSION
  task_id: string
  novel_id: string
  branch_id: string
  guidance_version: typeof SOURCE_ROLES_VERSION
  source_payload_sha256: string
}

export function readSourceRolesCreation(task: Task): SourceRolesCreation | null | undefined {
  const payload = object(task.payload)?.workflow_plan_source_roles_creation,
    result = object(task.result)?.workflow_plan_source_roles_creation
  if (payload === undefined && result === undefined) return null
  const row = object(payload)
  if (
    !row ||
    canonical(payload) !== canonical(result) ||
    Object.keys(row).sort().join(',') !==
      'branch_id,guidance_version,novel_id,source_payload_sha256,task_id,version' ||
    row.version !== SOURCE_ROLES_CREATION_VERSION ||
    row.guidance_version !== SOURCE_ROLES_VERSION ||
    !string(row.task_id) ||
    row.novel_id !== task.novel_id ||
    row.branch_id !== task.branch_id ||
    !hash(row.source_payload_sha256) ||
    object(task.payload)?.workflow_plan_source_roles_version !== SOURCE_ROLES_VERSION ||
    object(task.result)?.workflow_plan_source_roles_version !== SOURCE_ROLES_VERSION
  )
    return undefined
  return row as SourceRolesCreation
}

export function sourceRolesAnchorsMatch(task: Task) {
  const rolesVersion = object(task.result)?.workflow_plan_source_roles_version,
    payloadRolesVersion = object(task.payload)?.workflow_plan_source_roles_version,
    receiptValue = object(task.result)?.plan_preparation_restart,
    receipt = object(receiptValue),
    receiptRolesVersion = receipt?.workflow_plan_source_roles_version,
    receiptRequestRolesVersion = object(receipt?.request)?.workflow_plan_source_roles_version,
    creation = readSourceRolesCreation(task)
  if (creation === undefined) return false
  if (rolesVersion === undefined)
    return (
      creation === null &&
      payloadRolesVersion === undefined &&
      receiptRolesVersion === undefined &&
      receiptRequestRolesVersion === undefined
    )
  if (rolesVersion !== SOURCE_ROLES_VERSION || payloadRolesVersion !== rolesVersion) return false
  if (creation === null)
    return (
      receipt?.version === RESTART_VERSION &&
      receiptRolesVersion === rolesVersion &&
      receiptRequestRolesVersion === rolesVersion
    )
  const parent = task.result.continued_from
  if (creation.task_id === task.id) return parent === undefined && receiptValue === undefined
  // This is a snapshot boundary check. The formal preview validates the origin's
  // continued_from lineage in storage before it returns an actionable basis.
  if (!string(parent) || parent === task.id) return false
  if (receiptValue === undefined) return true
  return (
    receipt?.version === RESTART_VERSION &&
    receiptRolesVersion === rolesVersion &&
    receiptRequestRolesVersion === rolesVersion &&
    string(receipt.source_task_id) &&
    receipt.source_task_id !== task.id
  )
}

function initialInterpretationPhases(row: Record<string, unknown>, phases: Record<string, unknown>) {
  const version = row.workflow_plan_recipient_scope_version
  if (version === undefined) return Object.keys(phases).join(',') === 'interpret:0'
  if (
    version !== RECIPIENT_SCOPE_VERSION ||
    row.workflow_plan_source_roles_version !== SOURCE_ROLES_VERSION ||
    Object.keys(phases).sort().join(',') !== 'interpret:0,recipient_scope:0'
  )
    return false
  const slot = object(phases['recipient_scope:0']),
    receipt = object(slot?.response),
    inventory = object(receipt?.inventory)
  // This only exposes the read-only preview. The server verifies source spans,
  // receipt hashes and checkpoint lineage before returning an actionable basis.
  return Boolean(
    slot?.status === 'completed' &&
    string(slot.owner_task_id) &&
    integer(slot.attempt) &&
    hash(slot.response_sha256) &&
    receipt?.version === version &&
    receipt.status === 'supported' &&
    hash(receipt.source_sha256) &&
    receipt.source_sha256 === row.effective_plot_sha256 &&
    hash(receipt.input_sha256) &&
    inventory?.input_sha256 === receipt.input_sha256 &&
    Array.isArray(inventory.observations) &&
    inventory.observations.every((value) =>
      [
        'recipient_exclusivity',
        'named_destination',
        'specific_recipient_exclusion',
        'action_permission',
        'explicitly_unrestricted',
      ].includes(String(object(value)?.kind)),
    ) &&
    Array.isArray(inventory.unit_checks) &&
    inventory.unit_checks.length > 0 &&
    inventory.unit_checks.every((value) =>
      ['covered', 'not_scope_related'].includes(String(object(value)?.status)),
    ),
  )
}

export function canPreviewRestart(task: Task) {
  const row = preparation(task)
  const openVersion = object(task.result)?.workflow_plan_open_details_version,
    rolesVersion = object(task.result)?.workflow_plan_source_roles_version,
    payloadRolesVersion = object(task.payload)?.workflow_plan_source_roles_version,
    phases = object(row?.phases),
    initial = object(phases?.['interpret:0']),
    roleAnchor = sourceRolesAnchorsMatch(task)
  const legacy =
    openVersion === undefined &&
    rolesVersion === undefined &&
    payloadRolesVersion === undefined &&
    row?.workflow_plan_recipient_scope_version === undefined &&
    phases?.['recipient_scope:0'] === undefined &&
    roleAnchor
  const initialOpen =
    openVersion === OPEN_DETAILS_VERSION &&
    (rolesVersion === undefined || rolesVersion === SOURCE_ROLES_VERSION) &&
    payloadRolesVersion === rolesVersion &&
    row?.workflow_plan_source_roles_version === rolesVersion &&
    roleAnchor &&
    !!phases &&
    !!row &&
    initialInterpretationPhases(row, phases) &&
    !!initial &&
    !('response' in initial)
  return (
    task.kind === 'workflow' &&
    task.payload.mode === 'automatic' &&
    task.payload.plot_contract_check === true &&
    task.result.plot_contract_version === 'author-plot-audit/2' &&
    task.result.workflow_plan_preparation_version === 'workflow-plan-preparation/1' &&
    (legacy || initialOpen) &&
    ['paused', 'blocked_runtime'].includes(task.status) &&
    !task.result.waiting_artifact &&
    !task.result.waiting_structure &&
    !task.result.pending_structure &&
    !task.result.continued_to &&
    row?.status === 'interpreting' &&
    row.round === 0
  )
}

export function readRestartPreview(value: unknown, task: Task): RestartPreview | undefined {
  const row = object(value),
    basis = object(row?.request_basis),
    budget = object(row?.budget)
  const proof = object(row?.known_invalid_steps),
    prior = preparation(task)
  const rolesVersion = basis?.workflow_plan_source_roles_version,
    sourceRolesVersion = row?.workflow_plan_source_roles_version
  const sourceRoles = rolesVersion === SOURCE_ROLES_VERSION && sourceRolesVersion === rolesVersion
  const legacy = rolesVersion === undefined && sourceRolesVersion === undefined
  if (
    !row ||
    !canPreviewRestart(task) ||
    row.version !== RESTART_VERSION ||
    row.workflow_plan_open_details_version !== OPEN_DETAILS_VERSION ||
    row.task_id !== task.id ||
    row.branch_id !== task.branch_id ||
    !string(row.chapter_id) ||
    !basis ||
    !integer(basis.chapter) ||
    basis.chapter < 1 ||
    basis.chapter !== task.result.next_chapter ||
    !integer(basis.base_story_revision) ||
    !hash(basis.base_progress_sha256) ||
    (!legacy && !sourceRoles) ||
    (sourceRoles && task.result.workflow_plan_open_details_version !== OPEN_DETAILS_VERSION) ||
    (legacy && object(task.result)?.workflow_plan_open_details_version !== undefined) ||
    Object.keys(basis).sort().join(',') !==
      (sourceRoles
        ? 'base_progress_sha256,base_story_revision,chapter,workflow_plan_source_roles_version'
        : 'base_progress_sha256,base_story_revision,chapter') ||
    !string(row.original_plot) ||
    !hash(row.original_plot_sha256) ||
    !hash(row.old_preparation_sha256) ||
    !proof ||
    !ids(proof.fingerprints) ||
    !proof.fingerprints.every(hash) ||
    !ids(proof.call_ids) ||
    !budget ||
    !string(budget.provider) ||
    !nullableMoney(budget.source_run_limit) ||
    !money(budget.source_task_settled) ||
    !nullableMoney(budget.remaining_run_limit) ||
    budget.unknown_reserve !== 0 ||
    !string(row.policy) ||
    row.chapter_id !== prior?.chapter_id ||
    row.original_plot !== prior.original_plot ||
    row.original_plot_sha256 !== prior.original_plot_sha256 ||
    basis.base_story_revision !== object(task.result)?.expected_story_revision
  )
    return undefined
  // The server owns hashes, costs and request_basis. Never synthesize or repair them here.
  return row as RestartPreview
}

export function emptyChapterMatches(value: unknown, preview: RestartPreview) {
  const row = object(value)
  return Boolean(
    row &&
    row.id === preview.chapter_id &&
    row.branch_id === preview.branch_id &&
    row.position === preview.request_basis.chapter &&
    row.revision === 0 &&
    row.content === '' &&
    !row.archived,
  )
}

function inheritedPayload(value: unknown, sourceRoles: boolean) {
  const row = object(value)
  return (
    row &&
    Object.fromEntries(
      Object.entries(row).filter(
        ([key]) =>
          !['client_request_id', 'budget_limit'].includes(key) &&
          !(sourceRoles && key === 'workflow_plan_source_roles_version'),
      ),
    )
  )
}

export function readRestartChild(value: unknown, pending: PendingRestart): Task | undefined {
  const row = object(value),
    payload = object(row?.payload),
    progress = object(row?.result)
  const receipt = object(progress?.plan_preparation_restart),
    source = pending.source
  const rolesVersion = object(pending.request)?.workflow_plan_source_roles_version
  if (
    !row ||
    !string(row.id) ||
    row.id === source.id ||
    row.kind !== 'workflow' ||
    row.novel_id !== source.novel_id ||
    row.branch_id !== source.branch_id ||
    !string(row.status) ||
    typeof row.created !== 'number' ||
    !Number.isFinite(row.created) ||
    typeof row.cancel !== 'number' ||
    typeof row.error !== 'string' ||
    !payload ||
    !progress ||
    payload.mode !== 'automatic' ||
    payload.account !== source.payload.account ||
    canonical(inheritedPayload(payload, rolesVersion === SOURCE_ROLES_VERSION)) !==
      canonical(inheritedPayload(source.payload, rolesVersion === SOURCE_ROLES_VERSION)) ||
    payload.client_request_id !== pending.request.client_request_id ||
    payload.budget_limit !== pending.preview.budget.remaining_run_limit ||
    progress.continued_from !== source.id ||
    progress.workflow_plan_open_details_version !== OPEN_DETAILS_VERSION ||
    (rolesVersion !== undefined && rolesVersion !== SOURCE_ROLES_VERSION) ||
    rolesVersion !== pending.preview.workflow_plan_source_roles_version ||
    progress.workflow_plan_source_roles_version !== rolesVersion ||
    payload.workflow_plan_source_roles_version !== rolesVersion ||
    !sourceRolesAnchorsMatch(row as Task) ||
    Object.entries(source.result).some(
      ([key, field]) =>
        key.endsWith('_version') &&
        key !== 'workflow_plan_source_roles_version' &&
        canonical(progress[key]) !== canonical(field),
    ) ||
    canonical(progress.plan) !== canonical(source.result.plan) ||
    !receipt ||
    receipt.version !== RESTART_VERSION ||
    receipt.workflow_plan_source_roles_version !== rolesVersion ||
    receipt.source_task_id !== source.id ||
    canonical(receipt.request) !== canonical(pending.request) ||
    receipt.previous_preparation_sha256 !== pending.preview.old_preparation_sha256 ||
    canonical(receipt.known_invalid_steps) !== canonical(pending.preview.known_invalid_steps) ||
    canonical(receipt.budget) !== canonical(pending.preview.budget)
  )
    return undefined
  // Required Task fields and inherited payload/provenance were checked above;
  // optional server progress is preserved for the existing read-only record panels.
  return row as Task
}

export function readRestartLookup(value: unknown, pending: PendingRestart): Task | null | undefined {
  const row = object(value)
  if (
    !row ||
    row.source_task_id !== pending.source.id ||
    row.client_request_id !== pending.request.client_request_id
  )
    return undefined
  return row.task === null ? null : readRestartChild(row.task, pending)
}

export function readPendingRestart(value: unknown, owner: string): PendingRestart | undefined {
  const row = object(value),
    source = object(row?.source),
    request = object(row?.request)
  const payload = object(source?.payload),
    result = object(source?.result)
  const rolesVersion = request?.workflow_plan_source_roles_version
  if (
    !source ||
    !payload ||
    !result ||
    !string(source.id) ||
    !string(source.branch_id) ||
    !string(source.novel_id) ||
    canonical([source.novel_id, source.branch_id, source.id]) !== owner ||
    source.kind !== 'workflow' ||
    payload.mode !== 'automatic' ||
    !string(payload.account) ||
    !request ||
    !string(request.client_request_id) ||
    request.client_request_id.length < 8 ||
    !string(request.reason) ||
    request.reason.length > 2000 ||
    !integer(request.chapter) ||
    !integer(request.base_story_revision) ||
    !hash(request.base_progress_sha256) ||
    (rolesVersion !== undefined && rolesVersion !== SOURCE_ROLES_VERSION) ||
    Object.keys(request).sort().join(',') !==
      (rolesVersion === SOURCE_ROLES_VERSION
        ? 'base_progress_sha256,base_story_revision,chapter,client_request_id,reason,workflow_plan_source_roles_version'
        : 'base_progress_sha256,base_story_revision,chapter,client_request_id,reason')
  )
    return undefined
  const task = source as Task,
    preview = readRestartPreview(row?.preview, task)
  if (
    !preview ||
    canonical(preview.request_basis) !==
      canonical({
        chapter: request.chapter,
        base_story_revision: request.base_story_revision,
        base_progress_sha256: request.base_progress_sha256,
        ...(rolesVersion === SOURCE_ROLES_VERSION
          ? { workflow_plan_source_roles_version: rolesVersion }
          : {}),
      })
  )
    return undefined
  return { source: task, preview, request: request as RestartInput }
}
