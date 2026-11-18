import { expect, test } from '@playwright/test'

test('六项核对通过后确认完结，旧交付包可在改稿后下载', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novelResponse = await page.request.post('/api/novels', {
    headers,
    data: { title: '后台验证 · 遗铃完本交付' },
  })
  expect(novelResponse.ok(), await novelResponse.text()).toBe(true)
  const novel = await novelResponse.json()
  const branch = `/api/branches/${novel.branch_id}`
  const text = '😀学徒捡起铜铃。\r\n他看见铃身的旧痕。'
  const createChapter = async (title: string, content: string) => {
    const draft = await page.request.post(`${branch}/chapters`, { headers, data: { title } })
    expect(draft.ok(), await draft.text()).toBe(true)
    const { id } = await draft.json()
    const saved = await page.request.put(`/api/chapters/${id}`, {
      headers,
      data: { base_revision: 0, content },
    })
    expect(saved.ok(), await saved.text()).toBe(true)
    return saved.json()
  }
  const chapter = await createChapter('拾铃', text)
  await createChapter('归铃', '师姐归来，两人把铜铃交回主人。')

  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  const panel = page.getByRole('region', { name: '完本与交付', exact: true })
  const previewButton = panel.getByRole('button', { name: '预览交付材料' })
  await previewButton.click()
  await expect(panel.getByText('请填写简介并选择有保存稿的试读章，生成交付预览。')).toBeVisible()
  await panel.getByRole('textbox', { name: '通读记录', exact: true }).fill('已通读两章保存稿。')
  await panel.getByRole('textbox', { name: '主冲突与终局说明', exact: true }).fill('寻找师姐与归还铜铃收束。')
  await panel.getByRole('textbox', { name: '关键人物与关系说明', exact: true }).fill('师姐回到学徒身边。')
  await panel
    .getByRole('textbox', { name: '伏笔与读者承诺说明', exact: true })
    .fill('铜铃刻痕提示了师姐的去向。')
  await panel.getByRole('textbox', { name: '简介', exact: true }).fill('学徒捡到一枚带刻痕的铜铃。')
  await panel.getByText('添加正文依据', { exact: true }).click()
  await panel.getByLabel('完本引文章节').selectOption(chapter.id)
  const excerpt = panel.getByRole('textbox', { name: /保存稿修订.*选择要引用的文字/ })
  await expect(excerpt).toHaveValue(text.replaceAll('\r\n', '\n'))
  await excerpt.press('ControlOrMeta+a')
  await panel.getByRole('button', { name: '引用选区' }).click()

  const review = panel.getByRole('checkbox', { name: '已核对这次交付材料与剧透范围' })
  const confirmButton = panel.getByRole('button', { name: '确认完结', exact: true })
  await previewButton.click()
  await review.check()
  await expect(confirmButton).toBeDisabled()

  const inspections = `${branch}/workflow-inspections`
  const { preview } = await (await page.request.get(inspections)).json()
  const inspection = await page.request.post(inspections, {
    headers,
    data: {
      basis_sha256: preview.basis_sha256,
      author_reviews: preview.items
        .filter((item: { status: string }) => item.status === 'pending')
        .map((item: { check_id: string }) => ({
          check_id: item.check_id,
          status: 'green',
          basis_note: '已核对章纲与全部保存稿。',
          reason: '铜铃经学徒和师姐查到主人，结果清楚。',
          evidence: [
            { chapter_id: chapter.id, revision: chapter.revision, start: 0, end: text.length, quote: text },
          ],
        })),
      length_targets: { min_chars: 1, expected_chapters: 2 },
    },
  })
  expect(inspection.ok(), await inspection.text()).toBe(true)
  const reloaded = page.waitForResponse(
    (response) => response.request().method() === 'GET' && response.url().endsWith('/book-completion'),
  )
  await panel.getByRole('button', { name: '核对最新完本依据' }).click()
  expect((await reloaded).ok()).toBe(true)
  await previewButton.click()
  const materials = panel.getByRole('region', { name: '交付预览', exact: true })
  const sampleLabel = materials.locator('summary').filter({ hasText: /^试读\.md$/ })
  const sample = sampleLabel.locator('..')
  await sampleLabel.click()
  await expect(sample.locator('pre')).toContainText('学徒捡起铜铃')
  await expect(sample.locator('pre')).not.toContainText('师姐归来')
  await expect(confirmButton).toBeDisabled()
  await review.check()
  const confirmed = page.waitForResponse((response) => response.url().endsWith('/book-completion/confirm'))
  await confirmButton.click()
  const response = await confirmed
  expect(response.ok(), await response.text()).toBe(true)
  expect(response.request().postDataJSON().ending.sources).toEqual([
    { chapter_id: chapter.id, revision: chapter.revision, start: 0, end: text.length, quote: text },
  ])
  await expect(panel.getByRole('heading', { name: '当前版本已完结' })).toBeVisible()
  const download = panel.getByRole('link', { name: '下载交付包', exact: true })
  const url = (await download.getAttribute('href'))!
  const packageResponse = await page.request.get(url)
  expect(packageResponse.ok()).toBe(true)
  expect(packageResponse.headers()['content-type']).toBe('application/zip')

  const changed = await page.request.put(`/api/chapters/${chapter.id}`, {
    headers,
    data: { base_revision: chapter.revision, content: '学徒没有捡到铜铃。' },
  })
  expect(changed.ok(), await changed.text()).toBe(true)
  await panel.getByRole('button', { name: '核对最新完本依据' }).click()
  await expect(panel.getByRole('heading', { name: '历史完结记录 · 当前依据已变化' })).toBeVisible()
  await expect(panel.getByRole('link', { name: '下载历史交付包' })).toHaveAttribute('href', url)
  expect((await page.request.get(url)).ok()).toBe(true)
})

test('AI 完本建议载入空白项，作者通读仍待确认', async ({ page }) => {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const novel = await page.request.post('/api/novels', { headers, data: { title: '遗铃 · 完本建议' } })
  expect(novel.ok(), await novel.text()).toBe(true)
  const { id, branch_id } = await novel.json()
  const draft = await page.request.post(`/api/branches/${branch_id}/chapters`, {
    headers,
    data: { title: '寻铃' },
  })
  expect(draft.ok(), await draft.text()).toBe(true)
  const chapter = await draft.json()
  const body = '学徒将铜铃交回主人。'
  const saved = await page.request.put(`/api/chapters/${chapter.id}`, {
    headers,
    data: { base_revision: 0, content: body },
  })
  expect(saved.ok(), await saved.text()).toBe(true)
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(id)
  await page.getByRole('button', { name: '完本台', exact: true }).click()
  const panel = page.getByRole('region', { name: '完本与交付', exact: true })
  await panel.getByRole('textbox', { name: '关键人物与关系说明' }).fill('保留这项作者已有的看法。')
  await panel.getByText('AI 准备完本建议', { exact: true }).press('Enter')
  await panel.getByRole('button', { name: '生成完本核对建议与简介' }).click()
  await panel.getByRole('button', { name: '载入 AI 建议到空白项' }).click()
  await expect(panel.getByRole('textbox', { name: '简介', exact: true })).toHaveValue(
    '学徒从铜铃的来历追起。',
  )
  await expect(panel.getByRole('textbox', { name: '关键人物与关系说明' })).toHaveValue(
    '保留这项作者已有的看法。',
  )
  await expect(panel.getByRole('textbox', { name: '通读记录', exact: true })).toHaveValue('')
  await expect(panel.getByText(body, { exact: false }).first()).toBeVisible()
  await panel.getByRole('button', { name: '预览交付材料' }).click()
  await expect(panel.getByRole('button', { name: '确认完结', exact: true })).toBeDisabled()
})
