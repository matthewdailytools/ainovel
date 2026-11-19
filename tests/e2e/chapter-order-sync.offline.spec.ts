import { expect, test, type APIResponse, type Page } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { readFile } from 'node:fs/promises'

const cloud = 'http://127.0.0.1:8888'
test.skip(process.env.AINOVEL_SYNC_E2E !== '1', '使用隔离云端进程运行双端验收')

type Chapter = { id: string; position: number; title: string; archived: number }
type Revision = {
  id: string
  chapter_id: string
  revision: number
  parent_id: string | null
  content: string
  sha256: string
}
type Package = {
  format: string
  novel: unknown
  branch: { story_revision: number }
  chapters: Chapter[]
  revisions: Revision[]
  outline_history: { adopted_revision_id: string; versions: { id: string; plan: unknown }[] }
}

async function checked<T>(response: APIResponse): Promise<T> {
  try {
    expect(response.ok(), await response.text()).toBe(true)
    return (await response.json()) as T
  } finally {
    await response.dispose()
  }
}

function chain(pack: Package, chapter: Chapter) {
  return pack.revisions
    .filter(({ chapter_id }) => chapter_id === chapter.id)
    .sort((a, b) => a.revision - b.revision)
}

function activeOutline(pack: Package) {
  const { adopted_revision_id, versions } = pack.outline_history
  return versions.find(({ id }) => id === adopted_revision_id)
}

function chapterPositions(pack: Package) {
  return pack.chapters.filter(({ archived }) => !archived).map(({ id, position }) => [id, position])
}

async function moveFirstChapterLater(page: Page, novelId: string) {
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novelId)
  await page.getByText('章节整理与归档', { exact: true }).click()
  const move = page.getByRole('button', { name: '下移一章', exact: true })
  await expect(move).toBeEnabled()
  page.once('dialog', (dialog) => void dialog.accept())
  await move.click()
}

async function exportWeb(web: Page): Promise<Package> {
  const download = web.waitForEvent('download')
  await web.getByRole('button', { name: '导出稿件交换包', exact: true }).click()
  return JSON.parse(await readFile(await (await download).path(), 'utf8')) as Package
}

async function prepareDesktop(page: Page, title: string, titles?: string[]) {
  await page.goto('/')
  const { csrf } = await checked<{ csrf: string }>(await page.request.get('/api/session'))
  const headers = { 'X-Ainovel-CSRF': csrf }
  const request = <T>(path: string, method: 'GET' | 'POST' | 'PUT' = 'GET', data?: object) =>
    page.request.fetch(path, { method, headers, data }).then(checked<T>)
  const novel = await request<{ id: string; branch_id: string }>('/api/novels', 'POST', { title })
  const branch = `/api/branches/${novel.branch_id}`
  const endpoint = `${cloud}/api/sync/works/${novel.branch_id}`
  const common: Chapter[] = []
  for (const title of titles ?? ['桥下', '旧宅']) {
    const chapter = await request<Chapter>(`${branch}/chapters`, 'POST', { title })
    await request(`/api/chapters/${chapter.id}`, 'PUT', {
      base_revision: 0,
      content: `${chapter.title}有铃声。`,
    })
    common.push(chapter)
  }
  if (!titles) {
    const outline = `${branch}/outline-workspace`
    const { draft } = await request<{ draft: { revision: number } }>(outline, 'PUT', {
      base_revision: 0,
      plan: {
        summary: '从桥下的信物查到旧宅。',
        volumes: common.map(({ id }, index) => ({
          id: `confirmed-volume-${index}`,
          title: `既有卷 ${index + 1}`,
          summary: '核对信物来历。',
          units: [
            {
              id: `confirmed-unit-${index}`,
              title: '追踪',
              summary: '按留下的标记推进。',
              chapters: [
                {
                  chapter_id: id,
                  goal: '核对刻痕',
                  obstacle: '痕迹模糊',
                  turn: '发现第二笔',
                  result: '查到信物',
                },
              ],
            },
          ],
        })),
      },
    })
    await request(`${outline}/confirm`, 'POST', { base_revision: draft.revision })
  }
  const [desktopToken, webToken, coldToken] = await Promise.all(
    ['desktop', 'web', 'cold'].map(async (device_id) => {
      const { token } = await checked<{ token: string }>(
        await page.request.post(`${endpoint}/devices`, {
          headers: { Authorization: 'Bearer e2e-test-only-provisioning-token' },
          data: { device_id },
        }),
      )
      return token
    }),
  )
  type SyncResult = { cursor: number; conflicts: unknown[]; deferred: { object_id: string }[] }
  const desktopSync = (action: 'enable' | 'run' = 'run') =>
    request<SyncResult>(`${branch}/desktop-sync/${action}`, 'POST', {
      origin: cloud,
      token: desktopToken,
    })
  await desktopSync('enable')
  await desktopSync()
  const desktopPack = () => request<Package>(`${branch}/manuscript-package`)
  const baseline = await desktopPack()
  const checkpoint = () =>
    page.request
      .get(`${endpoint}/checkpoint`, {
        headers: { Authorization: `Bearer ${desktopToken}` },
      })
      .then(checked<{ cursor: number; ledger_id: string }>)
  return {
    novel,
    branch,
    common,
    endpoint,
    baseline,
    desktopPack,
    desktopSync,
    checkpoint,
    desktopToken,
    webToken,
    coldToken,
    request,
  }
}

async function backupDesktop(page: Page, branch: string) {
  const reply = await page.request.get(`${branch}/manuscript-backup`)
  try {
    expect(reply.ok()).toBe(true)
    return await reply.body()
  } finally {
    await reply.dispose()
  }
}

async function importWebBackup(web: Page, buffer: Buffer) {
  await web.goto('/offline/')
  await expect.poll(() => web.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
  await web.getByLabel('从独立备份恢复作品', { exact: true }).setInputFiles({
    name: '旧章序.ainovel-backup',
    mimeType: 'application/octet-stream',
    buffer,
  })
  await expect(web.getByText('已校验并恢复独立作品备份。', { exact: true })).toBeVisible()
}

async function previewWebCheckpoint(web: Page, snapshotId: string) {
  const history = web.getByLabel('已导入的卷书恢复点', { exact: true })
  await history.getByText('已导入的卷书恢复点', { exact: true }).press('Enter')
  await history.getByRole('combobox', { name: '选择已归档恢复点', exact: true }).selectOption(snapshotId)
  await history.getByRole('button', { name: '预览归档原位恢复', exact: true }).press('Enter')
  return history
}

async function restoreWebCheckpoint(web: Page, snapshotId: string) {
  const history = await previewWebCheckpoint(web, snapshotId)
  const preview = history.getByRole('region', { name: '归档原位恢复预览', exact: true })
  await preview.getByRole('checkbox', { name: '我已核对恢复影响', exact: true }).check()
  await preview.getByRole('button', { name: '原位恢复为新修订', exact: true }).press('Enter')
  await expect(history.getByRole('status').filter({ hasText: '已恢复为新的当前修订' })).toBeVisible()
}

async function openWeb(web: Page, title: string, pack: object, token: string, device: string) {
  await web.goto('/offline/')
  await web.getByLabel('导入新离线作品').setInputFiles({
    name: '同位章节验收.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(pack)),
  })
  const sync = await connectWeb(web, title, token, device)
  await web.getByRole('button', { name: '启用作品同步', exact: true }).click()
  return sync
}

async function connectWeb(web: Page, title: string, token: string, device: string) {
  await web.getByRole('combobox', { name: '离线作品', exact: true }).selectOption({ label: title })
  await web.getByText('作品云同步', { exact: true }).click()
  await web.getByLabel('同步服务地址').fill(cloud)
  await web.getByLabel('设备编号').fill(device)
  await web.getByLabel('设备凭据').fill(token)
  const sync = web.getByRole('button', { name: '同步当前工作版本', exact: true })
  return async () => {
    await sync.click()
    await expect(sync).toBeEnabled()
  }
}

test('桌面重排分页中断后，原 Web 只取回完整目录和原章稿', async ({ page, browser }) => {
  const title = '整组目录同步验收'
  const f = await prepareDesktop(page, title)
  const [first, second] = f.common
  const context = await browser.newContext()
  try {
    const web = await context.newPage()
    await (
      await openWeb(web, title, f.baseline, f.webToken, 'web')
    )()
    const confirmed = await f.checkpoint()
    const positions = chapterPositions
    const original = [
      [first.id, 1],
      [second.id, 2],
    ]
    const reordered = [
      [second.id, 1],
      [first.id, 2],
    ]
    expect(positions(await exportWeb(web))).toEqual(original)

    const snapshot = await f.request<{ id: string }>(`${f.branch}/work-snapshots`, 'POST', {
      scope: 'book',
      scope_id: null,
      label: '换序前目录',
    })

    await moveFirstChapterLater(page, f.novel.id)
    await expect.poll(async () => positions(await f.desktopPack())).toEqual(reordered)
    expect((await f.desktopSync()).conflicts).toHaveLength(0)

    const afterMove = await f.checkpoint()
    const { changes } = await checked<{ changes: { object_id: string; change_id: string }[] }>(
      await page.request.get(`${f.endpoint}/pull?after=${confirmed.cursor}&limit=50`, {
        headers: { Authorization: `Bearer ${f.desktopToken}`, 'X-Ainovel-Ledger': afterMove.ledger_id },
      }),
    )
    expect(changes.filter(({ change_id }) => change_id.startsWith('chapter-order-part-'))).toHaveLength(2)
    expect(changes.filter(({ object_id }) => object_id.startsWith('chapter-directory:'))).toHaveLength(1)

    const afterRequest: number[] = []
    let stoppedAt = 0
    await web.route(`${f.endpoint}/pull?*`, async (route) => {
      const url = new URL(route.request().url())
      afterRequest.push(Number(url.searchParams.get('after')))
      if (afterRequest.length === 1) {
        url.searchParams.set('limit', '1')
        const response = await route.fetch({ url: url.toString() })
        const part = await response.json()
        expect(part.has_more).toBe(true)
        expect(part.changes[0].change_id).toMatch(/^chapter-order-part-/)
        stoppedAt = part.cursor
        await route.fulfill({ response })
      } else {
        await route.abort('connectionreset')
      }
    })
    await web.getByRole('button', { name: '同步当前工作版本', exact: true }).click()
    await expect(web.getByRole('alert')).toBeVisible()
    expect(afterRequest).toEqual([confirmed.cursor, stoppedAt])
    expect(positions(await exportWeb(web))).toEqual(original)
    await web.close()

    const reopened = await context.newPage()
    await reopened.goto('/offline/')
    const sync = await connectWeb(reopened, title, f.webToken, 'web')
    expect(positions(await exportWeb(reopened))).toEqual(original)
    const resumed = reopened.waitForRequest((request) => request.url().startsWith(`${f.endpoint}/pull?`))
    await sync()
    expect(new URL((await resumed).url()).searchParams.get('after')).toBe(String(stoppedAt))
    await expect(reopened.getByRole('button', { name: `1. ${second.title}`, exact: true })).toBeVisible()
    const final = await exportWeb(reopened)
    expect(positions(final)).toEqual(reordered)
    expect(activeOutline(final)).toEqual(activeOutline(f.baseline))
    for (const chapter of f.common) expect(chain(final, chapter)).toEqual(chain(f.baseline, chapter))
    await sync()
    expect((await f.checkpoint()).cursor).toBe(afterMove.cursor)

    const path = `${f.branch}/work-snapshots/${snapshot.id}`
    const preview = await f.request<{ basis_sha256: string; in_place_blockers: string[] }>(`${path}/preview`)
    expect(preview.in_place_blockers).toEqual([])
    const restored = await f.request<{ recovery_snapshot_id: string }>(`${path}/restore`, 'POST', {
      mode: 'in_place',
      basis_sha256: preview.basis_sha256,
      idempotency_key: randomUUID(),
    })
    expect(restored.recovery_snapshot_id).toBeTruthy()
    const nativeRestored = await f.desktopPack()
    expect(positions(nativeRestored)).toEqual(original)
    await f.desktopSync()
    await sync()
    await expect(reopened.getByRole('button', { name: `1. ${first.title}`, exact: true })).toBeVisible()
    const webRestored = await exportWeb(reopened)
    expect(positions(webRestored)).toEqual(original)
    for (const chapter of f.common)
      expect(chain(webRestored, chapter)).toEqual(chain(nativeRestored, chapter))
  } finally {
    await context.close()
  }
})

test('独立 Web 原位恢复先发布整组旧章序，回执丢失后仍准确同步同文新修订', async ({ page, browser }) => {
  const title = '独立 Web 回滚章序验收'
  const f = await prepareDesktop(page, title)
  const [first, second] = f.common
  const original = [
    [first.id, 1],
    [second.id, 2],
  ]
  const reordered = [
    [second.id, 1],
    [first.id, 2],
  ]
  const quote = '有铃声'
  const source = chain(f.baseline, first).at(-1)!
  await f.request('/api/facts', 'POST', {
    chapter_id: first.id,
    source_revision: source.revision,
    subject: first.title,
    kind: 'event',
    truth: 'fact',
    evidence: quote,
    statement: '地点有铃声。',
  })
  const old = await f.request<{ id: string }>(`${f.branch}/work-snapshots`, 'POST', {
    scope: 'book',
    scope_id: null,
    label: '旧章序和原文选区',
  })
  const backup = await backupDesktop(page, f.branch)
  await f.desktopSync()

  const context = await browser.newContext({ viewport: { width: 420, height: 880 } })
  try {
    const web = await context.newPage()
    await importWebBackup(web, backup)
    const sync = await connectWeb(web, title, f.webToken, 'web')
    await web.getByRole('button', { name: '启用作品同步', exact: true }).click()
    await sync()
    expect(chapterPositions(await exportWeb(web))).toEqual(original)

    await moveFirstChapterLater(page, f.novel.id)
    await expect.poll(async () => chapterPositions(await f.desktopPack())).toEqual(reordered)
    expect((await f.desktopSync()).conflicts).toEqual([])
    await sync()
    expect(chapterPositions(await exportWeb(web))).toEqual(reordered)
    const common = await f.checkpoint()

    await context.setOffline(true)
    await restoreWebCheckpoint(web, old.id)
    const local = await exportWeb(web)
    expect(chapterPositions(local)).toEqual(original)
    const newSource = chain(local, first).at(-1)!
    expect(newSource.id).not.toBe(source.id)
    expect(newSource).toMatchObject({ parent_id: source.id, sha256: source.sha256, content: source.content })
    expect(await f.checkpoint()).toEqual(common)

    await context.setOffline(false)
    await web.route(
      (url) => url.pathname.endsWith('/chapter-order-batches'),
      async (route) => {
        const result = await route.fetch()
        expect(result.ok(), await result.text()).toBe(true)
        await route.abort('connectionreset')
      },
      { times: 1 },
    )
    await sync()
    await expect(web.getByRole('alert')).toBeVisible()
    const dropped = await f.checkpoint()
    expect(dropped.cursor).toBe(common.cursor + 3)
    await web.close()
    const reopened = await context.newPage()
    await reopened.goto('/offline/')
    const resumed = await connectWeb(reopened, title, f.webToken, 'web')
    await resumed()

    const final = await f.checkpoint()
    type Event = {
      object_id: string
      change_id: string
      parent_revision: string | null
      payload: { head?: Revision }
    }
    const { changes } = await checked<{ changes: Event[] }>(
      await page.request.get(`${f.endpoint}/pull?after=${common.cursor}&limit=50`, {
        headers: { Authorization: `Bearer ${f.desktopToken}`, 'X-Ainovel-Ledger': final.ledger_id },
      }),
    )
    expect(changes.filter(({ object_id }) => object_id.startsWith('chapter-directory:'))).toHaveLength(1)
    const parts = changes.slice(0, 2)
    expect(parts.map(({ change_id }) => change_id)).toEqual([
      expect.stringMatching(/^chapter-order-part-/),
      expect.stringMatching(/^chapter-order-part-/),
    ])
    expect(parts.find(({ object_id }) => object_id === `chapter:${first.id}`)?.payload.head?.id).toBe(
      source.id,
    )
    const formal = changes.findIndex(
      ({ object_id, payload }) => object_id === `chapter:${first.id}` && payload.head?.id === newSource.id,
    )
    const immutable = changes.findIndex(({ object_id }) => object_id === `revision:${newSource.id}`)
    expect(immutable).toBeGreaterThan(2)
    expect(formal).toBeGreaterThan(immutable)
    expect(changes[formal].parent_revision).toBe(
      parts.find(({ object_id }) => object_id === `chapter:${first.id}`)?.change_id,
    )

    const native = await f.desktopSync()
    expect(native.conflicts).toEqual([])
    expect(native.deferred).toEqual([])
    const desktop = await f.desktopPack()
    expect(chapterPositions(desktop)).toEqual(original)
    for (const chapter of f.common) expect(chain(desktop, chapter)).toEqual(chain(local, chapter))
    expect(activeOutline(desktop)?.plan).toEqual(activeOutline(local)?.plan)
    const facts = await f.request<unknown[]>(`${f.branch}/facts`)
    expect(facts).toContainEqual(
      expect.objectContaining({
        chapter_id: first.id,
        source_revision: newSource.revision,
        evidence: quote,
        evidence_start: source.content.indexOf(quote),
        evidence_end: source.content.indexOf(quote) + quote.length,
        stale: 0,
      }),
    )
    await resumed()
    expect(await f.checkpoint()).toEqual(final)
  } finally {
    await context.close()
  }
})

test('Web 混合回滚断线重开后用原提交续传被占用的章位', async ({ page, browser }) => {
  const title = '混合章序续传验收'
  const f = await prepareDesktop(page, title, ['初始一', '初始二', '初始三', '初始四'])
  const [first, second, third, fourth] = f.common
  const original = f.common.map(({ id, position }) => [id, position])
  const snapshot = await f.request<{ id: string }>(`${f.branch}/work-snapshots`, 'POST', {
    scope: 'book',
    label: '旧章序与活动章',
  })
  const backup = await backupDesktop(page, f.branch)
  const context = await browser.newContext()
  try {
    const web = await context.newPage()
    await importWebBackup(web, backup)
    const sync = await connectWeb(web, title, f.webToken, 'web')
    await web.getByRole('button', { name: '启用作品同步', exact: true }).click()
    await sync()
    expect(chapterPositions(await exportWeb(web))).toEqual(original)

    await f.request(`/api/chapters/${second.id}/archive`, 'POST', { base_revision: 1, archived: true })
    const added = await f.request<Chapter>(`${f.branch}/chapters`, 'POST', { title: '后加章' })
    await f.request(`/api/chapters/${added.id}`, 'PUT', { base_revision: 0, content: '额外的独立正文。' })
    for (const direction of ['up', 'down']) {
      const { branch } = await f.desktopPack()
      await f.request(`/api/chapters/${added.id}/move`, 'POST', {
        direction,
        base_story_revision: branch.story_revision,
      })
    }
    await f.desktopSync()
    const latest = [
      [first.id, 1],
      [third.id, 2],
      [fourth.id, 3],
      [added.id, 4],
    ]
    await expect
      .poll(async () => {
        await sync()
        return chapterPositions(await exportWeb(web))
      })
      .toEqual(latest)
    const beforeRestore = await f.checkpoint()

    await context.setOffline(true)
    await restoreWebCheckpoint(web, snapshot.id)
    const restored = await exportWeb(web)
    expect(chapterPositions(restored)).toEqual(original)
    await context.setOffline(false)
    const chapterIds = new Set([`chapter:${third.id}`, `chapter:${fourth.id}`])
    let restoredId = ''
    let interrupted: { object_id: string; change_id: string } | undefined
    await web.route(
      (url) => url.pathname.endsWith('/push'),
      async (route) => {
        const change = route.request().postDataJSON() as { object_id: string; change_id: string }
        if (restoredId && chapterIds.has(change.object_id)) {
          interrupted = change
          await route.abort('connectionreset')
        } else {
          if (change.object_id === `chapter:${second.id}`) restoredId = change.change_id
          await route.continue()
        }
      },
    )
    await sync()
    await expect(web.getByRole('alert')).toBeVisible()
    expect(restoredId).toBeTruthy()
    expect(interrupted?.object_id).toBe(`chapter:${third.id}`)
    await web.close()

    const reopened = await context.newPage()
    await reopened.goto('/offline/')
    const resumed = await connectWeb(reopened, title, f.webToken, 'web')
    expect(chapterPositions(await exportWeb(reopened))).toEqual(original)
    await expect
      .poll(async () => {
        await resumed()
        return (await f.desktopSync()).deferred
      })
      .toEqual([])
    const desktop = await f.desktopPack()
    expect(chapterPositions(desktop)).toEqual(original)
    expect(desktop.chapters.find(({ id }) => id === added.id)?.archived).toBe(1)
    for (const chapter of [...f.common, added])
      expect(chain(desktop, chapter)).toEqual(chain(restored, chapter))

    const checkpoint = await f.checkpoint()
    const { changes } = await checked<{ changes: { object_id: string; change_id: string }[] }>(
      await page.request.get(`${f.endpoint}/pull?after=${beforeRestore.cursor}&limit=50`, {
        headers: { Authorization: `Bearer ${f.desktopToken}`, 'X-Ainovel-Ledger': checkpoint.ledger_id },
      }),
    )
    expect(changes.filter(({ change_id }) => change_id === restoredId)).toHaveLength(1)
    expect(changes.filter(({ object_id }) => object_id === interrupted?.object_id)).toEqual([
      expect.objectContaining({ change_id: interrupted?.change_id }),
    ])
    await resumed()
    expect(await f.checkpoint()).toEqual(checkpoint)
  } finally {
    await context.close()
  }
})

test('正式大纲在合法重排后可由备份和云端冷恢复', async ({ page, browser }) => {
  const title = '重排后正式大纲冷恢复验收'
  const f = await prepareDesktop(page, title)
  const [first, second] = f.common
  const outline = activeOutline(f.baseline)
  expect(outline).toBeDefined()
  const snapshot = await f.request<{ id: string }>(`${f.branch}/work-snapshots`, 'POST', {
    scope: 'book',
    label: '旧大纲与章序',
  })
  await moveFirstChapterLater(page, f.novel.id)
  const moved = [
    [second.id, 1],
    [first.id, 2],
  ]
  await expect.poll(async () => chapterPositions(await f.desktopPack())).toEqual(moved)
  await f.desktopSync()

  const fromBackup = await browser.newContext()
  const fromCloud = await browser.newContext()
  try {
    const restored = await fromBackup.newPage()
    await importWebBackup(restored, await backupDesktop(page, f.branch))
    await restored.getByRole('combobox', { name: '离线作品', exact: true }).selectOption({ label: title })
    const cold = await fromCloud.newPage()
    const { format, novel, branch } = f.baseline
    const sync = await openWeb(
      cold,
      title,
      { format, novel, branch, chapters: [], revisions: [] },
      f.coldToken,
      'cold',
    )
    await sync()
    for (const tab of [restored, cold]) {
      const pack = await exportWeb(tab)
      expect(chapterPositions(pack)).toEqual(moved)
      expect(activeOutline(pack)).toEqual(outline)
      for (const chapter of f.common) expect(chain(pack, chapter)).toEqual(chain(f.baseline, chapter))
      const workbench = tab.getByLabel('离线大纲台', { exact: true })
      await workbench.getByText('大纲台', { exact: true }).click()
      await expect(workbench.getByText(`已确认修订 ${outline!.id}`, { exact: false })).toBeVisible()
    }
    await fromBackup.setOffline(true)
    await restoreWebCheckpoint(restored, snapshot.id)
    const recovered = await exportWeb(restored)
    expect(chapterPositions(recovered)).toEqual([
      [first.id, 1],
      [second.id, 2],
    ])
    expect(activeOutline(recovered)).toEqual(outline)
  } finally {
    await fromCloud.close()
    await fromBackup.close()
  }
})

async function orderConflictRow(surface: Page) {
  const conflict = surface.getByRole('region', { name: '章位冲突', exact: true })
  const row = conflict.getByRole('region', { name: '章位 3', exact: true })
  await expect(row).toContainText('第3章')
  await expect(row).toContainText('浏览器续章')
  return row
}

for (const first of ['desktop', 'web'] as const) {
  test(`两端新增同位章节可从${first === 'desktop' ? '独立 Web ' : '桌面'}确认章序并保留全部原稿`, async ({
    page,
    browser,
  }) => {
    const title = `同位章节验收 · ${first}`
    const f = await prepareDesktop(page, title)
    const browserContext = await browser.newContext({ viewport: { width: 420, height: 880 } })
    try {
      const web = await browserContext.newPage()
      const webSync = await openWeb(web, title, f.baseline, f.webToken, 'web')
      await webSync()
      await expect.poll(() => web.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
      await browserContext.setOffline(true)
      await page.reload()
      await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(f.novel.id)
      const desktopEditor = page.getByRole('textbox', { name: '章节正文', exact: true })
      await page.getByRole('button', { name: '＋ 新章', exact: true }).click()
      await expect(page.getByRole('textbox', { name: '章节标题', exact: true })).toHaveValue('第3章')
      const desktopText = '最后一级台阶下藏着一柄钥匙。😀'
      await desktopEditor.fill(desktopText)
      await page.getByRole('button', { name: '保存', exact: true }).click()
      await expect
        .poll(async () => (await f.desktopPack()).revisions.some(({ content }) => content === desktopText))
        .toBe(true)

      const webText = '灯柱后留了一张刻痕纸。𠮷'
      await web.getByLabel('新章节名').fill('浏览器续章')
      await web.getByRole('button', { name: '本地新建章节', exact: true }).click()
      await web.getByRole('button', { name: '3. 浏览器续章', exact: true }).click()
      await web.getByRole('textbox', { name: '章节正文', exact: true }).fill(webText)
      await web.getByRole('button', { name: '保存正文到浏览器', exact: true }).click()
      await expect(web.getByText('本地已保存 · 修订 1', { exact: true })).toBeVisible()

      const deskOriginal = await f.desktopPack()
      const webOriginal = await exportWeb(web)
      const deskChapter = deskOriginal.chapters.find(({ position }) => position === 3)!
      const webChapter = webOriginal.chapters.find(({ position }) => position === 3)!
      expect(deskChapter.id).not.toBe(webChapter.id)
      for (const [pack, chapter, prose] of [
        [deskOriginal, deskChapter, desktopText],
        [webOriginal, webChapter, webText],
      ] as const) {
        const versions = chain(pack, chapter)
        expect(versions.map(({ revision }) => revision)).toEqual([0, 1])
        expect(versions[1]).toMatchObject({ parent_id: versions[0].id, content: prose })
      }
      if (first === 'desktop') await f.desktopSync()
      await browserContext.setOffline(false)
      await webSync()
      const stopped = await f.desktopSync()
      await webSync()
      expect(stopped.conflicts).toHaveLength(0)
      expect(stopped.deferred.map(({ object_id }) => object_id)).toContain(`chapter:${webChapter.id}`)
      const parked = await f.checkpoint()
      const { changes } = await checked<{ changes: { object_id: string; device_id: string }[] }>(
        await page.request.get(`${f.endpoint}/pull?after=0&limit=50`, {
          headers: { Authorization: `Bearer ${f.desktopToken}`, 'X-Ainovel-Ledger': parked.ledger_id },
        }),
      )
      const newObjects = [`chapter:${deskChapter.id}`, `chapter:${webChapter.id}`]
      expect(
        changes.filter(({ object_id }) => newObjects.includes(object_id)).map(({ device_id }) => device_id),
      ).toEqual(first === 'desktop' ? ['desktop', 'web'] : ['web', 'desktop'])

      const moved = first === 'desktop' ? webChapter : deskChapter
      const remaining = first === 'desktop' ? deskChapter : webChapter
      if (first === 'desktop') {
        const row = await orderConflictRow(web)
        await row.getByLabel('选择调整章位的章节').selectOption(moved.id)
        await web.route(
          (url) => url.pathname.endsWith('/chapter-order-conflicts/resolve'),
          async (route) => {
            const result = await route.fetch()
            expect(result.ok(), await result.text()).toBe(true)
            await route.abort('connectionreset')
          },
          { times: 1 },
        )
        await row.getByRole('button', { name: '移至目录末尾', exact: true }).press('Enter')
        const retry = web.getByRole('button', { name: '重试章位调整', exact: true })
        await expect(retry).toBeEnabled()
        await expect(retry).toBeFocused()
        expect((await f.checkpoint()).cursor).toBe(parked.cursor + 1)
        await retry.press('Enter')
      } else {
        await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
        const panel = page.getByRole('region', { name: '作品云同步', exact: true })
        await panel.getByLabel('同步服务地址').fill(cloud)
        await panel.getByLabel('设备凭据').fill(f.desktopToken)
        await panel.getByRole('button', { name: '核对章位冲突', exact: true }).click()
        const row = await orderConflictRow(page)
        await row.getByLabel('选择调整章位的章节').selectOption(moved.id)
        await row.getByRole('button', { name: '移至目录末尾', exact: true }).press('Enter')
        await expect(panel.getByText('章位已调整并同步。', { exact: true })).toBeVisible()
        await expect(panel.getByRole('button', { name: '核对章位冲突', exact: true })).toBeFocused()
      }

      await expect.poll(async () => (await f.checkpoint()).cursor).toBe(parked.cursor + 1)
      if (first === 'desktop')
        await expect(web.getByText('云端章位已调整，本机目录已更新。', { exact: true })).toBeVisible()
      await f.desktopSync()
      await webSync()
      await expect(web.getByRole('button', { name: `3. ${remaining.title}`, exact: true })).toBeVisible()
      await expect(web.getByRole('button', { name: `4. ${moved.title}`, exact: true })).toBeVisible()

      const freshContext = await browser.newContext()
      try {
        const fresh = await freshContext.newPage()
        const { format, novel, branch } = f.baseline
        const coldSync = await openWeb(
          fresh,
          title,
          { format, novel, branch, chapters: [], revisions: [] },
          f.coldToken,
          'cold',
        )
        await coldSync()
        await expect(fresh.getByRole('button', { name: `4. ${moved.title}`, exact: true })).toBeVisible()
        const frozenOutline = activeOutline(f.baseline)
        expect(frozenOutline).toBeDefined()
        for (const pack of await Promise.all([f.desktopPack(), exportWeb(web), exportWeb(fresh)])) {
          expect(
            pack.chapters.filter(({ archived }) => !archived).map(({ id, position }) => [id, position]),
          ).toEqual([
            [f.common[0].id, 1],
            [f.common[1].id, 2],
            [remaining.id, 3],
            [moved.id, 4],
          ])
          expect(chain(pack, deskChapter)).toEqual(chain(deskOriginal, deskChapter))
          expect(chain(pack, webChapter)).toEqual(chain(webOriginal, webChapter))
          expect(activeOutline(pack)).toEqual(frozenOutline)
        }
        await f.desktopSync()
        await webSync()
        await coldSync()
        expect((await f.checkpoint()).cursor).toBe(parked.cursor + 1)
      } finally {
        await freshContext.close()
      }
    } finally {
      await browserContext.close()
    }
  })
}
