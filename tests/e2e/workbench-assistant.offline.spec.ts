import { expect, test, type Page } from '@playwright/test'
import { openBench } from './helpers/creationFlow'
import { writingTools } from './helpers/writingTools'

const original = '学徒找到一枚不鸣铜铃。'
const proposal = '悬疑短篇，围绕能够回查的铜铃记录推进。'

async function newWork(page: Page, title: string) {
  await page.goto('/')
  await page.getByText('＋ 新建作品', { exact: true }).click()
  await page.getByRole('textbox', { name: '作品名称', exact: true }).fill(title)
  const result = page.waitForResponse(
    (response) => response.request().method() === 'POST' && response.url().endsWith('/api/novels'),
  )
  await page.getByRole('button', { name: '创建并开始写作', exact: true }).click()
  const response = await result
  expect(response.ok(), await response.text()).toBeTruthy()
  const { branch_id } = (await response.json()) as { branch_id: string }
  await expect(
    page.getByRole('combobox', { name: '作品', exact: true }).locator('option:checked'),
  ).toHaveText(title)
  const { csrf } = (await (await page.request.get('/api/session')).json()) as { csrf: string }
  return { route: `/api/branches/${branch_id}`, headers: { 'X-Ainovel-CSRF': csrf } }
}

async function openProject(page: Page) {
  await openBench(page, '立项台')
  const project = page.locator('#bench-project')
  await project
    .locator('summary')
    .filter({ hasText: /^立项台/ })
    .click()
  return project
}

async function newWritingChapter(page: Page, title: string) {
  const { route, headers } = await newWork(page, title)
  const chapter = await page.request.post(`${route}/chapters`, { headers, data: { title: '试铃' } })
  expect(chapter.ok(), await chapter.text()).toBeTruthy()
  const { id } = (await chapter.json()) as { id: string }
  const saved = await page.request.put(`/api/chapters/${id}`, {
    headers,
    data: { base_revision: 0, content: original },
  })
  expect(saved.ok(), await saved.text()).toBeTruthy()
  await page.reload()
  const editor = page.getByRole('textbox', { name: '章节正文', exact: true })
  await expect(editor).toHaveText(original)
  return { route, editor }
}

test('立项快捷候选只写入所选字段，重开后回读多轮和载入记录', async ({ page }) => {
  await newWork(page, '助手验收 · 立项与历史')
  const project = await openProject(page)
  const field = project.getByRole('textbox', { name: '核心定位', exact: true })
  const ending = project.getByRole('textbox', { name: '终局', exact: true })
  await field.fill(original)
  await ending.fill('铜铃最终归还。')
  await project.getByRole('button', { name: '润色主题 · 核心定位', exact: true }).click()
  const rail = page.locator('.wb-assistant-rail')
  await expect(rail.getByText('已按本轮范围生成建议，请用当前稿复核。', { exact: true })).toBeVisible()
  await rail.getByText('比较建议', { exact: true }).click()
  await expect(rail.getByText(original, { exact: true })).toBeVisible()
  await expect(rail.getByText(proposal, { exact: true })).toBeVisible()
  await rail.getByRole('button', { name: '写入当前草稿', exact: true }).click()
  await expect(field).toHaveValue(proposal)
  await expect(ending).toHaveValue('铜铃最终归还。')
  await expect(rail.getByText('已写入草稿', { exact: true })).toBeVisible()

  const question = '保留旧信物的设定，再具体一点。'
  await rail.getByLabel('本轮操作').selectOption('refine')
  await rail.getByRole('textbox', { name: '创作问题' }).fill(question)
  await rail.getByRole('button', { name: '发送', exact: true }).click()
  await expect(rail.getByText(question, { exact: true })).toBeVisible()
  await expect(rail.getByText('已按本轮范围生成建议，请用当前稿复核。', { exact: true })).toHaveCount(2)

  // A second page can read history while the first page still owns the pending project draft.
  const reopened = await page.context().newPage()
  await reopened.goto('/')
  const sameProject = await openProject(reopened)
  await sameProject.getByRole('button', { name: '与 AI 讨论 · 核心定位', exact: true }).click()
  const oldRail = reopened.locator('.wb-assistant-rail')
  await oldRail.getByRole('tab', { name: '历史', exact: true }).click()
  await oldRail.getByRole('button', { name: /核心定位/ }).click()
  await expect(oldRail.getByText(question, { exact: true })).toBeVisible()
  await oldRail.getByText('比较建议', { exact: true }).first().click()
  await expect(oldRail.getByText('已写入草稿', { exact: true })).toBeVisible()
  await expect(sameProject.getByRole('textbox', { name: '核心定位', exact: true })).toHaveValue('')
  await reopened.close()
})

test('刷新历史后新问题仍发到新的对话', async ({ page }) => {
  const { route } = await newWork(page, '助手验收 · 新对话刷新')
  const project = await openProject(page)
  await project.getByRole('textbox', { name: '核心定位', exact: true }).fill(original)
  await project.getByRole('button', { name: '润色主题 · 核心定位', exact: true }).click()
  const rail = page.locator('.wb-assistant-rail')
  const answer = '已按本轮范围生成建议，请用当前稿复核。'
  await expect(rail.getByText(answer, { exact: true })).toBeVisible()
  const endpoint = route + '/assistant/conversations'
  const list = async (): Promise<{ id: string }[]> => {
    const response = await page.request.get(endpoint)
    expect(response.ok(), await response.text()).toBeTruthy()
    return (await response.json()).conversations
  }
  const [previous] = await list()

  await rail.getByRole('tab', { name: '历史', exact: true }).click()
  await rail.getByRole('button', { name: '新对话', exact: true }).click()
  const question = '请把这一轮另开一段讨论。'
  const composer = rail.getByRole('textbox', { name: '创作问题' })
  await composer.fill(question)
  const refreshed = page.waitForResponse(
    (response) =>
      response.ok() && response.request().method() === 'GET' && new URL(response.url()).pathname === endpoint,
  )
  await rail.getByRole('button', { name: '刷新历史', exact: true }).click()
  await refreshed
  await expect(rail.getByText(answer, { exact: true })).toHaveCount(0)
  await expect(composer).toHaveValue(question)

  const sent = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname.startsWith(endpoint + '/') &&
      response.url().endsWith('/turns'),
  )
  await rail.getByRole('button', { name: '发送', exact: true }).click()
  const receipt = await sent
  expect(receipt.status(), await receipt.text()).toBe(202)
  expect(new URL(receipt.url()).pathname).not.toBe(endpoint + '/' + previous.id + '/turns')
  expect(await list()).toHaveLength(2)
  const older = await page.request.get(endpoint + '/' + previous.id + '/turns')
  expect(older.ok(), await older.text()).toBeTruthy()
  expect((await older.json()).turns).toHaveLength(1)
})

test('写作工具可并列切换，保留未提交要求，读资料和准备提取时不运行模型', async ({ page }) => {
  const { editor } = await newWritingChapter(page, '助手验收 · 写作工具')
  const tools = writingTools(page)
  const generation = tools.getByRole('tab', { name: '生成与候选', exact: true })
  const assistant = tools.getByRole('tab', { name: 'AI 助手', exact: true })
  const sources = tools.getByRole('tab', { name: '资料与依据', exact: true })
  await expect(tools.getByRole('tab')).toHaveText(['生成与候选', 'AI 助手', '资料与依据'])
  await expect(generation).toHaveAttribute('aria-selected', 'true')
  const modelRequests: string[] = []
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname
    if (
      request.method() === 'POST' &&
      (path === '/api/modules' || /\/assistant\/conversations(?:\/|$)/.test(path))
    )
      modelRequests.push(path)
  })
  const prompt = page.getByRole('textbox', { name: '情节或具体要求', exact: true })
  const pending = '查清旧铃在哪里丢失，先保留这段要求。'
  await prompt.fill(pending)

  await generation.focus()
  await page.keyboard.press('ArrowRight')
  await expect(assistant).toBeFocused()
  await assistant.press('Enter')
  await expect(assistant).toHaveAttribute('aria-selected', 'true')
  const question = page.getByRole('textbox', { name: '创作问题' })
  await expect(question).toBeVisible()
  await question.fill('这一段的悬念是否清楚？')
  await expect(prompt).toBeHidden()

  await assistant.focus()
  await page.keyboard.press('End')
  await expect(sources).toBeFocused()
  await sources.press('Enter')
  await expect(sources).toHaveAttribute('aria-selected', 'true')
  await expect(question).toBeHidden()
  await page.setViewportSize({ width: 320, height: 860 })
  const generateLink = page.getByRole('link', { name: '去生成候选', exact: true })
  await expect(generateLink).toBeVisible()
  await generateLink.press('Enter')
  await expect(generation).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('#writing-task-form')).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(prompt).toBeFocused()
  await expect(prompt).toHaveValue(pending)

  await assistant.click()
  await expect(question).toHaveValue('这一段的悬念是否清楚？')
  await sources.click()
  await page.getByText(/^写到这里的故事记录 ·/).click()
  await page.getByRole('button', { name: '准备提取本章故事记录', exact: true }).click()
  await expect(generation).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('combobox', { name: '这次要做什么', exact: true })).toHaveValue(
    'memory.extract',
  )
  await expect(prompt).toHaveValue(/提取当前正文中有逐字依据/)
  await expect(editor).toHaveText(original)
  expect(modelRequests).toEqual([])
})

test('正文选区经作者载入后保留编辑器撤销，校验台十项都有 AI 入口', async ({ page }) => {
  const { route, editor } = await newWritingChapter(page, '助手验收 · 选区与校验')
  await editor.press('Control+a')
  const inline = page.getByRole('button', { name: 'AI 改写选区 · 试铃 · 正文选区', exact: true })
  const assistant = writingTools(page).getByRole('tab', { name: 'AI 助手', exact: true })
  await inline.click()
  await expect(assistant).toHaveAttribute('aria-selected', 'true')
  const rail = page.locator('.wb-assistant-rail')
  const compare = rail.getByText('比较建议', { exact: true })
  await compare.click()
  await page.keyboard.press('Escape')
  await expect(inline).toBeFocused()
  await assistant.click()
  await expect(compare).toBeVisible()
  await rail.getByRole('button', { name: '写入当前草稿', exact: true }).click()
  await expect(editor).toContainText('铜铃没有响，学徒把这一点记了下来。')
  await editor.press('Control+z')
  await expect(editor).toHaveText(original)

  await openBench(page, '校验台')
  const inspection = page.getByRole('region', { name: '十处对账体检' })
  const choices = inspection.getByLabel('核对项目')
  for (let number = 1; number <= 10; number++) {
    const code = String(number).padStart(2, '0')
    await choices.selectOption(code)
    await expect(inspection.getByRole('button', { name: new RegExp(`^AI 核对依据 · ${code}`) })).toBeVisible()
  }
  await choices.selectOption('07')
  await inspection.getByRole('button', { name: /^AI 核对依据 · 07/ }).click()
  await rail.getByText('比较建议', { exact: true }).click()
  await rail.getByRole('button', { name: '写入当前草稿', exact: true }).click()
  await expect(inspection.getByRole('textbox', { name: '判定理由', exact: true })).toHaveValue(
    '按当前保存稿和正式依据逐项核对，待作者确认。',
  )
  await expect(inspection.getByRole('combobox', { name: '作者判定', exact: true })).toHaveValue('')
  const current = (await (await page.request.get(`${route}/workflow-inspections`)).json()) as {
    latest: unknown
  }
  expect(current.latest).toBeNull()
})
