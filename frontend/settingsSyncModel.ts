import { validateVersionGraph, type ProjectSource } from './projectSyncModel'

const format = 'ainovel-manuscript-sync/1' as const
export const settingCategories = [
  ['core', '故事核心'],
  ['characters', '人物'],
  ['world', '世界设定'],
  ['relationships', '人物关系'],
  ['events', '事件与多线情节'],
  ['timeline', '时间线'],
  ['foreshadowing', '伏笔与回收'],
  ['outline', '全书与分卷大纲'],
] as const

export type SettingsPolicy = { strength: 'hard' | 'soft'; scope: string; exceptions: string }
export type SettingsItem = {
  id: string
  title: string
  detail: string
  links: string[]
  setting_policy?: SettingsPolicy
}
export type SettingsCategory = { id: string; title: string; items: SettingsItem[]; skipped: boolean }
export type SettingsEvidence = {
  chapter_id: string
  revision_id: string
  start: number
  end: number
  quote_sha256: string
}
export type SettingsOrigin = {
  origin: 'author' | 'ai'
  edited: boolean
  artifact_id: string | null
  device_id: string | null
  project_version_id: string | null
  evidence: SettingsEvidence | null
}
export type SettingsVersion = {
  id: string
  parent_id: string | null
  project_version_id: string | null
  categories: SettingsCategory[]
  origins: Record<string, SettingsOrigin>
  note: string
  reconciled_project_items: string[]
  reviewed_setting_findings: string[]
}
export type SettingsVersionObject = {
  format: typeof format
  kind: 'settings-version'
  branch_id: string
  version: SettingsVersion
}
export type SettingsHeadObject = {
  format: typeof format
  kind: 'settings-head'
  branch_id: string
  revision_id: string
}
export type SettingsVersionRecord = {
  version: SettingsVersion
  source?: ProjectSource
  fromPackage?: true
  recordedDevice?: string
}
export type SettingsConflictMerge = {
  conflictId: string
  localRevisionId: string
  current: SettingsVersionRecord
  incoming: SettingsVersionRecord
}
export type SettingsHistory = {
  adopted_revision_id: string | null
  versions: SettingsVersion[]
  source_devices?: Record<string, string>
}

function copyOrigin(origin: SettingsOrigin, source?: SettingsVersionRecord): SettingsOrigin {
  return {
    ...origin,
    evidence: origin.evidence && { ...origin.evidence },
    device_id: origin.device_id ?? source?.source?.device_id ?? source?.recordedDevice ?? null,
  }
}

export function createSettingsDraft(
  id: string,
  parentId: string | null,
  projectVersionId: string | null,
  source?: SettingsVersionRecord,
): SettingsVersion {
  const historical = source?.version
  const origins = Object.fromEntries(
    Object.entries(historical?.origins ?? {}).map(([itemId, origin]) => [itemId, copyOrigin(origin, source)]),
  )
  return {
    id,
    parent_id: parentId,
    project_version_id: projectVersionId,
    categories: historical
      ? structuredClone(historical.categories)
      : settingCategories.map(([categoryId, title]) => ({
          id: categoryId,
          title,
          items: [],
          skipped: false,
        })),
    origins,
    note: '',
    reviewed_setting_findings: [],
    reconciled_project_items: [],
  }
}

export function copyConflictSetting(draft: SettingsVersion, source: SettingsVersionRecord, itemId: string) {
  const sourceCategory = source.version.categories.find(({ items }) => items.some(({ id }) => id === itemId))
  const item = sourceCategory?.items.find(({ id }) => id === itemId)
  const origin = source.version.origins[itemId]
  if (!sourceCategory || !item || !origin || source.version.project_version_id !== draft.project_version_id)
    throw new Error('该条目的确切设定来源或一致的立项不可用。')
  const categories = draft.categories.some(({ id }) => id === sourceCategory.id)
    ? draft.categories
    : [...draft.categories, { ...sourceCategory, items: [] }]
  const result = categories.map((category) => {
    if (category.id !== sourceCategory.id)
      return { ...category, items: category.items.filter(({ id }) => id !== itemId) }
    const copy = structuredClone(item)
    const items = category.items.some(({ id }) => id === itemId)
      ? category.items.map((entry) => (entry.id === itemId ? copy : entry))
      : [...category.items, copy]
    if (items.length > 40) throw new Error('该类别已达 40 条，请先调整草稿条目。')
    return { ...category, items }
  })
  if (result.length > 24 || result.reduce((total, category) => total + category.items.length, 0) > 240)
    throw new Error('草稿已达类别或设定条目上限，请先调整后再取用。')
  return { ...draft, categories: result, origins: { ...draft.origins, [itemId]: copyOrigin(origin, source) } }
}

type LinkFinding = 'missing_link' | 'self_link' | 'duplicate_link'
type FindingKind = LinkFinding | 'same_name' | 'skipped_link' | 'empty_link' | 'policy_link'
type Location = {
  item_id: string
  category_id: string | null
  category_title: string | null
  item_number: number | null
  link_number: number | null
}
export type SettingsFinding = { id: string; kind: FindingKind; locations: Location[] }

export const blockingSettingsFindings: ReadonlySet<FindingKind> = new Set([
  'missing_link',
  'self_link',
  'duplicate_link',
])

const invalid = (message: string): never => {
  throw new Error(message)
}

function record(value: unknown, allowed?: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) invalid('设定应保存为结构化对象。')
  const data = value as Record<string, unknown>
  if (allowed && Object.keys(data).some((entry) => !allowed.includes(entry)))
    invalid('设定格式含无法识别的字段。')
  return data
}

function list(value: unknown, max: number, min = 0): unknown[] {
  if (!Array.isArray(value) || value.length > max || value.length < min)
    return invalid('设定列表的项数超出允许范围。')
  return value
}

export function settingsId(value: unknown): string {
  if (typeof value !== 'string' || !value.length || value.length > 128 || /[^a-zA-Z0-9_-]/u.test(value))
    return invalid('设定中有无效的条目或修订编号。')
  return value
}

function nullableId(value: unknown) {
  return value === null ? null : settingsId(value)
}

function text(value: unknown, max: number, min = 0) {
  if (typeof value !== 'string' || value.length > max * 2 || /\p{Surrogate}/u.test(value))
    return invalid('设定文本缺失、过长或含无效字符。')
  const size = Array.from(value).length
  if (size > max || size < min) invalid('设定文本长度超出允许范围。')
  return value
}

const stripWhitespace = (value: string) => value.replace(/^\p{White_Space}+|\p{White_Space}+$/gu, '')
const pythonWhitespace = /[\p{White_Space}\x1c-\x1f]+/gu
const compareText = (value: string) =>
  value.normalize('NFC').split(pythonWhitespace).filter(Boolean).join(' ')

function casefold(value: string) {
  // Python casefold uses context-independent mappings, including Cherokee and sharp S.
  return Array.from(value, (char) => {
    if (char === 'ı') return char
    if (char === 'ẞ') return 'ss'
    if (/\p{Script=Cherokee}/u.test(char)) return char.toUpperCase()
    return char.toUpperCase().toLowerCase()
  }).join('')
}

function title(value: unknown) {
  if (typeof value !== 'string') return invalid('请填写每个类别和条目的名称。')
  return text(stripWhitespace(value), 200, 1)
}

function boolean(value: unknown) {
  if (typeof value !== 'boolean') return invalid('设定状态必须是明确的是或否。')
  return value
}

function integer(value: unknown, min: number) {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < min)
    return invalid('正文选区位置无效。')
  return value
}

function parseItem(input: unknown): SettingsItem {
  const row = record(input, ['id', 'title', 'detail', 'links', 'setting_policy'])
  const item: SettingsItem = {
    id: settingsId(row.id),
    title: title(row.title),
    detail: text(row.detail === undefined ? '' : row.detail, 4_000),
    links: list(row.links === undefined ? [] : row.links, 30).map(settingsId),
  }
  if (row.setting_policy !== null && row.setting_policy !== undefined) {
    const policy = record(row.setting_policy, ['strength', 'scope', 'exceptions'])
    if (policy.strength !== 'hard' && policy.strength !== 'soft') return invalid('请选择约束强度。')
    item.setting_policy = {
      strength: policy.strength,
      scope: text(policy.scope === undefined ? '' : policy.scope, 800),
      exceptions: text(policy.exceptions === undefined ? '' : policy.exceptions, 800),
    }
  }
  return item
}

function parseCategories(value: unknown): SettingsCategory[] {
  const rows = list(value, 24, settingCategories.length).map((input) => {
    const row = record(input, ['id', 'title', 'items', 'skipped'])
    return {
      id: settingsId(row.id),
      title: title(row.title),
      items: list(row.items === undefined ? [] : row.items, 40).map(parseItem),
      skipped: boolean(row.skipped === undefined ? false : row.skipped),
    }
  })
  const ids = new Map(rows.map((row) => [row.id, row]))
  if (ids.size !== rows.length || new Set(rows.map((row) => casefold(row.title))).size !== rows.length)
    invalid('类别的名称和编号必须唯一。')
  if (settingCategories.some(([id, name]) => ids.get(id)?.title !== name))
    invalid('需要保留内置的设定类别及名称。')
  const items = rows.flatMap((row) => row.items)
  if (items.length > 240 || new Set(items.map((item) => item.id)).size !== items.length)
    invalid('条目编号必须唯一，每个工作版本最多保存 240 项。')
  if (new TextEncoder().encode(JSON.stringify(rows)).byteLength > 100_000)
    invalid('设定内容超过 100 KB，请缩减后再确认。')
  return rows
}

function parseEvidence(value: unknown): SettingsEvidence {
  const row = record(value, ['chapter_id', 'revision_id', 'start', 'end', 'quote_sha256'])
  const start = integer(row.start, 0)
  const end = integer(row.end, 1)
  const hash = row.quote_sha256
  if (end <= start || typeof hash !== 'string' || hash.length !== 64 || /[^a-f0-9]/u.test(hash))
    return invalid('设定原文依据的选区或摘要无效。')
  return {
    chapter_id: settingsId(row.chapter_id),
    revision_id: settingsId(row.revision_id),
    start,
    end,
    quote_sha256: hash,
  }
}

function parseOrigin(value: unknown): SettingsOrigin {
  const row = record(value, [
    'origin',
    'edited',
    'artifact_id',
    'device_id',
    'project_version_id',
    'evidence',
  ])
  if (row.origin !== 'author' && row.origin !== 'ai') return invalid('设定缺少有效的作者或 AI 来源。')
  return {
    origin: row.origin,
    edited: boolean(row.edited === undefined ? false : row.edited),
    artifact_id: nullableId(row.artifact_id ?? null),
    device_id: row.device_id === undefined || row.device_id === null ? null : text(row.device_id, 128, 1),
    project_version_id: nullableId(row.project_version_id ?? null),
    evidence: row.evidence === undefined || row.evidence === null ? null : parseEvidence(row.evidence),
  }
}

async function sha256(value: string) {
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))
  return Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

function sortedEvidenceJSON(value: unknown): string {
  // Match Python json.dumps(..., ensure_ascii=False, sort_keys=True).
  if (Array.isArray(value)) return `[${value.map(sortedEvidenceJSON).join(', ')}]`
  if (value !== null && typeof value === 'object') {
    const obj = value as Record<string, unknown>
    const pairs = Object.keys(obj)
      .sort()
      .map((key) => `${JSON.stringify(key)}: ${sortedEvidenceJSON(obj[key])}`)
    return `{${pairs.join(', ')}}`
  }
  return JSON.stringify(value)
}

const terms = (item: SettingsItem) =>
  [item.setting_policy?.scope ?? '', item.setting_policy?.exceptions ?? ''].map(compareText)

async function inspectCategories(categories: SettingsCategory[]) {
  const entries = new Map(
    categories.flatMap((category) =>
      category.items.map(
        (item, index) =>
          [
            item.id,
            {
              item,
              skipped: category.skipped,
              location: {
                item_id: item.id,
                category_id: category.id,
                category_title: category.title,
                item_number: index + 1,
                link_number: null,
              } satisfies Location,
            },
          ] as const,
      ),
    ),
  )
  const findings: Promise<SettingsFinding>[] = []
  function report(kind: FindingKind, ...locations: Location[]) {
    const evidence = locations.map((location) => [location, entries.get(location.item_id)?.item ?? null])
    findings.push(sha256(sortedEvidenceJSON([kind, evidence])).then((id) => ({ id, kind, locations })))
  }

  const names = new Map<string, { meaning: string; location: Location }[]>()
  for (const { item, skipped, location } of entries.values()) {
    if (skipped || !compareText(item.detail) || (location.category_id !== 'world' && !item.setting_policy))
      continue
    const meaning = JSON.stringify([
      compareText(item.detail),
      item.setting_policy?.strength ?? null,
      terms(item),
    ])
    const name = casefold(compareText(item.title).normalize('NFKC'))
    const group = names.get(name) ?? []
    group.push({ meaning, location })
    names.set(name, group)
  }
  for (const group of names.values()) {
    if (new Set(group.map(({ meaning }) => meaning)).size > 1)
      report('same_name', ...group.map(({ location }) => location))
  }

  for (const { item, skipped, location } of entries.values()) {
    const seen = new Set<string>()
    const sourceTerms = terms(item)
    for (const [index, reference] of item.links.entries()) {
      const target = entries.get(reference)
      const source = { ...location, link_number: index + 1 }
      const reason =
        reference === item.id
          ? 'self_link'
          : !target
            ? 'missing_link'
            : seen.has(reference)
              ? 'duplicate_link'
              : null
      seen.add(reference)
      if (reason) {
        report(
          reason,
          source,
          target?.location ?? {
            item_id: reference,
            category_id: null,
            category_title: null,
            item_number: null,
            link_number: null,
          },
        )
      } else if (!skipped && target) {
        if (target.skipped) report('skipped_link', source, target.location)
        else if (!compareText(target.item.detail)) report('empty_link', source, target.location)
        else if (
          terms(target.item).some((term, index) => term && sourceTerms[index] && term !== sourceTerms[index])
        )
          report('policy_link', source, target.location)
      }
    }
  }
  return Promise.all(findings)
}

function exactReview(proposed: string[], required: string[]) {
  const checked = new Set(proposed)
  return (
    proposed.length === checked.size &&
    checked.size === required.length &&
    required.every((id) => checked.has(id))
  )
}

export async function inspectSettingsDraft(
  categoriesInput: unknown,
  originsInput: unknown,
  projectInput: unknown,
) {
  const categories = parseCategories(categoriesInput)
  const origins = Object.fromEntries(
    Object.entries(record(originsInput)).map(([id, origin]) => [settingsId(id), parseOrigin(origin)]),
  )
  const items = categories.flatMap((category) => category.items)
  if (Object.keys(origins).length !== items.length || items.some((item) => !Object.hasOwn(origins, item.id)))
    invalid('每条设定需要原始来源，不能挂载其他条目的来源。')
  const project_version_id = nullableId(projectInput)
  const projectImpacts = categories
    .filter((category) => !category.skipped && project_version_id)
    .flatMap((category) => category.items)
    .filter(
      (item) =>
        origins[item.id].project_version_id && origins[item.id].project_version_id !== project_version_id,
    )
    .map((item) => item.id)
  return {
    categories,
    origins,
    project_version_id,
    projectImpacts,
    findings: await inspectCategories(categories),
  }
}

export async function prepareSettingsVersion(input: unknown): Promise<SettingsVersion> {
  const row = record(input, [
    'id',
    'parent_id',
    'project_version_id',
    'categories',
    'origins',
    'note',
    'reconciled_project_items',
    'reviewed_setting_findings',
  ])
  const id = settingsId(row.id)
  const parent_id = nullableId(row.parent_id)
  if (parent_id === id) invalid('设定不能以自己为父版本。')
  const { categories, origins, project_version_id, projectImpacts, findings } = await inspectSettingsDraft(
    row.categories,
    row.origins,
    row.project_version_id,
  )
  const reconciled_project_items = list(
    row.reconciled_project_items === undefined ? [] : row.reconciled_project_items,
    240,
  ).map(settingsId)
  const reviewed_setting_findings = list(
    row.reviewed_setting_findings === undefined ? [] : row.reviewed_setting_findings,
    7440,
  ).map(settingsId)
  if (findings.some((item) => blockingSettingsFindings.has(item.kind))) invalid('请先修复无效的条目关联。')
  if (!exactReview(reconciled_project_items, projectImpacts)) invalid('请逐项核对受立项变更影响的设定。')
  if (
    !exactReview(
      reviewed_setting_findings,
      findings.map((item) => item.id),
    )
  )
    invalid('请逐项核对设定冲突后再确认。')
  return {
    id,
    parent_id,
    project_version_id,
    categories,
    origins,
    note: text(row.note === undefined ? '' : row.note, 4000),
    reconciled_project_items,
    reviewed_setting_findings,
  }
}

export async function validateSettingsSyncObject(
  branchId: string,
  objectId: string,
  payload: unknown,
): Promise<SettingsHeadObject | SettingsVersionObject> {
  const version = objectId.startsWith('settings-version:')
  const object = record(payload, ['format', 'kind', 'branch_id', version ? 'version' : 'revision_id'])
  settingsId(branchId)
  if (object.format !== format || object.branch_id !== branchId)
    return invalid('设定同步对象与当前工作版本不符。')
  if (!version && object.kind === 'settings-head' && objectId === `settings:${branchId}`)
    return { format, kind: 'settings-head', branch_id: branchId, revision_id: settingsId(object.revision_id) }
  if (version && object.kind === 'settings-version') {
    const value = await prepareSettingsVersion(object.version)
    if (objectId !== `settings-version:${value.id}`) invalid('设定来源修订编号不一致。')
    return { format, kind: 'settings-version', branch_id: branchId, version: value }
  }
  return invalid('正式设定同步对象格式无效。')
}

type Awaitable<Value> = Value | Promise<Value>
type EvidenceRevision = { id: string; chapter_id: string; content: string }
export type SettingsSourceLookup = {
  project: (id: string) => Awaitable<unknown>
  settings: (id: string) => Awaitable<unknown>
  revision: (id: string) => Awaitable<EvidenceRevision | undefined | null>
}

export async function validateSettingsVersionSources(version: SettingsVersion, lookup: SettingsSourceLookup) {
  const origins = Object.values(version.origins)
  const projects = new Set([
    version.project_version_id,
    ...origins.map((origin) => origin.project_version_id),
  ])
  if (version.parent_id && !(await lookup.settings(version.parent_id)))
    invalid('缺少设定的父版本，待历史到齐。')
  for (const id of projects) {
    if (id && !(await lookup.project(id))) invalid('缺少设定引用的立项版本，待立项历史到齐。')
  }
  for (const { evidence } of origins) {
    if (!evidence) continue
    const data = await lookup.revision(evidence.revision_id)
    if (!data || data.id !== evidence.revision_id || data.chapter_id !== evidence.chapter_id)
      return invalid('缺少设定引用的准确正文修订，待正文历史到齐。')
    const quote = data.content.slice(evidence.start, evidence.end)
    if (
      evidence.end > data.content.length ||
      !quote ||
      Array.from(quote).length > 4000 ||
      /\p{Surrogate}/u.test(quote) ||
      (await sha256(quote)) !== evidence.quote_sha256
    )
      invalid('设定引用与源正文不符，请核对正文选区。')
  }
}

export async function validateSettingsHistory(
  input: unknown,
  projects: ReadonlySet<string>,
  revisions: ReadonlyMap<string, EvidenceRevision>,
): Promise<SettingsHistory> {
  const archive = record(input, ['adopted_revision_id', 'versions', 'source_devices'])
  const adopted_revision_id = nullableId(archive.adopted_revision_id)
  const versions = []
  for (const value of list(archive.versions, 5000, 1)) versions.push(await prepareSettingsVersion(value))
  const byId = validateVersionGraph(versions, adopted_revision_id, '设定')
  const devices = archive.source_devices === undefined ? [] : Object.entries(record(archive.source_devices))
  if (devices.length > byId.size || devices.some(([id]) => !byId.has(id)))
    invalid('设定来源设备关联了包中不存在的修订。')
  for (const version of versions) {
    await validateSettingsVersionSources(version, {
      project: (id) => projects.has(id),
      settings: (id) => byId.has(id),
      revision: (id) => revisions.get(id),
    })
  }
  return {
    adopted_revision_id,
    versions,
    ...(devices.length
      ? { source_devices: Object.fromEntries(devices.map(([id, device]) => [id, text(device, 128, 1)])) }
      : {}),
  }
}

export function describeSettingsVersion(version: SettingsVersion) {
  const lines = [`设定修订 ${version.id}`, `立项依据 ${version.project_version_id ?? '未采用立项'}`]
  if (version.parent_id) lines.push(`父版本 ${version.parent_id}`)
  for (const category of version.categories) {
    if (!category.items.length) continue
    lines.push(`${category.title}${category.skipped ? ' · 暂缓' : ''}`)
    for (const item of category.items) {
      lines.push(`${item.title} · ${item.id}`, item.detail)
      const rule = item.setting_policy
      if (rule)
        lines.push(
          `${rule.strength === 'hard' ? '硬规定' : '可调整设定'} · 适用范围 ${rule.scope || '未填写'} · 例外 ${rule.exceptions || '未填写'}`,
        )
      if (item.links.length) lines.push(`关联 ${item.links.join('、')}`)
    }
  }
  return lines.join('\n')
}
