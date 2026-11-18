import { expect, test, type Page } from '@playwright/test'

async function source(page: Page, label: string, plotCheck = false, variant = '') {
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await (
    await page.request.post('/api/novels', { headers, data: { title: `后台验证 · 记忆选择 ${label}` } })
  ).json()
  const response = await page.request.post('/api/workflows', {
    headers,
    data: {
      novel_id: novel.id,
      mode: 'assisted',
      brief: '记忆选择工程样例，未核定时点必须由作者决定。',
      plots: [
        { number: 1, title: '检查', plot: `memory-selection-e2e${variant}：她检查闸门，不修门。`, fixed: [] },
      ],
      chapters: 1,
      min_chars: 200,
      max_chars: 600,
      account: 'offline',
      adoption: 'manual',
      completion_scope: 'segment',
      structured_maintenance: false,
      scene_maintenance: false,
      plot_contract_check: plotCheck,
    },
  })
  expect(response.status(), await response.text()).toBe(202)
  const workflow = await response.json()
  const read = async () => (await page.request.get(`/api/tasks/${workflow.id}`)).json()
  await expect.poll(async () => (await read()).status, { timeout: 30_000 }).toBe('needs_review')
  const candidate = await (
    await page.request.get(`/api/artifacts/${(await read()).result.waiting_artifact}`)
  ).json()
  expect(candidate.payload.memory.facts).toHaveLength(variant === '-empty-initial' ? 0 : 3)
  expect(candidate.memory_facts_sha256).toMatch(/^[a-f0-9]{64}$/)
  return { novel, workflow, candidate, headers }
}
async function open(page: Page, row: Awaited<ReturnType<typeof source>>, view = '写作') {
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(row.novel.id)
  await page.getByRole('combobox', { name: '工作版本', exact: true }).selectOption(row.workflow.branch_id)
  await page.getByRole('button', { name: view, exact: true }).click()
  if (view === '写作')
    await page.getByRole('combobox', { name: '比较候选', exact: true }).selectOption(row.candidate.id)
  const panel = page.getByRole('region', { name: '正文附带的记忆候选', exact: true })
  if (!row.candidate.payload.memory.facts.length) {
    if (view === '写作')
      await expect(page.getByRole('textbox', { name: '候选内容', exact: true })).toHaveValue(
        row.candidate.payload.text,
      )
    else await expect(page.getByRole('heading', { name: '待审章节候选', exact: true })).toBeVisible()
    await expect(panel).toHaveCount(0)
    return panel
  }
  const summary = panel.locator(':scope > details > summary')
  await summary.focus()
  await summary.press('Enter')
  await expect(panel.getByRole('checkbox')).toHaveCount(3)
  for (const checkbox of await panel.getByRole('checkbox').all()) await expect(checkbox).toBeChecked()
  return panel
}

test('写作按原时点逐项采纳：遗漏需理由、失败同nonce重试、恢复后仍可查回执', async ({ page }, info) => {
  await page.goto('/')
  const row = await source(page, '部分采纳与恢复')
  const panel = await open(page, row)
  await expect(panel).toContainText('午后（未核定的夹具时点）')
  await expect(panel).toContainText(row.candidate.payload.memory.facts[1].evidence)
  const second = panel.getByRole('checkbox', { name: /^采纳记忆 2：/ })
  await expect(second).toBeEnabled()
  await second.focus()
  await expect(second).toBeFocused()
  await second.press('Space')
  await expect(second).not.toBeChecked()
  const accept = page.getByRole('button', { name: '采纳到正文', exact: true })
  await expect(accept).toBeDisabled()
  const reason = panel.getByRole('textbox', { name: '不采纳部分记忆的原因', exact: true })
  await reason.fill('正文没有午后的依据，暂不采纳该项时点😀𠮷。')
  await expect(accept).toBeEnabled()
  await page.setViewportSize({ width: 320, height: 900 })
  await page.evaluate(() => {
    const sizes = [...document.querySelectorAll('*')].map(
      (element) => [element, parseFloat(getComputedStyle(element).fontSize)] as const,
    )
    for (const [element, size] of sizes)
      if (element instanceof HTMLElement) element.style.fontSize = `${size * 2}px`
  })
  const metrics = await page.evaluate(() => ({
    body: parseFloat(getComputedStyle(document.body).fontSize),
    width: document.documentElement.scrollWidth,
  }))
  expect(metrics.body).toBe(30)
  expect(metrics.width).toBe(320)
  const requests: Record<string, unknown>[] = []
  await page.route(`**/api/artifacts/${row.candidate.id}/accept`, async (route) => {
    requests.push(route.request().postDataJSON())
    if (requests.length === 1)
      return route.fulfill({
        status: 503,
        json: { code: 'fixture', message: '记忆选择工程夹具：临时失败，选择保留。' },
      })
    return route.continue()
  })
  await accept.click()
  await expect(page.getByText('记忆选择工程夹具：临时失败，选择保留。', { exact: true })).toBeVisible()
  await expect(second).not.toBeChecked()
  await expect(reason).toHaveValue('正文没有午后的依据，暂不采纳该项时点😀𠮷。')
  await accept.focus()
  await page.keyboard.press('Enter')
  await page.keyboard.press('Enter')
  const recordSummary = page.getByText('正文采纳与记忆选择记录', { exact: true })
  await expect(recordSummary).toBeVisible()
  expect(requests).toHaveLength(2)
  expect(requests[0]).toEqual(requests[1])
  expect(requests[1]).toMatchObject({
    memory_fact_indices: [0, 2],
    memory_selection_reason: '正文没有午后的依据，暂不采纳该项时点😀𠮷。',
    memory_facts_sha256: row.candidate.memory_facts_sha256,
  })
  const adopted = await (await page.request.get(`/api/chapters/${row.candidate.chapter_id}`)).json()
  expect(adopted.revision).toBe(1)
  expect(adopted.content).toBe(row.candidate.payload.text)
  const context = await (await page.request.get(`/api/branches/${row.workflow.branch_id}/context`)).json()
  expect(context.facts).toHaveLength(2)
  expect(context.facts.map((fact: { story_time: string }) => fact.story_time)).toEqual(['未知', '未知'])
  const receipt = await (await page.request.get(`/api/artifacts/${row.candidate.id}/acceptance`)).json()
  expect(receipt.receipt.memory_selection).toMatchObject({
    fact_count: 3,
    selected_indices: [0, 2],
    omitted_indices: [1],
  })
  expect(
    (await (await page.request.get(`/api/artifacts/${row.candidate.id}`)).json()).payload.memory,
  ).toEqual(row.candidate.payload.memory)
  await recordSummary.focus()
  await recordSummary.press('Enter')
  await expect(recordSummary.locator('..')).toContainText('采纳 2 / 3 项记忆，省略 1 项')
  const history = await (await page.request.get(`/api/chapters/${row.candidate.chapter_id}/history`)).json()
  const restored = await page.request.post(`/api/chapters/${row.candidate.chapter_id}/restore`, {
    headers: row.headers,
    data: {
      base_revision: 1,
      revision_id: history.find((item: { revision: number }) => item.revision === 0).id,
    },
  })
  expect(restored.ok(), await restored.text()).toBeTruthy()
  expect(await (await page.request.get(`/api/artifacts/${row.candidate.id}/acceptance`)).json()).toEqual(
    receipt,
  )
  await page.reload()
  await page.getByRole('combobox', { name: '比较候选', exact: true }).selectOption(row.candidate.id)
  const restoredRecord = page.getByText('正文采纳与记忆选择记录', { exact: true })
  await restoredRecord.click()
  await expect(restoredRecord.locator('..')).toContainText('正文没有午后的依据')
  await expect(restoredRecord.locator('..')).toContainText('原记忆 2 · 当时省略')
  await expect(restoredRecord.locator('..')).toContainText('午后（未核定的夹具时点）')
  expect((await (await page.request.get(`/api/chapters/${row.candidate.chapter_id}`)).json()).revision).toBe(
    2,
  )
  await info.attach('memory-selection-partial-summary', {
    contentType: 'application/json',
    body: JSON.stringify({ metrics, requests, selected: [0, 2], restored_revision: 2, real_models: 0 }),
  })
})

test('整书待审人工全不选：仅采用正文，三条原记忆仍保留且回执明确零项', async ({ page }) => {
  await page.goto('/')
  const row = await source(page, '仅采纳正文')
  const panel = await open(page, row, '整书创作')
  const accept = page.getByRole('button', { name: '作者采纳此章', exact: true })
  const choices: Record<string, unknown>[] = []
  await page.route(`**/api/artifacts/${row.candidate.id}/accept`, async (route) => {
    choices.push(route.request().postDataJSON())
    if (choices.length === 1)
      return route.fulfill({
        status: 503,
        json: { code: 'fixture', message: '默认全选请求尚未采用，可以保留或重新决定。' },
      })
    return route.continue()
  })
  await accept.click()
  await expect(page.getByText('默认全选请求尚未采用，可以保留或重新决定。', { exact: true })).toBeVisible()
  expect(choices[0].memory_fact_indices).toEqual([0, 1, 2])
  await panel.getByRole('button', { name: '全不选（仅采纳正文）', exact: true }).click()
  await expect(accept).toBeDisabled()
  await panel
    .getByRole('textbox', { name: '不采纳部分记忆的原因', exact: true })
    .fill('先采用正文，派生事实另行核对。')
  const response = page.waitForResponse(
    (item) =>
      item.url().endsWith(`/api/artifacts/${row.candidate.id}/accept`) && item.request().method() === 'POST',
  )
  await accept.click()
  expect((await response).request().postDataJSON().memory_fact_indices).toEqual([])
  expect(choices).toHaveLength(2)
  expect(choices[1].idempotency_key).not.toBe(choices[0].idempotency_key)
  await expect(page.getByText('候选已采纳。点击恢复继续下一章。', { exact: true })).toBeVisible()
  expect((await (await page.request.get(`/api/chapters/${row.candidate.chapter_id}`)).json()).content).toBe(
    row.candidate.payload.text,
  )
  expect(
    (await (await page.request.get(`/api/branches/${row.workflow.branch_id}/context`)).json()).facts,
  ).toEqual([])
  const receipt = await (await page.request.get(`/api/artifacts/${row.candidate.id}/acceptance`)).json()
  expect(receipt.receipt.memory_selection).toMatchObject({ selected_indices: [], omitted_indices: [0, 1, 2] })
  expect(
    (await (await page.request.get(`/api/artifacts/${row.candidate.id}`)).json()).payload.memory.facts,
  ).toHaveLength(3)
})

for (const scenario of [
  { label: '部分→新列表', choice: 'partial', variant: '', count: 1 },
  { label: '全不选→新列表', choice: 'none', variant: '', count: 1 },
  { label: '部分→空列表', choice: 'partial', variant: '-empty-refresh', count: 0 },
  { label: '全不选→空列表', choice: 'none', variant: '-empty-refresh', count: 0 },
  { label: '默认空列表→新增事实', choice: 'default', variant: '-empty-initial', count: 1 },
])
  test(`Workflow同版轮询${scenario.label}：显式重读前硬阻断，旧hash409不写稿`, async ({ page }) => {
    await page.goto('/')
    const row = await source(page, scenario.label, true, scenario.variant)
    const panel = await open(page, row, '整书创作')
    const accept = page.getByRole('button', { name: '作者采纳此章', exact: true })
    if (scenario.choice === 'partial') await panel.getByRole('checkbox', { name: /^采纳记忆 2：/ }).uncheck()
    if (scenario.choice === 'none')
      await panel.getByRole('button', { name: '全不选（仅采纳正文）', exact: true }).click()
    if (scenario.choice !== 'default')
      await panel
        .getByRole('textbox', { name: '不采纳部分记忆的原因', exact: true })
        .fill('旧来源的人工选择，不能自动套用新事实。')
    const requests: Record<string, unknown>[] = []
    await page.route(`**/api/artifacts/${row.candidate.id}/accept`, async (route) => {
      requests.push(route.request().postDataJSON())
      if (requests.length === 1)
        return route.fulfill({
          status: 503,
          json: { code: 'fixture', message: '旧选择暂未采用，原稿保持。' },
        })
      return route.continue()
    })
    await accept.click()
    await expect(page.getByText('旧选择暂未采用，原稿保持。', { exact: true })).toBeVisible()
    expect(requests[0].memory_facts_sha256).toBe(row.candidate.memory_facts_sha256)
    if (scenario.choice === 'default') expect(requests[0].memory_fact_indices).toEqual([])
    const taskResponse = await page.request.post(`/api/artifacts/${row.candidate.id}/recheck`, {
      headers: row.headers,
      data: {
        candidate_revision: row.candidate.candidate_revision,
        account: 'offline',
        client_request_id: crypto.randomUUID(),
      },
    })
    expect(taskResponse.status(), await taskResponse.text()).toBe(202)
    const task = await taskResponse.json()
    const read = async () => (await page.request.get(`/api/tasks/${task.id}`)).json()
    await expect.poll(async () => (await read()).status, { timeout: 30_000 }).toBe('succeeded')
    const refreshed = await (await page.request.get(`/api/artifacts/${row.candidate.id}`)).json()
    expect(refreshed.candidate_revision).toBe(row.candidate.candidate_revision)
    expect(refreshed.memory_facts_sha256).not.toBe(row.candidate.memory_facts_sha256)
    expect(refreshed.payload.memory.facts).toHaveLength(scenario.count)
    const stale = await page.request.post(`/api/artifacts/${row.candidate.id}/accept`, {
      headers: row.headers,
      data: {
        candidate_revision: row.candidate.candidate_revision,
        idempotency_key: crypto.randomUUID(),
        memory_fact_indices: [],
        memory_selection_reason: '旧选择',
        memory_facts_sha256: row.candidate.memory_facts_sha256,
      },
    })
    expect(stale.status()).toBe(409)
    expect(
      (await (await page.request.get(`/api/chapters/${row.candidate.chapter_id}`)).json()).revision,
    ).toBe(0)
    // This is the real Workflow waiting-artifact poll, not a mocked GET response.
    // The gate remains outside a collapsed details and survives a transition to zero facts.
    await expect(panel).toContainText('原选择已失效')
    await expect(accept).toBeDisabled()
    await expect(panel.locator('details')).toHaveCount(0)
    const confirm = panel.getByRole('button', { name: '读取新版记忆并重新选择', exact: true })
    await expect(confirm).toBeVisible()
    expect(requests).toHaveLength(1)
    await confirm.focus()
    await confirm.press('Enter')
    if (scenario.count > 0) {
      await panel.locator(':scope > details > summary').click()
      await expect(panel.getByRole('checkbox')).toHaveCount(scenario.count)
      for (const checkbox of await panel.getByRole('checkbox').all()) await expect(checkbox).toBeChecked()
    } else await expect(panel).toHaveCount(0)
    await expect(panel.getByRole('textbox', { name: '不采纳部分记忆的原因', exact: true })).toHaveCount(0)
    await expect(accept).toBeEnabled()
    await accept.click()
    await expect(page.getByText('候选已采纳。点击恢复继续下一章。', { exact: true })).toBeVisible()
    expect(requests).toHaveLength(2)
    expect(requests[1].idempotency_key).not.toBe(requests[0].idempotency_key)
    expect(requests[1].memory_facts_sha256).toBe(refreshed.memory_facts_sha256)
    expect(requests[1].memory_fact_indices).toEqual(
      Array.from({ length: scenario.count }, (_, index) => index),
    )
    expect(
      (await (await page.request.get(`/api/chapters/${row.candidate.chapter_id}`)).json()).revision,
    ).toBe(1)
    if (scenario.label === '部分→新列表') {
      await page.getByRole('button', { name: '写作', exact: true }).click()
      await page
        .getByRole('combobox', { name: '比较候选', exact: true })
        .selectOption((await read()).result.artifact_id)
      const report = page.getByRole('region', { name: '候选复检报告', exact: true })
      await expect(report.getByRole('button', { name: /采纳|全不选/ })).toHaveCount(0)
    }
  })

test('采纳回执晚到另一作品：保留原决定，不改变新作品正文或新候选默认全选', async ({ page }) => {
  await page.goto('/')
  const first = await source(page, '旧请求'),
    next = await source(page, '新目标')
  const panel = await open(page, first)
  await panel.getByRole('checkbox', { name: /^采纳记忆 2：/ }).uncheck()
  await panel
    .getByRole('textbox', { name: '不采纳部分记忆的原因', exact: true })
    .fill('本次不采用旧稿中的午后时点。')
  let release = () => {},
    arrived = () => {}
  const held = new Promise<void>((resolve) => {
    release = resolve
  })
  const entered = new Promise<void>((resolve) => {
    arrived = resolve
  })
  await page.route(`**/api/artifacts/${first.candidate.id}/accept`, async (route) => {
    const response = await route.fetch()
    arrived()
    await held
    await route.fulfill({ response })
  })
  try {
    await page.getByRole('button', { name: '采纳到正文', exact: true }).click()
    await entered
    await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(next.novel.id)
    await page.getByRole('combobox', { name: '工作版本', exact: true }).selectOption(next.workflow.branch_id)
    const nextPanel = page.getByRole('region', { name: '正文附带的记忆候选', exact: true })
    await expect(nextPanel).toContainText('已选 3 / 3 项')
    release()
    await expect(page.getByRole('textbox', { name: '候选内容', exact: true })).toHaveValue(
      next.candidate.payload.text,
    )
    await expect(nextPanel).toContainText('已选 3 / 3 项')
    expect(
      (await (await page.request.get(`/api/chapters/${next.candidate.chapter_id}`)).json()).revision,
    ).toBe(0)
    expect(
      (await (await page.request.get(`/api/chapters/${first.candidate.chapter_id}`)).json()).revision,
    ).toBe(1)
    const posted = page.waitForResponse(
      (item) =>
        item.url().endsWith(`/api/artifacts/${next.candidate.id}/accept`) &&
        item.request().method() === 'POST',
    )
    await page.getByRole('button', { name: '采纳到正文', exact: true }).click()
    expect((await posted).request().postDataJSON().memory_fact_indices).toEqual([0, 1, 2])
  } finally {
    release()
  }
})
