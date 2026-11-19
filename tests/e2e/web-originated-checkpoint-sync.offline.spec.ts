import { expect, test, type APIResponse } from '@playwright/test'
import { createHash, randomUUID } from 'node:crypto'
import { readFile } from 'node:fs/promises'

const cloud = process.env.AINOVEL_TEST_SYNC_URL ?? 'http://127.0.0.1:8888'
const provisioning = { Authorization: 'Bearer e2e-test-only-provisioning-token' }
const oldQuote = '老钟在雾散后响了三次'
const newQuote = '潮声盖过了未落下的钟锤'
const oldText = `😀潮退时，${oldQuote}。\n阿楠因此知道船已返港。`
const laterText = `😀入夜后，${newQuote}。\n阿楠决定再查一次港口。`
const outsideText = '作者后来补记了第一卷的航线与出港时刻。'
const restoredGoal = '从钟鸣确认旧船已返港'
const laterGoal = '换查港口回来的那艘船'
const outsideGoal = '保留新核对的航线'
const hash = (value: string) => createHash('sha256').update(value, 'utf8').digest('hex')

test.skip(process.env.AINOVEL_SYNC_E2E !== '1', '使用独立 IndexedDB、SQLite 和回环同步服务')

async function okay(response: Pick<APIResponse, 'ok' | 'text' | 'json'>) {
  expect(response.ok(), await response.text()).toBe(true)
  return response.json()
}

test('独立 Web 断网恢复导入的卷点，联网发布后桌面采用整组正式修订', async ({ page, browser, baseURL }) => {
  test.setTimeout(90_000)
  await page.goto('/')
  const { csrf } = await okay(await page.request.get('/api/session'))
  const headers = { 'X-Ainovel-CSRF': csrf }
  const read = async (path: string) => okay(await page.request.get(path))
  const post = async (path: string, data: unknown) => okay(await page.request.post(path, { headers, data }))
  const put = async (path: string, data: unknown) => okay(await page.request.put(path, { headers, data }))
  const novel = await post('/api/novels', { title: '雾钟 · 独立 Web 卷点恢复' })
  const branch = `/api/branches/${novel.branch_id}`
  const upstream = await post(`${branch}/chapters`, { title: '旧航线' })
  const selected = await post(`${branch}/chapters`, { title: '雾中的渡口钟' })
  const earlierUpstream = await put(`/api/chapters/${upstream.id}`, {
    base_revision: 0,
    content: '船没在旧水道出现。',
  })
  const earlierSelected = await put(`/api/chapters/${selected.id}`, { base_revision: 0, content: oldText })

  const project = await put(`${branch}/conception/project-brief`, {
    base_revision: 0,
    idempotency_key: randomUUID(),
    idea: '掌灯的阿楠从雾钟和航线查出旧船是否返港。',
    brief: {
      position: '寻找雾中的旧船',
      track: '线索悬疑',
      genre_ids: ['mystery'],
      audience: '喜欢书中原文线索的读者',
      distinction: '逐次核对航线和钟声',
      length: '两卷各一章',
      ending: '阿楠找到返港的船。',
    },
  })
  const conception = `${branch}/conception`
  const setup = await read(conception)
  setup.categories.find(({ id }: { id: string }) => id === 'characters').items = [
    { id: 'lamp-keeper', title: '掌灯人阿楠', detail: '夜里在渡口记录钟声和航线。' },
  ]
  const edited = await put(conception, {
    base_revision: setup.revision,
    base_story_revision: setup.story_revision,
    categories: setup.categories,
  })
  const { confirmation: setting } = await post(`${conception}/confirm`, {
    base_revision: edited.revision,
    base_story_revision: edited.story_revision,
    idempotency_key: randomUUID(),
  })

  function storyEvidence(revision: number, text: string, quote: string, title: string) {
    const start = text.indexOf(quote)
    expect(start).toBeGreaterThanOrEqual(2)
    return {
      kind: 'event',
      title,
      status: 'actual',
      sources: [{ chapter_id: selected.id, revision, start, end: start + quote.length, quote }],
    }
  }
  const story = `${branch}/story-records`
  const earlierEventTitle = '阿楠听清渡口钟'
  const laterEventTitle = '阿楠复查潮声'
  const olderEvent = await post(story, {
    base_story_revision: (await read(`${branch}/story-graph`)).story_revision,
    record: storyEvidence(earlierSelected.revision, oldText, oldQuote, earlierEventTitle),
  })
  const event = olderEvent.records[0]
  const outline = `${branch}/outline-workspace`
  async function confirmOutline(base_revision: number, firstGoal: string, secondGoal: string) {
    const prepared = await put(outline, {
      base_revision,
      confirmation_id: setting.id,
      plan: {
        summary: '对照前卷航线和后卷钟声。',
        volumes: [upstream, selected].map(({ id }, index) => ({
          id: `volume-${index + 1}`,
          title: index ? '查钟卷' : '巡港卷',
          summary: '保留当次核对所用原文。',
          units: [
            {
              id: `unit-${index + 1}`,
              title: '寻船线索',
              summary: '保留钟声和航线的关系。',
              chapters: [
                {
                  chapter_id: id,
                  goal: index ? secondGoal : firstGoal,
                  obstacle: '水雾遮住船首',
                  turn: '在渡口听见响动',
                  result: '掌灯人得到一个线索',
                  setting_ids: ['lamp-keeper'],
                },
              ],
            },
          ],
        })),
      },
    })
    return (await post(`${outline}/confirm`, { base_revision: prepared.draft.revision })).confirmed
  }
  const oldOutline = await confirmOutline(0, '查找旧航线', restoredGoal)
  const archived = await post(`${branch}/work-snapshots`, {
    label: '后卷旧雾钟',
    scope: 'volume',
    scope_id: 'volume-2',
  })
  const kept = await put(`/api/chapters/${upstream.id}`, {
    base_revision: earlierUpstream.revision,
    content: outsideText,
  })
  const later = await put(`/api/chapters/${selected.id}`, {
    base_revision: earlierSelected.revision,
    content: laterText,
  })
  await put(`${story}/${event.id}`, {
    base_story_revision: (await read(`${branch}/story-graph`)).story_revision,
    base_revision: event.revision,
    record: storyEvidence(later.revision, laterText, newQuote, laterEventTitle),
  })
  await confirmOutline(oldOutline.revision, outsideGoal, laterGoal)
  const baseline = await read(`${branch}/manuscript-package`)
  const backupResponse = await page.request.get(`${branch}/manuscript-backup`)
  expect(backupResponse.ok(), await backupResponse.text()).toBe(true)
  const backup = await backupResponse.body()
  await backupResponse.dispose()

  const endpoint = `${cloud}/api/sync/works/${novel.branch_id}`
  const webDevice = 'web-originated-restore'
  async function grant(device_id: string) {
    const issued = await okay(
      await page.request.post(`${endpoint}/devices`, { headers: provisioning, data: { device_id } }),
    )
    return issued.token as string
  }
  const desktopToken = await grant('desktop-receives-restore')
  const webToken = await grant(webDevice)
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
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
  const seeded = await nativeSync()
  expect(seeded.cursor).toBeGreaterThan(2)

  const context = await browser.newContext({ baseURL, viewport: { width: 420, height: 900 } })
  try {
    const web = await context.newPage()
    await web.goto('/offline/')
    await expect.poll(() => web.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
    await context.setOffline(true)
    await web.reload()
    await web.getByLabel('从独立备份恢复作品', { exact: true }).setInputFiles({
      name: '雾钟卷点.ainovel-backup',
      mimeType: 'application/octet-stream',
      buffer: backup,
    })
    await expect(web.getByText('已校验并恢复独立作品备份。', { exact: true })).toBeVisible()
    const openSelected = () => web.getByRole('button', { name: '2. 雾中的渡口钟', exact: true }).click()
    const editor = web.getByLabel('章节正文')
    const connectWeb = async () => {
      await web.getByText('作品云同步', { exact: true }).click()
      await web.getByLabel('同步服务地址').fill(cloud)
      await web.getByLabel('设备编号').fill(webDevice)
      await web.getByLabel('设备凭据').fill(webToken)
    }
    const sync = web.getByRole('button', { name: '同步当前工作版本', exact: true })
    const settled = (cursor: number) =>
      web.getByText(`已启用 · 游标 ${cursor} · 0 条待发送或确认`, { exact: true })
    const syncStatus = web.getByRole('status').filter({ hasText: /已启用 · 游标 \d+ · 0 条待发送或确认/ })
    const readSettledCursor = async () => {
      const [status] = await syncStatus.allTextContents()
      return Number(/游标 (\d+)/.exec(status ?? '')?.[1] ?? 0)
    }
    const checkpoint = async () =>
      okay(
        await page.request.get(`${endpoint}/checkpoint`, {
          headers: { Authorization: `Bearer ${webToken}` },
        }),
      )
    await context.setOffline(false)
    await connectWeb()
    await web.getByRole('button', { name: '启用作品同步', exact: true }).click()
    await expect(sync).toBeEnabled()
    await sync.press('Enter')
    await expect.poll(readSettledCursor).toBeGreaterThanOrEqual(seeded.cursor)
    const common = await checkpoint()
    await expect(settled(common.cursor)).toBeVisible()
    expect((await nativeSync()).cursor).toBe(common.cursor)
    expect((await read(`${branch}/manuscript-package`)).outline_history.adopted_revision_id).toBe(
      baseline.outline_history.adopted_revision_id,
    )
    await openSelected()
    await expect(editor).toHaveValue(laterText)

    await context.setOffline(true)
    const history = web.getByLabel('已导入的卷书恢复点', { exact: true })
    await history.getByText('已导入的卷书恢复点', { exact: true }).press('Enter')
    await history.getByRole('combobox', { name: '选择已归档恢复点', exact: true }).selectOption(archived.id)
    await history.getByRole('button', { name: '预览归档原位恢复', exact: true }).press('Enter')
    const preview = history.getByRole('region', { name: '归档原位恢复预览', exact: true })
    await expect(preview.getByRole('heading', { name: '原位恢复「后卷旧雾钟」' })).toBeFocused()
    const apply = preview.getByRole('button', { name: '原位恢复为新修订', exact: true })
    await expect(apply).toBeDisabled()
    await preview.getByRole('checkbox', { name: '我已核对恢复影响', exact: true }).check()
    await expect(apply).toBeEnabled()
    await apply.press('Enter')
    const recovered = history.getByRole('status').filter({ hasText: '已恢复为新的当前修订' })
    await expect(recovered).toBeVisible()
    const recoveryId = (await recovered.locator('code').textContent())!
    await openSelected()
    await expect(editor).toHaveValue(oldText)
    expect(await checkpoint()).toMatchObject({ ledger_id: common.ledger_id, cursor: common.cursor })
    expect((await read(`/api/chapters/${selected.id}`)).content).toBe(laterText)
    expect((await read(`/api/chapters/${upstream.id}`)).revision).toBe(kept.revision)

    await web.reload()
    await web.getByRole('combobox', { name: '离线作品', exact: true }).selectOption(novel.id)
    await openSelected()
    await expect(editor).toHaveValue(oldText)
    const exporting = web.waitForEvent('download')
    await web.getByRole('button', { name: '导出稿件交换包', exact: true }).press('Enter')
    const restoredPack = JSON.parse(await readFile((await (await exporting).path())!, 'utf8'))
    const restoredOutlineId = restoredPack.outline_history.adopted_revision_id
    expect(restoredOutlineId).not.toBe(baseline.outline_history.adopted_revision_id)
    const restoredOutline = restoredPack.outline_history.versions.find(
      ({ id }: { id: string }) => id === restoredOutlineId,
    )
    expect(
      restoredOutline.plan.volumes.map(
        (volume: { units: { chapters: { goal: string }[] }[] }) => volume.units[0].chapters[0].goal,
      ),
    ).toEqual([outsideGoal, restoredGoal])
    expect(restoredPack.snapshot_history.entries.some(({ id }: { id: string }) => id === recoveryId)).toBe(
      true,
    )
    const restoredHead = restoredPack.revisions
      .filter(({ chapter_id }: { chapter_id: string }) => chapter_id === selected.id)
      .at(-1)
    expect(restoredHead).toMatchObject({
      revision: later.revision + 1,
      content: oldText,
      sha256: hash(oldText),
    })
    expect(
      restoredPack.revisions
        .filter(({ chapter_id }: { chapter_id: string }) => chapter_id === upstream.id)
        .at(-1),
    ).toMatchObject({ revision: kept.revision, content: outsideText })

    await connectWeb()
    await context.setOffline(false)
    await expect(sync).toBeEnabled()
    await sync.press('Enter')
    await expect.poll(readSettledCursor).toBeGreaterThan(common.cursor)
    const published = await checkpoint()
    expect(published.ledger_id).toBe(common.ledger_id)
    expect(published.cursor).toBeGreaterThan(common.cursor)
    await expect(settled(published.cursor)).toBeVisible()
    expect((await read(`/api/chapters/${selected.id}`)).content).toBe(laterText)

    const adopted = await nativeSync()
    expect(adopted.cursor).toBeGreaterThanOrEqual(published.cursor)
    expect(adopted.pending).toBe(0)
    expect(await checkpoint()).toMatchObject({ ledger_id: published.ledger_id, cursor: adopted.cursor })
    expect(await read(`/api/chapters/${selected.id}`)).toMatchObject({
      revision: later.revision + 1,
      content: oldText,
    })
    expect(await read(`/api/chapters/${upstream.id}`)).toMatchObject({
      revision: kept.revision,
      content: outsideText,
    })
    const received = await read(`${branch}/manuscript-package`)
    expect(received.project_history.adopted_revision_id).toBe(project.id)
    expect(received.settings_history.adopted_revision_id).toBe(setting.id)
    expect(received.outline_history.adopted_revision_id).toBe(restoredOutlineId)
    const adoptedEventId = received.story_history.adopted_heads[event.id]
    expect(adoptedEventId).toBe(restoredPack.story_history.adopted_heads[event.id])
    const restoredEvent = received.story_history.versions.find(
      ({ id }: { id: string }) => id === adoptedEventId,
    )
    expect(restoredEvent.record.title).toBe(earlierEventTitle)
    const source = restoredEvent.evidence.record[0]
    expect(source.revision_id).toBe(restoredHead.id)
    const sourceVersion = [...received.revisions, ...(received.source_revisions ?? [])].find(
      ({ id }: { id: string }) => id === source.revision_id,
    )
    expect(sourceVersion.sha256).toBe(hash(oldText))
    expect(sourceVersion.content.slice(source.start, source.end)).toBe(oldQuote)
    expect(source.quote_sha256).toBe(hash(oldQuote))
    expect(
      received.revisions.some(
        ({ chapter_id, revision, content }: { chapter_id: string; revision: number; content: string }) =>
          chapter_id === selected.id && revision === later.revision && content === laterText,
      ),
    ).toBe(true)
    expect(received.snapshot_history.entries.some(({ id }: { id: string }) => id === recoveryId)).toBe(false)
    await expect(sync).toBeEnabled()
    await sync.press('Enter')
    await expect(settled(adopted.cursor)).toBeVisible()
    await openSelected()
    await expect(editor).toHaveValue(oldText)
  } finally {
    await context.close()
  }
})
