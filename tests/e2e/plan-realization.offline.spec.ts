import { test, expect } from '@playwright/test'

test('自动批次：继承的计划由当前正文兑现为同一正式事件与状态', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await (
    await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 计划兑现' } })
  ).json()
  const ids: string[] = []
  async function add(record: object) {
    const before = await (await page.request.get(`/api/branches/${novel.branch_id}/story-graph`)).json()
    const response = await page.request.post(`/api/branches/${novel.branch_id}/story-records`, {
      headers,
      data: { base_story_revision: before.story_revision, record },
    })
    expect(response.ok(), await response.text()).toBeTruthy()
    const after = await response.json()
    const created = after.records.find((r: { id: string }) => !ids.includes(r.id))
    ids.push(created.id)
    return created.id as string
  }
  const actor = await add({ kind: 'character', title: '沈砚' })
  const event = await add({
    kind: 'event',
    title: '测量潮水',
    status: 'planned',
    story_order: 10,
    participants: [actor],
  })
  await add({
    kind: 'state',
    title: '测量记录',
    character_id: actor,
    event_id: event,
    category: 'knowledge',
    key: '潮水记录',
    value: '完成测量',
    audience: 'character',
    truth: 'planned',
  })
  const source = await (await page.request.get(`/api/branches/${novel.branch_id}/story-graph`)).json()
  const response = await page.request.post('/api/workflows', {
    headers,
    data: {
      novel_id: novel.id,
      source_branch_id: novel.branch_id,
      inherit_story_record_ids: ids,
      inherit_source_revision: source.story_revision,
      mode: 'automatic',
      brief: '沈砚测量潮水并留下记录，公开核验后收束。',
      chapters: 1,
      min_chars: 200,
      max_chars: 600,
      account: 'offline',
      adoption: 'automatic',
      structured_maintenance: true,
    },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  const task = await response.json()
  await expect
    .poll(async () => (await (await page.request.get(`/api/tasks/${task.id}`)).json()).status)
    .toBe('production_complete')
  const final = await (await page.request.get(`/api/tasks/${task.id}`)).json()
  const snapshot = await (await page.request.get(`/api/branches/${task.branch_id}/story-graph`)).json()
  expect(snapshot.records).toHaveLength(3)
  const actualEvent = snapshot.records.find((r: { kind: string }) => r.kind === 'event')
  const actualState = snapshot.records.find((r: { kind: string }) => r.kind === 'state')
  expect(actualEvent.payload.status).toBe('actual')
  expect(actualState.payload.truth).toBe('actual')
  expect(actualState.revision).toBe(1)
  expect(actualState.invalid_reasons).toEqual([])
  expect(actualState.payload.sources).toHaveLength(1)
  expect(actualState.payload.event_id).toBe(actualEvent.id)
  expect(final.result.structure_history).toHaveLength(1)
  expect(final.result.structure_inheritance.records.map((r: { target_id: string }) => r.target_id)).toContain(
    actualState.id,
  )
  const unchanged = await (await page.request.get(`/api/branches/${novel.branch_id}/story-graph`)).json()
  expect(unchanged).toEqual(source)
})
