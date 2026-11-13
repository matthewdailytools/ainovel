import { test, expect } from '@playwright/test'

test('双窗口保存冲突：保留中文本地输入与先提交原稿，刷新可比较恢复', async ({ page, browser }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await (
    await page.request.post('/api/novels', {
      headers,
      data: { title: '后台验证 · 双窗口正文冲突' },
    })
  ).json()
  const chapter = await (
    await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
      headers,
      data: { title: '渡船' },
    })
  ).json()
  const otherContext = await browser.newContext()
  const other = await otherContext.newPage()
  try {
    for (const tab of [page, other]) {
      await tab.goto('/')
      await tab.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
      await expect(tab.getByRole('textbox', { name: '章节正文', exact: true })).toBeVisible()
    }
    const first = '甲稿：渡船靠岸。😀𠮷\n她仍不知道铜牌用途。'
    const second = '乙稿：她留在岸边，等候证人。😀𠮷'
    await page.getByRole('textbox', { name: '章节正文', exact: true }).fill(first)
    await page.getByRole('button', { name: '保存', exact: true }).click()
    await expect
      .poll(async () => (await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content)
      .toBe(first)
    await other.getByRole('textbox', { name: '章节正文', exact: true }).fill(second)
    await other.getByRole('button', { name: '保存', exact: true }).click()
    await expect(other.getByText('保存失败，浏览器草稿保留；可下载草稿后比较', { exact: true })).toBeVisible()
    await expect(other.getByRole('textbox', { name: '章节正文', exact: true })).toHaveText(second)
    expect((await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content).toBe(first)
    other.once('dialog', (dialog) => dialog.accept())
    await other.reload()
    await expect(other.getByText('浏览器草稿与服务中的修订不同。', { exact: true })).toBeVisible()
    await expect(
      other.getByRole('textbox', { name: '章节正文', exact: true }).locator('.cm-line'),
    ).toHaveText(first.split('\n'))
    await other.getByRole('button', { name: '将此草稿载入编辑区', exact: true }).click()
    await expect(other.getByRole('textbox', { name: '章节正文', exact: true })).toHaveText(second)
    await expect
      .poll(async () => (await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content)
      .toBe(second)
    const tasks = await (await page.request.get('/api/tasks')).json()
    expect(tasks.filter((row: { novel_id: string }) => row.novel_id === novel.id)).toEqual([])
  } finally {
    await otherContext.close()
  }
})
