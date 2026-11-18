import { expect, test, type BrowserContext, type Locator, type Page, type Route } from '@playwright/test'
import { createHash } from 'node:crypto'
import type { components } from '../../frontend/api-schema'

const SUMMARY = '补齐AI粗纲未明确的工作细节并重新核对'
const READ = '读取写前重核依据'
const REREAD = '重新读取写前重核依据'
const CONFIRM = '确认使用新依据，保留原因'
const REASON = '本次写前重核原因'
const SUBMIT = '创建新任务重新核对'
const LOOKUP = '查询原提交回执'
const NOVEL = 'toy-novel-restart'
const BRANCH = 'toy-branch-a'
const SECOND_BRANCH = 'toy-branch-b'
const branches = [BRANCH, SECOND_BRANCH]
const PARENT = 'toy-parent-a'
const SECOND_TASK = 'toy-parent-a2'
const OTHER_TASK = 'toy-parent-b'
const PLOT = '许棠在驿站整理账页，提到旧账缺口，把疑问记在纸条上，未移交账本。'
const hash = (text: string) => createHash('sha256').update(text).digest('hex')
const categories = [
  ['core', '故事核心'],
  ['characters', '人物'],
  ['world', '世界设定'],
  ['relationships', '人物关系'],
  ['events', '事件与多线情节'],
  ['timeline', '时间线'],
  ['foreshadowing', '伏笔与回收'],
  ['outline', '全书与分卷大纲'],
].map(([id, title]) => ({ id, title, items: [], skipped: false }))
const conception = {
  revision: 0,
  story_revision: 12,
  categories,
  issues: categories.map(({ id }) => ({ category_id: id, message: '尚未填写构思内容。' })),
  confirmation: null,
  observed: [],
  sources: [],
  project: null,
  project_impacts: [],
  setting_findings: [],
} satisfies components['schemas']['ConceptionWorkspace']
type Row = Record<string, unknown>
type Preparation = Row & { chapter_id: string; original_plot: string; original_plot_sha256: string }
type MockTask = {
  id: string
  novel_id: string
  branch_id: string
  kind: string
  status: string
  created: number
  cancel: number
  error: string
  payload: Row & { account: string; budget_limit: number; mode: string }
  result: Row & {
    next_chapter: number
    expected_story_revision: number
    plan: unknown
    plot_preparations: Record<string, Preparation>
  }
}
type Basis = { chapter: number; base_story_revision: number; base_progress_sha256: string }
type Preview = {
  version: string
  workflow_plan_open_details_version: string
  task_id: string
  branch_id: string
  chapter_id: string
  request_basis: Basis
  original_plot: string
  original_plot_sha256: string
  old_preparation_sha256: string
  known_invalid_steps: { fingerprints: string[]; call_ids: string[] }
  budget: {
    provider: string
    source_run_limit: number
    source_task_settled: number
    remaining_run_limit: number
    unknown_reserve: number
    lineage_settled: number
    lineage_by_currency: unknown[]
    settled_calls_without_step: unknown[]
    lineage_task_ids: string[]
    old_call_replay: string
    note: string
  }
  policy: string
}
type RestartRequest = Basis & { client_request_id: string; reason: string }
type Reply = { status?: number; json?: unknown; abort?: boolean }
type Call = { method: string; path: string; body?: unknown }
type World = {
  tasks: MockTask[]
  originals: Record<string, MockTask>
  previews: Record<string, Preview>
  calls: Call[]
  posts: { parent: string; request: RestartRequest }[]
  unmatched: string[]
  external: string[]
  errors: string[]
  previewReads: Record<string, number>
  lookupReads: Record<string, number>
  receipts: Map<string, MockTask>
  onPreview?: (parent: string, count: number) => Promise<Reply | undefined> | Reply | undefined
  onPost?: (parent: string, request: RestartRequest) => Promise<Reply | undefined> | Reply | undefined
  onLookup?: (parent: string, nonce: string, count: number) => Promise<Reply | undefined> | Reply | undefined
  commit: (parent: string, request: RestartRequest) => MockTask
}
const worlds = new WeakMap<Page, World>()
function gate() {
  let release = () => {}
  const promise = new Promise<void>((resolve) => {
    release = resolve
  })
  return { promise, release }
}
function task(id: string, branch = BRANCH): MockTask {
  const plot = id === PARENT ? PLOT : '许棠核对另一册账页，' + id + '的空白仍待本章安排。'
  const chapterId = 'toy-chapter-' + id
  return {
    id,
    novel_id: NOVEL,
    branch_id: branch,
    kind: 'workflow',
    status: 'blocked_runtime',
    created: id === PARENT ? 1700000000 : 1700000001,
    cancel: 0,
    error: '原三次写前解释未通过连续原话与接收范围核对；正文未派发。',
    payload: {
      mode: 'automatic',
      account: 'deepseek',
      budget_limit: 1,
      chapters: 4,
      completion_scope: 'whole_book',
      adoption: 'automatic',
      plot_contract_check: true,
      structured_maintenance: true,
      fixed: ['本章不完成账本移交。'],
      client_request_id: 'toy-old-request-' + id,
    },
    result: {
      next_chapter: 4,
      expected_story_revision: 12,
      workflow_plan_preparation_version: 'workflow-plan-preparation/1',
      plot_contract_version: 'author-plot-audit/2',
      plan: { title: '驿站旧账', chapters: [{ number: 4, title: '账页缺口', plot, fixed: [] }] },
      plot_preparations: {
        '4': {
          chapter_id: chapterId,
          status: 'interpreting',
          round: 0,
          original_plot: plot,
          original_plot_sha256: hash(plot),
          reason: '三份旧解释保留，未形成已冻结的执行安排。',
          interpretations: [],
          phases: {
            'interpret:0': {
              status: 'started',
              owner_task_id: id,
              attempt: 1,
              before_steps: [],
              before_calls: [],
            },
          },
          choices: [],
          preservation_checks: [],
        },
      },
    },
  }
}
function preview(source: MockTask): Preview {
  const row = source.result.plot_preparations['4']
  return {
    version: 'workflow-plan-restart/1',
    workflow_plan_open_details_version: 'workflow-plan-open-details/1',
    task_id: source.id,
    branch_id: source.branch_id,
    chapter_id: row.chapter_id,
    request_basis: {
      chapter: 4,
      base_story_revision: 12,
      base_progress_sha256: hash(source.id + '-basis-1'),
    },
    original_plot: row.original_plot,
    original_plot_sha256: row.original_plot_sha256,
    old_preparation_sha256: hash(source.id + '-old-preparation'),
    known_invalid_steps: {
      fingerprints: [1, 2, 3].map((n) => hash(source.id + '-step-' + n)),
      call_ids: [1, 2, 3].map((n) => 'toy-settled-call-' + source.id + '-' + n),
    },
    budget: {
      provider: 'deepseek',
      source_run_limit: 1,
      source_task_settled: 0.12,
      remaining_run_limit: 0.88,
      unknown_reserve: 0,
      lineage_settled: 0.12,
      lineage_by_currency: [],
      settled_calls_without_step: [],
      lineage_task_ids: [source.id],
      old_call_replay: '三份已知无效解释保留，不把旧缓存改成新解释。',
      note: '仅工程模拟账目；沿用父账户及共享累计账本，不增加限额。',
    },
    policy: '先选择AI粗纲尚未指定的细节，再严格核对原安排；这不表示正文或整书通过。',
  }
}
function record(value: unknown): value is Row {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}
function restartBody(route: Route): RestartRequest {
  const body: unknown = route.request().postDataJSON()
  expect(record(body)).toBeTruthy()
  if (!record(body)) throw new Error('Expected JSON object')
  expect(Object.keys(body).sort()).toEqual([
    'base_progress_sha256',
    'base_story_revision',
    'chapter',
    'client_request_id',
    'reason',
  ])
  expect(typeof body.client_request_id).toBe('string')
  expect(String(body.client_request_id).length).toBeGreaterThanOrEqual(8)
  expect(String(body.client_request_id).length).toBeLessThanOrEqual(128)
  expect(typeof body.reason).toBe('string')
  expect(typeof body.chapter).toBe('number')
  expect(typeof body.base_story_revision).toBe('number')
  expect(typeof body.base_progress_sha256).toBe('string')
  expect(route.request().headers()['x-ainovel-csrf']).toBe('toy-csrf')
  return body as RestartRequest
}
async function setup(context: BrowserContext, page: Page) {
  const base = new URL(test.info().project.use.baseURL ?? 'http://127.0.0.1:8877')
  expect(`${base.protocol}//${base.hostname}`).toBe('http://127.0.0.1')
  const sources = [task(PARENT), task(SECOND_TASK), task(OTHER_TASK, SECOND_BRANCH)]
  const originals = Object.fromEntries(sources.map((s) => [s.id, structuredClone(s)]))
  const world: World = {
    tasks: structuredClone(sources),
    originals,
    previews: Object.fromEntries(sources.map((s) => [s.id, preview(s)])),
    calls: [],
    posts: [],
    unmatched: [],
    external: [],
    errors: [],
    previewReads: {},
    lookupReads: {},
    receipts: new Map(),
    commit(parent, request) {
      const old = originals[parent],
        basis = world.previews[parent]
      const child: MockTask = {
        ...structuredClone(old),
        id: 'toy-child-' + request.client_request_id,
        status: 'queued',
        created: old.created + 100,
        error: '',
        payload: {
          ...structuredClone(old.payload),
          budget_limit: basis.budget.remaining_run_limit,
          client_request_id: request.client_request_id,
        },
        result: {
          ...structuredClone(old.result),
          plot_preparations: {},
          continued_from: parent,
          workflow_plan_open_details_version: 'workflow-plan-open-details/1',
          plan_preparation_restart: {
            version: 'workflow-plan-restart/1',
            source_task_id: parent,
            request: structuredClone(request),
            previous_preparation_sha256: basis.old_preparation_sha256,
            known_invalid_steps: structuredClone(basis.known_invalid_steps),
            budget: structuredClone(basis.budget),
          },
        },
      }
      world.receipts.set(parent + '/' + request.client_request_id, child)
      world.tasks = [
        ...world.tasks
          .filter((t) => t.id !== child.id)
          .map((t) =>
            t.id === parent
              ? { ...t, status: 'continued', result: { ...t.result, continued_to: child.id } }
              : t,
          ),
        child,
      ]
      return child
    },
  }
  worlds.set(page, world)
  page.on('pageerror', (error) => world.errors.push(error.message))
  await context.addInitScript(
    ({ novel, branch }) => {
      localStorage.setItem('ainovel-novel', novel)
      if (!localStorage.getItem('ainovel-branch-' + novel))
        localStorage.setItem('ainovel-branch-' + novel, branch)
    },
    { novel: NOVEL, branch: BRANCH },
  )
  await context.route('**/*', async (route) => {
    const request = route.request(),
      url = new URL(request.url())
    if (url.origin !== base.origin) {
      world.external.push(request.url())
      return route.abort('blockedbyclient')
    }
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4),
      method = request.method()
    world.calls.push({
      method,
      path: path + url.search,
      body: request.postData() ? request.postDataJSON() : undefined,
    })
    async function reply(value: unknown, status = 200) {
      await route.fulfill({ status, json: value })
    }
    if (method === 'GET' && path === '/session')
      return reply({ csrf: 'toy-csrf', data_dir: '隔离无头模拟数据' })
    if (method === 'GET' && path === '/config')
      return reply({
        accounts: [
          {
            id: 'deepseek',
            kind: 'deepseek',
            model: 'offline-mock',
            configured: true,
            priced: true,
            currency: 'CNY',
            input_rate: '1',
            output_rate: '1',
            price_source: '#',
          },
        ],
        budget: [
          {
            provider: 'deepseek',
            currency: 'CNY',
            limit: '80',
            settled: 0.12,
            reserved_unknown: 0,
            remaining: 79.88,
            calls: [],
          },
        ],
        sending_scope: '仅模拟路由，不连接模型。',
      })
    if (method === 'GET' && path === '/novels')
      return reply([{ id: NOVEL, title: '工程原创 · 驿站旧账', brief: '核对旧账，不提前移交。' }])
    if (method === 'GET' && path === '/novels/' + NOVEL + '/branches')
      return reply([
        { id: BRANCH, novel_id: NOVEL, name: '隔离版本甲', mode: 'automatic', story_revision: 12 },
        { id: SECOND_BRANCH, novel_id: NOVEL, name: '隔离版本乙', mode: 'automatic', story_revision: 12 },
      ])
    if (method === 'GET' && path === '/tasks') {
      const branch = url.searchParams.get('branch_id')
      return reply(branch ? world.tasks.filter((t) => t.branch_id === branch) : world.tasks)
    }
    if (method === 'GET' && (path === '/artifacts' || path === '/library/excerpts')) return reply([])
    if (
      method === 'GET' &&
      path === '/reference-sessions' &&
      branches.some((id) => url.search === `?branch_id=${id}`)
    )
      return reply([])
    const branchMatch = path.match(/^\/branches\/([^/]+)\/(.+)$/)
    if (method === 'GET' && branchMatch && branches.includes(branchMatch[1])) {
      const id = branchMatch[1],
        action = branchMatch[2]
      if (action === 'outline-workspace/setting-options' && (!url.search || url.search === '?scope=world'))
        return reply({ confirmation_id: null, items: [] })
      if (!url.search) {
        if (action === 'outline-workspace')
          return reply({
            draft: null,
            confirmed: null,
            stale: [],
            story_revision: 12,
            chapters: [],
            promise_options: [],
            settings: [],
            promises: [],
          })
        if (action === 'conception') return reply(conception)
        if (action === 'outline-workspace/reference-options') return reply([])
        if (action === 'seven-stage-runs/sources') return reply({ writing_id: null, inspection_id: null })
        if (action === 'seven-stage-runs') return reply([])
      }
      if (action === 'chapters') return reply([])
      if (action === 'reading-state') return reply({ chapter_id: null, cursor: 0, note: '', revision: 0 })
      if (action === 'author-contract')
        return reply({
          branch_id: id,
          contract_version: 'author-intent/1',
          configured: false,
          author_text: '',
          intent_contract: null,
          provenance: null,
          source_artifact_id: null,
          source_story_revision: null,
          story_revision: 12,
        })
      if (action === 'genre-profile')
        return reply({
          branch_id: id,
          catalog_version: 'genre-profile/1',
          configured: false,
          audience: '不限',
          primary_genre: null,
          secondary_genres: [],
          genre_ids: [],
          custom_genres: [],
          mechanisms: [],
          mechanism_ids: [],
          custom_mechanisms: [],
          author_exceptions: [],
          author_notes: '',
          reading_promises: [],
          focus: [],
          checks: [],
          required_topics: [],
          optional_topics: [],
          conflicts: [],
          priority: [],
          story_revision: 12,
        })
      if (action === 'story-inheritance')
        return reply({ source_branch_id: id, source_story_revision: 12, coverage: '隔离空目录', items: [] })
    }
    const chapter = path.match(/^\/chapters\/([^/]+)$/)
    if (method === 'GET' && chapter) {
      const source = sources.find((t) => t.result.plot_preparations['4'].chapter_id === chapter[1])
      if (source)
        return reply({
          id: chapter[1],
          branch_id: source.branch_id,
          position: 4,
          title: '空白第4章',
          revision: 0,
          content: '',
          stale: 0,
          archived: 0,
        })
    }
    const taskMatch = path.match(/^\/tasks\/([^/]+)(?:\/(.*))?$/)
    if (taskMatch) {
      const parent = taskMatch[1],
        action = taskMatch[2]
      if (method === 'GET' && action === 'events' && world.tasks.some((t) => t.id === parent)) {
        return route.fulfill({
          status: 200,
          contentType: 'text/event-stream',
          body: 'event: end\ndata: {}\n\n',
        })
      }
      if (method === 'GET' && !action) {
        const found = world.tasks.find((t) => t.id === parent)
        if (found) return reply(found)
      }
      if (method === 'GET' && action === 'reprepare-plan-preview') {
        const count = (world.previewReads[parent] = (world.previewReads[parent] || 0) + 1)
        const custom = await world.onPreview?.(parent, count)
        if (custom?.abort) return route.abort('failed')
        return reply(custom?.json ?? world.previews[parent], custom?.status ?? 200)
      }
      if (method === 'POST' && action === 'reprepare-plan') {
        const body = restartBody(route)
        world.posts.push({ parent, request: structuredClone(body) })
        const custom = await world.onPost?.(parent, body)
        if (custom?.abort) return route.abort('failed')
        return reply(custom?.json ?? world.commit(parent, body), custom?.status ?? 202)
      }
      if (method === 'GET' && action?.startsWith('plan-restarts/')) {
        const nonce = decodeURIComponent(action.slice('plan-restarts/'.length)),
          key = parent + '/' + nonce
        const count = (world.lookupReads[key] = (world.lookupReads[key] || 0) + 1)
        const custom = await world.onLookup?.(parent, nonce, count)
        if (custom?.abort) return route.abort('failed')
        return reply(
          custom?.json ?? {
            source_task_id: parent,
            client_request_id: nonce,
            task: world.receipts.get(key) ?? null,
          },
          custom?.status ?? 200,
        )
      }
    }
    world.unmatched.push(method + ' ' + path + url.search)
    return route.abort('blockedbyclient')
  })
  return world
}
async function open(page: Page, select = true) {
  await page.goto('/')
  await expect(page.getByRole('combobox', { name: '作品', exact: true })).toHaveValue(NOVEL)
  await expect(page.getByRole('combobox', { name: '工作版本', exact: true })).toHaveValue(BRANCH)
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  if (select) await page.getByRole('combobox', { name: '本作品批次', exact: true }).selectOption(PARENT)
  return openPanel(page)
}
async function openPanel(page: Page) {
  const summary = page.getByText(SUMMARY, { exact: true })
  await expect(summary).toBeVisible()
  const panel = summary.locator('..')
  if ((await panel.getAttribute('open')) === null) await summary.click()
  return panel
}
async function read(page: Page, panel: Locator) {
  await panel.getByRole('button', { name: READ, exact: true }).click()
  await expect(panel.getByRole('textbox', { name: REASON, exact: true })).toBeVisible()
  await expect(panel.getByText(PLOT, { exact: true })).toBeVisible()
}
test.afterEach(async ({ page }, info) => {
  const world = worlds.get(page)
  if (!world) return
  await info.attach('mock-network-and-requests', {
    contentType: 'application/json',
    body: JSON.stringify({
      posts: world.posts,
      calls: world.calls,
      unmatched: world.unmatched,
      external: world.external,
      pageErrors: world.errors,
      realModelCalls: 0,
      actualBackendConnections: 0,
    }),
  })
  expect(world.external, 'only the isolated fixture origin may reach a server').toEqual([])
  expect(world.unmatched, 'all API routes must be explicitly fulfilled or intentionally aborted').toEqual([])
  expect(world.errors).toEqual([])
})

test('真实工作台入口：审阅原计划、三份失败来源和费用，双击只创建一个有原样依据的新任务', async ({
  context,
  page,
}, info) => {
  const world = await setup(context, page),
    pending = gate()
  world.onPost = async () => {
    await pending.promise
    return undefined
  }
  const panel = await open(page)
  await read(page, panel)
  await panel.getByText('三次已结失败调用与来源', { exact: true }).click()
  for (const id of world.previews[PARENT].known_invalid_steps.call_ids) await expect(panel).toContainText(id)
  for (const value of world.previews[PARENT].known_invalid_steps.fingerprints)
    await expect(panel).toContainText(value)
  await expect(panel).toContainText('0.12')
  await expect(panel).toContainText('0.88')
  await expect(panel).toContainText('CNY')
  const desktop = info.outputPath('desktop-preview.png')
  await panel.screenshot({ path: desktop })
  await info.attach('desktop-preview', { path: desktop, contentType: 'image/png' })
  await panel
    .getByRole('textbox', { name: REASON, exact: true })
    .fill('只补齐原粗纲未指定的工作细节，保持原结果。')
  const submit = panel.getByRole('button', { name: SUBMIT, exact: true })
  try {
    await submit.evaluate((element) => {
      if (!(element instanceof HTMLButtonElement)) throw new Error('Expected button')
      element.click()
      element.click()
    })
    await expect.poll(() => world.posts.length).toBe(1)
    await expect(submit).toBeDisabled()
    expect(world.posts[0].request).toMatchObject(world.previews[PARENT].request_basis)
    expect(world.posts[0].request.reason).toBe('只补齐原粗纲未指定的工作细节，保持原结果。')
  } finally {
    pending.release()
  }
  await expect(page.getByRole('region', { name: '写前重核新任务回执', exact: true })).toBeVisible()
  expect(world.posts).toHaveLength(1)
  expect(world.originals[PARENT].result.plan).toEqual(task(PARENT).result.plan)
  await page.getByRole('combobox', { name: '本作品批次', exact: true }).selectOption(PARENT)
  const old = page.getByText('AI工作章纲执行记录', { exact: true })
  await old.click()
  const records = old.locator('..')
  await records.getByText(/^第4章 · /).click()
  await expect(records.getByRole('heading', { name: '原AI章纲', exact: true })).toBeVisible()
  await records.getByText('逐轮解释、保持核对与来源', { exact: true }).click()
  // Failed model replies remain in steps/calls, not successful phase interpretations.
  const original = records.locator('pre').last()
  await expect(original).toContainText('"interpretations": []')
  await expect(original).toContainText(
    world.originals[PARENT].result.plot_preparations['4'].original_plot_sha256,
  )
  expect(world.originals[PARENT].result.plot_preparations['4'].phases).toEqual(
    task(PARENT).result.plot_preparations['4'].phases,
  )
})

test('409保留原因，不自动换依据；作者显式读取并确认新basis后才生成独立nonce', async ({ context, page }) => {
  const world = await setup(context, page)
  world.onPost = () =>
    world.posts.length === 1
      ? { status: 409, json: { code: 'task_changed', message: '服务端依据已变化，请保留原因并重新读取。' } }
      : undefined
  const panel = await open(page)
  await read(page, panel)
  const reason = panel.getByRole('textbox', { name: REASON, exact: true })
  await reason.fill('保留这段中文原因😀𠮷，原计划不替换。')
  await panel.getByRole('button', { name: SUBMIT, exact: true }).click()
  await expect(panel.getByRole('button', { name: REREAD, exact: true })).toBeVisible()
  await expect(reason).toHaveValue('保留这段中文原因😀𠮷，原计划不替换。')
  expect(world.previewReads[PARENT]).toBe(1)
  expect(world.posts).toHaveLength(1)
  world.previews[PARENT].request_basis.base_progress_sha256 = hash('new server-owned basis')
  await panel.getByRole('button', { name: REREAD, exact: true }).click()
  await expect(panel.getByRole('button', { name: CONFIRM, exact: true })).toBeVisible()
  await expect(panel.getByRole('button', { name: SUBMIT, exact: true })).toBeDisabled()
  await expect(reason).toHaveValue('保留这段中文原因😀𠮷，原计划不替换。')
  await panel.getByRole('button', { name: CONFIRM, exact: true }).click()
  await panel.getByRole('button', { name: SUBMIT, exact: true }).click()
  await expect.poll(() => world.posts.length).toBe(2)
  expect(world.posts[1].request).toMatchObject(world.previews[PARENT].request_basis)
  expect(world.posts[1].request.reason).toBe(world.posts[0].request.reason)
  expect(world.posts[1].request.client_request_id).not.toBe(world.posts[0].request.client_request_id)
  await expect(page.getByRole('region', { name: '写前重核新任务回执', exact: true })).toBeVisible()
})

test('POST响应未知：刷新保留原nonce和原因，null回执不重发，仅原标识只读查证后显示子任务', async ({
  context,
  page,
}, info) => {
  const world = await setup(context, page)
  world.onPost = (parent, request) => {
    world.commit(parent, request)
    return { abort: true }
  }
  world.onLookup = (parent, nonce, count) =>
    count === 1 ? { json: { source_task_id: parent, client_request_id: nonce, task: null } } : undefined
  let panel = await open(page)
  await read(page, panel)
  await panel.getByRole('textbox', { name: REASON, exact: true }).fill('响应不确定时只查原提交，不重复花费。')
  await panel.getByRole('button', { name: SUBMIT, exact: true }).click()
  await expect(panel.getByRole('button', { name: LOOKUP, exact: true })).toBeVisible()
  const submitted = structuredClone(world.posts[0])
  await page.reload()
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  await page.getByRole('combobox', { name: '本作品批次', exact: true }).selectOption(PARENT)
  panel = await openPanel(page)
  await expect(panel.getByRole('textbox', { name: REASON, exact: true })).toHaveValue(
    submitted.request.reason,
  )
  const unknown = info.outputPath('unknown-restored-original-nonce.png')
  await panel.screenshot({ path: unknown })
  await info.attach('unknown-restored-original-nonce', { path: unknown, contentType: 'image/png' })
  await panel.getByRole('button', { name: LOOKUP, exact: true }).click()
  await expect(panel.getByRole('button', { name: LOOKUP, exact: true })).toBeEnabled()
  expect(world.posts).toHaveLength(1)
  await expect(page.getByRole('region', { name: '写前重核新任务回执', exact: true })).toHaveCount(0)
  await panel.getByRole('button', { name: LOOKUP, exact: true }).click()
  await expect(page.getByRole('region', { name: '写前重核新任务回执', exact: true })).toBeVisible()
  expect(world.posts).toEqual([submitted])
  expect(world.calls.filter((c) => c.path.includes('/plan-restarts/')).map((c) => c.path)).toEqual([
    '/tasks/' + PARENT + '/plan-restarts/' + submitted.request.client_request_id,
    '/tasks/' + PARENT + '/plan-restarts/' + submitted.request.client_request_id,
  ])
})

for (const change of ['task', 'branch'] as const) {
  test('旧preview晚返回不污染新' + change + '，新目标保持服务端来源', async ({ context, page }) => {
    const world = await setup(context, page),
      pending = gate()
    const old = structuredClone(world.previews[PARENT])
    world.onPreview = async (parent) => {
      if (parent === PARENT) {
        await pending.promise
        return { json: old }
      }
    }
    let panel = await open(page, change !== 'branch')
    const oldResponse = page.waitForResponse(
      (response) => new URL(response.url()).pathname === '/api/tasks/' + PARENT + '/reprepare-plan-preview',
    )
    await panel.getByRole('button', { name: READ, exact: true }).click()
    await expect.poll(() => world.previewReads[PARENT] || 0).toBe(1)
    try {
      if (change === 'task')
        await page.getByRole('combobox', { name: '本作品批次', exact: true }).selectOption(SECOND_TASK)
      else await page.getByRole('combobox', { name: '工作版本', exact: true }).selectOption(SECOND_BRANCH)
      panel = await openPanel(page)
      await panel.getByRole('button', { name: READ, exact: true }).click()
      const current = world.previews[change === 'task' ? SECOND_TASK : OTHER_TASK]
      await expect(panel.getByText(current.original_plot, { exact: true })).toBeVisible()
    } finally {
      pending.release()
    }
    await (await oldResponse).finished()
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())))
    await expect(panel).not.toContainText(PLOT)
    expect(world.posts).toHaveLength(0)
  })
  test('旧POST晚返回不跳回原' + change + '或错误展示原任务成功', async ({ context, page }) => {
    const world = await setup(context, page),
      pending = gate()
    world.onPost = async () => {
      await pending.promise
      return undefined
    }
    let panel = await open(page, change !== 'branch')
    await read(page, panel)
    await panel
      .getByRole('textbox', { name: REASON, exact: true })
      .fill('旧目标的原因应保留在原目标，不能带到下一任务。')
    const oldResponse = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === '/api/tasks/' + PARENT + '/reprepare-plan' &&
        response.request().method() === 'POST',
    )
    await panel.getByRole('button', { name: SUBMIT, exact: true }).click()
    await expect.poll(() => world.posts.length).toBe(1)
    page.on('dialog', (dialog) => dialog.accept())
    try {
      if (change === 'task')
        await page.getByRole('combobox', { name: '本作品批次', exact: true }).selectOption(SECOND_TASK)
      else await page.getByRole('combobox', { name: '工作版本', exact: true }).selectOption(SECOND_BRANCH)
      panel = await openPanel(page)
      await panel.getByRole('button', { name: READ, exact: true }).click()
      await expect(
        panel.getByText(world.previews[change === 'task' ? SECOND_TASK : OTHER_TASK].original_plot, {
          exact: true,
        }),
      ).toBeVisible()
    } finally {
      pending.release()
    }
    await (await oldResponse).finished()
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())))
    await expect(
      page.getByRole('combobox', { name: change === 'task' ? '本作品批次' : '工作版本', exact: true }),
    ).toHaveValue(change === 'task' ? SECOND_TASK : SECOND_BRANCH)
    await expect(panel.getByRole('textbox', { name: REASON, exact: true })).not.toHaveValue(
      world.posts[0].request.reason,
    )
    await expect(page.getByRole('region', { name: '写前重核新任务回执', exact: true })).toHaveCount(0)
    expect(world.posts[0].parent).toBe(PARENT)
  })
}

test('320窄屏真实文字翻倍、键盘和中文组合输入：可读依据，组合Enter不派发，显式按钮仅一次', async ({
  context,
  page,
}, info) => {
  const world = await setup(context, page)
  await page.setViewportSize({ width: 320, height: 900 })
  const panel = await open(page)
  await read(page, panel)
  const reason = panel.getByRole('textbox', { name: REASON, exact: true })
  await reason.fill('核对驿站旧账😀𠮷')
  const before = await reason.evaluate((element) => parseFloat(getComputedStyle(element).fontSize))
  await page.evaluate(() => {
    const sizes = [...document.querySelectorAll('*')].map(
      (element) => [element, parseFloat(getComputedStyle(element).fontSize)] as const,
    )
    for (const [element, size] of sizes)
      if (element instanceof HTMLElement) element.style.fontSize = size * 2 + 'px'
  })
  expect(await reason.evaluate((element) => parseFloat(getComputedStyle(element).fontSize))).toBe(before * 2)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)
  const bounds = await reason.boundingBox()
  expect(bounds).not.toBeNull()
  expect(bounds!.x).toBeGreaterThanOrEqual(0)
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(320)
  await reason.scrollIntoViewIfNeeded()
  const narrow = info.outputPath('narrow-actual-2x-preview.png')
  await page.screenshot({ path: narrow })
  await info.attach('narrow-actual-2x-preview', { path: narrow, contentType: 'image/png' })
  const bodyFont = await page.evaluate(() => parseFloat(getComputedStyle(document.body).fontSize))
  await reason.focus()
  await reason.dispatchEvent('compositionstart', { data: '账' })
  await page.keyboard.press('Enter')
  await reason.dispatchEvent('compositionend', { data: '账' })
  expect(world.posts).toHaveLength(0)
  const entered = await reason.inputValue()
  await expect(reason).toHaveValue(entered)
  const submit = panel.getByRole('button', { name: SUBMIT, exact: true })
  await submit.focus()
  await expect(submit).toBeFocused()
  await page.keyboard.press('Enter')
  await expect.poll(() => world.posts.length).toBe(1)
  expect(entered.trim()).toBe('核对驿站旧账😀𠮷')
  expect(world.posts[0].request.reason).toBe(entered.trim())
  await expect(page.getByRole('region', { name: '写前重核新任务回执', exact: true })).toBeVisible()
  await info.attach('actual-text-scale', {
    contentType: 'application/json',
    body: JSON.stringify({
      before,
      after: before * 2,
      viewport: 320,
      bodyFont,
      reasonBounds: bounds,
      modelCalls: 0,
    }),
  })
})
