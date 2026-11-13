import { test, expect } from '@playwright/test'

async function setup(page: import('@playwright/test').Page, title: string, content = '') {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await (await page.request.post('/api/novels', { headers, data: { title } })).json()
  const chapter = await (
    await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
      headers,
      data: { title: '工程测试章' },
    })
  ).json()
  if (content)
    await page.request.put(`/api/chapters/${chapter.id}`, { headers, data: { base_revision: 0, content } })
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await expect(page.getByRole('textbox', { name: '章节正文', exact: true })).toBeVisible()
  return { novel, chapter, headers }
}

test('中文组合输入：浏览器IME组合与Enter不触发生成、不插入额外换行，提交后逐字保存', async ({ page }) => {
  const { novel, chapter } = await setup(page, '后台验证 · 中文IME组合事件')
  const editor = page.getByRole('textbox', { name: '章节正文', exact: true })
  await editor.focus()
  const events: { type: string; composing?: boolean; trusted: boolean }[] = []
  await page.exposeFunction('recordCompositionEvent', (value: (typeof events)[number]) => events.push(value))
  await editor.evaluate((element) => {
    for (const type of ['compositionstart', 'compositionupdate', 'compositionend', 'keydown']) {
      element.addEventListener(type, (event) => {
        const detail = {
          type: event.type,
          composing: 'isComposing' in event ? Boolean(event.isComposing) : undefined,
          trusted: event.isTrusted,
        }
        void (
          window as unknown as { recordCompositionEvent(value: typeof detail): Promise<void> }
        ).recordCompositionEvent(detail)
      })
    }
  })
  const protocol = await page.context().newCDPSession(page)
  await protocol.send('Input.imeSetComposition', { text: '渡船', selectionStart: 2, selectionEnd: 2 })
  await protocol.send('Input.dispatchKeyEvent', {
    type: 'keyDown',
    key: 'Enter',
    code: 'Enter',
    windowsVirtualKeyCode: 229,
    nativeVirtualKeyCode: 229,
  })
  await protocol.send('Input.dispatchKeyEvent', {
    type: 'keyUp',
    key: 'Enter',
    code: 'Enter',
    windowsVirtualKeyCode: 229,
    nativeVirtualKeyCode: 229,
  })
  await protocol.send('Input.insertText', { text: '渡船😀𠮷' })
  await expect(editor.locator('.cm-line')).toHaveText(['渡船😀𠮷'])
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect
    .poll(async () => (await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content)
    .toBe('渡船😀𠮷')
  await expect.poll(() => events.some((event) => event.type === 'compositionend')).toBe(true)
  await test.info().attach('browser-ime-events', {
    body: JSON.stringify(events),
    contentType: 'application/json',
  })
  // CDP starts a browser IME composition rather than fabricating DOM events.
  // Its insertText completion can end with an untrusted compositionend, which
  // stays in the evidence; this is not an OS input-method acceptance result.
  expect(events.some((event) => event.type === 'compositionstart' && event.trusted)).toBeTruthy()
  expect(events.some((event) => event.type === 'compositionupdate' && event.trusted)).toBeTruthy()
  expect(events.some((event) => event.type === 'keydown' && event.composing && event.trusted)).toBeTruthy()
  const tasks = await (await page.request.get('/api/tasks')).json()
  expect(tasks.filter((task: { novel_id: string }) => task.novel_id === novel.id)).toEqual([])
  await protocol.detach()
})

test('后台任务：刷新重连同一任务、取消保留片段、明确恢复后候选不覆盖原稿', async ({ page }) => {
  const original = '她心里很着急。\n第二段保留😀𠮷。'
  const { novel, chapter, headers } = await setup(page, '后台验证 · 取消重连恢复', original)
  const editor = page.getByRole('textbox', { name: '章节正文', exact: true })
  await editor.focus()
  await editor.press('ControlOrMeta+Home')
  await editor.press('Shift+End')
  await page
    .getByRole('combobox', { name: '这次要做什么', exact: true })
    .selectOption('writing.rewrite_selection')
  await page
    .getByRole('textbox', { name: '情节或具体要求', exact: true })
    .fill('后台故障演练：慢速可取消。只改当前选句，其他正文保持。')
  const response = page.waitForResponse(
    (value) => value.url().endsWith('/api/modules') && value.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '生成候选 →', exact: true }).click()
  const task = await (await response).json()
  await page.getByText('查看流式草稿（尚未采纳）', { exact: true }).click()
  await expect(page.locator('pre.stream')).toContainText('等待核查的未采纳片段。')
  await page.reload()
  await page.getByText('查看流式草稿（尚未采纳）', { exact: true }).click()
  await expect(page.locator('pre.stream')).toContainText('等待核查的未采纳片段。')
  const reconnected = await (await page.request.get('/api/tasks')).json()
  expect(reconnected.filter((row: { novel_id: string }) => row.novel_id === novel.id)).toHaveLength(1)
  await page.getByRole('button', { name: '取消生成', exact: true }).click()
  await expect
    .poll(async () => (await (await page.request.get(`/api/tasks/${task.id}`)).json()).status)
    .toBe('cancelled')
  expect((await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content).toBe(original)
  const artifacts = await (await page.request.get(`/api/artifacts?branch_id=${novel.branch_id}`)).json()
  expect(artifacts).toEqual([])
  await page.getByText('任务历史与失败产物', { exact: true }).click()
  await page.getByRole('button', { name: '查看保存的步骤与中断输出', exact: true }).click()
  await expect(page.locator('.candidates pre.stream')).toContainText('中断输出：')
  const refused = await page.request.post(`/api/tasks/${task.id}/resume`, {
    headers,
    data: { acknowledge_unknown: false },
  })
  expect(refused.status()).toBe(409)
  const resumed = await page.request.post(`/api/tasks/${task.id}/resume`, {
    headers,
    data: { acknowledge_unknown: true },
  })
  expect(resumed.ok(), await resumed.text()).toBeTruthy()
  await expect
    .poll(async () => (await (await page.request.get(`/api/tasks/${task.id}`)).json()).status, {
      timeout: 25_000,
    })
    .toBe('succeeded')
  const all = await (await page.request.get('/api/tasks')).json()
  expect(all.filter((row: { novel_id: string }) => row.novel_id === novel.id)).toHaveLength(1)
  expect((await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content).toBe(original)
  const finalArtifacts = await (await page.request.get(`/api/artifacts?branch_id=${novel.branch_id}`)).json()
  expect(finalArtifacts).toHaveLength(1)
  expect(finalArtifacts[0].status).toBe('candidate')
  expect(finalArtifacts[0].task_id).toBe(task.id)
  await expect(page.getByRole('button', { name: '采纳到正文', exact: true })).toBeVisible()
})
