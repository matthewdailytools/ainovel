import { test, expect } from '@playwright/test'

test('结构更新：明确目标与字段对照，保留未修改档案及原稿', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await (
    await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 已有结构更新' } })
  ).json()
  const chapter = await (
    await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
      headers,
      data: { title: '新称呼' },
    })
  ).json()
  const text = '宁禾被叫作小宁，也被叫作禾姑娘。'
  await page.request.put(`/api/chapters/${chapter.id}`, {
    headers,
    data: { base_revision: 0, content: text },
  })
  const initial = await (await page.request.get(`/api/branches/${novel.branch_id}/story-graph`)).json()
  const response = await page.request.post(`/api/branches/${novel.branch_id}/story-records`, {
    headers,
    data: {
      base_story_revision: initial.story_revision,
      record: {
        kind: 'character',
        title: '宁禾',
        aliases: ['小宁'],
        profile: '作者完整档案，更新别名时必须保留。',
      },
    },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  const before = await response.json()
  const id = before.records[0].id
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  await page.getByText('按保存稿整理人物、事件、状态和伏笔候选', { exact: true }).click()
  const panel = page.getByRole('region', { name: '结构化整理与确认', exact: true })
  await panel.getByRole('checkbox', { name: '1 · 新称呼', exact: true }).check()
  await panel.getByRole('button', { name: '从所选保存稿生成结构候选', exact: true }).click()
  await expect(panel.getByRole('checkbox', { name: '更新人物：宁禾', exact: true })).toBeVisible()
  await panel.getByText('对照正式记录修订0与更新提案', { exact: true }).click()
  await expect(panel.getByText('禾姑娘', { exact: true })).toBeVisible()
  await panel.getByRole('checkbox', { name: '更新人物：宁禾', exact: true }).check()
  await panel.getByRole('button', { name: '确认采用所选结构（不修改正文）', exact: true }).click()
  await expect(
    panel.getByText('所选结构已采用，未选项保留在原产物，正文未改变。', { exact: true }),
  ).toBeVisible()
  const after = await (await page.request.get(`/api/branches/${novel.branch_id}/story-graph`)).json()
  expect(after.records).toHaveLength(1)
  expect(after.records[0].id).toBe(id)
  expect(after.records[0].revision).toBe(1)
  expect(after.records[0].payload.profile).toBe('作者完整档案，更新别名时必须保留。')
  expect(after.records[0].payload.aliases).toEqual(['小宁', '禾姑娘'])
  const saved = await (await page.request.get(`/api/chapters/${chapter.id}`)).json()
  expect(saved.content).toBe(text)
  expect(saved.revision).toBe(1)
})
