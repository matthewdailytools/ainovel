import { test, expect } from '@playwright/test'

test('场景候选：原文核对、明确归档作者旧绑定、历史与正文保留', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await (
    await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 场景范围候选' } })
  ).json()
  const chapter = await (
    await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
      headers,
      data: { title: '渡口问话' },
    })
  ).json()
  const text = '😀宁禾在渡口停步。\n她决定先问用途。'
  const saved = await (
    await page.request.put(`/api/chapters/${chapter.id}`, {
      headers,
      data: { base_revision: 0, content: text },
    })
  ).json()
  const context = await (await page.request.get(`/api/branches/${novel.branch_id}/context`)).json()
  const oldResponse = await page.request.post(
    `/api/branches/${novel.branch_id}/chapters/${chapter.id}/scene-times`,
    {
      headers,
      data: {
        base_story_revision: context.branch.story_revision,
        scene: {
          title: '作者原绑定',
          source_revision: saved.revision,
          start: 0,
          end: text.length,
          story_order: 5,
          story_time: '作者手动确认',
          audience: 'author',
          character_id: null,
          note: '保留旧范围',
        },
      },
    },
  )
  expect(oldResponse.ok(), await oldResponse.text()).toBeTruthy()
  const old = (await oldResponse.json()).scenes[0]
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  await page.getByText('按保存稿整理人物、事件、状态和伏笔候选', { exact: true }).click()
  const panel = page.getByRole('region', { name: '结构化整理与确认', exact: true })
  await panel.getByRole('checkbox', { name: '1 · 渡口问话', exact: true }).check()
  await panel.getByRole('checkbox', { name: '同时提议新正文的场景范围', exact: true }).check()
  const dispatched = page.waitForResponse(
    (r) => r.url().endsWith('/story-extractions') && r.request().method() === 'POST',
  )
  await panel.getByRole('button', { name: '从所选保存稿生成结构候选', exact: true }).click()
  const extraction = await (await dispatched).json()
  expect(extraction.result.scene_contract_version).toBe('scene-event-refs/2')
  await panel.getByRole('checkbox', { name: '采用人物：宁禾', exact: true }).check()
  await panel.getByRole('checkbox', { name: '采用场景：本章场景', exact: true }).check()
  await expect(
    panel.getByText('保留未知的原因：本测试仅有范围依据，未独立确认故事时点。', { exact: true }),
  ).toBeVisible()
  await panel.getByText('核对场景原文', { exact: true }).click()
  await expect(
    panel.getByRole('region', { name: '场景范围候选', exact: true }).locator('blockquote'),
  ).toContainText(text)
  const failed = page.waitForResponse((r) => r.url().endsWith('/adopt') && r.request().method() === 'POST')
  await panel.getByRole('button', { name: '确认采用所选结构（不修改正文）', exact: true }).click()
  expect((await failed).status()).toBe(409)
  await panel
    .getByRole('checkbox', { name: '确认归档旧绑定并采用所选新范围（历史保留）', exact: true })
    .check()
  await page.setViewportSize({ width: 320, height: 800 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
  const adopt = panel.getByRole('button', { name: '确认采用所选结构（不修改正文）', exact: true })
  await adopt.focus()
  await expect(adopt).toBeFocused()
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.evaluate(() => {
    document.documentElement.style.fontSize = '200%'
  })
  await expect(adopt).toBeVisible()
  await adopt.click()
  await expect(
    panel.getByText('所选结构与场景已采用，旧绑定历史保留，正文未改变。', { exact: true }),
  ).toBeVisible()
  const after = await (
    await page.request.get(`/api/branches/${novel.branch_id}/chapters/${chapter.id}/scene-times`)
  ).json()
  expect(after.scenes.find((r: { id: string }) => r.id === old.id).archived).toBe(1)
  const active = after.scenes.filter((r: { archived: number }) => !r.archived)
  expect(active).toHaveLength(1)
  expect(active[0].payload.source_revision).toBe(saved.revision)
  expect(active[0].payload.end).toBe(text.length)
  const basis: unknown = active[0].payload.order_basis
  expect(basis).toMatchObject({
    version: 'scene-event-refs/2',
    kind: 'unknown',
    reason: '本测试仅有范围依据，未独立确认故事时点。',
    refs: [],
  })
  expect(active[0].payload.story_order).toBeNull()
  expect((await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content).toBe(text)
  const history = await (
    await page.request.get(
      `/api/branches/${novel.branch_id}/chapters/${chapter.id}/scene-times/${old.id}/history`,
    )
  ).json()
  expect(history).toHaveLength(2)

  await page.getByRole('button', { name: '写作', exact: true }).click()
  await expect(page.getByRole('textbox', { name: '章节标题', exact: true })).toHaveValue('渡口问话')
  await page.getByText('场景时点与人物当前状态', { exact: true }).click()
  await page.getByRole('button', { name: /^本章场景 · 范围已保存，时点未知/ }).click()
  await expect(
    page.getByRole('spinbutton', { name: '场景故事顺序（空白为未知）', exact: true }),
  ).toHaveJSProperty('readOnly', true)
  await page.getByRole('textbox', { name: '场景名称', exact: true }).fill('仅修改场景名称')
  await page
    .getByRole('textbox', { name: '时点确认说明', exact: true })
    .fill('仅补充作者说明，原时点依据和未知保持。')
  await page.getByRole('button', { name: '确认保存场景时点', exact: true }).click()
  await expect(
    page.getByText('场景范围已保存，故事时点仍未知；未据此核实人物当前知识。正文未改变。', { exact: true }),
  ).toBeVisible()
  const edited = await (
    await page.request.get(`/api/branches/${novel.branch_id}/chapters/${chapter.id}/scene-times`)
  ).json()
  const current = edited.scenes.find((r: { id: string }) => r.id === active[0].id)
  expect(current.payload.title).toBe('仅修改场景名称')
  expect(current.payload.note).toBe('仅补充作者说明，原时点依据和未知保持。')
  expect(current.payload.order_basis).toEqual(basis)
  expect(current.payload.story_order).toBeNull()
  expect(await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).toEqual(saved)
})
