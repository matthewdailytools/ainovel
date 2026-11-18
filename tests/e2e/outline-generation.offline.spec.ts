import { expect, test } from '@playwright/test'

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
