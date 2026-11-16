import { expect, test, type Locator, type Page, type Route } from '@playwright/test'
import { observationRetryFixture } from '../../fixtures/author-source-observation-retry'
import type { Task } from '../../frontend/api'
import { object } from '../../frontend/planRestart'
import type { ObservationRetryInput } from '../../frontend/authorSourceObservationRetry'
import { assertActualDoubleText } from './helpers/actualTextScale'

const SUMMARY = '来源引用修正未通过，核对已返回回执',
  READ = '读取来源引用恢复依据',
  REREAD = '重新读取来源引用恢复依据',
  SUBMIT = '创建并开始来源观察恢复子批次',
  INPUT = '恢复审计原因（必填，不加入情节）',
  LOOKUP = '只查询原来源引用恢复回执',
  CONFIRM = '我已核对三次已返回结算、原话和费用；确认按原采纳方式开始独立子批次',
  RECEIPT = '来源观察引用恢复回执'
function gate() {
  let release = () => {}
  const promise = new Promise<void>((done) => {
    release = done
  })
  return { promise, release }
}
type Reply = { status?: number; json?: unknown; abort?: boolean }
async function setup(page: Page) {
  const sessionResponse = await page.request.get('/api/session'),
    origin = new URL(sessionResponse.url()).origin
  const session = (await sessionResponse.json()) as { csrf: string }
  const response = await page.request.post('/api/novels', {
    headers: { 'X-Ainovel-CSRF': session.csrf },
    data: { title: '工程原创来源补充测试', brief: '独立确定性路由，不代表文学质量。' },
  })
  expect(response.status()).toBe(201)
  const novel = (await response.json()) as { id: string; branch_id: string }
  const first = observationRetryFixture('clarify-parent', novel.branch_id),
    second = observationRetryFixture('clarify-other', novel.branch_id)
  for (const fixture of [first, second]) {
    fixture.source.novel_id = novel.id
    fixture.preview.novel_id = novel.id
  }
  const tasks = [first.source, second.source],
    originals = structuredClone(tasks),
    posts: ObservationRetryInput[] = [],
    receipts = new Map<string, Task>(),
    external: string[] = [],
    errors: string[] = [],
    calls: string[] = []
  const world: {
    onPreview?: (id: string) => Promise<Reply>
    onPost?: (body: ObservationRetryInput) => Promise<Reply>
    onLookup?: (nonce: string) => Promise<Reply>
    commit: (body: ObservationRetryInput) => Task
  } = {
    commit(body) {
      const child = first.child(body)
      receipts.set(body.client_request_id, child)
      tasks.push(child)
      first.source.status = 'continued'
      first.source.result.continued_to = child.id
      return child
    },
  }
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('dialog', (dialog) => void dialog.accept())
  await page.context().route('**/*', async (route: Route) => {
    const request = route.request(),
      url = new URL(request.url())
    if (url.origin !== origin) {
      external.push(request.url())
      return route.abort('blockedbyclient')
    }
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4),
      method = request.method()
    if (path === '/tasks' && method === 'GET') return route.fulfill({ json: tasks })
    const match = path.match(/^\/tasks\/([^/]+)(?:\/(.*))?$/)
    if (!match) return route.continue()
    const [, id, action] = match,
      fixture = id === first.source.id ? first : second
    calls.push(method + ' ' + path + url.search)
    if (!action && method === 'GET') return route.fulfill({ json: tasks.find((task) => task.id === id) })
    if (action === 'events')
      return route.fulfill({ contentType: 'text/event-stream', body: 'event: end\ndata: {}\n\n' })
    let reply: Reply | undefined
    if (action === 'author-source-observation-retry-preview' && method === 'GET') {
      reply = await world.onPreview?.(id)
      if (!reply) reply = { json: fixture.preview }
    }
    if (action === 'author-source-observation-retries' && method === 'POST') {
      const body = request.postDataJSON() as ObservationRetryInput
      posts.push(body)
      reply = await world.onPost?.(body)
      if (!reply) reply = { status: 202, json: world.commit(body) }
    }
    if (action === 'author-source-observation-retries' && method === 'GET') {
      const nonce = url.searchParams.get('client_request_id')
      if (nonce === null) throw new Error('Missing original nonce query')
      reply = await world.onLookup?.(nonce)
      if (!reply)
        reply = { json: { source_task_id: id, client_request_id: nonce, task: receipts.get(nonce) ?? null } }
    }
    if (!reply) throw new Error('Unmatched fixture task request: ' + method + ' ' + path)
    if (reply.abort) return route.abort('connectionreset')
    return route.fulfill({ status: reply.status ?? 200, json: reply.json })
  })
  return {
    ...world,
    hooks: world,
    first,
    second,
    tasks,
    originals,
    posts,
    receipts,
    external,
    errors,
    calls,
    novel,
  }
}
async function open(page: Page, world: Awaited<ReturnType<typeof setup>>, id = world.first.source.id) {
  await page.goto('/')
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(world.novel.id)
  await page.getByRole('combobox', { name: '工作版本', exact: true }).selectOption(world.novel.branch_id)
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  await page.getByRole('combobox', { name: '本作品批次', exact: true }).selectOption(id)
  return panel(page)
}
async function panel(page: Page) {
  const summary = page.getByText(SUMMARY, { exact: true })
  await expect(summary).toBeVisible()
  const result = summary.locator('..')
  if ((await result.getAttribute('open')) === null) await summary.click()
  return result
}
async function read(target: Locator) {
  await target.getByRole('button', { name: READ, exact: true }).click()
  await expect(target.getByRole('textbox', { name: INPUT, exact: true })).toBeVisible()
}

test('逐字原话无需补写，明确费用及原自动采纳后只建一观察恢复子批次', async ({ page }) => {
  const world = await setup(page),
    target = await open(page, world)
  await read(target)
  await expect(target.getByRole('textbox')).toHaveCount(1)
  await expect(target.getByRole('checkbox', { name: CONFIRM, exact: true })).not.toBeChecked()
  const submit = target.getByRole('button', { name: SUBMIT, exact: true })
  await expect(submit).toBeDisabled()
  await expect(target).toContainText('0.120000 CNY')
  await target.getByRole('textbox', { name: INPUT, exact: true }).fill('  只核原话来源分类😀𠮷  ')
  await target.getByRole('checkbox', { name: CONFIRM, exact: true }).check()
  await submit.dblclick()
  await expect(page.getByRole('region', { name: RECEIPT, exact: true })).toBeVisible()
  expect(world.posts).toHaveLength(1)
  expect(world.posts[0].reason).toBe('只核原话来源分类😀𠮷')
  expect(Object.keys(world.posts[0]).sort()).toEqual([
    'client_request_id',
    'preview_basis_sha256',
    'reason',
    'version',
  ])
  const child = world.receipts.get(world.posts[0].client_request_id)!
  expect(child.payload).toMatchObject({ account: 'deepseek', adoption: 'automatic', budget_limit: 1.88 })
  expect(child.result.plan).toEqual(world.originals[0].result.plan)
  expect(world.first.preview.current_source.text).toBe(world.first.preview.original_source.text)
  expect(world.external).toEqual([])
  expect(world.errors).toEqual([])
})

test('未知提交刷新后只查原opaque nonce，404/null不重新POST', async ({ page }) => {
  const world = await setup(page)
  world.hooks.onPost = async () => ({ abort: true })
  let target = await open(page, world)
  await read(target)
  await target.getByRole('textbox', { name: INPUT, exact: true }).fill('核对旧观察，原情节保持。')
  await page.evaluate(() =>
    Object.defineProperty(crypto, 'randomUUID', { value: () => 'source-nonce+/字%2F?=same' }),
  )
  await target.getByRole('checkbox', { name: CONFIRM, exact: true }).check()
  await target.getByRole('button', { name: SUBMIT, exact: true }).click()
  await expect(target.getByRole('button', { name: LOOKUP, exact: true })).toBeVisible()
  const request = structuredClone(world.posts[0])
  target = await open(page, world)
  await expect(target.getByRole('button', { name: SUBMIT, exact: true })).toBeDisabled()
  world.hooks.onLookup = async (nonce) => {
    expect(nonce).toBe(request.client_request_id)
    return { status: 404, json: { code: 'missing', message: '尚未查到原标识' } }
  }
  await target.getByRole('button', { name: LOOKUP, exact: true }).click()
  await expect(target.getByRole('alert')).toContainText('尚未查到')
  world.hooks.onLookup = undefined
  await target.getByRole('button', { name: LOOKUP, exact: true }).click()
  await expect(target).toContainText('不代表从未创建')
  world.commit(request)
  await target.getByRole('button', { name: LOOKUP, exact: true }).click()
  await expect(page.getByRole('region', { name: RECEIPT, exact: true })).toBeVisible()
  expect(world.posts).toEqual([request])
  expect(world.errors).toEqual([])
})

test('409保留原因，重读确认新依据后仍须再确认费用', async ({ page }) => {
  const world = await setup(page)
  world.hooks.onPost = async () => ({
    status: 409,
    json: { code: 'task_changed', message: '原来源停点已变化' },
  })
  let target = await open(page, world)
  await read(target)
  await target.getByRole('textbox', { name: INPUT, exact: true }).fill('用新版核对原话分类。')
  await target.getByRole('checkbox', { name: CONFIRM, exact: true }).check()
  await target.getByRole('button', { name: SUBMIT, exact: true }).click()
  await expect(target.getByRole('alert')).toContainText('停点已变化')
  await target.getByRole('textbox', { name: INPUT, exact: true }).fill('原话不变，核对停点😀')
  target = await open(page, world)
  await expect(target.getByRole('textbox', { name: INPUT, exact: true })).toHaveValue('原话不变，核对停点😀')
  await target.getByRole('button', { name: REREAD, exact: true }).click()
  await target.getByRole('button', { name: '确认新依据，保留原因', exact: true }).click()
  await expect(target.getByRole('checkbox', { name: CONFIRM, exact: true })).not.toBeChecked()
  await expect(target.getByRole('button', { name: SUBMIT, exact: true })).toBeDisabled()
  await target.getByRole('checkbox', { name: CONFIRM, exact: true }).check()
  world.hooks.onPost = undefined
  await target.getByRole('button', { name: SUBMIT, exact: true }).click()
  await expect(page.getByRole('region', { name: RECEIPT, exact: true })).toBeVisible()
  expect(world.posts).toHaveLength(2)
  expect(world.posts[0].client_request_id).not.toBe(world.posts[1].client_request_id)
})

test('换批次后晚到预览不能武装新目标', async ({ page }) => {
  const world = await setup(page),
    hold = gate()
  world.hooks.onPreview = async (id) => {
    if (id === world.first.source.id) await hold.promise
    return { json: id === world.first.source.id ? world.first.preview : world.second.preview }
  }
  let target = await open(page, world)
  await target.getByRole('button', { name: READ, exact: true }).click()
  await page.getByRole('combobox', { name: '本作品批次', exact: true }).selectOption(world.second.source.id)
  target = await panel(page)
  hold.release()
  await expect(target.getByRole('textbox', { name: INPUT, exact: true })).toHaveCount(0)
  await read(target)
  expect(world.posts).toHaveLength(0)
})

test('换批次后晚到创建不误开子任务，回原批次只查原标识', async ({ page }) => {
  const world = await setup(page),
    hold = gate()
  world.hooks.onPost = async (body) => {
    const child = world.commit(body)
    await hold.promise
    return { status: 202, json: child }
  }
  let target = await open(page, world)
  await read(target)
  await target.getByRole('textbox', { name: INPUT, exact: true }).fill('仅核原来源，保留全部原话。')
  await target.getByRole('checkbox', { name: CONFIRM, exact: true }).check()
  await target.getByRole('button', { name: SUBMIT, exact: true }).click()
  await expect.poll(() => world.posts.length).toBe(1)
  await page.getByRole('combobox', { name: '本作品批次', exact: true }).selectOption(world.second.source.id)
  target = await panel(page)
  hold.release()
  await expect(page.getByRole('region', { name: RECEIPT, exact: true })).toHaveCount(0)
  await page.getByRole('combobox', { name: '本作品批次', exact: true }).selectOption(world.first.source.id)
  target = await panel(page)
  await target.getByRole('button', { name: LOOKUP, exact: true }).click()
  await expect(page.getByRole('region', { name: RECEIPT, exact: true })).toBeVisible()
  expect(world.posts).toHaveLength(1)
})

test('320真实文字2倍保留只读原话、费用确认、中文组合与键盘路径', async ({ page }, info) => {
  const world = await setup(page),
    target = await open(page, world)
  await read(target)
  const reason = target.getByRole('textbox', { name: INPUT, exact: true }),
    confirm = target.getByRole('checkbox', { name: CONFIRM, exact: true }),
    submit = target.getByRole('button', { name: SUBMIT, exact: true })
  await reason.fill('只核分类，原话保留😀𠮷')
  await confirm.check()
  await reason.dispatchEvent('compositionstart')
  await submit.click()
  expect(world.posts).toHaveLength(0)
  await reason.dispatchEvent('compositionend')
  const contrasts: Record<string, number> = {}
  for (const [name, control] of Object.entries({
    source: target.locator('pre.report').first(),
    reason,
    confirmation: confirm.locator('..'),
    submit,
  })) {
    contrasts[name] = await control.evaluate((element) => {
      let parent: Element | null = element
      while (parent && getComputedStyle(parent).backgroundColor === 'rgba(0, 0, 0, 0)')
        parent = parent.parentElement
      const luminance = (color: string) => {
        const rgb = (color.match(/[\d.]+/g) || [])
          .slice(0, 3)
          .map(Number)
          .map((channel) => channel / 255)
        const linear = rgb.map((channel) =>
          channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
        )
        return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722
      }
      const a = luminance(getComputedStyle(element).color),
        b = luminance(parent ? getComputedStyle(parent).backgroundColor : 'rgb(255,255,255)')
      return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
    })
    expect(contrasts[name]).toBeGreaterThanOrEqual(4.5)
  }
  await info.attach('same-source-text-contrast', {
    contentType: 'application/json',
    body: JSON.stringify(contrasts),
  })
  await page.setViewportSize({ width: 320, height: 950 })
  await assertActualDoubleText(
    page,
    { source: target.locator('pre.report').first(), reason, confirmation: confirm.locator('..'), submit },
    info,
  )
  await reason.focus()
  await page.keyboard.press('Tab')
  await expect(confirm).toBeFocused()
  await confirm.scrollIntoViewIfNeeded()
  await page.screenshot({ path: info.outputPath('same-source-confirm-320-actual2x.png') })
  await page.keyboard.press('Tab')
  await expect(submit).toBeFocused()
  expect(await submit.evaluate((element) => getComputedStyle(element).outlineStyle)).not.toBe('none')
  await submit.scrollIntoViewIfNeeded()
  await page.screenshot({ path: info.outputPath('same-source-submit-320-actual2x.png') })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await page.keyboard.press('Enter')
  await expect(page.getByRole('region', { name: RECEIPT, exact: true })).toBeVisible()
  expect(world.posts).toHaveLength(1)
  expect(world.external).toEqual([])
  expect(world.errors).toEqual([])
})

test('新执行模板或已有观察结果只读，不给重复恢复入口', async ({ page }) => {
  const world = await setup(page)
  Object.assign(world.first.source.result, {
    authored_source_observation_execution_version: 'authored-source-observation-execution/2',
  })
  Object.assign(world.second.record.phases.observe, { response: {} })
  let target = await open(page, world)
  await expect(target.getByRole('button', { name: READ, exact: true })).toBeDisabled()
  await page.getByRole('combobox', { name: '本作品批次', exact: true }).selectOption(world.second.source.id)
  target = await panel(page)
  await expect(target.getByRole('button', { name: READ, exact: true })).toBeDisabled()
  expect(world.posts).toHaveLength(0)
})

test('重读费用依据逐项保留原因但清除确认，不能沿旧确认派发', async ({ page }) => {
  const world = await setup(page),
    target = await open(page, world)
  await read(target)
  await target.getByRole('textbox', { name: INPUT, exact: true }).fill('仅审计引用恢复，保留原计划。')
  await target.getByRole('checkbox', { name: CONFIRM, exact: true }).check()
  const shared = world.first.preview.shared_budget.find((v) => v.provider === 'deepseek')!
  shared.settled += 1
  shared.remaining -= 1
  world.first.preview.request_basis.preview_basis_sha256 = 'e'.repeat(64)
  world.first.preview.baseline.preview_basis_sha256 = 'e'.repeat(64)
  await target.getByRole('button', { name: REREAD, exact: true }).click()
  await expect(target.getByRole('checkbox', { name: CONFIRM, exact: true })).not.toBeChecked()
  await expect(target.getByRole('button', { name: SUBMIT, exact: true })).toBeDisabled()
  await target.getByRole('button', { name: '确认新依据，保留原因', exact: true }).click()
  await expect(target.getByRole('textbox', { name: INPUT, exact: true })).toHaveValue(
    '仅审计引用恢复，保留原计划。',
  )
  await expect(target.getByRole('button', { name: SUBMIT, exact: true })).toBeDisabled()
  await target.getByRole('checkbox', { name: CONFIRM, exact: true }).check()
  await target.getByRole('button', { name: SUBMIT, exact: true }).click()
  await expect(page.getByRole('region', { name: RECEIPT, exact: true })).toBeVisible()
  expect(world.posts).toHaveLength(1)
  expect(world.posts[0].preview_basis_sha256).toBe('e'.repeat(64))
})

test('外窗取消原源后迟到创建回执不改选批次，原nonce仍可只读查询', async ({ page }) => {
  const world = await setup(page),
    hold = gate(),
    target = await open(page, world)
  world.hooks.onPost = async (body) => {
    const child = world.commit(body)
    await hold.promise
    return { status: 202, json: child }
  }
  await read(target)
  await target.getByRole('textbox', { name: INPUT, exact: true }).fill('旧源取消不覆盖，等待原提交查证。')
  await target.getByRole('checkbox', { name: CONFIRM, exact: true }).check()
  await target.getByRole('button', { name: SUBMIT, exact: true }).click()
  await expect.poll(() => world.posts.length).toBe(1)
  world.first.source.status = 'cancelled'
  world.first.source.cancel = 1
  await expect(target).toContainText('原任务停点已变化')
  hold.release()
  await expect(page.getByRole('combobox', { name: '本作品批次', exact: true })).toHaveValue(
    world.first.source.id,
  )
  await expect(page.getByRole('region', { name: RECEIPT, exact: true })).toHaveCount(0)
  await target.getByRole('button', { name: LOOKUP, exact: true }).click()
  await expect(page.getByRole('region', { name: RECEIPT, exact: true })).toBeVisible()
  expect(world.posts).toHaveLength(1)
  expect(world.first.source.cancel).toBe(1)
})

test('后续历史页沿祖先执行见证只读展示，不重新武装恢复', async ({ page }) => {
  const world = await setup(page),
    child = world.first.child(),
    descendant = structuredClone(child)
  descendant.id = 'clock-history-descendant'
  descendant.result.continued_from = child.id
  world.tasks.push(child, descendant)
  const target = await open(page, world, descendant.id)
  await expect(page.getByRole('region', { name: RECEIPT, exact: true })).toBeVisible()
  await expect(target.getByRole('button', { name: READ, exact: true })).toBeDisabled()
  await expect(target.getByRole('textbox')).toHaveCount(0)
  expect(world.posts).toHaveLength(0)
  expect(world.calls.some((x) => x.includes('observation-retry-preview'))).toBe(false)
})

test('正式API三次已返回结算停点，经UI独立新批次到Worker手动待审，旧费/原稿保留', async ({ page }) => {
  const session = (await (await page.request.get('/api/session')).json()) as { csrf: string }
  const seedResponse = await page.request.post('/test/author-source-observation-retry/fixtures', {
    headers: { 'X-Ainovel-CSRF': session.csrf },
    data: { client_request_id: 'api-observe-retry-' + crypto.randomUUID() },
  })
  expect(seedResponse.status()).toBe(201)
  const seed = (await seedResponse.json()) as {
    novel_id: string
    branch_id: string
    source_id: string
    engineering_mock: boolean
  }
  expect(seed.engineering_mock).toBe(true)
  const before = (await (await page.request.get(`/api/tasks/${seed.source_id}`)).json()) as Task
  const chaptersBefore = await (await page.request.get(`/api/branches/${seed.branch_id}/chapters`)).json()
  await page.goto('/')
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(seed.novel_id)
  await page.getByRole('combobox', { name: '工作版本', exact: true }).selectOption(seed.branch_id)
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  await page.getByRole('combobox', { name: '本作品批次', exact: true }).selectOption(seed.source_id)
  const target = await panel(page)
  await read(target)
  await expect(target.getByRole('checkbox', { name: CONFIRM, exact: true })).not.toBeChecked()
  await target.getByRole('textbox', { name: INPUT, exact: true }).fill('UI-RECOVERY-AUDIT-ONLY-8899😀')
  await target.getByRole('checkbox', { name: CONFIRM, exact: true }).check()
  await target.getByRole('button', { name: SUBMIT, exact: true }).click()
  await expect(page.getByRole('region', { name: RECEIPT, exact: true })).toBeVisible()
  const childId = await page.getByRole('combobox', { name: '本作品批次', exact: true }).inputValue()
  expect(childId).not.toBe(seed.source_id)
  await expect
    .poll(async () => ((await (await page.request.get(`/api/tasks/${childId}`)).json()) as Task).status)
    .toBe('needs_review')
  const child = (await (await page.request.get(`/api/tasks/${childId}`)).json()) as Task
  expect(object(child.result)!.authored_source_scope_version).toBe('authored-source-scope/2')
  expect(object(child.result)!.authored_source_observation_execution_version).toBe(
    'authored-source-observation-execution/2',
  )
  expect(object(child.result)!.author_source_observation_retry_version).toBe(
    'author-source-observation-retry/1',
  )
  expect(child.payload.adoption).toBe('manual')
  expect(child.result.plan).toEqual(before.result.plan)
  expect(child.result.waiting_artifact).toBeTruthy()
  const after = (await (await page.request.get(`/api/tasks/${seed.source_id}`)).json()) as Task
  expect(after.status).toBe('continued')
  expect(after.payload).toEqual(before.payload)
  expect(object(after.result)!.authored_source_scope_receipts).toEqual(
    object(before.result)!.authored_source_scope_receipts,
  )
  expect(await (await page.request.get(`/api/branches/${seed.branch_id}/chapters`)).json()).toEqual(
    chaptersBefore,
  )
  const evidence = (await (
    await page.request.get(`/test/author-source-observation-retry/fixtures/${seed.source_id}`)
  ).json()) as {
    source_call_rows_preserved: boolean
    source_calls: number
    source_call_statuses: string[]
    audit_reasons_in_frames: Record<string, boolean>
    actions: Record<string, string[]>
  }
  expect(evidence.source_call_rows_preserved).toBe(true)
  expect(evidence.source_calls).toBe(3)
  expect(evidence.source_call_statuses).toEqual(['settled', 'settled', 'settled'])
  expect(evidence.audit_reasons_in_frames[childId]).toBe(false)
  expect(evidence.actions[childId]).toContain('按章纲写完整正文')
  expect(evidence.actions[childId]).toContain('独立核对作者来源分类与完整原意保持')
})
