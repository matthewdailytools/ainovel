import { expect, test, type Page } from '@playwright/test'

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
  await page.getByRole('button', { name: '立项台', exact: true }).click()
  const project = page.locator('#bench-project')
  await project
    .locator('summary')
    .filter({ hasText: /^立项台/ })
    .click()
  return project
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

test('正文选区经作者载入后保留编辑器撤销，校验台十项都有 AI 入口', async ({ page }) => {
  const { route, headers } = await newWork(page, '助手验收 · 选区与校验')
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
  await editor.press('Control+a')
  await page.getByRole('button', { name: 'AI 改写选区 · 试铃 · 正文选区', exact: true }).click()
  const rail = page.locator('.wb-assistant-rail')
  await rail.getByText('比较建议', { exact: true }).click()
  await rail.getByRole('button', { name: '写入当前草稿', exact: true }).click()
  await expect(editor).toContainText('铜铃没有响，学徒把这一点记了下来。')
  await editor.press('Control+z')
  await expect(editor).toHaveText(original)

  await page.getByRole('button', { name: '校验台', exact: true }).click()
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
