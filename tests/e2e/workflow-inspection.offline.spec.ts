import { expect, test } from '@playwright/test'

test('十处对账保留作者选区和修订，正文改变后显示原判定与过期状态', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const created = await page.request.post('/api/novels', {
    headers,
    data: { title: '后台验证 · 遗铃十处对账' },
  })
  expect(created.ok()).toBe(true)
  const novel = await created.json()
  const chapterResponse = await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
    headers,
    data: { title: '拾铃' },
  })
  expect(chapterResponse.ok()).toBe(true)
  const chapter = await chapterResponse.json()
  const text = '😀学徒拾起铜铃。'
  const saved = await page.request.put(`/api/chapters/${chapter.id}`, {
    headers,
    data: { base_revision: 0, content: text },
  })
  expect(saved.ok()).toBe(true)
  const revision = (await saved.json()).revision

  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  const panel = page.getByRole('region', { name: '十处对账体检', exact: true })
  await panel.getByText('本次依据与未核查项', { exact: true }).click()
  await expect(panel.getByRole('button', { name: /^核对 \d{2}$/ })).toHaveCount(10)
  await panel.getByRole('button', { name: '核对 03' }).click()
  await panel.getByRole('combobox', { name: '作者判定', exact: true }).selectOption('green')
  await panel.getByLabel('对照的章纲、约定或规则', { exact: true }).fill('人物档案中的学徒身份。')
  await panel.getByLabel('判定理由', { exact: true }).fill('本章以学徒称谓写明动作主体。')
  await panel.getByRole('combobox', { name: '引用章节', exact: true }).selectOption(chapter.id)
  const excerpt = panel.getByRole('textbox', { name: '保存稿（选择要引用的文字）', exact: true })
  await expect(excerpt).toHaveValue(text)
  await excerpt.press('ControlOrMeta+a')
  await panel.getByRole('button', { name: '引用选区' }).press('Enter')
  await expect(panel.getByRole('button', { name: '移除引文 1' })).toBeVisible()
  await panel.getByLabel('章数', { exact: true }).fill('1')
  await panel.getByLabel('有效字符下限', { exact: true }).fill('1')
  const createdReport = page.waitForResponse(
    (response) => response.request().method() === 'POST' && response.url().endsWith('/workflow-inspections'),
  )
  await panel.getByRole('button', { name: '生成体检单' }).click()
  const reportResponse = await createdReport
  expect(reportResponse.ok(), await reportResponse.text()).toBe(true)
  const report = await reportResponse.json()
  const review = report.items.find((item: { check_id: string }) => item.check_id === '03')
  expect(review.author_review.evidence).toEqual([
    {
      chapter_id: chapter.id,
      revision,
      start: 0,
      end: text.length,
      quote: text,
    },
  ])
  const result = panel.getByRole('region', { name: '体检单结果', exact: true })
  await expect(result.getByText('依据仍为当前版本')).toBeVisible()
  await expect(result.getByText(/作者核对 · 通过/)).toBeVisible()

  const changed = await page.request.put(`/api/chapters/${chapter.id}`, {
    headers,
    data: { base_revision: revision, content: '师姐举起铜铃。' },
  })
  expect(changed.ok()).toBe(true)
  await panel.getByRole('button', { name: '重新读取依据' }).click()
  await expect(result.getByText('部分依据已更新，过期项待复检')).toBeVisible()
  const identity = result.getByRole('listitem').filter({ hasText: '03 人物身份、关系与状态' })
  await expect(identity.getByText('原判定 通过')).toBeVisible()
  await expect(identity.locator('blockquote')).toContainText(text)
})
