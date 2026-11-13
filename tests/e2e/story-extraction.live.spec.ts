import { test, expect } from './read-only-live'

test('日常实例：结构整理范围与历史只读入口，仅维护测试作品位置', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('combobox', { name: '工作版本', exact: true })).not.toHaveValue('')
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  await page.getByText('按保存稿整理人物、事件、状态和伏笔候选', { exact: true }).click()
  const panel = page.getByRole('region', { name: '结构化整理与确认', exact: true })
  await expect(panel.getByRole('button', { name: '从所选保存稿生成结构候选', exact: true })).toBeDisabled()
  await expect(panel.getByRole('combobox', { name: '保存的结构候选', exact: true })).toBeVisible()
})
