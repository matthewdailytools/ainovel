import {
  decodePortableRestoreState,
  lengthTargetKey,
  type PortableCheckpointFact,
  type PortableCheckpointLengthTarget,
} from './portableCheckpoint'
import { projectId as validateProjectId } from './projectSyncModel'
import { compactJSON } from './storySyncModel'

const format = 'ainovel-manuscript-sync/1' as const
type Hash = (text: string) => Promise<string>
type FactSlot = { chapter_id: string }
type LengthSlot = Pick<PortableCheckpointLengthTarget, 'scope' | 'scope_id'>
type LengthScope = PortableCheckpointLengthTarget['scope']
type FactDraft = FactSlot & {
  parent_id: string | null
  chapter_revision_id: string
  facts: PortableCheckpointFact[]
}
type LengthDraft = PortableCheckpointLengthTarget & {
  parent_id: string | null
  outline_version_id: string | null
}
const factVersionKind = 'fact-set-version'
const lengthVersionKind = 'length-target-version'
const factHeadKind = 'fact-set-head'
const lengthHeadKind = 'length-target-head'
type Envelope = { format: typeof format; branch_id: string }
export type RestoreStateDraft =
  { kind: typeof factVersionKind; draft: FactDraft } | { kind: typeof lengthVersionKind; draft: LengthDraft }
export type RestoreStateVersion =
  | (Envelope & { kind: typeof factVersionKind; version: FactDraft & { id: string } })
  | (Envelope & { kind: typeof lengthVersionKind; version: LengthDraft & { id: string } })
export type RestoreStateHead =
  | (Envelope & FactSlot & { kind: typeof factHeadKind; revision_id: string })
  | (Envelope & LengthSlot & { kind: typeof lengthHeadKind; revision_id: string })
export type RestoreStateSyncObject = RestoreStateVersion | RestoreStateHead

function invalid(): never {
  throw new Error('正式事实或篇幅目标的云端编号、范围、修订或摘要无效。')
}

function fields(value: unknown, allowed: string[]) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) invalid()
  const row = value as Record<string, unknown>
  if (Object.keys(row).length !== allowed.length || allowed.some((field) => !Object.hasOwn(row, field)))
    invalid()
  return row
}

function hash(value: unknown) {
  if (typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value)) invalid()
  return value
}

function scopeFields(row: Record<string, unknown>): LengthSlot {
  if (row.scope !== 'book' && row.scope !== 'volume' && row.scope !== 'chapter') invalid()
  const scope: LengthScope = row.scope
  const scope_id = row.scope_id === null ? null : validateProjectId(row.scope_id)
  if ((scope === 'book') !== (scope_id === null)) invalid()
  return { scope, scope_id }
}

export function isRestoreStateObjectId(id: string) {
  return [factVersionKind, lengthVersionKind, factHeadKind, lengthHeadKind].some((kind) =>
    id.startsWith(kind + ':'),
  )
}

export function isRestoreStateVersion(object: RestoreStateSyncObject): object is RestoreStateVersion {
  return object.kind === factVersionKind || object.kind === lengthVersionKind
}

export function isRestoreStateSyncObject(object: { kind: string } | null): object is RestoreStateSyncObject {
  return !!object && [factVersionKind, lengthVersionKind, factHeadKind, lengthHeadKind].includes(object.kind)
}

export function factSetSlot(chapterId: string) {
  return `fact:${chapterId}`
}

export function groupFactsByChapter(facts: PortableCheckpointFact[]) {
  const byChapter = new Map<string, PortableCheckpointFact[]>()
  for (const fact of facts) {
    const id = fact.evidence.chapter_id
    const rows = byChapter.get(id) ?? []
    rows.push(fact)
    byChapter.set(id, rows)
  }
  return byChapter
}

export function restoreStateSlot(object: RestoreStateSyncObject) {
  switch (object.kind) {
    case factVersionKind:
      return factSetSlot(object.version.chapter_id)
    case factHeadKind:
      return factSetSlot(object.chapter_id)
    case lengthVersionKind:
      return lengthTargetKey(object.version)
    case lengthHeadKind:
      return lengthTargetKey(object)
  }
}

export function restoreStateHead(object: RestoreStateVersion): RestoreStateHead {
  const { branch_id, version } = object
  const envelope = { format, branch_id, revision_id: version.id }
  return object.kind === factVersionKind
    ? { ...envelope, kind: factHeadKind, chapter_id: object.version.chapter_id }
    : { ...envelope, kind: lengthHeadKind, scope: object.version.scope, scope_id: object.version.scope_id }
}

export async function restoreStateObjectId(object: RestoreStateSyncObject, digest: Hash) {
  if (isRestoreStateVersion(object)) return `${object.kind}:${object.version.id}`
  const fields =
    object.kind === factHeadKind
      ? [object.branch_id, object.chapter_id]
      : [object.branch_id, object.scope, object.scope_id]
  return `${object.kind}:${await digest(compactJSON(fields))}`
}

export function restoreStateObjectSignature(object: RestoreStateSyncObject) {
  return compactJSON([
    object.kind,
    object.branch_id,
    isRestoreStateVersion(object) ? object.version.id : object.revision_id,
  ])
}

export function restoreStatePublicationSources(object: RestoreStateSyncObject) {
  switch (object.kind) {
    case factVersionKind:
      return [
        `revision:${object.version.chapter_revision_id}`,
        ...(object.version.parent_id ? [`${factVersionKind}:${object.version.parent_id}`] : []),
      ]
    case factHeadKind:
      return [`${factVersionKind}:${object.revision_id}`, `chapter:${object.chapter_id}`]
    case lengthVersionKind:
      return [
        ...(object.version.outline_version_id
          ? [`outline-version:${object.version.outline_version_id}`]
          : []),
        ...(object.version.parent_id ? [`${lengthVersionKind}:${object.version.parent_id}`] : []),
      ]
    case lengthHeadKind:
      return [
        `${lengthVersionKind}:${object.revision_id}`,
        ...(object.scope === 'chapter'
          ? [`chapter:${object.scope_id}`]
          : object.scope === 'volume'
            ? [`outline:${object.branch_id}`]
            : []),
      ]
  }
}

export async function buildRestoreStateVersion(
  branch_id: string,
  input: RestoreStateDraft,
  digest: Hash,
): Promise<RestoreStateVersion> {
  const id = await digest(compactJSON([branch_id, input.draft]))
  return input.kind === factVersionKind
    ? { format, kind: input.kind, branch_id, version: { id, ...input.draft } }
    : { format, kind: input.kind, branch_id, version: { id, ...input.draft } }
}

export async function validateRestoreStateSyncObject(
  branch_id: string,
  objectId: string,
  input: unknown,
  digest: Hash,
) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) invalid()
  const { kind } = input as { kind: unknown }
  let output: RestoreStateSyncObject
  const envelope = { format, branch_id }
  if (kind === factHeadKind || kind === lengthHeadKind) {
    const keys = kind === factHeadKind ? ['chapter_id'] : ['scope', 'scope_id']
    const row = fields(input, ['format', 'kind', 'branch_id', 'revision_id', ...keys])
    if (row.format !== format || row.branch_id !== branch_id) invalid()
    const revision_id = hash(row.revision_id)
    output =
      kind === factHeadKind
        ? { ...envelope, kind, revision_id, chapter_id: validateProjectId(row.chapter_id) }
        : { ...envelope, kind, revision_id, ...scopeFields(row) }
  } else {
    if (kind !== factVersionKind && kind !== lengthVersionKind) invalid()
    const row = fields(input, ['format', 'kind', 'branch_id', 'version'])
    if (row.format !== format || row.branch_id !== branch_id) invalid()
    const keys =
      kind === factVersionKind
        ? ['chapter_id', 'chapter_revision_id', 'facts']
        : ['scope', 'scope_id', 'outline_version_id', 'targets']
    const source = fields(row.version, ['id', 'parent_id', ...keys])
    const id = hash(source.id)
    const parent_id = source.parent_id === null ? null : hash(source.parent_id)
    if (parent_id === id) invalid()
    let draft: RestoreStateDraft
    if (kind === factVersionKind) {
      const chapter_id = validateProjectId(source.chapter_id)
      const chapter_revision_id = validateProjectId(source.chapter_revision_id)
      const { facts } = decodePortableRestoreState({ facts: source.facts, length_targets: [] })
      if (
        facts.some(
          ({ evidence }) =>
            evidence.chapter_id !== chapter_id || evidence.revision_id !== chapter_revision_id,
        )
      )
        invalid()
      draft = { kind, draft: { parent_id, chapter_id, chapter_revision_id, facts } }
    } else {
      const slot = scopeFields(source)
      const outline_version_id =
        source.outline_version_id === null ? null : validateProjectId(source.outline_version_id)
      if ((slot.scope === 'volume') !== (outline_version_id !== null)) invalid()
      const [target] = decodePortableRestoreState({
        facts: [],
        length_targets: [{ ...slot, targets: source.targets }],
      }).length_targets
      draft = { kind, draft: { ...target, parent_id, outline_version_id } }
    }
    output = await buildRestoreStateVersion(branch_id, draft, digest)
    if (output.version.id !== id) invalid()
  }
  if ((await restoreStateObjectId(output, digest)) !== objectId) invalid()
  return output
}
