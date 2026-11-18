import { expect, test } from '@playwright/test'
import { readFile } from 'node:fs/promises'

test('拆解台核对保存卡片，稿件包仅选带已预览的原句', async ({ page }) => {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const novel = await page.request.post('/api/novels', { headers, data: { title: '手法卡稿件包验收' } })
  expect(novel.ok(), await novel.text()).toBe(true)
  const { id, branch_id } = await novel.json()
  const text = '😀渡口的守夜人没有交地图，只交了半幅旧图。'
  const imported = await page.request.post('/api/library/import', {
    headers,
    multipart: { file: { name: '参考卡样本.txt', mimeType: 'text/plain', buffer: Buffer.from(text) } },
  })
  expect(imported.ok(), await imported.text()).toBe(true)
  const [unit] = await (
    await page.request.get(`/api/library/books/${(await imported.json()).id}/units`)
  ).json()
  const goal = '分析有限证据如何改变行动'
  const reference = await page.request.post('/api/reference-sessions', {
    headers,
    data: { unit_id: unit.id, start: 0, end: text.length, quote: text, goal, branch_id },
  })
  expect(reference.ok(), await reference.text()).toBe(true)
  const session = await reference.json()
  const cards = [
    { id: 'limited-proof', technique: '解析一层可信证据', evidence: '😀渡口' },
    { id: 'other-proof', technique: '用余下疑点引出下一步', evidence: '半幅旧图' },
  ].map((card) => ({
    ...card,
    observation: '线索留下部分空白',
    interpretation: '读者可由细节继续观察',
    conditions: '主角仍有后续行动',
    exceptions: '替换原人物、地点与物件',
  }))
  const saved = await page.request.patch(`/api/reference-sessions/${session.id}`, {
    headers,
    data: { base_revision: 0, branch_id, goal, cards },
  })
  expect(saved.ok(), await saved.text()).toBe(true)
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(id)
  await page.getByRole('button', { name: '拆解台', exact: true }).click()
  await page.getByText('已保存手法卡与历史', { exact: true }).press('Enter')
  const card = page.getByText('解析一层可信证据 · 1 个修订', { exact: true })
  await card.press('Enter')
  const revision = card.locator('..').getByText('修订 1 · 可复用 · 当前版本', { exact: true })
  await revision.press('Enter')
  const detail = revision.locator('..')
  await expect(detail.getByText('参考卡样本.txt · 本机有可核对的原文')).toBeVisible()
  await detail.getByRole('button', { name: '查看证据原句' }).press('Enter')
  await expect(detail.getByText('😀渡口', { exact: true })).toBeVisible()
  await detail.getByText('查看来源编号', { exact: true }).press('Enter')
  await expect(detail.getByText(/手法卡版本 SHA-256 [a-f0-9]{64}/)).toBeVisible()

  await page.getByText('离线写作与交换', { exact: true }).click()
  const defaultDownload = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出当前稿件包', exact: true }).click()
  const defaultPackage = JSON.parse(await readFile((await (await defaultDownload).path())!, 'utf8'))
  expect(defaultPackage.reference_history.excerpts).toBeUndefined()
  expect(JSON.stringify(defaultPackage)).not.toContain(text)

  await page.getByText('选带指定手法卡原文', { exact: true }).click()
  const first = page.getByRole('checkbox', { name: /解析一层可信证据 · 修订 1/ })
  await first.check()
  await page.getByRole('button', { name: '预览选带的原文', exact: true }).click()
  const preview = page.getByRole('region', { name: '选带原文预览' })
  await expect(preview.getByText('😀渡口', { exact: true })).toBeVisible()
  await first.uncheck()
  await expect(preview).toHaveCount(0)
  await first.check()
  await page.getByRole('button', { name: '预览选带的原文', exact: true }).click()
  const optedDownload = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载含所选原文的稿件包' }).click()
  const opted = JSON.parse(await readFile((await (await optedDownload).path())!, 'utf8'))
  expect(Object.values(opted.reference_history.excerpts)).toEqual(['😀渡口'])
  expect(JSON.stringify(opted)).not.toContain('半幅旧图')
})
