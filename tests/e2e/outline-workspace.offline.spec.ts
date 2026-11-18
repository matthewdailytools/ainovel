import { errors, expect, test } from '@playwright/test'

test('新作可确认四级章纲，并从台账跳到对应章节', async ({ page }) => {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const novel = await page.request.post('/api/novels', { headers, data: { title: '遗铃 · 大纲验收' } })
  expect(novel.ok(), await novel.text()).toBe(true)
  const { id, branch_id } = await novel.json()
  const chapter = await page.request.post(`/api/branches/${branch_id}/chapters`, {
    headers,
    data: { title: '门外寻铃' },
  })
  expect(chapter.ok(), await chapter.text()).toBe(true)
  const { id: chapterId } = await chapter.json()
  const quote = '守夜人只认出一段绳结，随后沿岸寻访系结者。'
  const imported = await page.request.post('/api/library/import', {
    headers,
    multipart: { file: { name: '遗铃写法样例.txt', mimeType: 'text/plain', buffer: Buffer.from(quote) } },
  })
  expect(imported.ok(), await imported.text()).toBe(true)
  const { id: bookId } = await imported.json()
  const [source] = await (await page.request.get(`/api/library/books/${bookId}/units`)).json()
  const created = await page.request.post('/api/reference-sessions', {
    headers,
    data: {
      unit_id: source.id,
      start: 0,
      end: quote.length,
      quote,
      goal: '观察局部进展',
      branch_id,
      chapter_id: chapterId,
    },
  })
  expect(created.ok(), await created.text()).toBe(true)
  const { id: sessionId } = await created.json()
  const referencePath = `/api/reference-sessions/${sessionId}`
  const card = {
    id: 'follow-evidence',
    observation: '只认出部分物件特征',
    interpretation: '部分线索能改变下一步行动',
    technique: '用少量线索引出下一步',
    conditions: '角色仍有可行动的选择',
    exceptions: '替换人物、地理和具体道具',
    evidence: quote,
  }
  const cardEdit = { goal: '观察局部进展', cards: [card], branch_id, chapter_id: chapterId }
  const cardSaved = await page.request.patch(referencePath, {
    headers,
    data: { ...cardEdit, base_revision: 0 },
  })
  expect(cardSaved.ok(), await cardSaved.text()).toBe(true)
  const graphPath = `/api/branches/${branch_id}/story-graph`
  const graph = await (await page.request.get(graphPath)).json()
  const promise = await page.request.post(`/api/branches/${branch_id}/story-records`, {
    headers,
    data: {
      base_story_revision: graph.story_revision,
      record: { kind: 'promise', title: '铃声为何消失', stages: [{ status: 'planned' }] },
    },
  })
  expect(promise.ok(), await promise.text()).toBe(true)
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(id)
  await page.getByRole('button', { name: '大纲与台账', exact: true }).click()
  await page.getByLabel('总纲', { exact: true }).fill('学徒查找失散的师姐。')
  await page.getByRole('button', { name: '新增卷', exact: true }).click()
  await page.getByLabel('卷名', { exact: true }).fill('拾铃卷')
  await page.getByLabel('卷纲', { exact: true }).fill('追索铃身的断痕。')
  await page.getByRole('button', { name: '新增单元', exact: true }).click()
  await page.getByLabel('单元名', { exact: true }).fill('辨铃')
  await page.getByLabel('单元纲', { exact: true }).fill('比较门内外铃声。')
  await page.getByLabel('加入已有章节').selectOption({ label: '1. 门外寻铃' })
  for (const [field, value] of Object.entries({
    目标: '发现铃身的断痕',
    阻碍: '门外听不见铃声',
    转折: '发现门上的旧记号',
    结果: '取得下一步线索',
  })) {
    await page.getByLabel(field, { exact: true }).fill(value)
  }
  await page.getByRole('combobox', { name: '加入手法卡', exact: true }).selectOption({
    label: '用少量线索引出下一步 · 遗铃写法样例.txt · 修订 1',
  })
  await page.getByRole('button', { name: '新增伏笔窗口' }).click()
  await page.getByRole('combobox', { name: '伏笔', exact: true }).selectOption({ label: '铃声为何消失' })
  for (const field of ['埋设章', '开始回收', '最迟回收']) {
    await page.getByRole('combobox', { name: field, exact: true }).selectOption({ label: '门外寻铃' })
  }
  await page.getByRole('button', { name: '新建伏笔计划' }).click()
  const pending = page.getByRole('group', { name: '新伏笔计划 1' })
  await pending.getByLabel('伏笔标题').fill('门上的旧记号由谁刻下')
  await pending.getByLabel('计划说明').fill('发现记号后核对刻痕是谁留下的。')
  for (const field of ['埋设章', '开始回收', '最迟回收']) {
    await pending.getByRole('combobox', { name: field, exact: true }).selectOption({ label: '门外寻铃' })
  }
  await page.getByRole('button', { name: '保存大纲草稿' }).click()
  await expect(page.getByText('已保存大纲草稿修订 1。', { exact: true })).toBeVisible()
  const beforeConfirm = await (await page.request.get(graphPath)).json()
  expect(beforeConfirm.records).toHaveLength(1)
  await page.getByRole('button', { name: '确认已保存大纲' }).click()
  await expect(page.getByText('已确认大纲修订 2', { exact: true })).toBeVisible()
  await expect(page.getByText(/铃声为何消失 · 计划/)).toBeVisible()
  await expect(page.getByText(/门上的旧记号由谁刻下 · 计划/)).toBeVisible()
  const redesigned = await page.request.patch(referencePath, {
    headers,
    data: { ...cardEdit, base_revision: 1, cards: [{ ...card, technique: '让新线索改变选择' }] },
  })
  expect(redesigned.ok(), await redesigned.text()).toBe(true)
  await page
    .getByRole('listitem')
    .filter({ hasText: '铃声为何消失' })
    .getByRole('button', { name: '门外寻铃', exact: true })
    .click()
  await expect(page.getByText('本章章纲 · 修订 2')).toBeVisible()
  await expect(page.getByText('发现铃身的断痕', { exact: true })).toBeVisible()
  const directory = page.getByRole('navigation', { name: '全书目录' })
  await expect(directory.getByText('拾铃卷', { exact: true })).toBeVisible()
  await expect(directory.getByRole('region', { name: '辨铃', exact: true })).toBeVisible()
  await page.getByText('本章章纲引用的手法卡', { exact: true }).press('Enter')
  await page.getByText('用少量线索引出下一步 · 手法卡修订 1', { exact: true }).press('Enter')
  await page.getByRole('button', { name: '查看手法卡原文依据' }).press('Enter')
  await expect(page.locator('blockquote').getByText(quote, { exact: true })).toBeVisible()
  await page.locator('.cm-content[contenteditable=true]').focus()
  await page.keyboard.insertText('学徒迈过门槛，铜铃响了。')
  await expect(page.getByLabel('今日事项')).toContainText('2 条伏笔处于回收窗口或已到期')
})

test('窄屏正文的原生中文组合输入仅在确认后自动保存', async ({ page, context, browserName }) => {
  test.skip(browserName !== 'chromium', 'Chromium DevTools Protocol 提供真实组合输入。')
  await page.setViewportSize({ width: 320, height: 850 })
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const novel = await page.request.post('/api/novels', { headers, data: { title: '遗铃 · 组合输入' } })
  expect(novel.ok(), await novel.text()).toBe(true)
  const { id, branch_id } = await novel.json()
  const chapter = await page.request.post(`/api/branches/${branch_id}/chapters`, {
    headers,
    data: { title: '门外听铃' },
  })
  expect(chapter.ok(), await chapter.text()).toBe(true)
  const { id: chapterId } = await chapter.json()
  const chapterUrl = `/api/chapters/${chapterId}`
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(id)
  await page.getByRole('button', { name: '写作', exact: true }).press('Enter')

  const editor = page.locator('.cm-content[contenteditable=true]')
  await editor.focus()
  await expect(editor).toBeFocused()
  const cdp = await context.newCDPSession(page)
  const draftSave = page
    .waitForRequest((request) => request.method() === 'PUT' && request.url().endsWith(chapterUrl), {
      timeout: 1400,
    })
    .then(
      () => true,
      (error) => {
        if (error instanceof errors.TimeoutError) return false
        throw error
      },
    )
  const composition = 'menwailing'
  await cdp.send('Input.imeSetComposition', {
    text: composition,
    selectionStart: composition.length,
    selectionEnd: composition.length,
  })
  await expect(editor).toContainText(composition)
  expect(await draftSave).toBe(false)
  expect(await (await page.request.get(chapterUrl)).json()).toMatchObject({ revision: 0, content: '' })
  await cdp.send('Input.imeSetComposition', { text: '', selectionStart: 0, selectionEnd: 0 })
  await expect(editor).toHaveText('')

  const committed = '学徒在门外听见铃声。'
  await cdp.send('Input.imeSetComposition', {
    text: committed,
    selectionStart: committed.length,
    selectionEnd: committed.length,
  })
  await expect(editor).toContainText(committed)
  await cdp.send('Input.insertText', { text: committed })
  await expect(editor).toContainText(committed)
  await expect(editor).toBeFocused()
  await expect.poll(async () => (await (await page.request.get(chapterUrl)).json()).revision).toBe(1)
  expect(await (await page.request.get(chapterUrl)).json()).toMatchObject({ content: committed })
  const history = await (await page.request.get(chapterUrl + '/history')).json()
  expect(history.map((entry: { revision: number }) => entry.revision)).toEqual([1, 0])
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)
})
