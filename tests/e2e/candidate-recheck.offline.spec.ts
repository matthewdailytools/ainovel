import { expect, test } from '@playwright/test'
import { createHash } from 'node:crypto'

test('旧列表到达后重新选择读取已保存候选，另一窗口变更仍保留未保存编辑', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await (
    await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 候选读取版本' } })
  ).json()
  const response = await page.request.post('/api/workflows', {
    headers,
    data: {
      novel_id: novel.id,
      source_branch_id: novel.branch_id,
      mode: 'automatic',
      brief: 'plan-preparation-e2e：只测量记录，疑点仍待查证。',
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
  const stopped = await readTask(workflow.id)
  const sourceId = stopped.result.waiting_artifact as string
  const source = await (await page.request.get(`/api/artifacts/${sourceId}`)).json()
  const recheckResponse = await page.request.post(`/api/artifacts/${sourceId}/recheck`, {
    headers,
    data: {
      candidate_revision: source.candidate_revision,
      account: 'offline',
      client_request_id: crypto.randomUUID(),
    },
  })
  expect(recheckResponse.status(), await recheckResponse.text()).toBe(202)
  const task = await recheckResponse.json()
  await expect.poll(async () => (await readTask(task.id)).status, { timeout: 25_000 }).toBe('succeeded')
  const reportId = (await readTask(task.id)).result.artifact_id as string
  const listUrl = `/api/artifacts?branch_id=${workflow.branch_id}`
  const staleList = await (await page.request.get(listUrl)).json()
  const savedText = source.payload.text + '\n最新保存：记录与推测分开。😀𠮷'
  const saved = await page.request.patch(`/api/artifacts/${sourceId}`, {
    headers,
    data: {
      text: savedText,
      candidate_revision: source.candidate_revision,
    },
  })
  expect(saved.ok(), await saved.text()).toBeTruthy()
  const savedCandidate = await saved.json()
  let freeze = true,
    staleReads = 0
  await page.route(new RegExp(`/api/artifacts\\?branch_id=${workflow.branch_id}$`), async (route) => {
    if (!freeze) return route.continue()
    staleReads++
    await route.fulfill({ json: staleList })
  })
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('combobox', { name: '工作版本', exact: true }).selectOption(workflow.branch_id)
  await page.getByRole('button', { name: '写作', exact: true }).click()
  const options = page.getByRole('combobox', { name: '比较候选', exact: true })
  await options.selectOption(reportId)
  await expect(page.getByText('历史报告：目标候选已变化，不能作为当前结论。', { exact: true })).toBeVisible()
  await options.selectOption(sourceId)
  const candidate = page.getByRole('textbox', { name: '候选内容', exact: true })
  await expect(candidate).toHaveValue(savedText)
  const reads = staleReads
  await expect.poll(() => staleReads).toBeGreaterThan(reads)
  await expect(candidate).toHaveValue(savedText)
  const recheck = page.getByRole('button', { name: '重新核对候选（额外模型费用）', exact: true })
  await expect(recheck).toBeEnabled()

  const localDraft = savedText + '\n本窗口尚未保存的修改。'
  await candidate.fill(localDraft)
  const remoteText = savedText + '\n另一窗口已保存的修改。'
  const remote = await page.request.patch(`/api/artifacts/${sourceId}`, {
    headers,
    data: {
      candidate_revision: savedCandidate.candidate_revision,
      text: remoteText,
    },
  })
  expect(remote.ok(), await remote.text()).toBeTruthy()
  freeze = false
  await expect(page.getByRole('button', { name: '载入已保存候选', exact: true })).toBeVisible()
  await expect(candidate).toHaveValue(localDraft)
  await expect(recheck).toBeDisabled()
  expect((await (await page.request.get(`/api/chapters/${source.chapter_id}`)).json()).content).toBe('')
})

test('候选编辑、同契约复检、再次复检与历史回看，作者采用后明确续跑', async ({ page }, info) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await (
    await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 候选复检与历史' } })
  ).json()
  const original = await (
    await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
      headers,
      data: { title: '作者保留原章' },
    })
  ).json()
  await page.request.put(`/api/chapters/${original.id}`, {
    headers,
    data: { base_revision: 0, content: '原作品正文保留😀𠮷。' },
  })
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  await page.getByRole('radio', { name: /^全自动/ }).check()
  await page.getByRole('combobox', { name: '本次任务范围', exact: true }).selectOption('segment')
  await page.getByLabel('作品设定、粗纲与结局方向').fill('plan-preparation-e2e：只测量记录，疑点仍待查证。')
  await page.getByLabel('计划章数', { exact: true }).fill('1')
  await page.getByLabel('每章字数下限').fill('200')
  await page.getByLabel('每章字数上限').fill('600')
  await page.getByRole('combobox', { name: '采纳政策', exact: true }).selectOption('manual')
  await page.getByRole('checkbox', { name: '额外逐项核对本次情节', exact: true }).check()
  const workflowResponse = page.waitForResponse(
    (r) => r.url().endsWith('/api/workflows') && r.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '创建独立版本并启动', exact: true }).click()
  const workflow = await (await workflowResponse).json()
  const readTask = async (id: string) => (await page.request.get(`/api/tasks/${id}`)).json()
  await expect
    .poll(async () => (await readTask(workflow.id)).status, { timeout: 30_000 })
    .toBe('needs_review')
  const stopped = await readTask(workflow.id)
  const sourceId = stopped.result.waiting_artifact as string
  const source = await (await page.request.get(`/api/artifacts/${sourceId}`)).json()
  const originalCandidate = source.payload.text as string
  const edited = originalCandidate + '\n候选补记：原始记录与待验证推测分开。😀𠮷'
  await page.getByRole('button', { name: '写作', exact: true }).click()
  await page.getByRole('combobox', { name: '工作版本', exact: true }).selectOption(workflow.branch_id)
  await expect(page.getByRole('textbox', { name: '候选内容', exact: true })).toHaveValue(originalCandidate)
  const recheck = page.getByRole('button', { name: '重新核对候选（额外模型费用）', exact: true })
  await page.getByRole('textbox', { name: '候选内容', exact: true }).fill(edited)
  await expect(recheck).toBeDisabled()
  await page.getByRole('button', { name: '保存候选编辑（原检查失效）', exact: true }).click()
  await expect(recheck).toBeEnabled()

  const requests: { candidate_revision: number; client_request_id: string }[] = []
  async function checkCandidate() {
    const response = page.waitForResponse(
      (r) => r.url().endsWith(`/api/artifacts/${sourceId}/recheck`) && r.request().method() === 'POST',
    )
    await expect(recheck).toBeEnabled()
    await recheck.click()
    const submitted = await response
    expect(submitted.status(), await submitted.text()).toBe(202)
    requests.push(submitted.request().postDataJSON())
    const task = await submitted.json()
    await expect.poll(async () => (await readTask(task.id)).status, { timeout: 25_000 }).toBe('succeeded')
    const result = await readTask(task.id)
    const report = await (await page.request.get(`/api/artifacts/${result.result.artifact_id}`)).json()
    const panel = page.getByRole('region', { name: '候选复检报告', exact: true })
    await expect(panel.getByText('模型标复检通过，仍须作者核对。', { exact: true })).toBeVisible()
    await expect(panel.getByRole('button', { name: /采纳|拒绝|恢复/ })).toHaveCount(0)
    expect((await readTask(workflow.id)).status).toBe('needs_review')
    expect((await (await page.request.get(`/api/chapters/${source.chapter_id}`)).json()).content).toBe('')
    return { report, panel }
  }

  const first = await checkCandidate()
  const firstBytes = JSON.stringify(first.report)
  await page.setViewportSize({ width: 320, height: 800 })
  await page.evaluate(() => (document.documentElement.style.fontSize = '200%'))
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
  await first.panel.getByText('复检前候选与检查记录', { exact: true }).click()
  await expect(first.panel.locator('pre.report').first()).toContainText('previous_review')
  await page.screenshot({ path: info.outputPath('candidate-recheck-320-200.png'), fullPage: true })
  const target = first.panel.getByRole('button', { name: '查看目标候选', exact: true })
  await target.focus()
  await expect(target).toBeFocused()
  await target.press('Enter')
  await expect(page.getByRole('textbox', { name: '候选内容', exact: true })).toHaveValue(edited)
  const history = page.getByText('候选编辑与复检历史', { exact: true }).locator('..')
  await history.locator(':scope > summary').click()
  await expect(history).toContainText('候选编辑记录')
  await expect(history).toContainText('候选复检记录')
  const events = await (await page.request.get(`/api/artifacts/${sourceId}/history`)).json()
  expect(
    events.items.find((r: { kind: string }) => r.kind === 'candidate_edited').payload.previous_candidate.text,
  ).toBe(originalCandidate)
  await page.evaluate(() => (document.documentElement.style.fontSize = ''))
  await page.setViewportSize({ width: 1280, height: 900 })
  const second = await checkCandidate()
  expect(requests[0].candidate_revision).toBe(1)
  expect(requests[1].candidate_revision).toBe(1)
  expect(requests[1].client_request_id).not.toBe(requests[0].client_request_id)
  expect(JSON.stringify(await (await page.request.get(`/api/artifacts/${first.report.id}`)).json())).toBe(
    firstBytes,
  )
  expect(
    (
      await page.request.post(`/api/artifacts/${second.report.id}/accept`, {
        headers,
        data: { idempotency_key: 'do-not-adopt-report' },
      })
    ).status(),
  ).toBe(409)

  await second.panel.getByRole('button', { name: '查看目标候选', exact: true }).click()
  const newer = edited + '\n再核对一句：不把推测写成事实。'
  await page.getByRole('textbox', { name: '候选内容', exact: true }).fill(newer)
  await page.getByRole('button', { name: '保存候选编辑（原检查失效）', exact: true }).click()
  await page.getByRole('combobox', { name: '比较候选', exact: true }).selectOption(second.report.id)
  await expect(
    second.panel.getByText('历史报告：目标候选已变化，不能作为当前结论。', { exact: true }),
  ).toBeVisible()
  await page.getByRole('combobox', { name: '比较候选', exact: true }).selectOption(sourceId)
  await checkCandidate()
  await page.getByRole('button', { name: '查看目标候选', exact: true }).click()
  await page.getByRole('button', { name: '采纳到正文', exact: true }).click()
  await expect
    .poll(async () => (await (await page.request.get(`/api/chapters/${source.chapter_id}`)).json()).content)
    .toBe(newer)
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  const firstResume = page.waitForResponse(
    (r) => r.url().endsWith(`/api/tasks/${workflow.id}/resume`) && r.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '从保存的检查点恢复', exact: true }).click()
  expect((await firstResume).ok()).toBeTruthy()
  await expect
    .poll(async () => typeof (await readTask(workflow.id)).result.waiting_structure, { timeout: 25_000 })
    .toBe('string')
  const structureId = (await readTask(workflow.id)).result.waiting_structure
  expect(typeof structureId).toBe('string')
  await page.getByRole('button', { name: '打开此批次的结构审阅', exact: true }).click()
  await page.getByText('按保存稿整理人物、事件、状态和伏笔候选', { exact: true }).click()
  const structure = page.getByRole('region', { name: '结构化整理与确认', exact: true })
  await structure.getByRole('combobox', { name: '保存的结构候选', exact: true }).selectOption(structureId)
  await expect(structure.getByRole('checkbox', { name: /^采用人物：/ })).toHaveCount(1)
  for (const box of await structure.getByRole('checkbox', { name: /^采用/ }).all()) await box.check()
  await structure.getByRole('button', { name: '确认采用所选结构（不修改正文）', exact: true }).click()
  await expect
    .poll(async () => (await (await page.request.get(`/api/artifacts/${structureId}`)).json()).status)
    .toBe('accepted')
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  const secondResume = page.waitForResponse(
    (r) => r.url().endsWith(`/api/tasks/${workflow.id}/resume`) && r.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '从保存的检查点恢复', exact: true }).click()
  expect((await secondResume).ok()).toBeTruthy()
  await expect
    .poll(async () => (await readTask(workflow.id)).status, { timeout: 25_000 })
    .toBe('segment_complete')
  expect((await (await page.request.get(`/api/chapters/${original.id}`)).json()).content).toBe(
    '原作品正文保留😀𠮷。',
  )
  expect(second.report.payload.target.content_sha256).toBe(createHash('sha256').update(edited).digest('hex'))
  await info.attach('candidate-recheck-summary.json', {
    body: JSON.stringify({
      workflow: workflow.id,
      source: sourceId,
      report_ids: [first.report.id, second.report.id],
      requests,
    }),
    contentType: 'application/json',
  })
})
