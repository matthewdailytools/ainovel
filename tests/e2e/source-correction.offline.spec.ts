import { expect, test, type Page } from '@playwright/test'
import { assertActualDoubleText } from './helpers/actualTextScale'
import { openWritingPanel } from './helpers/writingPanels'

type Fixture = {
  novel_id: string
  branch_id: string
  chapter_id: string
  parent_id: string
  candidate_id: string
}
async function fixture(page: Page): Promise<Fixture> {
  const session = await (await page.request.get('/api/session')).json()
  const response = await page.request.post('/test/source-correction/fixtures', {
    headers: { 'X-Ainovel-CSRF': session.csrf },
    data: { client_request_id: crypto.randomUUID() },
  })
  expect(response.status(), await response.text()).toBe(201)
  return response.json()
}
async function open(page: Page, row: Fixture) {
  await page.goto('/')
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(row.novel_id)
  await page.getByRole('button', { name: '写作', exact: true }).click()
  await page.getByRole('combobox', { name: '比较候选', exact: true }).selectOption(row.candidate_id)
  await page.getByText('核对原情节解释，再显式复检正文', { exact: true }).click()
  const panel = page.getByRole('region', { name: '原情节来源解释纠偏', exact: true })
  await expect(panel.getByRole('button', { name: '读取来源解释依据', exact: true })).toBeEnabled()
  return panel
}
async function ready(page: Page, taskId: string) {
  await expect
    .poll(async () => (await (await page.request.get(`/api/tasks/${taskId}`)).json()).status, {
      timeout: 25000,
    })
    .toBe('succeeded')
  return (await page.request.get(`/api/tasks/${taskId}`)).json()
}

test('来源纠偏完整作者路径：独立回执、显式正文复检、只读和编辑后失效', async ({ page }, info) => {
  page.on('dialog', (dialog) => void dialog.accept())
  const row = await fixture(page)
  const original = await (await page.request.get(`/api/artifacts/${row.candidate_id}`)).json()
  const parent = await (await page.request.get(`/api/tasks/${row.parent_id}`)).json()
  const panel = await open(page, row)
  await panel.getByRole('button', { name: '读取来源解释依据', exact: true }).click()
  await panel.getByRole('checkbox', { name: /^P02/ }).check()
  await expect(panel.getByRole('checkbox', { name: /^P02/ })).toBeChecked()
  const reason = panel.getByRole('textbox', { name: '本次来源解释纠偏原因（可留空）', exact: true })
  const submit = panel.getByRole('button', { name: '提交来源解释纠偏（额外模型费用）', exact: true })
  await reason.fill('原'.repeat(2001))
  await expect(submit).toBeDisabled()
  await expect(panel).toContainText('原因超过2000个字符')
  await reason.fill('')
  await expect(submit).toBeEnabled()
  const posted = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/artifacts/${row.candidate_id}/source-interpretation-corrections`) &&
      response.request().method() === 'POST',
  )
  await submit.click()
  const dispatched = await posted
  expect(dispatched.status(), await dispatched.text()).toBe(202)
  const sourceTask = await ready(page, (await dispatched.json()).id)
  const sourceReceiptId = sourceTask.result.artifact_id
  const sourceReceipt = await (await page.request.get(`/api/artifacts/${sourceReceiptId}`)).json()
  expect(sourceReceipt.payload.state).toBe('ready')
  expect(sourceReceipt.payload.proposal.items[1]).toMatchObject({
    actor: '',
    statement_kind: 'narrative_requirement',
  })
  expect(await (await page.request.get(`/api/artifacts/${row.candidate_id}`)).json()).toEqual(original)
  await expect(panel.getByRole('region', { name: '来源解释纠偏只读回执' })).toContainText('回执就绪')
  const readBody = panel.getByRole('button', { name: '读取就绪回执对应的正文复检依据', exact: true })
  await expect(readBody).toBeEnabled()
  await readBody.focus()
  await readBody.press('Enter')
  const bodySubmit = panel.getByRole('button', { name: '提交独立正文复检（额外模型费用）', exact: true })
  await expect(bodySubmit).toBeEnabled()
  const bodyPosted = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/artifacts/${row.candidate_id}/recheck-source-correction`) &&
      response.request().method() === 'POST',
  )
  await bodySubmit.click()
  const bodyResponse = await bodyPosted
  expect(bodyResponse.status(), await bodyResponse.text()).toBe(202)
  const checked = await ready(page, (await bodyResponse.json()).id)
  const reportId = checked.result.artifact_id
  const candidate = await (await page.request.get(`/api/artifacts/${row.candidate_id}`)).json()
  expect(candidate.candidate_revision).toBe(0)
  expect(candidate.payload.text).toBe(original.payload.text)
  expect(candidate.payload.review_passed).toBe(true)
  expect(candidate.payload.source_corrected_rechecks[0].artifact_id).toBe(reportId)
  expect((await (await page.request.get(`/api/tasks/${row.parent_id}`)).json()).result).toEqual(parent.result)
  expect((await (await page.request.get(`/api/chapters/${row.chapter_id}`)).json()).content).toBe('')
  const compare = page.getByRole('combobox', { name: '比较候选', exact: true })
  await expect(compare.locator(`option[value="${reportId}"]`)).toBeAttached()
  await compare.selectOption(reportId)
  const report = page.getByRole('region', { name: '来源解释只读记录', exact: true })
  await expect(report).toContainText('模型标本轮复检通过')
  await expect(report.getByRole('button', { name: /采纳|拒绝|保存候选/ })).toHaveCount(0)
  await report.getByText('情节逐项核对 · 模型标一致，仍须作者核对', { exact: true }).click()
  await page.setViewportSize({ width: 320, height: 800 })
  await openWritingPanel(page, 'AI 对话')
  await assertActualDoubleText(
    page,
    {
      paragraph: report.locator('p').first(),
      quote: report.locator('blockquote').first(),
      button: report.getByRole('button', { name: '查看原候选', exact: true }),
      summary: report.locator('summary').first(),
    },
    info,
  )
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
  await report.screenshot({ path: info.outputPath('source-body-readonly-320-200.png') })
  await report.getByRole('button', { name: '查看原候选', exact: true }).click()
  await page
    .getByRole('textbox', { name: '候选内容', exact: true })
    .fill(candidate.payload.text + '\n作者补充一次未采用编辑。')
  await page.getByRole('button', { name: '保存候选编辑（原检查失效）', exact: true }).click()
  await expect
    .poll(
      async () =>
        (await (await page.request.get(`/api/artifacts/${row.candidate_id}`)).json()).candidate_revision,
    )
    .toBe(1)
  await compare.selectOption(reportId)
  await expect(report).toContainText('目标候选已变化，以下仅为历史记录')
  expect((await (await page.request.get(`/api/chapters/${row.chapter_id}`)).json()).revision).toBe(0)
})

test('来源提交响应未知：刷新后只查询原nonce，不重复创建任务', async ({ page }) => {
  page.on('dialog', (dialog) => void dialog.accept())
  const row = await fixture(page)
  const panel = await open(page, row)
  await panel.getByRole('button', { name: '读取来源解释依据', exact: true }).click()
  await panel.getByRole('checkbox', { name: /^P02/ }).check()
  let posts = 0
  let sourceId = ''
  await page.route(
    `**/api/artifacts/${row.candidate_id}/source-interpretation-corrections`,
    async (route) => {
      if (route.request().method() !== 'POST') return route.continue()
      posts++
      const response = await route.fetch()
      expect(response.status()).toBe(202)
      sourceId = (await response.json()).id
      await route.abort('failed')
    },
  )
  await panel.getByRole('button', { name: '提交来源解释纠偏（额外模型费用）', exact: true }).click()
  await expect(panel.getByRole('button', { name: '只查询原提交回执（不重发）', exact: true })).toBeVisible()
  await page.reload()
  await page.getByRole('combobox', { name: '比较候选', exact: true }).selectOption(row.candidate_id)
  await page.getByText('核对原情节解释，再显式复检正文', { exact: true }).click()
  const restored = page.getByRole('region', { name: '原情节来源解释纠偏', exact: true })
  await restored.getByRole('button', { name: '只查询原提交回执（不重发）', exact: true }).click()
  await expect(restored).toContainText(sourceId)
  expect(posts).toBe(1)
  const tasks = await (await page.request.get(`/api/tasks?branch_id=${row.branch_id}`)).json()
  expect(
    tasks.filter((task: { kind: string }) => task.kind === 'source_interpretation_correction'),
  ).toHaveLength(1)
  expect((await (await page.request.get(`/api/chapters/${row.chapter_id}`)).json()).content).toBe('')
})

test('来源409保留原因和范围，确认新依据后才允许新提交', async ({ page }) => {
  page.on('dialog', (dialog) => void dialog.accept())
  const row = await fixture(page)
  const panel = await open(page, row)
  await panel.getByRole('button', { name: '读取来源解释依据', exact: true }).click()
  await panel.getByRole('checkbox', { name: /^P02/ }).check()
  const reason = panel.getByRole('textbox', { name: '本次来源解释纠偏原因（可留空）', exact: true })
  await reason.fill('只核原话的叙述要求😀𠮷')
  await page.route(`**/api/artifacts/${row.candidate_id}/source-interpretation-corrections`, (route) =>
    route.fulfill({
      status: 409,
      json: { code: 'source_correction_preview_changed', message: '测试：原依据已变化，请重新读取。' },
    }),
  )
  await panel.getByRole('button', { name: '提交来源解释纠偏（额外模型费用）', exact: true }).click()
  await expect(panel).toContainText('测试：原依据已变化')
  await expect(reason).toHaveValue('只核原话的叙述要求😀𠮷')
  await expect(panel.getByRole('checkbox', { name: /^P02/ })).toBeChecked()
  await expect(
    panel.getByRole('button', { name: '提交来源解释纠偏（额外模型费用）', exact: true }),
  ).toBeDisabled()
  await panel.getByRole('button', { name: '重新读取来源解释依据', exact: true }).click()
  await expect(panel.getByRole('button', { name: '确认使用新依据，保留输入', exact: true })).toBeVisible()
  await expect(reason).toHaveValue('只核原话的叙述要求😀𠮷')
  await panel.getByRole('button', { name: '确认使用新依据，保留输入', exact: true }).click()
  await expect(
    panel.getByRole('button', { name: '提交来源解释纠偏（额外模型费用）', exact: true }),
  ).toBeEnabled()
  const tasks = await (await page.request.get(`/api/tasks?branch_id=${row.branch_id}`)).json()
  expect(tasks).toHaveLength(1)
})
