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
