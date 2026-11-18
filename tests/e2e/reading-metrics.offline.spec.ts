import { expect, test } from '@playwright/test'

test('从故事与审阅导入追读事件和汇总，保留窗口、缺失范围与发布版本', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const created = await page.request.post('/api/novels', {
    headers,
    data: { title: '后台验证 · 遗铃追读' },
  })
  expect(created.ok()).toBe(true)
  const novel = await created.json()
  const chapters: { chapter_id: string; published_at: string }[] = []
  for (const [title, day] of [
    ['拾铃', '01'],
    ['入门', '05'],
    ['回声', '13'],
  ]) {
    const created = await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
      headers,
      data: { title },
    })
    expect(created.ok()).toBe(true)
    const chapter = await created.json()
    chapters.push({ chapter_id: chapter.id, published_at: `2026-11-${day}T00:00:00+08:00` })
  }
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  const panel = page.getByRole('region', { name: '追读数据', exact: true })
  await expect(panel.getByText('暂无追读报告。导入发布数据后可查看统计。')).toBeVisible()
  const file = panel.getByLabel('追读数据文件')
  const importButton = panel.getByRole('button', { name: '导入并计算' })
  const common = {
    format: 'ainovel-reading/1',
    source: '离线发布样本',
    release_version: '遗铃发布版 1',
    timezone: 'Asia/Shanghai',
    observed_from: '2026-11-01T00:00:00+08:00',
    observed_until: '2026-11-14T00:00:00+08:00',
    extracted_at: '2026-11-14T00:00:00+08:00',
    read_definition: '章节加载完毕',
    chapters,
  }
  const event = (index: number, reader_key: string, day: string) => ({
    chapter_id: chapters[index].chapter_id,
    reader_key,
    at: `2026-11-${day}T00:00:00+08:00`,
    channel: '测试来源',
    kind: 'read',
  })
  await file.setInputFiles({
    name: 'reading-events.json',
    mimeType: 'application/json',
    buffer: Buffer.from(
      JSON.stringify({
        ...common,
        mode: 'events',
        events: [event(0, '甲', '01'), event(0, '甲', '02'), event(0, '乙', '01'), event(1, '甲', '11')],
      }),
    ),
  })
  await importButton.press('Enter')
  await expect(panel.getByRole('heading', { name: '读者事件报告 · 遗铃发布版 1' })).toBeVisible()
  await expect(panel.getByText(/跟踪窗口 168 小时/)).toBeVisible()
  const followTable = panel.getByRole('table', { name: '相邻章追读率' })
  await expect(
    followTable.getByRole('row', { name: /拾铃 → 入门/ }).getByRole('cell', { name: '50%' }),
  ).toBeVisible()
  await expect(followTable.getByRole('row', { name: /入门 → 回声/ }).getByText('无可计算数据')).toBeVisible()
  await panel.getByRole('combobox', { name: '查看指标' }).selectOption('chapter_completion')
  await expect(panel.getByRole('table', { name: '单章完读率' })).toBeVisible()

  await file.setInputFiles({
    name: 'reading-bad.json',
    mimeType: 'application/json',
    buffer: Buffer.from('{bad'),
  })
  await importButton.click()
  await expect(panel.getByRole('alert')).toContainText('追读文件格式无效')
  await expect(panel.getByRole('heading', { name: '读者事件报告 · 遗铃发布版 1' })).toBeVisible()

  await file.setInputFiles({
    name: 'reading-counts.json',
    mimeType: 'application/json',
    buffer: Buffer.from(
      JSON.stringify({
        ...common,
        release_version: '遗铃发布版 2',
        mode: 'aggregate',
        counts: [
          { chapter_id: chapters[0].chapter_id, unique_readers: 2 },
          { chapter_id: chapters[1].chapter_id, unique_readers: 3 },
        ],
      }),
    ),
  })
  await importButton.click()
  await expect(panel.getByRole('heading', { name: '独立读人数汇总 · 遗铃发布版 2' })).toBeVisible()
  const aggregateTable = panel.getByRole('table', { name: '各章独立读人数比值' })
  await expect(
    aggregateTable.getByRole('row', { name: /拾铃 → 入门/ }).getByRole('cell', { name: '1.5 倍' }),
  ).toBeVisible()
  await expect(aggregateTable.getByRole('row', { name: /入门 → 回声/ }).getByText('未提供')).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  const scroll = panel.getByRole('region', { name: '各章独立读人数比值' })
  await scroll.focus()
  await expect(scroll).toBeFocused()
  await page.reload()
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  await expect(panel.getByRole('heading', { name: '独立读人数汇总 · 遗铃发布版 2' })).toBeVisible()
})
