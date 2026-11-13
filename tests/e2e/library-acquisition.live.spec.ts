import { test, expect } from '@playwright/test'
import { writeFile } from 'node:fs/promises'

test('日常书库：官方单页获取、阅读搜索回源、摘录及写作资料包，无模型调用', async ({ page }, testInfo) => {
  test.skip(process.env.AINOVEL_LIBRARY_ACQUISITION !== '1', '仅显式开启官方网络获取实测')
  test.setTimeout(180_000)
  const title = '岳陽樓記'
  const marker = `后台验证 · 官方书库闭环 · ${Date.now()}`
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const before = await (await page.request.get('/api/config')).json()
  const created = await page.request.post('/api/novels', { headers, data: { title: marker } })
  expect(created.ok(), await created.text()).toBeTruthy()
  const novel = await created.json()
  const chapterResponse = await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
    headers,
    data: { title: '独立取材试写章' },
  })
  expect(chapterResponse.ok(), await chapterResponse.text()).toBeTruthy()
  const chapter = await chapterResponse.json()
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '资料书库', exact: true }).click()
  const query = page.getByRole('textbox', { name: '书名、主题或正文词语', exact: true })
  await query.fill(title)
  await page.getByText('按维基文库页面标题获取／查看下载任务', { exact: true }).click()
  await page.getByRole('combobox', { name: '获取范围', exact: true }).selectOption('page_text')
  const reusedJob = process.env.AINOVEL_EXISTING_OFFICIAL_JOB
  let job: { id: string }
  if (reusedJob) {
    job = { id: reusedJob }
  } else {
    const download = page.waitForResponse(
      (response) =>
        response.url().endsWith('/api/library/download') && response.request().method() === 'POST',
    )
    await page.getByRole('button', { name: '按当前输入的页面标题获取', exact: true }).click()
    const dispatched = await download
    expect(dispatched.ok(), await dispatched.text()).toBeTruthy()
    job = await dispatched.json()
  }
  let finalJob: { id: string; status: string; book_id?: string; error?: string } | undefined
  await expect
    .poll(
      async () => {
        const jobs = await (await page.request.get('/api/library/jobs')).json()
        finalJob = jobs.find((row: { id: string }) => row.id === job.id)
        return finalJob && !['queued', 'downloading'].includes(finalJob.status)
      },
      { timeout: 100_000, intervals: [1000, 2000, 4000] },
    )
    .toBeTruthy()
  await writeFile(testInfo.outputPath('official-job.json'), JSON.stringify(finalJob, null, 2))
  expect(finalJob?.status, `官方下载真实结果：${JSON.stringify(finalJob)}`).not.toBe('failed')
  expect(finalJob?.book_id, `官方下载未发布书籍：${JSON.stringify(finalJob)}`).toBeTruthy()
  const books = await (await page.request.get('/api/library/books')).json()
  const book = books.find((row: { id: string }) => row.id === finalJob?.book_id)
  expect(book.source).toBe(`https://zh.wikisource.org/wiki/${encodeURIComponent(title)}`)
  expect(book.coverage).toContain('当前单页面')
  expect(book.coverage).toContain('未跟随子页面')
  await page
    .locator('.catalog-row')
    .filter({ hasText: title })
    .getByRole('button', { name: '阅读', exact: true })
    .click()
  const reader = page.getByRole('textbox', { name: '资料原文', exact: true })
  await expect(reader).toHaveValue(/洞庭/)
  await expect(reader).toHaveValue(/先天下之憂而憂/)
  await page.getByRole('combobox', { name: '正文范围', exact: true }).selectOption('book')
  const searches = []
  for (const term of ['洞庭', '先天下之憂而憂']) {
    await query.fill(term)
    const searched = page.waitForResponse(
      (response) => response.url().includes('/api/library/search?') && response.request().method() === 'GET',
    )
    await page.getByRole('button', { name: '搜索正文', exact: true }).click()
    const result = await (await searched).json()
    const hit = result.hits.find((row: { book_id: string }) => row.book_id === book.id)
    expect(hit, `未找到连续原文：${term}`).toBeTruthy()
    const hitButton = page.locator('button.hit').first()
    await expect(hitButton).toContainText(`UTF-16 ${hit.start}—${hit.end}`)
    await hitButton.click()
    const readSelection = () =>
      reader.evaluate((element: HTMLTextAreaElement) => ({
        start: element.selectionStart,
        end: element.selectionEnd,
        quote: element.value.slice(element.selectionStart, element.selectionEnd),
      }))
    await expect.poll(readSelection).toEqual({ start: hit.start, end: hit.end, quote: term })
    const selected = await readSelection()
    expect(hit.file_hash).toBe(book.file_hash)
    searches.push({
      term,
      selected,
      hit: { id: hit.id, file_hash: hit.file_hash },
      matching: result.matching,
    })
  }
  await page
    .getByRole('textbox', { name: '摘录笔记', exact: true })
    .fill('后台验证：借鉴忧乐对照的表达机制，仅选本句，未把公版资料当作本书事实。')
  const excerptResponse = page.waitForResponse(
    (response) => response.url().endsWith('/api/library/excerpts') && response.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '保存摘录与出处', exact: true }).click()
  const excerpt = await (await excerptResponse).json()
  await expect(page.getByText('摘录已保存，可在写作页主动选入资料包。', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '写作', exact: true }).click()
  await expect(page.getByRole('textbox', { name: '章节正文', exact: true })).toBeVisible()
  await page.getByText('固定条件、写法与资料包', { exact: true }).click()
  const selectedExcerpt = page.getByRole('checkbox', { name: /先天下之憂而憂/ })
  await expect(selectedExcerpt).not.toBeChecked()
  await selectedExcerpt.check()
  await expect(selectedExcerpt).toBeChecked()
  const tasks = await (await page.request.get(`/api/tasks?branch_id=${novel.branch_id}`)).json()
  expect(tasks).toEqual([])
  const saved = await (await page.request.get(`/api/chapters/${chapter.id}`)).json()
  expect(saved.content).toBe('')
  expect(saved.revision).toBe(0)
  const after = await (await page.request.get('/api/config')).json()
  expect(after.budget).toEqual(before.budget)
  const summary = {
    novel_id: novel.id,
    chapter_id: chapter.id,
    job_id: job.id,
    book_id: book.id,
    acquisition: reusedJob ? '复用本轮已成功的官方获取任务，不重新联网或本地导入' : '本次真实官方获取',
    source: book.source,
    coverage: book.coverage,
    file_hash: book.file_hash,
    searches,
    excerpt_id: excerpt.id,
    model_tasks: 0,
    budget_unchanged: true,
    scope: '维基文库单页面；未跟随子页，不代表多页面整书；仅主动选入资料包，未调用模型',
  }
  await writeFile(testInfo.outputPath('official-library-summary.json'), JSON.stringify(summary, null, 2))
  await page.screenshot({ path: testInfo.outputPath('library-selected-package.png'), fullPage: true })
})
