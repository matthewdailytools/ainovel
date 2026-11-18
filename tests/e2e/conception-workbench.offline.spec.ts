import { expect, test, type Page } from '@playwright/test'
import type { ConceptionState } from '../../frontend/conception'

type Novel = { id: string; branch_id: string }

async function openWorkbench(page: Page, title: string, original?: string) {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const created = await page.request.post('/api/novels', { headers, data: { title } })
  expect(created.ok(), await created.text()).toBe(true)
  const novel: Novel = await created.json()
  let chapterId: string | undefined
  if (original) {
    const chapter = await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
      headers,
      data: { title: '渡口暗号' },
    })
    expect(chapter.ok(), await chapter.text()).toBe(true)
    chapterId = (await chapter.json()).id
    const saved = await page.request.put(`/api/chapters/${chapterId}`, {
      headers,
      data: { base_revision: 0, content: original },
    })
    expect(saved.ok(), await saved.text()).toBe(true)
  }
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '小说构思', exact: true }).click()
  await expect(page.getByRole('heading', { name: '构思工作台', exact: true })).toBeVisible()
  return { novel, chapterId, headers, base: `/api/branches/${novel.branch_id}/conception` }
}

async function readWorkbench(page: Page, base: string): Promise<ConceptionState> {
  const response = await page.request.get(base)
  expect(response.ok(), await response.text()).toBe(true)
  return response.json()
}

async function checkedCall(page: Page, taskId: string, stage: string, snapshotId: string) {
  await expect
    .poll(async () => {
      const transcript = await (await page.request.get(`/api/tasks/${taskId}/transcript`)).json()
      return transcript.steps.map((step: { result: { call_id: string } }) => step.result.call_id)
    })
    .toContain(`offline-conception-${stage}-${snapshotId}`)
}

test('立项建议可修改并以确认终局交给世界设定', async ({ page }) => {
  const { base } = await openWorkbench(page, '遗铃 · 立项交接')
  await page.locator('#bench-project').getByText('立项台', { exact: true }).click()
  await page.getByRole('textbox', { name: '创作思路', exact: true }).fill('学徒靠铜铃的断痕寻找师姐。')
  await page.getByRole('button', { name: 'AI 分析立项', exact: true }).click()
  await page.getByRole('button', { name: '载入 AI 立项建议', exact: true }).click()
  await expect(page.getByRole('combobox', { name: '主类型', exact: true })).toHaveValue('xianxia')
  const ending = page.getByRole('textbox', { name: '终局', exact: true })
  await expect(ending).toHaveValue('学徒独自带铃离山。')
  await ending.fill('师姐与学徒在门内共同修复铜铃。')
  await page.getByRole('button', { name: '确认立项', exact: true }).click()
  await expect(page.getByText('立项台 · 已确认修订 1', { exact: true })).toBeVisible()
  const project = (await readWorkbench(page, base)).project
  expect(project?.brief.ending).toBe('师姐与学徒在门内共同修复铜铃。')
  expect(project?.candidate_id).toBeTruthy()
  await page.getByRole('button', { name: '去完善世界设定', exact: true }).click()
  const queued = page.waitForResponse(
    (response) => response.url().endsWith(`${base}/generate`) && response.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '生成当前类别', exact: true }).click()
  const response = await queued
  expect(response.ok(), await response.text()).toBe(true)
  expect(response.request().postDataJSON()).toMatchObject({
    category_id: 'world',
    project_confirmation_id: project?.id,
  })
  await expect(page.getByText('本草案使用立项修订 1。', { exact: true })).toBeVisible()
})

test('遗铃从确认设定到正文对账并保留过期证据', async ({ page }) => {
  const { base, chapterId, headers } = await openWorkbench(page, '遗铃', '😀学徒拾起铜铃。')
  await page
    .getByRole('navigation', { name: '构思类别' })
    .getByRole('button', { name: /世界设定/ })
    .click()
  await page.getByRole('button', { name: '添加构思项目', exact: true }).click()
  await page.getByRole('textbox', { name: '项目名称', exact: true }).fill('铃声规则')
  await page.getByRole('textbox', { name: '设定内容', exact: true }).fill('铜铃只在门内响。')
  await page.getByRole('button', { name: '保存构思设定', exact: true }).click()
  await expect
    .poll(
      async () =>
        (await readWorkbench(page, base)).categories.find((category) => category.id === 'world')?.items[0]
          ?.detail,
    )
    .toBe('铜铃只在门内响。')
  await page.getByRole('textbox', { name: '保留事项说明', exact: true }).fill('先写开篇。')
  await page.getByRole('button', { name: '确认当前构思', exact: true }).click()
  await page.getByRole('button', { name: '去写作（使用构思）', exact: true }).click()
  await page.getByRole('button', { name: /渡口暗号/ }).click()
  const editor = page.locator('.cm-content[contenteditable=true]')
  await editor.press('End')
  await editor.press('Enter')
  await page.keyboard.insertText('铜铃在门外响了。')
  await page.getByText(/已确认世界设定 · 修订/).click()
  await page.getByText('铃声规则', { exact: true }).click()
  await page.getByRole('button', { name: '用此设定对账' }).click()
  const queued = page.waitForResponse(
    (response) => response.url().endsWith('/api/modules') && response.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '运行设定对账' }).click()
  const response = await queued
  expect(response.ok(), await response.text()).toBe(true)
  const report = page.getByRole('region', { name: '设定对账结果' })
  await expect(report.getByRole('status')).toHaveText('当前版本 · 冲突')
  await report.getByRole('link', { name: '查看对应设定' }).click()
  await expect(report.getByRole('region', { name: '设定依据' })).toBeFocused()
  await report.getByRole('link', { name: '查看对应正文段落' }).click()
  const evidence = report.getByRole('region', { name: '正文依据' })
  await expect(evidence).toBeFocused()
  await expect(evidence).toContainText('铜铃在门外响了。')
  const current = await (await page.request.get(`/api/chapters/${chapterId}`)).json()
  const updated = await page.request.put(`/api/chapters/${chapterId}`, {
    headers,
    data: { base_revision: current.revision, content: '铜铃在门内响了。' },
  })
  expect(updated.ok(), await updated.text()).toBe(true)
  await expect(report.getByRole('status')).toContainText('已过期')
  await expect(evidence).toContainText('铜铃在门外响了。')
})

test('按作品保存同级分类与项目，刷新恢复，并带缺项确认后交接写作', async ({ page }) => {
  const { base, novel, chapterId, headers } = await openWorkbench(
    page,
    '后台验证 · 构思分类和写作交接',
    '沈砚留下自己的测量笔记。😀',
  )
  const untouched = await page.request.post('/api/novels', {
    headers,
    data: { title: '后台验证 · 不继承其他小说构思' },
  })
  expect(untouched.ok(), await untouched.text()).toBe(true)
  const second: Novel = await untouched.json()
  const name = '账簿复核制度'
  const title = '纸面副本必须当场核对'
  const detail = '记录人在场时复述读数，留存签名；缺页不得推定内容。😀𠮷'
  await page.getByRole('textbox', { name: '类别名称', exact: true }).fill(name)
  await page.getByRole('button', { name: '添加构思类别', exact: true }).click()
  await page.getByRole('button', { name: '添加构思项目', exact: true }).click()
  await page.getByRole('textbox', { name: '项目名称', exact: true }).fill(title)
  const field = page.getByRole('textbox', { name: '设定内容', exact: true })
  await field.focus()
  await field.dispatchEvent('compositionstart')
  await field.fill(detail)
  await field.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true })
  await field.dispatchEvent('compositionend')
  expect((await readWorkbench(page, base)).categories.some((category) => category.title === name)).toBe(false)
  await page.getByRole('button', { name: '保存构思设定', exact: true }).click()
  await expect
    .poll(
      async () =>
        (await readWorkbench(page, base)).categories.find((category) => category.title === name)?.items[0]
          .detail,
    )
    .toBe(detail)
  expect(
    (await readWorkbench(page, `/api/branches/${second.branch_id}/conception`)).categories.some(
      (category) => category.title === name,
    ),
  ).toBe(false)

  await page.reload()
  await page.getByRole('button', { name: '小说构思', exact: true }).click()
  await page
    .getByRole('navigation', { name: '构思类别' })
    .getByRole('button', { name: new RegExp(name) })
    .click()
  await expect(field).toHaveValue(detail)
  await page.setViewportSize({ width: 320, height: 800 })
  await page.evaluate(() => (document.documentElement.style.fontSize = '200%'))
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await field.focus()
  await expect(field).toBeFocused()

  const before = await readWorkbench(page, base)
  expect(before.issues.length).toBeGreaterThan(0)
  await page
    .getByRole('textbox', { name: '保留事项说明', exact: true })
    .fill('先写第一章，人物和结局仍待作者核对。')
  const confirmationResponse = page.waitForResponse(
    (response) => response.url().endsWith(`${base}/confirm`) && response.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '确认当前构思', exact: true }).click()
  const confirmed = await confirmationResponse
  expect(confirmed.ok(), await confirmed.text()).toBe(true)
  const state = await readWorkbench(page, base)
  expect(state.confirmation?.current).toBe(true)
  const snapshotId = state.confirmation!.id
  expect(state.issues).toEqual(before.issues)
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.evaluate(() => (document.documentElement.style.fontSize = ''))
  await page.getByRole('button', { name: '去写作（使用构思）', exact: true }).click()
  await page.getByRole('combobox', { name: '这次要做什么', exact: true }).selectOption('writing.advise')
  await page
    .getByRole('textbox', { name: '情节或具体要求', exact: true })
    .fill('下一段如何保留可复核的读数？')
  const queued = page.waitForResponse(
    (response) => response.url().endsWith('/api/modules') && response.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '生成候选 →', exact: true }).click()
  const writing = await queued
  expect(writing.ok(), await writing.text()).toBe(true)
  expect(writing.request().postDataJSON().conception_snapshot_id).toBe(snapshotId)
  const task = await writing.json()
  await expect
    .poll(async () => (await (await page.request.get(`/api/tasks/${task.id}`)).json()).status)
    .toBe('succeeded')
  const done = await (await page.request.get(`/api/tasks/${task.id}`)).json()
  expect(done.result.conception_snapshot).toMatchObject({ id: snapshotId, revision: state.revision })
  await checkedCall(page, task.id, 'writing', snapshotId)
  const after = await (await page.request.get(`/api/chapters/${chapterId}`)).json()
  expect(after.content).toBe('沈砚留下自己的测量笔记。😀')
  const graph = await (await page.request.get(`/api/branches/${novel.branch_id}/story-graph`)).json()
  expect(graph.records).toEqual([])
})

test('逐类和一键生成均保留草案，采用所选项目并确认后交接两种批次', async ({ page }) => {
  const { base, novel } = await openWorkbench(page, '后台验证 · 构思生成与批次交接')
  const initial = await readWorkbench(page, base)
  expect(initial.categories.length).toBeGreaterThanOrEqual(6)
  const active = initial.categories[0]
  await page
    .getByRole('textbox', { name: '构思要求', exact: true })
    .fill('写一个修表学徒查找旧账缺页的故事。')
  const review = page.getByRole('region', { name: '核对 AI 草案', exact: true })
  for (const [button, category, covers] of [
    ['生成当前类别', active.id, [active.id]],
    ['一键生成全套草案', null, initial.categories.map((entry) => entry.id)],
  ] as const) {
    const queued = page.waitForResponse(
      (response) => response.url().endsWith(`${base}/generate`) && response.request().method() === 'POST',
    )
    await page.getByRole('button', { name: button, exact: true }).click()
    const reply = await queued
    expect(reply.ok(), await reply.text()).toBe(true)
    expect(reply.request().postDataJSON().category_id).toBe(category)
    await expect(page.getByRole('heading', { name: '核对 AI 草案', exact: true })).toBeVisible()
    const task = await reply.json()
    await expect
      .poll(async () => (await (await page.request.get(`/api/tasks/${task.id}`)).json()).status)
      .toBe('succeeded')
    const completed = await (await page.request.get(`/api/tasks/${task.id}`)).json()
    const artifact = await (await page.request.get(`/api/artifacts/${completed.result.artifact_id}`)).json()
    expect(artifact.payload.categories.map((entry: { id: string }) => entry.id)).toEqual(covers)
    await expect(review.getByRole('group', { name: '选择草案项目' }).locator(':scope > details')).toHaveCount(
      covers.length,
    )
    if (category === null) {
      expect(artifact.payload.model_review).toEqual({
        version: 'conception-consistency/1',
        issues: [{ category_id: 'core', message: '工程替身提示：请核对主角能否找回指定的缺页。' }],
      })
      expect(completed.result.pending_consistency_review).toBe(false)
      await review.getByText('AI 一致性复核', { exact: true }).click()
      await expect(review.getByText(/工程替身提示：请核对主角能否找回指定的缺页/)).toBeVisible()
    } else {
      expect(artifact.payload.model_review).toBeUndefined()
      await review.getByRole('checkbox', { name: `选择 候选 · ${active.title}`, exact: true }).check()
      const adopt = review.getByRole('button', { name: '采用所选构思', exact: true })
      await expect(adopt).toBeEnabled()
      page.once('dialog', (dialog) => void dialog.accept())
      await page.getByRole('button', { name: '拒绝整份草案', exact: true }).click()
      await expect(page.getByText('已拒绝 · 历史草案', { exact: true })).toBeVisible()
      const refused = await (await page.request.get(`/api/artifacts/${artifact.id}`)).json()
      expect(refused.status).toBe('rejected')
      expect(refused.payload).toEqual(artifact.payload)
      await expect(review.getByRole('textbox', { name: '项目名称', exact: true }).first()).toHaveValue(
        `候选 · ${active.title}`,
      )
      await expect(adopt).toBeDisabled()
    }
    expect((await readWorkbench(page, base)).categories).toEqual(initial.categories)
  }
  const candidate = `候选 · ${active.title}`
  await review.getByRole('checkbox', { name: `选择 ${candidate}`, exact: true }).check()
  await page.getByRole('button', { name: '采用所选构思', exact: true }).click()
  await expect
    .poll(async () => (await readWorkbench(page, base)).categories[0].items.map((item) => item.title))
    .toEqual([candidate])
  await page
    .getByRole('textbox', { name: '保留事项说明', exact: true })
    .fill('其余条目暂不采用，写作时继续核对。')
  await page.getByRole('button', { name: '确认当前构思', exact: true }).click()
  await expect.poll(async () => (await readWorkbench(page, base)).confirmation?.current).toBe(true)
  const after = await readWorkbench(page, base)
  const snapshotId = after.confirmation!.id
  for (const [label, mode] of [
    ['半自动', 'assisted'],
    ['全自动', 'automatic'],
  ] as const) {
    await page.getByRole('button', { name: `去${label}创作`, exact: true }).click()
    await expect(page.getByRole('radio', { name: new RegExp(`^${label}`) })).toBeChecked()
    const brief = page.getByRole('textbox', { name: '作品设定、粗纲与结局方向', exact: true })
    await expect(brief).toHaveValue(new RegExp(candidate))
    await brief.fill((await brief.inputValue()) + '\n工程验收构思快照')
    await page.getByLabel('计划章数', { exact: true }).fill('1')
    await page.getByLabel('每章字数下限').fill('200')
    await page.getByLabel('每章字数上限').fill('600')
    if (mode === 'assisted') {
      await page.getByLabel('作者批量情节（每行一章）').fill('测量 | 用旧尺测量潮水并记下读数')
    }
    const queued = page.waitForResponse(
      (response) => response.url().endsWith('/api/workflows') && response.request().method() === 'POST',
    )
    await page.getByRole('button', { name: '创建独立版本并启动', exact: true }).click()
    const reply = await queued
    expect(reply.ok(), await reply.text()).toBe(true)
    expect(reply.request().postDataJSON()).toMatchObject({ conception_snapshot_id: snapshotId, mode })
    const task = await reply.json()
    expect(task.branch_id).not.toBe(novel.branch_id)
    expect(task.result.conception_snapshot).toMatchObject({ id: snapshotId, revision: after.revision })
    if (mode === 'automatic') await checkedCall(page, task.id, 'planning', snapshotId)
    await checkedCall(page, task.id, 'prose', snapshotId)
    await expect(page.getByRole('status').filter({ hasText: '已创建独立工作版本' })).toBeVisible()
    const branch = page.getByRole('combobox', { name: '工作版本', exact: true })
    await expect(branch).toHaveValue(task.branch_id)
    const accept = (dialog: import('@playwright/test').Dialog) => void dialog.accept()
    page.on('dialog', accept)
    await branch.selectOption(novel.branch_id)
    await expect(branch).toHaveValue(novel.branch_id)
    await page.getByRole('button', { name: '小说构思', exact: true }).click()
    await expect(page.getByRole('heading', { name: '构思工作台', exact: true })).toBeVisible()
    page.off('dialog', accept)
  }
  expect(await (await page.request.get(`/api/branches/${novel.branch_id}/chapters`)).json()).toEqual([])
})

test('从构思工作台核对旧作正文候选，采用来源记录并保留原始正文', async ({ page }) => {
  const rule = '渡口规矩是收工后才登记借出的尺。'
  const text = '😀宁禾看懂暗号，钱叔收起铜牌。' + rule
  const { base, novel, chapterId } = await openWorkbench(page, '后台验证 · 构思旧作反向整理', text)
  const original = await (await page.request.get(`/api/chapters/${chapterId}`)).json()
  await page.getByText('从旧作正文反向提取', { exact: true }).click()
  await page.getByRole('checkbox', { name: '选择用于提取世界规则的章节 1 · 渡口暗号', exact: true }).check()
  const worldRequest = page.waitForResponse(
    (response) => response.url().endsWith(`${base}/extract-world`) && response.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '从所选保存稿提取世界设定', exact: true }).click()
  const worldReply = await worldRequest
  expect(worldReply.ok(), await worldReply.text()).toBe(true)
  expect(worldReply.request().postDataJSON().chapter_ids).toEqual([chapterId])
  const review = page.getByRole('region', { name: '核对 AI 草案', exact: true })
  await review.getByText('查看原文依据', { exact: true }).click()
  await expect(review.getByText(rule, { exact: true })).toBeVisible()
  expect((await readWorkbench(page, base)).categories.every((category) => !category.items.length)).toBe(true)
  await review.getByRole('checkbox', { name: '选择 渡口借尺登记', exact: true }).check()
  await review.getByRole('button', { name: '采用所选构思', exact: true }).click()
  await expect
    .poll(
      async () =>
        (await readWorkbench(page, base)).categories.find((category) => category.id === 'world')?.items[0]
          ?.title,
    )
    .toBe('渡口借尺登记')
  await page.getByText('按保存稿整理人物、事件、状态和伏笔候选', { exact: true }).click()
  const panel = page.getByRole('region', { name: '结构化整理与确认', exact: true })
  await panel.getByRole('checkbox', { name: '1 · 渡口暗号', exact: true }).check()
  await panel.getByRole('button', { name: '从所选保存稿生成结构候选', exact: true }).click()
  await expect(panel.getByRole('checkbox', { name: '采用人物：宁禾', exact: true })).toBeVisible()
  const graphUrl = `/api/branches/${novel.branch_id}/story-graph`
  expect((await (await page.request.get(graphUrl)).json()).records).toEqual([])
  for (const box of await panel.locator('article input[type=checkbox]').all()) await box.check()
  await panel.getByRole('button', { name: '确认采用所选结构（不修改正文）', exact: true }).click()
  await expect(
    panel.getByText('所选结构已采用，未选项保留在原产物，正文未改变。', { exact: true }),
  ).toBeVisible()
  const graph = await (await page.request.get(graphUrl)).json()
  expect(graph.records).toHaveLength(6)
  const originalAfter = await (await page.request.get(`/api/chapters/${chapterId}`)).json()
  expect(originalAfter.content).toBe(original.content)
  expect(originalAfter.revision).toBe(original.revision)
  const state = await readWorkbench(page, base)
  expect(state.story_revision).toBe(graph.story_revision)
  expect(state.observed).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ title: '宁禾', category_id: 'characters' }),
      expect.objectContaining({ title: '认出暗号', category_id: 'events' }),
      expect.objectContaining({ title: '铜牌来历', category_id: 'foreshadowing' }),
    ]),
  )
  expect(state.categories.filter((category) => category.items.length).map((category) => category.id)).toEqual(
    ['world'],
  )
})
