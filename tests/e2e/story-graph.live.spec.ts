import { test, expect } from '@playwright/test'

test('日常实例：长篇工作台只读查看，不改变稿件和费用', async ({ page }) => {
  await page.goto('/')
  const before = await (await page.request.get('/api/config')).json()
  await expect(page.getByRole('combobox', { name: '工作版本', exact: true })).not.toHaveValue('')
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  const workspace = page.getByRole('region', { name: '长篇人物时点与多线工作台' })
  await expect(workspace.getByRole('heading', { name: '长篇人物、时点与多线' })).toBeVisible()
  await workspace.getByRole('combobox', { name: '查看方式', exact: true }).selectOption('relations')
  await expect(workspace.getByRole('group', { name: '人物关系图', exact: true })).toBeVisible()
  await workspace.getByRole('combobox', { name: '查看方式', exact: true }).selectOption('table')
  await expect(workspace.getByRole('table')).toBeVisible()
  const after = await (await page.request.get('/api/config')).json()
  expect(after.budget).toEqual(before.budget)
})
