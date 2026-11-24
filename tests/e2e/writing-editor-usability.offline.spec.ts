import { expect, test } from '@playwright/test'

test('首章从可见正文框开始写作，字数随输入更新，自动保存保持位置', async ({ page }) => {
  await page.setViewportSize({ width: 1100, height: 760 })
  await page.goto('/')
  await page.locator('.new-novel summary').click()
  await page.getByRole('textbox', { name: '作品名称', exact: true }).fill('后台验证 · 首章直接写作')
  await page.getByRole('button', { name: '创建并开始写作', exact: true }).click()

  const creation = page.waitForResponse(
    (response) =>
      /^\/api\/branches\/[^/]+\/chapters$/.test(new URL(response.url()).pathname) &&
      response.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '新建第一章', exact: true }).click()
  const created = await creation
  expect(created.ok()).toBeTruthy()
  const chapter = await created.json()
  const savePath = `/api/chapters/${chapter.id}`
  const contextPath = `/api/branches/${chapter.branch_id}/chapters/${chapter.id}/writing-context`

  const writing = page.getByRole('region', { name: '正文写作', exact: true })
  const toolbar = writing.getByRole('group', { name: '正文工具栏', exact: true })
  const count = toolbar.getByRole('group', { name: '字数', exact: true })
  const save = toolbar.getByRole('button', { name: '保存', exact: true })
  const saved = toolbar.getByRole('status')
  const editor = writing.getByRole('textbox', { name: '章节正文', exact: true })
  const frame = writing.locator('.editor')
  const outline = writing.getByText('本章章纲 · 暂无章纲', { exact: true })
  const outlineAction = writing.getByRole('button', { name: '记下作者章纲', exact: true })
  const displayedCount = (amount: number) => new RegExp(`^字数\\s*${amount}\\s*字$`)
  const nextSave = () =>
    page.waitForResponse(
      (response) => new URL(response.url()).pathname === savePath && response.request().method() === 'PUT',
    )
  const position = () =>
    frame.evaluate((element) => ({
      top: element.getBoundingClientRect().top,
      pageScroll: window.scrollY,
      zoom: window.visualViewport?.scale ?? 1,
    }))

  await expect(editor).toBeEditable()
  await expect(editor).toBeFocused()
  await expect(writing.getByText('正文', { exact: true })).toBeVisible()
  await expect(editor.getByText('在这里开始写正文…', { exact: true })).toBeVisible()
  await expect(frame).toHaveCSS('border-top-width', '1px')
  await expect(frame).toHaveCSS('border-top-style', 'solid')
  await expect(count).toHaveText(displayedCount(0))
  await expect(save).toBeInViewport()
  await expect(outline).toBeVisible()
  if (!(await outlineAction.isVisible())) await outline.click()
  await expect(outlineAction).toBeVisible()
  await editor.focus()
  await frame.evaluate((element) => {
    window.scrollBy(0, Math.max(0, element.getBoundingClientRect().top - 180))
  })
  await expect(save).toBeInViewport()
  const before = await position()

  const refreshGate = Promise.withResolvers<void>()
  await page.route(`**${contextPath}`, async (route) => {
    await refreshGate.promise
    await route.continue()
  })
  const autosave = nextSave()
  const contextRefresh = page.waitForRequest((request) => new URL(request.url()).pathname === contextPath)
  const draft = '风停了。 AI 在岗。\n她没回头。'
  const expectStablePosition = async () => {
    const after = await position()
    expect(Math.abs(after.top - before.top)).toBeLessThanOrEqual(1)
    expect(Math.abs(after.pageScroll - before.pageScroll)).toBeLessThanOrEqual(1)
    expect(after.zoom).toBe(before.zoom)
  }
  try {
    await page.keyboard.insertText('风停了。')
    await expect(count).toHaveText(displayedCount(4))
    await page.keyboard.insertText(' AI 在岗。')
    await page.keyboard.press('Enter')
    await page.keyboard.insertText('她没回头。')
    await expect(count).toHaveText(displayedCount(14))
    const auto = await autosave
    expect(auto.ok()).toBeTruthy()
    expect(await auto.json()).toMatchObject({ content: draft })
    await contextRefresh
    await expect(saved).toHaveText('已保存到本机')
    await expect(outlineAction).toBeVisible()
    await expectStablePosition()
    const ready = page.waitForResponse((response) => new URL(response.url()).pathname === contextPath)
    refreshGate.resolve()
    expect((await ready).ok()).toBeTruthy()
    await expect(outlineAction).toBeVisible()
    await expectStablePosition()
    await expect(editor).toBeFocused()
  } finally {
    refreshGate.resolve()
  }

  const continuation = '她松开门栓。'
  await page.keyboard.insertText(continuation)
  await expect(count).toHaveText(displayedCount(20))
  const explicitSave = nextSave()
  await save.click()
  expect((await explicitSave).ok()).toBeTruthy()
  await expect(saved).toHaveText('已保存到本机')
  expect(await (await page.request.get(savePath)).json()).toMatchObject({ content: draft + continuation })
})

test('正文服务保存失败后，同一标签页恢复未提交草稿且其他章仍读自己的正文', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const created = await page.request.post('/api/novels', {
    headers,
    data: { title: '后台验证 · 本章草稿缓存隔离' },
  })
  expect(created.ok()).toBeTruthy()
  const novel = await created.json()
  async function seedChapter(title: string, content: string) {
    const response = await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
      headers,
      data: { title },
    })
    expect(response.ok()).toBeTruthy()
    const { id } = await response.json()
    const saved = await page.request.put(`/api/chapters/${id}`, {
      headers,
      data: { base_revision: 0, content },
    })
    expect(saved.ok()).toBeTruthy()
    return saved.json()
  }
  const first = await seedChapter('第一章 · 屋檐', '她听见雨落在檐下。')
  const second = await seedChapter('第二章 · 城门', '城门沿旧轨缓缓打开。')
  const pending = '她伸手接住一粒雨。😀𠮷'
  const savePath = `/api/chapters/${first.id}`
  await page.context().route(`**${savePath}`, async (route) => {
    if (route.request().method() === 'PUT')
      await route.fulfill({ status: 503, json: { code: 'offline_test', message: '验收暂时断开正文保存' } })
    else await route.continue()
  })

  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  const editor = page.getByRole('textbox', { name: '章节正文', exact: true })
  await expect(editor).toHaveText(first.content)
  const rejected = page.waitForResponse(
    (response) => new URL(response.url()).pathname === savePath && response.request().method() === 'PUT',
  )
  await editor.fill(pending)
  await page.getByRole('button', { name: '保存', exact: true }).click()
  expect((await rejected).status()).toBe(503)
  await expect(page.getByRole('group', { name: '正文工具栏', exact: true }).getByRole('status')).toHaveText(
    '保存失败，浏览器草稿保留；可下载草稿后比较',
  )
  expect(await (await page.request.get(savePath)).json()).toMatchObject({
    content: first.content,
    revision: first.revision,
  })

  page.once('dialog', (dialog) => dialog.accept())
  await page.reload()
  await expect(editor).toHaveText(pending)

  const other = await page.context().newPage()
  try {
    await other.goto('/')
    await other.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
    await other
      .getByRole('navigation', { name: '作品树' })
      .getByRole('button', { name: /第二章 · 城门/ })
      .click()
    await expect(other.getByRole('textbox', { name: '章节正文', exact: true })).toHaveText(second.content)
    expect(await (await page.request.get(`/api/chapters/${second.id}`)).json()).toMatchObject({
      content: second.content,
      revision: second.revision,
    })
    expect(await (await page.request.get(savePath)).json()).toMatchObject({
      content: first.content,
      revision: first.revision,
    })
  } finally {
    await other.close()
  }
})
