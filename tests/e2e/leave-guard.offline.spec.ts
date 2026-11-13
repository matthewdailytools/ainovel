import { test, expect } from '@playwright/test'

test('未提交整书设置：取消切换保留输入，确认跨作品切换隔离表单', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novels = []
  for (const title of ['后台验证 · 切换甲', '后台验证 · 切换乙']) {
    const response = await page.request.post('/api/novels', {
      headers,
      data: { title, brief: `${title}原始简报` },
    })
    expect(response.ok()).toBeTruthy()
    novels.push(await response.json())
  }
  await page.reload()
  const selector = page.getByRole('combobox', { name: '作品', exact: true })
  await selector.selectOption(novels[0].id)
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  const brief = page.getByRole('textbox', { name: '作品设定、粗纲与结局方向', exact: true })
  await brief.fill('甲的未提交设定：不应进入乙作品😀')
  page.once('dialog', async (dialog) => {
    expect(dialog.message()).toContain('整书创作设置')
    await dialog.dismiss()
  })
  await selector.selectOption(novels[1].id)
  await expect(selector).toHaveValue(novels[0].id)
  await expect(brief).toHaveValue('甲的未提交设定：不应进入乙作品😀')
  page.once('dialog', (dialog) => dialog.dismiss())
  await page.getByRole('button', { name: '资料书库', exact: true }).click()
  await expect(brief).toBeVisible()
  page.once('dialog', (dialog) => dialog.accept())
  await selector.selectOption(novels[1].id)
  await expect(selector).toHaveValue(novels[1].id)
  await expect(brief).toHaveValue('后台验证 · 切换乙原始简报')
  // An unchanged target form leaves without an unnecessary confirmation.
  page.once('dialog', () => {
    throw new Error('干净表单不应拦截导航')
  })
  await page.getByRole('button', { name: '资料书库', exact: true }).click()
  await expect(brief).toHaveCount(0)
  const tasks = await (await page.request.get('/api/tasks')).json()
  expect(tasks.filter((task: { novel_id: string }) => novels.some((n) => n.id === task.novel_id))).toEqual([])
})

test('未保存场景：拒绝切章保留表单，保存后可切换且不串用范围', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await (
    await page.request.post('/api/novels', {
      headers,
      data: { title: '后台验证 · 场景切换' },
    })
  ).json()
  for (const title of ['场景甲章', '场景乙章']) {
    const chapter = await (
      await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
        headers,
        data: { title },
      })
    ).json()
    await page.request.put(`/api/chapters/${chapter.id}`, {
      headers,
      data: { base_revision: 0, content: `${title}正文😀。` },
    })
  }
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: /场景甲章.*字符/ }).click()
  await page.getByText('场景时点与人物当前状态', { exact: true }).click()
  const title = page.getByRole('textbox', { name: '场景名称', exact: true })
  await title.fill('甲的未保存场景')
  page.once('dialog', (dialog) => dialog.dismiss())
  await page.getByRole('button', { name: /场景乙章.*字符/ }).click()
  await expect(title).toHaveValue('甲的未保存场景')
  await page.getByRole('button', { name: '确认保存场景时点', exact: true }).click()
  await expect(
    page.getByText('场景时点已确认，不修改正文。正文后续变化时须重新复核范围。', { exact: true }),
  ).toBeVisible()
  page.once('dialog', () => {
    throw new Error('保存成功后不应拦截切章')
  })
  await page.getByRole('button', { name: /场景乙章.*字符/ }).click()
  await page.getByText('场景时点与人物当前状态', { exact: true }).click()
  await expect(title).toHaveValue('')
})
