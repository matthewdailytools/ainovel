import { test, expect } from './read-only-live'

test('日常实例：结构继承只读查看、默认不选，仅维护测试作品位置', async ({ page, readOnlyStory }) => {
  await page.goto('/')
  const previewResponse = await page.request.get(`/api/branches/${readOnlyStory.branchId}/story-inheritance`)
  expect(previewResponse.ok(), await previewResponse.text()).toBeTruthy()
  const preview = await previewResponse.json()
  await expect(page.getByRole('combobox', { name: '作品', exact: true })).toHaveValue(readOnlyStory.novelId)
  await expect(page.getByRole('combobox', { name: '工作版本', exact: true })).toHaveValue(
    readOnlyStory.branchId,
  )
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  const summary = page.getByText('新版本继承人物与结构计划 · 已选0项', { exact: true })
  await summary.click()
  const panel = summary.locator('..')
  const choices = panel.getByRole('checkbox')
  const eligible = preview.items.filter((r: { eligible: boolean }) => r.eligible)
  await expect(choices).toHaveCount(eligible.length)
  for (const choice of await choices.all()) await expect(choice).not.toBeChecked()
})
