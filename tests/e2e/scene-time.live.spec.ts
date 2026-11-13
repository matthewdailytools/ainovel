import { test, expect } from './read-only-live'

test('日常实例：时点绑定只读入口与保存稿范围，仅维护测试作品位置', async ({ page, readOnlyStory }) => {
  const positionSaved = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === `/api/branches/${readOnlyStory.branchId}/reading-state` &&
      response.request().method() === 'PUT' &&
      response.ok(),
  )
  await page.goto('/')
  await expect(page.getByRole('textbox', { name: '章节标题', exact: true })).toBeVisible()
  await page.getByText('场景时点与人物当前状态', { exact: true }).click()
  const panel = page.getByRole('region', { name: '场景时点绑定', exact: true })
  await expect(panel.getByRole('button', { name: '确认保存场景时点', exact: true })).toBeDisabled()
  await expect(panel.getByRole('combobox', { name: '场景信息视角', exact: true })).toHaveValue('author')
  await expect(panel.getByRole('region', { name: '当前光标有效状态', exact: true })).toBeVisible()
  await positionSaved
})
