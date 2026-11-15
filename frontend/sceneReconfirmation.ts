import type { components } from './api-schema'
import { readSceneOrderBasis, type SceneEventWitness } from './sceneOrderBasis'

export const RECONFIRMATION_VERSION = 'scene-author-reconfirmation/1'
export const SCENE_PAYLOAD_HASH_VERSION = 'scene-time-public-payload/1'
export type ReconfirmationInput = components['schemas']['ReconfirmationInput']
type CurrentRef = ReconfirmationInput['refs'][number]
export type TimeEvidence = NonNullable<ReconfirmationInput['time_evidence']>[number]
export type ScenePayload = {
  title: string
  source_revision: number
  start: number
  end: number
  story_order: number | null
  story_time: string
  audience: string
  character_id: string | null
  order_basis?: unknown
  event_id?: string | null
  event_revision?: number | null
  author_reconfirmed?: unknown
}
export type SceneRow = {
  id: string
  revision: number
  payload: ScenePayload
  archived: number
  stale?: boolean
  payload_sha256?: string
  payload_hash_version?: string
}
export type Preview = {
  version: typeof RECONFIRMATION_VERSION
  authority: 'author_confirmation_not_model_review'
  scene_id: string
  scene: SceneRow
  source: TimeEvidence
  request_basis: Omit<ReconfirmationInput, 'reason' | 'client_request_id'> & {
    time_evidence: TimeEvidence[]
  }
}
export type Receipt = {
  version: typeof RECONFIRMATION_VERSION
  authority: 'author_confirmation_not_model_review'
  scene_id: string
  client_request_id: string
  old_revision: number
  revision: number
  story_revision: number
  base_story_revision: number
  source_revision: number
  reason: string
  previous_story_time: string
  story_time: string
  previous_refs: SceneEventWitness[]
  current_refs: CurrentRef[]
  time_evidence: TimeEvidence[]
  covered_events_unchanged: true
  [key: string]: unknown
}
export const object = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value)
const integer = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value)
const nonnegative = (value: unknown): value is number => integer(value) && value >= 0
const hash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
const nonempty = (value: unknown): value is string => typeof value === 'string' && !!value.trim()

export function canReconfirmScene(row: SceneRow) {
  const basis = readSceneOrderBasis(row.payload.order_basis)
  return (
    !row.archived &&
    row.payload_hash_version === SCENE_PAYLOAD_HASH_VERSION &&
    hash(row.payload_sha256) &&
    basis?.kind === 'event_refs' &&
    basis.refs.length > 0 &&
    integer(row.payload.story_order) &&
    row.payload.event_id == null &&
    row.payload.event_revision == null
  )
}
function currentRef(value: unknown): value is CurrentRef {
  return (
    object(value) &&
    nonempty(value.ref) &&
    nonempty(value.event_id) &&
    nonnegative(value.event_revision) &&
    integer(value.story_order) &&
    hash(value.record_sha256)
  )
}
function previousRef(value: unknown): value is SceneEventWitness {
  return (
    object(value) &&
    nonempty(value.ref) &&
    nonempty(value.event_id) &&
    (value.event_revision === null || nonnegative(value.event_revision)) &&
    (value.story_order === null || integer(value.story_order)) &&
    hash(value.record_sha256)
  )
}
function evidence(value: unknown): value is TimeEvidence {
  return (
    object(value) &&
    nonempty(value.chapter_id) &&
    nonnegative(value.revision) &&
    nonnegative(value.start) &&
    integer(value.end) &&
    value.end > value.start &&
    nonempty(value.quote) &&
    value.quote.length === value.end - value.start
  )
}
function scenePayload(value: unknown): value is ScenePayload {
  return (
    object(value) &&
    nonempty(value.title) &&
    nonnegative(value.source_revision) &&
    nonnegative(value.start) &&
    integer(value.end) &&
    value.end > value.start &&
    integer(value.story_order) &&
    typeof value.story_time === 'string' &&
    ['author', 'reader', 'character'].includes(String(value.audience)) &&
    (value.character_id === null || typeof value.character_id === 'string')
  )
}
export function readPreview(
  value: unknown,
  branchId: string,
  chapterId: string,
  sceneId: string,
): Preview | undefined {
  if (
    !object(value) ||
    value.version !== RECONFIRMATION_VERSION ||
    value.authority !== 'author_confirmation_not_model_review' ||
    value.payload_hash_version !== SCENE_PAYLOAD_HASH_VERSION ||
    value.scene_id !== sceneId ||
    !object(value.scene) ||
    value.scene.id !== sceneId ||
    value.scene.branch_id !== branchId ||
    value.scene.chapter_id !== chapterId ||
    !nonnegative(value.scene.revision) ||
    value.scene.archived !== 0 ||
    !scenePayload(value.scene.payload) ||
    !hash(value.scene.payload_sha256) ||
    value.scene.payload_hash_version !== SCENE_PAYLOAD_HASH_VERSION ||
    !evidence(value.source) ||
    value.source.chapter_id !== chapterId ||
    !object(value.request_basis)
  )
    return undefined
  const row: SceneRow = {
    id: sceneId,
    revision: value.scene.revision,
    archived: 0,
    payload: value.scene.payload,
    payload_sha256: value.scene.payload_sha256,
    payload_hash_version: SCENE_PAYLOAD_HASH_VERSION,
  }
  const basis = value.request_basis,
    audit = readSceneOrderBasis(row.payload.order_basis)
  if (
    !canReconfirmScene(row) ||
    !audit ||
    !nonnegative(basis.base_story_revision) ||
    basis.base_revision !== row.revision ||
    basis.base_payload_sha256 !== row.payload_sha256 ||
    basis.source_revision !== value.source.revision ||
    basis.source_revision !== row.payload.source_revision ||
    !hash(basis.content_sha256) ||
    !Array.isArray(basis.refs) ||
    !basis.refs.every(currentRef) ||
    basis.refs.length !== audit.refs.length ||
    new Set(basis.refs.map((ref) => ref.event_id)).size !== basis.refs.length ||
    basis.refs.some(
      (ref, index) =>
        ref.ref !== audit.refs[index].ref ||
        ref.event_id !== audit.refs[index].event_id ||
        ref.story_order !== row.payload.story_order,
    ) ||
    basis.story_time !== row.payload.story_time ||
    !nonempty(basis.story_time) ||
    !Array.isArray(basis.time_evidence) ||
    basis.time_evidence.length ||
    value.source.start !== row.payload.start ||
    value.source.end !== row.payload.end
  )
    return undefined
  return {
    version: RECONFIRMATION_VERSION,
    authority: 'author_confirmation_not_model_review',
    scene_id: sceneId,
    scene: row,
    source: value.source,
    request_basis: {
      base_story_revision: basis.base_story_revision,
      base_revision: row.revision,
      base_payload_sha256: value.scene.payload_sha256,
      source_revision: value.source.revision,
      content_sha256: basis.content_sha256,
      refs: basis.refs,
      story_time: basis.story_time,
      time_evidence: [],
    },
  }
}
function boundary(text: string, position: number) {
  return (
    position >= 0 &&
    position <= text.length &&
    !(
      position > 0 &&
      position < text.length &&
      /[\uD800-\uDBFF]/.test(text[position - 1]) &&
      /[\uDC00-\uDFFF]/.test(text[position])
    )
  )
}
export function selectedEvidence(source: TimeEvidence, start: number, end: number): TimeEvidence | undefined {
  if (
    !integer(start) ||
    !integer(end) ||
    start >= end ||
    !boundary(source.quote, start) ||
    !boundary(source.quote, end)
  )
    return undefined
  const quote = source.quote.slice(start, end)
  if (!quote.trim() || quote.length > 4000) return undefined
  return {
    chapter_id: source.chapter_id,
    revision: source.revision,
    start: source.start + start,
    end: source.start + end,
    quote,
  }
}
export function textareaSelection(
  source: TimeEvidence,
  displayed: string,
  start: number,
  end: number,
): { start: number; end: number } | undefined {
  const normalized = source.quote.replace(/\r\n?/g, '\n')
  if (
    displayed !== normalized ||
    !integer(start) ||
    !integer(end) ||
    start < 0 ||
    end < start ||
    end > normalized.length
  )
    return undefined
  const offsets = [0]
  for (let original = 0; original < source.quote.length;) {
    original += source.quote[original] === '\r' && source.quote[original + 1] === '\n' ? 2 : 1
    offsets.push(original)
  }
  return { start: offsets[start], end: offsets[end] }
}
export function createReconfirmation(
  preview: Preview,
  reason: string,
  storyTime: string,
  quotes: TimeEvidence[],
  nonce: string,
): ReconfirmationInput {
  if (!reason.trim() || reason.length > 2000) throw new Error('请填写本次人工核对理由。')
  if (!storyTime.trim() || storyTime.length > 200)
    throw new Error('请填写有效的故事时间说明；未知可明确写未知。')
  if (storyTime !== preview.request_basis.story_time && !quotes.length)
    throw new Error('更改时间说明前，请从原场景范围选择连续原文依据。')
  for (const quote of quotes) {
    const expected = selectedEvidence(
      preview.source,
      quote.start - preview.source.start,
      quote.end - preview.source.start,
    )
    if (!expected || JSON.stringify(expected) !== JSON.stringify(quote))
      throw new Error('时间依据已不对应本次原场景，请重新选取。')
  }
  return {
    ...preview.request_basis,
    story_time: storyTime,
    time_evidence: quotes,
    reason,
    client_request_id: nonce,
  }
}
export function readReceipt(value: unknown, sceneId: string, nonce: string): Receipt | undefined {
  if (
    !object(value) ||
    value.version !== RECONFIRMATION_VERSION ||
    value.authority !== 'author_confirmation_not_model_review' ||
    value.scene_id !== sceneId ||
    value.client_request_id !== nonce ||
    value.payload_hash_version !== SCENE_PAYLOAD_HASH_VERSION ||
    !nonnegative(value.old_revision) ||
    value.revision !== value.old_revision + 1 ||
    !nonnegative(value.base_story_revision) ||
    value.story_revision !== value.base_story_revision + 1 ||
    !nonnegative(value.source_revision) ||
    !nonempty(value.reason) ||
    typeof value.previous_story_time !== 'string' ||
    !nonempty(value.story_time) ||
    !Array.isArray(value.current_refs) ||
    !value.current_refs.length ||
    !value.current_refs.every(currentRef) ||
    !Array.isArray(value.previous_refs) ||
    !value.previous_refs.every(previousRef) ||
    value.previous_refs.length !== value.current_refs.length ||
    !Array.isArray(value.time_evidence) ||
    !value.time_evidence.every(evidence) ||
    value.covered_events_unchanged !== true ||
    !hash(value.payload_sha256) ||
    !hash(value.old_payload_sha256) ||
    !hash(value.request_sha256) ||
    !hash(value.content_sha256) ||
    !hash(value.old_raw_json_sha256) ||
    !hash(value.raw_json_sha256)
  )
    return undefined
  const previous = value.previous_refs
  if (
    value.current_refs.some(
      (ref, index) => ref.ref !== previous[index].ref || ref.event_id !== previous[index].event_id,
    )
  )
    return undefined
  return value as Receipt
}
export function receiptMatchesRequest(receipt: Receipt, request: ReconfirmationInput) {
  const current = receipt.current_refs.map((ref) => [
    ref.ref,
    ref.event_id,
    ref.event_revision,
    ref.story_order,
    ref.record_sha256,
  ])
  const expected = request.refs.map((ref) => [
    ref.ref,
    ref.event_id,
    ref.event_revision,
    ref.story_order,
    ref.record_sha256,
  ])
  const quotes = (values: TimeEvidence[]) =>
    values.map((source) => [source.chapter_id, source.revision, source.start, source.end, source.quote])
  return (
    receipt.client_request_id === request.client_request_id &&
    receipt.old_revision === request.base_revision &&
    receipt.old_payload_sha256 === request.base_payload_sha256 &&
    receipt.base_story_revision === request.base_story_revision &&
    receipt.story_revision === request.base_story_revision + 1 &&
    receipt.source_revision === request.source_revision &&
    receipt.content_sha256 === request.content_sha256 &&
    receipt.reason === request.reason &&
    receipt.story_time === request.story_time &&
    JSON.stringify(current) === JSON.stringify(expected) &&
    JSON.stringify(quotes(receipt.time_evidence)) === JSON.stringify(quotes(request.time_evidence || []))
  )
}
