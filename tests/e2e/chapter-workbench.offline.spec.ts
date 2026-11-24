import { expect, test, type Page } from '@playwright/test'

const firstTitle = '石碑与扣减'
const secondTitle = '天亮后的竹匣'
const carvedRule = '不得烧毁旧符。'
const firstCost = '施术后，宁禾折损了十年寿元。'
const editedCost = '施术后，宁禾折损了十二年寿元。'
const secondCost = '宁禾仍记得昨夜折损了十年寿元。'
const box = '她在天亮后才打开竹匣。'
const lifespan = '本章和上一章所写的寿元扣减都应为十年。'
const opening = '本章应在天亮后打开竹匣。'
const foreshadowing = '她把来历未明的青石收进袖中。'
const revisedForeshadowing = '她把来历未明的青石藏进左袖。'

async function selectChapter(page: Page, title: string) {
  const directory = page.getByRole('button', { name: '目录', exact: true })
  if (await directory.isVisible()) await directory.click()
  await page.locator('.chapter-list button').filter({ hasText: title }).click()
  await expect(page.getByRole('textbox', { name: '章节标题', exact: true })).toHaveValue(title)
}

test('相邻两章：章纲、设定原文、前章改稿影响、单项复检和历史', async ({ page }, info) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novelResponse = await page.request.post('/api/novels', {
    headers,
    data: { title: '后台验证 · 相邻两章工作台' },
  })
  expect(novelResponse.ok(), await novelResponse.text()).toBeTruthy()
  const novel = await novelResponse.json()
  const branch = `/api/branches/${novel.branch_id}`
  const conceptionUrl = branch + '/conception'
  async function addChapter(title: string, content: string) {
    const created = await page.request.post(branch + '/chapters', { headers, data: { title } })
    expect(created.ok(), await created.text()).toBeTruthy()
    const { id } = await created.json()
    const saved = await page.request.put(`/api/chapters/${id}`, {
      headers,
      data: { base_revision: 0, content },
    })
    expect(saved.ok(), await saved.text()).toBeTruthy()
    return saved.json()
  }
  const first = await addChapter(
    firstTitle,
    `石碑最下一行只刻了一条禁令：${carvedRule}\n${firstCost}\n${foreshadowing}`,
  )
  const second = await addChapter(secondTitle, `${secondCost}\n${box}`)
  const secondUrl = `/api/chapters/${second.id}`
  const outline = '目标：查读石碑。阻碍：术法扣减寿元。结果：只埋青石来历，暂不回收。'
  const conception = await (await page.request.get(conceptionUrl)).json()
  const updateConception = await page.request.put(conceptionUrl, {
    headers,
    data: {
      base_revision: conception.revision,
      base_story_revision: conception.story_revision,
      categories: conception.categories.map((category: { id: string; items: unknown[] }) =>
        category.id === 'world'
          ? {
              ...category,
              items: [
                ...category.items,
                { id: 'lifespan', title: '寿元约束', detail: '施术固定扣减十年寿元。', links: [] },
              ],
            }
          : category,
      ),
    },
  })
  expect(updateConception.ok(), await updateConception.text()).toBeTruthy()
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  const editor = page.getByRole('textbox', { name: '章节正文', exact: true })
  const outlinePanel = page.getByRole('button', { name: /^章纲 · / })
  await expect(editor.locator('.cm-line')).toHaveText(first.content.split('\n'))
  await expect(outlinePanel).toHaveText('章纲 · 暂无')
  await outlinePanel.click()
  await page.getByRole('button', { name: '记下作者章纲', exact: true }).click()
  await page.getByRole('textbox', { name: '本章安排', exact: true }).fill(outline)
  await page.getByRole('button', { name: '保存章纲', exact: true }).click()
  await expect(outlinePanel).toHaveText('章纲 · 作者章纲')
  await expect(page.getByText(outline, { exact: true })).toBeVisible()
  await outlinePanel.press('Enter')
  await expect(page.getByText(outline, { exact: true })).toBeHidden()
  await outlinePanel.press('Enter')
  await expect(page.getByText(outline, { exact: true })).toBeVisible()

  const firstUrl = `/api/chapters/${first.id}`
  const firstAfterOutline = await (await page.request.get(firstUrl)).json()
  expect(firstAfterOutline.revision).toBe(first.revision)
  expect(firstAfterOutline.content).toBe(first.content)
  await editor.press('ControlOrMeta+End')
  await editor.press('Shift+Home')
  await page
    .getByRole('combobox', { name: '这次要做什么', exact: true })
    .selectOption('writing.rewrite_selection')
  await page
    .getByRole('textbox', { name: '情节或具体要求', exact: true })
    .fill('后台验证 · 只调整藏青石的动作')
  await page.getByRole('button', { name: '生成候选 →', exact: true }).click()
  const adopt = page.getByRole('button', { name: '采纳到正文', exact: true })
  await expect(adopt).toBeEnabled()
  expect((await (await page.request.get(firstUrl)).json()).content).toBe(first.content)
  await adopt.click()
  const firstBaseline = first.content.replace(foreshadowing, revisedForeshadowing)
  await expect.poll(async () => (await (await page.request.get(firstUrl)).json()).content).toBe(firstBaseline)
  const firstAfterAdoption = await (await page.request.get(firstUrl)).json()
  const fact = await page.request.post('/api/facts', {
    headers,
    data: {
      chapter_id: first.id,
      source_revision: firstAfterAdoption.revision,
      subject: '石碑禁令',
      kind: 'world',
      statement: '石碑禁止焚毁旧符。',
      truth: 'fact',
      evidence: carvedRule,
    },
  })
  expect(fact.ok(), await fact.text()).toBeTruthy()

  await selectChapter(page, secondTitle)
  await expect(outlinePanel).toHaveText('章纲 · 暂无')
  await expect(editor).toBeEditable()
  const continued = second.content + '\n她把匣盖放回桌上。'
  await editor.press('ControlOrMeta+End')
  await editor.press('End')
  await editor.press('Enter')
  await page.keyboard.insertText('她把匣盖放回桌上。')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect.poll(async () => (await (await page.request.get(secondUrl)).json()).content).toBe(continued)
  const secondBaseline = await (await page.request.get(secondUrl)).json()
  const caret = () =>
    editor.evaluate((element) => {
      const selection = window.getSelection()
      return selection?.anchorNode && element.contains(selection.anchorNode)
        ? { text: selection.anchorNode.textContent, offset: selection.anchorOffset }
        : null
    })
  await editor.press('ControlOrMeta+End')
  const beforeLookUp = await caret()
  expect(beforeLookUp).toEqual({ text: '她把匣盖放回桌上。', offset: '她把匣盖放回桌上。'.length })
  const settings = page.getByRole('region', { name: '本章设定依据', exact: true })
  const rule = settings.getByRole('article').filter({ hasText: '石碑禁令' })
  await rule.getByText('查看设定原文', { exact: true }).press('Enter')
  await expect(rule.getByText(carvedRule, { exact: true })).toBeVisible()
  await expect(rule).toContainText(`修订 ${firstAfterAdoption.revision}`)
  await rule.getByRole('button', { name: '定位来源原文', exact: true }).press('Enter')
  await expect(rule.locator('mark')).toHaveText(carvedRule)
  await expect(rule).toContainText('石碑最下一行只刻了一条禁令')
  await expect(page.getByRole('textbox', { name: '章节标题', exact: true })).toHaveValue(secondTitle)
  await expect(editor.locator('.cm-line')).toHaveText(continued.split('\n'))
  await editor.focus()
  expect(await caret()).toEqual(beforeLookUp)

  const checks = page.getByRole('region', { name: '本章校验', exact: true })
  const checkUrl = `/api/chapters/${second.id}/checks`
  async function createCheck(condition: string, includePrevious: boolean) {
    await checks.getByText('新建校验项', { exact: true }).click()
    await checks.getByRole('textbox', { name: '校验要求', exact: true }).fill(condition)
    if (includePrevious) {
      await checks.getByRole('checkbox', { name: '同时核对上一章', exact: false }).check()
      await checks.getByRole('combobox', { name: /^设定依据/ }).selectOption({ label: '寿元约束' })
    }
    const response = page.waitForResponse(
      (r) => r.url().endsWith(checkUrl) && r.request().method() === 'POST',
    )
    await checks.getByRole('button', { name: '运行此项校验', exact: true }).click()
    const submitted = await response
    expect(submitted.status(), await submitted.text()).toBe(202)
    const task = await submitted.json()
    await expect
      .poll(async () => (await (await page.request.get(`/api/tasks/${task.id}`)).json()).status)
      .toBe('succeeded')
    return checks.getByRole('article').filter({ hasText: condition })
  }
  const related = await createCheck(lifespan, true)
  const unrelated = await createCheck(opening, false)
  await expect(related).toContainText('符合要求')
  await expect(unrelated).toContainText('符合要求')
  const initial = await (await page.request.get(checkUrl)).json()
  expect(initial.items).toHaveLength(2)
  const initialRelated = initial.items.find((row: { condition: string }) => row.condition === lifespan)
  const initialUnrelated = initial.items.find((row: { condition: string }) => row.condition === opening)
  expect(initialRelated.status).toBe('current')
  expect(initialUnrelated.status).toBe('current')

  await selectChapter(page, firstTitle)
  const edited = firstBaseline.replace(firstCost, editedCost)
  await editor.fill(edited)
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect.poll(async () => (await (await page.request.get(firstUrl)).json()).content).toBe(edited)
  const revisions = await (await page.request.get(firstUrl + '/history')).json()
  expect(revisions.some((row: { content: string }) => row.content === first.content)).toBeTruthy()
  expect(revisions.some((row: { content: string }) => row.content === firstBaseline)).toBeTruthy()
  await selectChapter(page, secondTitle)
  await expect(related.getByText('已过期 · 上一章正文有变动', { exact: true })).toBeVisible()
  await related.getByText('过期结果的依据', { exact: true }).click()
  await expect(related.getByText(firstCost, { exact: true })).toBeVisible()
  await expect(unrelated.getByText('已过期', { exact: false })).toHaveCount(0)
  await expect(unrelated).toContainText('符合要求')
  await expect(rule.getByText('原文依据待核对', { exact: true })).toBeVisible()
  await rule.getByText('查看设定原文', { exact: true }).click()
  await expect(rule.getByText(carvedRule, { exact: true })).toBeVisible()
  await rule.getByRole('button', { name: '定位来源原文', exact: true }).click()
  await expect(rule.locator('mark')).toHaveText(carvedRule)
  await expect(rule).toContainText(firstCost)
  await expect(rule).not.toContainText(editedCost)

  const recheckUrl = `${checkUrl}/${initialRelated.id}/recheck`
  const recheck = related.getByRole('button', { name: '复检此项', exact: true })
  await expect(recheck).toBeEnabled()
  await recheck.focus()
  await expect(recheck).toBeFocused()
  const recheckResponse = page.waitForResponse(
    (r) => r.url().endsWith(recheckUrl) && r.request().method() === 'POST',
  )
  await recheck.press('Enter')
  const submitted = await recheckResponse
  expect(submitted.status(), await submitted.text()).toBe(202)
  const recheckTask = await submitted.json()
  await expect
    .poll(async () => (await (await page.request.get(`/api/tasks/${recheckTask.id}`)).json()).status)
    .toBe('succeeded')
  await expect(related).toContainText('发现问题')
  if (!(await related.getByText(editedCost, { exact: true }).isVisible()))
    await related.getByText('查看校验依据', { exact: true }).click()
  await related.getByText(/^历次校验/).click()
  await related.getByText(/^修订 \d+ \/ 前章修订 \d+ · 符合要求$/).click()
  await expect(related.getByText(firstCost, { exact: true })).toBeVisible()
  await expect(related.getByText(editedCost, { exact: true })).toBeVisible()
  const final = await (await page.request.get(checkUrl)).json()
  const current = final.items.find((row: { condition: string }) => row.condition === lifespan)
  const untouched = final.items.find((row: { condition: string }) => row.condition === opening)
  expect(current.status).toBe('current')
  expect(current.latest.id).not.toBe(initialRelated.latest.id)
  expect(current.history).toContainEqual(initialRelated.latest)
  expect(untouched).toEqual(initialUnrelated)
  const secondSaved = await (await page.request.get(secondUrl)).json()
  expect(secondSaved.content).toBe(continued)
  expect(secondSaved.revision).toBe(secondBaseline.revision)
  const tasks = await (await page.request.get('/api/tasks')).json()
  expect(
    tasks.filter(
      (task: { kind: string; novel_id: string }) =>
        task.kind === 'chapter_item_check' && task.novel_id === novel.id,
    ),
  ).toHaveLength(3)

  await page.setViewportSize({ width: 320, height: 800 })
  await page.evaluate(() => {
    document.documentElement.style.fontSize = '200%'
  })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
  await page.screenshot({ path: info.outputPath('chapter-workbench-320-200.png'), fullPage: true })
  await selectChapter(page, firstTitle)
  await expect(editor.locator('.cm-line')).toHaveText(edited.split('\n'))
})

test('全自动工作批次的写作页显示该章写前冻结的 AI 章纲', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const created = await page.request.post('/api/novels', {
    headers,
    data: { title: '后台验证 · 写作页冻结章纲' },
  })
  expect(created.ok(), await created.text()).toBeTruthy()
  const novel = await created.json()
  const submitted = await page.request.post('/api/workflows', {
    headers,
    data: {
      novel_id: novel.id,
      source_branch_id: novel.branch_id,
      mode: 'automatic',
      brief: 'plan-preparation-e2e：只测量记录，疑点不提前解释。',
      chapters: 2,
      min_chars: 200,
      max_chars: 600,
      account: 'offline',
      adoption: 'automatic',
      completion_scope: 'segment',
      plot_contract_check: true,
    },
  })
  expect(submitted.status(), await submitted.text()).toBe(202)
  const started = await submitted.json()
  const taskUrl = `/api/tasks/${started.id}`
  await expect
    .poll(async () => (await (await page.request.get(taskUrl)).json()).status, { timeout: 30_000 })
    .toBe('segment_complete')
  const task = await (await page.request.get(taskUrl)).json()
  const chapters = await (await page.request.get(`/api/branches/${started.branch_id}/chapters`)).json()
  expect(chapters).toHaveLength(2)
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('combobox', { name: '工作版本', exact: true }).selectOption(started.branch_id)
  await page.getByRole('button', { name: '写作', exact: true }).click()
  for (const [index, chapter] of chapters.entries()) {
    await selectChapter(page, chapter.title)
    const outline = page.getByRole('button', { name: '章纲 · 冻结 AI 章纲', exact: true })
    await expect(outline).toBeVisible()
    if ((await outline.getAttribute('aria-expanded')) !== 'true') await outline.click()
    const preparation = task.result.plot_preparations[String(index + 1)]
    expect(preparation.status).toBe('ready')
    await expect(page.getByText(preparation.effective_plot, { exact: true })).toBeVisible()
  }
})
