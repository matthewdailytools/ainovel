import type { components } from './api-schema'
import { readSceneOrderBasis } from './sceneOrderBasis'
import {
  object,
  SCENE_PAYLOAD_HASH_VERSION,
  selectedEvidence,
  type SceneRow,
  type TimeEvidence,
} from './sceneReconfirmation'

export const UNKNOWN_REASON_VERSION = 'scene-unknown-reason-correction/1'
export const UNKNOWN_REASON_AUTHORITY = 'explicit_correction_not_model_or_literary_review'
export type Actor = 'author' | 'authorized_operator'
export type Evidence = TimeEvidence & { content_sha256: string }
export type SceneSnapshot = components['schemas']['SceneTime']
export type CorrectionScene = SceneRow & {
  branch_id: string
  chapter_id: string
  payload: SceneSnapshot
  payload_sha256: string
  payload_hash_version: typeof SCENE_PAYLOAD_HASH_VERSION
}
export type Preview = {
  version: typeof UNKNOWN_REASON_VERSION
  authority: typeof UNKNOWN_REASON_AUTHORITY
  scene_id: string
  scene: CorrectionScene
  source: Evidence
  request_basis: {
    base_story_revision: number
    base_revision: number
    base_payload_sha256: string
    source_revision: number
    content_sha256: string
  }
}
export type CorrectionInput = {
  version: typeof UNKNOWN_REASON_VERSION
  client_request_id: string
  actor: Actor
  base_payload_sha256: string
  source_revision: number
  content_sha256: string
  reason: string
  evidence: Evidence[]
}
export type SaveInput = {
  base_story_revision: number
  base_revision: number
  scene: SceneSnapshot
  archived: false
  unknown_reason_correction: CorrectionInput
}
export type Pending = { preview: Preview; request: SaveInput }
export type Receipt = {
  version: typeof UNKNOWN_REASON_VERSION
  authority: typeof UNKNOWN_REASON_AUTHORITY
  actor: Actor
  scene_id: string
  client_request_id: string
  request_sha256: string
  reason: string
  previous_reason: string
  original_reason: string
  previous_scene_revision: number
  scene_revision: number
  base_story_revision: number
  story_revision: number
  source_revision: number
  content_sha256: string
  previous_payload_sha256: string
  previous_raw_json_sha256: string
  payload_sha256: string
  raw_json_sha256: string
  corrected_at: number
  evidence: Evidence[]
  story_time_inferred: false
  model_review_performed: false
  literary_review_performed: false
}
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0
const hash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
const nonempty = (value: unknown): value is string => typeof value === 'string' && !!value.trim()
const actor = (value: unknown): value is Actor => value === 'author' || value === 'authorized_operator'
export function canonical(value: unknown): string {
  const normalize = (item: unknown): unknown =>
    Array.isArray(item)
      ? item.map(normalize)
      : object(item)
        ? Object.fromEntries(
            Object.keys(item)
              .sort()
              .map((key) => [key, normalize(item[key])]),
          )
        : item
  return JSON.stringify(normalize(value))
}
export function canCorrectUnknownScene(scene: SceneRow) {
  return (
    scene.archived === 0 &&
    scene.payload_hash_version === SCENE_PAYLOAD_HASH_VERSION &&
    hash(scene.payload_sha256) &&
    scene.payload.story_order === null &&
    readSceneOrderBasis(scene.payload.order_basis)?.kind === 'unknown'
  )
}
export function readEvidence(value: unknown): Evidence | undefined {
  if (
    !object(value) ||
    !nonempty(value.chapter_id) ||
    !integer(value.revision) ||
    !integer(value.start) ||
    !integer(value.end) ||
    value.end <= value.start ||
    !nonempty(value.quote) ||
    value.quote.length !== value.end - value.start ||
    !hash(value.content_sha256)
  )
    return undefined
  return value as Evidence
}
export function readCorrectionPreview(
  value: unknown,
  branchId: string,
  chapterId: string,
  sceneId: string,
): Preview | undefined {
  if (
    !object(value) ||
    value.version !== UNKNOWN_REASON_VERSION ||
    value.authority !== UNKNOWN_REASON_AUTHORITY ||
    value.scene_id !== sceneId ||
    !object(value.scene) ||
    value.scene.id !== sceneId ||
    value.scene.branch_id !== branchId ||
    value.scene.chapter_id !== chapterId ||
    !integer(value.scene.revision) ||
    value.scene.archived !== 0 ||
    value.scene.payload_hash_version !== SCENE_PAYLOAD_HASH_VERSION ||
    !hash(value.scene.payload_sha256) ||
    !object(value.scene.payload) ||
    !nonempty(value.scene.payload.title) ||
    !integer(value.scene.payload.source_revision) ||
    !integer(value.scene.payload.start) ||
    !integer(value.scene.payload.end) ||
    value.scene.payload.end <= value.scene.payload.start ||
    value.scene.payload.story_order !== null ||
    typeof value.scene.payload.story_time !== 'string' ||
    !['author', 'reader', 'character'].includes(String(value.scene.payload.audience)) ||
    (value.scene.payload.character_id !== null && typeof value.scene.payload.character_id !== 'string') ||
    typeof value.scene.payload.note !== 'string' ||
    readSceneOrderBasis(value.scene.payload.order_basis)?.kind !== 'unknown' ||
    !object(value.request_basis)
  )
    return undefined
  const basis = value.request_basis,
    source = readEvidence(value.source),
    payload = value.scene.payload
  if (
    !source ||
    source.chapter_id !== chapterId ||
    source.revision !== payload.source_revision ||
    source.start !== payload.start ||
    source.end !== payload.end ||
    Object.keys(basis).sort().join(',') !==
      'base_payload_sha256,base_revision,base_story_revision,content_sha256,source_revision' ||
    !integer(basis.base_story_revision) ||
    basis.base_revision !== value.scene.revision ||
    basis.base_payload_sha256 !== value.scene.payload_sha256 ||
    basis.source_revision !== source.revision ||
    basis.content_sha256 !== source.content_sha256
  )
    return undefined
  // Preserve the complete normalized public payload; no scene field is rebuilt from a UI draft.
  return value as Preview
}
export function correctionEvidence(source: Evidence, start: number, end: number): Evidence | undefined {
  const quote = selectedEvidence(source, start, end)
  return quote ? { ...quote, content_sha256: source.content_sha256 } : undefined
}
export function createCorrection(
  preview: Preview,
  reason: string,
  quotes: Evidence[],
  nonce: string,
  by: Actor,
): SaveInput {
  if (!reason.trim() || reason.length > 2000) throw new Error('请填写本次更正的未知原因，最多2000字。')
  if (!actor(by)) throw new Error('请明确记录操作者来源。')
  if (!quotes.length || quotes.length > 30) throw new Error('请从原场景选取至少一条、最多30条依据。')
  for (const quote of quotes) {
    const expected = correctionEvidence(
      preview.source,
      quote.start - preview.source.start,
      quote.end - preview.source.start,
    )
    if (!expected || canonical(expected) !== canonical(quote))
      throw new Error('选段已不对应原场景，请重新选取。')
  }
  return {
    base_story_revision: preview.request_basis.base_story_revision,
    base_revision: preview.request_basis.base_revision,
    scene: structuredClone(preview.scene.payload),
    archived: false,
    unknown_reason_correction: {
      version: UNKNOWN_REASON_VERSION,
      client_request_id: nonce,
      actor: by,
      base_payload_sha256: preview.request_basis.base_payload_sha256,
      source_revision: preview.request_basis.source_revision,
      content_sha256: preview.request_basis.content_sha256,
      reason,
      evidence: structuredClone(quotes),
    },
  }
}
export function readPendingCorrection(
  value: unknown,
  branchId: string,
  chapterId: string,
  sceneId: string,
): Pending | undefined {
  if (!object(value)) return undefined
  const preview = readCorrectionPreview(value.preview, branchId, chapterId, sceneId)
  if (!preview || !object(value.request) || !object(value.request.unknown_reason_correction)) return undefined
  const input = value.request.unknown_reason_correction
  if (
    !nonempty(input.client_request_id) ||
    input.client_request_id.length < 8 ||
    input.client_request_id.length > 128 ||
    !nonempty(input.reason) ||
    !actor(input.actor) ||
    !Array.isArray(input.evidence) ||
    !input.evidence.every((row) => !!readEvidence(row))
  )
    return undefined
  try {
    const expected = createCorrection(
      preview,
      input.reason,
      input.evidence as Evidence[],
      input.client_request_id,
      input.actor,
    )
    if (canonical(expected) !== canonical(value.request)) return undefined
    return { preview, request: expected }
  } catch {
    return undefined
  }
}
export function readCorrectionReceipt(value: unknown, sceneId: string, nonce: string): Receipt | undefined {
  if (
    !object(value) ||
    value.version !== UNKNOWN_REASON_VERSION ||
    value.authority !== UNKNOWN_REASON_AUTHORITY ||
    value.scene_id !== sceneId ||
    value.client_request_id !== nonce ||
    !actor(value.actor) ||
    !hash(value.request_sha256) ||
    !nonempty(value.reason) ||
    !nonempty(value.previous_reason) ||
    !nonempty(value.original_reason) ||
    !integer(value.previous_scene_revision) ||
    value.scene_revision !== value.previous_scene_revision + 1 ||
    !integer(value.base_story_revision) ||
    value.story_revision !== value.base_story_revision + 1 ||
    !integer(value.source_revision) ||
    !hash(value.content_sha256) ||
    !hash(value.previous_payload_sha256) ||
    !hash(value.previous_raw_json_sha256) ||
    !hash(value.payload_sha256) ||
    !hash(value.raw_json_sha256) ||
    typeof value.corrected_at !== 'number' ||
    !Number.isFinite(value.corrected_at) ||
    value.story_time_inferred !== false ||
    value.model_review_performed !== false ||
    value.literary_review_performed !== false ||
    !Array.isArray(value.evidence) ||
    !value.evidence.length ||
    value.evidence.length > 30 ||
    !value.evidence.every((row) => !!readEvidence(row))
  )
    return undefined
  return value as Receipt
}
export function correctionReceiptMatches(receipt: Receipt, pending: Pending) {
  const correction = pending.request.unknown_reason_correction
  const payload: unknown = pending.preview.scene.payload
  const prior =
    object(payload) && object(payload.unknown_reason_corrected) ? payload.unknown_reason_corrected : undefined
  const previousReason = readSceneOrderBasis(pending.preview.scene.payload.order_basis)?.reason
  const originalReason = prior ? prior.original_reason : previousReason
  return (
    receipt.actor === correction.actor &&
    receipt.reason === correction.reason &&
    receipt.previous_scene_revision === pending.request.base_revision &&
    receipt.base_story_revision === pending.request.base_story_revision &&
    receipt.previous_payload_sha256 === correction.base_payload_sha256 &&
    receipt.source_revision === correction.source_revision &&
    receipt.content_sha256 === correction.content_sha256 &&
    canonical(receipt.evidence) === canonical(correction.evidence) &&
    receipt.previous_reason === previousReason &&
    receipt.original_reason === originalReason
  )
}
