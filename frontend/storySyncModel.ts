import type { ProjectSource } from './projectSyncModel'
import type { SettingsEvidence } from './settingsSyncModel'

const format = 'ainovel-manuscript-sync/1' as const
const maxVersions = 20_000
const truths = ['planned', 'actual', 'unknown', 'conflict'] as const
export const storyPromiseStages = [
  'planned',
  'planted',
  'reinforced',
  'resolved',
  'retained',
  'abandoned',
] as const
const stateCategories = ['knowledge', 'capability', 'resource', 'injury', 'other'] as const
const audiences = ['author', 'character', 'reader'] as const

type Truth = (typeof truths)[number]
export type StoryPromiseStage = {
  status: (typeof storyPromiseStages)[number]
  event_id: string | null
  sources: []
  note: string
}
type BaseRecord = {
  title: string
  notes: string
  sources: []
  depends_on: string[]
  volume: string
}
export type StoryRecord = BaseRecord &
  (
    | { kind: 'character'; aliases: string[]; profile: string }
    | {
        kind: 'event'
        thread: string
        story_order: number | null
        story_time: string
        status: Truth
        participants: string[]
        after: string[]
      }
    | {
        kind: 'state'
        character_id: string
        event_id: string
        category: (typeof stateCategories)[number]
        key: string
        value: string
        audience: (typeof audiences)[number]
        truth: Truth
        until_event_id: string | null
      }
    | {
        kind: 'relationship'
        from_id: string
        to_id: string
        relation: string
        event_id: string | null
        truth: Truth
      }
    | { kind: 'promise'; thread: string; stages: StoryPromiseStage[] }
  )
export type StoryVersion = {
  id: string
  parent_id: string | null
  record_id: string
  archived: boolean
  record: StoryRecord
  evidence: { record: SettingsEvidence[]; stages: SettingsEvidence[][] }
  dependencies: Record<string, string>
}
export type StoryVersionDraft = Omit<StoryVersion, 'id'>
export type StoryVersionRecord = { version: StoryVersion; source?: ProjectSource; recordedDeviceId?: string }
export type StoryVersionObject = {
  format: typeof format
  kind: 'story-version'
  branch_id: string
  version: StoryVersion
}
export type StoryHeadObject = {
  format: typeof format
  kind: 'story-head'
  branch_id: string
  record_id: string
  revision_id: string
}
export type StoryChapterRevision = { id: string; chapter_id: string; sha256: string; content: string }
export type StoryNativeRevision = {
  revision: number
  version_id: string
  created: number
  dependencies: Record<string, number>
  evidence: { record: string[]; stages: string[][] }
}
export type StoryHistory = {
  adopted_heads: Record<string, string>
  versions: StoryVersion[]
  source_devices: Record<string, string>
  native_history: Record<string, StoryNativeRevision[]>
}

const invalid = (message: string): never => {
  throw new Error(message)
}

function object(value: unknown, keys?: string[]) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid('故事记录格式无效。')
  const row = value as Record<string, unknown>
  if (keys && Object.keys(row).some((key) => !keys.includes(key))) invalid('故事记录含无法识别的字段。')
  return row
}

function list(value: unknown, max: number, min = 0): unknown[] {
  if (!Array.isArray(value) || value.length > max || value.length < min)
    return invalid('故事记录的列表项数不符合要求。')
  return value
}

const defaulted = (value: unknown, otherwise: unknown) => (value === undefined ? otherwise : value)
const hasText = (value: string) => !!value.replace(/[\p{White_Space}\x1c-\x1f]/gu, '')

function text(value: unknown, max: number, min = 0): string {
  if (
    typeof value !== 'string' ||
    /\p{Surrogate}/u.test(value) ||
    value.length < min ||
    value.length > max * 2 ||
    Array.from(value).length > max
  )
    return invalid('故事记录的文字缺失、无效或超出长度。')
  return value
}

function identifier(value: unknown): string {
  if (typeof value !== 'string' || !value || value.length > 128 || /[^a-zA-Z0-9_-]/u.test(value))
    return invalid('故事记录或来源编号无效。')
  return value
}

function hashId(value: unknown): string {
  if (typeof value !== 'string' || value.length !== 64 || /[^a-f0-9]/u.test(value))
    return invalid('故事记录的 SHA-256 编号无效。')
  return value
}

const optionalReference = (value: unknown) =>
  value === null || value === undefined ? null : value === '' ? '' : identifier(value)
const referenceList = (value: unknown, max: number) => list(defaulted(value, []), max).map(identifier)
function choice<const Options extends readonly string[]>(value: unknown, options: Options): Options[number] {
  if (typeof value !== 'string' || !options.includes(value)) return invalid('故事记录的类型或状态无效。')
  return value
}

function integer(value: unknown, min = -Number.MAX_SAFE_INTEGER) {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < min)
    return invalid('故事记录的修订或选区位置无效。')
  return value
}

function indexValues<T>(input: unknown, max: number, each: (value: unknown) => T): Record<string, T> {
  const entries = Object.entries(object(input))
  if (entries.length > max) invalid('故事记录的关联或历史超出数量。')
  return Object.fromEntries(entries.map(([key, value]) => [identifier(key), each(value)]))
}

const baseKeys = ['kind', 'title', 'notes', 'sources', 'depends_on', 'volume']

function baseRecord(row: Record<string, unknown>): BaseRecord {
  list(defaulted(row.sources, []), 0)
  const title = text(row.title, 200, 1)
  if (!hasText(title)) invalid('请填写故事记录名称。')
  return {
    title,
    notes: text(defaulted(row.notes, ''), 4000),
    sources: [],
    depends_on: referenceList(row.depends_on, 50),
    volume: text(defaulted(row.volume, ''), 100),
  }
}

function stageRecord(input: unknown): StoryPromiseStage {
  const row = object(input, ['status', 'event_id', 'sources', 'note'])
  list(defaulted(row.sources, []), 0)
  const status = choice(row.status, storyPromiseStages)
  const note = text(defaulted(row.note, ''), 2000)
  if ((status === 'retained' || status === 'abandoned') && !hasText(note))
    invalid('作者保留或放弃伏笔时须写明理由。')
  return { status, event_id: optionalReference(row.event_id), sources: [], note }
}

export function parseStoryRecord(input: unknown): StoryRecord {
  const { kind } = object(input)
  if (kind === 'character') {
    const row = object(input, [...baseKeys, 'aliases', 'profile'])
    return {
      kind,
      ...baseRecord(row),
      aliases: list(defaulted(row.aliases, []), 30).map((alias) => text(alias, 200)),
      profile: text(defaulted(row.profile, ''), 8000),
    }
  }
  if (kind === 'event') {
    const row = object(input, [
      ...baseKeys,
      'thread',
      'story_order',
      'story_time',
      'status',
      'participants',
      'after',
    ])
    return {
      kind,
      ...baseRecord(row),
      thread: text(defaulted(row.thread, '主线'), 100),
      story_order:
        row.story_order === null || row.story_order === undefined ? null : integer(row.story_order),
      story_time: text(defaulted(row.story_time, '未知'), 200),
      status: choice(defaulted(row.status, 'planned'), truths),
      participants: referenceList(row.participants, 50),
      after: referenceList(row.after, 50),
    }
  }
  if (kind === 'state') {
    const row = object(input, [
      ...baseKeys,
      'character_id',
      'event_id',
      'category',
      'key',
      'value',
      'audience',
      'truth',
      'until_event_id',
    ])
    return {
      kind,
      ...baseRecord(row),
      character_id: identifier(row.character_id),
      event_id: identifier(row.event_id),
      category: choice(row.category, stateCategories),
      key: text(row.key, 100, 1),
      value: text(row.value, 4000, 1),
      audience: choice(row.audience, audiences),
      truth: choice(defaulted(row.truth, 'actual'), truths),
      until_event_id: optionalReference(row.until_event_id),
    }
  }
  if (kind === 'relationship') {
    const row = object(input, [...baseKeys, 'from_id', 'to_id', 'relation', 'event_id', 'truth'])
    return {
      kind,
      ...baseRecord(row),
      from_id: identifier(row.from_id),
      to_id: identifier(row.to_id),
      relation: text(row.relation, 500, 1),
      event_id: optionalReference(row.event_id),
      truth: choice(defaulted(row.truth, 'planned'), truths),
    }
  }
  if (kind === 'promise') {
    const row = object(input, [...baseKeys, 'thread', 'stages'])
    return {
      kind,
      ...baseRecord(row),
      thread: text(defaulted(row.thread, '主线'), 100),
      stages: list(row.stages, 100, 1).map(stageRecord),
    }
  }
  return invalid('故事记录类型无效。')
}

function structuralReferences(record: StoryRecord) {
  switch (record.kind) {
    case 'character':
      return { people: [], events: [] }
    case 'event':
      return { people: record.participants, events: record.after }
    case 'state':
      return {
        people: [record.character_id],
        events: [record.event_id, record.until_event_id].filter(Boolean) as string[],
      }
    case 'relationship':
      return { people: [record.from_id, record.to_id], events: record.event_id ? [record.event_id] : [] }
    case 'promise':
      return { people: [], events: record.stages.flatMap(({ event_id }) => (event_id ? [event_id] : [])) }
  }
}

export function storyReferences(record: StoryRecord) {
  const { people, events } = structuralReferences(record)
  return new Set([...record.depends_on, ...people, ...events])
}

function parseProof(input: unknown): SettingsEvidence {
  const row = object(input, ['chapter_id', 'revision_id', 'start', 'end', 'quote_sha256'])
  const start = integer(row.start, 0)
  const end = integer(row.end, start + 1)
  return {
    chapter_id: identifier(row.chapter_id),
    revision_id: identifier(row.revision_id),
    start,
    end,
    quote_sha256: hashId(row.quote_sha256),
  }
}

function proofGroups<T>(value: unknown, parse: (item: unknown) => T) {
  const row = object(value, ['record', 'stages'])
  const group = (items: unknown) => list(items, 30).map(parse)
  return {
    record: group(defaulted(row.record, [])),
    stages: list(defaulted(row.stages, []), 100).map(group),
  }
}

export function allStoryEvidence(version: StoryVersion): SettingsEvidence[] {
  return [version.evidence.record, ...version.evidence.stages].flat()
}

function parseDraft(row: Record<string, unknown>): StoryVersionDraft {
  const parent_id = row.parent_id === null ? null : hashId(row.parent_id)
  const record_id = identifier(row.record_id)
  if (typeof row.archived !== 'boolean') return invalid('故事记录归档状态无效。')
  const record = parseStoryRecord(row.record)
  const evidence = proofGroups(row.evidence, parseProof)
  const dependencies = indexValues(row.dependencies, 200, hashId)
  const refs = storyReferences(record)
  if (
    refs.has(record_id) ||
    Object.keys(dependencies).length !== refs.size ||
    [...refs].some((id) => !Object.hasOwn(dependencies, id))
  )
    invalid('故事记录的精确依赖不匹配。')
  const stages = record.kind === 'promise' ? record.stages : []
  if (stages.length !== evidence.stages.length) invalid('伏笔各阶段须有对应的原文选区列表。')
  const actual =
    (record.kind === 'event' && record.status === 'actual') ||
    ((record.kind === 'state' || record.kind === 'relationship') && record.truth === 'actual')
  if (
    (actual && !evidence.record.length) ||
    stages.some(
      ({ status }, index) =>
        ['planted', 'reinforced', 'resolved'].includes(status) && !evidence.stages[index].length,
    )
  )
    invalid('实际状态、埋设、强化或回收缺少原文证据。')
  return { parent_id, record_id, archived: row.archived, record, evidence, dependencies }
}

export function compactJSON(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(compactJSON).join(',')}]`
  if (value !== null && typeof value === 'object') {
    const row = value as Record<string, unknown>
    return `{${Object.keys(row)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${compactJSON(row[key])}`)
      .join(',')}}`
  }
  return JSON.stringify(value)
}

async function digest(value: string) {
  const data = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))
  return Array.from(new Uint8Array(data), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

const draftKeys = ['parent_id', 'record_id', 'archived', 'record', 'evidence', 'dependencies']

export async function buildStoryVersion(branchId: string, input: unknown): Promise<StoryVersion> {
  const draft = parseDraft(object(input, draftKeys))
  const id = await digest(compactJSON([identifier(branchId), draft]))
  return { id, ...draft }
}

export async function validateStoryVersion(branchId: string, input: unknown): Promise<StoryVersion> {
  const source = object(input, ['id', ...draftKeys])
  const id = hashId(source.id)
  const draft = parseDraft(source)
  if (id === draft.parent_id || id !== (await digest(compactJSON([identifier(branchId), draft]))))
    invalid('故事记录版本哈希与内容或工作版本不匹配。')
  return { id, ...draft }
}

export async function validateStorySyncObject(
  branchId: string,
  objectId: string,
  input: unknown,
): Promise<StoryVersionObject | StoryHeadObject> {
  const version = objectId.startsWith('story-version:')
  const row = object(input, [
    'format',
    'kind',
    'branch_id',
    ...(version ? ['version'] : ['record_id', 'revision_id']),
  ])
  identifier(branchId)
  if (row.format !== format || row.branch_id !== branchId) invalid('故事记录与当前工作版本不符。')
  if (version && row.kind === 'story-version') {
    const value = await validateStoryVersion(branchId, row.version)
    if (objectId !== `story-version:${value.id}`) invalid('故事记录来源编号不一致。')
    return { format, kind: 'story-version', branch_id: branchId, version: value }
  }
  if (!version && row.kind === 'story-head') {
    const record_id = identifier(row.record_id)
    if (objectId !== `story:${record_id}`) invalid('故事采用头与记录编号不一致。')
    return {
      format,
      kind: 'story-head',
      branch_id: branchId,
      record_id,
      revision_id: hashId(row.revision_id),
    }
  }
  return invalid('同步故事记录需要正式修订。归档须作为新版本提交。')
}

export function storyObjectSignature(value: StoryVersionObject | StoryHeadObject | null): string {
  return !value
    ? 'null'
    : value.kind === 'story-head'
      ? JSON.stringify([value.kind, value.branch_id, value.record_id, value.revision_id])
      : JSON.stringify([value.kind, value.branch_id, value.version.id])
}

function quoteAt(content: string, start: number, end: number) {
  const quote = content.slice(integer(start, 0), integer(end, start + 1))
  if (end > content.length || !quote || Array.from(quote).length > 4000 || /\p{Surrogate}/u.test(quote))
    invalid('故事记录引用的正文选区无效，不能拆开表情或扩展汉字。')
  return quote
}

export async function captureStoryEvidence(revision: StoryChapterRevision, start: number, end: number) {
  const quote = quoteAt(revision.content, start, end)
  return {
    chapter_id: identifier(revision.chapter_id),
    revision_id: identifier(revision.id),
    start,
    end,
    quote_sha256: await digest(quote),
  } satisfies SettingsEvidence
}

export async function verifyStoryEvidence(
  evidence: SettingsEvidence,
  revision?: StoryChapterRevision | null,
) {
  if (!revision || revision.id !== evidence.revision_id || revision.chapter_id !== evidence.chapter_id)
    return invalid('缺少故事记录引用的原始章稿。')
  const quote = quoteAt(revision.content, evidence.start, evidence.end)
  if ((await digest(quote)) !== evidence.quote_sha256) invalid('故事记录的原文选区散列不匹配。')
  return quote
}

type Awaitable<T> = T | Promise<T>
export type StorySourceLookup = {
  story: (id: string) => Awaitable<StoryVersion | undefined | null>
  revision: (id: string) => Awaitable<StoryChapterRevision | undefined | null>
}

export async function validateStoryVersionSources(version: StoryVersion, lookup: StorySourceLookup) {
  if (version.parent_id) {
    const parent = await lookup.story(version.parent_id)
    if (
      !parent ||
      parent.id !== version.parent_id ||
      parent.record_id !== version.record_id ||
      parent.record.kind !== version.record.kind
    )
      invalid('缺少同一故事记录的上一个版本。')
  }
  const dependencies = new Map<string, StoryVersion>()
  for (const [recordId, versionId] of Object.entries(version.dependencies)) {
    const exact = await lookup.story(versionId)
    if (!exact || exact.id !== versionId || exact.record_id !== recordId)
      return invalid('缺少故事记录引用的准确依赖版本。')
    dependencies.set(recordId, exact)
  }
  const record = version.record
  const { people, events } = structuralReferences(record)
  if (
    people.some((id) => dependencies.get(id)?.record.kind !== 'character') ||
    events.some((id) => dependencies.get(id)?.record.kind !== 'event')
  )
    invalid('故事关系、事件或人物的关联类型不符。')
  if (
    (record.kind === 'state' || record.kind === 'relationship') &&
    record.truth === 'actual' &&
    record.event_id
  ) {
    const event = dependencies.get(record.event_id)?.record
    if (event?.kind !== 'event' || event.status !== 'actual')
      invalid('实际状态或人物关系需要已发生的关联事件。')
  }
  for (const proof of allStoryEvidence(version))
    await verifyStoryEvidence(proof, await lookup.revision(proof.revision_id))
}

function topological<T>(
  rows: ReadonlyMap<string, T>,
  deps: (value: T) => Iterable<string>,
  external: ReadonlySet<string> = new Set(),
) {
  const remaining = new Map<string, number>()
  const children = new Map<string, string[]>()
  const ready: string[] = []
  for (const [id, row] of rows) {
    let pending = 0
    for (const dependency of new Set(deps(row))) {
      if (!rows.has(dependency)) {
        if (external.has(dependency)) continue
        invalid('故事图缺少依赖版本。')
      }
      const next = children.get(dependency) ?? []
      next.push(id)
      children.set(dependency, next)
      pending++
    }
    if (pending) remaining.set(id, pending)
    else ready.push(id)
  }
  const ordered: T[] = []
  for (let index = 0; index < ready.length; index++) {
    const id = ready[index]
    ordered.push(rows.get(id)!)
    for (const child of children.get(id) ?? []) {
      const pending = (remaining.get(child) ?? 0) - 1
      if (!pending) ready.push(child)
      else remaining.set(child, pending)
    }
  }
  if (ordered.length !== rows.size) invalid('故事图的记录关联或历史版本形成循环。')
  return ordered
}

export function orderStoryVersions(
  versions: ReadonlyMap<string, StoryVersion>,
  external?: ReadonlySet<string>,
) {
  return topological(
    versions,
    (version) => [...Object.values(version.dependencies), ...(version.parent_id ? [version.parent_id] : [])],
    external,
  )
}

export function validateStoryEffectiveGraph(heads: ReadonlyMap<string, StoryVersion>) {
  orderStoryHeads(heads)
}

export function orderStoryHeads(heads: ReadonlyMap<string, StoryVersion>) {
  return topological(heads, (version) => Object.keys(version.dependencies))
}

export function validateStoryHeadSelection(
  version: StoryVersion,
  current: ReadonlyMap<string, StoryVersion>,
) {
  const prior = current.get(version.record_id)
  if (prior && prior.record.kind !== version.record.kind) invalid('已有故事记录的类型不能改变。')
  for (const [recordId, frozen] of Object.entries(version.dependencies)) {
    const dependency = current.get(recordId)
    if (dependency?.id !== frozen || dependency?.archived)
      invalid('本机尚未采用故事记录所需的关联版本，或关联已归档。')
  }
  const selected = new Map(current)
  selected.set(version.record_id, version)
  validateStoryEffectiveGraph(selected)
  const record = version.record
  if (record.kind === 'character') {
    const names = [record.title, ...record.aliases]
    if (new Set(names).size !== names.length || names.some((name) => !hasText(name)))
      invalid('人物姓名和别名须非空且不重复。')
    if (
      [...current].some(
        ([id, entry]) =>
          id !== version.record_id &&
          !entry.archived &&
          entry.record.kind === 'character' &&
          [entry.record.title, ...entry.record.aliases].some((name) => names.includes(name)),
      )
    )
      invalid('姓名或别名已用于其他人物，请核对人物身份。')
  } else if (record.kind === 'event') {
    const order = record.story_order
    if (
      order !== null &&
      record.after.some((id) => {
        const event = current.get(id)?.record
        return event?.kind === 'event' && event.story_order !== null && event.story_order >= order
      })
    )
      invalid('先行事件的故事顺序须早于当前事件。')
  } else if (record.kind === 'state' && record.until_event_id) {
    const start = current.get(record.event_id)?.record
    const end = current.get(record.until_event_id)?.record
    if (start?.kind !== 'event' || end?.kind !== 'event') return invalid('状态起止须关联已有事件。')
    if (start.story_order !== null && end.story_order !== null) {
      if (start.story_order >= end.story_order) invalid('状态结束事件须明确晚于开始事件。')
    } else {
      const queue = [...end.after]
      const seen = new Set<string>()
      for (let index = 0; index < queue.length; index++) {
        const id = queue[index]
        if (seen.has(id)) continue
        seen.add(id)
        const ancestor = current.get(id)?.record
        if (ancestor?.kind === 'event') queue.push(...ancestor.after)
      }
      if (!seen.has(record.event_id)) invalid('状态结束事件须有明确的先后依赖。')
    }
  }
}

function nativeRevision(value: unknown): StoryNativeRevision {
  const row = object(value, ['revision', 'version_id', 'created', 'dependencies', 'evidence'])
  if (typeof row.created !== 'number' || !Number.isFinite(row.created) || row.created < 0)
    return invalid('故事本机修订的时间无效。')
  return {
    revision: integer(row.revision, 0),
    version_id: hashId(row.version_id),
    created: row.created,
    dependencies: indexValues(row.dependencies, 200, (value) => integer(value, 0)),
    evidence: proofGroups(row.evidence, identifier),
  }
}

export async function validateStoryNativeBindings(
  version: StoryVersion,
  row: StoryNativeRevision,
  lookup: {
    original: (id: string) => Awaitable<StoryChapterRevision | undefined | null>
    native: (id: string) => Awaitable<StoryChapterRevision | undefined | null>
  },
) {
  const proof = [version.evidence.record, ...version.evidence.stages]
  const bindings = [row.evidence.record, ...row.evidence.stages]
  if (
    proof.length !== bindings.length ||
    proof.some((group, index) => group.length !== bindings[index].length)
  )
    invalid('故事本机原文引用与正式修订的选区数不一致。')
  const aliases: { sourceId: string; nativeId: string }[] = []
  for (const [groupIndex, group] of proof.entries()) {
    for (const [index, evidence] of group.entries()) {
      const nativeId = bindings[groupIndex][index]
      const [original, native] = await Promise.all([
        lookup.original(evidence.revision_id),
        lookup.native(nativeId),
      ])
      if (
        !original ||
        !native ||
        original.chapter_id !== native.chapter_id ||
        original.sha256 !== native.sha256
      )
        return invalid('故事记录缺少已采用的同一章、同全文的原文修订。')
      await verifyStoryEvidence({ ...evidence, revision_id: nativeId }, native)
      if (nativeId !== evidence.revision_id) aliases.push({ sourceId: evidence.revision_id, nativeId })
    }
  }
  return aliases
}

export async function validateStoryHistory(
  branchId: string,
  value: unknown,
  revisions: {
    original: ReadonlyMap<string, StoryChapterRevision>
    native: ReadonlyMap<string, StoryChapterRevision>
  },
): Promise<StoryHistory> {
  const row = object(value, ['adopted_heads', 'versions', 'source_devices', 'native_history'])
  const adopted_heads = indexValues(row.adopted_heads, maxVersions, hashId)
  const source_devices = indexValues(defaulted(row.source_devices, {}), maxVersions, (value) =>
    text(value, 128, 1),
  )
  const versions: StoryVersion[] = []
  for (const version of list(row.versions, maxVersions, 1))
    versions.push(await validateStoryVersion(branchId, version))
  const byId = new Map(versions.map((version) => [version.id, version]))
  if (byId.size !== versions.length || Object.keys(source_devices).some((id) => !byId.has(id)))
    invalid('故事版本编号须唯一，来源设备须关联已有版本。')
  for (const version of orderStoryVersions(byId)) {
    await validateStoryVersionSources(version, {
      story: (id) => byId.get(id),
      revision: (id) => revisions.original.get(id),
    })
  }
  const native_history = indexValues(row.native_history, maxVersions, (source) =>
    list(source, maxVersions, 1).map(nativeRevision),
  )
  const entries = Object.entries(native_history)
  if (
    entries.length !== Object.keys(adopted_heads).length ||
    entries.some(([id]) => !Object.hasOwn(adopted_heads, id)) ||
    entries.reduce((sum, [, history]) => sum + history.length, 0) > maxVersions
  )
    invalid('故事采用头与本机历史须逐条对应，且不超过归档上限。')
  const heads = new Map<string, StoryVersion>()
  const nativeIndex = new Map<string, Map<number, StoryNativeRevision>>()
  for (const [recordId, history] of entries) {
    const head = byId.get(adopted_heads[recordId])
    if (
      !head ||
      head.record_id !== recordId ||
      history.at(-1)?.version_id !== head.id ||
      history.some(
        (entry, index) =>
          entry.revision !== history[0].revision + index ||
          byId.get(entry.version_id)?.record_id !== recordId,
      )
    )
      return invalid('故事采用头须与同一记录最后一个本机修订相同，修订号须连续。')
    heads.set(recordId, head)
    nativeIndex.set(recordId, new Map(history.map((revision) => [revision.revision, revision])))
  }
  for (const [, history] of entries) {
    for (const local of history) {
      const version = byId.get(local.version_id)!
      const links = Object.entries(local.dependencies)
      if (
        links.length !== Object.keys(version.dependencies).length ||
        links.some(
          ([id, revision]) =>
            !Object.hasOwn(version.dependencies, id) ||
            nativeIndex.get(id)?.get(revision)?.version_id !== version.dependencies[id],
        )
      )
        invalid('故事本机修订的依赖与冻结版本不一致。')
      await validateStoryNativeBindings(version, local, {
        original: (id) => revisions.original.get(id),
        native: (id) => revisions.native.get(id),
      })
    }
  }
  validateStoryEffectiveGraph(heads)
  return { adopted_heads, versions, source_devices, native_history }
}

export function describeStoryVersion(version: StoryVersion) {
  const names: Record<StoryRecord['kind'], string> = {
    character: '人物',
    event: '事件',
    state: '状态',
    relationship: '关系',
    promise: '伏笔',
  }
  const statusNames: Record<StoryPromiseStage['status'] | Truth, string> = {
    planned: '计划',
    actual: '已发生',
    unknown: '未知',
    conflict: '待核对',
    planted: '已埋设',
    reinforced: '已强化',
    resolved: '已回收',
    retained: '作者保留',
    abandoned: '作者放弃',
  }
  const { record } = version
  const lines = [`${names[record.kind]} ${record.title}`, `来源修订 ${version.id}`]
  if (version.archived) lines.push('已归档')
  if (record.notes) lines.push(record.notes)
  if (record.kind === 'event') lines.push(statusNames[record.status], record.story_time)
  if (record.kind === 'state') lines.push(statusNames[record.truth], `${record.key} · ${record.value}`)
  if (record.kind === 'relationship') lines.push(statusNames[record.truth], record.relation)
  if (record.kind === 'character' && record.profile) lines.push(record.profile)
  if (record.kind === 'promise')
    for (const stage of record.stages) lines.push(`${statusNames[stage.status]} ${stage.note}`.trimEnd())
  for (const proof of allStoryEvidence(version))
    lines.push(`原文依据 ${proof.chapter_id} · 修订 ${proof.revision_id} · 选区 ${proof.start}–${proof.end}`)
  return lines.join('\n')
}
