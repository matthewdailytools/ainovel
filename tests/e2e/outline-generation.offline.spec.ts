import { expect, test } from '@playwright/test'
import type { components } from '../../frontend/api-schema'

test('AI 大纲由作者载入修改、保存，再确认', async ({ page }) => {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const novel = await page.request.post('/api/novels', { headers, data: { title: '遗铃 · AI 大纲验收' } })
  expect(novel.ok(), await novel.text()).toBe(true)
  const { id, branch_id } = await novel.json()
  for (const title of ['门外寻铃', '门内验证']) {
    const chapter = await page.request.post(`/api/branches/${branch_id}/chapters`, {
      headers,
      data: { title },
    })
    expect(chapter.ok(), await chapter.text()).toBe(true)
  }
  const endpoint = `/api/branches/${branch_id}/outline-workspace`
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(id)
  await page.getByRole('button', { name: '大纲与台账', exact: true }).click()
  await page.getByLabel('本次规划要求').fill('先写门外找铃，再在门内揭示规则。')
  await page.getByRole('button', { name: '生成四级大纲候选', exact: true }).click()
  const load = page.getByRole('button', { name: '载入候选供编辑', exact: true })
  await expect(load).toBeEnabled()
  expect((await (await page.request.get(endpoint)).json()).draft).toBeNull()
  await load.click()
  const summary = page.getByRole('textbox', { name: '总纲', exact: true })
  await expect(summary).toHaveValue('沿铃声寻找师姐。')
  await expect(page.getByText('门内验证 · 章纲')).toBeVisible()
  await summary.fill('确认铜铃来历，最终把铃还给师姐。')
  await page.getByRole('button', { name: '保存大纲草稿' }).click()
  const confirm = page.getByRole('button', { name: '确认已保存大纲' })
  await expect(confirm).toBeEnabled()
  expect((await (await page.request.get(endpoint)).json()).confirmed).toBeNull()
  await confirm.click()
  await expect(page.getByText('已确认大纲修订 2', { exact: true })).toBeVisible()
  const final = await (await page.request.get(endpoint)).json()
  expect(final.confirmed.plan.summary).toBe('确认铜铃来历，最终把铃还给师姐。')
})

test('大纲生成可显式选用已保存手法卡，过期修订须重新核对', async ({ page }) => {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const created = await page.request.post('/api/novels', {
    headers,
    data: { title: '水尺 · 手法卡生成验收' },
  })
  expect(created.ok(), await created.text()).toBe(true)
  const { id, branch_id } = await created.json()
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(id)
  await page.getByRole('button', { name: '新建第一章', exact: true }).click()

  const quote = '渡口掌柜先问用途，又把水路图收回袖中。他只肯给半幅图，却记下了来客的目的地。'
  const filename = '水尺手法卡合成原文.txt'
  const imported = await page.request.post('/api/library/import', {
    headers,
    multipart: { file: { name: filename, mimeType: 'text/plain', buffer: Buffer.from(quote) } },
  })
  expect(imported.ok(), await imported.text()).toBe(true)
  await page.getByRole('button', { name: '资料书库', exact: true }).click()
  await page.getByText(filename, { exact: false }).first().click()
  const reader = page.getByRole('textbox', { name: '资料原文' })
  await expect(reader).toHaveValue(quote)
  await reader.press('ControlOrMeta+A')
  await page.getByRole('button', { name: '拆解这段写法', exact: true }).click()
  await page.getByRole('button', { name: '拆解选段写法', exact: true }).click()
  await page.getByRole('button', { name: /载入分析卡供编辑和选择/ }).click()
  await page.getByRole('button', { name: /明确使用顶部当前工作版本/ }).click()
  await expect(page.getByRole('checkbox', { name: /本次采用/ })).not.toBeChecked()
  const saved = page.waitForResponse(
    (response) => response.url().includes('/reference-sessions/') && response.request().method() === 'PATCH',
  )
  await page.getByRole('button', { name: '保存本次规则与方案', exact: true }).click()
  const savedResponse = await saved
  expect(savedResponse.ok(), await savedResponse.text()).toBe(true)
  const session = await savedResponse.json()

  const root = `/api/branches/${branch_id}/outline-workspace`
  const endpoint = root + '/generation'
  async function getCard() {
    const response = await page.request.get(root + '/reference-options')
    expect(response.ok(), await response.text()).toBe(true)
    const cards: components['schemas']['OutlineCardOption'][] = await response.json()
    expect(cards).toHaveLength(1)
    return cards[0]
  }
  const card = await getCard()
  await page.getByRole('button', { name: '大纲与台账', exact: true }).click()
  const panel = page.getByRole('region', { name: '大纲生成' })
  await page.setViewportSize({ width: 390, height: 820 })
  const choices = panel.getByText('已保存的手法卡（可选）', { exact: true })
  await choices.focus()
  await expect(choices).toBeFocused()
  await choices.press('Enter')
  const picker = panel.getByRole('combobox', { name: '选用手法卡', exact: true })
  await expect(picker).toHaveValue('')
  await picker.selectOption(card.version_id!)
  await expect(panel.getByText(`适用条件 · ${card.conditions}`, { exact: true })).toBeVisible()
  await expect(panel.getByText(`须替换 · ${card.exceptions}`, { exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)

  const revised = await page.request.patch(`/api/reference-sessions/${session.id}`, {
    headers,
    data: {
      base_revision: session.revision,
      branch_id,
      goal: session.payload.goal,
      cards: session.payload.cards.map((item: { id: string }) =>
        item.id === card.card_id ? { ...item, conditions: '主人公必须还有一条可验证的新线索。' } : item,
      ),
      selected_ids: [],
    },
  })
  expect(revised.ok(), await revised.text()).toBe(true)
  const latest = await getCard()
  expect(latest.version_id).not.toBe(card.version_id)
  const generate = panel.getByRole('button', { name: '生成四级大纲候选', exact: true })
  const request = () =>
    page.waitForResponse((entry) => entry.url().endsWith(endpoint) && entry.request().method() === 'POST')
  const rejected = request()
  await expect(generate).toBeEnabled()
  await generate.press('Enter')
  const conflict = await rejected
  expect(conflict.status(), await conflict.text()).toBe(409)
  expect(conflict.request().postDataJSON().reference_cards[0].version_id).toBe(card.version_id)
  expect(await (await page.request.get(endpoint)).json()).toEqual([])
  await expect(panel.getByRole('alert')).toContainText('请移除失效项并核对所需修订')
  await expect(generate).toBeDisabled()
  const removeStale = panel.getByRole('button', { name: '移除失效手法卡', exact: true })
  await expect(removeStale).toBeEnabled()
  await removeStale.press('Enter')
  await picker.selectOption(latest.version_id!)
  await expect(panel.getByText(`适用条件 · ${latest.conditions}`, { exact: true })).toBeVisible()
  const submitted = request()
  await expect(generate).toBeEnabled()
  await generate.press('Enter')
  const queued = await submitted
  expect(queued.status(), await queued.text()).toBe(202)
  const reference = {
    session_id: latest.session_id,
    card_id: latest.card_id,
    revision: latest.revision,
    version_id: latest.version_id,
  }
  expect(queued.request().postDataJSON().reference_cards).toEqual([reference])

  const load = panel.getByRole('button', { name: '载入候选供编辑', exact: true })
  await expect(load).toBeEnabled()
  const [generation] = await (await page.request.get(endpoint)).json()
  const artifact = await (await page.request.get(`/api/artifacts/${generation.artifact_id}`)).json()
  expect(artifact.basis.outline_generation.setting_basis.reference_cards).toEqual([latest])
  expect(JSON.stringify(artifact.basis)).not.toContain(quote)
  await load.press('Enter')
  await expect(
    page.getByText(`${latest.technique} · 手法卡修订 ${latest.revision}`, { exact: true }),
  ).toBeVisible()
  await page.getByRole('button', { name: '保存大纲草稿' }).click()
  const confirm = page.getByRole('button', { name: '确认已保存大纲' })
  await expect(confirm).toBeEnabled()
  await confirm.press('Enter')
  await expect(page.getByText('已确认大纲修订 2', { exact: true })).toBeVisible()
  const final = await (await page.request.get(root)).json()
  expect(final.confirmed.plan.volumes[0].units[0].chapters[0].reference_cards).toEqual([reference])
})
