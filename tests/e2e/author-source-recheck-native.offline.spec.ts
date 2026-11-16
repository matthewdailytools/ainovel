import { expect, test, type Page } from '@playwright/test'
import { object } from '../../frontend/planRestart'
const SUMMARY = '同一作者原话，按新版范围政策重新核对',
  READ = '读取同原话核对依据',
  SUBMIT = '创建并开始同原话新版核对子任务',
  CONFIRM = '我已核对逐字原话、后续模型费用和原采纳策略，确认开始独立子任务',
  LOOKUP = '只查询原同原话核对回执'
type Fixture = { novel_id: string; branch_id: string; source_id: string }
async function fixture(page: Page, mode = 'ready'): Promise<Fixture> {
  const session = await (await page.request.get('/api/session')).json()
  const response = await page.request.post('/test/author-source-recheck/fixtures', {
    headers: { 'X-Ainovel-CSRF': session.csrf },
    data: { client_request_id: crypto.randomUUID(), mode },
  })
  expect(response.status(), await response.text()).toBe(201)
  return response.json()
}
async function open(page: Page, row: Fixture) {
  await page.goto('/')
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(row.novel_id)
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  await page.getByRole('combobox', { name: '本作品批次', exact: true }).selectOption(row.source_id)
  const summary = page.getByText(SUMMARY, { exact: true }),
    details = summary.locator('..')
  if ((await details.getAttribute('open')) === null) await summary.click()
  return page.getByRole('region', { name: '同原话新版核对', exact: true })
}
async function prepare(page: Page, row: Fixture) {
  const panel = await open(page, row)
  await panel.getByRole('button', { name: READ, exact: true }).click()
  await panel
    .getByRole('textbox', { name: '本次同原话核对原因（必填，仅记录，不作为情节）', exact: true })
    .fill('核对原话分类，不补写新剧情。')
  await expect(panel.getByRole('checkbox', { name: CONFIRM, exact: true })).not.toBeChecked()
  await expect(panel.getByRole('button', { name: SUBMIT, exact: true })).toBeDisabled()
  await panel.getByRole('checkbox', { name: CONFIRM, exact: true }).check()
  return panel
}
async function submit(page: Page, panel: ReturnType<Page['getByRole']>) {
  const response = page.waitForResponse(
    (r) => r.url().endsWith('/author-source-rechecks') && r.request().method() === 'POST',
  )
  await panel.getByRole('button', { name: SUBMIT, exact: true }).click()
  const posted = await response
  expect(posted.status(), await posted.text()).toBe(202)
  expect(Object.keys(posted.request().postDataJSON()).sort()).toEqual([
    'client_request_id',
    'preview_basis_sha256',
    'reason',
    'version',
  ])
  return posted.json()
}
async function terminal(page: Page, id: string, status: string) {
  await expect
    .poll(async () => (await (await page.request.get(`/api/tasks/${id}`)).json()).status, { timeout: 30000 })
    .toBe(status)
  return (await page.request.get(`/api/tasks/${id}`)).json()
}

test('真实API/Worker重新三步核原话，保持旧来源、规划、原稿和累计费用', async ({ page }, info) => {
  page.on('dialog', (dialog) => void dialog.accept())
  const row = await fixture(page),
    panel = await prepare(page, row)
  await panel
    .getByRole('textbox', { name: '本次同原话核对原因（必填，仅记录，不作为情节）', exact: true })
    .fill('旧观察误标，原话不补写')
  await panel.locator('pre.report').first().scrollIntoViewIfNeeded()
  await page.screenshot({ path: info.outputPath('same-source-readonly-desktop.png') })
  const created = await submit(page, panel),
    child = await terminal(page, created.id, 'needs_review')
  expect(child.result.authored_source_scope_version).toBe('authored-source-scope/2')
  const record = child.result.authored_source_scope_receipts['chapter:1']
  expect(record.state).toBe('ready')
  expect(Object.keys(record.phases)).toEqual(['observe', 'interpret', 'preserve'])
  expect(record.contract.items).toHaveLength(7)
  const evidence = await (
    await page.request.get(`/test/author-source-recheck/fixtures/${row.source_id}`)
  ).json()
  expect(evidence.actions[child.id].slice(0, 3)).toEqual([
    '独立观察作者情节接收范围来源',
    '仅依据作者原话整理本场景情节',
    '独立核对作者来源分类与完整原意保持',
  ])
  expect(evidence.actions[child.id].filter((action: string) => action === '按章纲写完整正文')).toHaveLength(1)
  expect(evidence.after.payload).toEqual(evidence.before.payload)
  expect({ ...evidence.after.result, continued_to: undefined }).toMatchObject(evidence.before.result)
  expect(child.result.plan).toEqual(evidence.before.result.plan)
  expect(child.result.author_source_recheck.source_entry).toEqual(
    child.result.author_source_recheck.previous_source_entry,
  )
  expect(child.result.author_source_recheck.previous_source_receipt).toEqual(
    evidence.before.result.authored_source_scope_receipts['chapter:1'],
  )
  const chapters = await (await page.request.get(`/api/branches/${row.branch_id}/chapters`)).json()
  expect(chapters).toHaveLength(1)
  const manuscripts = await Promise.all(
    chapters.map(async (chapter: { id: string }) => {
      const response = await page.request.get(`/api/chapters/${encodeURIComponent(chapter.id)}`)
      expect(response.status()).toBe(200)
      return response.json()
    }),
  )
  expect(
    manuscripts.every(
      (chapter: { revision: number; content: string }) => chapter.revision === 0 && chapter.content === '',
    ),
  ).toBe(true)
  await expect(page.getByRole('region', { name: '同原话新版核对回执', exact: true })).toBeVisible()
  const sourceRecord = page.getByRole('region', { name: '本任务新版来源核对', exact: true })
  await sourceRecord.getByText('本任务新版来源观察、全部情节解释与独立保持（只读）', { exact: true }).click()
  await expect(sourceRecord).toContainText('classification_result')
  await expect(sourceRecord).toContainText('authored-source-scope/2')
})

for (const [mode, phases] of [
  ['ambiguous', 2],
  ['preserve_unknown', 3],
  ['classification_unknown', 3],
] as const) {
  test(`真实${mode}停点不就绪、不派正文；新版不再快捷重核`, async ({ page }) => {
    page.on('dialog', (dialog) => void dialog.accept())
    const row = await fixture(page, mode),
      panel = await prepare(page, row),
      created = await submit(page, panel),
      child = await terminal(page, created.id, 'blocked_runtime')
    expect(child.result.authored_source_scope_receipts['chapter:1'].state).toBe('unverified')
    expect(Object.keys(child.result.authored_source_scope_receipts['chapter:1'].phases)).toHaveLength(phases)
    expect(child.result.waiting_artifact).toBeUndefined()
    const evidence = await (
      await page.request.get(`/test/author-source-recheck/fixtures/${row.source_id}`)
    ).json()
    expect(evidence.actions[child.id]).toHaveLength(phases)
    await page.getByRole('combobox', { name: '本作品批次', exact: true }).selectOption(child.id)
    const sourceRecord = page.getByRole('region', { name: '本任务新版来源核对', exact: true })
    await expect(sourceRecord).toContainText('新版来源仍在核对或待核')
    await sourceRecord
      .getByText('本任务新版来源观察、全部情节解释与独立保持（只读）', { exact: true })
      .click()
    await expect(sourceRecord).toContainText('authored-source-scope/2')
    if (phases === 3) await expect(sourceRecord).toContainText('classification_result')
    await page.getByText(SUMMARY, { exact: true }).click()
    await expect(
      page
        .getByRole('region', { name: '同原话新版核对', exact: true })
        .getByRole('button', { name: READ, exact: true }),
    ).toBeDisabled()
    const clarification = page.getByText('补充作者情节来源，保留原话与费用', { exact: true }).locator('..')
    await clarification.locator('summary').click()
    await clarification.getByRole('button', { name: '读取来源补充依据', exact: true }).click()
    await expect(
      clarification.getByRole('textbox', { name: '本章补充说明（作者）', exact: true }),
    ).toBeVisible()
  })
}

test('真实子任务已建但响应丢失，刷新后404/null只查原nonce', async ({ page }) => {
  page.on('dialog', (dialog) => void dialog.accept())
  const row = await fixture(page),
    panel = await prepare(page, row)
  let posts = 0,
    nonce = '',
    childId = ''
  await page.route(`**/api/tasks/${row.source_id}/author-source-rechecks`, async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    posts++
    nonce = route.request().postDataJSON().client_request_id
    const response = await route.fetch()
    expect(response.status()).toBe(202)
    childId = (await response.json()).id
    await route.abort('connectionreset')
  })
  await panel.getByRole('button', { name: SUBMIT, exact: true }).click()
  await expect(panel).toContainText('提交结果尚未核实')
  const restored = await open(page, row)
  let lookups = 0
  await page.route(`**/api/tasks/${row.source_id}/author-source-rechecks?*`, async (route) => {
    expect(new URL(route.request().url()).searchParams.get('client_request_id')).toBe(nonce)
    lookups++
    if (lookups === 1) return route.fulfill({ status: 404, json: { code: 'missing', message: '尚未查到' } })
    if (lookups === 2)
      return route.fulfill({ json: { source_task_id: row.source_id, client_request_id: nonce, task: null } })
    return route.continue()
  })
  await restored.getByRole('button', { name: LOOKUP, exact: true }).click()
  await expect(restored).toContainText('尚未查到')
  await restored.getByRole('button', { name: LOOKUP, exact: true }).click()
  await expect(restored).toContainText('不代表从未创建')
  await restored.getByRole('button', { name: LOOKUP, exact: true }).click()
  await expect(page.getByRole('combobox', { name: '本作品批次', exact: true })).toHaveValue(childId)
  expect(posts).toBe(1)
  expect(lookups).toBe(3)
  const child = await terminal(page, childId, 'needs_review')
  expect(object(child.result)?.author_source_recheck_version).toBe('author-source-recheck/1')
})

test('新自动批次正式API使用/3，独立分类保持可读且无作者原话快捷升级入口', async ({ page }, info) => {
  page.on('dialog', (dialog) => void dialog.accept())
  const session = await (await page.request.get('/api/session')).json(),
    headers = { 'X-Ainovel-CSRF': session.csrf }
  const createdNovel = await page.request.post('/api/novels', {
    headers,
    data: { title: '后台验证 · 新自动章纲分类', brief: '原创工程替身，不作文学评估。' },
  })
  expect(createdNovel.status()).toBe(201)
  const novel = await createdNovel.json()
  const queued = await page.request.post('/api/workflows', {
    headers,
    data: {
      novel_id: novel.id,
      mode: 'automatic',
      completion_scope: 'segment',
      brief: 'auto-scope-classification-e2e',
      chapters: 1,
      min_chars: 200,
      max_chars: 600,
      adoption: 'manual',
      account: 'offline',
      plot_contract_check: true,
      client_request_id: crypto.randomUUID(),
    },
  })
  expect(queued.status(), await queued.text()).toBe(202)
  const task = await terminal(page, (await queued.json()).id, 'needs_review'),
    record = task.result.plot_preparations['1']
  expect(record.workflow_plan_recipient_scope_version).toBe('workflow-plan-recipient-scope/3')
  expect(record.scope_classification_preservations).toHaveLength(1)
  expect(record.scope_classification_preservations[0].result.constraints_check.result).toBe('pass')
  expect(record.phases['scope_preserve:0'].status).toBe('completed')
  await page.goto('/')
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  await page.getByRole('combobox', { name: '本作品批次', exact: true }).selectOption(task.id)
  await expect(page.getByRole('region', { name: '同原话新版核对', exact: true })).toHaveCount(0)
  const preparation = page.getByText('AI工作章纲执行记录', { exact: true }).locator('..')
  await preparation.locator(':scope > summary').click()
  await preparation
    .locator('summary')
    .filter({ hasText: /^第1章 · 已冻结执行安排/ })
    .click()
  const classification = preparation.getByRole('region', { name: 'AI章纲来源分类记录', exact: true })
  await classification.getByText('新版AI章纲来源分类与完整原意保持（只读）', { exact: true }).click()
  await expect(classification).toContainText('workflow-plan-recipient-scope/3')
  await expect(classification).toContainText('classification_result')
  await expect(classification).toContainText('source_observations')
  await expect(classification).toContainText('不是作者原话或已发生事实')
  await classification.scrollIntoViewIfNeeded()
  await page.screenshot({ path: info.outputPath('auto3-classification-readonly-desktop.png') })
})
