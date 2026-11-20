import { expect, test, type Page, type Request } from '@playwright/test'

async function enterProjectWorkbench(page: Page, title: string) {
  await page.goto('/')
  await page.getByText('＋ 新建作品', { exact: true }).click()
  await page.getByRole('textbox', { name: '作品名称', exact: true }).fill(title)
  await page.getByRole('button', { name: '创建并开始写作', exact: true }).click()
  await expect(
    page.getByRole('combobox', { name: '作品', exact: true }).locator('option:checked'),
  ).toHaveText(title)
  await page.getByRole('button', { name: '立项台', exact: true }).click()
}

test('窄屏助手用键盘切换视图并将焦点还给原入口', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 })
  await enterProjectWorkbench(page, '助手窄屏键盘复核')
  const toggle = page.getByRole('button', { name: '立项助手', exact: true })
  await toggle.focus()
  await toggle.press('Enter')
  const rail = page.getByRole('complementary', { name: '立项助手' })
  await expect(rail.getByRole('heading', { name: '立项助手' })).toBeFocused()
  const conversation = rail.getByRole('tab', { name: '对话' })
  await conversation.focus()
  await conversation.press('ArrowRight')
  const history = rail.getByRole('tab', { name: '历史' })
  await expect(history).toBeFocused()
  await expect(history).toHaveAttribute('aria-selected', 'true')
  await history.press('ArrowRight')
  const tools = rail.getByRole('tab', { name: '本台工具' })
  await expect(tools).toBeFocused()
  await expect(rail.getByRole('button', { name: '返回本台工具' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)
  await tools.press('Escape')
  await expect(rail).toBeHidden()
  await expect(toggle).toBeFocused()
})

test('切换工作台后再次打开历史不会另发原快捷请求', async ({ page }) => {
  await enterProjectWorkbench(page, '助手切台复核')
  const project = page.locator('#bench-project')
  await project
    .locator('summary')
    .filter({ hasText: /^立项台/ })
    .click()
  const sent: Request[] = []
  page.on('request', (request) => {
    if (request.method() === 'POST' && /assistant\/conversations\/[^/]+\/turns$/.test(request.url()))
      sent.push(request)
  })
  await project.getByRole('textbox', { name: '核心定位', exact: true }).fill('学徒找到一枚不鸣铜铃。')
  await project.getByRole('button', { name: '润色主题 · 核心定位', exact: true }).click()
  const answer = '已按本轮范围生成建议，请用当前稿复核。'
  const rail = page.locator('.wb-assistant-rail')
  await expect(rail.getByText(answer, { exact: true })).toBeVisible()
  expect(sent).toHaveLength(1)
  page.on('dialog', (dialog) => void dialog.accept())
  await page.getByRole('button', { name: '设定台', exact: true }).click()
  await expect(rail.getByRole('heading', { name: '设定助手', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '立项台', exact: true }).click()
  const restored = page.waitForResponse(
    (response) =>
      response.ok() &&
      response.request().method() === 'GET' &&
      /assistant\/conversations\/[^/]+\/turns\?/.test(response.url()),
  )
  await rail.getByRole('button', { name: '刷新历史', exact: true }).click()
  await restored
  await expect(rail.getByText(answer, { exact: true })).toHaveCount(1)
  expect(sent).toHaveLength(1)
})
