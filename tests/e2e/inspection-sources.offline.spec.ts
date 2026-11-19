import { expect, test, type APIResponse, type Page } from '@playwright/test'

async function checked(response: APIResponse) {
  expect(response.ok(), await response.text()).toBe(true)
  return response.json()
}

async function createWork(page: Page, title: string) {
  await page.goto('/')
  const { csrf } = await checked(await page.request.get('/api/session'))
  const headers = { 'X-Ainovel-CSRF': csrf }
  const { id, branch_id } = await checked(
    await page.request.post('/api/novels', { headers, data: { title } }),
  )
  const branch = `/api/branches/${branch_id}`
  async function save(id: string, base_revision: number, content: string) {
    return checked(
      await page.request.put(`/api/chapters/${id}`, { headers, data: { base_revision, content } }),
    )
  }
  async function chapter(title: string, content: string) {
    const row = await checked(await page.request.post(`${branch}/chapters`, { headers, data: { title } }))
    return save(row.id, row.revision, content)
  }
  async function openInspection() {
    await page.reload()
    await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(id)
    await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
    const panel = page.getByRole('region', { name: '十处对账体检', exact: true })
    await expect(panel.getByRole('button', { name: '生成体检单' })).toBeEnabled()
    return panel
  }
  return { id, branch_id, headers, branch, chapter, save, openInspection }
}

test('时点与披露证据不足保留未核查，改稿后旧场景和双侧原文仍可定位', async ({ page }) => {
  const work = await createWork(page, '河岸纸条 · 时点与视角复验')
  const early = '清晨，阿陶在河岸只见泥印。'
  const late = '夜里，来人说纸条藏在船舱。'
  const chapter = await work.chapter('晨间等候', `😀${early}\r\n${late}`)
  const graph = `${work.branch}/story-graph`
  async function addRecord(record: Record<string, unknown>) {
    const { story_revision } = await checked(await page.request.get(graph))
    const saved = await checked(
      await page.request.post(`${work.branch}/story-records`, {
        headers: work.headers,
        data: { base_story_revision: story_revision, record },
      }),
    )
    return saved.records.find(({ payload }: { payload: { title: string } }) => payload.title === record.title)
      .id as string
  }
  const sight = {
    chapter_id: chapter.id,
    revision: chapter.revision,
    start: 2,
    end: early.length + 2,
    quote: early,
  }
  const received = {
    ...sight,
    start: chapter.content.indexOf(late),
    end: chapter.content.length,
    quote: late,
  }
  const actor = await addRecord({ kind: 'character', title: '阿陶' })
  const anchor = await addRecord({
    kind: 'event',
    title: '夜里问到纸条',
    status: 'actual',
    story_time: '夜里',
    sources: [received],
  })
  await addRecord({
    kind: 'state',
    title: '纸条位置对阿陶公开',
    character_id: actor,
    event_id: anchor,
    category: 'knowledge',
    key: '纸条位置',
    value: '夜里听人说纸条藏在船舱。',
    audience: 'character',
    truth: 'actual',
    sources: [received],
  })
  await addRecord({
    kind: 'state',
    title: '披露计划尚待明确',
    character_id: actor,
    event_id: anchor,
    category: 'knowledge',
    key: '读者披露',
    value: '读者何时看到纸条尚待核定。',
    audience: 'reader',
    truth: 'unknown',
  })
  const { story_revision } = await checked(await page.request.get(graph))
  const scenes = await checked(
    await page.request.post(`${work.branch}/chapters/${chapter.id}/scene-times`, {
      headers: work.headers,
      data: {
        base_story_revision: story_revision,
        scene: {
          title: '晨间守候',
          source_revision: chapter.revision,
          start: sight.start,
          end: sight.end,
          story_time: '清晨',
          audience: 'character',
          character_id: actor,
        },
      },
    }),
  )
  expect(scenes.scenes[0].stale).toBe(false)
  const route = `${work.branch}/workflow-inspections`
  const panel = await work.openInspection()
  const previewLink = panel.getByText('本次依据与未核查项', { exact: true })
  await previewLink.press('Enter')
  const preview = previewLink.locator('..')
  const time = preview.getByRole('listitem').filter({ hasText: '04 故事时间、地点与事件因果' }).first()
  const viewpoint = preview.getByRole('listitem').filter({ hasText: '06 视角、人物所知与读者披露' }).first()
  await expect(time.getByText('未核查', { exact: true })).toBeVisible()
  await expect(viewpoint.getByText('未核查', { exact: true })).toBeVisible()
  await page.setViewportSize({ width: 320, height: 900 })
  await time.getByText('事件顺序与场景时点', { exact: true }).press('Enter')
  await expect(time.getByRole('list', { name: '冻结事件' })).toContainText('故事顺序未定 · 夜里')
  await expect(time.getByRole('list', { name: '冻结场景范围' })).toContainText('故事顺序未定 · 清晨')
  await viewpoint.getByText('场景视角与知情依据', { exact: true }).press('Enter')
  await expect(viewpoint.getByRole('list', { name: '冻结知情记录' })).toContainText(
    '读者所知 · 阿陶 · 待核实',
  )
  await expect(viewpoint.getByRole('list', { name: '冻结知情记录' })).toContainText(
    '获知依据 夜里问到纸条 · 故事顺序未定 · 夜里',
  )
  await expect(viewpoint.getByRole('list', { name: '冻结场景范围' })).toContainText('人物所知 · 阿陶')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  const generated = page.waitForResponse(
    (reply) => reply.request().method() === 'POST' && reply.url().endsWith(route),
  )
  await panel.getByRole('button', { name: '生成体检单' }).press('Enter')
  const previous = await checked(await generated)

  const revised = await work.save(chapter.id, chapter.revision, '😀阿陶在白天重新询问纸条去向。')
  await panel.getByRole('button', { name: '重新读取依据' }).press('Enter')
  await expect(panel.getByText('部分依据已更新，过期项待复检', { exact: true })).toBeVisible()
  if (!(await preview.evaluate((element: HTMLDetailsElement) => element.open)))
    await previewLink.press('Enter')
  await expect(time.getByText('过期', { exact: true })).toBeVisible()
  await expect(viewpoint.getByText('过期', { exact: true })).toBeVisible()
  await viewpoint.getByRole('button', { name: '核对 06', exact: true }).press('Enter')
  const verdicts = panel.getByRole('combobox', { name: '作者判定', exact: true })
  await expect(verdicts.getByRole('option', { name: '通过', exact: true })).toHaveCount(0)
  await expect(verdicts.getByRole('option', { name: '不适用', exact: true })).toHaveCount(0)
  const now = await checked(await page.request.get(route))
  expect(now.latest.id).toBe(previous.id)
  for (const check_id of ['04', '06']) {
    const refused = await page.request.post(route, {
      headers: work.headers,
      data: {
        basis_sha256: now.preview.basis_sha256,
        author_reviews: [
          {
            check_id,
            status: 'green',
            basis_note: '依据已经换稿。',
            reason: '试用当前正文绕过场景复核。',
            evidence: [
              {
                chapter_id: revised.id,
                revision: revised.revision,
                start: 0,
                end: revised.content.length,
                quote: revised.content,
              },
            ],
          },
        ],
      },
    })
    expect(refused.status()).toBe(409)
    expect((await refused.json()).code).toBe('inspection_story_outdated')
  }
  const results = panel.getByRole('region', { name: '体检单结果', exact: true })
  const history = results.getByRole('listitem').filter({ hasText: '06 视角、人物所知与读者披露' }).first()
  await expect(history.getByText('原判定 未核查', { exact: true })).toBeVisible()
  await history.getByText('查看体检原文', { exact: true }).press('Enter')
  await history.getByText(/^打开晨间等候原文 · 修订 1 · 位置 2–/).press('Enter')
  await history.getByText(new RegExp(`^打开晨间等候原文 · 修订 1 · 位置 ${received.start}–`)).press('Enter')
  await expect(history.locator('mark')).toHaveText([late, early])
})

test('跨章体检同时打开冲突双方的固定选区，改稿后历史与新单各读自己的原文', async ({ page }) => {
  const work = await createWork(page, '渡口 · 对账回源')
  const first = await work.chapter('东岸记时', '🐚日落时，学徒已从东岸登船。')
  const second = await work.chapter('西岸记时', '😀日落时，学徒仍在西岸。')
  const book = `${work.branch}/story-graph`
  const records = `${work.branch}/story-records`
  const citation = (chapter: typeof first) => ({
    chapter_id: chapter.id,
    revision: chapter.revision,
    start: 2,
    end: chapter.content.length,
    quote: chapter.content.slice(2),
  })
  const record = {
    kind: 'event',
    title: '登船时点',
    status: 'conflict',
    sources: [citation(first), citation(second)],
  }
  const { story_revision } = await checked(await page.request.get(book))
  const saved = await checked(
    await page.request.post(records, {
      headers: work.headers,
      data: { base_story_revision: story_revision, record },
    }),
  )
  await page.setViewportSize({ width: 320, height: 900 })
  const panel = await work.openInspection()
  async function sign() {
    const response = page.waitForResponse(
      (reply) => reply.request().method() === 'POST' && reply.url().endsWith('/workflow-inspections'),
    )
    await panel.getByRole('button', { name: '生成体检单' }).press('Enter')
    return checked(await response)
  }
  const past = await sign()
  const otherWork = await checked(
    await page.request.post('/api/novels', { headers: work.headers, data: { title: '不相关的工作版本' } }),
  )
  const inaccessible = await page.request.get(
    `/api/branches/${otherWork.branch_id}/workflow-inspections/${past.id}/source-context/04?source_index=0`,
  )
  expect(inaccessible.status()).toBe(404)
  const result = panel.getByRole('region', { name: '体检单结果', exact: true })
  const eventCheck = result.getByRole('listitem').filter({ hasText: '04 故事时间、地点与事件因果' }).first()
  async function openPair(east: string, west: string) {
    await eventCheck.getByText('查看体检原文', { exact: true }).press('Enter')
    await eventCheck.getByText(/^打开东岸记时原文/).press('Enter')
    await eventCheck.getByText(/^打开西岸记时原文/).press('Enter')
    await expect(eventCheck.getByRole('region', { name: /^东岸记时原文/ }).locator('mark')).toHaveText(east)
    await expect(eventCheck.getByRole('region', { name: /^西岸记时原文/ }).locator('mark')).toHaveText(west)
  }
  await expect(eventCheck.getByText('阻断', { exact: true })).toBeVisible()
  await openPair(first.content.slice(2), second.content.slice(2))
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)

  const edited = await work.save(second.id, second.revision, '😀日落后，学徒随船抵达东岸。')
  const graph = await checked(await page.request.get(book))
  const target = saved.records[0]
  await checked(
    await page.request.put(`${records}/${target.id}`, {
      headers: work.headers,
      data: {
        base_story_revision: graph.story_revision,
        base_revision: target.revision,
        record: { ...record, status: 'actual', sources: [citation(first), citation(edited)] },
      },
    }),
  )
  await panel.getByRole('button', { name: '重新读取依据' }).click()
  const latest = await sign()
  expect(latest.id).not.toBe(past.id)
  await openPair(first.content.slice(2), edited.content.slice(2))
  await panel.getByRole('combobox', { name: '选择体检单', exact: true }).selectOption(past.id)
  await expect(eventCheck.getByText('原判定 阻断')).toBeVisible()
  await openPair(first.content.slice(2), second.content.slice(2))
})

test('全书体检汇总当前章级红项并可用键盘回到原报告的选区', async ({ page }) => {
  const work = await createWork(page, '铜铃 · 章级红项')
  const original = '😀渡口的学徒还未见到守铃人。'
  const chapter = await work.chapter('渡口见面', original)
  const route = `${work.branch}/workflow-inspections`
  const scope = { scope: 'chapter', scope_id: chapter.id }
  const { preview } = await checked(await page.request.get(route, { params: scope }))
  const { id } = await checked(
    await page.request.post(route, {
      headers: work.headers,
      data: {
        ...scope,
        basis_sha256: preview.basis_sha256,
        author_reviews: [
          {
            check_id: '03',
            status: 'red',
            basis_note: '按已确认的人物出场关系核对。',
            reason: '这一段与本章已确定的相见时点冲突。',
            evidence: [
              {
                chapter_id: chapter.id,
                revision: chapter.revision,
                start: 2,
                end: original.length,
                quote: original.slice(2),
              },
            ],
          },
        ],
      },
    }),
  )
  await page.setViewportSize({ width: 320, height: 900 })
  const panel = await work.openInspection()
  await panel.getByText('本次依据与未核查项', { exact: true }).press('Enter')
  const sources = panel.getByRole('list', { name: '章卷红项来源', exact: true })
  await expect(sources).toContainText(id)
  await expect(sources).toContainText('渡口见面')
  await sources.getByText('查看体检原文', { exact: true }).press('Enter')
  await sources.getByText(/^打开渡口见面原文/).press('Enter')
  await expect(sources.getByRole('region', { name: /^渡口见面原文/ }).locator('mark')).toHaveText(
    original.slice(2),
  )
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)
})

test('M6-03 已知人物状态冲突拦住普通签认，桌面与离线历史均回读双方原文', async ({ page, context }) => {
  const work = await createWork(page, '拾铃学徒 · 相同时点的伤势')
  const east = await work.chapter('桥东', '😀学徒的左手骨折。')
  const west = await work.chapter('桥西', '🐚学徒的左手完好。')
  const citation = (chapter: typeof east) => ({
    chapter_id: chapter.id,
    revision: chapter.revision,
    start: 2,
    end: chapter.content.length,
    quote: chapter.content.slice(2),
  })
  async function record(payload: Record<string, unknown>) {
    const { story_revision } = await checked(await page.request.get(`${work.branch}/story-graph`))
    const result = await checked(
      await page.request.post(`${work.branch}/story-records`, {
        headers: work.headers,
        data: { base_story_revision: story_revision, record: payload },
      }),
    )
    return result.records.find(
      ({ payload: row }: { payload: { title: string } }) => row.title === payload.title,
    )
  }
  const actor = await record({ kind: 'character', title: '拾铃学徒' })
  const moment = await record({
    kind: 'event',
    title: '铃响',
    status: 'actual',
    story_order: 1,
    participants: [actor.id],
    sources: [citation(east)],
  })
  const condition = {
    kind: 'state',
    character_id: actor.id,
    event_id: moment.id,
    category: 'injury',
    key: '左手伤势',
    audience: 'author',
    truth: 'actual',
  }
  const injured = await record({ ...condition, title: '左手骨折', value: '骨折', sources: [citation(east)] })
  const uninjured = await record({
    ...condition,
    title: '左手完好',
    value: '完好',
    sources: [citation(west)],
  })

  await page.setViewportSize({ width: 320, height: 900 })
  const panel = await work.openInspection()
  await panel.getByText('本次依据与未核查项', { exact: true }).press('Enter')
  await panel.getByRole('button', { name: '核对 03', exact: true }).press('Enter')
  const decisions = panel.getByRole('combobox', { name: '作者判定', exact: true })
  await expect(decisions).toBeVisible()
  await expect(decisions.locator('option[value=green]')).toHaveCount(0)
  await expect(decisions.locator('option[value=yellow]')).toHaveCount(0)
  await expect(decisions.locator('option[value=red]')).toHaveCount(1)

  const recorded = page.waitForResponse(
    (response) => response.request().method() === 'POST' && response.url().endsWith('/workflow-inspections'),
  )
  await panel.getByRole('button', { name: '生成体检单', exact: true }).press('Enter')
  const report = await checked(await recorded)
  const result = panel.getByRole('region', { name: '体检单结果', exact: true })
  const conflictCheck = result.getByRole('listitem').filter({ hasText: '03 人物身份、关系与状态' }).first()
  await expect(conflictCheck.getByText('阻断', { exact: true })).toBeVisible()
  const conflictName = '左手伤势 · 作者所知 · 故事时点 1'
  const sides = conflictCheck.getByRole('list', { name: '已确定的人物状态冲突', exact: true })
  await sides.getByText(conflictName, { exact: true }).press('Enter')
  await expect(sides).toContainText(injured.id)
  await expect(sides).toContainText(uninjured.id)
  await conflictCheck.getByText('查看体检原文', { exact: true }).press('Enter')
  for (const chapter of [east, west]) {
    await conflictCheck.getByText(new RegExp(`^打开${chapter.title}原文`)).press('Enter')
    await expect(
      conflictCheck.getByRole('region', { name: new RegExp(`^${chapter.title}原文`) }).locator('mark'),
    ).toHaveText(citation(chapter).quote)
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)

  const pack = await page.request.get(`${work.branch}/manuscript-package`)
  expect(pack.ok(), await pack.text()).toBe(true)
  const buffer = await pack.body()
  const altered = JSON.parse(buffer.toString())
  const alteredReport = altered.workflow_history.inspections.find(
    ({ id }: { id: string }) => id === report.id,
  )
  alteredReport.report.items.find(({ check_id }: { check_id: string }) => check_id === '03').status = 'green'

  await page.goto('/offline/')
  await expect(page.getByText('离线页面就绪', { exact: true })).toBeVisible()
  await context.setOffline(true)
  const input = page.getByLabel('导入新离线作品')
  await input.setInputFiles({
    name: '改写冲突报告.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(altered)),
  })
  await expect(page.getByRole('alert')).toContainText('有确定的人物状态冲突时，第 03 项须保留红项。')
  await expect(page.getByText('历次体检与完本交付', { exact: true })).toHaveCount(0)
  await input.setInputFiles({ name: '伤势冲突.json', mimeType: 'application/json', buffer })
  await page.getByText('历次体检与完本交付', { exact: true }).press('Enter')
  const history = page.getByRole('group', { name: '历次体检与完本交付', exact: true })
  await history.getByText(/人物身份、关系与状态.*红/).press('Enter')
  await history.getByText(conflictName, { exact: true }).press('Enter')
  const original = history.getByRole('region', { name: '历史报告引用正文', exact: true })
  await history.getByRole('button', { name: /^查看左手骨折 · 原文/ }).press('Enter')
  await expect(original.locator('mark')).toHaveText(citation(east).quote)
  await history.getByRole('button', { name: /^查看左手完好 · 原文/ }).press('Enter')
  await expect(original.locator('mark')).toHaveText(citation(west).quote)
})

test('全书逐条签认 31 章的软偏离，独立 Web 可验证并读回签认', async ({ page, context }) => {
  const work = await createWork(page, '遗铃 · 软设定签认')
  const chapters: { id: string; revision: number }[] = []
  for (let index = 1; index <= 31; index++) {
    chapters.push(await work.chapter(`拾铃 ${index}`, `😀学徒第${index}次拾起铜铃。\n铜铃在门外响了。`))
  }
  const route = `${work.branch}/conception`
  const state = await checked(await page.request.get(route))
  const world = state.categories.find(({ id }: { id: string }) => id === 'world')
  world.items = [
    {
      id: 'bell',
      title: '铃声规则',
      detail: '铜铃只在门内响。',
      setting_policy: { strength: 'soft', scope: '平时', exceptions: '' },
    },
  ]
  const saved = await checked(
    await page.request.put(route, {
      headers: work.headers,
      data: {
        base_revision: state.revision,
        base_story_revision: state.story_revision,
        categories: state.categories,
      },
    }),
  )
  const adopted = await checked(
    await page.request.post(`${route}/confirm`, {
      headers: work.headers,
      data: {
        base_revision: saved.revision,
        base_story_revision: saved.story_revision,
        idempotency_key: 'confirm-soft-bell',
        note: '按可调整设定采用。',
      },
    }),
  )
  const tasks: string[] = []
  for (const current of chapters) {
    const run = await checked(
      await page.request.post('/api/modules', {
        headers: work.headers,
        data: {
          novel_id: work.id,
          branch_id: work.branch_id,
          chapter_id: current.id,
          base_revision: current.revision,
          account: 'offline',
          action: 'review.chapter',
          instruction: '单条设定对账',
          conception_snapshot_id: adopted.confirmation.id,
          setting_item_id: 'bell',
        },
      }),
    )
    tasks.push(run.id)
  }
  await expect
    .poll(async () => {
      const run = await checked(await page.request.get(`/api/tasks/${tasks.at(-1)}`))
      return run.status
    })
    .toBe('succeeded')
  for (const task of tasks)
    expect((await checked(await page.request.get(`/api/tasks/${task}`))).status).toBe('succeeded')
  const panel = await work.openInspection()
  await panel.getByText('本次依据与未核查项', { exact: true }).click()
  const initial = panel.getByRole('listitem').filter({ hasText: '05 设定规则、数值与资源' }).first()
  await expect(initial.getByText('待处理', { exact: true })).toBeVisible()
  await panel.getByRole('button', { name: '核对 05', exact: true }).press('Enter')
  await panel.getByRole('combobox', { name: '作者判定', exact: true }).selectOption('green')
  const witness = panel.getByRole('checkbox', { name: '核对并引用「铃声规则」的偏离', exact: true })
  await expect(witness).toHaveCount(31)
  for (const entry of await witness.all()) {
    await expect(entry).not.toBeChecked()
    await entry.press('Space')
  }
  await panel.getByLabel('对照的章纲、约定或规则', { exact: true }).fill('铃声规则为软设定。')
  await panel
    .getByLabel('判定理由', { exact: true })
    .fill('作者核对后保留本段，门外响铃用于建立本章认知反差。')
  const received = page.waitForResponse(
    (response) => response.request().method() === 'POST' && response.url().endsWith('/workflow-inspections'),
  )
  await panel.getByRole('button', { name: '生成体检单', exact: true }).press('Enter')
  const report = await checked(await received)
  expect(report.items[4].status).toBe('green')
  expect(report.items[4].author_review.soft_deviation_report_ids).toHaveLength(31)
  expect(
    report.items[4].author_review.evidence.map(({ chapter_id }: { chapter_id: string }) => chapter_id),
  ).toEqual(chapters.map(({ id }) => id))
  const result = panel.getByRole('region', { name: '体检单结果', exact: true })
  await expect(result.getByText('已签认 31 条软设定偏离。')).toBeVisible()

  const pack = await page.request.get(`${work.branch}/manuscript-package`)
  expect(pack.ok(), await pack.text()).toBe(true)
  const buffer = await pack.body()
  await page.goto('/offline/')
  await expect(page.getByText('离线页面就绪', { exact: true })).toBeVisible()
  await context.setOffline(true)
  const input = page.getByLabel('导入新离线作品')
  const altered = JSON.parse(buffer.toString())
  altered.workflow_history.inspections.find(
    ({ id }: { id: string }) => id === report.id,
  ).report.items[4].status = 'yellow'
  await input.setInputFiles({
    name: '改写判定的稿件包.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(altered)),
  })
  await expect(page.getByRole('alert')).toContainText('软设定签认与原检查项不一致')
  await input.setInputFiles({
    name: '软设定签认.json',
    mimeType: 'application/json',
    buffer,
  })
  await page.getByText('历次体检与完本交付', { exact: true }).click()
  const archive = page.getByRole('group', { name: '历次体检与完本交付', exact: true })
  await expect(archive.getByText(/设定规则、数值与资源.*绿/)).toBeVisible()
})
