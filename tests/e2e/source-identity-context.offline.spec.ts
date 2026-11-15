import { expect, test } from '@playwright/test'
import { assertActualDoubleText } from './helpers/actualTextScale'

const source = 'source-identity-e2e：沈砚从渡口一位修船匠借得旧尺，约定后日归还。'
const version = 'source-identity-context/1'
test('补写可主动核对原角色限定，独立报告保留原解释与原稿', async ({ page }, info) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await (
    await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 原话角色限定' } })
  ).json()
  const chapter = await (
    await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
      headers,
      data: { title: '作者保留原稿' },
    })
  ).json()
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '写作', exact: true }).click()
  await page.getByRole('combobox', { name: '这次要做什么', exact: true }).selectOption('writing.expand_plot')
  const option = page.getByRole('checkbox', { name: '按原话补充角色限定', exact: true })
  await expect(option).toBeDisabled()
  await page.getByRole('checkbox', { name: '额外逐项核对本次情节', exact: true }).check()
  await option.check()
  await page.getByRole('textbox', { name: '情节或具体要求', exact: true }).fill(source)
  const posted = page.waitForResponse(
    (r) => r.url().endsWith('/api/modules') && r.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '生成候选 →', exact: true }).click()
  const response = await posted
  expect(response.status(), await response.text()).toBe(202)
  const task = await response.json()
  expect(task.payload.source_identity_context).toBe(true)
  expect(task.result.source_identity_context_version).toBe(version)
  expect(task.result.flow_source_entailment_version).toBe('source-flow-entailment/1')
  await expect
    .poll(async () => (await (await page.request.get(`/api/tasks/${task.id}`)).json()).status, {
      timeout: 25000,
    })
    .toBe('succeeded')
  const finished = await (await page.request.get(`/api/tasks/${task.id}`)).json()
  const artifact = await (await page.request.get(`/api/artifacts/${finished.result.artifact_id}`)).json()
  const audit = artifact.payload.plot_audit
  expect(audit.items[0].supplier).toBe('修船匠')
  expect(audit.direction_guard.entities.E01).toBe('渡口一位修船匠')
  expect(audit.status).toBe('model_pass')
  await page.getByText('情节逐项核对 · 模型标一致，仍须作者核对', { exact: true }).click()
  const details = page.getByRole('region', { name: '原话角色限定核对', exact: true })
  await expect(details).toContainText('渡口一位修船匠')
  expect(audit.direction_guard.source_entailment.version).toBe('source-flow-entailment/1')
  expect(audit.direction_guard.source_entailment.checks).toHaveLength(1)
  const flowSummary = page.getByText('正文流向观察的出处复核 · 1项', { exact: true })
  await flowSummary.focus()
  await flowSummary.press('Enter')
  const flowDetails = flowSummary.locator('..')
  await expect(flowDetails).toContainText('原文支持')
  await expect(flowDetails).toContainText('渡口一位修船匠')
  await expect(flowDetails).toContainText('原观察引用')
  await expect(flowDetails).toContainText('出处复核')
  await expect(flowDetails).toContainText('来源未知不等于正文违约')
  await flowDetails.screenshot({ path: info.outputPath('source-flow-desktop.png') })
  await page.setViewportSize({ width: 320, height: 800 })
  await assertActualDoubleText(
    page,
    {
      paragraph: flowDetails.locator('p').first(),
      quote: flowDetails.locator('blockquote').first(),
      small: flowDetails.locator('p.small').first(),
      summary: flowSummary,
      button: page.getByRole('button', { name: '保存', exact: true }),
    },
    info,
  )
  await expect(details).toBeVisible()
  await expect(flowDetails).toBeVisible()
  await flowDetails.screenshot({ path: info.outputPath('source-flow-320-200.png') })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
  await page.screenshot({ path: info.outputPath('source-identity-320-200.png'), fullPage: true })
  expect((await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content).toBe('')
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const object = (value: unknown): value is Record<string, unknown> =>
    !!value && typeof value === 'object' && !Array.isArray(value)
  const malformed = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(malformed)
    if (!object(value) || value.id !== artifact.id || !object(value.payload)) return value
    const plot = value.payload.plot_audit
    if (!object(plot) || !object(plot.direction_guard) || !object(plot.direction_guard.source_entailment))
      return value
    return {
      ...value,
      payload: {
        ...value.payload,
        plot_audit: {
          ...plot,
          direction_guard: {
            ...plot.direction_guard,
            source_entailment: {
              ...plot.direction_guard.source_entailment,
              status: { toString: null },
            },
          },
        },
      },
    }
  }
  await page.route(/\/api\/artifacts(?:\/|\?)/, async (route) => {
    if (route.request().method() !== 'GET') return route.continue()
    const response = await route.fetch()
    await route.fulfill({ response, json: malformed(await response.json()) })
  })
  await page.reload()
  await page.getByRole('combobox', { name: '比较候选', exact: true }).selectOption(artifact.id)
  const invalid = page.getByRole('region', { name: '情节逐项核对', exact: true })
  await expect(invalid).toContainText('情节核对报告格式无法核对，不能视为一致；原始报告仍保留。')
  await expect(page.getByRole('textbox', { name: '候选内容', exact: true })).toBeEditable()
  // Finish in-flight polling handlers before Playwright disposes their responses.
  await page.unrouteAll({ behavior: 'wait' })
  expect(errors).toEqual([])
  // The corrupted JSON is only a browser fixture; stored candidate/report remain intact.
  const unchanged = await (await page.request.get(`/api/artifacts/${artifact.id}`)).json()
  expect(unchanged.payload.plot_audit.direction_guard.source_entailment.status).toBe('supported')
})

for (const mode of ['assisted', 'automatic'] as const) {
  test(`${mode}待审候选可选原话角色核对，旧解释、父任务与原稿保持`, async ({ page }) => {
    await page.goto('/')
    const session = await (await page.request.get('/api/session')).json()
    const headers = { 'X-Ainovel-CSRF': session.csrf }
    const novel = await (
      await page.request.post('/api/novels', { headers, data: { title: `后台验证 · ${mode}限定角色` } })
    ).json()
    const response = await page.request.post('/api/workflows', {
      headers,
      data: {
        novel_id: novel.id,
        source_branch_id: novel.branch_id,
        mode,
        brief: 'source-identity-e2e：只完成本批借尺，不提前收束后续。',
        plots: mode === 'assisted' ? [{ number: 1, title: '借尺', plot: source, fixed: [] }] : [],
        chapters: 1,
        min_chars: 200,
        max_chars: 600,
        account: 'offline',
        adoption: 'manual',
        completion_scope: 'segment',
        plot_contract_check: true,
      },
    })
    expect(response.status(), await response.text()).toBe(202)
    const queued = await response.json()
    const readTask = async (id: string) => (await page.request.get(`/api/tasks/${id}`)).json()
    await expect.poll(async () => (await readTask(queued.id)).status, { timeout: 30000 }).toBe('needs_review')
    const parent = await readTask(queued.id)
    const aid = parent.result.waiting_artifact as string
    const original = await (await page.request.get(`/api/artifacts/${aid}`)).json()
    expect(original.payload.plot_audit.status).toBe('unverified')
    await page.reload()
    await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
    await page.getByRole('combobox', { name: '工作版本', exact: true }).selectOption(parent.branch_id)
    await page.getByRole('button', { name: '写作', exact: true }).click()
    await page.getByRole('combobox', { name: '比较候选', exact: true }).selectOption(aid)
    const panel = page.getByRole('region', { name: '重新核对候选', exact: true })
    await panel.getByRole('checkbox', { name: '按原话补充角色限定', exact: true }).check()
    const posted = page.waitForResponse(
      (r) => r.url().endsWith(`/api/artifacts/${aid}/recheck`) && r.request().method() === 'POST',
    )
    await panel.getByRole('button', { name: '重新核对候选（额外模型费用）', exact: true }).click()
    const receipt = await posted
    expect(receipt.status(), await receipt.text()).toBe(202)
    const task = await receipt.json()
    expect(task.payload.source_identity_context).toBe(true)
    expect(task.result.source_identity_context_version).toBe(version)
    await expect.poll(async () => (await readTask(task.id)).status, { timeout: 25000 }).toBe('succeeded')
    const after = await (await page.request.get(`/api/artifacts/${aid}`)).json()
    expect(after.payload.text).toBe(original.payload.text)
    expect(after.candidate_revision).toBe(original.candidate_revision)
    expect(after.payload.plot_audit.items).toEqual(original.payload.plot_audit.items)
    expect(after.payload.plot_audit.direction_guard.source_identity_context.entities.E01).toBe(
      '渡口一位修船匠',
    )
    expect(after.payload.plot_audit.status).toBe('model_pass')
    expect((await readTask(parent.id)).result).toEqual(parent.result)
    expect((await readTask(parent.id)).status).toBe('needs_review')
    expect((await (await page.request.get(`/api/chapters/${after.chapter_id}`)).json()).content).toBe('')
  })
}
