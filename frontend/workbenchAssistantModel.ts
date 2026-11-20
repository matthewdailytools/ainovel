import { api } from './api'

export const assistantFields = {
  M1: [
    'idea',
    'position',
    'track',
    'genre_ids',
    'audience',
    'distinction',
    'length',
    'cadence',
    'ending',
    'final_image',
  ],
  M2: ['goal', 'observation', 'interpretation', 'technique', 'conditions', 'exceptions'],
  M3: ['title', 'detail'],
  M4: ['summary', 'title', 'goal', 'obstacle', 'turn', 'result'],
  M5: ['selection', 'text'],
  M6: ['01', '02', '03', '04', '05', '06', '07', '08', '09', '10'],
  M7: ['intro', 'ending', 'characters', 'promises', 'read_note'],
} as const

export type CreationBench = keyof typeof assistantFields
export type AssistantAction = 'discuss' | 'suggest' | 'refine' | 'review'
export type AssistantValue = string | string[]
export type AssistantRequestOptions = {
  spoiler_scope?: 'opening' | 'whole'
  sample_chapters?: number
  selection?: { start: number; end: number; revision: number }
}
export type AssistantLocation = {
  branchId: string
  stage: CreationBench
  scopeId: string | null
  draftKey: string | null
}
export type AssistantCitation = {
  kind: string
  id: string
  label: string
  revision?: number | null
  excerpt?: string | null
}
export type AssistantSuggestion = { field: string; value: AssistantValue; reason: string }
export type AssistantConversation = {
  id: string
  stage: CreationBench
  scope_id: string | null
  draft_key: string | null
  subject: string
  revision: number
  created: number
  updated: number
}
export type AssistantTurn = {
  id: string
  sequence: number
  question: string
  task_id: string | null
  status: string
  error?: string
  result: {
    answer: string
    citations: AssistantCitation[]
    suggestions: AssistantSuggestion[]
  } | null
  action: AssistantAction
  target: string | null
  target_key: string | null
  context_key: string | null
  draft_text: string
  basis_sha256: string
  basis_current: boolean
  applied_fields: string[]
  created: number
}
export type AssistantHistory = {
  conversation: AssistantConversation
  basis: {
    sha256: string
    versions: AssistantCitation[]
    checks: { id: string; title: string; status: string; detail: string }[]
    scope_label: string
  } | null
  turns: AssistantTurn[]
  next_before: number | null
}
export type AssistantTurnInput = AssistantRequestOptions & {
  base_revision: number
  basis_sha256: string
  question: string
  account: string
  client_request_id: string
  action: AssistantAction
  target?: string
  target_key?: string
  context_key?: string
  draft_text?: string
  budget_limit?: number
}
export type AssistantDraftBinding = {
  field: string
  label: string
  objectKey?: string
  contextKey?: string
  value: AssistantValue
}

export const assistantActions: Record<AssistantAction, string> = {
  discuss: '讨论',
  suggest: '给建议',
  refine: '润色',
  review: '复核',
}

export function allowsAssistantTarget(stage: CreationBench, field: string) {
  return (assistantFields[stage] as readonly string[]).includes(field)
}

export function assistantDraftText(value: AssistantValue) {
  return typeof value === 'string' ? value : JSON.stringify(value)
}

export function assistantLocationKey({ branchId, stage, scopeId, draftKey }: AssistantLocation) {
  return JSON.stringify([branchId, stage, scopeId, draftKey])
}

export function assistantTurnMatchesTarget(turn: AssistantTurn, target: AssistantDraftBinding) {
  return (
    turn.target === target.field &&
    turn.target_key === (target.objectKey ?? null) &&
    turn.context_key === (target.contextKey ?? null)
  )
}

export function canApplyAssistantSuggestion(
  turn: AssistantTurn,
  suggestion: AssistantSuggestion,
  target: AssistantDraftBinding,
  currentBasis: string | undefined,
) {
  return (
    turn.status === 'succeeded' &&
    turn.result?.suggestions.includes(suggestion) === true &&
    turn.target === suggestion.field &&
    assistantTurnMatchesTarget(turn, target) &&
    assistantDraftText(target.value) === turn.draft_text &&
    typeof target.value === typeof suggestion.value &&
    currentBasis === turn.basis_sha256
  )
}

export const assistantTurnPending = (turn: AssistantTurn) =>
  turn.status === 'queued' || turn.status === 'running'

function invalid(): never {
  throw new Error('本机服务返回的助手记录无法核对，请刷新历史后重试。')
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid()
  return value as Record<string, unknown>
}

function content(value: unknown): string {
  return typeof value === 'string' ? value : invalid()
}

function nullable(value: unknown): string | null {
  return value == null ? null : content(value)
}

function number(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : invalid()
}

function list<T>(value: unknown, parse: (value: unknown) => T) {
  if (!Array.isArray(value)) return invalid()
  return value.map(parse)
}

function parseConversation(value: unknown, location: AssistantLocation): AssistantConversation {
  const row = record(value)
  if (
    row.stage !== location.stage ||
    nullable(row.scope_id) !== location.scopeId ||
    nullable(row.draft_key) !== location.draftKey
  )
    return invalid()
  return {
    id: content(row.id),
    stage: location.stage,
    scope_id: location.scopeId,
    draft_key: location.draftKey,
    subject: content(row.subject),
    revision: number(row.revision),
    created: number(row.created),
    updated: number(row.updated),
  }
}

function parseCitation(value: unknown): AssistantCitation {
  const row = record(value)
  return {
    kind: content(row.kind),
    id: content(row.id),
    label: content(row.label),
    revision: row.revision == null ? null : number(row.revision),
    excerpt: nullable(row.excerpt),
  }
}

function parseTurn(value: unknown): AssistantTurn {
  const row = record(value)
  const result = row.result == null ? null : record(row.result)
  const action = row.action ?? 'discuss'
  if (action !== 'discuss' && action !== 'suggest' && action !== 'refine' && action !== 'review')
    return invalid()
  return {
    id: content(row.id),
    sequence: number(row.sequence),
    question: content(row.question),
    task_id: nullable(row.task_id),
    status: content(row.status),
    ...(typeof row.error === 'string' ? { error: row.error } : {}),
    result: result && {
      answer: content(result.answer),
      citations: list(result.citations, parseCitation),
      suggestions: list(result.suggestions, (item) => {
        const suggestion = record(item)
        const field = content(suggestion.field)
        const value = field === 'genre_ids' ? list(suggestion.value, content) : content(suggestion.value)
        return { field, value, reason: content(suggestion.reason) }
      }),
    },
    action,
    target: nullable(row.target),
    target_key: nullable(row.target_key),
    context_key: nullable(row.context_key),
    draft_text: content(row.draft_text ?? ''),
    basis_sha256: content(row.basis_sha256 ?? ''),
    basis_current: row.basis_current === true,
    applied_fields: list(row.applied_fields ?? [], content),
    created: number(row.created),
  }
}

function conversationsPath(location: AssistantLocation) {
  return '/branches/' + encodeURIComponent(location.branchId) + '/assistant/conversations'
}

function turnsPath(location: AssistantLocation, conversationId: string) {
  return conversationsPath(location) + '/' + encodeURIComponent(conversationId) + '/turns'
}

export async function listAssistantConversations(location: AssistantLocation) {
  const params = new URLSearchParams({ stage: location.stage })
  if (location.scopeId) params.set('scope_id', location.scopeId)
  if (location.draftKey) params.set('draft_key', location.draftKey)
  const data = record(await api(conversationsPath(location) + '?' + params))
  return list(data.conversations, (item) => parseConversation(item, location))
}

export async function createAssistantConversation(
  location: AssistantLocation,
  subject: string,
  requestId: string,
) {
  return parseConversation(
    await api(conversationsPath(location), 'POST', {
      stage: location.stage,
      scope_id: location.scopeId,
      ...(location.draftKey ? { draft_key: location.draftKey } : {}),
      subject,
      client_request_id: requestId,
    }),
    location,
  )
}

export async function readAssistantHistory(
  location: AssistantLocation,
  conversationId: string,
  before?: number,
): Promise<AssistantHistory> {
  const params = new URLSearchParams({ limit: '20' })
  if (before !== undefined) params.set('before', String(before))
  const data = record(await api(turnsPath(location, conversationId) + '?' + params))
  const conversation = parseConversation(data.conversation, location)
  if (conversation.id !== conversationId) return invalid()
  const basis = data.basis == null ? null : record(data.basis)
  return {
    conversation,
    basis: basis && {
      sha256: content(basis.sha256),
      versions: list(basis.versions, parseCitation),
      checks: list(basis.checks ?? [], (item) => {
        const row = record(item)
        return {
          id: content(row.id),
          title: content(row.title),
          status: content(row.status),
          detail: content(row.detail),
        }
      }),
      scope_label: content(basis.scope_label),
    },
    turns: list(data.turns, parseTurn).sort((left, right) => left.sequence - right.sequence),
    next_before: data.next_before == null ? null : number(data.next_before),
  }
}

export async function postAssistantTurn(location: AssistantLocation, id: string, input: AssistantTurnInput) {
  const data = record(await api(turnsPath(location, id), 'POST', input))
  const conversation = parseConversation(data.conversation, location)
  if (conversation.id !== id) return invalid()
  return { conversation, turn: parseTurn(data.turn) }
}

export const cancelAssistantTask = (taskId: string) =>
  api('/tasks/' + encodeURIComponent(taskId) + '/cancel', 'POST')

export async function recordAssistantDraftApplication(
  location: AssistantLocation,
  conversationId: string,
  turnId: string,
  field: string,
) {
  const path = turnsPath(location, conversationId) + '/' + encodeURIComponent(turnId) + '/apply-receipt'
  const result = record(await api(path, 'POST', { field }))
  if (!list(result.applied_fields, content).includes(field)) invalid()
}
