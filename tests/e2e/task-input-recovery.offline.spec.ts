import { test, expect, type Page } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  page.on('pageerror', (error) =>
    test.info().annotations.push({ type: 'pageerror', description: error.message }),
  )
})
test.afterEach(async () => {
  expect(test.info().annotations.filter((item) => item.type === 'pageerror')).toEqual([])
})

const CONTENT = '开篇😀𠮷。她检查刻度，再把记录放回原处。'
async function setup(page: Page) {
  await page.goto('/')
  const headers = { 'X-Ainovel-CSRF': (await (await page.request.get('/api/session')).json()).csrf }
  const novel = await (
    await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 最近任务资料恢复' } })
  ).json()
  const chapter = await (
    await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
      headers,
      data: { title: '原任务章' },
    })
  ).json()
  await page.request.put(`/api/chapters/${chapter.id}`, {
    headers,
    data: { base_revision: 0, content: CONTENT },
  })
  const other = await (
    await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
      headers,
      data: { title: '另一章' },
    })
  ).json()
  await page.request.put(`/api/chapters/${other.id}`, {
    headers,
    data: { base_revision: 0, content: '另一章原稿保持。' },
  })
  const book = await (
    await page.request.post('/api/library/import', {
      headers,
      multipart: {
        file: {
          name: '原创恢复练习.txt',
          mimeType: 'text/plain',
          buffer: Buffer.from('水声渐低。窗外起风。'),
        },
      },
    })
  ).json()
  const units = await (await page.request.get(`/api/library/books/${book.id}/units`)).json()
  const references: { id: string }[] = []
  for (const [start, end, quote] of [
    [0, 4, '水声渐低'],
    [5, 9, '窗外起风'],
  ] as const) {
    const response = await page.request.post('/api/library/excerpts', {
      headers,
      data: { unit_id: units[0].id, start, end, quote, note: '只参考节奏', novel_id: novel.id },
    })
    expect(response.ok()).toBeTruthy()
    references.push(await response.json())
  }
  const response = await page.request.post('/api/modules', {
    headers,
    data: {
      action: 'writing.rewrite_selection',
      novel_id: novel.id,
      branch_id: novel.branch_id,
      chapter_id: chapter.id,
      base_revision: 1,
      instruction: '只补一个具体动作，保持结果。',
      selection_start: 2,
      selection_end: 6,
      selection_text: '😀𠮷',
      reference_ids: [references[0].id],
      fixed: ['结果保持'],
      account: 'offline',
      max_output_tokens: 512,
      client_request_id: 'recovery-' + novel.id,
    },
  })
  expect(response.status(), await response.text()).toBe(202)
  const task = await response.json()
  await expect
    .poll(async () => (await (await page.request.get(`/api/tasks/${task.id}`)).json()).status)
    .toBe('succeeded')
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await expect(page.getByRole('textbox', { name: '章节正文', exact: true }).locator('.cm-line')).toHaveText(
    CONTENT,
  )
  const panel = page.getByRole('region', { name: '恢复最近任务的输入与资料', exact: true })
  await expect(panel.getByRole('button', { name: '恢复最近任务的输入', exact: true })).toBeEnabled()
  return { novel, chapter, other, task, references, headers, panel }
}

test('键盘恢复原资料及UTF16选区；取消保留当前选择；生成实际发送确认的原引用', async ({ page }, testInfo) => {
  const { panel, references, chapter } = await setup(page)
  await page.getByText('固定条件、写法与资料包', { exact: true }).click()
  const current = page.getByRole('group', { name: '主动发送的摘录', exact: true })
  await current.getByRole('checkbox').filter({ hasText: '' }).nth(0).check()
  const originallyChosen = await current
    .getByRole('checkbox')
    .evaluateAll((nodes) => nodes.map((node) => (node as HTMLInputElement).checked))
  await panel.getByRole('button', { name: '恢复最近任务的输入', exact: true }).focus()
  await page.keyboard.press('Enter')
  await expect(panel).toContainText('没有历史资料内容快照')
  await expect(panel).toContainText('原选区 2—6')
  await panel.getByRole('button', { name: '取消恢复', exact: true }).click()
  expect(
    await current
      .getByRole('checkbox')
      .evaluateAll((nodes) => nodes.map((node) => (node as HTMLInputElement).checked)),
  ).toEqual(originallyChosen)
  await expect(page.getByLabel('情节或具体要求')).toHaveValue('')
  await panel.getByRole('button', { name: '恢复最近任务的输入', exact: true }).click()
  const confirm = panel.getByRole('checkbox', { name: /我已核对当前内容/ })
  await page.screenshot({ path: testInfo.outputPath('recovery-desktop.png'), fullPage: true })
  await confirm.check()
  const restore = panel.getByRole('button', { name: '确认恢复输入与资料', exact: true })
  await restore.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByLabel('情节或具体要求')).toHaveValue('只补一个具体动作，保持结果。')
  await expect(page.getByText('已选择 4 个 UTF-16 单元，可局部改写', { exact: true })).toBeVisible()
  const request = page.waitForRequest((r) => r.method() === 'POST' && r.url().endsWith('/api/modules'))
  await page.getByRole('button', { name: '生成候选 →', exact: true }).click()
  const body = (await request).postDataJSON()
  expect(body.reference_ids).toEqual([references[0].id])
  expect([
    body.chapter_id,
    body.base_revision,
    body.selection_start,
    body.selection_end,
    body.selection_text,
  ]).toEqual([chapter.id, 1, 2, 6, '😀𠮷'])
  expect((await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content).toBe(CONTENT)
})

test('缺失引用逐项显示并显式省略；320宽真实文字2倍可用', async ({ page }, testInfo) => {
  const { panel, references } = await setup(page)
  await page.route('**/api/library/excerpts?*', async (route) => {
    const response = await route.fetch()
    await route.fulfill({
      response,
      json: (await response.json()).filter((row: { id: string }) => row.id !== references[0].id),
    })
  })
  await panel.getByRole('button', { name: '恢复最近任务的输入', exact: true }).click()
  await expect(panel).toContainText(references[0].id)
  await expect(panel).toContainText('当前列表未找到')
  await expect(panel.getByRole('button', { name: '确认恢复输入与资料', exact: true })).toBeDisabled()
  await page.setViewportSize({ width: 320, height: 800 })
  const sizes = await panel
    .locator('button,p,label,legend')
    .evaluateAll((nodes) => nodes.map((node) => Number.parseFloat(getComputedStyle(node).fontSize)))
  await panel
    .locator('button,p,label,legend')
    .evaluateAll((nodes) =>
      nodes.forEach(
        (node) =>
          ((node as HTMLElement).style.fontSize =
            `${Number.parseFloat(getComputedStyle(node).fontSize) * 2}px`),
      ),
    )
  const doubled = await panel
    .locator('button,p,label,legend')
    .evaluateAll((nodes) => nodes.map((node) => Number.parseFloat(getComputedStyle(node).fontSize)))
  expect(doubled.every((size, index) => Math.abs(size - sizes[index] * 2) < 0.1)).toBeTruthy()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await page.screenshot({ path: testInfo.outputPath('recovery-missing-320-actual-2x.png'), fullPage: true })
  await panel.getByRole('checkbox', { name: /我已核对当前内容/ }).check()
  await panel.getByRole('button', { name: '确认恢复输入与资料', exact: true }).click()
  const request = page.waitForRequest((r) => r.method() === 'POST' && r.url().endsWith('/api/modules'))
  await page.getByRole('button', { name: '生成候选 →', exact: true }).click()
  expect((await request).postDataJSON().reference_ids).toEqual([])
})

test('确认前资料变化不恢复输入；重读后必须重新确认', async ({ page }) => {
  const { panel, references } = await setup(page)
  let changed = false
  await page.route('**/api/library/excerpts?*', async (route) => {
    const response = await route.fetch()
    await route.fulfill({
      response,
      json: (await response.json()).map((row: { id: string; note: string }) =>
        changed && row.id === references[0].id ? { ...row, note: '当前笔记变化' } : row,
      ),
    })
  })
  await panel.getByRole('button', { name: '恢复最近任务的输入', exact: true }).click()
  await panel.getByRole('checkbox', { name: /我已核对当前内容/ }).check()
  changed = true
  await panel.getByRole('button', { name: '确认恢复输入与资料', exact: true }).click()
  await expect(panel).toContainText('发生变化，未恢复')
  await expect(page.getByLabel('情节或具体要求')).toHaveValue('')
  await expect(panel.getByRole('checkbox', { name: /我已核对当前内容/ })).not.toBeChecked()
  await panel.getByRole('button', { name: '重新读取任务与资料', exact: true }).click()
  await panel.getByRole('button', { name: '取消恢复', exact: true }).click()
  await expect(page.getByLabel('情节或具体要求')).toHaveValue('')
})

for (const cancelFirst of [false, true]) {
  test(`读取期间切章使迟到返回失效，事先取消=${cancelFirst}，不把旧选区和资料带到新目标`, async ({
    page,
  }) => {
    const { panel, task } = await setup(page)
    let release!: () => void
    let entered!: () => void
    const pending = new Promise<void>((resolve) => (release = resolve))
    const started = new Promise<void>((resolve) => (entered = resolve))
    await page.route(`**/api/tasks/${task.id}`, async (route) => {
      entered()
      await pending
      await route.continue()
    })
    await panel.getByRole('button', { name: '恢复最近任务的输入', exact: true }).click()
    await started
    if (cancelFirst) await panel.getByRole('button', { name: '取消恢复', exact: true }).click()
    await page
      .locator('.chapter-list')
      .getByRole('button', { name: /另一章/ })
      .click()
    release()
    await expect(page.getByRole('textbox', { name: '章节正文', exact: true }).locator('.cm-line')).toHaveText(
      '另一章原稿保持。',
    )
    await expect(panel.getByRole('group', { name: '原任务引用清单', exact: true })).toHaveCount(0)
    await expect(page.getByLabel('情节或具体要求')).toHaveValue('')
    await panel.getByRole('button', { name: '恢复最近任务的输入', exact: true }).click()
    await expect(panel).toContainText('不会使用旧坐标')
  })
}
