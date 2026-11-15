import { expect, test, type Page, type BrowserContext, type Route } from '@playwright/test'
import { assertActualDoubleText } from './helpers/actualTextScale'

const DEFAULT_ORIGIN = 'http://127.0.0.1:8877'
const SUMMARY = '按新版流向来源政策继续后续章节'
const READ = '读取新版政策续跑依据'
const REREAD = '重新读取新版政策依据'
const CONFIRM = '确认使用新版政策依据，保留输入'
const REASON = '本次新版流向政策续跑原因'
const POLICY = '明确为新子任务选择 source-flow-entailment/2，接受后续新增核对及费用'
const SUBMIT = '创建并开始新版流向政策子批次'
const LOOKUP = '查询原政策提交回执'
type Row = Record<string, unknown>
type Request = {
  client_request_id: string
  preview_basis_sha256: string
  flow_source_entailment_version: string
  reason: string
}
type Task = {
  id: string
  novel_id: string
  branch_id: string
  kind: string
  status: string
  cancel: number
  created: number
  error: string
  payload: Row
  result: Row
}
type Preview = Row & {
  budget: Row
  preview_basis_sha256: string
  plan_sha256: string
}
type Reply = { status?: number; json?: unknown; abort?: boolean }
type World = {
  tasks: Task[]
  originals: Record<string, Task>
  previews: Record<string, Preview>
  posts: Request[]
  lookups: string[]
  unmatched: string[]
  external: string[]
  errors: string[]
  receipts: Map<string, Task>
  onPost?: (r: Request) => Promise<Reply> | Reply
  onPreview?: (id: string) => Promise<Reply> | Reply
  onLookup?: (nonce: string) => Reply
  commit: (r: Request) => Task
}
const worlds = new WeakMap<Page, World>()
const gate = () => {
  let release = () => {}
  const promise = new Promise<void>((resolve) => {
    release = resolve
  })
  return { promise, release }
}
function source(id = 'parent', branch = 'branch-a'): Task {
  return {
    id,
    novel_id: 'novel-flow',
    branch_id: branch,
    kind: 'workflow',
    status: 'paused',
    cancel: 0,
    created: id === 'parent' ? 1 : 2,
    error: '',
    payload: {
      mode: 'automatic',
      account: 'deepseek',
      chapters: 6,
      budget_limit: 5,
      plot_contract_check: true,
      structured_maintenance: true,
      adoption: 'automatic',
      client_request_id: 'original-' + id,
    },
    result: {
      next_chapter: 3,
      expected_story_revision: 12,
      plot_contract_version: 'author-plot-audit/2',
      conditional_book_policy_version: 'conditional-book-check/1',
      latest_artifact: 'accepted-' + id,
      structure_history: [
        {
          artifact_id: 'structure-' + id,
          policy: 'worker_automatic',
          created: 1,
        },
      ],
      plan: {
        title: '山城修理铺',
        chapters: [1, 2, 3, 4, 5, 6].map((number) => ({
          number,
          title: '账页' + number,
          plot: '林舟在山城核对修理账页，未公开旧账原因。',
          fixed: [],
        })),
      },
      plot_preparations: {},
    },
  }
}
function preview(t: Task): Preview {
  const foreign = {
    id: 'old-usd-unknown',
    task_id: 'old-parent',
    account: 'oa-old',
    provider: 'openai',
    currency: 'USD',
    reserved: 0.3,
    charged: null,
    status: 'unknown',
    input_tokens: null,
    output_tokens: null,
    has_complete_step: false,
    scope: 'workflow',
  }
  return {
    version: 'workflow-flow-policy-continuation/1',
    flow_source_entailment_version: 'source-flow-entailment/2',
    source_task_id: t.id,
    branch_id: t.branch_id,
    story_revision: 12,
    source_flow_policy: 'legacy',
    source_policies: {
      flow_source_entailment_version: null,
      plot_contract_version: 'author-plot-audit/2',
      conditional_book_policy_version: 'conditional-book-check/1',
    },
    next_chapter: 3,
    plan_sha256: 'a'.repeat(64),
    preview_basis_sha256: (t.id === 'parent' ? 'b' : 'c').repeat(64),
    account: 'deepseek',
    budget: {
      provider: 'deepseek',
      source_run_limit: 5,
      source_task_settled: 1,
      remaining_run_limit: 4,
      effective_remaining_run_limit: 3.5,
      auxiliary_settled: 0.5,
      unknown_reserve: 0,
      foreign_history_calls: [foreign],
      foreign_unknown_calls: [foreign],
    },
    notice: '旧任务与旧稿保持；新子任务后续使用新版出处核对。',
  }
}
async function setup(context: BrowserContext, page: Page) {
  const origin = test.info().project.use.baseURL || DEFAULT_ORIGIN
  const originals = Object.fromEntries(
    [source(), source('second'), source('other', 'branch-b')].map((t) => [t.id, t]),
  )
  const w: World = {
    tasks: structuredClone(Object.values(originals)),
    originals,
    previews: Object.fromEntries(Object.values(originals).map((t) => [t.id, preview(t)])),
    posts: [],
    lookups: [],
    unmatched: [],
    external: [],
    errors: [],
    receipts: new Map(),
    commit(r) {
      const parent = originals.parent,
        p = w.previews.parent
      const child: Task = {
        ...structuredClone(parent),
        id: 'child-' + r.client_request_id,
        status: 'queued',
        created: 100,
        payload: {
          ...parent.payload,
          client_request_id: r.client_request_id,
          budget_limit: 4,
        },
        result: {
          ...structuredClone(parent.result),
          continued_from: parent.id,
          flow_source_entailment_version: r.flow_source_entailment_version,
          flow_policy_continuation: {
            version: 'workflow-flow-policy-continuation/1',
            source_task_id: parent.id,
            source_flow_policy: 'legacy',
            source_payload_sha256: 'd'.repeat(64),
            source_progress_sha256: 'e'.repeat(64),
            plan_sha256: p.plan_sha256,
            next_chapter: 3,
            request: structuredClone(r),
            budget: structuredClone(p.budget),
          },
        },
      }
      w.receipts.set(r.client_request_id, child)
      w.tasks = w.tasks.map((t) =>
        t.id === parent.id
          ? {
              ...t,
              status: 'continued',
              result: { ...t.result, continued_to: child.id },
            }
          : t,
      )
      w.tasks.push(child)
      return child
    },
  }
  worlds.set(page, w)
  page.on('pageerror', (e) => w.errors.push(e.message))
  await context.addInitScript(() => {
    localStorage.setItem('ainovel-novel', 'novel-flow')
    if (!localStorage.getItem('ainovel-branch-novel-flow'))
      localStorage.setItem('ainovel-branch-novel-flow', 'branch-a')
  })
  await context.route('**/*', async (route: Route) => {
    const r = route.request(),
      url = new URL(r.url())
    if (url.origin !== origin) {
      w.external.push(r.url())
      return route.abort()
    }
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4),
      method = r.method(),
      reply = (json: unknown, status = 200) => route.fulfill({ status, json })
    if (method === 'GET' && path === '/session')
      return reply({ csrf: 'toy-csrf', data_dir: '仅隔离工程模拟' })
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
            input_rate: '2',
            output_rate: '8',
            price_source: 'toy',
          },
        ],
        budget: [
          {
            provider: 'deepseek',
            currency: 'CNY',
            limit: '80',
            settled: 3,
            reserved_unknown: 0,
            remaining: 77,
            calls: [],
          },
        ],
        sending_scope: '0模型，全API模拟',
      })
    if (method === 'GET' && path === '/novels')
      return reply([
        {
          id: 'novel-flow',
          title: '后台模拟 · 山城账页',
          brief: '仅原创工程夹具。',
        },
      ])
    if (method === 'GET' && path === '/novels/novel-flow/branches')
      return reply([
        {
          id: 'branch-a',
          novel_id: 'novel-flow',
          name: '隔离甲',
          mode: 'automatic',
          story_revision: 12,
        },
        {
          id: 'branch-b',
          novel_id: 'novel-flow',
          name: '隔离乙',
          mode: 'automatic',
          story_revision: 12,
        },
      ])
    if (method === 'GET' && path === '/tasks')
      return reply(
        url.searchParams.has('branch_id')
          ? w.tasks.filter((t) => t.branch_id === url.searchParams.get('branch_id'))
          : w.tasks,
      )
    if (method === 'GET' && (path === '/artifacts' || path === '/library/excerpts')) return reply([])
    const branch = path.match(/^\/branches\/([^/]+)\/(.+)$/)
    if (method === 'GET' && branch) {
      const id = branch[1],
        action = branch[2]
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
        return reply({
          source_branch_id: id,
          source_story_revision: 12,
          coverage: '隔离目录',
          items: [],
        })
    }
    const task = path.match(/^\/tasks\/([^/]+)(?:\/(.*))?$/)
    if (task) {
      const id = task[1],
        action = task[2]
      if (method === 'GET' && action === 'events' && w.tasks.some((t) => t.id === id)) {
        return route.fulfill({
          status: 200,
          contentType: 'text/event-stream',
          body: 'event: end\ndata: {}\n\n',
        })
      }
      if (method === 'GET' && !action) {
        const found = w.tasks.find((t) => t.id === id)
        if (found) return reply(found)
      }
      if (method === 'GET' && action === 'continue-flow-policy-preview') {
        const value = await w.onPreview?.(id)
        return value?.abort ? route.abort() : reply(value?.json ?? w.previews[id], value?.status ?? 200)
      }
      if (method === 'POST' && action === 'continue-flow-policy') {
        const body = r.postDataJSON() as Request
        expect(Object.keys(body).sort()).toEqual([
          'client_request_id',
          'flow_source_entailment_version',
          'preview_basis_sha256',
          'reason',
        ])
        expect(r.headers()['x-ainovel-csrf']).toBe('toy-csrf')
        expect(body.flow_source_entailment_version).toBe('source-flow-entailment/2')
        w.posts.push(body)
        const value = await w.onPost?.(body)
        if (value?.abort) return route.abort()
        return reply(value?.json ?? w.commit(body), value?.status ?? 202)
      }
      if (method === 'GET' && action?.startsWith('flow-policy-continuations/')) {
        const nonce = action.split('/')[1]
        w.lookups.push(nonce)
        const value = w.onLookup?.(nonce)
        if (value) return reply(value.json, value.status)
        return w.receipts.has(nonce)
          ? reply(w.receipts.get(nonce))
          : reply({ code: 'not_found', message: '原nonce尚未查到，禁止重发。' }, 404)
      }
    }
    w.unmatched.push(method + ' ' + path)
    return route.abort('blockedbyclient')
  })
  await page.goto(origin)
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  await page.getByRole('combobox', { name: '本作品批次', exact: true }).selectOption('parent')
  return w
}
const panel = (page: Page) => page.getByRole('region', { name: '新版流向政策续跑', exact: true })
async function open(page: Page) {
  await page.getByText(SUMMARY, { exact: true }).click()
  await panel(page).getByRole('button', { name: READ, exact: true }).click()
  await expect(panel(page).getByRole('textbox', { name: REASON })).toBeVisible()
}
async function decide(page: Page, reason = '  让后续问句与答案分别核对。  ') {
  await panel(page).getByRole('textbox', { name: REASON }).fill(reason)
  await panel(page).getByRole('checkbox', { name: POLICY, exact: true }).check()
}
test.afterEach(async ({ page, context }) => {
  await context.unrouteAll({ behavior: 'wait' })
  const w = worlds.get(page)
  if (w) {
    expect(w.unmatched).toEqual([])
    expect(w.external).toEqual([])
    expect(w.errors).toEqual([])
  }
})

test('写前重核与政策续跑同时存在时，轮询与切任务不复制或串用入口', async ({ page, context }) => {
  const w = await setup(context, page)
  w.tasks = w.tasks.map((task) => ({
    ...task,
    result: { ...task.result, workflow_plan_preparation_version: 'workflow-plan-preparation/1' },
  }))
  for (const task of w.tasks) {
    w.previews[task.id].source_policies = {
      ...(w.previews[task.id].source_policies as Row),
      workflow_plan_preparation_version: 'workflow-plan-preparation/1',
    }
  }
  const planSummary = page.getByText('补齐AI粗纲未明确的工作细节并重新核对', { exact: true })
  const flowSummary = page.getByText(SUMMARY, { exact: true })
  await expect(planSummary).toHaveCount(1)
  await expect(flowSummary).toHaveCount(1)
  for (const id of ['second', 'parent', 'other', 'parent']) {
    await page.getByRole('combobox', { name: '本作品批次', exact: true }).selectOption(id)
    await expect(planSummary).toHaveCount(1)
    await expect(flowSummary).toHaveCount(1)
  }
  await open(page)
  await expect(panel(page).getByRole('textbox', { name: REASON })).toBeVisible()
  const polling = page.waitForResponse(
    (response) => response.request().method() === 'GET' && new URL(response.url()).pathname === '/api/tasks',
  )
  await polling
  await expect(planSummary).toHaveCount(1)
  await expect(flowSummary).toHaveCount(1)
  await expect(panel(page).getByRole('textbox', { name: REASON })).toBeVisible()
  expect(w.posts).toHaveLength(0)
})

test('明确预览与选择后只建一个子任务，保原规划费用并显式定位', async ({ page, context }, info) => {
  const w = await setup(context, page)
  await open(page)
  expect(w.posts).toHaveLength(0)
  await expect(panel(page)).toContainText('offline-mock')
  await expect(panel(page)).toContainText('77.000000 CNY')
  await expect(panel(page)).toContainText('3.500000 CNY')
  await expect(panel(page)).toContainText('历史外币未决 1 笔')
  await panel(page).getByRole('textbox', { name: REASON }).fill('  保留原输入空白与政策原因。  ')
  await expect(panel(page).getByRole('button', { name: SUBMIT, exact: true })).toBeDisabled()
  await panel(page).getByRole('checkbox', { name: POLICY, exact: true }).check()
  await panel(page).screenshot({
    path: info.outputPath('flow-preview-desktop.png'),
  })
  await panel(page).getByRole('button', { name: SUBMIT, exact: true }).dblclick()
  await expect(page.getByRole('region', { name: '新版流向政策子任务回执' })).toBeVisible()
  expect(w.posts).toHaveLength(1)
  expect(w.posts[0].reason).toBe('  保留原输入空白与政策原因。  ')
  expect(w.tasks.find((t) => t.id === 'parent')?.result.plan).toEqual(w.originals.parent.result.plan)
  await panel(page).getByRole('button', { name: '打开新版政策子任务', exact: true }).click()
  await expect(page.getByRole('combobox', { name: '本作品批次', exact: true })).toHaveValue(
    'child-' + w.posts[0].client_request_id,
  )
})
test('409保原因与政策输入，读取新basis需再次确认且新nonce', async ({ page, context }) => {
  const w = await setup(context, page)
  let rejected = true
  w.onPost = () =>
    rejected
      ? {
          status: 409,
          json: {
            code: 'flow_basis_changed',
            message: '依据变化，未创建任务。',
          },
        }
      : {}
  await open(page)
  await decide(page)
  await panel(page).getByRole('button', { name: SUBMIT, exact: true }).click()
  await expect(panel(page).getByRole('alert')).toContainText('依据变化')
  const first = w.posts[0]
  await expect(panel(page).getByRole('textbox', { name: REASON })).toHaveValue(first.reason)
  await expect(panel(page).getByRole('checkbox', { name: POLICY, exact: true })).toBeChecked()
  await expect(panel(page).getByRole('button', { name: SUBMIT, exact: true })).toBeDisabled()
  w.previews.parent.preview_basis_sha256 = 'f'.repeat(64)
  await panel(page).getByRole('button', { name: REREAD, exact: true }).click()
  await expect(panel(page).getByRole('button', { name: SUBMIT, exact: true })).toBeDisabled()
  await panel(page).getByRole('button', { name: CONFIRM, exact: true }).click()
  rejected = false
  await panel(page).getByRole('button', { name: SUBMIT, exact: true }).click()
  await expect(page.getByRole('region', { name: '新版流向政策子任务回执' })).toBeVisible()
  expect(w.posts).toHaveLength(2)
  expect(w.posts[1].client_request_id).not.toBe(first.client_request_id)
  expect(w.posts[1].preview_basis_sha256).toBe('f'.repeat(64))
  expect(w.posts[1].reason).toBe(first.reason)
})
test('未知POST与重载只查原nonce，404不生成新请求', async ({ page, context }) => {
  const w = await setup(context, page)
  w.onPost = (r) => {
    w.commit(r)
    return { abort: true }
  }
  await open(page)
  await decide(page)
  await panel(page).getByRole('button', { name: SUBMIT, exact: true }).click()
  await expect(panel(page).getByRole('button', { name: LOOKUP, exact: true })).toBeVisible()
  expect(w.posts).toHaveLength(1)
  const nonce = w.posts[0].client_request_id
  await expect(page.getByRole('button', { name: '从保存的检查点恢复', exact: true })).toBeDisabled()
  w.onLookup = () => ({
    status: 404,
    json: { code: 'not_found', message: '原nonce尚未查到。' },
  })
  await panel(page).getByRole('button', { name: LOOKUP, exact: true }).click()
  await expect(panel(page).getByRole('alert')).toContainText('不代表永远未创建')
  await page.reload()
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  await page.getByRole('combobox', { name: '本作品批次', exact: true }).selectOption('parent')
  await expect(panel(page).getByRole('button', { name: LOOKUP, exact: true })).toBeVisible()
  w.onLookup = undefined
  await panel(page).getByRole('button', { name: LOOKUP, exact: true }).click()
  await expect(page.getByRole('region', { name: '新版流向政策子任务回执' })).toBeVisible()
  expect(w.posts).toHaveLength(1)
  expect(w.lookups).toEqual([nonce, nonce])
})
test('旧FLOW/1待查提交在新版界面仅查原nonce，既不换版本也不补发POST', async ({ page, context }) => {
  const w = await setup(context, page)
  const legacyRequest: Request = {
    client_request_id: 'historical-flow-v1-browser-nonce',
    preview_basis_sha256: w.previews.parent.preview_basis_sha256,
    flow_source_entailment_version: 'source-flow-entailment/1',
    reason: '旧版本已有未知提交，只读查证原回执。',
  }
  const config = await page.evaluate(async () => (await fetch('/api/config')).json())
  const capsule = {
    request: legacyRequest,
    source: structuredClone(w.originals.parent),
    context: {
      preview: {
        ...structuredClone(w.previews.parent),
        flow_source_entailment_version: 'source-flow-entailment/1',
      },
      account: config.accounts[0],
      shared: config.budget[0],
    },
  }
  const child = w.commit(legacyRequest)
  expect(child.result.flow_source_entailment_version).toBe('source-flow-entailment/1')
  await page.evaluate((pending) => {
    const owner = JSON.stringify(['novel-flow', 'branch-a', 'parent'])
    sessionStorage.setItem(
      `ainovel-flow-continuation:${owner}`,
      JSON.stringify({
        version: 1,
        reason: pending.request.reason,
        selectedPolicy: true,
        outcome: 'unverified',
        pending,
      }),
    )
  }, capsule)
  await page.reload()
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  await page.getByRole('combobox', { name: '本作品批次', exact: true }).selectOption('parent')
  await expect(panel(page).getByRole('button', { name: LOOKUP, exact: true })).toBeVisible()
  await panel(page).getByRole('button', { name: LOOKUP, exact: true }).click()
  await expect(page.getByRole('region', { name: '新版流向政策子任务回执' })).toContainText(
    'source-flow-entailment/1',
  )
  expect(w.posts).toEqual([])
  expect(w.lookups).toEqual([legacyRequest.client_request_id])
})
test('非法200目标不冒充成功，只读查回原绑定任务', async ({ page, context }) => {
  const w = await setup(context, page)
  w.onPost = (r) => {
    const child = w.commit(r)
    return { json: { ...child, branch_id: 'other-branch' } }
  }
  await open(page)
  await decide(page)
  await panel(page).getByRole('button', { name: SUBMIT, exact: true }).click()
  await expect(panel(page).getByRole('alert')).toContainText('回执与原提交')
  await expect(page.getByRole('region', { name: '新版流向政策子任务回执' })).toHaveCount(0)
  await panel(page).getByRole('button', { name: LOOKUP, exact: true }).click()
  await expect(page.getByRole('region', { name: '新版流向政策子任务回执' })).toBeVisible()
  expect(w.posts).toHaveLength(1)
})
test('切任务后的旧preview不流入新目标，待审明确禁用', async ({ page, context }) => {
  const w = await setup(context, page),
    g = gate()
  w.onPreview = async (id) => {
    if (id === 'parent') await g.promise
    return { json: w.previews[id] }
  }
  await page.getByText(SUMMARY, { exact: true }).click()
  await panel(page).getByRole('button', { name: READ, exact: true }).click()
  await page.getByRole('combobox', { name: '本作品批次', exact: true }).selectOption('second')
  g.release()
  await page.getByText(SUMMARY, { exact: true }).click()
  await expect(panel(page).getByRole('textbox', { name: REASON })).toHaveCount(0)
  await panel(page).getByRole('button', { name: READ, exact: true }).click()
  await expect(panel(page).getByRole('textbox', { name: REASON })).toBeVisible()
  expect(w.posts).toHaveLength(0)
  w.tasks = w.tasks.map((t) => (t.id === 'second' ? { ...t, status: 'needs_review' } : t))
  await expect(panel(page)).toContainText('当前仍待审阅', { timeout: 8000 })
  await expect(panel(page).getByRole('button', { name: REREAD, exact: true })).toBeDisabled()
})
test('中文组合不误提交，键盘与320实际文字2倍不丢输入', async ({ page, context }, info) => {
  const w = await setup(context, page)
  await open(page)
  await decide(page, '核对后续新审查，保留所有旧记录与原币种预留。')
  const input = panel(page).getByRole('textbox', { name: REASON }),
    submit = panel(page).getByRole('button', { name: SUBMIT, exact: true })
  await input.dispatchEvent('compositionstart')
  await submit.click()
  expect(w.posts).toHaveLength(0)
  await input.dispatchEvent('compositionend')
  await page.setViewportSize({ width: 320, height: 800 })
  const details = panel(page).getByText('查看冻结依据与逐币历史费用', {
    exact: true,
  })
  await details.focus()
  await details.press('Enter')
  await assertActualDoubleText(
    page,
    {
      paragraph: panel(page).locator('p').first(),
      quote: panel(page).locator('pre').first(),
      small: panel(page).locator('p.small').first(),
      summary: page.getByText(SUMMARY, { exact: true }),
      button: submit,
    },
    info,
  )
  await expect(input).toHaveValue('核对后续新审查，保留所有旧记录与原币种预留。')
  await panel(page).screenshot({
    path: info.outputPath('flow-preview-320-actual2x.png'),
  })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
  await submit.focus()
  await submit.press('Enter')
  await expect(page.getByRole('region', { name: '新版流向政策子任务回执' })).toBeVisible()
  expect(w.posts).toHaveLength(1)
})

test('切版本后的晚POST不显示错误目标成功，原nonce可回源查证', async ({ page, context }) => {
  const w = await setup(context, page),
    g = gate()
  w.onPost = async (r) => {
    await g.promise
    return { json: w.commit(r) }
  }
  await open(page)
  await decide(page)
  await panel(page).getByRole('button', { name: SUBMIT, exact: true }).click()
  await expect.poll(() => w.posts.length).toBe(1)
  page.on('dialog', (dialog) => dialog.accept())
  await page.getByRole('combobox', { name: '工作版本', exact: true }).selectOption('branch-b')
  g.release()
  await expect(panel(page)).toContainText('所选任务属于其他工作版本')
  await expect(page.getByRole('region', { name: '新版流向政策子任务回执' })).toHaveCount(0)
  await page.getByRole('combobox', { name: '工作版本', exact: true }).selectOption('branch-a')
  await expect(panel(page).getByRole('button', { name: LOOKUP, exact: true })).toBeVisible()
  await panel(page).getByRole('button', { name: LOOKUP, exact: true }).click()
  await expect(page.getByRole('region', { name: '新版流向政策子任务回执' })).toBeVisible()
  expect(w.posts).toHaveLength(1)
  expect(w.lookups).toEqual([w.posts[0].client_request_id])
})
