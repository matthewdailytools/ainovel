import { expect, test, type Page } from '@playwright/test'

function barrier() {
  let release = () => {}
  const promise = new Promise<void>((resolve) => {
    release = resolve
  })
  return { promise, release }
}

async function setup(page: Page, title: string) {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await (await page.request.post('/api/novels', { headers, data: { title } })).json()
  const originals = ['甲章原稿😀𠮷。\n第二行只属于甲章。', '乙章独有原稿。\n不能出现甲章版本。']
  const chapters: { id: string; title: string; revision: number }[] = []
  for (const [index, content] of originals.entries()) {
    const chapter = await (
      await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
        headers,
        data: { title: index ? '乙章' : '甲章' },
      })
    ).json()
    const saved = await page.request.put(`/api/chapters/${chapter.id}`, {
      headers,
      data: { base_revision: 0, content },
    })
    expect(saved.ok(), await saved.text()).toBeTruthy()
    chapters.push(await saved.json())
  }
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await expect(page.getByRole('textbox', { name: '章节标题', exact: true })).toHaveValue('甲章')
  await expect(page.getByRole('button', { name: '版本历史', exact: true })).toBeEnabled()
  return { novel, chapters, originals, headers }
}

test('采纳后章节重载期间历史入口禁用，就绪后可打开并逐字恢复原稿', async ({ page }) => {
  const { novel, chapters, originals, headers } = await setup(page, '后台验证 · 采纳与历史读取竞争')
  const chapter = chapters[0]
  const submitted = await page.request.post('/api/modules', {
    headers,
    data: {
      action: 'writing.expand_plot',
      novel_id: novel.id,
      branch_id: novel.branch_id,
      chapter_id: chapter.id,
      base_revision: 1,
      instruction: '只核对公开刻度，借旧尺复测，不修门。',
      account: 'offline',
      max_output_tokens: 1024,
    },
  })
  expect(submitted.ok(), await submitted.text()).toBeTruthy()
  const task = await submitted.json()
  await expect
    .poll(async () => (await (await page.request.get(`/api/tasks/${task.id}`)).json()).status)
    .toBe('succeeded')
  const accept = page.getByRole('button', { name: '采纳到正文', exact: true })
  await expect(accept).toBeEnabled()
  const entered = barrier()
  const release = barrier()
  let held = false
  await page.route(`**/api/chapters/${chapter.id}`, async (route) => {
    if (route.request().method() !== 'GET' || held) return route.continue()
    held = true
    const response = await route.fetch()
    entered.release()
    await release.promise
    await route.fulfill({ response })
  })
  try {
    await accept.click()
    await entered.promise
    const history = page.getByRole('button', { name: '版本历史', exact: true })
    await expect(history).toBeDisabled()
    await expect(page.locator('.history')).toHaveCount(0)
    const accepted = await (await page.request.get(`/api/chapters/${chapter.id}`)).json()
    expect(accepted.revision).toBe(2)
    expect(accepted.content).toContain('旧尺')
    release.release()
    await expect(history).toBeEnabled()
    await history.click()
    const original = page.locator('.history details').filter({
      has: page.locator('summary').filter({ hasText: '修订 1 · 作者编辑' }),
    })
    await expect(original).toHaveCount(1)
    await original.locator('summary').click()
    expect(await original.locator('pre').textContent()).toBe(originals[0])
    const revisions = await (await page.request.get(`/api/chapters/${chapter.id}/history`)).json()
    const previous = revisions.find((row: { revision: number }) => row.revision === 1)
    expect(previous.content).toBe(originals[0])
    const wrongTarget = await page.request.post(`/api/chapters/${chapters[1].id}/restore`, {
      headers,
      data: { base_revision: 1, revision_id: previous.id },
    })
    expect(wrongTarget.status()).toBe(404)
    expect((await wrongTarget.json()).code).toBe('not_found')
    expect((await (await page.request.get(`/api/chapters/${chapters[1].id}`)).json()).content).toBe(
      originals[1],
    )
    await original.getByRole('button', { name: '恢复为新修订', exact: true }).click()
    await expect
      .poll(async () => (await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content)
      .toBe(originals[0])
    expect((await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).revision).toBe(3)
  } finally {
    release.release()
  }
})

test('历史GET晚返回不能污染新章或清除新请求loading，同组件切章后只展示新章历史', async ({ page }) => {
  const { chapters, originals } = await setup(page, '后台验证 · 历史晚返回与真实切章')
  const firstEntered = barrier()
  const firstRelease = barrier()
  const secondEntered = barrier()
  const secondRelease = barrier()
  for (const [chapter, entered, release] of [
    [chapters[0], firstEntered, firstRelease],
    [chapters[1], secondEntered, secondRelease],
  ] as const) {
    await page.route(`**/api/chapters/${chapter.id}/history`, async (route) => {
      const response = await route.fetch()
      entered.release()
      await release.promise
      await route.fulfill({ response })
    })
  }
  try {
    const history = page.getByRole('button', { name: '版本历史', exact: true })
    await history.click()
    await firstEntered.promise
    await expect(history).toBeDisabled()
    await page.locator('.chapter-list button').filter({ hasText: '乙章' }).click()
    await expect(page.getByRole('textbox', { name: '章节标题', exact: true })).toHaveValue('乙章')
    await expect(history).toBeEnabled()
    await history.click()
    await secondEntered.promise
    await expect(history).toBeDisabled()
    const oldResponse = page.waitForResponse((response) =>
      response.url().endsWith(`/api/chapters/${chapters[0].id}/history`),
    )
    firstRelease.release()
    await (await oldResponse).finished()
    // An event-loop round trip lets the completed old fetch/JSON continuation run.
    // The second response remains held by an explicit barrier, with no timed sleep.
    await page.evaluate(() => Promise.resolve())
    await expect(page.getByRole('textbox', { name: '章节标题', exact: true })).toHaveValue('乙章')
    await expect(history).toBeDisabled()
    await expect(page.locator('.history')).toHaveCount(0)
    secondRelease.release()
    await expect(history).toBeEnabled()
    await expect(page.locator('.history')).toBeVisible()
    const contents = await page.locator('.history pre').allTextContents()
    expect(contents).toContain(originals[1])
    expect(contents).not.toContain(originals[0])
    for (const [index, chapter] of chapters.entries()) {
      const saved = await (await page.request.get(`/api/chapters/${chapter.id}`)).json()
      expect(saved.content).toBe(originals[index])
      expect(saved.revision).toBe(1)
    }
  } finally {
    firstRelease.release()
    secondRelease.release()
  }
})
