import { test, expect } from '@playwright/test'

test('U01：空白手写、卡点方向、作者选择、补写、拒绝、采纳与恢复', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await (
    await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 作者选择方向' } })
  ).json()
  const chapter = await (
    await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
      headers,
      data: { title: '空白起笔章' },
    })
  ).json()
  const other = await (
    await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
      headers,
      data: { title: '其他章节' },
    })
  ).json()
  const sourceText = '自拟资料：旧尺只能用来复测，不能修理闸门。'
  const book = await (
    await page.request.post('/api/library/import', {
      headers,
      multipart: { file: { name: '方向资料.txt', mimeType: 'text/plain', buffer: Buffer.from(sourceText) } },
    })
  ).json()
  const units = await (await page.request.get(`/api/library/books/${book.id}/units`)).json()
  const excerpt = await (
    await page.request.post('/api/library/excerpts', {
      headers,
      data: {
        unit_id: units[0].id,
        start: 0,
        end: sourceText.length,
        quote: sourceText,
        note: '方向资料',
        novel_id: novel.id,
      },
    })
  ).json()
  expect(excerpt.id).toBeTruthy()
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  const editor = page.getByRole('textbox', { name: '章节正文', exact: true })
  await expect(editor).toHaveText('')
  const original = '她站在闸门前。😀𠮷'
  await editor.fill(original)
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect
    .poll(async () => (await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content)
    .toBe(original)
  await page
    .getByRole('textbox', { name: '情节或具体要求', exact: true })
    .fill('不知道下一步如何调查，给出不同方向。')
  await page.getByText('固定条件、写法与资料包', { exact: true }).click()
  const fixed = page.getByRole('textbox', { name: '必须保持的条件（每行一条）', exact: true })
  await fixed.fill('不修门\n不取得私藏账簿')
  await page.getByRole('combobox', { name: '常用写法', exact: true }).selectOption({ label: '心理' })
  const profile = await page.getByRole('textbox', { name: '表达设置', exact: true }).inputValue()
  const reference = page.getByRole('checkbox', { name: /方向资料.txt/ })
  await reference.check()
  await page.getByRole('button', { name: '生成候选 →', exact: true }).click()
  const directionPanel = page.getByRole('region', { name: '选择方向后补写', exact: true })
  const selected = directionPanel.getByRole('textbox', { name: '作者选定的本章方向', exact: true })
  await expect(selected).toHaveValue('')
  await expect(page.getByRole('textbox', { name: '候选内容', exact: true })).toHaveValue(
    /方向二：改查私藏账簿/,
  )
  const adviceRows = await (await page.request.get(`/api/artifacts?branch_id=${novel.branch_id}`)).json()
  const advice = adviceRows.find((row: { kind: string }) => row.kind === 'advice')
  expect(advice).toBeTruthy()
  // The author may keep writing after advice. Its old revision stays visible.
  let continued = original + '\n她先抄下眼前可见的刻度。'
  await editor.fill(continued)
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect(directionPanel).toContainText('当前保存稿已是修订 2')
  const chosen = '先核对公开刻度，再借旧尺复测。只记录读数，不修门，不查私藏账簿。'
  await selected.fill(chosen + '作者暂定')
  await selected.fill(chosen)
  const chapterButtons = page.locator('.chapter-list button').filter({ hasText: '其他章节' })
  await Promise.all([
    page.waitForEvent('dialog').then(async (dialog) => {
      expect(dialog.message()).toContain('尚未载入的作者选定方向')
      await dialog.dismiss()
    }),
    chapterButtons.click(),
  ])
  await expect(selected).toHaveValue(chosen)
  expect((await (await page.request.get(`/api/chapters/${other.id}`)).json()).content).toBe('')
  const load = directionPanel.getByRole('button', { name: '将选定方向载入本章补写（不生成）', exact: true })
  await expect(load).toBeEnabled()
  await Promise.all([
    page.waitForEvent('dialog').then(async (dialog) => {
      expect(dialog.message()).toContain('将替换当前“情节或具体要求”')
      await dialog.dismiss()
    }),
    load.click(),
  ])
  await expect(page.getByRole('textbox', { name: '情节或具体要求', exact: true })).toHaveValue(
    '不知道下一步如何调查，给出不同方向。',
  )
  await expect(load).toBeEnabled()
  await load.focus()
  await Promise.all([page.waitForEvent('dialog').then((dialog) => dialog.accept()), load.press('Enter')])
  await expect(page.getByRole('combobox', { name: '这次要做什么', exact: true })).toHaveValue(
    'writing.expand_plot',
  )
  await expect(page.getByRole('textbox', { name: '情节或具体要求', exact: true })).toHaveValue(chosen)
  await expect(page.getByRole('region', { name: '作者选定方向来源', exact: true })).toContainText(advice.id)
  await expect(page.getByRole('textbox', { name: '情节或具体要求', exact: true })).toBeFocused()
  await expect(fixed).toHaveValue('不修门\n不取得私藏账簿')
  await expect(page.getByRole('textbox', { name: '表达设置', exact: true })).toHaveValue(profile)
  await expect(reference).toBeChecked()
  // Loading a direction leaves the manuscript editable and uses its newest saved revision.
  continued += '\n她把本子翻到空白页，继续手写自己的观察。'
  await editor.fill(continued)
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect
    .poll(async () => (await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).revision)
    .toBe(3)
  const tasksAfterLoad = await (await page.request.get(`/api/tasks?branch_id=${novel.branch_id}`)).json()
  expect(tasksAfterLoad).toHaveLength(1)
  expect((await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content).toBe(continued)
  expect((await (await page.request.get(`/api/artifacts/${advice.id}`)).json()).status).toBe('candidate')
  await page.setViewportSize({ width: 320, height: 800 })
  await page.evaluate(() => {
    document.documentElement.style.fontSize = '200%'
  })
  await expect(page.getByRole('region', { name: '作者选定方向来源', exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
  await page.evaluate(() => {
    document.documentElement.style.fontSize = ''
  })
  await page.setViewportSize({ width: 1280, height: 900 })
  const request = page.waitForRequest((row) => row.url().endsWith('/api/modules') && row.method() === 'POST')
  await page.getByRole('button', { name: '生成候选 →', exact: true }).click()
  const submitted = (await request).postDataJSON()
  expect(submitted.action).toBe('writing.expand_plot')
  expect(submitted.chapter_id).toBe(chapter.id)
  expect(submitted.base_revision).toBe(3)
  expect(submitted.instruction).toContain(chosen)
  expect(submitted.instruction).toContain(`"advice_id":"${advice.id}"`)
  expect(submitted.instruction).toContain('"candidate_revision":0')
  expect(submitted.instruction).not.toContain('方向二：改查私藏账簿')
  expect(submitted.fixed).toEqual(['不修门', '不取得私藏账簿'])
  expect(submitted.writing_profile).toBe(profile)
  expect(submitted.reference_ids).toEqual([excerpt.id])
  await expect(page.getByRole('button', { name: '采纳到正文', exact: true })).toBeVisible()
  const comparison = page.getByRole('combobox', { name: '比较候选', exact: true })
  async function chooseArtifact(id: string) {
    await expect(comparison.locator(`option[value="${id}"]`)).toBeAttached()
    if ((await comparison.inputValue()) === id) return
    await Promise.all([
      page.waitForEvent('dialog').then(async (dialog) => {
        expect(dialog.message()).toContain('已载入的作者选定方向')
        await dialog.accept()
      }),
      comparison.selectOption(id),
    ])
    await expect(comparison).toHaveValue(id)
  }
  const rejectedId = await comparison.inputValue()
  const beforeReject = await (await page.request.get(`/api/artifacts/${rejectedId}`)).json()
  expect(beforeReject.kind).toBe('chapter_draft')
  expect(beforeReject.status).toBe('candidate')
  expect(beforeReject.chapter_id).toBe(chapter.id)
  expect((await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content).toBe(continued)
  await page.getByRole('button', { name: '拒绝', exact: true }).click()
  await expect
    .poll(async () => (await (await page.request.get(`/api/artifacts/${rejectedId}`)).json()).status)
    .toBe('rejected')
  // With another pending advice, the list may select it after rejecting a draft.
  // Inspect this exact rejected artifact, rather than assuming it stays selected.
  await expect(comparison.locator(`option[value="${rejectedId}"]`)).toContainText('已拒绝')
  await chooseArtifact(rejectedId)
  await expect(page.getByRole('button', { name: '采纳到正文', exact: true })).toBeDisabled()
  const disallowed = await page.request.post(`/api/artifacts/${rejectedId}/accept`, {
    headers,
    data: { idempotency_key: `rejected-${rejectedId}`, candidate_revision: beforeReject.candidate_revision },
  })
  expect(disallowed.status()).toBe(409)
  expect((await disallowed.json()).code).toBe('candidate_state')
  expect((await (await page.request.get(`/api/artifacts/${rejectedId}`)).json()).status).toBe('rejected')
  expect((await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content).toBe(continued)
  const nextTask = page.waitForResponse(
    (response) => response.url().endsWith('/api/modules') && response.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '生成候选 →', exact: true }).click()
  const next = await (await nextTask).json()
  await expect
    .poll(async () => (await (await page.request.get(`/api/tasks/${next.id}`)).json()).status)
    .toBe('succeeded')
  const finished = await (await page.request.get(`/api/tasks/${next.id}`)).json()
  expect(finished.result.artifact_id).not.toBe(rejectedId)
  await chooseArtifact(finished.result.artifact_id)
  await expect(page.getByRole('button', { name: '采纳到正文', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: '采纳到正文', exact: true }).click()
  await expect
    .poll(async () => (await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content)
    .toContain('旧尺')
  await page.getByRole('button', { name: '版本历史', exact: true }).click()
  const previous = page.locator('.history details').filter({ hasText: continued })
  await previous.locator('summary').click()
  await previous.getByRole('button', { name: '恢复为新修订', exact: true }).click()
  await expect
    .poll(async () => (await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content)
    .toBe(continued)
  expect((await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).revision).toBe(5)
  await page.getByRole('button', { name: '清除方向来源，保留当前要求', exact: true }).click()
  await chapterButtons.click()
  await expect(page.getByRole('textbox', { name: '章节标题', exact: true })).toHaveValue('其他章节')
  await page
    .getByRole('textbox', { name: '情节或具体要求', exact: true })
    .fill('本章独立要求，不能套用上一章方向。')
  await page.getByRole('button', { name: '恢复最近任务的输入', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('选定方向的目标已改变')
  await expect(page.getByRole('textbox', { name: '情节或具体要求', exact: true })).toHaveValue(
    '本章独立要求，不能套用上一章方向。',
  )
  const returnToSource = page.locator('.chapter-list button').filter({ hasText: '空白起笔章' })
  await Promise.all([
    page.waitForEvent('dialog').then(async (dialog) => {
      expect(dialog.message()).toContain('未提交的写作辅助设置')
      await dialog.dismiss()
    }),
    returnToSource.click(),
  ])
  await expect(page.getByRole('textbox', { name: '章节标题', exact: true })).toHaveValue('其他章节')
  await expect(page.getByRole('textbox', { name: '情节或具体要求', exact: true })).toHaveValue(
    '本章独立要求，不能套用上一章方向。',
  )
  await Promise.all([
    page.waitForEvent('dialog').then(async (dialog) => {
      expect(dialog.message()).toContain('未提交的写作辅助设置')
      await dialog.accept()
    }),
    returnToSource.click(),
  ])
  await expect(page.getByRole('textbox', { name: '章节标题', exact: true })).toHaveValue('空白起笔章')
  await page.getByRole('button', { name: '恢复最近任务的输入', exact: true }).click()
  await expect(page.getByRole('textbox', { name: '情节或具体要求', exact: true })).toHaveValue(chosen)
  await expect(page.getByRole('region', { name: '作者选定方向来源', exact: true })).toContainText(advice.id)
  await page.getByRole('button', { name: '清除方向来源，保留当前要求', exact: true }).click()
})
