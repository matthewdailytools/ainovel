import { test, expect } from '@playwright/test'

test('人工反馈：定位旧来源、确认当前稿、作者选区生成候选并保留未选正文', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await (
    await page.request.post('/api/novels', {
      headers,
      data: { title: '后台验证 · 人工反馈局部修订' },
    })
  ).json()
  const chapter = await (
    await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
      headers,
      data: { title: '渡口反馈章' },
    })
  ).json()
  const quote = '她心里很着急。'
  await page.request.put(`/api/chapters/${chapter.id}`, {
    headers,
    data: { base_revision: 0, content: quote },
  })
  await page.request.post('/api/feedback', {
    headers,
    data: {
      chapter_id: chapter.id,
      category: '审美偏好',
      text: '把笼统解释改成核对刻度的具体操作。',
      source_quote: quote,
    },
  })
  const current = quote + '\n第二句保持😀𠮷。'
  await page.request.put(`/api/chapters/${chapter.id}`, {
    headers,
    data: { base_revision: 1, content: current },
  })
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  await page.getByRole('button', { name: '打开此反馈的局部修订', exact: true }).click()
  const panel = page.getByRole('region', { name: '手动反馈局部修订', exact: true })
  await expect(panel).toContainText('来源修订 1')
  await expect(panel).toContainText('未作为独立模型审稿结论')
  const editor = page.getByRole('textbox', { name: '章节正文', exact: true })
  await expect(editor.locator('.cm-line')).toHaveText(current.split('\n'))
  await editor.focus()
  await editor.press('ControlOrMeta+Home')
  await editor.press('Shift+End')
  await page.getByRole('button', { name: '生成候选 →', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('正文与反馈来源修订不同')
  const before = await (await page.request.get('/api/tasks')).json()
  expect(before.filter((row: { novel_id: string }) => row.novel_id === novel.id)).toHaveLength(0)
  await panel.getByRole('checkbox', { name: /我已核对当前保存稿修订 2/ }).check()
  const request = page.waitForRequest((r) => r.url().endsWith('/api/modules') && r.method() === 'POST')
  await page.getByRole('button', { name: '生成候选 →', exact: true }).click()
  const payload = (await request).postDataJSON()
  expect(payload.action).toBe('writing.rewrite_selection')
  expect(payload.base_revision).toBe(2)
  expect(payload.chapter_id).toBe(chapter.id)
  expect(payload.selection_text).toBe(quote)
  expect(payload.instruction).toContain('"source_revision":1')
  expect(payload.instruction).toContain('尚未由独立模型审稿证实')
  expect(payload.source_review_id).toBeUndefined()
  await expect(page.getByRole('button', { name: '采纳到正文', exact: true })).toBeVisible()
  expect((await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content).toBe(current)
  await page.getByRole('button', { name: '采纳到正文', exact: true }).click()
  await expect
    .poll(async () => (await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content)
    .toBe('她先核对刻度，再记录读数。\n第二句保持😀𠮷。')
  const history = await (await page.request.get(`/api/chapters/${chapter.id}/history`)).json()
  expect(history.some((row: { content: string }) => row.content === current)).toBeTruthy()
  await page.setViewportSize({ width: 320, height: 800 })
  const clear = panel.getByRole('button', { name: '退出反馈修订，保留正文', exact: true })
  await clear.focus()
  await expect(clear).toBeFocused()
  await clear.press('Enter')
  await expect(panel).toHaveCount(0)
})
