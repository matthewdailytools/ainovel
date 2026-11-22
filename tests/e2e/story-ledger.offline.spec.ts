import { expect, test, type APIResponse, type Page } from '@playwright/test'

const clue = '🧭阿阮从匣里摸出半片铜叶。'
const rule = '夜渡只认青灯，一盏只能带一人。'
const revisedRule = '夜渡只用青灯引路，每盏限一人同行。'
const progress = '铜叶背面的刻痕恰与堤石吻合。'
const revisedProgress = '铜叶背面的刻痕只与东侧堤石吻合。'
const payoff = '守堤人拼齐铜叶，找到了缺失的水道图。'
const revisedPayoff = '阿阮将新找到的铜叶拼上去，认出了缺失的水道图。'
const settingTitle = '青灯渡规'
const promiseTitle = '半片铜叶的来历'
const unplannedTitle = '未定期的船号'

async function checked(response: APIResponse) {
  expect(response.ok(), await response.text()).toBeTruthy()
  return response.json()
}

async function fixture(page: Page) {
  await page.goto('/')
  const session = await checked(await page.request.get('/api/session'))
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await checked(
    await page.request.post('/api/novels', {
      headers,
      data: { title: '后台验证 · 设定与伏笔跨章台账' },
    }),
  )
  const branch = `/api/branches/${novel.branch_id}`
  const chapters = []
  for (const [title, content] of [
    ['铜叶与青灯', `${clue}\n${rule}`],
    ['堤石上的刻痕', progress],
    ['水道图失而复得', payoff],
    ['收灯前的发现', ''],
  ]) {
    const chapter = await checked(await page.request.post(branch + '/chapters', { headers, data: { title } }))
    const url = `/api/chapters/${chapter.id}`
    const saved = await checked(await page.request.put(url, { headers, data: { base_revision: 0, content } }))
    chapters.push({ id: chapter.id as string, title, content, revision: saved.revision as number, url })
  }
  const workspace = await checked(await page.request.get(branch + '/conception'))
  await checked(
    await page.request.put(branch + '/conception', {
      headers,
      data: {
        base_revision: workspace.revision,
        base_story_revision: workspace.story_revision,
        categories: workspace.categories.map((category: { id: string; items: unknown[] }) =>
          category.id === 'world'
            ? {
                ...category,
                items: [
                  ...category.items,
                  { id: 'night-ferry', title: settingTitle, detail: rule, links: [] },
                ],
              }
            : category,
        ),
      },
    }),
  )
  const graph = await checked(await page.request.get(branch + '/story-graph'))
  await checked(
    await page.request.post(branch + '/story-records', {
      headers,
      data: {
        base_story_revision: graph.story_revision,
        record: { kind: 'promise', title: unplannedTitle, stages: [{ status: 'planned' }] },
      },
    }),
  )
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  return { novel, branch, chapters, headers }
}

async function openChapter(page: Page, title: string) {
  await page.locator('.chapter-list button').filter({ hasText: title }).click()
  await expect(page.getByRole('textbox', { name: '章节标题', exact: true })).toHaveValue(title)
}

test('跨章台账：计划窗口、设定生效、伏笔全过程和单项复检', async ({ page }, info) => {
  const { novel, branch, chapters, headers } = await fixture(page)
  const [first, second, third, fourth] = chapters
  const url = branch + '/continuity-ledger'
  const ledger = page.getByRole('region', { name: '设定与伏笔台账', exact: true })
  const today = page.getByRole('region', { name: '今日条', exact: true })
  const editor = page.getByRole('textbox', { name: '章节正文', exact: true })
  const chapterTitle = page.getByRole('textbox', { name: '章节标题', exact: true })
  const readLedger = async () => checked(await page.request.get(url))
  const entry = (name: string) => ledger.getByRole('article', { name, exact: true })
  const stage = (name: string, action: string) =>
    entry(name).getByRole('region', { name: action, exact: true })
  const button = (name: string) => ledger.getByRole('button', { name, exact: true })
  await expect(entry(unplannedTitle)).toBeVisible()
  await expect(today).toHaveCount(0)

  async function ensureEntryOpen(title: string) {
    const details = entry(title).locator(':scope > details')
    await expect(details).toBeVisible()
    if (!(await details.evaluate((node: HTMLDetailsElement) => node.open)))
      await details.locator(':scope > summary').click()
  }

  async function create(kind: '设定' | '伏笔', title: string) {
    await button(`新建${kind}计划`).click()
    await ledger.getByRole('textbox', { name: '计划名称', exact: true }).fill(title)
    if (kind === '设定') {
      await ledger
        .getByRole('combobox', { name: '采用的设定', exact: true })
        .selectOption({ label: settingTitle })
    }
    await button('保存计划').click()
    await ensureEntryOpen(title)
  }

  async function setWindow(name: string, action: string, chapter: (typeof chapters)[number]) {
    await ensureEntryOpen(name)
    const row = stage(name, action)
    await row.getByRole('button', { name: '调整计划窗口', exact: true }).click()
    await row.getByRole('combobox', { name: '窗口起章', exact: true }).selectOption(chapter.id)
    await row.getByRole('combobox', { name: '窗口止章', exact: true }).selectOption(chapter.id)
    await row.getByRole('button', { name: '保存计划窗口', exact: true }).click()
    await expect(row.getByRole('combobox', { name: '窗口止章', exact: true })).toHaveCount(0)
  }

  async function record(name: string, action: string, evidence: string, lastLine = false, recheck = false) {
    await ensureEntryOpen(name)
    await editor.press(lastLine ? 'ControlOrMeta+End' : 'ControlOrMeta+Home')
    await editor.press(lastLine ? 'Shift+Home' : 'Shift+End')
    const row = stage(name, action)
    await row.getByRole('button', { name: recheck ? '用当前选区复检' : '记录正文选区', exact: true }).click()
    await expect(
      row.getByRole('group', { name: '正文证据' }).getByText(evidence, { exact: true }),
    ).toBeVisible()
  }

  async function save(content: string, chapter: (typeof chapters)[number]) {
    await editor.fill(content)
    await page.getByRole('button', { name: '保存', exact: true }).click()
    await expect.poll(async () => (await checked(await page.request.get(chapter.url))).content).toBe(content)
  }

  await openChapter(page, first.title)
  const settingPlanTitle = '青灯规则首次生效'
  await create('设定', settingPlanTitle)
  await setWindow(settingPlanTitle, '生效', first)
  await expect(stage(settingPlanTitle, '生效')).toContainText('未登记正文证据')
  await expect(today.getByRole('button', { name: /青灯规则首次生效.*生效已过窗口.*第 1 章/ })).toBeVisible()
  await record(settingPlanTitle, '生效', rule, true)
  await expect(today.getByRole('button', { name: /青灯规则首次生效/ })).toHaveCount(0)

  await create('伏笔', promiseTitle)
  const promise = entry(promiseTitle)
  await setWindow(promiseTitle, '回收', third)
  await expect(today.getByRole('button', { name: /半片铜叶的来历.*回收到期.*第 3 章/ })).toBeVisible()
  await expect(today.getByRole('button', { name: /未定期的船号/ })).toHaveCount(0)
  await promise.getByRole('button', { name: '编辑关联章节', exact: true }).click()
  await promise.getByRole('checkbox', { name: `第 2 章 ${second.title}`, exact: true }).check()
  await promise.getByRole('checkbox', { name: `第 3 章 ${third.title}`, exact: true }).check()
  await promise.getByRole('button', { name: '保存关联章节', exact: true }).click()
  await expect(promise.getByRole('button', { name: '保存关联章节', exact: true })).toHaveCount(0)
  await record(promiseTitle, '埋设', clue)
  await openChapter(page, second.title)
  await record(promiseTitle, '推进', progress)
  const due = today.getByRole('button', { name: /半片铜叶的来历.*回收到期.*第 3 章/ })
  await expect(due).toBeEnabled()
  await due.focus()
  await due.press('Enter')
  await expect(chapterTitle).toHaveValue(third.title)
  await expect(stage(promiseTitle, '回收').getByRole('heading', { name: '回收', exact: true })).toBeFocused()
  await record(promiseTitle, '回收', payoff)
  const before = await readLedger()
  const promiseBefore = before.entries.find((item: { title: string }) => item.title === promiseTitle)
  const settingBefore = before.entries.find((item: { title: string }) => item.title === settingPlanTitle)
  expect(
    promiseBefore.stages.find((item: { stage: string }) => item.stage === 'planted').source,
  ).toMatchObject({
    chapter_id: first.id,
    quote: clue,
    start: 0,
    end: clue.length,
  })
  expect(
    promiseBefore.stages.every((item: { review_status: string }) => item.review_status === 'current'),
  ).toBeTruthy()
  expect(before.reminders).toEqual([])

  await openChapter(page, second.title)
  await save(revisedProgress, second)
  await expect(today.getByRole('button', { name: /半片铜叶的来历.*推进待复检.*第 2 章/ })).toBeVisible()
  await expect(today.getByRole('button', { name: /青灯规则首次生效|未定期的船号/ })).toHaveCount(0)
  const changed = await readLedger()
  const changedPromise = changed.entries.find((item: { id: string }) => item.id === promiseBefore.id)
  expect(changed.reminders).toHaveLength(1)
  expect(changedPromise.impacted_chapters.map((chapter: { id: string }) => chapter.id)).toEqual([
    second.id,
    third.id,
  ])
  expect(changedPromise.stages.map((item: { review_status: string }) => item.review_status)).toEqual([
    'current',
    'stale',
    'current',
  ])
  expect(changed.entries.find((item: { id: string }) => item.id === settingBefore.id)).toEqual(settingBefore)

  await ensureEntryOpen(promiseTitle)
  await promise.getByRole('button', { name: `查看第 3 章 ${third.title}`, exact: true }).click()
  await expect(chapterTitle).toHaveValue(third.title)
  await today.getByRole('button', { name: /半片铜叶的来历.*推进待复检.*第 2 章/ }).click()
  await expect(chapterTitle).toHaveValue(second.title)
  await expect(stage(promiseTitle, '推进').getByRole('heading', { name: '推进', exact: true })).toBeFocused()
  await record(promiseTitle, '推进', revisedProgress, false, true)
  await expect(today).toHaveCount(0)

  await setWindow(promiseTitle, '回收', fourth)
  await expect(stage(promiseTitle, '回收')).toContainText('尚未进入窗口')
  await expect(today.getByRole('button', { name: /回收到期.*第 3 章/ })).toHaveCount(0)
  const rescheduled = await readLedger()
  const currentPromise = rescheduled.entries.find((item: { id: string }) => item.id === promiseBefore.id)
  const oldPayoff = currentPromise.stages.find((item: { stage: string }) => item.stage === 'resolved')
  expect(oldPayoff.review_status).toBe('outside_window')
  expect(oldPayoff.source.quote).toBe(payoff)
  const rejected = await page.request.put(`${url}/entries/${currentPromise.id}/evidence`, {
    headers,
    data: {
      base_revision: currentPromise.revision,
      stage: 'resolved',
      source: oldPayoff.source,
      note: '仍在旧窗口。',
    },
  })
  expect(rejected.status(), await rejected.text()).toBe(409)
  expect((await checked(await page.request.get(third.url))).content).toBe(payoff)
  expect((await readLedger()).entries.find((item: { id: string }) => item.id === promiseBefore.id)).toEqual(
    currentPromise,
  )
  await openChapter(page, fourth.title)
  await save(revisedPayoff, fourth)
  await ensureEntryOpen(promiseTitle)
  await expect(stage(promiseTitle, '回收')).toContainText('本章到期')
  const inNewWindow = await readLedger()
  expect(
    inNewWindow.reminders.some(
      (item: { stage?: string; chapter_id: string }) =>
        item.stage === 'resolved' && item.chapter_id === fourth.id,
    ),
  ).toBeTruthy()
  await record(promiseTitle, '回收', revisedPayoff, false, true)
  await expect(today).toHaveCount(0)
  await promise.getByRole('button', { name: '查看台账历史', exact: true }).click()
  const archive = promise.getByRole('region', { name: '台账历史', exact: true })
  await archive.getByText(`台账修订 ${promiseBefore.revision}`, { exact: true }).click()
  await expect(archive.getByText(payoff, { exact: true }).filter({ visible: true })).toBeVisible()
  await expect(archive.getByText(progress, { exact: true }).filter({ visible: true })).toBeVisible()

  await stage(promiseTitle, '推进').getByRole('button', { name: '定位正文原文', exact: true }).click()
  await expect(chapterTitle).toHaveValue(second.title)
  await expect
    .poll(async () =>
      editor.evaluate((element) => {
        const selection = window.getSelection()
        return selection?.anchorNode && element.contains(selection.anchorNode) ? selection.toString() : ''
      }),
    )
    .toBe(revisedProgress)

  const after = await readLedger()
  const verified = after.entries.find((item: { id: string }) => item.id === promiseBefore.id)
  const newPayoff = verified.stages.find((item: { stage: string }) => item.stage === 'resolved')
  expect(newPayoff.review_status).toBe('current')
  expect(newPayoff.source).toMatchObject({
    chapter_id: fourth.id,
    quote: revisedPayoff,
    start: 0,
    end: revisedPayoff.length,
  })
  expect(newPayoff.window).toEqual({ start_chapter_id: fourth.id, end_chapter_id: fourth.id })
  expect((await checked(await page.request.get(second.url))).content).toBe(revisedProgress)
  expect((await checked(await page.request.get(fourth.url))).content).toBe(revisedPayoff)

  await openChapter(page, first.title)
  await save(`${clue}\n${revisedRule}`, first)
  const effectiveReminder = today.getByRole('button', { name: /青灯规则首次生效.*生效待复检.*第 1 章/ })
  await expect(effectiveReminder).toBeVisible()
  await effectiveReminder.click()
  await expect(stage(settingPlanTitle, '生效')).toBeVisible()
  await record(settingPlanTitle, '生效', revisedRule, true, true)
  await expect(effectiveReminder).toHaveCount(0)
  await expect(today.getByRole('button', { name: /半片铜叶的来历.*埋设待复检.*第 1 章/ })).toBeVisible()
  const afterRule = await readLedger()
  const verifiedSetting = afterRule.entries.find((item: { id: string }) => item.id === settingBefore.id)
  expect(verifiedSetting.stages[0].review_status).toBe('current')
  expect(verifiedSetting.stages[0].source.quote).toBe(revisedRule)
  expect(
    afterRule.entries.find((item: { id: string }) => item.id === promiseBefore.id).stages.slice(1),
  ).toEqual(verified.stages.slice(1))

  const tasks = await checked(await page.request.get('/api/tasks'))
  expect(tasks.filter((task: { novel_id: string }) => task.novel_id === novel.id)).toHaveLength(0)

  await page.setViewportSize({ width: 320, height: 800 })
  await page.evaluate(() => {
    document.documentElement.style.fontSize = '200%'
  })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
  await page.screenshot({ path: info.outputPath('story-ledger-320-200.png'), fullPage: true })
})
