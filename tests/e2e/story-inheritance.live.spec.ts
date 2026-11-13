import { test, expect } from '@playwright/test'

test('日常实例：结构继承只读查看、默认不选且费用不变', async ({ page }) => {
  await page.goto('/')
  const before = await (await page.request.get('/api/config')).json()
  const novels = await (await page.request.get('/api/novels')).json()
  expect(novels.length).toBeGreaterThan(0)
  const novel = novels[0]
  const branches = await (await page.request.get(`/api/novels/${novel.id}/branches`)).json()
  expect(branches.length).toBeGreaterThan(0)
  const branch = branches[0]
  const previewResponse = await page.request.get(`/api/branches/${branch.id}/story-inheritance`)
  expect(previewResponse.ok(), await previewResponse.text()).toBeTruthy()
  const preview = await previewResponse.json()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('combobox', { name: '工作版本', exact: true }).selectOption(branch.id)
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  const summary = page.getByText('新版本继承人物与结构计划 · 已选0项', { exact: true })
  await summary.click()
  const panel = summary.locator('..')
  const choices = panel.getByRole('checkbox')
  const eligible = preview.items.filter((r: { eligible: boolean }) => r.eligible)
  await expect(choices).toHaveCount(eligible.length)
  for (const choice of await choices.all()) await expect(choice).not.toBeChecked()
  const after = await (await page.request.get('/api/config')).json()
  expect(after.budget).toEqual(before.budget)
})
