import { test, expect } from '@playwright/test'

test('长章写作：二十万字符定位末尾、修改保存和重开完整保留', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await (
    await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 长章编辑响应' } })
  ).json()
  const chapter = await (
    await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
      headers,
      data: { title: '长章核对' },
    })
  ).json()
  const text = Array.from(
    { length: 400 },
    (_, index) => `第${index}段：${'渡船复测数据'.repeat(80)}😀𠮷。`,
  ).join('\n')
  expect(text.length).toBeGreaterThan(190000)
  expect(text.length).toBeLessThan(250000)
  const saved = await page.request.put(`/api/chapters/${chapter.id}`, {
    headers,
    data: { base_revision: 0, content: text },
  })
  expect(saved.ok(), await saved.text()).toBeTruthy()
  const started = Date.now()
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  const editor = page.getByRole('textbox', { name: '章节正文', exact: true })
  await expect(editor).toBeVisible()
  const openedMs = Date.now() - started
  await editor.focus()
  const editing = Date.now()
  await editor.press('ControlOrMeta+End')
  await page.keyboard.insertText('末尾补记😀𠮷')
  await expect(editor).toContainText('末尾补记😀𠮷')
  const editMs = Date.now() - editing
  const saving = Date.now()
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect
    .poll(async () => (await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content)
    .toBe(text + '末尾补记😀𠮷')
  const saveMs = Date.now() - saving
  await page.reload()
  await expect(page.getByRole('textbox', { name: '章节正文', exact: true })).toBeVisible()
  const current = await (await page.request.get(`/api/chapters/${chapter.id}`)).json()
  expect(current.content).toBe(text + '末尾补记😀𠮷')
  const tasks = await (await page.request.get('/api/tasks')).json()
  expect(tasks.filter((row: { novel_id: string }) => row.novel_id === novel.id)).toEqual([])
  await test
    .info()
    .attach('long-manuscript-response', {
      contentType: 'application/json',
      body: JSON.stringify({
        chars: text.length,
        utf16: text.length,
        openedMs,
        editMs,
        saveMs,
        revision: current.revision,
        synthetic: true,
        modelCalls: 0,
      }),
    })
})
