import { test, expect } from '@playwright/test'

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
    await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
    const nav = page.getByRole('button', { name: view, exact: true })
    await nav.click()
    await expect(nav).toHaveAttribute('aria-current', 'page')
    if (view === '写作')
      await expect(page.getByRole('textbox', { name: '章节正文', exact: true })).toBeVisible()
    else await expect(page.getByRole('heading', { name: view, exact: true })).toBeVisible()
    if (view === '资料书库')
      await expect(page.locator('.reader-layout button.wide').filter({ hasText: name })).toBeVisible()
    const previous = await nav.evaluate((element) => parseFloat(getComputedStyle(element).fontSize))
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
    expect(await nav.evaluate((element) => parseFloat(getComputedStyle(element).fontSize))).toBe(previous * 2)
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
