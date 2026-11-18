import { genres } from '../backend/ainovel/genre_catalog.json'

export const projectGenres = genres.map(({ id, name }) => ({ id, name }))
export const briefFields = [
  ['position', '核心定位', true],
  ['track', '赛道', true],
  ['audience', '读者定位', true],
  ['distinction', '差异化', true],
  ['length', '篇幅目标', true],
  ['cadence', '更新计划', false],
  ['ending', '终局', true],
  ['final_image', '最后一镜', false],
] as const

type BriefField = (typeof briefFields)[number][0]
export type ProjectBrief = Record<BriefField, string> & { genre_ids: string[] }
export type ProjectVersion = { id: string; parent_id: string | null; idea: string; brief: ProjectBrief }
export type ProjectHistory = { adopted_revision_id: string | null; versions: ProjectVersion[] }
export type ProjectVersionObject = {
  format: 'ainovel-manuscript-sync/1'
  kind: 'project-version'
  branch_id: string
  version: ProjectVersion
}
export type ProjectHeadObject = {
  format: 'ainovel-manuscript-sync/1'
  kind: 'project-head'
  branch_id: string
  revision_id: string
}

export type ProjectSource = { device_id: string; change_id: string; sequence: number }
export type ProjectVersionRecord = { version: ProjectVersion; source?: ProjectSource; fromPackage?: true }

export const blankProjectBrief: ProjectBrief = {
  position: '',
  track: '',
  audience: '',
  distinction: '',
  length: '',
  cadence: '',
  ending: '',
  final_image: '',
  genre_ids: [],
}

const substantive = new Set(['待定', '待补充', '未定', '略', 'tbd', '...', '……'])

export function projectId(value: unknown): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(value))
    throw new Error('立项修订编号无效。')
  return value
}

function fields(value: unknown, allowed: ReadonlyArray<string>): Record<string, unknown> {
  if (
    !value ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    Object.keys(value).some((key) => !allowed.includes(key))
  )
    throw new Error('立项格式含无法识别的字段。')
  return value as Record<string, unknown>
}

function text(value: unknown, maximum: number, title: string, required: boolean) {
  if (typeof value !== 'string' || value.length > maximum * 2 || /\p{Surrogate}/u.test(value))
    throw new Error(`${title}的内容无效。`)
  const normalized = required ? value.trim() : value
  if (Array.from(normalized).length > maximum || (required && !normalized))
    throw new Error(`${title}缺失或超出 ${maximum} 字。`)
  return normalized
}

export function validateProjectDraft(idea: unknown, value: unknown): Pick<ProjectVersion, 'idea' | 'brief'> {
  const brief = fields(value, ['genre_ids', ...briefFields.map(([key]) => key)])
  const genre_ids = brief.genre_ids
  if (
    !Array.isArray(genre_ids) ||
    genre_ids.length < 1 ||
    genre_ids.length > 3 ||
    new Set(genre_ids).size !== genre_ids.length ||
    genre_ids.some((id) => !projectGenres.some((genre) => genre.id === id))
  )
    throw new Error('请选择互不重复的现有题材，主类型放在第一位。')
  const result: ProjectBrief = { ...blankProjectBrief, genre_ids }
  for (const [key, title, required] of briefFields) {
    const content = text(brief[key] ?? (required ? undefined : ''), 700, title, required)
    if (required && substantive.has(content.toLowerCase())) throw new Error(`${title}需要具体内容。`)
    result[key] = content
  }
  return { idea: text(idea, 6000, '创作思路', true), brief: result }
}

function validateProjectVersion(source: unknown): ProjectVersion {
  const value = fields(source, ['id', 'parent_id', 'idea', 'brief'])
  const id = projectId(value.id)
  const parent_id = value.parent_id === null ? null : projectId(value.parent_id)
  if (id === parent_id) throw new Error('立项父修订不能引用自身。')
  return { id, parent_id, ...validateProjectDraft(value.idea, value.brief) }
}

export function validateProjectHistory(source: unknown): ProjectHistory {
  const history = fields(source, ['adopted_revision_id', 'versions'])
  if (!Array.isArray(history.versions) || !history.versions.length || history.versions.length > 5000)
    throw new Error('立项历史缺失或版本数量超出限制。')
  const versions = history.versions.map(validateProjectVersion)
  const byId = new Map(versions.map((version) => [version.id, version]))
  if (byId.size !== versions.length) throw new Error('立项历史的修订编号重复。')
  const adopted_revision_id =
    history.adopted_revision_id === null ? null : projectId(history.adopted_revision_id)
  if (adopted_revision_id && !byId.has(adopted_revision_id)) throw new Error('稿件包缺少采用的立项修订。')
  const proven = new Set<string>()
  for (const version of versions) {
    const path = new Set<string>()
    let id: string | null = version.id
    while (id && !proven.has(id)) {
      const row = byId.get(id)
      if (!row || path.has(id)) throw new Error('立项父修订缺失或形成循环。')
      path.add(id)
      id = row.parent_id
    }
    for (const id of path) proven.add(id)
  }
  return { adopted_revision_id, versions }
}

export function validateProjectSyncObject(branchId: string, objectId: string, payload: unknown) {
  const version = objectId.startsWith('project-version:')
  const object = fields(payload, ['format', 'kind', 'branch_id', version ? 'version' : 'revision_id'])
  const format = 'ainovel-manuscript-sync/1' as const
  if (object.format !== format || object.branch_id !== branchId) throw new Error('立项与当前工作版本不符。')
  if (!version && object.kind === 'project-head' && objectId === `project:${branchId}`)
    return {
      format,
      kind: 'project-head' as const,
      branch_id: branchId,
      revision_id: projectId(object.revision_id),
    }
  if (version && object.kind === 'project-version') {
    const entry = validateProjectVersion(object.version)
    if (objectId !== `project-version:${entry.id}`) throw new Error('立项来源修订不一致。')
    return {
      format,
      kind: 'project-version' as const,
      branch_id: branchId,
      version: entry,
    }
  }
  throw new Error('立项同步对象格式无效。')
}

export function describeProjectVersion(version: ProjectVersion) {
  const names = new Map(projectGenres.map(({ id, name }) => [id, name]))
  return [
    `创作思路　${version.idea}`,
    `题材　${version.brief.genre_ids.map((id) => names.get(id) ?? id).join('、')}`,
    ...briefFields.map(([key, name]) => `${name}　${version.brief[key] || '未填写'}`),
  ].join('\n')
}
