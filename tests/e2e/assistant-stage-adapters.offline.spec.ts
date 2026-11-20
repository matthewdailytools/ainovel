import { expect, test, type Locator, type Page } from '@playwright/test'

const proposal = '悬疑短篇，围绕能够回查的铜铃记录推进。'

async function applyQuickSuggestion(
  page: Page,
  route: string,
  button: Locator,
  target: { stage: string; scopeId: string | null; field: string; input: string },
  options: { spoiler_scope: string; sample_chapters: number } | Record<string, never> = {},
) {
  const conversations = `${route}/assistant/conversations`
  const created = page.waitForResponse(
    (reply) => reply.request().method() === 'POST' && new URL(reply.url()).pathname === conversations,
  )
  const queued = page.waitForResponse(
    (reply) =>
      reply.request().method() === 'POST' &&
      new URL(reply.url()).pathname.startsWith(conversations + '/') &&
      new URL(reply.url()).pathname.endsWith('/turns'),
  )
  await button.press('Enter')
  const conversation = await created
  expect(conversation.status(), await conversation.text()).toBe(201)
  expect(conversation.request().postDataJSON()).toMatchObject({
    stage: target.stage,
    scope_id: target.scopeId,
  })
  const turn = await queued
  expect(turn.status(), await turn.text()).toBe(202)
  expect(turn.request().postDataJSON()).toMatchObject({
    target: target.field,
    draft_text: target.input,
    ...options,
  })
  const rail = page.locator('.wb-assistant-rail')
  await rail.getByText('比较建议', { exact: true }).click()
  await expect(rail.getByText(proposal, { exact: true })).toBeVisible()
  await rail.getByRole('button', { name: '写入当前草稿', exact: true }).press('Enter')
  await expect(rail.getByText('已写入草稿', { exact: true })).toBeVisible()
}

test('拆解、设定、大纲、完本的助手绑定当前对象且只载入草稿', async ({ page }) => {
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const json = async (path: string) => {
    const response = await page.request.get(path)
    expect(response.ok(), await response.text()).toBe(true)
    return response.json()
  }
  const novel = await page.request.post('/api/novels', {
    headers,
    data: { title: '助手验收 · 四台范围与正式保存' },
  })
  expect(novel.status()).toBe(201)
  const { id, branch_id: branchId } = await novel.json()
  const route = `/api/branches/${branchId}`
  for (const [title, content] of [
    ['拾铃', '学徒拾到铜铃，记下铃身的记号。'],
    ['归还', '原来守库人才是失散的姐姐。'],
  ]) {
    const chapter = await page.request.post(`${route}/chapters`, { headers, data: { title } })
    expect(chapter.status()).toBe(201)
    const { id: chapterId } = await chapter.json()
    const saved = await page.request.put(`/api/chapters/${chapterId}`, {
      headers,
      data: { base_revision: 0, content },
    })
    expect(saved.ok(), await saved.text()).toBe(true)
  }

  const sourceText = '留客没有接过灯，只看了灯罩里的火苗。'
  const upload = await page.request.post('/api/library/import', {
    headers,
    multipart: {
      file: { name: '助手范围样例.txt', mimeType: 'text/plain', buffer: Buffer.from(sourceText) },
    },
  })
  expect(upload.ok(), await upload.text()).toBe(true)
  const { id: bookId } = await upload.json()
  const [unit] = await json(`/api/library/books/${bookId}/units`)
  const goal = '观察以身边物件收束。'
  const reference = await page.request.post('/api/reference-sessions', {
    headers,
    data: {
      branch_id: branchId,
      unit_id: unit.id,
      start: 0,
      end: sourceText.length,
      quote: sourceText,
      goal,
    },
  })
  expect(reference.status(), await reference.text()).toBe(201)
  const { id: sessionId } = await reference.json()
  const referencePath = `/api/reference-sessions/${sessionId}`
  const savedReference = await json(referencePath)

  const conceptionPath = `${route}/conception`
  const beforeSettings = await json(conceptionPath)
  const settingId = 'adapter-bell-rule'
  const settingDetail = '旧物须经库房登记。'
  beforeSettings.categories.find(({ id: category }: { id: string }) => category === 'world').items = [
    { id: settingId, title: '库房规则', detail: settingDetail },
  ]
  const settings = await page.request.put(conceptionPath, {
    headers,
    data: {
      base_revision: beforeSettings.revision,
      base_story_revision: beforeSettings.story_revision,
      categories: beforeSettings.categories,
    },
  })
  expect(settings.ok(), await settings.text()).toBe(true)
  const settingsDraft = await settings.json()
  const confirmed = await page.request.post(conceptionPath + '/confirm', {
    headers,
    data: {
      base_revision: settingsDraft.revision,
      base_story_revision: settingsDraft.story_revision,
      idempotency_key: 'assistant-adapter-settings-' + branchId,
    },
  })
  expect(confirmed.ok(), await confirmed.text()).toBe(true)
  const { confirmation } = await confirmed.json()
  const savedSettings = await json(conceptionPath)
  const outlinePath = `${route}/outline-workspace`
  const summary = '学徒查库房登记，循铜铃寻找失散亲人。'
  const outline = await page.request.put(outlinePath, {
    headers,
    data: { base_revision: 0, confirmation_id: confirmation.id, plan: { summary } },
  })
  expect(outline.ok(), await outline.text()).toBe(true)
  const savedOutline = await json(outlinePath)

  await page.goto('/')
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(id)
  const benches = page.getByRole('navigation', { name: '七台工作流' })
  await benches.getByRole('button', { name: '拆解台', exact: true }).click()
  await page.getByRole('button', { name: /观察以身边物件收束/ }).click()
  await applyQuickSuggestion(page, route, page.getByRole('button', { name: 'AI 润色 · 参考学习目标' }), {
    stage: 'M2',
    scopeId: sessionId,
    field: 'goal',
    input: goal,
  })
  await expect(page.getByRole('textbox', { name: '想学习什么' })).toHaveValue(proposal)
  expect(await json(referencePath)).toEqual(savedReference)

  page.once('dialog', (dialog) => dialog.accept())
  await benches.getByRole('button', { name: '设定台', exact: true }).click()
  await page.getByRole('navigation', { name: '构思类别' }).getByRole('button', { name: '世界设定' }).click()
  await applyQuickSuggestion(
    page,
    route,
    page.getByRole('button', { name: '完善设定 · 库房规则 · 设定内容' }),
    { stage: 'M3', scopeId: settingId, field: 'detail', input: settingDetail },
  )
  await expect(page.getByRole('textbox', { name: '设定内容', exact: true })).toHaveValue(proposal)
  expect(await json(conceptionPath)).toEqual(savedSettings)

  page.once('dialog', (dialog) => dialog.accept())
  await benches.getByRole('button', { name: '大纲台', exact: true }).click()
  await applyQuickSuggestion(page, route, page.getByRole('button', { name: '完善总纲 · 总纲' }), {
    stage: 'M4',
    scopeId: null,
    field: 'summary',
    input: summary,
  })
  await expect(page.getByRole('textbox', { name: '总纲', exact: true })).toHaveValue(proposal)
  expect(await json(outlinePath)).toEqual(savedOutline)

  page.once('dialog', (dialog) => dialog.accept())
  await benches.getByRole('button', { name: '完本台', exact: true }).click()
  const completionPath = `${route}/book-completion`
  const beforeCompletion = await json(completionPath)
  expect(beforeCompletion.checks).toHaveLength(6)
  const completion = page.getByRole('region', { name: '完本与交付', exact: true })
  await applyQuickSuggestion(
    page,
    route,
    completion.getByRole('button', { name: 'AI 起草简介 · 交付简介' }),
    { stage: 'M7', scopeId: null, field: 'intro', input: '' },
    { spoiler_scope: 'opening', sample_chapters: 1 },
  )
  const intro = completion.getByRole('textbox', { name: '简介', exact: true })
  const preview = completion.getByRole('button', { name: '预览交付材料' })
  await expect(intro).toHaveValue(proposal)
  await expect(preview).toBeEnabled()
  await completion.getByLabel('从首章起选入的试读章数').selectOption('2')
  await expect(completion.getByText(/已载入的 AI 简介采用另一剧透或试读范围/)).toBeVisible()
  await expect(preview).toBeDisabled()
  await expect(intro).toHaveValue(proposal)
  await completion.getByLabel('从首章起选入的试读章数').selectOption('1')
  await expect(preview).toBeEnabled()
  await completion.getByLabel('简介与试读的剧透范围').selectOption('whole')
  await expect(preview).toBeDisabled()
  await expect(completion.getByLabel('已通读本次稿件')).not.toBeChecked()
  await expect(completion.getByLabel('主冲突与终局判定')).toHaveValue('pending')
  await expect(completion.getByLabel('关键人物与关系判定')).toHaveValue('pending')
  await expect(completion.getByLabel('伏笔与读者承诺判定')).toHaveValue('pending')
  const afterCompletion = await json(completionPath)
  expect(afterCompletion.checks).toEqual(beforeCompletion.checks)
  expect(afterCompletion.latest).toBeNull()
})
