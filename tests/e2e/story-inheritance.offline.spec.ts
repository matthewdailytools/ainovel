import { test, expect } from '@playwright/test'

test('新版本结构继承：明确勾选、依赖漏选阻止、历史隔离和独立身份', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await (
    await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 结构计划继承' } })
  ).json()
  const chapter = await (
    await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
      headers,
      data: { title: '旧版经历' },
    })
  ).json()
  const text = '😀旧版中宁禾已经读到暗号。'
  const saved = await (
    await page.request.put(`/api/chapters/${chapter.id}`, {
      headers,
      data: { base_revision: 0, content: text },
    })
  ).json()
  async function record(payload: object) {
    const before = await (await page.request.get(`/api/branches/${novel.branch_id}/story-graph`)).json()
    const response = await page.request.post(`/api/branches/${novel.branch_id}/story-records`, {
      headers,
      data: { base_story_revision: before.story_revision, record: payload },
    })
    expect(response.ok(), await response.text()).toBeTruthy()
    const after = await response.json()
    return after.records.find(
      (r: { id: string }) => !before.records.some((old: { id: string }) => old.id === r.id),
    )
  }
  const character = await record({
    kind: 'character',
    title: '宁禾',
    aliases: ['小宁'],
    profile: '本版作者明确选择的档案',
  })
  const plan = await record({
    kind: 'event',
    title: '未来调查',
    status: 'planned',
    participants: [character.id],
    story_order: 20,
  })
  await record({
    kind: 'event',
    title: '旧版已经发生',
    status: 'actual',
    story_order: 10,
    participants: [character.id],
    sources: [
      { chapter_id: chapter.id, revision: saved.revision, start: 2, end: text.length, quote: text.slice(2) },
    ],
  })
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  await page.getByText('新版本继承人物与结构计划 · 已选0项', { exact: true }).click()
  await expect(page.getByRole('checkbox', { name: '继承人物档案：宁禾', exact: true })).not.toBeChecked()
  await page.getByText('查看不能继承的历史或失效记录', { exact: true }).click()
  await expect(
    page.getByText('旧版已经发生：已发生、未知或冲突事件不继承为新版本经历', { exact: true }),
  ).toBeVisible()
  await page.getByRole('checkbox', { name: '继承事件计划：未来调查', exact: true }).check()
  await page.getByRole('radio', { name: /^全自动/ }).check()
  await page.getByLabel('作品设定、粗纲与结局方向').fill('测量潮水，公开核验并收束。')
  await page.getByLabel('计划章数', { exact: true }).fill('1')
  await page.getByLabel('每章字数下限').fill('200')
  await page.getByLabel('每章字数上限').fill('600')
  await page.getByRole('combobox', { name: '采纳政策', exact: true }).selectOption('automatic')
  await page.getByRole('checkbox', { name: '每章采用后整理人物、时点、事件和伏笔' }).uncheck()
  const failedPromise = page.waitForResponse(
    (r) => r.url().endsWith('/api/workflows') && r.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '创建独立版本并启动', exact: true }).click()
  expect((await failedPromise).status()).toBe(409)
  await expect(page.getByRole('checkbox', { name: '继承事件计划：未来调查', exact: true })).toBeChecked()
  await page.getByRole('checkbox', { name: '继承人物档案：宁禾', exact: true }).check()
  await page.setViewportSize({ width: 320, height: 800 })
  const inheritedCharacter = page.getByRole('checkbox', { name: '继承人物档案：宁禾', exact: true })
  await inheritedCharacter.focus()
  await expect(inheritedCharacter).toBeFocused()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.evaluate(() => {
    document.documentElement.style.fontSize = '200%'
  })
  await expect(inheritedCharacter).toBeVisible()
  await page.evaluate(() => {
    document.documentElement.style.fontSize = ''
  })
  const startPromise = page.waitForResponse(
    (r) => r.url().endsWith('/api/workflows') && r.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '创建独立版本并启动', exact: true }).click()
  const response = await startPromise
  expect(response.ok(), await response.text()).toBeTruthy()
  const task = await response.json()
  await expect(page.getByText('本批继承的设定与计划 · 2项', { exact: true })).toBeVisible()
  await expect
    .poll(async () => (await (await page.request.get(`/api/tasks/${task.id}`)).json()).status)
    .toBe('production_complete')
  const inherited = await (await page.request.get(`/api/branches/${task.branch_id}/story-graph`)).json()
  expect(inherited.records).toHaveLength(2)
  const nextCharacter = inherited.records.find((r: { kind: string }) => r.kind === 'character')
  const nextPlan = inherited.records.find((r: { kind: string }) => r.kind === 'event')
  expect(nextCharacter.id).not.toBe(character.id)
  expect(nextPlan.id).not.toBe(plan.id)
  expect(nextPlan.payload.participants).toEqual([nextCharacter.id])
  expect(nextPlan.payload.sources).toEqual([])
  expect((await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content).toBe(text)
})
