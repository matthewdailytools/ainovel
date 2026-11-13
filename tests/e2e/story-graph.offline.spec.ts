import { test, expect } from '@playwright/test'

test('长篇工作台：人物、原文事件、时点状态和多线图', async ({ page }) => {
  await page.goto('/')
  // Each case creates its own marked work; the test server is isolated from real manuscripts.
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await (
    await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 长篇人物时点' } })
  ).json()
  const chapter = await (
    await page.request.post(`/api/branches/${novel.branch_id}/chapters`, { headers, data: { title: '渡口' } })
  ).json()
  const text = '😀宁禾看懂暗号，收起铜牌。'
  await page.request.put(`/api/chapters/${chapter.id}`, {
    headers,
    data: { base_revision: 0, content: text },
  })
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  const workspace = page.getByRole('region', { name: '长篇人物时点与多线工作台' })
  await workspace.getByRole('textbox', { name: '记录名称', exact: true }).fill('宁禾')
  await workspace.getByRole('textbox', { name: '别名（每行一个）', exact: true }).fill('小宁')
  await workspace.getByRole('button', { name: '保存长篇记录', exact: true }).click()
  await expect(workspace.getByText('已保存；关联旧版本的记录需重新核对。')).toBeVisible()
  await workspace.getByRole('button', { name: '新建事件', exact: true }).click()
  await workspace.getByRole('textbox', { name: '记录名称', exact: true }).fill('认出暗号')
  await workspace.getByRole('spinbutton', { name: '故事顺序（空白为未知）' }).fill('10')
  await workspace.getByRole('combobox', { name: '发生状态', exact: true }).selectOption('actual')
  await workspace.getByRole('checkbox', { name: '宁禾', exact: true }).check()
  await workspace.getByText('从保存稿选区关联原文', { exact: true }).click()
  await workspace.getByRole('combobox', { name: '来源章节', exact: true }).selectOption(chapter.id)
  const source = workspace.getByRole('textbox', { name: '保存稿原文（选择要引用的范围）', exact: true })
  await expect(source).toHaveValue(text)
  await source.focus()
  await source.press('ControlOrMeta+A')
  await workspace.getByRole('button', { name: '关联所选原文', exact: true }).click()
  await workspace.getByRole('button', { name: '保存长篇记录', exact: true }).click()
  await expect(workspace.getByRole('button', { name: /认出暗号 · 故事序10/ })).toBeVisible()
  await workspace.getByRole('button', { name: '新建时点状态', exact: true }).click()
  await workspace.getByRole('textbox', { name: '记录名称', exact: true }).fill('暗号认知')
  await workspace.getByRole('combobox', { name: '所属人物', exact: true }).selectOption({ label: '宁禾' })
  await workspace
    .getByRole('combobox', { name: '获得或变化事件', exact: true })
    .selectOption({ label: '认出暗号' })
  await workspace.getByRole('textbox', { name: '状态事项', exact: true }).fill('暗号')
  await workspace.getByRole('textbox', { name: '该时点的状态值', exact: true }).fill('已知')
  await workspace.getByRole('combobox', { name: '状态性质', exact: true }).selectOption('actual')
  if (!(await source.isVisible())) await workspace.getByText('从保存稿选区关联原文', { exact: true }).click()
  await source.focus()
  await source.press('ControlOrMeta+A')
  await workspace.getByRole('button', { name: '关联所选原文', exact: true }).click()
  await workspace.getByRole('button', { name: '保存长篇记录', exact: true }).click()
  await workspace.getByText('查询人物在故事时点知道什么、具有什么', { exact: true }).click()
  await workspace.getByRole('spinbutton', { name: '查询故事顺序', exact: true }).fill('9')
  await workspace.getByRole('button', { name: '查询有效状态', exact: true }).click()
  await expect(workspace.getByText('有效状态 0；冲突组 0；未知或待判断 0')).toBeVisible()
  await workspace.getByRole('spinbutton', { name: '查询故事顺序', exact: true }).fill('10')
  await workspace.getByRole('button', { name: '查询有效状态', exact: true }).click()
  await expect(workspace.getByText('有效状态 1；冲突组 0；未知或待判断 0')).toBeVisible()
  await workspace.getByRole('combobox', { name: '查看方式', exact: true }).selectOption('dependencies')
  await workspace.getByRole('button', { name: '编辑认出暗号', exact: true }).press('Enter')
  await expect(workspace.getByRole('textbox', { name: '记录名称', exact: true })).toHaveValue('认出暗号')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.locator('html').evaluate((element) => {
    element.style.fontSize = '200%'
  })
  await expect(workspace.getByRole('button', { name: '保存长篇记录', exact: true })).toBeEnabled()
  const width = await page.locator('body').evaluate((element) => element.scrollWidth)
  expect(width).toBeLessThanOrEqual(390)
  await page.screenshot({ path: 'test-results/story-graph-narrow.png' })
})
