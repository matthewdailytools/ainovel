import type { components } from './api-schema'
export type ModuleInput = components['schemas']['ModuleInput']
export type WorkflowInput = components['schemas']['WorkflowInput']
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
  basis: Record<string, unknown>
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
    waiting_artifact?: string
    latest_artifact?: string
    plan?: unknown
    completion?: unknown
  }
  payload: {
    mode?: 'assisted' | 'automatic'
    chapters?: number
    account: string
    instruction?: string
    action?: ModuleInput['action']
    fixed?: string[]
    writing_profile?: string
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
  needs_review: '等待作者审阅',
  blocked_runtime: '运行已停止',
  blocked_creative: '创作检查未通过',
  production_complete: '生成完成，待作者验收',
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
