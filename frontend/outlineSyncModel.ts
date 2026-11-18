import { validateVersionGraph, type ProjectSource } from './projectSyncModel'
import { settingIsAdopted } from './conception'
import type { ArchivedReferenceCard } from './referenceArchive'
import type { SettingsItem, SettingsVersion } from './settingsSyncModel'

const format = 'ainovel-manuscript-sync/1' as const
export const outlineChapterFields = [
  ['goal', '目标'],
  ['obstacle', '阻碍'],
  ['turn', '转折'],
  ['result', '结果'],
] as const
export const outlineWindowFields = [
  ['plant_chapter_id', '埋设章'],
  ['opens_chapter_id', '开始回收'],
  ['closes_chapter_id', '最迟回收'],
] as const

type ChapterText = (typeof outlineChapterFields)[number][0]
type WindowPart = (typeof outlineWindowFields)[number][0]
export type OutlineCardReference = {
  session_id: string
  card_id: string
  revision: number
  version_id?: string
}
export type OutlineCard = OutlineCardReference & {
  technique: string
  conditions: string
  exceptions: string
  source_title: string
}
export type OutlineChapterPlan = Record<ChapterText, string> & {
  chapter_id: string
  setting_ids: string[]
  reference_cards?: OutlineCardReference[]
}
export type OutlineUnit = { id: string; title: string; summary: string; chapters: OutlineChapterPlan[] }
export type OutlineVolume = { id: string; title: string; summary: string; units: OutlineUnit[] }
export type OutlineWindow = Record<WindowPart, string> & { promise_id: string }
export type OutlinePlan = { summary: string; volumes: OutlineVolume[]; promises: OutlineWindow[] }
export type OutlinePromiseSeed = {
  kind: 'promise'
  title: string
  notes: string
  sources: []
  depends_on: []
  volume: string
  thread: string
  stages: [{ status: 'planned'; event_id: null | ''; sources: []; note: string }]
}
export type OutlineVersion = {
  id: string
  parent_id: string | null
  project_version_id: string | null
  settings_version_id: string | null
  plan: OutlinePlan
  reference_cards: OutlineCard[]
  promise_sources: Record<string, OutlinePromiseSeed>
}
export type OutlineVersionObject = {
  format: typeof format
  kind: 'outline-version'
  branch_id: string
  version: OutlineVersion
}
export type OutlineHeadObject = {
  format: typeof format
  kind: 'outline-head'
  branch_id: string
  revision_id: string
}
export type OutlineVersionRecord = {
  version: OutlineVersion
  source?: ProjectSource
  recordedDeviceId?: string
}
export type OutlineHistory = {
  adopted_revision_id: string | null
  versions: OutlineVersion[]
  source_devices?: Record<string, string>
  required_story_records?: string[]
}
export type OutlineArchivePending = { revisionId: string; requiredStoryRecords: string[]; reason: string }
export type OutlineDirectoryChapter = {
  id: string
  title: string
  position: number
  archived?: number | boolean
}
export type OutlineExistingPromise = {
  id: string
  title: string
  archived?: number | boolean
  seed?: OutlinePromiseSeed
  advanced?: boolean
}

const invalid = (message: string): never => {
  throw new Error(message)
}

function object(value: unknown, keys?: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid('大纲内容格式无效。')
  if (keys && Object.keys(value).some((key) => !keys.includes(key))) invalid('大纲内容含无法识别的字段。')
  return value as Record<string, unknown>
}

function array(value: unknown, max: number, min = 0): unknown[] {
  if (!Array.isArray(value) || value.length > max || value.length < min)
    return invalid('大纲列表缺项或超出允许数量。')
  return value
}

function text(value: unknown, max = Infinity, min = 0) {
  if (typeof value !== 'string' || /\p{Surrogate}/u.test(value)) return invalid('大纲有无效的文字。')
  if (value.length > max * 2 || Array.from(value).length > max || value.length < min)
    invalid('大纲名称或文字缺项，或超出允许长度。')
  return value
}

const trimmed = (value: unknown, max: number) =>
  text(typeof value === 'string' ? value.replace(/^\p{White_Space}+|\p{White_Space}+$/gu, '') : value, max, 1)

function identifier(value: unknown) {
  if (typeof value !== 'string' || !value.length || value.length > 128 || /[^a-zA-Z0-9_-]/u.test(value))
    return invalid('大纲包含无效的修订或条目编号。')
  return value
}

const nullableId = (value: unknown) => (value === null ? null : identifier(value))
const cardReferenceFields = ['session_id', 'card_id', 'revision', 'version_id']

function cardAddress(row: Record<string, unknown>): OutlineCardReference {
  const revision = row.revision
  if (typeof revision !== 'number' || !Number.isSafeInteger(revision) || revision < 0)
    return invalid('手法卡修订号无效。')
  const version = row.version_id
  if (
    version !== undefined &&
    version !== null &&
    (typeof version !== 'string' || version.length !== 64 || /[^a-f0-9]/u.test(version))
  )
    invalid('手法卡正式版本编号无效。')
  return {
    session_id: identifier(row.session_id),
    card_id: identifier(row.card_id),
    revision,
    ...(typeof version === 'string' ? { version_id: version } : {}),
  }
}

export const outlineCardKey = (ref: OutlineCardReference) =>
  `${ref.session_id}/${ref.card_id}/${ref.revision}/${ref.version_id ?? ''}`

export function outlineCardFromReference(version?: ArchivedReferenceCard | null): OutlineCard | undefined {
  if (version?.assessment !== 'ready' || !version.card || !version.citation) return
  const { id, session_id, card_id, revision, card, citation } = version
  return {
    session_id,
    card_id,
    revision,
    version_id: id,
    technique: card.technique,
    conditions: card.conditions,
    exceptions: card.exceptions,
    source_title: citation.title,
  }
}

function parseCard(value: unknown): OutlineCard {
  const row = object(value, [...cardReferenceFields, 'technique', 'conditions', 'exceptions', 'source_title'])
  const technique = text(row.technique)
  const conditions = text(row.conditions)
  const exceptions = text(row.exceptions)
  if ([technique, conditions, exceptions].some((value) => !value.replace(/[\p{White_Space}\x1c-\x1f]/gu, '')))
    invalid('手法卡需要冻结手法、适用条件及例外。')
  return { ...cardAddress(row), technique, conditions, exceptions, source_title: text(row.source_title) }
}

function parseChapter(value: unknown): OutlineChapterPlan {
  const row = object(value, [
    'chapter_id',
    'goal',
    'obstacle',
    'turn',
    'result',
    'setting_ids',
    'reference_cards',
  ])
  const setting_ids = array(row.setting_ids === undefined ? [] : row.setting_ids, 40).map(identifier)
  const cards = array(row.reference_cards === undefined ? [] : row.reference_cards, 8).map((card) =>
    cardAddress(object(card, cardReferenceFields)),
  )
  if (
    new Set(setting_ids).size !== setting_ids.length ||
    new Set(cards.map(({ session_id, card_id }) => `${session_id}/${card_id}`)).size !== cards.length
  )
    invalid('每章仅能引用一次设定，且同一手法卡只能选择一个修订。')
  return {
    chapter_id: identifier(row.chapter_id),
    goal: trimmed(row.goal, 4000),
    obstacle: trimmed(row.obstacle, 4000),
    turn: trimmed(row.turn, 4000),
    result: trimmed(row.result, 4000),
    setting_ids,
    ...(cards.length ? { reference_cards: cards } : {}),
  }
}

function parseUnit(value: unknown): OutlineUnit {
  const row = object(value, ['id', 'title', 'summary', 'chapters'])
  return {
    id: identifier(row.id),
    title: trimmed(row.title, 200),
    summary: trimmed(row.summary, 4000),
    chapters: array(row.chapters, 100, 1).map(parseChapter),
  }
}

function parseVolume(value: unknown): OutlineVolume {
  const row = object(value, ['id', 'title', 'summary', 'units'])
  return {
    id: identifier(row.id),
    title: trimmed(row.title, 200),
    summary: trimmed(row.summary, 4000),
    units: array(row.units, 100, 1).map(parseUnit),
  }
}

function parseWindow(value: unknown): OutlineWindow {
  const row = object(value, [...outlineWindowFields.map(([key]) => key), 'promise_id'])
  return {
    promise_id: identifier(row.promise_id),
    plant_chapter_id: identifier(row.plant_chapter_id),
    opens_chapter_id: identifier(row.opens_chapter_id),
    closes_chapter_id: identifier(row.closes_chapter_id),
  }
}

export const outlineChapterPlans = (plan: OutlinePlan) =>
  plan.volumes.flatMap(({ units }) => units.flatMap(({ chapters }) => chapters))

export function requiredOutlineStoryRecord(
  version: OutlineVersion,
  pending?: OutlineArchivePending,
  available: ReadonlySet<string> = new Set(),
) {
  return version.plan.promises.find(
    ({ promise_id }) => pending?.requiredStoryRecords.includes(promise_id) && !available.has(promise_id),
  )?.promise_id
}

function parsePlan(value: unknown): OutlinePlan {
  const row = object(value, ['summary', 'volumes', 'promises', 'new_promises'])
  if (row.new_promises !== undefined) array(row.new_promises, 0)
  const plan = {
    summary: trimmed(row.summary, 4000),
    volumes: array(row.volumes, 100, 1).map(parseVolume),
    promises: array(row.promises === undefined ? [] : row.promises, 250).map(parseWindow),
  }
  const chapterIds = outlineChapterPlans(plan).map(({ chapter_id }) => chapter_id)
  const nodes = plan.volumes.flatMap(({ id, units }) => [id, ...units.map((unit) => unit.id)])
  if (new Set(nodes).size !== nodes.length || new Set(chapterIds).size !== chapterIds.length)
    invalid('卷、单元或章节编号重复。')
  const position = new Map(chapterIds.map((id, index) => [id, index]))
  const used = new Set<string>()
  for (const window of plan.promises) {
    if (used.has(window.promise_id)) invalid('每条伏笔只能安排一个窗口。')
    used.add(window.promise_id)
    const plant = position.get(window.plant_chapter_id)
    const opens = position.get(window.opens_chapter_id)
    const closes = position.get(window.closes_chapter_id)
    if (plant === undefined || opens === undefined || closes === undefined || plant > opens || opens > closes)
      invalid('伏笔埋设、开始回收和最迟回收须在当前大纲中按章序排列。')
  }
  return plan
}

export function outlinePromiseSeed(title: string, notes: string): OutlinePromiseSeed {
  return {
    kind: 'promise',
    title,
    notes,
    sources: [],
    depends_on: [],
    volume: '',
    thread: '主线',
    stages: [{ status: 'planned', event_id: null, sources: [], note: '' }],
  }
}

function parseSeed(value: unknown): OutlinePromiseSeed {
  const row = object(value, ['kind', 'title', 'notes', 'sources', 'depends_on', 'volume', 'thread', 'stages'])
  if (row.kind !== 'promise') invalid('大纲只能携带伏笔计划种子。')
  array(row.sources === undefined ? [] : row.sources, 0)
  array(row.depends_on === undefined ? [] : row.depends_on, 0)
  const [initial] = array(row.stages, 1, 1)
  const stage = object(initial, ['status', 'event_id', 'sources', 'note'])
  const event_id = stage.event_id ?? null
  if (stage.status !== 'planned' || (event_id !== null && event_id !== ''))
    return invalid('伏笔种子只含初始计划阶段。')
  array(stage.sources === undefined ? [] : stage.sources, 0)
  const title = text(row.title, 200, 1)
  if (!title.replace(/[\p{White_Space}\x1c-\x1f]/gu, '')) invalid('请填写伏笔名称。')
  return {
    ...outlinePromiseSeed(title, text(row.notes === undefined ? '' : row.notes, 4000)),
    volume: text(row.volume === undefined ? '' : row.volume, 100),
    thread: text(row.thread === undefined ? '主线' : row.thread, 100),
    stages: [
      {
        status: 'planned',
        event_id,
        sources: [],
        note: text(stage.note === undefined ? '' : stage.note, 2000),
      },
    ],
  }
}

export function prepareOutlineVersion(value: unknown): OutlineVersion {
  const row = object(value, [
    'id',
    'parent_id',
    'project_version_id',
    'settings_version_id',
    'plan',
    'reference_cards',
    'promise_sources',
  ])
  const id = identifier(row.id)
  const parent_id = nullableId(row.parent_id)
  const project_version_id = nullableId(row.project_version_id)
  const settings_version_id = nullableId(row.settings_version_id)
  if (id === parent_id) invalid('大纲不能以自己为父版本。')
  const plan = parsePlan(row.plan)
  const reference_cards = array(row.reference_cards === undefined ? [] : row.reference_cards, 1000).map(
    parseCard,
  )
  const proposed = Object.entries(object(row.promise_sources === undefined ? {} : row.promise_sources))
  const windows = new Set(plan.promises.map((window) => window.promise_id))
  if (proposed.length > 250 || proposed.some(([id]) => !windows.has(id)))
    invalid('只能携带当前窗口已引用的伏笔计划种子。')
  const promise_sources = Object.fromEntries(proposed.map(([id, seed]) => [identifier(id), parseSeed(seed)]))
  const chapters = outlineChapterPlans(plan)
  const selected = new Set(chapters.flatMap((chapter) => (chapter.reference_cards ?? []).map(outlineCardKey)))
  const cards = new Set(reference_cards.map(outlineCardKey))
  if (
    cards.size !== reference_cards.length ||
    cards.size !== selected.size ||
    [...selected].some((id) => !cards.has(id))
  )
    invalid('章纲的每张手法卡须有完整、对应的冻结摘要。')
  if (!settings_version_id && chapters.some(({ setting_ids }) => setting_ids.length))
    invalid('章纲引用的设定须有确认版本。')
  return { id, parent_id, project_version_id, settings_version_id, plan, reference_cards, promise_sources }
}

export function validateOutlineSyncObject(
  branchId: string,
  objectId: string,
  payload: unknown,
): OutlineHeadObject | OutlineVersionObject {
  identifier(branchId)
  const version = objectId.startsWith('outline-version:')
  const value = object(payload, ['format', 'kind', 'branch_id', version ? 'version' : 'revision_id'])
  if (value.format !== format || value.branch_id !== branchId) invalid('大纲与当前工作版本不符。')
  if (!version && value.kind === 'outline-head' && objectId === `outline:${branchId}`)
    return { format, kind: 'outline-head', branch_id: branchId, revision_id: identifier(value.revision_id) }
  if (version && value.kind === 'outline-version') {
    const plan = prepareOutlineVersion(value.version)
    if (objectId !== `outline-version:${plan.id}`) invalid('大纲的源修订编号不一致。')
    return { format, kind: 'outline-version', branch_id: branchId, version: plan }
  }
  return invalid('正式大纲同步对象格式无效。')
}

export function outlineObjectSignature(object: OutlineHeadObject | OutlineVersionObject | null) {
  if (!object) return 'null'
  if (object.kind === 'outline-head')
    return JSON.stringify([object.kind, object.branch_id, object.revision_id])
  const { promise_sources, ...version } = object.version
  const seeds = Object.keys(promise_sources)
    .sort()
    .map((id) => [id, promise_sources[id]])
  return JSON.stringify([object.kind, object.branch_id, version, seeds])
}

export function eligibleOutlineSettings(version: SettingsVersion | undefined | null): SettingsItem[] {
  return (
    version?.categories
      .find(({ id, skipped }) => id === 'world' && !skipped)
      ?.items.filter(
        (item) =>
          settingIsAdopted(item) &&
          item.detail.replace(/[\p{White_Space}\x1c-\x1f]/gu, '') &&
          Object.hasOwn(version.origins, item.id) &&
          !version.origins[item.id].evidence,
      ) ?? []
  )
}

export function validateOutlineActiveBasis(
  version: OutlineVersion,
  basis: { projectVersionId: string | null; settingsVersionId: string | null },
) {
  if (version.project_version_id !== basis.projectVersionId)
    invalid('本机采用的立项与大纲依据不同，待采用对应立项。')
  if (version.settings_version_id && version.settings_version_id !== basis.settingsVersionId)
    invalid('本机采用的设定与大纲依据不同，待采用对应设定。')
}

type Awaitable<T> = T | Promise<T>
export type OutlineSourceLookup = {
  project: (id: string) => Awaitable<unknown>
  outline: (id: string) => Awaitable<unknown>
  settings: (id: string) => Awaitable<SettingsVersion | null | undefined>
  chapter?: (id: string) => Awaitable<OutlineDirectoryChapter | null | undefined>
  promise?: (id: string) => Awaitable<OutlineExistingPromise | null | undefined>
  reference?: (id: string) => Awaitable<ArchivedReferenceCard | null | undefined>
}

export async function validateOutlineVersionSources(version: OutlineVersion, lookup: OutlineSourceLookup) {
  if (version.parent_id && !(await lookup.outline(version.parent_id))) invalid('大纲父版本尚未到齐。')
  if (version.project_version_id && !(await lookup.project(version.project_version_id)))
    invalid('大纲引用的立项版本尚未到齐。')
  const settings = version.settings_version_id ? await lookup.settings(version.settings_version_id) : null
  if (
    version.settings_version_id &&
    (!settings ||
      settings.id !== version.settings_version_id ||
      settings.project_version_id !== version.project_version_id)
  )
    invalid('大纲引用的设定版本或对应立项尚未到齐。')
  const eligible = new Set(eligibleOutlineSettings(settings).map(({ id }) => id))
  if (lookup.reference) {
    for (const card of version.reference_cards) {
      if (!card.version_id) continue
      const source = outlineCardFromReference(await lookup.reference(card.version_id))
      if (!source) return invalid('缺少大纲引用的完整正式手法卡修订。')
      if (
        outlineCardKey(source) !== outlineCardKey(card) ||
        source.technique !== card.technique ||
        source.conditions !== card.conditions ||
        source.exceptions !== card.exceptions ||
        source.source_title !== card.source_title
      )
        invalid('大纲中的手法卡地址或冻结摘要与正式修订不符。')
    }
  }
  let position = 0
  for (const chapter of outlineChapterPlans(version.plan)) {
    if (chapter.setting_ids.some((id) => !eligible.has(id)))
      invalid('章纲引用的设定须为所选版本中的有效世界设定。')
    if (lookup.chapter) {
      const directory = await lookup.chapter(chapter.chapter_id)
      if (
        !directory ||
        directory.id !== chapter.chapter_id ||
        directory.archived ||
        !Number.isSafeInteger(directory.position) ||
        directory.position < 1
      )
        return invalid('大纲引用的有效章节目录尚未到齐。')
      if (directory.position < position) invalid('章纲顺序须与章节目录一致。')
      position = directory.position
    }
  }
  if (lookup.promise) {
    for (const window of version.plan.promises) {
      const promise = await lookup.promise(window.promise_id)
      const seed = Object.hasOwn(version.promise_sources, window.promise_id)
        ? version.promise_sources[window.promise_id]
        : undefined
      if (!promise && !seed) invalid('大纲引用的伏笔记录尚未到齐。')
      if (promise && (promise.id !== window.promise_id || promise.archived))
        invalid('大纲引用的伏笔归属或归档状态不同。')
      if (
        promise &&
        seed &&
        (!promise.seed || JSON.stringify(parseSeed(promise.seed)) !== JSON.stringify(seed))
      )
        invalid('同一伏笔的初始计划不一致。')
    }
  }
}

export async function validateOutlineHistory(
  input: unknown,
  dependencies: {
    projects: ReadonlySet<string>
    settings: ReadonlyMap<string, SettingsVersion>
    chapters: ReadonlySet<string>
    references?: ReadonlyMap<string, ArchivedReferenceCard>
  },
): Promise<OutlineHistory> {
  const history = object(input, [
    'adopted_revision_id',
    'versions',
    'source_devices',
    'required_story_records',
  ])
  const adopted_revision_id = nullableId(history.adopted_revision_id)
  const versions = array(history.versions, 5000, 1).map(prepareOutlineVersion)
  const byId = validateVersionGraph(versions, adopted_revision_id, '大纲')
  const devices = history.source_devices === undefined ? [] : Object.entries(object(history.source_devices))
  if (devices.length > byId.size || devices.some(([id]) => !byId.has(id)))
    invalid('大纲来源设备关联了包中不存在的修订。')
  const required = array(
    history.required_story_records === undefined ? [] : history.required_story_records,
    250,
  ).map(identifier)
  const selected = adopted_revision_id ? byId.get(adopted_revision_id) : undefined
  const windows = new Set(selected?.plan.promises.map(({ promise_id }) => promise_id))
  if (
    new Set(required).size !== required.length ||
    required.some((id) => !windows.has(id)) ||
    [...windows].some(
      (id) => selected && !Object.hasOwn(selected.promise_sources, id) && !required.includes(id),
    )
  )
    invalid('待恢复的故事记录须对应采用版窗口，并包含缺少初始计划的伏笔。')
  for (const version of versions) {
    await validateOutlineVersionSources(version, {
      project: (id) => dependencies.projects.has(id),
      settings: (id) => dependencies.settings.get(id),
      outline: (id) => byId.has(id),
      reference: (id) => dependencies.references?.get(id),
    })
    if (outlineChapterPlans(version.plan).some(({ chapter_id }) => !dependencies.chapters.has(chapter_id)))
      invalid('稿件包缺少大纲引用的原章节目录。')
  }
  return {
    adopted_revision_id,
    versions,
    ...(devices.length
      ? { source_devices: Object.fromEntries(devices.map(([id, value]) => [id, text(value, 128, 1)])) }
      : {}),
    ...(required.length ? { required_story_records: required } : {}),
  }
}

export function describeOutlineVersion(version: OutlineVersion) {
  const lines = [
    `大纲修订 ${version.id}`,
    `立项依据 ${version.project_version_id ?? '未采用'}`,
    `设定依据 ${version.settings_version_id ?? '未采用'}`,
    `总纲 ${version.plan.summary}`,
  ]
  for (const volume of version.plan.volumes) {
    lines.push(`卷 ${volume.title} · ${volume.summary}`)
    for (const unit of volume.units) {
      lines.push(`单元 ${unit.title} · ${unit.summary}`)
      for (const chapter of unit.chapters) {
        lines.push(`章节 ${chapter.chapter_id}`)
        for (const [field, label] of outlineChapterFields) lines.push(`${label} ${chapter[field]}`)
      }
    }
  }
  for (const window of version.plan.promises) {
    const title = version.promise_sources[window.promise_id]?.title ?? window.promise_id
    lines.push(
      `伏笔 ${title} · ${window.plant_chapter_id} → ${window.opens_chapter_id} → ${window.closes_chapter_id}`,
    )
  }
  return lines.join('\n')
}
