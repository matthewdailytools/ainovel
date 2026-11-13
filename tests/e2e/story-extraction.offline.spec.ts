import { test, expect } from '@playwright/test'

test('反向整理：原文候选、依赖提示、作者采用且原稿不改', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await (
    await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 结构整理' } })
  ).json()
  const chapter = await (
    await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
      headers,
      data: { title: '渡口暗号' },
    })
  ).json()
  const text = '😀宁禾看懂暗号，钱叔收起铜牌。'
  await page.request.put(`/api/chapters/${chapter.id}`, {
    headers,
    data: { base_revision: 0, content: text },
  })
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await expect(page.getByRole('combobox', { name: '工作版本', exact: true })).not.toHaveValue('')
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  await page.getByText('按保存稿整理人物、事件、状态和伏笔候选', { exact: true }).click()
  const panel = page.getByRole('region', { name: '结构化整理与确认', exact: true })
  await panel.getByRole('checkbox', { name: '1 · 渡口暗号', exact: true }).check()
  await panel.getByRole('button', { name: '从所选保存稿生成结构候选', exact: true }).click()
  await expect(panel.getByRole('checkbox', { name: '采用人物：宁禾', exact: true })).toBeVisible()
  const original = await (await page.request.get(`/api/chapters/${chapter.id}`)).json()
  expect(original.content).toBe(text)
  const before = await (await page.request.get(`/api/branches/${novel.branch_id}/story-graph`)).json()
  expect(before.records).toHaveLength(0)
  await panel.getByRole('checkbox', { name: '采用时点状态：暗号认知', exact: true }).check()
  await panel.getByRole('button', { name: '确认采用所选结构（不修改正文）', exact: true }).click()
  await expect(panel.getByRole('status').filter({ hasText: '未选择的人物或事件' })).toBeVisible()
  const rejected = await (await page.request.get(`/api/branches/${novel.branch_id}/story-graph`)).json()
  expect(rejected.records).toHaveLength(0)
  for (const box of await panel.locator('article input[type=checkbox]').all()) await box.check()
  await panel.getByRole('button', { name: '确认采用所选结构（不修改正文）', exact: true }).click()
  await expect(
    panel.getByText('所选结构已采用，未选项保留在原产物，正文未改变。', { exact: true }),
  ).toBeVisible()
  const after = await (await page.request.get(`/api/branches/${novel.branch_id}/story-graph`)).json()
  expect(after.records).toHaveLength(6)
  const saved = await (await page.request.get(`/api/chapters/${chapter.id}`)).json()
  expect(saved.content).toBe(text)
  expect(saved.revision).toBe(original.revision)
  await page.reload()
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  await page.getByText('按保存稿整理人物、事件、状态和伏笔候选', { exact: true }).click()
  await expect(panel.getByRole('combobox', { name: '保存的结构候选', exact: true })).toContainText('已采纳')
})
