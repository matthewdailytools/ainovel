import { expect, test } from '@playwright/test'

test('半自动候选编辑后沿用作者原情节复检，保留原稿、历史与待审流程', async ({ page }, info) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await (
    await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 半自动候选复检' } })
  ).json()
  const response = await page.request.post('/api/workflows', {
    headers,
    data: {
      novel_id: novel.id,
      source_branch_id: novel.branch_id,
      mode: 'assisted',
      brief: '工程替身：检查闸门，保持原安排。',
      plots: [{ number: 1, title: '检查', plot: '她检查闸门，不修门。', fixed: ['不修门'] }],
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
  const workflow = await response.json()
  const readTask = async (id: string) => (await page.request.get(`/api/tasks/${id}`)).json()
  await expect
    .poll(async () => (await readTask(workflow.id)).status, { timeout: 30_000 })
    .toBe('needs_review')
  const parent = await readTask(workflow.id)
  const sourceId = parent.result.waiting_artifact as string
  const source = await (await page.request.get(`/api/artifacts/${sourceId}`)).json()
  const originalText = source.payload.text as string
  const edited = originalText + '\n候选补记：记录与推测分开。😀𠮷'
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('combobox', { name: '工作版本', exact: true }).selectOption(workflow.branch_id)
  await page.getByRole('button', { name: '写作', exact: true }).click()
  const textbox = page.getByRole('textbox', { name: '候选内容', exact: true })
  await expect(textbox).toHaveValue(originalText)
  const recheck = page.getByRole('button', { name: '重新核对候选（额外模型费用）', exact: true })
  await expect(recheck).toBeEnabled()
  await textbox.fill(edited)
  await expect(recheck).toBeDisabled()
  await page.getByRole('button', { name: '保存候选编辑（原检查失效）', exact: true }).click()
  await expect(recheck).toBeEnabled()
  await recheck.focus()
  const submitted = page.waitForResponse(
    (r) => r.url().endsWith(`/api/artifacts/${sourceId}/recheck`) && r.request().method() === 'POST',
  )
  await recheck.press('Enter')
  const receipt = await submitted
  expect(receipt.status(), await receipt.text()).toBe(202)
  const task = await receipt.json()
  await expect.poll(async () => (await readTask(task.id)).status, { timeout: 25_000 }).toBe('succeeded')
  const reportId = (await readTask(task.id)).result.artifact_id as string
  const report = await (await page.request.get(`/api/artifacts/${reportId}`)).json()
  expect(report.payload.plot_audit.source_kind).toBe('author_plot')
  expect(report.payload.plot_audit.items).toEqual(source.payload.plot_audit.items)
  const after = await readTask(workflow.id)
  expect(after.status).toBe('needs_review')
  expect(after.payload).toEqual(parent.payload)
  expect(after.result).toEqual(parent.result)
  expect((await (await page.request.get(`/api/chapters/${source.chapter_id}`)).json()).content).toBe('')
  const history = await (await page.request.get(`/api/artifacts/${sourceId}/history`)).json()
  expect(
    history.items.find((x: { kind: string }) => x.kind === 'candidate_edited').payload.previous_candidate
      .text,
  ).toBe(originalText)
  await page.getByRole('combobox', { name: '比较候选', exact: true }).selectOption(reportId)
  const panel = page.getByRole('region', { name: '候选复检报告', exact: true })
  await expect(panel).toContainText('模型标复检通过，仍须作者核对。')
  await page.setViewportSize({ width: 320, height: 800 })
  await page.evaluate(() => (document.documentElement.style.fontSize = '200%'))
  const target = panel.getByRole('button', { name: '查看目标候选', exact: true })
  await target.focus()
  await expect(target).toBeFocused()
  await target.press('Enter')
  await expect(textbox).toHaveValue(edited)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
  await page.screenshot({ path: info.outputPath('assisted-recheck-320-200.png'), fullPage: true })
})
