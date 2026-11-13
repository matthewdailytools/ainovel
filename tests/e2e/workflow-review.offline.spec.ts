import { test, expect } from '@playwright/test'

test('人工批次：采用正文、打开结构审阅、采用结构后续跑', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await (
    await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 结构人工审阅' } })
  ).json()
  const startedResponse = await page.request.post('/api/workflows', {
    headers,
    data: {
      novel_id: novel.id,
      mode: 'automatic',
      brief: '测量潮水后公开核验并收束。',
      chapters: 1,
      min_chars: 200,
      max_chars: 600,
      account: 'offline',
      adoption: 'manual',
      structured_maintenance: true,
    },
  })
  expect(startedResponse.ok(), await startedResponse.text()).toBeTruthy()
  const task = await startedResponse.json()
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('combobox', { name: '工作版本', exact: true }).selectOption(task.branch_id)
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  await page.getByRole('button', { name: '作者采纳此章', exact: true }).click()
  await page.getByRole('button', { name: '从保存的检查点恢复', exact: true }).click()
  await expect(page.getByRole('heading', { name: '正文已采用，结构整理等待核对' })).toBeVisible()
  await expect(page.getByRole('button', { name: '从保存的检查点恢复', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: '打开此批次的结构审阅', exact: true }).click()
  await page.getByText('按保存稿整理人物、事件、状态和伏笔候选', { exact: true }).click()
  const panel = page.getByRole('region', { name: '结构化整理与确认', exact: true })
  await panel.getByRole('checkbox', { name: '采用人物：沈砚', exact: true }).check()
  await panel.getByRole('button', { name: '确认采用所选结构（不修改正文）', exact: true }).click()
  await expect(
    panel.getByText('所选结构已采用，未选项保留在原产物，正文未改变。', { exact: true }),
  ).toBeVisible()
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  await page.getByRole('button', { name: '从保存的检查点恢复', exact: true }).click()
  await expect
    .poll(async () => (await (await page.request.get(`/api/tasks/${task.id}`)).json()).status)
    .toBe('production_complete')
  const final = await (await page.request.get(`/api/tasks/${task.id}`)).json()
  expect(final.result.structure_history[0].policy).toBe('author_confirmed')
  expect(final.result.waiting_structure).toBeUndefined()
  const maintenance = page.getByRole('checkbox', { name: '每章采用后整理人物、时点、事件和伏笔' })
  await page.setViewportSize({ width: 320, height: 800 })
  await maintenance.focus()
  await expect(maintenance).toBeFocused()
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBeTruthy()
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.evaluate(() => {
    document.documentElement.style.fontSize = '200%'
  })
  await expect(maintenance).toBeVisible()
  await expect(page.getByRole('button', { name: '创建独立版本并启动', exact: true })).toBeVisible()
})
