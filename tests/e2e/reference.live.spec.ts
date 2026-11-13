import { test, expect } from '@playwright/test'

test('日常实例：独立参考学习手工流程，无模型调用或原稿变更', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const before = await (await page.request.get('/api/config')).json()
  const marker = `后台验证参考-${Date.now()}`
  const response = await page.request.post('/api/library/import', {
    headers,
    multipart: {
      file: {
        name: `${marker}.txt`,
        mimeType: 'text/plain',
        buffer: Buffer.from('她核对刻度，发现读数只解释了部分磨损。她记下偏差，决定再找一位见证者。'),
      },
    },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  await page.reload()
  await page.getByRole('button', { name: '资料书库', exact: true }).click()
  await page.getByText(`${marker}.txt`, { exact: false }).first().click()
  const reader = page.getByRole('textbox', { name: '资料原文' })
  await reader.focus()
  await reader.press('ControlOrMeta+A')
  await page.getByRole('button', { name: '拆解这段写法', exact: true }).click()
  await page.getByRole('button', { name: '添加手工写法卡', exact: true }).click()
  await page.getByText('编辑卡片 1：观察、解释、写法与证据', { exact: true }).click()
  await page.getByRole('textbox', { name: '原文观察', exact: true }).fill('只取得部分答案')
  await page.getByRole('textbox', { name: '作用解释（分析判断）', exact: true }).fill('可能使下一步行动有依据')
  await page.getByRole('textbox', { name: '可迁移写法', exact: true }).fill('用局部证据推动下一步选择')
  await page.getByRole('textbox', { name: '适用条件', exact: true }).fill('角色仍有未解决问题')
  await page.getByRole('checkbox', { name: /本次采用/ }).check()
  await page.getByRole('button', { name: '保存本次规则与方案', exact: true }).click()
  await expect(
    page.getByText('已保存采用规则及本次方案。旧结果仍保留在历史中，新动作使用此版本。'),
  ).toBeVisible()
  await page.reload()
  await page.getByRole('button', { name: '资料书库', exact: true }).click()
  await page.getByRole('button', { name: new RegExp(`${marker}.txt · .* · 修订`) }).click()
  await expect(page.getByRole('checkbox', { name: /本次采用/ })).toBeChecked()
  const after = await (await page.request.get('/api/config')).json()
  expect(after.budget).toEqual(before.budget)
})
