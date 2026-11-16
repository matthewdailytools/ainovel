import { expect, test, type Page } from '@playwright/test'
import { assertActualDoubleText } from './helpers/actualTextScale'
type Fixture = {
  novel_id: string
  branch_id: string
  chapter_id: string
  parent_id: string
  candidate_id: string
}
async function fixture(page: Page, unknown = false): Promise<Fixture> {
  const session = await (await page.request.get('/api/session')).json()
  const response = await page.request.post('/test/prepared-scope/fixtures', {
    headers: { 'X-Ainovel-CSRF': session.csrf },
    data: { client_request_id: crypto.randomUUID(), unknown },
  })
  expect(response.status(), await response.text()).toBe(201)
  return response.json()
}
async function open(page: Page, row: Fixture) {
  await page.goto('/')
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(row.novel_id)
  await page.getByRole('button', { name: '写作', exact: true }).click()
  await page.getByRole('combobox', { name: '比较候选', exact: true }).selectOption(row.candidate_id)
  await page.getByText('核对AI章纲范围，再显式复检正文', { exact: true }).click()
  return page.getByRole('region', { name: 'AI章纲范围来源纠偏', exact: true })
}
async function prepare(page: Page, row: Fixture) {
  const panel = await open(page, row)
  await panel.getByRole('button', { name: '读取章纲范围依据', exact: true }).click()
  await panel.getByRole('checkbox', { name: /^P01/ }).check()
  return panel
}
async function waitTask(page: Page, id: string) {
  await expect
    .poll(async () => (await (await page.request.get(`/api/tasks/${id}`)).json()).status, { timeout: 25000 })
    .toBe('succeeded')
  return (await page.request.get(`/api/tasks/${id}`)).json()
}
const sourceSubmit = '提交AI章纲范围纠偏（额外模型费用）'
test('AI章纲范围：两个独立来源步骤后显式FLOW2正文复检，原稿与旧报告保持', async ({ page }, info) => {
  page.on('dialog', (d) => void d.accept())
  const row = await fixture(page),
    panel = await prepare(page, row)
  const before = await (await page.request.get(`/api/artifacts/${row.candidate_id}`)).json(),
    parent = await (await page.request.get(`/api/tasks/${row.parent_id}`)).json()
  await expect(panel).toContainText('来源是冻结的有效AI章纲，不是作者原话或已发生事实')
  const posted = page.waitForResponse(
    (r) => r.url().endsWith('/prepared-source-scope-corrections') && r.request().method() === 'POST',
  )
  await panel.getByRole('button', { name: sourceSubmit, exact: true }).click()
  const response = await posted
  expect(response.status(), await response.text()).toBe(202)
  const source = await waitTask(page, (await response.json()).id)
  const receipt = await (await page.request.get(`/api/artifacts/${source.result.artifact_id}`)).json()
  expect(receipt.payload.state).toBe('ready')
  expect(receipt.payload.corrected_contract.items[0].recipient_scope).toBe('unspecified')
  expect(await (await page.request.get(`/api/artifacts/${row.candidate_id}`)).json()).toEqual(before)
  await expect(panel.getByRole('region', { name: 'AI章纲范围纠偏只读回执' })).toContainText(
    '来源就绪不代表正文通过',
  )
  await panel.screenshot({ path: info.outputPath('prepared-scope-source-desktop.png') })
  const readBody = panel.getByRole('button', { name: '读取就绪回执对应的正文复检依据', exact: true })
  await expect(readBody).toBeEnabled()
  await readBody.focus()
  await readBody.press('Enter')
  const bodySubmit = panel.getByRole('button', { name: '提交独立正文复检（额外模型费用）', exact: true })
  await expect(bodySubmit).toBeEnabled()
  const bodyPosted = page.waitForResponse(
    (r) => r.url().endsWith('/recheck-source-correction') && r.request().method() === 'POST',
  )
  await bodySubmit.click()
  const bodyResponse = await bodyPosted
  expect(bodyResponse.status(), await bodyResponse.text()).toBe(202)
  const body = await waitTask(page, (await bodyResponse.json()).id)
  expect(body.result.flow_source_entailment_version).toBe('source-flow-entailment/2')
  const after = await (await page.request.get(`/api/artifacts/${row.candidate_id}`)).json()
  expect(after.payload.text).toBe(before.payload.text)
  expect(after.candidate_revision).toBe(0)
  expect(after.payload.previous_plot_audit).toEqual(before.payload.plot_audit)
  expect(after.payload.review_passed).toBe(false)
  expect((await (await page.request.get(`/api/tasks/${row.parent_id}`)).json()).result).toEqual(parent.result)
  expect((await (await page.request.get(`/api/chapters/${row.chapter_id}`)).json()).content).toBe('')
  const compare = page.getByRole('combobox', { name: '比较候选', exact: true })
  await expect(compare.locator(`option[value="${body.result.artifact_id}"]`)).toBeAttached()
  await compare.selectOption(body.result.artifact_id)
  const report = page.getByRole('region', { name: 'AI章纲范围只读记录', exact: true })
  await expect(report).toContainText('目标候选正文与修订吻合')
  await expect(report).toContainText('本轮正文复检仍有未通过或未能核实的项目')
  await expect(report).not.toContainText('目标候选已变化')
  expect(await report.getByRole('button', { name: /采纳|拒绝|保存候选/ }).count()).toBe(0)
  await page.screenshot({ path: info.outputPath('prepared-scope-body-desktop-viewport.png') })
  await page.setViewportSize({ width: 320, height: 900 })
  await assertActualDoubleText(
    page,
    {
      paragraph: report.locator('p').first(),
      button: report.getByRole('button', { name: '查看原候选', exact: true }),
      summary: report.locator('summary').first(),
    },
    info,
  )
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
  await report.locator('h3').scrollIntoViewIfNeeded()
  await page.screenshot({ path: info.outputPath('prepared-scope-body-320-2x-viewport.png') })
  await report.getByRole('button', { name: '查看原候选', exact: true }).click()
  await page
    .getByRole('textbox', { name: '候选内容', exact: true })
    .fill(after.payload.text + '\n作者未采纳编辑。')
  await page.getByRole('button', { name: '保存候选编辑（原检查失效）', exact: true }).click()
  await compare.selectOption(body.result.artifact_id)
  await expect(report).toContainText('目标候选已变化，以下仅为历史记录')
})
test('来源语义未知仍保存只读回执，不派保持检查或启用正文复检', async ({ page }) => {
  page.on('dialog', (d) => void d.accept())
  const row = await fixture(page, true),
    panel = await prepare(page, row)
  const posted = page.waitForResponse(
    (r) => r.url().endsWith('/prepared-source-scope-corrections') && r.request().method() === 'POST',
  )
  await panel.getByRole('button', { name: sourceSubmit, exact: true }).click()
  const task = await waitTask(page, (await (await posted).json()).id)
  const receipt = await (await page.request.get(`/api/artifacts/${task.result.artifact_id}`)).json()
  expect(receipt.payload.state).toBe('unverified')
  expect(receipt.payload.preservation).toBeNull()
  await expect(panel.getByRole('region', { name: 'AI章纲范围纠偏只读回执' })).toContainText(
    '没有派发独立保持核对',
  )
  await expect(
    panel.getByRole('button', { name: '读取就绪回执对应的正文复检依据', exact: true }),
  ).toBeDisabled()
})
test('未知POST刷新后只查询同nonce，404及null均不允许再次POST', async ({ page }) => {
  page.on('dialog', (d) => void d.accept())
  const row = await fixture(page)
  const panel = await prepare(page, row)
  let posts = 0,
    nonce = '',
    id = ''
  await page.route(
    `**/api/artifacts/${row.candidate_id}/prepared-source-scope-corrections`,
    async (route) => {
      if (route.request().method() !== 'POST') return route.continue()
      posts++
      nonce = route.request().postDataJSON().client_request_id
      const r = await route.fetch()
      expect(r.status()).toBe(202)
      id = (await r.json()).id
      await route.abort('failed')
    },
  )
  await panel.getByRole('button', { name: sourceSubmit, exact: true }).click()
  await expect(panel).toContainText('原提交标识')
  await page.reload()
  await page.getByRole('combobox', { name: '比较候选', exact: true }).selectOption(row.candidate_id)
  await page.getByText('核对AI章纲范围，再显式复检正文', { exact: true }).click()
  const restored = page.getByRole('region', { name: 'AI章纲范围来源纠偏', exact: true })
  let queries = 0
  await page.route(
    `**/api/artifacts/${row.candidate_id}/prepared-source-scope-corrections?*`,
    async (route) => {
      expect(new URL(route.request().url()).searchParams.get('client_request_id')).toBe(nonce)
      queries++
      if (queries === 1)
        return route.fulfill({ status: 404, json: { code: 'not_found', message: '暂未查到' } })
      if (queries === 2)
        return route.fulfill({
          json: { source_candidate_id: row.candidate_id, client_request_id: nonce, task: null },
        })
      return route.continue()
    },
  )
  const lookup = restored.getByRole('button', { name: '只查询原提交回执（不重发）', exact: true })
  await lookup.click()
  await expect(restored).toContainText('继续只查原标识')
  await lookup.click()
  await expect(restored).toContainText('保留原标识继续只读查询')
  await lookup.click()
  await expect(restored).toContainText(id)
  expect(posts).toBe(1)
  expect(queries).toBe(3)
})
test('输入法组合不提交；320实际文字2倍保留可读输入与按钮', async ({ page }, info) => {
  page.on('dialog', (d) => void d.accept())
  const row = await fixture(page),
    panel = await prepare(page, row)
  const reason = panel.getByRole('textbox', { name: '本次AI章纲范围纠偏原因（可留空）', exact: true }),
    submit = panel.getByRole('button', { name: sourceSubmit, exact: true })
  let posts = 0
  await page.route('**/prepared-source-scope-corrections', async (route) => {
    if (route.request().method() === 'POST') posts++
    return route.continue()
  })
  await reason.fill('只核接收范围，保留角色动作😀𠮷')
  await reason.dispatchEvent('compositionstart')
  await submit.click()
  expect(posts).toBe(0)
  await reason.dispatchEvent('compositionend')
  await page.setViewportSize({ width: 320, height: 900 })
  await assertActualDoubleText(
    page,
    {
      paragraph: panel.locator('p').first(),
      button: submit,
      summary: panel.locator('summary').last(),
      input: reason,
    },
    info,
  )
  await reason.scrollIntoViewIfNeeded()
  await page.screenshot({ path: info.outputPath('prepared-scope-input-320-2x-viewport.png') })
  await submit.scrollIntoViewIfNeeded()
  await page.screenshot({ path: info.outputPath('prepared-scope-submit-320-2x-viewport.png') })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
})
test('409 retains scope and audit reason until explicitly accepting a newly read basis', async ({ page }) => {
  page.on('dialog', (d) => void d.accept())
  const row = await fixture(page),
    panel = await prepare(page, row)
  const reason = panel.getByRole('textbox', { name: '本次AI章纲范围纠偏原因（可留空）', exact: true })
  await reason.fill('仅核原范围😀𠮷')
  await page.route(`**/api/artifacts/${row.candidate_id}/prepared-source-scope-corrections`, (route) =>
    route.fulfill({ status: 409, json: { code: 'prepared_scope_basis', message: '测试：旧依据变化' } }),
  )
  await panel.getByRole('button', { name: sourceSubmit, exact: true }).click()
  await expect(panel).toContainText('测试：旧依据变化')
  await expect(reason).toHaveValue('仅核原范围😀𠮷')
  await expect(panel.getByRole('checkbox', { name: /^P01/ })).toBeChecked()
  await expect(panel.getByRole('button', { name: sourceSubmit, exact: true })).toBeDisabled()
  await panel.getByRole('button', { name: '重新读取章纲范围依据', exact: true }).click()
  await expect(panel.getByRole('button', { name: sourceSubmit, exact: true })).toBeDisabled()
  await panel.getByRole('button', { name: '确认使用新依据，保留输入', exact: true }).click()
  await expect(panel.getByRole('button', { name: sourceSubmit, exact: true })).toBeEnabled()
  expect((await (await page.request.get(`/api/tasks?branch_id=${row.branch_id}`)).json()).length).toBe(1)
})
test('late source preview after switching work cannot arm a request for another candidate', async ({
  page,
}) => {
  page.on('dialog', (d) => void d.accept())
  const first = await fixture(page),
    second = await fixture(page),
    panel = await open(page, first)
  let release!: () => void
  let observed!: () => void
  const started = new Promise<void>((r) => {
    observed = r
  })
  const held = new Promise<void>((r) => {
    release = r
  })
  await page.route(`**/api/artifacts/${first.candidate_id}/prepared-source-scope-preview`, async (route) => {
    const response = await route.fetch()
    observed()
    await held
    await route.fulfill({ response })
  })
  const click = panel.getByRole('button', { name: '读取章纲范围依据', exact: true }).click()
  await started
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(second.novel_id)
  release()
  await click
  await page.getByRole('combobox', { name: '比较候选', exact: true }).selectOption(second.candidate_id)
  await page.getByText('核对AI章纲范围，再显式复检正文', { exact: true }).click()
  const current = page.getByRole('region', { name: 'AI章纲范围来源纠偏', exact: true })
  await expect(current.getByRole('button', { name: '读取章纲范围依据', exact: true })).toBeEnabled()
  await expect(current.getByRole('button', { name: sourceSubmit, exact: true })).toHaveCount(0)
  await expect(current.getByRole('checkbox')).toHaveCount(0)
})
