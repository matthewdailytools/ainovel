import { test, expect, type Page } from '@playwright/test'

const text =
  '😀宁禾领到铜牌，身上有两枚铜钱。\n她带铜牌抵达渡口。\n抵达后她付掉一枚铜钱。\n更晚她听到暗号。\n与此同时，北岸掌柜检修灯罩。'

async function fixture(page: Page) {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await (
    await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 相对事件时点' } })
  ).json()
  const other = await (
    await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 另一作品查询' } })
  ).json()
  const chapter = await (
    await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
      headers,
      data: { title: '渡口先后' },
    })
  ).json()
  expect(
    (
      await page.request.put(`/api/chapters/${chapter.id}`, {
        headers,
        data: { base_revision: 0, content: text },
      })
    ).ok(),
  ).toBeTruthy()
  let revision = (await (await page.request.get(`/api/branches/${novel.branch_id}/story-graph`)).json())
    .story_revision
  const recorded = new Set<string>()
  async function save(record: Record<string, unknown>) {
    const response = await page.request.post(`/api/branches/${novel.branch_id}/story-records`, {
      headers,
      data: { base_story_revision: revision, record },
    })
    expect(response.ok(), await response.text()).toBeTruthy()
    const graph = await response.json()
    revision = graph.story_revision
    const row = graph.records.find((row: { id: string }) => !recorded.has(row.id))
    recorded.add(row.id)
    return row.id as string
  }
  const lines = text.split('\n')
  const source = (i: number) => ({
    chapter_id: chapter.id,
    revision: 1,
    start: text.indexOf(lines[i]),
    end: text.indexOf(lines[i]) + lines[i].length,
    quote: lines[i],
  })
  const person = await save({ kind: 'character', title: '宁禾', sources: [source(0)] })
  const event = async (title: string, i: number, after: string[] = []) =>
    save({
      kind: 'event',
      title,
      status: 'actual',
      story_order: null,
      sources: [source(i)],
      participants: [person],
      after,
    })
  const first = await event('领取铜牌', 0)
  const arrived = await event('抵达渡口', 1, [first])
  const paid = await event('付掉一枚铜钱', 2, [arrived])
  await event('听到暗号', 3, [paid])
  await event('北岸检灯', 4)
  const state = async (
    title: string,
    value: string,
    eventId: string,
    i: number,
    until: string | null = null,
  ) =>
    save({
      kind: 'state',
      title,
      character_id: person,
      event_id: eventId,
      category: 'resource',
      key: '铜钱',
      value,
      truth: 'actual',
      audience: 'character',
      until_event_id: until,
      sources: [source(i)],
    })
  await state('铜钱两枚', '两枚', first, 0, paid)
  await state('铜钱一枚', '一枚', paid, 2)
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  const workspace = page.getByRole('region', { name: '长篇人物时点与多线工作台', exact: true })
  await workspace.getByText('查询人物在故事时点知道什么、具有什么', { exact: true }).click()
  await workspace.getByRole('combobox', { name: '查询人物', exact: true }).selectOption(person)
  await workspace.getByRole('combobox', { name: '查询时间依据', exact: true }).selectOption('event')
  const choose = workspace.getByRole('combobox', { name: '查询实际事件', exact: true })
  return { novel, other, chapter, person, first, arrived, paid, workspace, choose, revision }
}

test('无整数编号的事件先后：查询、失效、来源路径、信息视角及原文边界', async ({ page }, info) => {
  const f = await fixture(page)
  const query = f.workspace.getByRole('button', { name: '查询有效状态', exact: true })
  const result = f.workspace.getByRole('region', { name: '人物时点查询结果', exact: true })
  const before = await (await page.request.get(`/api/branches/${f.novel.branch_id}/story-graph`)).json()
  await f.choose.selectOption(f.arrived)
  const sent = page.waitForRequest((request) => request.url().includes('/story-context?'))
  await query.click()
  const url = new URL((await sent).url())
  expect(url.searchParams.get('at_event_id')).toBe(f.arrived)
  expect(url.searchParams.has('story_order')).toBe(false)
  expect(url.searchParams.get('base_story_revision')).toBe(String(f.revision))
  await expect(result.getByRole('button', { name: '铜钱两枚 · 两枚', exact: true })).toBeVisible()
  await result.getByRole('button', { name: '领取铜牌', exact: true }).click()
  await expect(f.workspace.getByRole('textbox', { name: '记录名称', exact: true })).toHaveValue('领取铜牌')
  await f.choose.selectOption(f.paid)
  await expect(result).not.toBeVisible()
  await query.click()
  await expect(result.getByRole('button', { name: '铜钱一枚 · 一枚', exact: true })).toBeVisible()
  await expect(result.getByRole('button', { name: '铜钱两枚 · 两枚', exact: true })).toHaveCount(0)
  await f.workspace.getByRole('combobox', { name: '查询信息视角', exact: true }).selectOption('reader')
  await query.click()
  await expect(result.getByText(/有效状态 0；冲突组 0/)).toBeVisible()
  await f.workspace.getByRole('combobox', { name: '查询信息视角', exact: true }).selectOption('character')
  await f.workspace.getByRole('combobox', { name: '查询原文边界', exact: true }).selectOption(f.chapter.id)
  await f.choose.selectOption(f.first)
  await query.click()
  await expect(f.workspace.getByRole('combobox', { name: '查询实际事件', exact: true })).toHaveValue(f.first)
  await expect(f.workspace.getByRole('alert')).toContainText('不是当前位置可见')
  await f.workspace.getByRole('button', { name: '读至本章末尾', exact: true }).click()
  await f.workspace.getByText('精确选择章内原文（可选）', { exact: true }).click()
  await expect(
    f.workspace.getByRole('spinbutton', { name: '原文偏移（UTF-16，供核对）', exact: true }),
  ).toHaveValue(String(text.length))
  await query.click()
  await expect(result.getByRole('button', { name: '铜钱两枚 · 两枚', exact: true })).toBeVisible()
  const reader = f.workspace.getByRole('textbox', { name: '查询边界原文', exact: true })
  await reader.focus()
  await reader.scrollIntoViewIfNeeded()
  const drag = await reader.evaluate((element: HTMLTextAreaElement) => {
    element.scrollTop = 0
    const style = getComputedStyle(element)
    const box = element.getBoundingClientRect()
    const canvas = document.createElement('canvas')
    const context = canvas.getContext('2d')!
    context.font = style.font
    const x = box.left + parseFloat(style.borderLeftWidth) + parseFloat(style.paddingLeft)
    const y =
      box.top +
      parseFloat(style.borderTopWidth) +
      parseFloat(style.paddingTop) +
      parseFloat(style.lineHeight) / 2
    return { x, y, end: x + context.measureText(element.value.split('\n')[0]).width + 2 }
  })
  await page.mouse.move(drag.x + 1, drag.y)
  await page.mouse.down()
  await page.mouse.move(drag.end, drag.y, { steps: 6 })
  await page.mouse.up()
  await info.attach('boundary-selection-before-commit', {
    contentType: 'application/json',
    body: JSON.stringify(
      await reader.evaluate((element: HTMLTextAreaElement) => ({
        start: element.selectionStart,
        end: element.selectionEnd,
        focused: document.activeElement === element,
      })),
    ),
  })
  await expect
    .poll(() => reader.evaluate((element: HTMLTextAreaElement) => element.selectionEnd))
    .toBe(text.split('\n')[0].length)
  await f.workspace.getByRole('button', { name: '读至选区末尾', exact: true }).click()
  await expect(
    f.workspace.getByRole('spinbutton', { name: '原文偏移（UTF-16，供核对）', exact: true }),
  ).toHaveValue(String(text.split('\n')[0].length))
  await query.click()
  await expect(result.getByText('失效事件与查询事件先后不明，无法确认此时仍有效。')).toBeVisible()
  await f.workspace.getByRole('button', { name: '读至本章末尾', exact: true }).click()
  await query.click()
  await expect(result.getByRole('button', { name: '铜钱两枚 · 两枚', exact: true })).toBeVisible()
  await page.setViewportSize({ width: 320, height: 900 })
  await page.evaluate(() => {
    const sizes = [...document.querySelectorAll('*')].map(
      (element) => [element, parseFloat(getComputedStyle(element).fontSize)] as const,
    )
    for (const [element, size] of sizes)
      if (element instanceof HTMLElement) element.style.fontSize = `${size * 2}px`
  })
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)
  await f.choose.focus()
  await expect(f.choose).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(f.workspace.getByRole('combobox', { name: '查询信息视角', exact: true })).toBeFocused()
  const metrics = await page.evaluate(() => ({
    bodyFont: getComputedStyle(document.body).fontSize,
    width: document.documentElement.scrollWidth,
  }))
  await info.attach('relative-query-actual-text', {
    contentType: 'application/json',
    body: JSON.stringify(metrics),
  })
  expect(await (await page.request.get(`/api/branches/${f.novel.branch_id}/story-graph`)).json()).toEqual(
    before,
  )
  expect((await (await page.request.get(`/api/chapters/${f.chapter.id}`)).json()).content).toBe(text)
})

test('切换事件后旧查询晚到：不覆盖新结果，也不在切版后展示旧错误', async ({ page }) => {
  const f = await fixture(page)
  let release: () => void = () => {}
  let started: () => void = () => {}
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  const ready = new Promise<void>((resolve) => {
    started = resolve
  })
  await page.route(`**/api/branches/${f.novel.branch_id}/story-context?*`, async (route) => {
    if (new URL(route.request().url()).searchParams.get('at_event_id') !== f.arrived) return route.continue()
    const response = await route.fetch()
    started()
    await gate
    await route.fulfill({ response })
  })
  await f.choose.selectOption(f.arrived)
  await f.workspace.getByRole('button', { name: '查询有效状态', exact: true }).click()
  await ready
  await f.choose.selectOption(f.paid)
  await f.workspace.getByRole('button', { name: '查询有效状态', exact: true }).click()
  const result = f.workspace.getByRole('region', { name: '人物时点查询结果', exact: true })
  await expect(result.getByRole('button', { name: '铜钱一枚 · 一枚', exact: true })).toBeVisible()
  const late = page.waitForResponse(
    (response) => new URL(response.url()).searchParams.get('at_event_id') === f.arrived,
  )
  release()
  await (await late).finished()
  await page.evaluate(
    () => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))),
  )
  await expect(result.getByRole('button', { name: '铜钱一枚 · 一枚', exact: true })).toBeVisible()
  await expect(result.getByRole('button', { name: '铜钱两枚 · 两枚', exact: true })).toHaveCount(0)
  await page.unroute(`**/api/branches/${f.novel.branch_id}/story-context?*`)

  let errorReady: () => void = () => {}
  let errorRelease: () => void = () => {}
  const errorStarted = new Promise<void>((resolve) => {
    errorReady = resolve
  })
  const errorGate = new Promise<void>((resolve) => {
    errorRelease = resolve
  })
  await page.route(`**/api/branches/${f.novel.branch_id}/story-context?*`, async (route) => {
    errorReady()
    await errorGate
    await route.fulfill({
      status: 409,
      contentType: 'application/json',
      body: JSON.stringify({ code: 'story_changed', message: '过期查询错误不应串到其他作品' }),
    })
  })
  await f.choose.selectOption(f.arrived)
  await f.workspace.getByRole('button', { name: '查询有效状态', exact: true }).click()
  await errorStarted
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(f.other.id)
  await expect(page.getByRole('combobox', { name: '工作版本', exact: true })).toHaveValue(f.other.branch_id)
  const oldError = page.waitForResponse((response) =>
    response.url().includes(`/branches/${f.novel.branch_id}/story-context?`),
  )
  errorRelease()
  await (await oldError).finished()
  await page.evaluate(
    () => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))),
  )
  await expect(page.getByText('过期查询错误不应串到其他作品', { exact: true })).toHaveCount(0)
})
