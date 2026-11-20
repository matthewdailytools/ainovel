import { api } from './api'
import { assistantFields, type AssistantValue, type CreationBench } from './workbenchAssistantModel'

export const novelAssistantStages: Record<CreationBench, string> = {
  M1: '立项台',
  M2: '拆解台',
  M3: '设定台',
  M4: '大纲台',
  M5: '写作台',
  M6: '校验台',
  M7: '完本台',
}

export type NovelAssistantScope = { stage: CreationBench; scope_id: string | null }
export type NovelAssistantReference = NovelAssistantScope & { scope_label: string }
export type NovelAssistantDraft = { text: string; revision: number }
export type NovelAssistantCard = NovelAssistantScope & {
  id: string
  field: string
  title: string
  value: AssistantValue
  reason: string
}
export type NovelAssistantTurn = NovelAssistantReference & {
  id: string
  sequence: number
  question: string
  status: string
  error: string | null
  task_id: string | null
  include_current_scope: boolean
  references: NovelAssistantReference[]
  result: { answer: string; cards: NovelAssistantCard[] } | null
}
export type NovelAssistantHistory = {
  revision: number
  draft: NovelAssistantDraft
  turns: NovelAssistantTurn[]
  next_before: number | null
}
export type NovelAssistantTurnInput = NovelAssistantScope & {
  base_revision: number
  question: string
  account: string
  client_request_id: string
  include_current_scope: boolean
  references: NovelAssistantScope[]
}
export type NovelAssistantDestination = {
  scope_id: string | null
  label: string
  fields: { id: string; label: string }[]
}
export type NovelAssistantDeliveryInput = NovelAssistantScope & {
  turn_id: string
  card_id: string
  field: string
  value: AssistantValue
}
export type NovelAssistantPreview = NovelAssistantScope & {
  field: string
  target_label: string
  field_label: string
  basis_sha256: string
  before: AssistantValue
  value: AssistantValue
}
export type NovelAssistantDelivery = NovelAssistantPreview & {
  id: string
  turn_id: string
  card_id: string
  created: number
  basis_current: boolean
}

function invalid(): never {
  throw new Error('本机服务返回的作品对话记录无法核对，请刷新后重试。')
}
function object(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : invalid()
}
function content(value: unknown): string {
  return typeof value === 'string' ? value : invalid()
}
function nullable(value: unknown): string | null {
  return value == null ? null : content(value)
}
function integer(value: unknown): number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : invalid()
}
function boolean(value: unknown): boolean {
  return typeof value === 'boolean' ? value : invalid()
}
function list<T>(value: unknown, map: (value: unknown) => T): T[] {
  return Array.isArray(value) ? value.map(map) : invalid()
}
function assistantValue(value: unknown): AssistantValue {
  return typeof value === 'string' ? value : list(value, content)
}
export function isNovelAssistantStage(value: unknown): value is CreationBench {
  return typeof value === 'string' && Object.hasOwn(assistantFields, value)
}
function scope(value: Record<string, unknown>): NovelAssistantScope {
  if (!isNovelAssistantStage(value.stage)) invalid()
  return { stage: value.stage, scope_id: nullable(value.scope_id) }
}
function reference(value: unknown): NovelAssistantReference {
  const row = object(value)
  return { ...scope(row), scope_label: content(row.scope_label) }
}
function card(value: unknown): NovelAssistantCard {
  const row = object(value)
  return {
    ...scope(row),
    id: content(row.id),
    field: content(row.field),
    title: content(row.title),
    value: assistantValue(row.value),
    reason: content(row.reason),
  }
}
function turn(value: unknown): NovelAssistantTurn {
  const row = object(value)
  const result = row.result === null ? null : object(row.result)
  return {
    ...reference(row),
    id: content(row.id),
    sequence: integer(row.sequence),
    question: content(row.question),
    status: content(row.status),
    error: nullable(row.error),
    task_id: nullable(row.task_id),
    include_current_scope: boolean(row.include_current_scope),
    references: list(row.references, reference),
    result: result && { answer: content(result.answer), cards: list(result.cards, card) },
  }
}
function draft(value: unknown): NovelAssistantDraft {
  const row = object(value)
  return { text: content(row.text), revision: integer(row.revision) }
}
function destination(value: unknown): NovelAssistantDestination {
  const row = object(value)
  return {
    scope_id: nullable(row.scope_id),
    label: content(row.label),
    fields: list(row.fields, (value) => {
      const field = object(value)
      return { id: content(field.id), label: content(field.label) }
    }),
  }
}
function preview(value: unknown): NovelAssistantPreview {
  const row = object(value)
  return {
    ...scope(row),
    field: content(row.field),
    target_label: content(row.target_label),
    field_label: content(row.field_label),
    basis_sha256: content(row.basis_sha256),
    before: assistantValue(row.before),
    value: assistantValue(row.value),
  }
}
function delivery(value: unknown): NovelAssistantDelivery {
  const row = object(value)
  if (typeof row.created !== 'number' || !Number.isFinite(row.created)) invalid()
  return {
    ...preview(row),
    id: content(row.id),
    turn_id: content(row.turn_id),
    card_id: content(row.card_id),
    created: row.created,
    basis_current: boolean(row.basis_current),
  }
}
function path(branchId: string) {
  return '/branches/' + encodeURIComponent(branchId) + '/novel-assistant'
}

export async function readNovelAssistant(branchId: string, before?: number): Promise<NovelAssistantHistory> {
  const value = object(await api(path(branchId) + (before === undefined ? '' : '?before=' + before)))
  return {
    revision: integer(value.revision),
    draft: draft(value.draft),
    turns: list(value.turns, turn),
    next_before: value.next_before == null ? null : integer(value.next_before),
  }
}
export async function saveNovelAssistantDraft(
  branchId: string,
  text: string,
  base_revision: number,
): Promise<NovelAssistantDraft> {
  return draft(await api(path(branchId) + '/draft', 'PUT', { text, base_revision }))
}
export async function sendNovelAssistantTurn(branchId: string, input: NovelAssistantTurnInput) {
  const value = object(await api(path(branchId) + '/turns', 'POST', input))
  return { revision: integer(value.revision), turn: turn(value.turn) }
}
export async function readNovelAssistantDestinations(branchId: string, stage: CreationBench) {
  return list(object(await api(path(branchId) + '/destinations?stage=' + stage)).options, destination)
}
export async function previewNovelAssistantDelivery(branchId: string, input: NovelAssistantDeliveryInput) {
  return preview(await api(path(branchId) + '/deliveries/preview', 'POST', input))
}
export async function saveNovelAssistantDelivery(
  branchId: string,
  input: NovelAssistantDeliveryInput & { basis_sha256: string },
) {
  return delivery(await api(path(branchId) + '/deliveries', 'POST', input))
}
export async function readNovelAssistantDeliveries(branchId: string, stage?: CreationBench) {
  const suffix = stage ? '?stage=' + stage : ''
  return list(object(await api(path(branchId) + '/deliveries' + suffix)).deliveries, delivery)
}
export async function readNovelAssistantDelivery(branchId: string, deliveryId: string) {
  return delivery(await api(path(branchId) + '/deliveries/' + encodeURIComponent(deliveryId)))
}

export function sameNovelAssistantScope(a: NovelAssistantScope, b: NovelAssistantScope) {
  return a.stage === b.stage && a.scope_id === b.scope_id
}
export function novelAssistantScopeLabel(scope: NovelAssistantReference) {
  const stage = novelAssistantStages[scope.stage]
  return scope.scope_label && scope.scope_label !== stage ? stage + ' · ' + scope.scope_label : stage
}
export function novelAssistantValueText(value: AssistantValue) {
  return typeof value === 'string' ? value : value.join('、')
}
