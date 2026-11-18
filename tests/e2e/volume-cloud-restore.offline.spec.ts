import { expect, test, type APIResponse } from '@playwright/test'
import { createHash, randomUUID } from 'node:crypto'
import { readFile } from 'node:fs/promises'

const cloud = 'http://127.0.0.1:8888'
const provisioning = { Authorization: 'Bearer e2e-test-only-provisioning-token' }
const title = '整卷回滚跨端验收 · 铜铃'
const idea = '根据旧宅门内的铃声确认归还。'
const rule = '铜铃只能在旧宅门内响。'
const originalQuote = '师姐在门内认出铜铃'
const revisedQuote = '学徒把铜铃留在木盒里'
const originalText = `😀开门后，${originalQuote}，亲手接过铜铃。`
const revisedText = `😀夜里，${revisedQuote}，约定明早交还。`
const priorVolumeText = '前卷先确认钥匙还在桥下。'
const oldGoal = '门内当面交还铜铃'
const newGoal = '把铜铃暂存木盒'

test.skip(process.env.AINOVEL_SYNC_E2E !== '1', '使用独立浏览器、暂存 SQLite 和隔离同步服务')

async function okay(response: APIResponse) {
  expect(response.ok(), await response.text()).toBe(true)
  return response.json()
}

function hash(text: string) {
  return createHash('sha256').update(text).digest('hex')
}

test('桌面原位回滚末卷，独立 Web 中断续传后恢复正式稿及台账原文', async ({ page, browser, baseURL }) => {
  test.setTimeout(90_000)
  await page.goto('/')
  const { csrf } = await okay(await page.request.get('/api/session'))
  const headers = { 'X-Ainovel-CSRF': csrf }
  const create = async (path: string, data: unknown) => okay(await page.request.post(path, { headers, data }))
  const put = async (path: string, data: unknown) => okay(await page.request.put(path, { headers, data }))
  const read = async (path: string) => okay(await page.request.get(path))
  const novel = await create('/api/novels', { title })
  const branch = `/api/branches/${novel.branch_id}`
  const endpoint = `${cloud}/api/sync/works/${novel.branch_id}`
  const desktopDevice = 'volume-restore-desktop'
  const webDevice = 'volume-restore-web'
  const seed = await read(`${branch}/manuscript-package`)
  const first = await create(`${branch}/chapters`, { title: '桥下钥匙' })
  const second = await create(`${branch}/chapters`, { title: '铜铃归还' })

  function saveChapter(id: string, base_revision: number, content: string) {
    return put(`/api/chapters/${id}`, { base_revision, content })
  }

  async function grant(device_id: string): Promise<string> {
    return (
      await okay(
        await page.request.post(`${endpoint}/devices`, { headers: provisioning, data: { device_id } }),
      )
    ).token
  }

  async function checkpoint(token: string): Promise<{ ledger_id: string; cursor: number }> {
    return okay(
      await page.request.get(`${endpoint}/checkpoint`, { headers: { Authorization: `Bearer ${token}` } }),
    )
  }

  await saveChapter(first.id, 0, priorVolumeText)
  const opening = await saveChapter(second.id, 0, originalText)
  const project = await put(`${branch}/conception/project-brief`, {
    base_revision: 0,
    idempotency_key: randomUUID(),
    idea,
    brief: {
      position: '追查铜铃归属',
      track: '线索式悬疑',
      genre_ids: ['xianxia'],
      audience: '喜欢悬疑线索的读者',
      distinction: '以可核对的铃声规则推进',
      length: '两卷各一章',
      ending: '铜铃最终归还原主。',
    },
  })
  const conception = `${branch}/conception`
  const workspace = await read(conception)
  const world = workspace.categories.find(({ id }: { id: string }) => id === 'world')
  world.items = [
    {
      id: 'bell-rule',
      title: '门内铃声',
      detail: rule,
      adoption_state: 'adopted',
      setting_policy: { strength: 'hard', scope: '旧宅门内' },
    },
  ]
  const revisedSetting = await put(conception, {
    base_revision: workspace.revision,
    base_story_revision: workspace.story_revision,
    categories: workspace.categories,
  })
  const { confirmation: setting } = await create(conception + '/confirm', {
    base_revision: revisedSetting.revision,
    base_story_revision: revisedSetting.story_revision,
    idempotency_key: randomUUID(),
  })

  const storyEndpoint = `${branch}/story-records`
  const titleOfPromise = '向师姐归还铜铃'
  function promiseBody(revision: number, text: string, quote: string, note: string) {
    const start = text.indexOf(quote)
    expect(start).toBeGreaterThanOrEqual(2)
    return {
      kind: 'promise',
      title: titleOfPromise,
      notes: note,
      stages: [
        { status: 'planned' },
        {
          status: 'resolved',
          sources: [
            {
              chapter_id: second.id,
              revision,
              start,
              end: start + quote.length,
              quote,
            },
          ],
        },
      ],
    }
  }
  const initialGraph = await read(`${branch}/story-graph`)
  const recorded = await create(storyEndpoint, {
    base_story_revision: initialGraph.story_revision,
    record: promiseBody(opening.revision, originalText, originalQuote, '由师姐亲手接收。'),
  })
  const promise = recorded.records[0]

  const outlinePath = `${branch}/outline-workspace`
  async function confirmOutline(base_revision: number, goal: string) {
    const draft = await put(outlinePath, {
      base_revision,
      confirmation_id: setting.id,
      plan: {
        summary: '从桥下钥匙追到铜铃的归属。',
        volumes: [first, second].map((chapter, index) => ({
          id: `volume-${index + 1}`,
          title: index ? '归铃卷' : '寻钥卷',
          summary: '完成卷内线索。',
          units: [
            {
              id: `unit-${index + 1}`,
              title: '核对卷内依据',
              summary: '依据此前行动执行。',
              chapters: [
                {
                  chapter_id: chapter.id,
                  goal: index ? goal : '找到桥下钥匙',
                  obstacle: '夜里视线不足',
                  turn: '门外灯亮',
                  result: '线索可回查',
                  setting_ids: ['bell-rule'],
                },
              ],
            },
          ],
        })),
        promises: [
          {
            promise_id: promise.id,
            plant_chapter_id: second.id,
            opens_chapter_id: second.id,
            closes_chapter_id: second.id,
            fulfillment: 'author_required',
          },
        ],
      },
    })
    return (
      await create(outlinePath + '/confirm', {
        base_revision: draft.draft.revision,
        acknowledge_required_promises: true,
      })
    ).confirmed
  }
  const oldOutline = await confirmOutline(0, oldGoal)

  const inspections = `${branch}/workflow-inspections`
  async function inspect(scope: 'chapter' | 'volume', scope_id: string) {
    const { preview } = await read(`${inspections}?scope=${scope}&scope_id=${scope_id}`)
    return create(inspections, { scope, scope_id, basis_sha256: preview.basis_sha256 })
  }
  const originalReport = await inspect('volume', 'volume-2')

  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  const snapshots = page.getByRole('region', { name: '作品快照与回滚', exact: true })
  await snapshots.getByRole('combobox', { name: '快照范围' }).selectOption('volume')
  await snapshots.getByRole('combobox', { name: '快照所用卷' }).selectOption('volume-2')
  await snapshots.getByRole('textbox', { name: '快照名称' }).fill('归铃卷原稿')
  await snapshots.getByRole('button', { name: '保存快照' }).press('Enter')
  await expect(snapshots.getByRole('status')).toHaveText('已保存作品快照。')
  const snapshot = (await read(`${branch}/work-snapshots`)).find(
    ({ label }: { label: string }) => label === '归铃卷原稿',
  )
  expect(snapshot.scope_id).toBe('volume-2')

  const changed = await saveChapter(second.id, opening.revision, revisedText)
  const priorGraph = await read(`${branch}/story-graph`)
  await put(`${storyEndpoint}/${promise.id}`, {
    base_story_revision: priorGraph.story_revision,
    base_revision: promise.revision,
    record: promiseBody(changed.revision, revisedText, revisedQuote, '铜铃先留在木盒。'),
  })
  const changedOutline = await confirmOutline(oldOutline.revision, newGoal)
  const volumeReport = await inspect('volume', 'volume-2')
  const chapterReport = await inspect('chapter', second.id)
  const unaffected = await inspect('volume', 'volume-1')
  const previousPack = await read(`${branch}/manuscript-package`)

  const desktopToken = await grant(desktopDevice)
  const webToken = await grant(webDevice)
  const native = page.getByRole('region', { name: '作品云同步', exact: true })
  await native.getByLabel('同步服务地址').fill(cloud)
  await native.getByLabel('设备凭据').fill(desktopToken)
  await native.getByRole('button', { name: '启用作品同步', exact: true }).click()
  const nativeRun = native.getByRole('button', { name: '同步当前工作版本', exact: true })
  async function nativeSync() {
    await expect(nativeRun).toBeEnabled()
    const completed = page.waitForResponse(
      (response) => response.url().endsWith('/desktop-sync/run') && response.request().method() === 'POST',
    )
    await nativeRun.press('Enter')
    const state = await okay(await completed)
    expect(state.conflicts).toHaveLength(0)
    expect(state.deferred).toHaveLength(0)
    return state
  }
  await nativeSync()
  const baseline = await checkpoint(webToken)

  const isolated = await browser.newContext({ baseURL })
  try {
    const web = await isolated.newPage()
    await web.goto('/offline/')
    await web.getByLabel('导入新离线作品').setInputFiles({
      name: '仅作品标识.json',
      mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify(seed)),
    })
    const webRun = web.getByRole('button', { name: '同步当前工作版本', exact: true })
    const syncStatus = (cursor: number) =>
      web.getByText(`已启用 · 游标 ${cursor} · 0 条待发送或确认`, { exact: true })
    async function openConnection() {
      await web.getByRole('combobox', { name: '离线作品', exact: true }).selectOption(novel.id)
      await web.getByText('作品云同步', { exact: true }).click()
      await web.getByLabel('同步服务地址').fill(cloud)
      await web.getByLabel('设备编号').fill(webDevice)
      await web.getByLabel('设备凭据').fill(webToken)
    }
    await openConnection()
    await web.getByRole('button', { name: '启用作品同步', exact: true }).click()
    await webRun.click()
    await expect(syncStatus(baseline.cursor)).toBeVisible()
    const secondChapter = web.getByRole('button', { name: '2. 铜铃归还', exact: true })
    const editor = web.getByLabel('章节正文')
    await secondChapter.click()
    await expect(editor).toHaveValue(revisedText)

    await snapshots.getByRole('combobox', { name: '已保存快照' }).selectOption(snapshot.id)
    await snapshots.getByRole('button', { name: '预览恢复差异' }).press('Enter')
    const preview = snapshots.getByRole('region', { name: '恢复差异预览' })
    const affected = preview.getByRole('region', { name: '受影响报告' })
    await expect(affected).toContainText(volumeReport.id)
    await expect(affected).toContainText(chapterReport.id)
    await expect(affected).not.toContainText(unaffected.id)
    await preview.getByRole('checkbox', { name: '我已核对恢复影响' }).check()
    const restoring = page.waitForResponse(
      (response) =>
        response.url().endsWith(`${snapshot.id}/restore`) && response.request().method() === 'POST',
    )
    await preview.getByRole('button', { name: '提交恢复' }).press('Enter')
    const restored = await okay(await restoring)
    expect(restored.branch_id).toBe(novel.branch_id)
    expect(restored.recovery_snapshot_id).toBeTruthy()
    await expect(snapshots.getByRole('status')).toContainText('已恢复为新的当前修订')
    const restoredDetail = await read(`/api/chapters/${second.id}`)
    expect(restoredDetail.content).toBe(originalText)
    expect(restoredDetail.revision).toBeGreaterThan(changed.revision)
    for (const report of [volumeReport, chapterReport]) {
      const previous = await read(`${inspections}/${report.id}`)
      expect(previous.current).toBe(false)
      expect(
        previous.items.some(
          ({ current_status }: { current_status: string }) => current_status === 'outdated',
        ),
      ).toBe(true)
    }
    expect((await read(`${inspections}/${unaffected.id}`)).current).toBe(true)
    expect((await read(`${inspections}/${originalReport.id}`)).id).toBe(originalReport.id)
    await nativeSync()
    const published = await checkpoint(webToken)
    expect(published.ledger_id).toBe(baseline.ledger_id)
    expect(published.cursor).toBeGreaterThan(baseline.cursor + 1)

    const pulls: number[] = []
    let durable = 0
    await web.route(`${endpoint}/pull?*`, async (route) => {
      const url = new URL(route.request().url())
      pulls.push(Number(url.searchParams.get('after')))
      if (pulls.length === 1) {
        url.searchParams.set('limit', '1')
        const firstPage = await route.fetch({ url: url.toString() })
        expect(firstPage.ok(), await firstPage.text()).toBe(true)
        const data = await firstPage.json()
        expect(data.has_more).toBe(true)
        expect(data.changes).toHaveLength(1)
        expect(data.changes[0].object_id).toMatch(/^revision:/)
        durable = data.cursor
        await route.fulfill({ response: firstPage })
      } else if (pulls.length === 2) {
        await route.abort('connectionreset')
      } else {
        await route.continue()
      }
    })
    await webRun.press('Enter')
    await expect(web.getByRole('alert')).toBeVisible()
    expect(durable).toBe(baseline.cursor + 1)
    expect(pulls).toEqual([baseline.cursor, durable])
    await expect(editor).toHaveValue(revisedText)
    await web.reload()
    await openConnection()
    await expect(syncStatus(durable)).toBeVisible()
    await webRun.press('Enter')
    await expect(syncStatus(published.cursor)).toBeVisible()
    expect(pulls[2]).toBe(durable)
    await secondChapter.click()
    await expect(editor).toHaveValue(originalText)
    await web.getByRole('button', { name: '1. 桥下钥匙', exact: true }).click()
    await expect(editor).toHaveValue(priorVolumeText)

    const desktopAfter = await read(`${branch}/manuscript-package`)
    expect(desktopAfter.project_history.adopted_revision_id).toBe(project.id)
    expect(desktopAfter.settings_history.adopted_revision_id).toBe(setting.id)
    expect(desktopAfter.outline_history.adopted_revision_id).toBe(restored.outline_id)
    expect(restored.outline_id).not.toBe(changedOutline.id)
    expect(desktopAfter.story_history.adopted_heads[promise.id]).not.toBe(
      previousPack.story_history.adopted_heads[promise.id],
    )
    await isolated.setOffline(true)
    const projectPane = web.getByLabel('离线立项台')
    await projectPane.getByText('立项台', { exact: true }).click()
    await expect(projectPane.getByRole('textbox', { name: '创作思路', exact: true })).toHaveValue(idea)
    const outlines = web.getByLabel('离线大纲台')
    await outlines.getByText('大纲台', { exact: true }).click()
    await expect(outlines.getByText(`已确认修订 ${restored.outline_id}`, { exact: true })).toBeVisible()
    await outlines.getByText('大纲版本历史', { exact: true }).click()
    await outlines.getByRole('combobox', { name: '浏览正式大纲修订' }).selectOption(restored.outline_id)
    await expect(outlines.getByLabel('所选大纲修订')).toContainText(oldGoal)
    await expect(outlines.getByLabel('所选大纲修订')).not.toContainText(newGoal)
    const records = web.getByLabel('离线故事记录')
    await records.getByText('故事记录', { exact: true }).click()
    await records.getByRole('combobox', { name: '查看故事记录' }).selectOption(promise.id)
    await records.getByText('故事记录历史', { exact: true }).click()
    await expect(records.locator('pre')).toContainText('已回收')

    const archive = web.getByLabel('历次体检与完本交付')
    await archive.getByText('历次体检与完本交付', { exact: true }).click()
    await archive.getByLabel('选择历史报告').selectOption(volumeReport.id)
    await expect(archive.getByRole('list', { name: '十处对账' }).locator(':scope > li')).toHaveCount(10)
    await expect(archive.getByText(`源自同步设备 ${desktopDevice}`, { exact: false })).toBeVisible()
    await archive.getByRole('button', { name: /查看来源修订/ }).click()
    const cited = archive.getByRole('region', { name: '历史报告引用正文' })
    await expect(cited).toContainText(revisedText)
    await expect(cited).toContainText(hash(revisedText))
    await archive.getByLabel('选择历史报告').selectOption(originalReport.id)
    await archive.getByRole('button', { name: /查看来源修订/ }).click()
    await expect(cited).toContainText(originalText)
    await archive.getByLabel('选择历史报告').selectOption(unaffected.id)
    await archive.getByRole('button', { name: /查看来源修订/ }).click()
    await expect(cited).toContainText(priorVolumeText)

    const exported = web.waitForEvent('download')
    await web.getByRole('button', { name: '导出稿件交换包', exact: true }).click()
    const downloaded = await exported
    const webPack = JSON.parse(await readFile((await downloaded.path())!, 'utf8'))
    for (const key of ['project_history', 'settings_history', 'outline_history']) {
      expect(webPack[key].adopted_revision_id).toBe(desktopAfter[key].adopted_revision_id)
    }
    expect(
      webPack.settings_history.versions
        .find(({ id }: { id: string }) => id === setting.id)
        .categories.find(({ id }: { id: string }) => id === 'world').items[0].detail,
    ).toBe(rule)
    const finalStory = webPack.story_history.adopted_heads[promise.id]
    expect(finalStory).toBe(desktopAfter.story_history.adopted_heads[promise.id])
    const storyVersion = webPack.story_history.versions.find(({ id }: { id: string }) => id === finalStory)
    expect(storyVersion.record.stages.at(-1).status).toBe('resolved')
    expect(storyVersion.evidence.stages.at(-1)).toHaveLength(1)
    const proof = storyVersion.evidence.stages.at(-1)[0]
    const source = [...webPack.revisions, ...(webPack.source_revisions ?? [])].find(
      ({ id }: { id: string }) => id === proof.revision_id,
    )
    const desktopSource = desktopAfter.revisions.find(
      ({ chapter_id, revision }: { chapter_id: string; revision: number }) =>
        chapter_id === second.id && revision === restoredDetail.revision,
    )
    expect(source.id).toBe(desktopSource.id)
    expect(source.sha256).toBe(hash(originalText))
    expect(source.content.slice(proof.start, proof.end)).toBe(originalQuote)
    expect(proof.quote_sha256).toBe(hash(originalQuote))
    await expect(records.locator('pre')).toContainText(`修订 ${source.id} · 选区 ${proof.start}–${proof.end}`)
    expect(webPack.workflow_history.inspections.map(({ id }: { id: string }) => id)).toEqual(
      expect.arrayContaining([originalReport.id, volumeReport.id, chapterReport.id, unaffected.id]),
    )
  } finally {
    await isolated.close()
  }
})
