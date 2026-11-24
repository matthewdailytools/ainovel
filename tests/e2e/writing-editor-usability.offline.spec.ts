import { expect, test } from '@playwright/test'

test('首章从可见正文框开始写作，字数随输入更新，自动保存保持位置', async ({ page }, info) => {
  await page.setViewportSize({ width: 1280, height: 720 })
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
  const outline = toolbar.getByRole('button', { name: '章纲 · 暂无', exact: true })
  const outlineAction = page
    .getByRole('complementary', { name: '创作辅助', exact: true })
    .getByRole('button', { name: '记下作者章纲', exact: true })
  const history = toolbar.getByRole('button', { name: '版本历史', exact: true })
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
  const identity = toolbar.locator('.chapter-identity')
  await expect(identity.getByRole('textbox', { name: '章节标题', exact: true })).toHaveValue(/^第\s*1\s*章$/)
  await expect(identity.getByText(/^第\s*1\s*章$/, { exact: true })).toBeHidden()
  await expect(writing.getByText(/^修订 \d+$/)).toHaveCount(0)
  await page.evaluate(() => window.scrollTo(0, 0))
  const firstFold = await position()
  expect(firstFold.top).toBeGreaterThan(0)
  expect(firstFold.top).toBeLessThanOrEqual(200)
  await expect(outline).toBeVisible()
  await expect(outlineAction).toBeHidden()
  await outline.click()
  await expect(outlineAction).toBeVisible()
  expect(Math.abs((await position()).top - firstFold.top)).toBeLessThanOrEqual(1)
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

  const beforeHistory = await position()
  await history.click()
  const historyPane = page.getByRole('dialog', { name: '版本历史', exact: true })
  await expect(historyPane).toBeVisible()
  await expect(historyPane.locator('summary').first()).toContainText(/^修订 \d+/)
  expect(Math.abs((await position()).top - beforeHistory.top)).toBeLessThanOrEqual(1)
  await historyPane.getByRole('button', { name: '关闭版本历史', exact: true }).click()
  await expect(history).toBeFocused()
  await expect(editor).toContainText(continuation)

  await editor.press('End')
  await page.keyboard.insertText('风又起了。')
  await page.evaluate(() => window.scrollTo(0, 0))
  const beforeSettings = await position()
  const settingsButton = page.getByRole('button', { name: '设置', exact: true })
  await settingsButton.click()
  const settings = page.getByRole('dialog', { name: '设置', exact: true })
  await expect(settings.getByRole('heading', { name: '模型与预算', exact: true })).toBeVisible()
  await expect(settings.getByRole('heading', { name: '本机数据', exact: true })).toBeVisible()
  await expect(settings.getByRole('combobox', { name: '模型账户', exact: true })).toBeVisible()
  await expect(
    page.getByRole('combobox', { name: '模型账户', exact: true, includeHidden: true }),
  ).toHaveCount(1)
  expect(Math.abs((await position()).top - beforeSettings.top)).toBeLessThanOrEqual(1)
  await page.keyboard.press('Escape')
  await expect(settings).toBeHidden()
  await expect(settingsButton).toBeFocused()
  await expect(editor).toContainText('风又起了。')
  await save.click()
  await expect(saved).toHaveText('已保存到本机')

  await info.attach('writing-first-fold-desktop', {
    contentType: 'application/json',
    body: JSON.stringify({ width: 1280, height: 720, ...firstFold }),
  })
})

test('窄屏首屏可写作，目录和 AI 面板返回后保留正文与光标', async ({ page }, info) => {
  await page.goto('/')
  await page.locator('.new-novel summary').click()
  await page.getByRole('textbox', { name: '作品名称', exact: true }).fill('后台验证 · 窄屏首屏')
  await page.getByRole('button', { name: '创建并开始写作', exact: true }).click()
  const created = page.waitForResponse(
    (response) => response.url().endsWith('/chapters') && response.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '新建第一章', exact: true }).click()
  const chapter = await (await created).json()
  const editor = page.getByRole('textbox', { name: '章节正文', exact: true })
  const title = page.getByRole('textbox', { name: '章节标题', exact: true })
  const save = page.getByRole('button', { name: '保存', exact: true })
  await expect(title).toBeEditable()
  const titleText = '山径夜话'
  await title.fill(titleText)
  await title.press('Tab')
  await expect
    .poll(async () => (await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).title)
    .toBe(titleText)
  await editor.fill('云来了。')
  await save.click()
  await expect(page.getByText('已保存到本机', { exact: true })).toBeVisible()
  await page.reload()
  await expect(editor).toHaveText('云来了。')

  const frame = page.getByRole('region', { name: '正文写作', exact: true }).locator('.editor')
  const measurements: { width: number; height: number; editorTop: number; documentWidth: number }[] = []
  for (const { width, height } of [
    { width: 390, height: 844 },
    { width: 320, height: 720 },
  ]) {
    await page.setViewportSize({ width, height })
    await page.evaluate(() => window.scrollTo(0, 0))
    const directory = page.getByRole('button', { name: '目录', exact: true })
    const assistant = page.getByRole('button', { name: 'AI 对话', exact: true })
    for (const control of [directory, assistant, save]) await expect(control).toBeInViewport()
    const measure = () =>
      frame.evaluate((element) => ({
        width: innerWidth,
        height: innerHeight,
        editorTop: Math.round(element.getBoundingClientRect().top),
        documentWidth: document.documentElement.scrollWidth,
      }))
    const before = await measure()
    measurements.push(before)
    if (width === 390) expect(before.editorTop).toBeLessThanOrEqual(260)
    expect(before.documentWidth).toBeLessThanOrEqual(width)

    await directory.click()
    const tree = page.getByRole('navigation', { name: '作品树', exact: true })
    await expect(tree.getByRole('button', { name: /山径夜话/, exact: false })).toBeVisible()
    await page.getByRole('button', { name: '关闭目录', exact: true }).click()
    await expect(directory).toBeFocused()
    await expect(tree).toBeHidden()

    await editor.press('End')
    await editor.press('ArrowLeft')
    await assistant.click()
    await expect(page.getByRole('region', { name: 'AI 对话', exact: true })).toBeVisible()
    await expect(page.getByRole('combobox', { name: '模型账户', exact: true })).toHaveCount(0)
    await page.getByRole('button', { name: '关闭创作辅助', exact: true }).click()
    await expect(assistant).toBeFocused()
    const original = await editor.textContent()
    await editor.focus()
    await page.keyboard.insertText('又')
    await expect(editor).toHaveText(original!.replace('。', '又。'))
    await save.click()
    await expect(page.getByText('已保存到本机', { exact: true })).toBeVisible()

    await page.evaluate(() => window.scrollTo(0, 0))
    const settingsButton = page.getByRole('button', { name: '设置', exact: true })
    await expect(settingsButton).toBeInViewport()
    await settingsButton.click()
    const settings = page.getByRole('dialog', { name: '设置', exact: true })
    await expect(settings.getByRole('combobox', { name: '模型账户', exact: true })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    await page.keyboard.press('Escape')
    await expect(settings).toBeHidden()
    await expect(settingsButton).toBeFocused()
  }
  expect(await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).toMatchObject({
    content: '云来了又又。',
  })
  await info.attach('writing-first-fold-viewports', {
    contentType: 'application/json',
    body: JSON.stringify(measurements),
  })
})

test('模型账户通过全局设置切换，换工作区与刷新后继续使用所选账户', async ({ page }) => {
  await page.goto('/')
  await page.locator('.new-novel summary').click()
  await page.getByRole('textbox', { name: '作品名称', exact: true }).fill('后台验证 · 全局模型账户')
  await page.getByRole('button', { name: '创建并开始写作', exact: true }).click()
  await page.getByRole('button', { name: '新建第一章', exact: true }).click()
  await expect(page.getByRole('textbox', { name: '章节正文', exact: true })).toBeEditable()

  const settingsButton = page.getByRole('button', { name: '设置', exact: true })
  const settings = page.getByRole('dialog', { name: '设置', exact: true })
  const account = settings.getByRole('combobox', { name: '模型账户', exact: true })
  const close = settings.getByRole('button', { name: '关闭设置', exact: true })
  await expect(page.getByRole('combobox', { name: '模型账户', exact: true })).toHaveCount(0)
  await settingsButton.click()
  await expect(account).toHaveValue('offline')
  await account.selectOption('offline-relay')
  await close.click()
  await expect(settingsButton).toBeFocused()

  for (const workspace of ['资料书库', '整书创作', '写作']) {
    const destination = page.getByRole('button', { name: workspace, exact: true })
    await destination.click()
    await expect(destination).toHaveAttribute('aria-current', 'page')
    await expect(page.getByRole('combobox', { name: '模型账户', exact: true })).toHaveCount(0)
    await settingsButton.click()
    await expect(account).toHaveValue('offline-relay')
    await page.keyboard.press('Escape')
    await expect(settingsButton).toBeFocused()
  }

  await page.reload()
  await expect(page.getByRole('textbox', { name: '章节正文', exact: true })).toBeEditable()
  await settingsButton.click()
  await expect(account).toHaveValue('offline-relay')
  await close.click()
  const assistant = page.getByRole('region', { name: 'AI 对话', exact: true })
  await assistant.getByText('对话设置与发送范围', { exact: true }).click()
  await expect(assistant.getByText(/模型账户 offline-relay/)).toBeVisible()
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
