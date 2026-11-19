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
  branch: unknown
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

async function exportWeb(web: Page): Promise<Package> {
  const download = web.waitForEvent('download')
  await web.getByRole('button', { name: '导出稿件交换包', exact: true }).click()
  return JSON.parse(await readFile(await (await download).path(), 'utf8')) as Package
}

async function prepareDesktop(page: Page, title: string) {
  await page.goto('/')
  const { csrf } = await checked<{ csrf: string }>(await page.request.get('/api/session'))
  const headers = { 'X-Ainovel-CSRF': csrf }
  const request = <T>(path: string, method: 'GET' | 'POST' | 'PUT' = 'GET', data?: object) =>
    page.request.fetch(path, { method, headers, data }).then(checked<T>)
  const novel = await request<{ id: string; branch_id: string }>('/api/novels', 'POST', { title })
  const branch = `/api/branches/${novel.branch_id}`
  const endpoint = `${cloud}/api/sync/works/${novel.branch_id}`
  const common = await Promise.all(
    ['桥下', '旧宅'].map((title) => request<Chapter>(`${branch}/chapters`, 'POST', { title })),
  )
  // Use the actual directory order when preparing the confirmed outline.
  common.sort((a, b) => a.position - b.position)
  for (const chapter of common) {
    await request(`/api/chapters/${chapter.id}`, 'PUT', {
      base_revision: 0,
      content: `${chapter.title}有铃声。`,
    })
  }
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
    const positions = (pack: Package) =>
      pack.chapters.filter(({ archived }) => !archived).map(({ id, position }) => [id, position])
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

    await page.reload()
    await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(f.novel.id)
    await page.getByText('章节整理与归档', { exact: true }).click()
    const move = page.getByRole('button', { name: '下移一章', exact: true })
    await expect(move).toBeEnabled()
    page.once('dialog', (dialog) => void dialog.accept())
    await move.click()
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
