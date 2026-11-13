export type SceneEventWitness = {
  ref: string
  event_id: string
  event_revision: number | null
  story_order: number | null
  record_sha256: string
}
export type SceneOrderBasis = {
  version: 'scene-event-refs/2'
  kind: 'event_refs' | 'unknown'
  reason: string
  refs: SceneEventWitness[]
  covered_events: SceneEventWitness[]
}
export type SceneOrderValue = {
  order_basis?: unknown
  story_order?: number | null
}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}
function integer(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value)
}
function witness(value: unknown): value is SceneEventWitness {
  return (
    object(value) &&
    typeof value.ref === 'string' &&
    !!value.ref &&
    typeof value.event_id === 'string' &&
    !!value.event_id &&
    (value.event_revision === null || (integer(value.event_revision) && value.event_revision >= 0)) &&
    (value.story_order === null || integer(value.story_order)) &&
    typeof value.record_sha256 === 'string' &&
    value.record_sha256.length === 64
  )
}

export function readSceneOrderBasis(value: unknown): SceneOrderBasis | undefined {
  if (
    !object(value) ||
    value.version !== 'scene-event-refs/2' ||
    (value.kind !== 'event_refs' && value.kind !== 'unknown') ||
    typeof value.reason !== 'string' ||
    !Array.isArray(value.refs) ||
    !Array.isArray(value.covered_events) ||
    !value.refs.every(witness) ||
    !value.covered_events.every(witness) ||
    new Set(value.refs.map((ref) => ref.event_id)).size !== value.refs.length ||
    (value.kind === 'event_refs' && !value.refs.length) ||
    (value.kind === 'unknown' && !value.reason.trim())
  )
    return undefined
  return {
    version: value.version,
    kind: value.kind,
    reason: value.reason,
    refs: value.refs,
    covered_events: value.covered_events,
  }
}

export function hasSceneOrderBasis(scene: SceneOrderValue) {
  return scene.order_basis !== undefined && scene.order_basis !== null
}

export function sceneOrderBasisState(scene: SceneOrderValue, stale = false) {
  const basis = readSceneOrderBasis(scene.order_basis)
  const invalid =
    hasSceneOrderBasis(scene) &&
    (!basis ||
      (basis.kind === 'unknown' && scene.story_order !== null) ||
      (basis.kind === 'event_refs' &&
        (!integer(scene.story_order) || basis.refs.some((ref) => ref.story_order !== scene.story_order))))
  const state = stale
    ? 'stale'
    : invalid
      ? 'invalid'
      : !basis
        ? 'legacy'
        : basis.kind === 'unknown'
          ? 'unknown'
          : 'event_refs'
  return { basis, state }
}

export function sceneSaveMessage(storyOrder: number | null | undefined, archived = false) {
  return archived
    ? '已归档保存的绑定，历史保留。'
    : storyOrder === null || storyOrder === undefined
      ? '场景范围已保存，故事时点仍未知；未据此核实人物当前知识。正文未改变。'
      : '场景时点已确认，不修改正文。正文后续变化时须重新复核范围。'
}
