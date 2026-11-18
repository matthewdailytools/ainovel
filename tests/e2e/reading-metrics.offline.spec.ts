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
  await expect(panel.getByText('按共同开放假设统计，读者资格未观测')).toBeVisible()
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
  await panel.getByRole('combobox', { name: '发布报告', exact: true }).selectOption({ index: 1 })
  await expect(panel.getByRole('heading', { name: '读者事件报告 · 遗铃发布版 1' })).toBeVisible()

  await file.setInputFiles({
    name: 'reading-diagnosis.json',
    mimeType: 'application/json',
    buffer: Buffer.from(
      JSON.stringify({
        ...common,
        mode: 'events',
        release_version: '遗铃发布版 3',
        window_hours: 24,
        events: [
          ...['甲', '乙', '丙'].map((reader) => event(0, reader, '01')),
          { ...event(1, '甲', '05'), channel: '推荐' },
          { ...event(1, '乙', '05'), channel: '搜索' },
          { ...event(1, '丙', '05'), channel: '推荐' },
          { ...event(1, '丙', '05'), channel: '搜索' },
          event(2, '甲', '13'),
        ],
        reader_access: [chapters[1], chapters[2]].flatMap((chapter) =>
          ['甲', '乙', '丙'].map((reader_key) => ({
            chapter_id: chapter.chapter_id,
            reader_key,
            available_from: chapter.published_at,
          })),
        ),
        publication_changes: [
          {
            chapter_id: chapters[2].chapter_id,
            kind: 'access',
            at: chapters[2].published_at,
            note: '本章开始仅订阅读者可读。',
          },
        ],
      }),
    ),
  })
  await importButton.click()
  const diagnosis = panel.getByRole('region', { name: '追读待验证线索' })
  await expect(diagnosis).toContainText('入门 → 回声')
  await expect(diagnosis).toContainText('33.33%')
  await expect(diagnosis).toContainText('本章开始仅订阅读者可读。')
  const breakdown = diagnosis.getByRole('table', { name: '入门 → 回声 · 首读渠道分解' })
  await expect(breakdown).toBeVisible()
  for (const [channel, counts] of [
    ['推荐', ['1', '1', '100%', '0', '0', '0', '0']],
    ['搜索', ['0', '1', '0%', '0', '0', '0', '0']],
    ['同刻多个首读渠道', ['0', '1', '0%', '0', '0', '0', '0']],
  ] as const) {
    const row = breakdown
      .getByRole('row')
      .filter({ has: page.getByRole('rowheader', { name: channel, exact: true }) })
    await expect(row.getByRole('cell')).toHaveText([...counts])
  }
  const channelScroll = diagnosis.getByRole('region', { name: '入门 → 回声 · 首读渠道分解' })
  await channelScroll.focus()
  await expect(channelScroll).toBeFocused()

  const latestPath = `/api/branches/${novel.branch_id}/reading-metrics`
  const oldResponse = await (await page.request.get(latestPath)).json()
  delete oldResponse.latest.report.diagnostics[0].channel_breakdown
  delete oldResponse.latest.report.access_mode
  await page.route(`**${latestPath}`, (route) => route.fulfill({ json: oldResponse }))
  await page.reload()
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  await expect(panel.getByText('此历史报告未记录读者资格口径。')).toBeVisible()
  await expect(diagnosis.getByText('该报告未保存渠道细分。', { exact: true })).toBeVisible()
  await expect(breakdown).toHaveCount(0)
})

test('按目标章实际资格算出一人中一人，资格未知时显示缺失而不补算', async ({ page }) => {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const created = await page.request.post('/api/novels', {
    headers,
    data: { title: '后台验证 · 追读资格' },
  })
  expect(created.ok()).toBe(true)
  const novel = await created.json()
  const branch = `/api/branches/${novel.branch_id}`
  const chapters: { chapter_id: string; published_at: string; opens_at?: string }[] = []
  for (const [title, day] of [
    ['拾铃', '01'],
    ['门内', '05'],
  ]) {
    const response = await page.request.post(branch + '/chapters', { headers, data: { title } })
    expect(response.ok()).toBe(true)
    chapters.push({ chapter_id: (await response.json()).id, published_at: `2026-11-${day}T00:00:00+08:00` })
  }
  chapters[1].opens_at = '2026-11-06T00:00:00+08:00'
  const event = (index: number, reader_key: string, channel: string, day: string) => ({
    chapter_id: chapters[index].chapter_id,
    reader_key,
    channel,
    kind: 'read',
    at: `2026-11-${day}T00:00:00+08:00`,
  })
  const sample = {
    format: 'ainovel-reading/1',
    mode: 'events',
    source: '合成订阅样本',
    release_version: '明确资格',
    timezone: 'Asia/Shanghai',
    observed_from: '2026-11-01T00:00:00+08:00',
    observed_until: '2026-11-14T00:00:00+08:00',
    extracted_at: '2026-11-14T10:00:00+08:00',
    read_definition: '合成有效阅读',
    chapters,
    window_hours: 24,
    events: [
      event(0, 'reader-a-example', '订阅', '01'),
      event(0, 'reader-b-example', '推荐', '01'),
      event(0, 'reader-c-example', '推荐', '01'),
      event(1, 'reader-a-example', '站内', '09'),
    ],
    publication_changes: [
      {
        chapter_id: chapters[1].chapter_id,
        kind: 'access',
        at: chapters[1].published_at,
        note: '开放给订阅用户。',
      },
    ],
  }
  const permissions = ['a', 'b', 'c'].map((suffix) => ({
    chapter_id: chapters[1].chapter_id,
    reader_key: `reader-${suffix}-example`,
    available_from: suffix === 'a' ? '2026-11-09T00:00:00+08:00' : null,
  }))
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  const panel = page.getByRole('region', { name: '追读数据', exact: true })
  async function upload(payload: object) {
    await panel.getByLabel('追读数据文件').setInputFiles({
      name: '读者资格.json',
      mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify(payload)),
    })
    const submit = panel.getByRole('button', { name: '导入并计算' })
    await expect(submit).toBeEnabled()
    const posted = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' && response.url().endsWith(branch + '/reading-metrics/import'),
    )
    await submit.press('Enter')
    const response = await posted
    expect(response.ok(), await response.text()).toBe(true)
  }
  await upload({ ...sample, reader_access: permissions })
  await expect(panel.getByText('按读者资格统计，资格未知和明确无权分别排除')).toBeVisible()
  const pair = panel.getByRole('table', { name: '相邻章追读率' }).getByRole('row', { name: /拾铃 → 门内/ })
  await expect(pair.getByRole('cell')).toHaveText(['1', '1', '100%', '0', '0', '2', '0'])
  await panel.getByText('按章对查看首读渠道', { exact: true }).press('Enter')
  const channel = panel.getByRole('region', { name: '拾铃 → 门内 · 渠道资格' })
  await channel.focus()
  await expect(channel).toBeFocused()
  await expect(channel.getByRole('row', { name: /推荐/ }).getByRole('cell')).toHaveText([
    '0',
    '0',
    '无可计算数据',
    '0',
    '0',
    '2',
    '0',
  ])
  const saved = await page.request.get(branch + '/reading-metrics')
  expect(await saved.text()).not.toContain('reader-a-example')
  const { latest } = await saved.json()

  await upload({ ...sample, release_version: '资格未观测' })
  await expect(panel.getByText('读者资格覆盖未知，章间追读与留存无可计算数据')).toBeVisible()
  await expect(pair.getByRole('cell')).toHaveText(['0', '0', '无可计算数据', '0', '0', '0', '3'])
  await panel.getByRole('combobox', { name: '发布报告', exact: true }).selectOption(latest.id)
  await expect(pair.getByRole('cell')).toHaveText(['1', '1', '100%', '0', '0', '2', '0'])
})

test('单章完读区分已覆盖的零、覆盖未知和明确未采集', async ({ page }) => {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const created = await page.request.post('/api/novels', {
    headers,
    data: { title: '后台验证 · 完读覆盖' },
  })
  expect(created.ok()).toBe(true)
  const novel = await created.json()
  const branch = `/api/branches/${novel.branch_id}`
  const chapters: { chapter_id: string; published_at: string }[] = []
  for (const [title, day] of [
    ['已采集', '01'],
    ['覆盖未知', '02'],
    ['未采集', '03'],
  ]) {
    const response = await page.request.post(branch + '/chapters', { headers, data: { title } })
    expect(response.ok()).toBe(true)
    chapters.push({ chapter_id: (await response.json()).id, published_at: `2026-11-${day}T00:00:00+08:00` })
  }
  const payload = {
    format: 'ainovel-reading/1',
    mode: 'events',
    source: '合成埋点样本',
    release_version: '逐章完读采集覆盖',
    timezone: 'Asia/Shanghai',
    observed_from: '2026-11-01T00:00:00+08:00',
    observed_until: '2026-11-05T00:00:00+08:00',
    extracted_at: '2026-11-05T10:00:00+08:00',
    read_definition: '文章加载完毕',
    completion_definition: '最后一屏已达',
    completion_coverage: [
      { chapter_id: chapters[0].chapter_id, coverage: 'observed', definition: '该章读至九成' },
      { chapter_id: chapters[2].chapter_id, coverage: 'not_observed' },
    ],
    chapters,
    window_hours: 24,
    events: chapters.map((chapter) => ({
      chapter_id: chapter.chapter_id,
      reader_key: 'anonymous-start',
      channel: '站内',
      kind: 'start',
      at: chapter.published_at,
    })),
  }
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  const panel = page.getByRole('region', { name: '追读数据', exact: true })
  async function upload(value: object) {
    const file = panel.getByLabel('追读数据文件')
    await file.setInputFiles({
      name: '完读采集范围.json',
      mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify(value)),
    })
    const submit = panel.getByRole('button', { name: '导入并计算' })
    await expect(submit).toBeEnabled()
    const posted = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' && response.url().endsWith(branch + '/reading-metrics/import'),
    )
    await submit.press('Enter')
    return posted
  }
  expect((await upload(payload)).status()).toBe(201)
  await panel.getByRole('combobox', { name: '查看指标' }).selectOption('chapter_completion')
  const table = panel.getByRole('table', { name: '单章完读率' })
  const row = (title: string) =>
    table.getByRole('row').filter({ has: page.getByRole('rowheader', { name: title, exact: true }) })
  await expect(row('已采集').getByRole('cell')).toHaveText([
    '0',
    '1',
    '0%',
    '0',
    '来源确认已采集 · 该章读至九成',
  ])
  await expect(row('覆盖未知').getByRole('cell')).toHaveText([
    '未提供',
    '1',
    '无可计算数据',
    '0',
    '完读采集覆盖未知 · 最后一屏已达',
  ])
  await expect(row('未采集').getByRole('cell')).toHaveText([
    '未提供',
    '1',
    '无可计算数据',
    '0',
    '来源确认未采集 · 最后一屏已达',
  ])
  const contradicted = await upload({
    ...payload,
    events: [...payload.events, { ...payload.events[2], kind: 'finish' }],
  })
  expect(contradicted.status()).toBe(400)
  await expect(panel.getByRole('alert')).toContainText('声明未采集完读的章节不能同时提供完读事件')
  await expect(row('已采集').getByText('0%', { exact: true })).toBeVisible()
})
