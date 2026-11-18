import { expect, test } from '@playwright/test'

test('作品快照预览保留旧章稿，检查并发基线后原位产生新修订', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const created = await page.request.post('/api/novels', {
    headers,
    data: { title: '后台验证 · 遗铃作品快照' },
  })
  expect(created.ok()).toBe(true)
  const novel = await created.json()
  const chapterResponse = await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
    headers,
    data: { title: '铃响' },
  })
  expect(chapterResponse.ok()).toBe(true)
  const chapter = await chapterResponse.json()
  const first = '师姐在门内接过铜铃。😀'
  async function save(base_revision: number, content: string): Promise<number> {
    const response = await page.request.put(`/api/chapters/${chapter.id}`, {
      headers,
      data: { base_revision, content },
    })
    expect(response.ok(), await response.text()).toBe(true)
    return (await response.json()).revision
  }
  const firstRevision = await save(0, first)
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  const panel = page.getByRole('region', { name: '作品快照与回滚', exact: true })
  await panel.getByRole('textbox', { name: '快照名称', exact: true }).fill('首稿检查点')
  await panel.getByRole('button', { name: '保存快照', exact: true }).press('Enter')
  await expect(panel.getByRole('status')).toHaveText('已保存作品快照。')

  const second = '学徒在门外收起铜铃。'
  const secondRevision = await save(firstRevision, second)
  await panel.getByRole('button', { name: '预览恢复差异' }).click()
  const effect = panel.getByRole('region', { name: '恢复差异预览' })
  await expect(effect.getByRole('listitem').filter({ hasText: '恢复快照稿' })).toHaveCount(1)
  await effect.getByRole('button', { name: '比较铃响正文' }).click()
  const compare = effect.getByRole('region', { name: '章稿对照' })
  await expect(compare.getByText(first)).toBeVisible()
  await expect(compare.getByText(second)).toBeVisible()
  const third = '师姐改将铜铃挂在檐下。'
  const thirdRevision = await save(secondRevision, third)
  const submit = effect.getByRole('button', { name: '提交恢复' })
  await expect(submit).toBeDisabled()
  await effect.getByRole('checkbox', { name: '我已核对恢复影响' }).check()
  await submit.click()
  await expect(panel.getByRole('alert')).toContainText('请重新预览')

  await panel.getByRole('button', { name: '预览恢复差异' }).click()
  await effect.getByRole('checkbox', { name: '我已核对恢复影响' }).check()
  const committed = page.waitForResponse(
    (response) => response.request().method() === 'POST' && response.url().endsWith('/restore'),
  )
  await submit.click()
  const result = await committed
  expect(result.ok(), await result.text()).toBe(true)
  const receipt = await result.json()
  expect(receipt.recovery_snapshot_id).toBeTruthy()
  const after = await (await page.request.get(`/api/chapters/${chapter.id}`)).json()
  expect(after.content).toBe(first)
  expect(after.revision).toBeGreaterThan(thirdRevision)
  const history = await (await page.request.get(`/api/chapters/${chapter.id}/history`)).json()
  expect(history.find((row: { revision: number }) => row.revision === thirdRevision).content).toBe(third)
})
