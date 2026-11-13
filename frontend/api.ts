import type { components } from './api-schema'
export type IntentFields = {
  emotion_target: string
  core_hook: string
  direction: string
  ending: string
  fixed_results: string[]
  prohibitions: string[]
  open_details: string[]
}
export type AuthorContract = {
  branch_id?: string
  contract_version: string
  configured: boolean
  author_text: string
  intent_contract: IntentFields | null
  provenance: unknown
  source_artifact_id: string | null
  source_story_revision: number | null
  story_revision?: number
  legacy_unconfigured?: boolean
}
export type ModuleInput = Omit<components['schemas']['ModuleInput'], 'plot_contract_check'> & {
  plot_contract_check?: boolean
}
export type WorkflowInput = Omit<components['schemas']['WorkflowInput'], 'plot_contract_check'> & {
  plot_contract_check?: boolean
}
export type ContinuationInput = components['schemas']['ContinuationInput']
export type DefinitionInput = components['schemas']['DefinitionInput']
export type GenreTopic = {
  id: string
  title: string
  name: string
  group: string
  owner: string
  items: string[]
}
export type GenreCatalog = {
  version: string
  genres: { id: string; name: string; title: string; family: string; description: string }[]
  mechanisms: { id: string; name: string; title: string; items: string[] }[]
  audiences: { id: string; name: string }[]
}
export type GenreProfile = {
  catalog_version: string
  audience: '不限' | '男频' | '女频'
  primary_genre: string | null
  secondary_genres: string[]
  genre_ids: string[]
  custom_genres: string[]
  mechanisms: string[]
  mechanism_ids: string[]
  custom_mechanisms: string[]
  author_exceptions: string[]
  author_notes: string
  reading_promises: string[]
  focus: string[]
  checks: string[]
  required_topics: GenreTopic[]
  optional_topics: GenreTopic[]
  conflicts: string[]
  priority: string[]
  configured: boolean
  source_artifact_id?: string | null
  source_story_revision?: number | null
  story_revision?: number
  branch_id?: string
  reader_definition?: {
    audience?: string
    genres?: string[]
    mechanisms?: string[]
    author_exceptions?: string[]
    text?: string
  } | null
  legacy_unconfigured?: boolean
}
export type Novel = { id: string; title: string; brief: string }
export type Branch = { id: string; novel_id: string; name: string; mode: string; story_revision: number }
export type Chapter = {
  id: string
  branch_id: string
  position: number
  title: string
  revision: number
  content: string
  stale: number
  chars?: number
}
export type Artifact = {
  id: string
  branch_id?: string
  task_id?: string | null
  candidate_revision: number
  kind: string
  chapter_id: string | null
  base_revision: number | null
  status: string
  payload: {
    text?: string
    original?: string
    start?: number
    end?: number
    review_passed?: boolean
    [key: string]: unknown
  }
  basis: Record<string, unknown> & { genre_profile?: GenreProfile }
}
export type Task = {
  id: string
  created: number
  novel_id: string
  branch_id: string
  kind: string
  status: string
  cancel: number
  error: string
  result: {
    next_chapter?: number
    repair_round?: number
    continued_to?: string
    continued_from?: string
    waiting_artifact?: string
    waiting_structure?: string
    pending_structure?: { chapter_id: string; revision: number; artifact_id?: string }
    structure_history?: { artifact_id: string; policy: string; created: number }[]
    structure_inheritance?: {
      artifact_id: string
      source_branch_id: string
      source_story_revision: number
      records: {
        source_id: string
        source_revision: number
        target_id: string
        kind: string
        title: string
      }[]
      copied_manuscripts: boolean
    }
    artifact_id?: string
    latest_artifact?: string
    plan?: unknown
    completion?: unknown
    author_contract_snapshot?: AuthorContract
    genre_profile_snapshot?: GenreProfile
    character_field_repairs?: unknown
  }
  payload: {
    completion_scope?: 'whole_book' | 'segment'
    mode?: 'assisted' | 'automatic'
    chapters?: number
    account: string
    instruction?: string
    action?: ModuleInput['action']
    fixed?: string[]
    writing_profile?: string
    budget_limit?: number | null
    structured_maintenance?: boolean
    adoption?: 'manual' | 'automatic'
    character_field_repair?: boolean
    plot_contract_check?: boolean
  }
}
export type Account = {
  id: string
  kind: string
  model: string
  configured: boolean
  priced: boolean
  currency: string
  input_rate: string
  output_rate: string
  price_source: string
}
export type Money = {
  provider: string
  currency: string
  limit: string
  settled: number
  reserved_unknown: number
  remaining: number
  calls: {
    id: string
    task_id: string
    account: string
    model: string
    status: string
    input_tokens: number
    output_tokens: number
    charged: number
    reserved: number
  }[]
}
export type Config = { accounts: Account[]; budget: Money[]; sending_scope: string }
export type Fact = {
  id: string
  chapter_id: string
  source_revision: number
  subject: string
  kind: string
  truth: string
  statement: string
  story_time: string
  evidence: string
  evidence_start: number
  evidence_end: number
  stale: number
  position: number
}
export type Book = {
  id: string
  title: string
  source: string
  status: string
  coverage: string
  file_hash: string
}
export type Unit = {
  id: string
  book_id: string
  position: number
  title: string
  content: string
  book_title?: string
  source?: string
  file_hash?: string
}
export type Excerpt = {
  id: string
  start: number
  end: number
  quote: string
  note: string
  title: string
  unit_id: string
  book_id: string
  source: string
}

let token = ''
export async function startSession() {
  const session = await api<{ csrf: string; data_dir: string }>('/session')
  token = session.csrf
  return session
}
export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
  ) {
    super(message)
  }
}
export async function api<T = unknown>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const headers: Record<string, string> = {}
  if (method !== 'GET') headers['X-Ainovel-CSRF'] = token
  const form = body instanceof FormData
  if (body !== undefined && !form) headers['Content-Type'] = 'application/json'
  const response = await fetch('/api' + path, {
    method,
    headers,
    credentials: 'same-origin',
    body: body === undefined ? undefined : form ? body : JSON.stringify(body),
  })
  let data: unknown
  try {
    data = await response.json()
  } catch {
    throw new ApiError(
      'invalid_response',
      '本机服务未返回有效数据；请检查启动窗口并保留当前草稿后重连。',
      response.status,
    )
  }
  if (!data || typeof data !== 'object')
    throw new ApiError('invalid_response', '本机服务返回格式不正确，当前输入已保留。', response.status)
  if (!response.ok)
    throw new ApiError(
      ('code' in data && typeof data.code === 'string' ? data.code : '') || 'request',
      ('message' in data && typeof data.message === 'string' ? data.message : '') ||
        (response.status === 422 ? '输入格式不正确，请检查必填项、编号和字数。' : '请求失败。'),
      response.status,
    )
  return data as T
}

export const statuses: Record<string, string> = {
  queued: '排队中',
  running: '执行中',
  succeeded: '候选已生成',
  cancelled: '已取消',
  paused: '已暂停',
  continued: '已转入续跑任务',
  needs_review: '等待作者审阅',
  blocked_runtime: '运行已停止',
  blocked_creative: '创作检查未通过',
  production_complete: '生成完成，待作者验收',
  segment_complete: '本批阶段完成，非全书完结',
  candidate: '待采纳',
  accepted: '已采纳',
  rejected: '已拒绝',
  ready: '可阅读',
  partial: '部分可用',
  parse_failed: '解析失败',
  failed: '获取失败',
  downloading: '下载中',
}
export const label = (state: string) => statuses[state] || state
