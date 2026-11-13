import { test, expect } from '@playwright/test'

test('日常实例：时点绑定只读入口与保存稿范围，费用不变', async ({ page }) => {
  await page.goto('/')
  const before = await (await page.request.get('/api/config')).json()
  await expect(page.getByRole('textbox', { name: '章节标题', exact: true })).toBeVisible()
  await page.getByText('场景时点与人物当前状态', { exact: true }).click()
  const panel = page.getByRole('region', { name: '场景时点绑定', exact: true })
  await expect(panel.getByRole('button', { name: '确认保存场景时点', exact: true })).toBeDisabled()
  await expect(panel.getByRole('combobox', { name: '场景信息视角', exact: true })).toHaveValue('author')
  await expect(panel.getByRole('region', { name: '当前光标有效状态', exact: true })).toBeVisible()
  const after = await (await page.request.get('/api/config')).json()
  expect(after.budget).toEqual(before.budget)
})
