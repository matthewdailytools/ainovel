import type { components } from './api-schema'

type Schemas = components['schemas']
export type ConceptionItem = Schemas['ConceptionItem']
export type ConceptionCategory = Omit<Schemas['ConceptionCategory'], 'items'> & {
  items: ConceptionItem[]
}

export type ConceptionConfirmation = Schemas['ConceptionConfirmation']
export type ConceptionIssue = Schemas['ConceptionIssue']
export type ConceptionModelReview = { version: string; issues: ConceptionIssue[] }

export type ConceptionState = Omit<Schemas['ConceptionWorkspace'], 'categories'> & {
  categories: ConceptionCategory[]
}

export type ConceptionSnapshot = {
  id: string
  revision: number
  categories: Pick<ConceptionCategory, 'id' | 'title' | 'items'>[]
}

export type ConceptionHandoff = {
  branchId: string
  confirmation: ConceptionConfirmation
}

export type ConceptionDestination = 'writing' | 'assisted' | 'automatic'

export type CandidateCategory = Pick<ConceptionCategory, 'id' | 'items'>
export type ConceptionSource = Schemas['Source']

export type ConceptionDraft = Pick<ConceptionState, 'revision' | 'story_revision' | 'categories'>

export function baseRevision(draft: ConceptionDraft) {
  return { base_revision: draft.revision, base_story_revision: draft.story_revision }
}

export function removeConceptionLinks(rows: ConceptionCategory[], removed: string[]) {
  return rows.map((row) => ({
    ...row,
    items: row.items.map((item) => ({
      ...item,
      links: (item.links || []).filter((id) => !removed.includes(id)),
    })),
  }))
}

export function draftFromState(state: ConceptionState): ConceptionDraft {
  return { revision: state.revision, story_revision: state.story_revision, categories: state.categories }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

function isItem(value: unknown): value is ConceptionItem {
  return (
    isRecord(value) &&
    typeof value.id === 'string' &&
    typeof value.title === 'string' &&
    typeof value.detail === 'string' &&
    (value.links === undefined ||
      (Array.isArray(value.links) && value.links.every((link) => typeof link === 'string')))
  )
}

function isCandidateCategory(value: unknown): value is CandidateCategory {
  return (
    isRecord(value) && typeof value.id === 'string' && Array.isArray(value.items) && value.items.every(isItem)
  )
}

export function candidateCategories(value: unknown): CandidateCategory[] {
  if (!Array.isArray(value) || !value.every(isCandidateCategory))
    throw new Error('草案格式异常，请保留任务记录并重新生成。')
  return value
}

function isConceptionIssue(value: unknown): value is ConceptionIssue {
  return isRecord(value) && typeof value.category_id === 'string' && typeof value.message === 'string'
}

export function candidateIssues(value: unknown): ConceptionIssue[] {
  if (value === undefined) return []
  if (!Array.isArray(value) || !value.every(isConceptionIssue)) throw new Error('草案的核对提示格式异常。')
  return value
}

export function candidateModelReview(value: unknown): ConceptionModelReview | undefined {
  if (value === undefined) return
  if (!isRecord(value) || typeof value.version !== 'string' || !value.version || !Array.isArray(value.issues))
    throw new Error('AI 一致性复核格式异常。')
  return { version: value.version, issues: candidateIssues(value.issues) }
}

function isConceptionSource(value: unknown): value is ConceptionSource {
  return (
    isRecord(value) &&
    typeof value.chapter_id === 'string' &&
    typeof value.revision === 'number' &&
    Number.isSafeInteger(value.revision) &&
    value.revision >= 0 &&
    typeof value.start === 'number' &&
    Number.isSafeInteger(value.start) &&
    value.start >= 0 &&
    typeof value.end === 'number' &&
    Number.isSafeInteger(value.end) &&
    value.end > value.start &&
    typeof value.quote === 'string' &&
    value.quote.length === value.end - value.start
  )
}

export function conceptionSourceEvidence(value: unknown): Record<string, ConceptionSource> {
  const error = '草案中的正文依据格式异常，请保留任务记录并重新提取。'
  if (!isRecord(value)) throw new Error(error)
  return Object.fromEntries(
    Object.entries(value).map(([id, evidence]) => {
      if (!isConceptionSource(evidence)) throw new Error(error)
      return [id, evidence]
    }),
  )
}

function isCategory(value: unknown): value is ConceptionCategory {
  return (
    isRecord(value) &&
    typeof value.title === 'string' &&
    typeof value.skipped === 'boolean' &&
    isCandidateCategory(value)
  )
}

export function readConceptionDraft(key: string): ConceptionDraft | undefined {
  const value: unknown = JSON.parse(sessionStorage.getItem(key) || 'null')
  if (value === null) return
  if (
    !isRecord(value) ||
    typeof value.revision !== 'number' ||
    !Number.isSafeInteger(value.revision) ||
    value.revision < 0 ||
    typeof value.story_revision !== 'number' ||
    !Number.isSafeInteger(value.story_revision) ||
    value.story_revision < 0 ||
    !Array.isArray(value.categories) ||
    !value.categories.every(isCategory)
  )
    throw new Error('浏览器里的构思编辑无法读取。请先核对服务端已保存的版本。')
  return { revision: value.revision, story_revision: value.story_revision, categories: value.categories }
}

export const conceptionHints: Record<string, string> = {
  core: '从一句话开始，逐步明确主角的目标与动机、核心矛盾、读者期待和终局。',
  characters: '为主要人物分别记下身份、目标、限制、选择与可能的变化。',
  world: '列出会实际影响情节的环境、制度、资源、能力规则与代价。',
  relationships: '用项目记录一段关系，关联涉及的人物，写清关系变化的原因和结果。',
  events: '为主线或支线转折建立项目，关联必要前因，写清选择、结果与代价。',
  timeline: '按故事先后安排行动和结果，补记倒叙或插叙的呈现位置；未知时间可以留待核对。',
  foreshadowing: '逐条记录读者期待、铺设、强化、误导或回收方式，关联相应事件。',
  outline: '写出全书和各卷的段落目标、关键转折及收束，关联构思中的事件或伏笔。',
}
