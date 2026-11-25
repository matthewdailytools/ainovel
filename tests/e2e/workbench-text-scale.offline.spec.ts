import { test, expect } from '@playwright/test'
import { openWritingPanel } from './helpers/writingPanels'

test('窄屏新建作品：普通和实际翻倍文字下表单均可见，键盘提交不改旧作品', async ({ page }, info) => {
  await page.setViewportSize({ width: 320, height: 900 })
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const seed = await (
    await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 先前已有作品' } })
  ).json()
  const original = await (
    await page.request.post(`/api/branches/${seed.branch_id}/chapters`, {
      headers,
      data: { title: '已有原稿' },
    })
  ).json()
  const kept = await (
    await page.request.put(`/api/chapters/${original.id}`, {
      headers,
      data: { base_revision: 0, content: '😀原稿和修订不因新建另一作品而改变。' },
    })
  ).json()
  await page.reload()
  await expect(page.getByRole('button', { name: '目录', exact: true })).toBeVisible()
  await openWritingPanel(page, '目录')
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(seed.id)
  let submissions = 0
  await page.route('**/api/novels', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    submissions++
    await new Promise((resolve) => setTimeout(resolve, 500))
    await route.continue()
  })
  const before = await (await page.request.get('/api/novels')).json()
  const budget = (await (await page.request.get('/api/config')).json()).budget
  await openWritingPanel(page, '目录')
  await page.locator('.new-novel summary').click()
  const form = page.locator('.new-novel form')
  const title = page.getByRole('textbox', { name: '作品名称', exact: true })
  await expect(title).toBeVisible()
  const measurements = []
  for (const scale of [1, 2]) {
    if (scale === 2)
      await page.evaluate(() => {
        const sizes = [...document.querySelectorAll('*')].map(
          (element) => [element, parseFloat(getComputedStyle(element).fontSize)] as const,
        )
        for (const [element, size] of sizes)
          if (element instanceof HTMLElement) element.style.fontSize = `${size * 2}px`
      })
    const bounds = await form.boundingBox()
    expect(bounds).not.toBeNull()
    measurements.push({ scale, ...bounds })
    expect(bounds!.x).toBeGreaterThanOrEqual(0)
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(320)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)
  }
  const name = '后台验证 · 键盘创建😀𠮷'
  await title.focus()
  await title.fill(name)
  const receipt = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === '/api/novels' && response.request().method() === 'POST',
  )
  await page.keyboard.press('Enter')
  await page.keyboard.press('Enter')
  const response = await receipt
  expect(response.status()).toBe(201)
  const returned = await response.json()
  const novels = await (await page.request.get('/api/novels')).json()
  expect(novels).toHaveLength(before.length + 1)
  for (const old of before) expect(novels).toContainEqual(old)
  const created = novels.find((novel: { title: string }) => novel.title === name)
  expect(created).toBeDefined()
  expect(created.id).toBe(returned.id)
  await expect(page.getByRole('button', { name: '新建第一章', exact: true })).toBeVisible()
  await openWritingPanel(page, '目录')
  await expect(page.getByRole('combobox', { name: '作品', exact: true })).toHaveValue(created.id)
  expect(submissions).toBe(1)
  expect(await (await page.request.get(`/api/chapters/${original.id}`)).json()).toEqual(kept)
  expect((await (await page.request.get('/api/config')).json()).budget).toEqual(budget)
  await info.attach('new-novel-form-bounds', {
    contentType: 'application/json',
    body: JSON.stringify({ measurements, modelCalls: 0 }),
  })
})

test('五个工作区实际文字翻倍：长参考来源仍可阅读并用键盘打开', async ({ page }, info) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const before = await (await page.request.get('/api/config')).json()
  const novelResponse = await page.request.post('/api/novels', {
    headers,
    data: { title: '后台验证 · 实际文字放大' },
  })
  expect(novelResponse.ok()).toBeTruthy()
  const novel = await novelResponse.json()
  const chapterResponse = await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
    headers,
    data: { title: '手写与参考学习' },
  })
  expect(chapterResponse.ok()).toBeTruthy()
  const source = '😀渡口来客收好半幅图，决定去问摆渡人。'
  const name = `参考来源-${'chapter'.repeat(14)}.txt`
  const imported = await page.request.post('/api/library/import', {
    headers,
    multipart: {
      file: { name, mimeType: 'text/plain', buffer: Buffer.from(source) },
    },
  })
  expect(imported.ok()).toBeTruthy()
  const book = await imported.json()
  const units = await (await page.request.get(`/api/library/books/${book.id}/units`)).json()
  const created = await page.request.post('/api/reference-sessions', {
    headers,
    data: {
      unit_id: units[0].id,
      start: 0,
      end: source.length,
      quote: source,
      goal: '有限信息下取得局部进展',
      branch_id: novel.branch_id,
    },
  })
  expect(created.ok()).toBeTruthy()
  const measurements = []
  for (const view of ['写作', '小说构思', '整书创作', '故事与审阅', '资料书库']) {
    // Reload before each view so inherited sizes and newly mounted elements are
    // measured once, without scaling a previous view a second time.
    await page.reload()
    await page.setViewportSize({ width: 320, height: 900 })
    await expect(page.getByRole('button', { name: '目录', exact: true })).toBeVisible()
    await openWritingPanel(page, '目录')
    await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
    const closeDirectory = page.getByRole('button', { name: '关闭目录', exact: true })
    if (await closeDirectory.isVisible()) await closeDirectory.click()
    const narrowMenu = page.locator('.workbench-navigation-toggle')
    await narrowMenu.click()
    const nav = page
      .getByRole('navigation', { name: '主要页面' })
      .getByRole('button', { name: view, exact: true })
    await nav.click()
    await expect(
      page
        .getByRole('navigation', { name: '主要页面', includeHidden: true })
        .getByRole('button', { name: view, exact: true, includeHidden: true }),
    ).toHaveAttribute('aria-current', 'page')
    if (view === '写作')
      await expect(page.getByRole('textbox', { name: '章节正文', exact: true })).toBeVisible()
    else await expect(page.getByRole('heading', { name: view, exact: true })).toBeVisible()
    if (view === '资料书库') {
      await page.getByText(/^参考学习历史/).click()
      await expect(page.locator('.reader-layout button.wide').filter({ hasText: name })).toBeVisible()
    }
    const control = narrowMenu
    const previous = await control.evaluate((element) => parseFloat(getComputedStyle(element).fontSize))
    await page.evaluate(() => {
      const sizes = [...document.querySelectorAll('*')].map(
        (element) => [element, parseFloat(getComputedStyle(element).fontSize)] as const,
      )
      for (const [element, size] of sizes) {
        if (element instanceof HTMLElement) element.style.fontSize = `${size * 2}px`
      }
    })
    const measured = await page.evaluate(() => ({
      bodyFont: parseFloat(getComputedStyle(document.body).fontSize),
      document: document.documentElement.scrollWidth,
      viewport: innerWidth,
    }))
    measurements.push({ view, ...measured })
    expect(await control.evaluate((element) => parseFloat(getComputedStyle(element).fontSize))).toBe(
      previous * 2,
    )
    expect(measured.document, `${view} should keep the page within the viewport`).toBeLessThanOrEqual(
      measured.viewport,
    )
  }
  const history = page.locator('.reader-layout button.wide').filter({ hasText: name })
  const bounds = await history.boundingBox()
  expect(bounds).not.toBeNull()
  expect(bounds!.x).toBeGreaterThanOrEqual(0)
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(320)
  await history.focus()
  await expect(history).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: '参考仿写工作区', exact: true })).toBeVisible()
  const after = await (await page.request.get('/api/config')).json()
  expect(after.budget).toEqual(before.budget)
  const tasks: { novel_id: string }[] = await (await page.request.get('/api/tasks')).json()
  expect(tasks.filter((task) => task.novel_id === novel.id)).toEqual([])
  await info.attach('workbench-actual-text-scale', {
    contentType: 'application/json',
    body: JSON.stringify({ measurements, modelCalls: 0 }),
  })
})
