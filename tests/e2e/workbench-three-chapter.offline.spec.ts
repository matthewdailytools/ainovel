import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { readFile } from 'node:fs/promises'

test.use({ actionTimeout: 15_000 })

const title = '潮尺与封账'
const chapterTitles = ['旧尺试量', '封套里的复测', '归尺封账']
const plans = [
  '旧尺试量 | 沈砚借旧尺试量，发现两份读数有差异，把初次读数留在原账页。',
  '封套里的复测 | 次日用共同刻度复测，明确哪些数字可公开，把更正说明另封。',
  '归尺封账 | 沈砚依约归还旧尺，将原账与更正说明分开封存，留下查验途径。',
].join('\n')
const fixed = '原账须保留，可附更正说明。'
const editedNote = '保留原账页和首次差值，两次记录分别可查。'
const baseLine = '他把旧尺平放在柜边，约定明早归还。'
const adoptedLine = '他把蓝绳系回旧尺，再把尺平放在柜边，约定明早归还。'

type ModelCall = {
  action: string
  chapter: number | null
  call_id: string
  dispatch_event_id: number
  fingerprint: string
  task_id: string
  author_structure_note_in_context: boolean
  author_body_edit_in_context: boolean
  chapter2_context: { id: string; revision: number; author_edit_marker: boolean }[]
}

async function read(page: Page, path: string) {
  const response = await page.request.get(path)
  expect(response.ok(), await response.text()).toBeTruthy()
  return response.json()
}

function reply(page: Page, method: string, path: string) {
  return page.waitForResponse((r) => r.request().method() === method && new URL(r.url()).pathname === path)
}

function button(page: Page, name: string) {
  return page.getByRole('button', { name, exact: true })
}

function nav(page: Page, name: string) {
  return button(page, name).click()
}

async function evidence(page: Page, info: TestInfo, name: string) {
  await info.attach(name, { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' })
}

async function checkpoint(
  page: Page,
  taskId: string,
  status: string,
  next: number,
  waitFor?: 'waiting_artifact' | 'waiting_structure',
) {
  let task: Awaited<ReturnType<typeof read>>
  await expect
    .poll(async () => {
      task = await read(page, `/api/tasks/${taskId}`)
      return [task.status, task.result.next_chapter, waitFor ? !!task.result[waitFor] : true]
    })
    .toEqual([status, next, true])
  return task
}

async function confirmStructure(page: Page, taskId: string, next: number, info: TestInfo, edit = false) {
  const state = await checkpoint(page, taskId, 'needs_review', next, 'waiting_structure')
  const id = state.result.waiting_structure
  expect(id).toBeTruthy()
  await expect(page.getByRole('heading', { name: '正文已采用，结构整理等待核对' })).toBeVisible()
  await expect(button(page, '从保存的检查点恢复')).toBeDisabled()
  await expect(page.getByText(/当前不会推进下一章/)).toBeVisible()
  await evidence(page, info, `需要确认结构-第${next - 1}章`)
  await button(page, '打开此批次的结构审阅').click()
  await page.getByText('按保存稿整理人物、事件、状态和伏笔候选', { exact: true }).click()
  const panel = page.getByRole('region', { name: '结构化整理与确认', exact: true })
  await expect(panel.getByRole('combobox', { name: '保存的结构候选' })).toHaveValue(id)
  const record = panel
    .locator('article')
    .filter({ has: page.getByRole('checkbox', { name: /采用(人物|事件)/ }) })
    .first()
  await expect(record).toBeVisible()
  await record.getByText('核对具体内容、关联和原文', { exact: true }).click()
  await expect(record.locator('blockquote').first()).toBeVisible()
  if (edit) {
    const previous = await read(page, `/api/artifacts/${id}`)
    await record.getByRole('button', { name: '修改结构候选说明', exact: true }).click()
    await record.getByRole('textbox', { name: '结构候选说明', exact: true }).fill(editedNote)
    await expect(
      panel.getByRole('button', { name: '确认采用所选结构（不修改正文）', exact: true }),
    ).toBeDisabled()
    const edited = reply(page, 'PATCH', `/api/branches/${state.branch_id}/story-extractions/${id}/notes`)
    await record.getByRole('button', { name: '保存候选说明', exact: true }).click()
    const response = await edited
    expect(response.ok(), await response.text()).toBeTruthy()
    const saved = await response.json()
    expect(saved.candidate_revision).toBe(previous.candidate_revision + 1)
    expect(saved.payload.source_manifest).toEqual(previous.payload.source_manifest)
    expect(saved.payload.scenes).toEqual(previous.payload.scenes)
    await expect(record.getByText(editedNote, { exact: true })).toBeVisible()
    await evidence(page, info, '已保存作者修改的结构说明')
  }
  for (const choice of await panel
    .getByRole('checkbox', { name: /^采用(人物|事件|时点状态|伏笔|关系|场景)：/ })
    .all()) {
    await choice.check()
  }
  await panel.getByRole('button', { name: '确认采用所选结构（不修改正文）', exact: true }).click()
  await expect(panel.getByText('所选结构与场景已采用，旧绑定历史保留，正文未改变。')).toBeVisible()
  expect((await read(page, `/api/artifacts/${id}`)).status).toBe('accepted')
  await nav(page, '整书创作')
  return id
}

async function writingChapter(page: Page, name: string) {
  await nav(page, '写作')
  const chapter = page.locator('.chapter-list').getByRole('button', { name: new RegExp(name) })
  await chapter.click()
  await expect(chapter).toHaveAttribute('aria-current', 'true')
  await expect(button(page, '版本历史')).toBeEnabled()
  return page.getByRole('textbox', { name: '章节正文', exact: true })
}

test('同一套三章的工作台验收：编辑确认、版本冲突、断点恢复与操作提示', async ({ page, browser }, info) => {
  test.setTimeout(180_000)
  await page.goto('/')
  const firstWork = page.getByRole('textbox', { name: '第一部作品的名字', exact: true })
  const novelCreated = reply(page, 'POST', '/api/novels')
  if (await firstWork.count()) {
    await firstWork.fill(title)
    await button(page, '打开写作台 →').click()
  } else {
    await page.getByText('＋ 新建作品', { exact: true }).click()
    await page.getByRole('textbox', { name: '作品名称', exact: true }).fill(title)
    await button(page, '创建并开始写作').click()
  }
  const novelResponse = await novelCreated
  expect(novelResponse.status()).toBe(201)
  const novel = await novelResponse.json()

  await nav(page, '整书创作')
  await page.getByRole('radio', { name: /^半自动/ }).check()
  await page
    .getByLabel('作品设定、粗纲与结局方向')
    .fill(`${title}。沈砚为核对渡口的账目借用旧尺，复测后归还。`)
  await page.getByLabel('作者批量情节（每行一章）').fill(plans.replace('留下查验途径', '待后续安排'))
  await page.getByLabel('作者批量情节（每行一章）').fill(plans)
  await page.getByLabel('全书固定条件（每行一条）').fill(fixed)
  await page.getByLabel('计划章数', { exact: true }).fill('3')
  await page.getByLabel('每章字数下限').fill('200')
  await page.getByLabel('每章字数上限').fill('600')
  await page.getByRole('combobox', { name: '采纳政策', exact: true }).selectOption('manual')
  await expect(page.getByRole('checkbox', { name: '每章采用后整理人物、时点、事件和伏笔' })).toBeChecked()
  const started = reply(page, 'POST', '/api/workflows')
  await button(page, '创建独立版本并启动').click()
  const response = await started
  expect(response.status(), await response.text()).toBe(202)
  const run = await response.json()
  const taskId: string = run.id
  const branchId: string = run.branch_id
  expect(run.novel_id).toBe(novel.id)
  expect(branchId).not.toBe(novel.branch_id)
  const first = await checkpoint(page, taskId, 'needs_review', 1, 'waiting_artifact')
  expect(first.result.plan.chapters.map((chapter: { title: string }) => chapter.title)).toEqual(chapterTitles)
  expect(first.result.plan.chapters[2].plot).toContain('留下查验途径')
  await expect(page.getByRole('heading', { name: '待审章节候选' })).toBeVisible()
  const plan = page.getByText('采用规划与整书检查报告', { exact: true })
  await plan.click()
  await expect(plan.locator('..').getByText(/留下查验途径/)).toBeVisible()
  await plan.click()
  await evidence(page, info, '第1章待审正文与批次进度')
  await button(page, '作者采纳此章').click()
  await expect(page.getByText('候选已采纳。点击恢复继续下一章。')).toBeVisible()
  await page.getByRole('checkbox', { name: '完成已采纳章节维护后暂停', exact: true }).check()
  await button(page, '从保存的检查点恢复').click()
  const firstStructure = await confirmStructure(page, taskId, 2, info, true)
  await button(page, '从保存的检查点恢复').click()
  const paused = await checkpoint(page, taskId, 'paused', 2)
  expect(paused.result.structure_history).toHaveLength(1)
  expect(paused.result.structure_history[0].artifact_id).toBe(firstStructure)
  const chapterOne = await read(page, `/api/artifacts/${first.result.waiting_artifact}`)
  expect(chapterOne.status).toBe('accepted')
  const keptFirst = await read(page, `/api/chapters/${chapterOne.chapter_id}`)
  const proofPath = `/test/three-chapter/${taskId}/proof`
  const beforeResume = await read(page, proofPath)
  expect(beforeResume.chapters.map((chapter: { position: number }) => chapter.position)).toEqual([1])
  await evidence(page, info, '第1章维护后暂停')

  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('combobox', { name: '工作版本', exact: true }).selectOption(branchId)
  await nav(page, '整书创作')
  await expect(button(page, '从保存的检查点恢复')).toBeEnabled()
  await expect(page.getByText('已确认的内容已保留。从保存的检查点恢复后继续第 2 章。')).toBeVisible()
  const reopened = await checkpoint(page, taskId, 'paused', 2)
  expect(reopened.result.plan).toEqual(paused.result.plan)
  expect(reopened.result.structure_history).toEqual(paused.result.structure_history)
  expect(await read(page, proofPath)).toEqual(beforeResume)
  await evidence(page, info, '重新打开后沿用原检查点')
  await page.setViewportSize({ width: 320, height: 900 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
  const resume = button(page, '从保存的检查点恢复')
  await resume.focus()
  await expect(resume).toBeFocused()
  await resume.press('Enter')
  await page.setViewportSize({ width: 1280, height: 900 })
  const second = await checkpoint(page, taskId, 'needs_review', 2, 'waiting_artifact')
  const secondId: string = second.result.waiting_artifact
  const draft = await read(page, `/api/artifacts/${secondId}`)
  expect(draft.payload.text).toContain(baseLine)
  const advice = page.getByRole('region', { name: '候选检查意见' })
  await expect(advice.getByText('可选偏好建议', { exact: true })).toBeVisible()
  await expect(advice.getByText(baseLine, { exact: true })).toBeVisible()
  await writingChapter(page, chapterTitles[1])
  await page.getByLabel('比较候选').selectOption(secondId)
  await expect(page.getByText('可选偏好建议', { exact: true })).toBeVisible()
  await evidence(page, info, '第2章一般建议随候选显示')
  const text = page.getByRole('textbox', { name: '候选内容', exact: true })
  await expect(text).toHaveValue(draft.payload.text)
  const editedDraft = draft.payload.text.replace(baseLine, adoptedLine)
  await text.fill(editedDraft)
  const edit = reply(page, 'PATCH', `/api/artifacts/${secondId}`)
  await button(page, '保存候选编辑（原检查失效）').click()
  const editResponse = await edit
  expect(editResponse.status()).toBe(200)
  expect((await editResponse.json()).candidate_revision).toBe(draft.candidate_revision + 1)
  await expect(text).toHaveValue(editedDraft)
  await evidence(page, info, '第2章候选已编辑并保存')
  await expect(page.getByText(/原选择已失效/)).toBeVisible()
  await button(page, '读取新版记忆并重新选择').click()
  await expect(button(page, '采纳到正文')).toBeEnabled()
  const chosen = reply(page, 'POST', `/api/artifacts/${secondId}/accept`)
  await button(page, '采纳到正文').click()
  expect((await chosen).ok()).toBeTruthy()
  const chapterPath = `/api/chapters/${draft.chapter_id}`
  const beforeConflict = await read(page, chapterPath)
  expect(beforeConflict.content).toBe(editedDraft)

  const secondContext = await browser.newContext()
  try {
    const other = await secondContext.newPage()
    await other.clock.install()
    await other.goto('/')
    await other.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
    await other.getByRole('combobox', { name: '工作版本', exact: true }).selectOption(branchId)
    const otherEditor = await writingChapter(other, chapterTitles[1])
    await expect(otherEditor.locator('.cm-line')).toHaveText(editedDraft.split('\n'))
    await expect(other.getByText(new RegExp(`非空白字符 · 修订 ${beforeConflict.revision}$`))).toBeVisible()
    const currentEditor = page.getByRole('textbox', { name: '章节正文', exact: true })
    const serverText = `${editedDraft}\n他把复测日期写在封套外侧。`
    const localText = `${editedDraft}\n他在借条背面记清归还时辰。`
    const merged = `${serverText}\n他在借条背面记清归还时辰。`
    await currentEditor.fill(serverText)
    await button(page, '保存').click()
    await expect.poll(async () => (await read(page, chapterPath)).content).toBe(serverText)
    const serverRevision = (await read(page, chapterPath)).revision
    const miss = reply(other, 'PUT', chapterPath)
    await otherEditor.fill(localText)
    await button(other, '保存').click()
    const conflict = await miss
    expect(conflict.status()).toBe(409)
    expect(conflict.request().postDataJSON().base_revision).toBe(beforeConflict.revision)
    expect((await conflict.json()).code).toBe('revision_conflict')
    await expect(other.getByText('保存失败，浏览器草稿保留；可下载草稿后比较')).toBeVisible()
    expect((await read(page, chapterPath)).content).toBe(serverText)
    await expect(other.getByText('第 2 章保存冲突', { exact: true })).toBeVisible()
    await button(other, '对照两份稿件').click()
    await expect(other.getByText('浏览器草稿与服务中的修订不同。')).toBeVisible()
    await expect(
      other.getByText(
        `草稿基线 ${beforeConflict.revision}，当前修订 ${serverRevision}。请比较后决定，服务稿尚未改动。`,
      ),
    ).toBeVisible()
    await expect(otherEditor.locator('.cm-line')).toHaveText(serverText.split('\n'))
    const comparison = other.locator('.notice').filter({ hasText: '浏览器草稿与服务中的修订不同。' })
    await expect(comparison.getByLabel('本机服务稿')).toContainText('他把复测日期写在封套外侧。')
    await expect(comparison.getByLabel('浏览器草稿')).toContainText('他在借条背面记清归还时辰。')
    await evidence(other, info, '第2章实际版本冲突与两份稿件')
    await button(other, '将此草稿载入编辑区').click()
    await expect(other.getByText('合并稿待手动保存', { exact: true })).toBeVisible()
    await other.clock.fastForward(1200)
    expect((await read(page, chapterPath)).content).toBe(serverText)
    await otherEditor.fill(merged)
    await other.clock.fastForward(1200)
    expect((await read(page, chapterPath)).content).toBe(serverText)
    await button(other, '保存').click()
    await expect.poll(async () => (await read(other, chapterPath)).content).toBe(merged)
    await evidence(other, info, '合并冲突后的确认原稿')
  } finally {
    await secondContext.close()
  }

  await nav(page, '整书创作')
  await button(page, '从保存的检查点恢复').click()
  await confirmStructure(page, taskId, 3, info)
  await button(page, '从保存的检查点恢复').click()
  const third = await checkpoint(page, taskId, 'needs_review', 3, 'waiting_artifact')
  await expect(page.getByRole('heading', { name: '待审章节候选' })).toBeVisible()
  await button(page, '作者采纳此章').click()
  await expect(page.getByText('候选已采纳。点击恢复继续下一章。')).toBeVisible()
  await button(page, '从保存的检查点恢复').click()
  await confirmStructure(page, taskId, 4, info)
  await button(page, '从保存的检查点恢复').click()
  const final = await checkpoint(page, taskId, 'production_complete', 4)
  expect(final.result.structure_history).toHaveLength(3)
  expect(final.result.plan).toEqual(paused.result.plan)
  expect(await read(page, `/api/chapters/${chapterOne.chapter_id}`)).toEqual(keptFirst)
  expect((await read(page, `/api/artifacts/${third.result.waiting_artifact}`)).status).toBe('accepted')
  const afterResume = await read(page, proofPath)
  await info.attach('暂停前与完结全链路证据', {
    body: JSON.stringify({ beforeResume, afterResume }, null, 2),
    contentType: 'application/json',
  })
  expect(
    afterResume.events.filter(
      (event: { kind: string; payload: { status?: string } }) =>
        event.kind === 'checkpoint' && event.payload.status === 'paused',
    ),
  ).toHaveLength(1)
  expect(afterResume.mock_calls.slice(0, beforeResume.mock_calls.length)).toEqual(beforeResume.mock_calls)
  expect(afterResume.completed_steps.slice(0, beforeResume.completed_steps.length)).toEqual(
    beforeResume.completed_steps,
  )
  const firstReceipts = new Set([chapterOne.id, firstStructure])
  expect(
    afterResume.adoption_receipts.filter((r: { artifact_id: string }) => firstReceipts.has(r.artifact_id)),
  ).toEqual(beforeResume.adoption_receipts)
  const calls: ModelCall[] = afterResume.mock_calls
  expect(new Set(calls.map((call) => `${call.task_id}/${call.fingerprint}`)).size).toBe(calls.length)
  expect(new Set(calls.map((call) => call.call_id)).size).toBe(calls.length)
  expect(new Set(calls.map((call) => call.dispatch_event_id)).size).toBe(calls.length)
  expect(afterResume.provider_ledger_rows).toEqual([])
  expect(afterResume.adoption_receipts.map((r: { artifact_id: string }) => r.artifact_id).sort()).toEqual(
    [
      chapterOne.id,
      secondId,
      third.result.waiting_artifact,
      ...final.result.structure_history.map((entry: { artifact_id: string }) => entry.artifact_id),
    ].sort(),
  )
  const drafts = calls.filter((call) => call.action === '按章纲写完整正文')
  expect(drafts.map((call) => call.chapter)).toEqual([1, 2, 3])
  expect(drafts[1].author_structure_note_in_context).toBe(true)
  expect(drafts[2].author_body_edit_in_context).toBe(true)
  const confirmedSecond = await read(page, chapterPath)
  expect(drafts[2].chapter2_context).toEqual([
    expect.objectContaining({
      id: confirmedSecond.id,
      revision: confirmedSecond.revision,
      author_edit_marker: true,
    }),
  ])
  expect(afterResume.chapters.map((chapter: { title: string }) => chapter.title)).toEqual(chapterTitles)
  await expect(page.getByText(/已完成 3 次章节结构维护/)).toBeVisible()
  const exportLink = page.getByRole('link', { name: '导出该批次当前稿', exact: true })
  const downloadPromise = page.waitForEvent('download')
  await exportLink.click()
  const download = await downloadPromise
  const exported = info.outputPath('潮尺与封账.md')
  await download.saveAs(exported)
  const manuscript = await readFile(exported, 'utf-8')
  for (const chapter of afterResume.chapters) expect(manuscript).toContain(chapter.content)
  await info.attach('三章确认稿', { path: exported, contentType: 'text/markdown' })
  await evidence(page, info, '三章完成与当前导出入口')
})
