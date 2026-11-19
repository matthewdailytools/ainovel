import { expect, test, type APIResponse, type BrowserContext, type Page } from '@playwright/test'
import { createHash, randomUUID } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import type { components } from '../../frontend/api-schema'

const cloud = process.env.AINOVEL_TEST_SYNC_URL ?? 'http://127.0.0.1:8888'
const provisioning = { Authorization: 'Bearer e2e-test-only-provisioning-token' }
const oldQuote = '暮鼓在北岸回响'
const newQuote = '南岸的灯光先亮'
const original = ['石匠沿北岸查看桥墩。', `😀船靠岸时，${oldQuote}。`]
const rewritten = ['石匠决定去南岸问人。', `😀入夜后，${newQuote}。`]
const settingId = 'bridge-keeper'
const hash = (value: string) => createHash('sha256').update(value, 'utf8').digest('hex')

type Schema = components['schemas']
type Package = Schema['ManuscriptPackage']
type Chapter = { id: string; revision: number; content: string }
type StoryGraph = { story_revision: number; records: { id: string; revision: number }[] }
type Head<T> = { adopted_revision_id: string | null; versions: T[] }

test.skip(process.env.AINOVEL_SYNC_E2E !== '1', '需要隔离云账本、桌面 SQLite 和独立浏览器 IndexedDB')

async function checked<T>(response: APIResponse): Promise<T> {
  expect(response.ok(), await response.text()).toBe(true)
  return response.json()
}

function adopted<T extends { id: string }>(history: Head<T> | null | undefined): T {
  const version = history?.versions.find(({ id }) => id === history.adopted_revision_id)
  expect(version).toBeDefined()
  return version!
}

async function exportPackage(web: Page): Promise<Package> {
  const downloading = web.waitForEvent('download')
  await web.getByRole('button', { name: '导出稿件交换包', exact: true }).press('Enter')
  return JSON.parse(await readFile((await (await downloading).path())!, 'utf8'))
}

async function prepareBook(page: Page) {
  await page.goto('/')
  const { csrf } = await checked<{ csrf: string }>(await page.request.get('/api/session'))
  const headers = { 'X-Ainovel-CSRF': csrf }
  const get = async <T>(path: string) => checked<T>(await page.request.get(path))
  const post = async <T>(path: string, data: unknown) =>
    checked<T>(await page.request.post(path, { headers, data }))
  const put = async <T>(path: string, data: unknown) =>
    checked<T>(await page.request.put(path, { headers, data }))
  const novel = await post<{ id: string; branch_id: string }>('/api/novels', { title: '两岸暮鼓 · 整书恢复' })
  const branch = `/api/branches/${novel.branch_id}`
  const packPath = `${branch}/manuscript-package`
  const empty = await get<Package>(packPath)
  const conception = `${branch}/conception`
  const story = `${branch}/story-records`
  const outline = `${branch}/outline-workspace`
  const chapterTitles = ['北岸桥墩', '先响的暮鼓']
  const chapters: Chapter[] = []
  for (const title of chapterTitles) chapters.push(await post<Chapter>(`${branch}/chapters`, { title }))
  const [first, second] = chapters
  const save = (chapter: Chapter, content: string) =>
    put<Chapter>(`/api/chapters/${chapter.id}`, { base_revision: chapter.revision, content })
  async function saveDefinitions(projectRevision: number, outlineRevision: number, before: boolean) {
    const ending = before ? '石匠辨认出北岸暮鼓。' : '石匠查明南岸灯号。'
    const detail = before ? '守桥人辨认暮鼓的方向。' : '守桥人辨认灯号的次序。'
    const goal = before ? '确认北岸暮鼓' : '核对南岸灯号'
    const summary = before ? '以北岸暮鼓作结。' : '以南岸灯号作结。'
    const project = await put<Schema['ConfirmedProject']>(`${conception}/project-brief`, {
      base_revision: projectRevision,
      idempotency_key: randomUUID(),
      idea: '石匠在两岸查证渡船从哪头靠岸。',
      brief: {
        position: '查明渡船停泊地点',
        track: '线索悬疑',
        genre_ids: ['mystery'],
        audience: '喜欢循证的悬疑读者',
        distinction: '比对桥墩、暮鼓和灯号',
        length: '两卷各一章',
        ending,
      },
    })
    const setup = await get<Schema['ConceptionWorkspace']>(conception)
    const category = setup.categories.find(({ id }) => id === 'characters')!
    category.items = [{ id: settingId, title: '守桥人', detail }]
    const saved = await put<Schema['ConceptionWorkspace']>(conception, {
      base_revision: setup.revision,
      base_story_revision: setup.story_revision,
      categories: setup.categories,
    })
    const { confirmation } = await post<{ confirmation: Schema['ConceptionConfirmation'] }>(
      `${conception}/confirm`,
      {
        base_revision: saved.revision,
        base_story_revision: saved.story_revision,
        idempotency_key: randomUUID(),
      },
    )
    const draft = await put<{ draft: { revision: number } }>(outline, {
      base_revision: outlineRevision,
      confirmation_id: confirmation.id,
      plan: {
        summary,
        volumes: chapters.map(({ id }, index) => ({
          id: `river-volume-${index + 1}`,
          title: index === 0 ? '看桥卷' : '查船卷',
          summary: '查找渡船靠岸的原文证据。',
          units: [
            {
              id: `river-unit-${index + 1}`,
              title: '核对靠岸证据',
              summary: '从桥墩与原文证据定位渡船。',
              chapters: [
                {
                  chapter_id: id,
                  goal,
                  obstacle: '渡船被桥墩挡住',
                  turn: '守桥人指出渡船去向',
                  result: '石匠辨认出渡船去向',
                  setting_ids: [settingId],
                },
              ],
            },
          ],
        })),
      },
    })
    const { confirmed } = await post<{ confirmed: { id: string; revision: number } }>(`${outline}/confirm`, {
      base_revision: draft.draft.revision,
    })
    return { project, confirmation, confirmed, ending, detail, goal, summary }
  }
  async function storyBody(chapter: Chapter, quote: string, title: string) {
    const start = chapter.content.indexOf(quote)
    expect(start).toBeGreaterThan(2)
    return {
      base_story_revision: (await get<StoryGraph>(`${branch}/story-graph`)).story_revision,
      record: {
        kind: 'event',
        title,
        status: 'actual',
        sources: [
          { chapter_id: chapter.id, revision: chapter.revision, start, end: start + quote.length, quote },
        ],
      },
    }
  }
  await save(first, original[0])
  const oldChapter = await save(second, original[1])
  const oldEventTitle = '暮鼓来自北岸'
  const event = (await post<StoryGraph>(story, await storyBody(oldChapter, oldQuote, oldEventTitle)))
    .records[0]
  const old = await saveDefinitions(0, 0, true)
  const snapshot = await post<{ id: string }>(`${branch}/work-snapshots`, {
    label: '旧暮鼓整书',
    scope: 'book',
  })
  const modified = [await save({ ...first, revision: 1 }, rewritten[0]), await save(oldChapter, rewritten[1])]
  await put(`${story}/${event.id}`, {
    ...(await storyBody(modified[1], newQuote, '南岸先亮灯')),
    base_revision: event.revision,
  })
  const later = await saveDefinitions(old.project.revision, old.confirmed.revision, false)
  const added = await post<Chapter>(`${branch}/chapters`, { title: '后加的问询' })
  const addedContent = '额外问询记录留在后来章节的历史里。'
  await save(added, addedContent)
  const addedHistory = (await get<Package>(packPath)).revisions.filter(
    ({ chapter_id }) => chapter_id === added.id,
  )
  expect(addedHistory.map(({ content }) => content)).toEqual(['', addedContent])
  const backupResponse = await page.request.get(`${branch}/manuscript-backup`)
  expect(backupResponse.ok(), await backupResponse.text()).toBe(true)
  const backup = await backupResponse.body()
  await backupResponse.dispose()

  const endpoint = `${cloud}/api/sync/works/${novel.branch_id}`
  const device = 'book-restore-old-web'
  const nativeDevice = 'book-restore-desktop'
  const grant = async (device_id: string) =>
    (
      await checked<{ token: string }>(
        await page.request.post(`${endpoint}/devices`, { headers: provisioning, data: { device_id } }),
      )
    ).token
  const checkpoint = async (token: string) =>
    checked<{ ledger_id: string; cursor: number }>(
      await page.request.get(`${endpoint}/checkpoint`, { headers: { Authorization: `Bearer ${token}` } }),
    )
  const native = (action: 'enable' | 'run', token: string) =>
    page.request.post(`${branch}/desktop-sync/${action}`, { headers, data: { origin: cloud, token } })
  const fixture = (action: 'backup' | 'restore' | 'finish') =>
    page.request.post(`${cloud}/__e2e/ledger/${action}`, { headers: provisioning })
  const nativeToken = await grant(nativeDevice)
  const token = await grant(device)
  await checked(await native('enable', nativeToken))
  await checked(await native('run', nativeToken))

  async function openOffline(web: Page) {
    await web.goto('/offline/')
    await expect.poll(() => web.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
  }
  async function enterCredentials(web: Page, name: string, key: string) {
    const work = web.getByRole('combobox', { name: '离线作品', exact: true })
    await expect(work.locator(`option[value="${novel.id}"]`)).toHaveCount(1)
    await work.selectOption(novel.id)
    const panel = web.getByText('作品云同步', { exact: true })
    await expect(panel).toBeVisible()
    await panel.click()
    await web.getByLabel('同步服务地址').fill(cloud)
    await web.getByLabel('设备编号').fill(name)
    await web.getByLabel('设备凭据').fill(key)
  }
  async function connect(web: Page, name: string, key: string) {
    await enterCredentials(web, name, key)
    await web.getByRole('button', { name: '启用作品同步', exact: true }).click()
  }
  async function syncWeb(web: Page) {
    const button = web.getByRole('button', { name: '同步当前工作版本', exact: true })
    await expect(button).toBeEnabled()
    const verifying = web.waitForResponse((response) => response.url() === `${endpoint}/checkpoint`)
    await button.click()
    expect((await verifying).ok()).toBe(true)
    await expect(button).toBeEnabled()
  }
  function settled(web: Page, cursor: number) {
    return web.getByText(`已启用 · 游标 ${cursor} · 0 条待发送或确认`, { exact: true })
  }
  function verifyBook(pack: Package) {
    const m1 = adopted(pack.project_history)
    const m3 = adopted(pack.settings_history)
    const m4 = adopted(pack.outline_history)
    expect(m1).toMatchObject({ parent_id: later.project.id, brief: { ending: old.ending } })
    expect(m3).toMatchObject({ parent_id: later.confirmation.id, project_version_id: m1.id })
    expect(m3.categories.flatMap(({ items }) => items ?? []).find(({ id }) => id === settingId)?.detail).toBe(
      old.detail,
    )
    expect(m4).toMatchObject({
      parent_id: later.confirmed.id,
      settings_version_id: m3.id,
      project_version_id: m1.id,
    })
    expect(m4.plan.summary).toBe(old.summary)
    expect(
      m4.plan.volumes?.flatMap(({ units }) =>
        units?.flatMap(({ chapters }) => chapters?.map(({ goal }) => goal)),
      ),
    ).toEqual([old.goal, old.goal])
    const heads = chapters.map(({ id }) =>
      pack.revisions.filter(({ chapter_id }) => chapter_id === id).at(-1)!,
    )
    expect(heads.map(({ content, revision }) => [content, revision])).toEqual(
      original.map((content, index) => [content, modified[index].revision + 1]),
    )
    expect(pack.chapters.find(({ id }) => id === added.id)?.archived).toBe(1)
    expect(pack.revisions.filter(({ chapter_id }) => chapter_id === added.id)).toEqual(addedHistory)
    const storyHead = pack.story_history?.adopted_heads[event.id]
    const record = pack.story_history?.versions.find(({ id }) => id === storyHead)
    expect(record?.record.title).toBe(oldEventTitle)
    const proof = record?.evidence.record?.[0]
    expect(proof).toMatchObject({ revision_id: heads[1].id, quote_sha256: hash(oldQuote) })
    expect(heads[1].content.slice(proof?.start, proof?.end)).toBe(oldQuote)
    expect(heads[1].sha256).toBe(hash(original[1]))
    return [m1.id, m3.id, m4.id, storyHead, ...heads.map(({ id }) => id)]
  }

  async function openLinked(context: BrowserContext) {
    const web = await context.newPage()
    await openOffline(web)
    await web.getByLabel('从独立备份恢复作品', { exact: true }).setInputFiles({
      name: '暮鼓整书旧恢复点.ainovel-backup',
      mimeType: 'application/octet-stream',
      buffer: backup,
    })
    await expect(web.getByText('已校验并恢复独立作品备份。', { exact: true })).toBeVisible()
    await connect(web, device, token)
    await syncWeb(web)
    await expect.poll(async () => settled(web, (await checkpoint(token)).cursor).isVisible()).toBe(true)
    await checked(await native('run', nativeToken))
    const common = await checkpoint(token)
    return { web, common }
  }
  async function restoreOnDesktop() {
    const path = `${branch}/work-snapshots/${snapshot.id}`
    const preview = await get<Schema['SnapshotRestorePreview']>(`${path}/preview`)
    expect(preview.in_place_blockers).toEqual([])
    const result = await post<{ recovery_snapshot_id: string }>(`${path}/restore`, {
      mode: 'in_place',
      basis_sha256: preview.basis_sha256,
      idempotency_key: randomUUID(),
    })
    expect(result.recovery_snapshot_id).toBeTruthy()
  }
  return {
    added,
    chapterTitles,
    checkpoint,
    connect,
    device,
    empty,
    endpoint,
    enterCredentials,
    first,
    fixture,
    get,
    grant,
    later,
    modified,
    native,
    nativeDevice,
    nativeToken,
    openLinked,
    openOffline,
    packPath,
    restoreOnDesktop,
    save,
    settled,
    snapshot,
    syncWeb,
    token,
    verifyBook,
  }
}

test('整书原位回滚跨端采用后，替换云账本保留整组历史并阻止旧连接写入', async ({ page, browser, baseURL }) => {
  test.setTimeout(120_000)
  const {
    chapterTitles,
    checkpoint,
    connect,
    device,
    empty,
    endpoint,
    first,
    fixture,
    get,
    grant,
    later,
    modified,
    native,
    nativeDevice,
    nativeToken,
    openLinked,
    openOffline,
    packPath,
    save,
    settled,
    snapshot,
    syncWeb,
    token,
    verifyBook,
  } = await prepareBook(page)

  const current = await browser.newContext({ baseURL, viewport: { width: 420, height: 900 } })
  const replica = await browser.newContext({ baseURL })
  try {
    const { web, common } = await openLinked(current)

    await current.setOffline(true)
    const history = web.getByLabel('已导入的卷书恢复点', { exact: true })
    await history.getByText('已导入的卷书恢复点', { exact: true }).press('Enter')
    await history.getByRole('combobox', { name: '选择已归档恢复点', exact: true }).selectOption(snapshot.id)
    await history.getByRole('button', { name: '预览归档原位恢复', exact: true }).press('Enter')
    const preview = history.getByRole('region', { name: '归档原位恢复预览', exact: true })
    await expect(preview.getByRole('heading', { name: '原位恢复「旧暮鼓整书」' })).toBeFocused()
    const apply = preview.getByRole('button', { name: '原位恢复为新修订', exact: true })
    await expect(apply).toBeDisabled()
    await preview.getByRole('checkbox', { name: '我已核对恢复影响', exact: true }).check()
    await apply.press('Enter')
    await expect(history.getByRole('status').filter({ hasText: '已恢复为新的当前修订' })).toBeVisible()
    const restoredIds = verifyBook(await exportPackage(web))
    expect(await checkpoint(token)).toEqual(common)
    expect(adopted((await get<Package>(packPath)).project_history).id).toBe(later.project.id)

    await current.setOffline(false)
    await syncWeb(web)
    await expect.poll(async () => (await checkpoint(token)).cursor).toBeGreaterThan(common.cursor)
    const desktopApplied = await checked<{ conflicts: unknown[]; deferred: unknown[]; pending: number }>(
      await native('run', nativeToken),
    )
    expect(desktopApplied).toMatchObject({ conflicts: [], deferred: [], pending: 0 })
    expect(verifyBook(await get<Package>(packPath))).toEqual(restoredIds)
    const recovered = await checkpoint(token)
    await syncWeb(web)
    await expect(settled(web, recovered.cursor)).toBeVisible()
    await checked(await fixture('backup'))

    const nativeLater = '桌面在旧账本继续补写北岸正文。'
    await save({ ...modified[0], revision: modified[0].revision + 1 }, nativeLater)
    await checked(await native('run', nativeToken))
    const beforeReplace = await checkpoint(token)
    expect(beforeReplace.cursor).toBeGreaterThan(recovered.cursor)
    await syncWeb(web)
    await expect(settled(web, beforeReplace.cursor)).toBeVisible()
    await web.getByRole('button', { name: `2. ${chapterTitles[1]}`, exact: true }).click()
    const editor = web.getByLabel('章节正文')
    const pending = '独立 Web 在换账本前保存的第二章。'
    await editor.fill(pending)
    await web.getByRole('button', { name: '保存正文到浏览器', exact: true }).press('Enter')
    await expect(
      web.getByText(`本地已保存 · 修订 ${modified[1].revision + 2}`, { exact: true }),
    ).toBeVisible()

    await checked(await fixture('restore'))
    expect([401, 403]).toContain(
      (
        await page.request.get(`${endpoint}/checkpoint`, { headers: { Authorization: `Bearer ${token}` } })
      ).status(),
    )
    const freshNativeToken = await grant(nativeDevice)
    const rotated = await grant(device)
    const replaced = await checkpoint(rotated)
    expect(replaced).toMatchObject({ cursor: recovered.cursor })
    expect(replaced.ledger_id).not.toBe(recovered.ledger_id)
    const rejected = await native('run', freshNativeToken)
    expect(rejected.status()).toBe(409)
    expect(await rejected.json()).toMatchObject({ code: 'sync_ledger_replaced' })
    expect((await get<Chapter>(`/api/chapters/${first.id}`)).content).toBe(nativeLater)
    await web.getByLabel('设备凭据').fill(rotated)
    await syncWeb(web)
    await expect(web.getByRole('alert').filter({ hasText: '同步历史不一致' })).toContainText(
      '本机稿和待同步记录已保留',
    )
    await expect(editor).toHaveValue(pending)
    expect(await checkpoint(rotated)).toEqual(replaced)

    const fresh = await replica.newPage()
    await openOffline(fresh)
    await fresh.getByLabel('导入新离线作品').setInputFiles({
      name: '整书空基线.json',
      mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify(empty)),
    })
    const freshDevice = 'book-restore-new-web'
    const freshToken = await grant(freshDevice)
    await connect(fresh, freshDevice, freshToken)
    await syncWeb(fresh)
    await expect(settled(fresh, replaced.cursor)).toBeVisible()
    await expect(fresh.getByRole('button', { name: /后加的问询/ })).toHaveCount(0)
    expect(verifyBook(await exportPackage(fresh))).toEqual(restoredIds)
    await syncWeb(fresh)
    await expect(settled(fresh, replaced.cursor)).toBeVisible()
    expect(verifyBook(await exportPackage(fresh))).toEqual(restoredIds)
    expect(await checkpoint(freshToken)).toEqual(replaced)
  } finally {
    await current.close()
    await replica.close()
    expect((await fixture('finish')).status()).toBe(204)
  }
})

test('桌面整书原位回滚后，Web 分页断开及离线重开保留当前版本，续传后采用正式修订', async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(120_000)
  const {
    added,
    checkpoint,
    device,
    endpoint,
    enterCredentials,
    get,
    later,
    native,
    nativeToken,
    openLinked,
    packPath,
    restoreOnDesktop,
    settled,
    syncWeb,
    token,
    verifyBook,
  } = await prepareBook(page)
  const context = await browser.newContext({ baseURL })
  try {
    const { web, common } = await openLinked(context)
    await restoreOnDesktop()
    const restoredIds = verifyBook(await get<Package>(packPath))
    const sent = await checked<{ conflicts: unknown[]; deferred: unknown[]; pending: number }>(
      await native('run', nativeToken),
    )
    expect(sent).toMatchObject({ conflicts: [], deferred: [], pending: 0 })
    const published = await checkpoint(token)
    expect(published.ledger_id).toBe(common.ledger_id)
    expect(published.cursor).toBeGreaterThan(common.cursor + 1)

    const pulls: number[] = []
    const partial = common.cursor + 1
    await web.route(`${endpoint}/pull?*`, async (route) => {
      const url = new URL(route.request().url())
      pulls.push(Number(url.searchParams.get('after')))
      if (pulls.length === 1) {
        url.searchParams.set('limit', '1')
        const response = await route.fetch({ url: url.toString() })
        const result = await checked<{ cursor: number; has_more: boolean; changes: { object_id: string }[] }>(
          response,
        )
        expect(result).toMatchObject({
          cursor: partial,
          has_more: true,
          changes: [{ object_id: expect.stringMatching(/^revision:/) }],
        })
        await route.fulfill({ response })
      } else if (pulls.length === 2) {
        await route.abort('connectionreset')
      } else {
        await route.continue()
      }
    })
    const waiting = web.getByText('已收到云端更新，1 项待应用到本机。当前正式稿可继续查看。', { exact: true })
    async function verifyPrevious() {
      const prior = await exportPackage(web)
      expect(adopted(prior.project_history).id).toBe(later.project.id)
      expect(adopted(prior.settings_history).id).toBe(later.confirmation.id)
      expect(adopted(prior.outline_history).id).toBe(later.confirmed.id)
      expect(prior.chapters.find(({ id }) => id === added.id)?.archived).toBe(0)
      expect(
        prior.revisions.filter(({ content }) => rewritten.includes(content)).map(({ content }) => content),
      ).toEqual(rewritten)
    }

    await syncWeb(web)
    expect(pulls).toEqual([common.cursor, partial])
    await expect(waiting).toBeVisible()
    await verifyPrevious()
    await context.setOffline(true)
    await web.reload()
    await enterCredentials(web, device, token)
    await expect(waiting).toBeVisible()
    await verifyPrevious()

    await context.setOffline(false)
    await syncWeb(web)
    expect(pulls[2]).toBe(partial)
    await expect(settled(web, (await checkpoint(token)).cursor)).toBeVisible()
    await expect(waiting).toHaveCount(0)
    expect(verifyBook(await exportPackage(web))).toEqual(restoredIds)
  } finally {
    await context.close()
  }
})
