import { test, expect } from '@playwright/test'

const SOURCE = '😀渡口掌柜先问用途，又把水路图收回袖中。他只肯给半幅图，却记下了来客的目的地。'

test('书库选段到拆解、方案、试写、对照采纳和历史恢复', async ({ page }) => {
  await page.goto('/')
  const initial = await (await page.request.get('/api/session')).json()
  const novel = await (
    await page.request.post('/api/novels', {
      headers: { 'X-Ainovel-CSRF': initial.csrf },
      data: { title: '后台验证 · 修城器师' },
    })
  ).json()
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await expect(page.getByRole('combobox', { name: '工作版本', exact: true })).not.toHaveValue('')
  await page.getByRole('button', { name: '新建第一章', exact: true }).click()
  await page
    .getByRole('textbox', { name: '章节正文', exact: true })
    .fill('原稿：她来到闸门前，准备检查外露结构。')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByText('已保存', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '资料书库', exact: true }).click()
  // Import through the real authenticated API, then exercise reading/selection in UI.
  const session = await (await page.request.get('/api/session')).json()
  const response = await page.request.post('/api/library/import', {
    headers: { 'X-Ainovel-CSRF': session.csrf },
    multipart: { file: { name: '自拟参考.txt', mimeType: 'text/plain', buffer: Buffer.from(SOURCE) } },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  await page.reload()
  await page.getByRole('button', { name: '资料书库', exact: true }).click()
  await page.getByText('自拟参考.txt', { exact: false }).first().click()
  await expect(page.getByRole('textbox', { name: '资料原文' })).toHaveValue(SOURCE)
  const reader = page.getByRole('textbox', { name: '资料原文' })
  await reader.focus()
  await reader.press('ControlOrMeta+A')
  await page.getByRole('button', { name: '拆解这段写法', exact: true }).click()
  await expect(page.getByRole('heading', { name: '参考仿写工作区' })).toBeVisible()
  await page.getByRole('button', { name: '拆解选段写法', exact: true }).click()
  await page.getByRole('button', { name: '载入分析卡供编辑和选择（尚未采用）', exact: true }).click()
  await page.getByRole('checkbox', { name: /本次采用/ }).check()
  await page.getByRole('button', { name: /明确使用顶部当前工作版本/ }).click()
  await page.getByLabel('本次目标章节').selectOption({ index: 1 })
  await page.getByLabel('本次原创情节与结果').fill('检查闸门外露磨损，但拿不到维修记录，不修门。')
  await page.getByLabel('固定条件（每行一条）').fill('不修门')
  await page.getByRole('button', { name: '保存本次规则与方案', exact: true }).click()
  await page.getByRole('button', { name: '生成本书应用方案', exact: true }).click()
  await page.getByRole('button', { name: '载入方案供编辑确认', exact: true }).click()
  await page.getByRole('button', { name: '保存本次规则与方案', exact: true }).click()
  await page.getByRole('button', { name: '按保存方案原创试写', exact: true }).click()
  await expect(page.getByLabel('参考试写候选')).toHaveValue(/螺栓/)
  await page.reload()
  await page.getByRole('button', { name: '资料书库', exact: true }).click()
  await page.getByRole('button', { name: /自拟参考.txt · .* · 修订/ }).click()
  await expect(page.getByLabel('参考试写候选')).toHaveValue(/螺栓/)
  await page.getByText('阶段任务与历史规则版本', { exact: true }).click()
  await expect(page.getByText('reference.draft · 候选已生成', { exact: false })).toBeVisible()
  await expect(page.getByRole('region', { name: '参考写法对照', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '对照后采纳到目标正文', exact: true }).click()
  await expect(page.getByText('已采纳为新正文修订，原稿历史保留。故事记录可按需整理。')).toBeVisible()
  await page.getByRole('button', { name: '回到写作页继续编辑或恢复版本' }).click()
  await expect(page.getByRole('textbox', { name: '章节正文', exact: true })).toContainText('螺栓')
  await page.getByRole('button', { name: '版本历史', exact: true }).click()
  const originalHistory = page
    .locator('.history details')
    .filter({ hasText: '原稿：她来到闸门前，准备检查外露结构。' })
  await originalHistory.locator('summary').click()
  await expect(originalHistory.locator('pre')).toHaveText('原稿：她来到闸门前，准备检查外露结构。')
})
