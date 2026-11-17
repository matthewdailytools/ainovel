import { expect, test, type Locator, type Page, type Route } from '@playwright/test'
import { ADDITION, clarificationFixture } from '../../fixtures/author-source-clarification'
import { clarificationFormatFailureFixture } from '../../fixtures/author-source-clarification-format-failure'
import type { Task } from '../../frontend/api'
import type { ClarificationInput } from '../../frontend/authorSourceClarification'
import { object } from '../../frontend/planRestart'
import { assertActualDoubleText } from './helpers/actualTextScale'

const SUMMARY = '补充作者情节来源，保留原话与费用',
  READ = '读取来源补充依据',
  REREAD = '重新读取来源补充依据',
  SUBMIT = '创建并开始来源澄清子任务',
  INPUT = '本章补充说明（作者）',
  LOOKUP = '查询原来源补充回执'
function gate() {
  let release = () => {}
  const promise = new Promise<void>((done) => {
    release = done
  })
  return { promise, release }
}
type Reply = { status?: number; json?: unknown; abort?: boolean }
async function setup(page: Page, formatFailure = false) {
  const sessionResponse = await page.request.get('/api/session'),
    origin = new URL(sessionResponse.url()).origin
  const session = (await sessionResponse.json()) as { csrf: string }
  const response = await page.request.post('/api/novels', {
    headers: { 'X-Ainovel-CSRF': session.csrf },
    data: { title: '工程原创来源补充测试', brief: '独立确定性路由，不代表文学质量。' },
  })
  expect(response.status()).toBe(201)
  const novel = (await response.json()) as { id: string; branch_id: string }
  const first = (formatFailure ? clarificationFormatFailureFixture : clarificationFixture)(
      'clarify-parent',
      novel.branch_id,
    ),
    second = clarificationFixture('clarify-other', novel.branch_id)
  for (const fixture of [first, second]) {
    fixture.source.novel_id = novel.id
    fixture.preview.novel_id = novel.id
  }
  const tasks = [first.source, second.source],
    originals = structuredClone(tasks),
    posts: ClarificationInput[] = [],
    receipts = new Map<string, Task>(),
    external: string[] = [],
    errors: string[] = [],
    calls: string[] = []
  const world: {
    onPreview?: (id: string) => Promise<Reply>
    onPost?: (body: ClarificationInput) => Promise<Reply>
    onLookup?: (nonce: string) => Promise<Reply>
    commit: (body: ClarificationInput) => Task
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
    if (action === 'author-source-clarification-preview' && method === 'GET') {
      reply = await world.onPreview?.(id)
      if (!reply) reply = { json: fixture.preview }
    }
    if (action === 'author-source-clarifications' && method === 'POST') {
      const body = request.postDataJSON() as ClarificationInput
      posts.push(body)
      reply = await world.onPost?.(body)
      if (!reply) reply = { status: 202, json: world.commit(body) }
    }
    if (action === 'author-source-clarifications' && method === 'GET') {
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

test('明确作者补充、IME与重复触发：只建一子任务，原情节与轮次保留', async ({ page }) => {
  const world = await setup(page),
    target = await open(page, world)
  await read(target)
  await expect(target.getByText(/原任务已付：0.120000 CNY/)).toBeVisible()
  await expect(target.getByText(/逐章由作者确认采纳/)).toBeVisible()
  const input = target.getByRole('textbox', { name: INPUT, exact: true })
  await input.fill(ADDITION)
  await input.dispatchEvent('compositionstart')
  await target.getByRole('button', { name: SUBMIT, exact: true }).click()
  expect(world.posts).toHaveLength(0)
  await input.dispatchEvent('compositionend')
  await target.getByRole('button', { name: SUBMIT, exact: true }).dblclick()
  await expect(page.getByRole('region', { name: '作者来源补充回执', exact: true })).toBeVisible()
  expect(world.posts).toHaveLength(1)
  expect(world.posts[0].actor).toBe('author')
  expect(Object.keys(world.posts[0]).sort()).toEqual([
    'actor',
    'clarification',
    'client_request_id',
    'preview_basis_sha256',
    'reason',
    'version',
  ])
  const child = world.receipts.get(world.posts[0].client_request_id)!
  expect(child.payload).toMatchObject({ account: 'deepseek', adoption: 'manual', budget_limit: 1.88 })
  expect(child.result.repair_round).toBe(1)
  expect(child.result.plan).toEqual(world.originals[0].result.plan)
  expect({
    ...world.first.source,
    status: world.originals[0].status,
    result: { ...world.first.source.result, continued_to: undefined },
  }).toMatchObject(world.originals[0])
  expect(world.external).toEqual([])
  expect(world.errors).toEqual([])
})

test('三份完整格式失败经现有表单显式补充，重复触发及刷新只保留一个原nonce', async ({ page }) => {
  const world = await setup(page, true)
  world.hooks.onPost = async () => ({ abort: true })
  let target = await open(page, world)
  await read(target)
  await expect(
    target.getByText(
      '三份原始返回已完整保存，但格式核对失败。补充说明会与原话分开保存，不会修改旧返回或将其记为通过。',
      { exact: true },
    ),
  ).toBeVisible()
  const input = target.getByRole('textbox', { name: INPUT, exact: true }),
    submit = target.getByRole('button', { name: SUBMIT, exact: true })
  await expect(submit).toBeDisabled()
  await input.fill(' \n ')
  await expect(submit).toBeDisabled()
  expect(world.posts).toHaveLength(0)
  await input.fill(ADDITION)
  await submit.dblclick()
  await expect(target.getByRole('button', { name: LOOKUP, exact: true })).toBeVisible()
  expect(world.posts).toHaveLength(1)
  const originalRequest = structuredClone(world.posts[0])
  expect(originalRequest.actor).toBe('author')
  expect(originalRequest.clarification).toBe(ADDITION)
  expect(object(world.first.source.result)!.authored_source_scope_receipts).toEqual(
    object(world.originals[0].result)!.authored_source_scope_receipts,
  )
  expect(world.first.record.phases.observe.status).toBe('started')
  expect(world.first.record.phases.observe).not.toHaveProperty('response')
  expect(world.first.record.phases.observe).not.toHaveProperty('response_sha256')

  target = await open(page, world)
  await expect(target.getByRole('textbox', { name: INPUT, exact: true })).toHaveValue(ADDITION)
  await expect(target.getByRole('button', { name: SUBMIT, exact: true })).toBeDisabled()
  world.commit(originalRequest)
  await target.getByRole('button', { name: LOOKUP, exact: true }).click()
  await expect(page.getByRole('region', { name: '作者来源补充回执', exact: true })).toBeVisible()
  expect(world.posts).toEqual([originalRequest])
  const queries = world.calls.filter(
    (call) => call.startsWith('GET ') && call.includes('/author-source-clarifications?'),
  )
  expect(queries).toHaveLength(1)
  expect(new URL('http://offline.invalid' + queries[0].slice(4)).searchParams.get('client_request_id')).toBe(
    originalRequest.client_request_id,
  )
  expect(object(world.first.source.result)!.authored_source_scope_receipts).toEqual(
    object(world.originals[0].result)!.authored_source_scope_receipts,
  )
  expect(world.first.record.phases.observe.status).toBe('started')
  expect(world.first.record.phases.observe).not.toHaveProperty('response')
  expect(world.external).toEqual([])
  expect(world.errors).toEqual([])
})

test('未知提交刷新后只查原nonce；404与null不会派发另一POST', async ({ page }) => {
  const world = await setup(page)
  world.hooks.onPost = async () => ({ abort: true })
  let target = await open(page, world)
  await read(target)
  await target.getByRole('textbox', { name: INPUT, exact: true }).fill(ADDITION)
  await target.getByRole('button', { name: SUBMIT, exact: true }).click()
  await expect(target.getByRole('button', { name: LOOKUP, exact: true })).toBeVisible()
  const request = structuredClone(world.posts[0])
  target = await open(page, world)
  await expect(target.getByRole('textbox', { name: INPUT, exact: true })).toHaveValue(ADDITION)
  await expect(target.getByRole('button', { name: SUBMIT, exact: true })).toBeDisabled()
  world.hooks.onLookup = async () => ({ status: 404, json: { code: 'missing', message: '此刻未查到' } })
  await target.getByRole('button', { name: LOOKUP, exact: true }).click()
  await expect(target.getByRole('alert')).toContainText('此刻未查到')
  world.hooks.onLookup = undefined
  await target.getByRole('button', { name: LOOKUP, exact: true }).click()
  await expect(target.getByRole('status').last()).toContainText('只查询原回执')
  world.commit(request)
  await target.getByRole('button', { name: LOOKUP, exact: true }).click()
  await expect(page.getByRole('region', { name: '作者来源补充回执', exact: true })).toBeVisible()
  expect(world.posts).toEqual([request])
  expect(
    world.calls.filter((call) => call.includes('/author-source-clarifications?') && call.startsWith('GET')),
  ).toHaveLength(3)
  expect(world.errors).toEqual([])
})

test('明确409后刷新仍保留已编辑补充，显式确认新依据后才生成新nonce', async ({ page }) => {
  const world = await setup(page)
  world.hooks.onPost = async () => ({
    status: 409,
    json: { code: 'task_changed', message: '来源已变化，请重新核对' },
  })
  let target = await open(page, world)
  await read(target)
  await target.getByRole('textbox', { name: INPUT, exact: true }).fill(ADDITION)
  await target.getByRole('button', { name: SUBMIT, exact: true }).click()
  await expect(target.getByRole('alert')).toContainText('来源已变化')
  const edited = ADDITION + '补充记录😀𠮷仍保留。'
  await target.getByRole('textbox', { name: INPUT, exact: true }).fill(edited)
  target = await open(page, world)
  await expect(target.getByRole('textbox', { name: INPUT, exact: true })).toHaveValue(edited)
  await expect(target.getByRole('button', { name: SUBMIT, exact: true })).toBeDisabled()
  await target.getByRole('button', { name: REREAD, exact: true }).click()
  await expect(target.getByRole('button', { name: SUBMIT, exact: true })).toBeDisabled()
  await target.getByRole('button', { name: '确认使用新依据，保留补充', exact: true }).click()
  world.hooks.onPost = undefined
  await target.getByRole('button', { name: SUBMIT, exact: true }).click()
  await expect(page.getByRole('region', { name: '作者来源补充回执', exact: true })).toBeVisible()
  expect(world.posts).toHaveLength(2)
  expect(world.posts[0].client_request_id).not.toBe(world.posts[1].client_request_id)
  expect(world.posts[1].clarification).toBe(edited)
})

test('切换批次后晚到预览不进入新目标', async ({ page }) => {
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
  await expect(page.getByRole('combobox', { name: '本作品批次', exact: true })).toHaveValue(
    world.second.source.id,
  )
})

test('切换后晚到POST不误开子任务，回原目标仍只查原nonce', async ({ page }) => {
  const world = await setup(page),
    hold = gate()
  world.hooks.onPost = async (body) => {
    const child = world.commit(body)
    await hold.promise
    return { status: 202, json: child }
  }
  let target = await open(page, world)
  await read(target)
  await target.getByRole('textbox', { name: INPUT, exact: true }).fill(ADDITION)
  await target.getByRole('button', { name: SUBMIT, exact: true }).click()
  await expect.poll(() => world.posts.length).toBe(1)
  await page.getByRole('combobox', { name: '本作品批次', exact: true }).selectOption(world.second.source.id)
  target = await panel(page)
  hold.release()
  await expect(page.getByRole('region', { name: '作者来源补充回执', exact: true })).toHaveCount(0)
  await page.getByRole('combobox', { name: '本作品批次', exact: true }).selectOption(world.first.source.id)
  target = await panel(page)
  await expect(target.getByRole('button', { name: LOOKUP, exact: true })).toBeVisible()
  await target.getByRole('button', { name: LOOKUP, exact: true }).click()
  await expect(page.getByRole('region', { name: '作者来源补充回执', exact: true })).toBeVisible()
  expect(world.posts).toHaveLength(1)
  expect(world.errors).toEqual([])
})

test('320宽实际双倍文字、键盘焦点和中文草稿可操作', async ({ page }, info) => {
  await page.setViewportSize({ width: 320, height: 950 })
  const world = await setup(page),
    target = await open(page, world)
  await read(target)
  const input = target.getByRole('textbox', { name: INPUT, exact: true }),
    submit = target.getByRole('button', { name: SUBMIT, exact: true })
  await input.fill(ADDITION + '😀𠮷')
  const contrast: Record<string, unknown> = {}
  for (const [name, control] of Object.entries({
    input,
    submit,
    source: target.locator('pre.report').first(),
  })) {
    const measurement = await control.evaluate((element) => {
      const rgb = (value: string) => (value.match(/[\d.]+/g) || []).map(Number)
      const background = (node: Element | null): number[] => {
        if (!node) return [255, 255, 255]
        const value = rgb(getComputedStyle(node).backgroundColor)
        return value.length === 3 || value[3] === 1 ? value : background(node.parentElement)
      }
      const luminance = (value: number[]) =>
        value
          .slice(0, 3)
          .map((channel) => channel / 255)
          .map((channel) => (channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4))
          .reduce((sum, channel, index) => sum + channel * [0.2126, 0.7152, 0.0722][index], 0)
      const foreground = getComputedStyle(element).color,
        back = background(element),
        a = luminance(rgb(foreground)),
        b = luminance(back),
        rectangle = element.getBoundingClientRect()
      return {
        foreground,
        background: back,
        ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05),
        width: rectangle.width,
        height: rectangle.height,
      }
    })
    expect(measurement.ratio).toBeGreaterThanOrEqual(4.5)
    if (name !== 'source') {
      expect(measurement.width).toBeGreaterThanOrEqual(24)
      expect(measurement.height).toBeGreaterThanOrEqual(24)
    }
    contrast[name] = measurement
  }
  await info.attach('author-clarification-text-contrast-target-size', {
    contentType: 'application/json',
    body: JSON.stringify(contrast),
  })
  await assertActualDoubleText(
    page,
    {
      summary: page.getByText(SUMMARY, { exact: true }),
      input,
      submit,
      source: target.locator('pre.report').first(),
    },
    info,
  )
  await page.screenshot({
    path: info.outputPath('author-clarification-320-actual-double-text-form.png'),
    fullPage: true,
  })
  await input.focus()
  await expect(input).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(
    target.getByRole('textbox', { name: '本次补充原因（可空，仅留作记录）', exact: true }),
  ).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(submit).toBeFocused()
  expect(await submit.evaluate((element) => getComputedStyle(element).outlineStyle)).not.toBe('none')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('region', { name: '作者来源补充回执', exact: true })).toBeVisible()
  expect(world.posts).toHaveLength(1)
  expect(world.external).toEqual([])
  expect(world.errors).toEqual([])
  await page.screenshot({
    path: info.outputPath('author-clarification-320-child-after-submit.png'),
    fullPage: true,
  })
})
