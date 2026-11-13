import { test, expect } from '@playwright/test'

test('场景时点：绑定人物视角、隔离未来能力、保留绑定历史', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const n = await (
    await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 场景时点' } })
  ).json()
  const first = await (
    await page.request.post(`/api/branches/${n.branch_id}/chapters`, { headers, data: { title: '获得能力' } })
  ).json()
  const text = '😀宁禾在后来学会开锁。'
  await page.request.put(`/api/chapters/${first.id}`, { headers, data: { base_revision: 0, content: text } })
  const target = await (
    await page.request.post(`/api/branches/${n.branch_id}/chapters`, {
      headers,
      data: { title: '倒叙与以后' },
    })
  ).json()
  await page.request.put(`/api/chapters/${target.id}`, {
    headers,
    data: { base_revision: 0, content: '回到尚未学会开锁的那一日。' },
  })
  const source = { chapter_id: first.id, revision: 1, start: 0, end: text.length, quote: text }
  async function record(payload: object) {
    const g = await (await page.request.get(`/api/branches/${n.branch_id}/story-graph`)).json()
    const r = await page.request.post(`/api/branches/${n.branch_id}/story-records`, {
      headers,
      data: { base_story_revision: g.story_revision, record: payload },
    })
    expect(r.ok(), await r.text()).toBeTruthy()
    const v = await r.json()
    return v.records.at(-1)
  }
  const character = await record({ kind: 'character', title: '宁禾' })
  const event = await record({
    kind: 'event',
    title: '学会开锁',
    status: 'actual',
    story_order: 20,
    participants: [character.id],
    sources: [source],
  })
  await record({
    kind: 'state',
    title: '开锁能力',
    character_id: character.id,
    event_id: event.id,
    category: 'capability',
    key: '开锁',
    value: '已学会',
    audience: 'character',
    truth: 'actual',
    sources: [source],
  })
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(n.id)
  await page.getByRole('button', { name: /02 倒叙与以后/ }).click()
  await expect(page.getByRole('textbox', { name: '章节标题', exact: true })).toHaveValue('倒叙与以后')
  await page.getByText('场景时点与人物当前状态', { exact: true }).click()
  const panel = page.getByRole('region', { name: '场景时点绑定', exact: true })
  await panel.getByRole('textbox', { name: '场景名称', exact: true }).fill('回忆之前')
  await panel.getByRole('spinbutton', { name: '场景故事顺序（空白为未知）', exact: true }).fill('10')
  await panel.getByRole('combobox', { name: '场景信息视角', exact: true }).selectOption('character')
  await panel.getByRole('combobox', { name: '本场景视角人物', exact: true }).selectOption(character.id)
  await panel.getByRole('button', { name: '确认保存场景时点', exact: true }).click()
  await expect(panel.getByText(/场景：回忆之前 · 故事序10/)).toBeVisible()
  await expect(panel.getByText('有效状态 0；冲突组 0；待判断 0', { exact: true })).toBeVisible()
  await panel.getByRole('spinbutton', { name: '场景故事顺序（空白为未知）', exact: true }).fill('21')
  await panel.getByRole('button', { name: '确认保存场景时点', exact: true }).click()
  await expect(panel.getByText('开锁能力：已学会', { exact: true })).toBeVisible()
  await panel.getByRole('button', { name: '查看场景绑定历史', exact: true }).click()
  await expect(panel.getByText(/修订0 · 回忆之前 · 故事序10/)).toBeVisible()
  const saved = await (await page.request.get(`/api/chapters/${target.id}`)).json()
  expect(saved.revision).toBe(1)
})
