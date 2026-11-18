import { validateVersionGraph } from './projectSyncModel'
import { compactJSON } from './storySyncModel'

const cardFields = ['observation', 'interpretation', 'technique', 'conditions', 'exceptions'] as const
const citationFields = [
  'book_id',
  'unit_id',
  'file_sha256',
  'title',
  'selection_start',
  'selection_end',
  'selection_sha256',
  'evidence_sha256',
  'evidence_start',
  'evidence_end',
] as const

function invalid(message: string): never {
  throw new Error(message)
}

function fields(input: unknown, allowed?: readonly string[]) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) return invalid('手法卡档案格式无效。')
  const value = input as Record<string, unknown>
  if (allowed && Object.keys(value).some((key) => !allowed.includes(key)))
    invalid('手法卡档案含无法识别的字段。')
  return value
}

function rows(input: unknown, maximum: number, minimum = 0): unknown[] {
  if (!Array.isArray(input) || input.length > maximum || input.length < minimum)
    return invalid('手法卡档案的记录数量超出范围。')
  return input
}

function text(value: unknown, maximum: number) {
  if (
    typeof value !== 'string' ||
    value.length > maximum * 2 ||
    Array.from(value).length > maximum ||
    /\p{Surrogate}/u.test(value) ||
    !value.trim()
  )
    return invalid('手法卡档案中的名称或内容无效。')
  return value
}

function identifier(value: unknown) {
  const id = text(value, 128)
  if (!/^[a-z0-9_-]+$/i.test(id)) invalid('手法卡来源编号无效。')
  return id
}

function hash(value: unknown) {
  const id = text(value, 64)
  if (!/^[a-f0-9]{64}$/.test(id)) invalid('手法卡档案中的 SHA-256 无效。')
  return id
}

function offset(value: unknown) {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0)
    return invalid('手法卡修订编号或选区无效。')
  return value
}

function parseCitation(value: unknown) {
  const row = fields(value, citationFields)
  const selection_start = offset(row.selection_start)
  const selection_end = offset(row.selection_end)
  const evidence_start = row.evidence_start === null ? null : offset(row.evidence_start)
  const evidence_end = row.evidence_end === null ? null : offset(row.evidence_end)
  if (
    selection_end <= selection_start ||
    (evidence_start === null) !== (evidence_end === null) ||
    (evidence_start !== null &&
      evidence_end !== null &&
      (evidence_start < selection_start || evidence_end > selection_end || evidence_end <= evidence_start))
  )
    invalid('手法卡证据必须位于冻结选段内。')
  return {
    book_id: identifier(row.book_id),
    unit_id: identifier(row.unit_id),
    file_sha256: hash(row.file_sha256),
    title: text(row.title, 1000),
    selection_start,
    selection_end,
    selection_sha256: hash(row.selection_sha256),
    evidence_sha256: hash(row.evidence_sha256),
    evidence_start,
    evidence_end,
  }
}

function assessmentOf(value: unknown): 'ready' | 'incomplete' | 'archived' {
  if (value !== 'ready' && value !== 'incomplete' && value !== 'archived')
    return invalid('手法卡完成状态无效。')
  return value
}

function parseVersion(value: unknown) {
  const row = fields(value, [
    'id',
    'parent_id',
    'session_id',
    'card_id',
    'revision',
    'assessment',
    'card',
    'citation',
    'goal',
    'missing_fields',
  ])
  const assessment = assessmentOf(row.assessment)
  const missing = [...cardFields, 'evidence'] as const
  const missing_fields = rows(row.missing_fields ?? [], 6).map((value) => {
    const field = missing.find((item) => item === value)
    return field ?? invalid('未完成手法卡包含无效缺项。')
  })
  if (
    (assessment === 'incomplete' &&
      (!missing_fields.length || new Set(missing_fields).size !== missing_fields.length)) ||
    (assessment !== 'incomplete' && missing_fields.length)
  )
    invalid('手法卡完成状态与记录的缺项不一致。')
  const card = row.card == null ? null : fields(row.card, cardFields)
  const citation = row.citation == null ? null : parseCitation(row.citation)
  const goal = row.goal == null ? null : text(row.goal, 3000)
  if (assessment === 'ready' ? !card || !citation || !goal : card || citation || goal)
    invalid('完整手法卡须保存内容、目标和引用地址，其他状态仅保存缺项。')
  return {
    id: hash(row.id),
    parent_id: row.parent_id === null ? null : hash(row.parent_id),
    session_id: identifier(row.session_id),
    card_id: identifier(row.card_id),
    revision: offset(row.revision),
    assessment,
    card: card
      ? {
          observation: text(card.observation, 2000),
          interpretation: text(card.interpretation, 2000),
          technique: text(card.technique, 2000),
          conditions: text(card.conditions, 2000),
          exceptions: text(card.exceptions, 2000),
        }
      : null,
    citation,
    goal,
    missing_fields,
  }
}

export type ArchivedReferenceCard = ReturnType<typeof parseVersion>
export type ReferenceCardFields = NonNullable<ArchivedReferenceCard['card']>
type Digest = (data: string) => Promise<string>
export type ReferenceScope = { session_id: string; card_id: string }
export type ReferenceCardHead = ReferenceScope & { revision_id: string }
const format = 'ainovel-manuscript-sync/1' as const
export type ReferenceCardVersionObject = {
  format: typeof format
  kind: 'reference-card-version'
  branch_id: string
  version: ArchivedReferenceCard
}
export type ReferenceCardHeadObject = ReferenceCardHead & {
  format: typeof format
  kind: 'reference-card-head'
  branch_id: string
}
export type ReferenceSyncObject = ReferenceCardVersionObject | ReferenceCardHeadObject
export const referenceCardKey = (scope: ReferenceScope) => JSON.stringify([scope.session_id, scope.card_id])
const citationKeys = [
  'book_id',
  'unit_id',
  'file_sha256',
  'selection_start',
  'selection_end',
  'selection_sha256',
] as const

export async function referenceHeadObjectId(branchId: string, scope: ReferenceScope, digest: Digest) {
  return `reference-card:${await digest(compactJSON([branchId, scope.session_id, scope.card_id]))}`
}

function referenceHead(row: Record<string, unknown>): ReferenceCardHead {
  return {
    session_id: identifier(row.session_id),
    card_id: identifier(row.card_id),
    revision_id: hash(row.revision_id),
  }
}

export async function validateReferenceSyncObject(
  work: string,
  objectId: string,
  payload: unknown,
  digest: Digest,
): Promise<ReferenceSyncObject> {
  const isVersion = objectId.startsWith('reference-card-version:')
  const row = fields(payload, [
    'format',
    'kind',
    'branch_id',
    ...(isVersion ? ['version'] : ['session_id', 'card_id', 'revision_id']),
  ])
  if (row.format !== format || row.branch_id !== work) invalid('云端手法卡与当前工作版本不符。')
  if (isVersion && row.kind === 'reference-card-version') {
    const version = parseVersion(row.version)
    const { id, ...values } = version
    if (
      objectId !== `reference-card-version:${id}` ||
      (await digest(compactJSON([work, values]))) !== id ||
      version.parent_id === id
    )
      invalid('手法卡正式修订编号与内容不匹配。')
    return { format, kind: 'reference-card-version', branch_id: work, version }
  }
  if (!isVersion && row.kind === 'reference-card-head') {
    const head = referenceHead(row)
    if (objectId !== (await referenceHeadObjectId(work, head, digest)))
      invalid('云端手法卡采用头指向另一条记录。')
    return { format, kind: 'reference-card-head', branch_id: work, ...head }
  }
  return invalid('云端手法卡格式无效。')
}

export function referenceSyncSignature(object: ReferenceSyncObject) {
  return compactJSON(object)
}

function requireEarlierCard(version: ArchivedReferenceCard, parent: ArchivedReferenceCard | undefined) {
  if (
    !parent ||
    referenceCardKey(parent) !== referenceCardKey(version) ||
    parent.revision >= version.revision
  )
    return invalid('手法卡父修订不是同一卡片的较早版本。')
  return parent
}

function confirmCitation(version: ArchivedReferenceCard, prior: ArchivedReferenceCard['citation']) {
  if (prior && version.citation && citationKeys.some((key) => prior[key] !== version.citation?.[key]))
    invalid('手法卡的原始选段在同一会话中发生变化。')
}

export function validateReferenceVersionParent(
  version: ArchivedReferenceCard,
  lookup: (id: string) => ArchivedReferenceCard | undefined,
) {
  if (!version.parent_id) return
  let parent = requireEarlierCard(version, lookup(version.parent_id))
  if (version.citation) {
    while (parent.parent_id && !parent.citation) parent = requireEarlierCard(parent, lookup(parent.parent_id))
    confirmCitation(version, parent.citation)
  }
}

export async function createEditedReferenceVersion(
  branchId: string,
  base: ArchivedReferenceCard,
  card: ReferenceCardFields,
  digest: Digest,
) {
  if (base.assessment !== 'ready' || !base.citation || !base.card)
    return invalid('请从带准确引用地址的已保存手法卡开始。')
  if (cardFields.some((key) => !card[key].trim()))
    return invalid('请填齐观察、解释、手法、适用条件和须替换处再确认。')
  const { id: parent_id, ...before } = base
  const version = { ...before, parent_id, revision: base.revision + 1, card }
  return parseVersion({ ...version, id: await digest(compactJSON([branchId, version])) })
}

export function describeReferenceVersion(version: ArchivedReferenceCard) {
  const { card, citation } = version
  if (!card || !citation)
    return [
      `卡片 ${version.card_id} · 修订 ${version.revision}`,
      version.assessment === 'archived' ? '已归档' : `未完成 · 缺 ${version.missing_fields.join('、')}`,
    ].join('\n')
  return [
    `修订 ${version.revision} · 来源 ${citation.title}`,
    `观察　${card.observation}`,
    `解释　${card.interpretation}`,
    `可迁移手法　${card.technique}`,
    `适用条件　${card.conditions}`,
    `须替换处　${card.exceptions}`,
  ].join('\n')
}

export async function validateReferenceHistory(input: unknown, branchId: string, digest: Digest) {
  const history = fields(input, ['adopted_heads', 'versions', 'source_devices', 'excerpts'])
  const versions = rows(history.versions, 5000, 1).map(parseVersion)
  const indexed = validateVersionGraph(versions, null, '手法卡')
  const adopted_heads = rows(history.adopted_heads, 5000).map((value) => {
    const head = fields(value, ['session_id', 'card_id', 'revision_id'])
    const row = referenceHead(head)
    const version = indexed.get(row.revision_id)
    if (!version || referenceCardKey(version) !== referenceCardKey(row))
      invalid('手法卡采用头指向另一条记录。')
    return row
  })
  if (new Set(adopted_heads.map(referenceCardKey)).size !== adopted_heads.length)
    invalid('手法卡档案中有重复采用头。')

  const citations = new Map<string, ArchivedReferenceCard['citation']>()
  for (const { id, ...version } of [...versions].sort((left, right) => left.revision - right.revision)) {
    if ((await digest(compactJSON([branchId, version]))) !== id)
      invalid('手法卡正式修订编号与内容或工作版本不一致。')
    const parent = version.parent_id
      ? requireEarlierCard({ id, ...version }, indexed.get(version.parent_id))
      : undefined
    const prior = parent && citations.get(parent.id)
    if (prior) confirmCitation({ id, ...version }, prior)
    citations.set(id, version.citation ?? prior ?? null)
  }
  const source_devices = Object.fromEntries(
    Object.entries(fields(history.source_devices ?? {})).map(([key, value]) => {
      if (!indexed.has(key)) invalid('手法卡设备来源找不到对应版本。')
      return [key, text(value, 128)]
    }),
  )
  const excerpts: [string, string][] = []
  for (const [key, content] of Object.entries(fields(history.excerpts ?? {}))) {
    const citation = indexed.get(key)?.citation
    const quote = text(content, 2000)
    if (
      !citation ||
      quote.length > citation.selection_end - citation.selection_start ||
      (citation.evidence_start !== null &&
        citation.evidence_end !== null &&
        quote.length !== citation.evidence_end - citation.evidence_start) ||
      (await digest(quote)) !== citation.evidence_sha256
    )
      invalid('选带的手法卡原文与引用校验值不一致。')
    excerpts.push([key, quote])
  }
  return {
    adopted_heads,
    versions,
    source_devices,
    ...(excerpts.length ? { excerpts: Object.fromEntries(excerpts) } : {}),
  }
}

export type ReferenceHistory = Awaited<ReturnType<typeof validateReferenceHistory>>
