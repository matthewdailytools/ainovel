import { test, expect } from '@playwright/test'
import { manuscriptImportFlow } from './manuscript-import-flow'
test('日常实例：明确标识的测试作品完成长篇导入，费用不变', async ({ page }) => {
  await page.goto('/')
  const before = await (await page.request.get('/api/config')).json()
  await manuscriptImportFlow(page)
  const after = await (await page.request.get('/api/config')).json()
  expect(after.budget).toEqual(before.budget)
})
