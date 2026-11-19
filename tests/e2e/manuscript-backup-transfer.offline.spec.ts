import {
  expect,
  test,
  type APIRequestContext,
  type APIResponse,
  type BrowserContext,
  type Page,
} from '@playwright/test'
import { spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import { copyFile, mkdir, stat, truncate } from 'node:fs/promises'
import type { LocalRevision } from '../../frontend/offlineStore'

const title = '渡口风铃 · 跨端长篇备份'
const chapterCount = 36
const exchangeLimit = 24 * 1024 * 1024
const lead = '😀学徒记录了渡口第一次铃声。'
const body = '潮退后，行人顺着石阶重新查看铜铃的来处。'.repeat(14_000).slice(0, 249_900)
const chapterName = (number: number) => `渡口试稿 ${number}`
const chapterText = (number: number) => `${lead}\n第${number}章开场。\n${body}\n第${number}章留档。`
const digest = (text: string) => createHash('sha256').update(text, 'utf8').digest('hex')
const targetServer = `
import socket, sys
from pathlib import Path
from threading import Thread
import uvicorn
from ainovel.api import create_app
from ainovel.settings import CAPS, Settings

with socket.socket() as listener:
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    limits = {kind: maximum for kind, (_, maximum) in CAPS.items()}
    settings = Settings(Path(sys.argv[1]), {}, limits, test_mode=True, port=port)
    server = uvicorn.Server(uvicorn.Config(create_app(settings), host='127.0.0.1', port=port, access_log=False))

    def stop():
        sys.stdin.buffer.read()
        server.should_exit = True

    Thread(target=stop, daemon=True).start()
    print(port, flush=True)
    server.run(sockets=[listener])
`

async function isolatedDesktop(directory: string, request: APIRequestContext) {
  await mkdir(directory, { recursive: true })
  const python = process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python'
  const child = spawn(python, ['-u', '-c', targetServer, directory], { windowsHide: true })
  let output = ''
  let errors = ''
  const closed = new Promise((resolve) => child.once('close', resolve))
  child.stdout.setEncoding('utf8').on('data', (text) => (output += text))
  child.stderr.setEncoding('utf8').on('data', (text) => (errors += text))
  child.on('error', (error) => (errors += error.message))
  child.stdin.on('error', (error) => (errors += error.message))
  async function stop() {
    child.stdin.end()
    const timeout = setTimeout(() => child.kill(), 5_000)
    try {
      await closed
    } finally {
      clearTimeout(timeout)
    }
  }
  try {
    await expect.poll(() => output.trim(), { timeout: 15_000 }).toMatch(/^\d+$/)
    const origin = `http://127.0.0.1:${output.trim()}`
    await expect
      .poll(
        async () => {
          try {
            const response = await request.get(`${origin}/api/config`, { timeout: 1_000 })
            const ready = response.ok()
            await response.dispose()
            return ready
          } catch {
            return false
          }
        },
        { timeout: 15_000 },
      )
      .toBe(true)
    return { origin, stop }
  } catch (error) {
    await stop()
    throw new Error(`隔离目标未启动：${String(error)}\n${errors}`, { cause: error })
  }
}

async function checked<T>(response: APIResponse): Promise<T> {
  try {
    if (!response.ok()) throw new Error(`HTTP ${response.status()}：${await response.text()}`)
    return (await response.json()) as T
  } finally {
    await response.dispose()
  }
}

type Novel = { id: string; branch_id: string }
type Chapter = { id: string; revision: number; content: string }
type Fingerprint = Pick<LocalRevision, 'id' | 'parent_id' | 'revision' | 'sha256'>
type ChapterHistory = { id: string; versions: Fingerprint[] }

async function captureHistory(api: APIRequestContext, ids: string[]) {
  const manifest: ChapterHistory[] = []
  for (const id of ids) {
    const rows = await checked<Chapter[]>(await api.get(`/api/chapters/${id}/history`))
    rows.reverse()
    manifest.push({
      id,
      versions: rows.map((row, index) => ({
        id: row.id,
        revision: row.revision,
        parent_id: rows[index - 1]?.id ?? null,
        sha256: digest(row.content),
      })),
    })
  }
  return manifest
}

async function webRevision(page: Page, chapter: string, revision: number): Promise<Fingerprint> {
  // Compare the browser's persisted IDs to the receiving API without using IndexedDB to seed the test.
  return page.evaluate(
    async ([id, number]) => {
      function wait<T>(request: IDBRequest<T>) {
        return new Promise<T>((resolve, reject) => {
          request.onsuccess = () => resolve(request.result)
          request.onerror = () => reject(request.error)
        })
      }
      const db = await wait(indexedDB.open('ainovel-offline-workspace'))
      try {
        const store = db.transaction('revisions', 'readonly').objectStore('revisions')
        const {
          id: version,
          parent_id,
          revision: numberSaved,
          sha256,
        } = (await wait(store.index('byRevision').get([id, number]))) as LocalRevision
        return { id: version, parent_id, revision: numberSaved, sha256 }
      } finally {
        db.close()
      }
    },
    [chapter, revision] as const,
  )
}

test('超过 24 MiB 的独立备份由桌面经断网 Web 返回另一套 SQLite，保留历史与来源', async ({
  page,
  browser,
  baseURL,
}, testInfo) => {
  test.setTimeout(150_000)
  await page.goto('/')
  const sourceSession = await checked<{ csrf: string; data_dir: string }>(
    await page.request.get('/api/session'),
  )
  const headers = { 'X-Ainovel-CSRF': sourceSession.csrf }
  const source = await checked<Novel>(await page.request.post('/api/novels', { headers, data: { title } }))
  const branch = `/api/branches/${source.branch_id}`
  const chapters: string[] = []
  for (let number = 1; number <= chapterCount; number++) {
    const chapter = await checked<Chapter>(
      await page.request.post(`${branch}/chapters`, { headers, data: { title: chapterName(number) } }),
    )
    chapters.push(chapter.id)
    await checked<Chapter>(
      await page.request.put(`/api/chapters/${chapter.id}`, {
        headers,
        data: { base_revision: 0, content: chapterText(number) },
      }),
    )
  }
  const original = await captureHistory(page.request, chapters)
  for (const [index, chapter] of original.entries()) {
    expect(chapter.versions.map((row) => row.revision)).toEqual([0, 1])
    expect(chapter.versions[1].sha256).toBe(digest(chapterText(index + 1)))
  }
  const firstHead = original[0].versions[1]
  const inspectionUrl = `${branch}/workflow-inspections`
  const scope = { scope: 'chapter', scope_id: chapters[0] }
  const { preview } = await checked<{ preview: { basis_sha256: string } }>(
    await page.request.get(inspectionUrl, { params: scope }),
  )
  const citation = {
    chapter_id: chapters[0],
    revision: firstHead.revision,
    start: 0,
    end: lead.length,
    quote: lead,
  }
  const frozen = await checked<{ id: string; basis_sha256: string }>(
    await page.request.post(inspectionUrl, {
      headers,
      data: {
        ...scope,
        basis_sha256: preview.basis_sha256,
        author_reviews: [
          {
            check_id: '01',
            status: 'green',
            basis_note: '按对应章稿选区校对稿件。',
            reason: '首章明确记录首次铃声。',
            evidence: [citation],
          },
        ],
      },
    }),
  )

  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(source.id)
  await page.getByText('离线写作与交换', { exact: true }).click()
  const sourceFile = testInfo.outputPath('desktop-to-web.ainovel-backup')
  const sourceDownload = page.waitForEvent('download')
  const desktopBackup = page.getByRole('button', { name: '下载独立作品备份', exact: true })
  await expect(desktopBackup).toBeEnabled()
  await desktopBackup.press('Enter')
  const downloaded = await sourceDownload
  expect(downloaded.suggestedFilename()).toMatch(/\.ainovel-backup$/)
  await downloaded.saveAs(sourceFile)
  expect((await stat(sourceFile)).size).toBeGreaterThan(exchangeLimit)

  const webFile = testInfo.outputPath('web-to-desktop.ainovel-backup')
  const webContext = await browser.newContext({ baseURL, viewport: { width: 420, height: 900 } })
  const sourceLast = original.at(-1)!
  const editedText = '末章离线续写：师姐把风铃挂回渡口。'
  let edited: Fingerprint
  try {
    const web = await webContext.newPage()
    await web.goto('/offline/')
    await expect(web.getByText('离线页面就绪', { exact: true })).toBeVisible()
    await expect.poll(() => web.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
    await webContext.setOffline(true)
    await web.reload()
    await web.getByLabel('从独立备份恢复作品', { exact: true }).setInputFiles(sourceFile)
    await expect(web.getByText('已校验并恢复独立作品备份。', { exact: true })).toBeVisible()
    await expect(web.getByRole('navigation', { name: '离线章节目录' }).getByRole('button')).toHaveCount(
      chapterCount,
    )
    const history = web.getByLabel('历次体检与完本交付', { exact: true })
    await history.getByText('历次体检与完本交付', { exact: true }).click()
    await expect(history.getByRole('combobox', { name: '选择历史报告' })).toHaveValue(frozen.id)
    await history.getByText('立项及作者约定与正文 · 绿 · 已核对', { exact: true }).click()
    await expect(history.getByRole('list', { name: '核对原文引用' })).toContainText(lead)
    await history.getByRole('button', { name: `查看来源修订 1 · ${firstHead.id.slice(0, 8)}` }).click()
    const originalText = history.getByRole('region', { name: '历史报告引用正文' })
    await expect(originalText).toContainText(firstHead.sha256)
    expect(digest((await originalText.locator('pre').textContent())!)).toBe(firstHead.sha256)
    await history.getByText('历次体检与完本交付', { exact: true }).click()
    await web
      .getByRole('button', { name: `${chapterCount}. ${chapterName(chapterCount)}`, exact: true })
      .click()
    const editor = web.getByLabel('章节正文')
    expect(digest(await editor.inputValue())).toBe(sourceLast.versions[1].sha256)
    expect(await webRevision(web, sourceLast.id, 1)).toEqual(sourceLast.versions[1])
    await editor.fill(editedText)
    await web.getByRole('button', { name: '保存正文到浏览器', exact: true }).click()
    await expect(web.getByText('本地已保存 · 修订 2', { exact: true })).toBeVisible()
    edited = await webRevision(web, sourceLast.id, 2)
    expect(edited).toMatchObject({
      revision: 2,
      parent_id: sourceLast.versions[1].id,
      sha256: digest(editedText),
    })
    const outgoing = web.waitForEvent('download')
    await web.getByRole('button', { name: '下载独立作品备份', exact: true }).click()
    await (await outgoing).saveAs(webFile)
    await expect(web.getByText('已下载独立作品备份。', { exact: true })).toBeVisible()
  } finally {
    await webContext.close()
  }
  expect((await stat(webFile)).size).toBeGreaterThan(exchangeLimit)
  const truncated = testInfo.outputPath('truncated.ainovel-backup')
  await copyFile(webFile, truncated)
  await truncate(truncated, (await stat(truncated)).size - 13)

  const target = await isolatedDesktop(testInfo.outputPath('isolated-target-sqlite'), page.request)
  let targetContext: BrowserContext | undefined
  try {
    targetContext = await browser.newContext({ baseURL: target.origin })
    const desktop = await targetContext.newPage()
    await desktop.goto('/')
    const session = await checked<{ csrf: string; data_dir: string }>(
      await desktop.request.get('/api/session'),
    )
    expect(session.data_dir).not.toBe(sourceSession.data_dir)
    const targetHeaders = { 'X-Ainovel-CSRF': session.csrf }
    const other = await checked<Novel>(
      await desktop.request.post('/api/novels', { headers: targetHeaders, data: { title: '目标已有作品' } }),
    )
    const kept = await checked<Chapter>(
      await desktop.request.post(`/api/branches/${other.branch_id}/chapters`, {
        headers: targetHeaders,
        data: { title: '原存稿' },
      }),
    )
    const saved = await checked<Chapter>(
      await desktop.request.put(`/api/chapters/${kept.id}`, {
        headers: targetHeaders,
        data: { base_revision: 0, content: '这份目标既有稿要完整保留。' },
      }),
    )
    await desktop.reload()
    const works = desktop.getByRole('combobox', { name: '作品', exact: true })
    await works.selectOption(other.id)
    await desktop.getByText('离线写作与交换', { exact: true }).click()
    const importBackup = desktop.getByLabel('导入独立作品备份', { exact: true })
    const receiptFor = () =>
      desktop.waitForResponse((response) => response.url().endsWith('/api/manuscript-backups/import'))
    const failure = receiptFor()
    await importBackup.setInputFiles(truncated)
    expect((await failure).status()).toBe(400)
    await expect(desktop.getByRole('alert')).toContainText(/备份.*(?:无效|校验|不完整)/)
    await expect(works).toHaveValue(other.id)
    const missing = await desktop.request.get(`${branch}/chapters`)
    expect(missing.status()).toBe(404)
    await missing.dispose()
    expect(await checked<Chapter>(await desktop.request.get(`/api/chapters/${kept.id}`))).toEqual(saved)

    const receipt = receiptFor()
    await importBackup.setInputFiles(webFile)
    expect((await receipt).status()).toBe(201)
    await expect(desktop.getByText('已从独立备份导入新作品。', { exact: true })).toBeVisible()
    await expect(works).toHaveValue(source.id)
    const imported = await checked<Chapter[]>(await desktop.request.get(`${branch}/chapters`))
    expect(imported.map((chapter) => chapter.id)).toEqual(chapters)
    const expected = [...original.slice(0, -1), { ...sourceLast, versions: [...sourceLast.versions, edited] }]
    expect(await captureHistory(desktop.request, chapters)).toEqual(expected)
    expect(
      await checked<LocalRevision>(await desktop.request.get(`${branch}/manuscript-sources/${edited.id}`)),
    ).toMatchObject({ ...edited, chapter_id: sourceLast.id, content: editedText })
    const report = await checked<{
      basis_sha256: string
      scope_id: string
      current: boolean
      origin: string
      chapter_bindings: { id: string; revision_id: string; sha256: string }[]
      items: { check_id: string; author_review?: { evidence: unknown[] } }[]
    }>(await desktop.request.get(`${inspectionUrl}/${frozen.id}`))
    expect(report).toMatchObject({
      basis_sha256: frozen.basis_sha256,
      scope_id: chapters[0],
      current: false,
      origin: 'manuscript_package',
    })
    expect(report.chapter_bindings).toMatchObject([
      { id: chapters[0], revision_id: firstHead.id, sha256: firstHead.sha256 },
    ])
    expect(report.items.find((row) => row.check_id === '01')?.author_review?.evidence).toEqual([citation])
    expect(await checked<Chapter>(await desktop.request.get(`/api/chapters/${kept.id}`))).toEqual(saved)
  } finally {
    await Promise.all([targetContext?.close(), target.stop()])
  }
})

test('卷和整书历史恢复点随独立备份往返，保持原编号、范围与精确章稿', async ({
  page,
  browser,
  baseURL,
}, testInfo) => {
  test.setTimeout(90_000)
  await page.goto('/')
  const { csrf } = await checked<{ csrf: string }>(await page.request.get('/api/session'))
  const headers = { 'X-Ainovel-CSRF': csrf }
  const source = await checked<Novel>(
    await page.request.post('/api/novels', { headers, data: { title: '两卷旧恢复点 · 渡口' } }),
  )
  const branch = `/api/branches/${source.branch_id}`
  async function post<T>(route: string, data: unknown) {
    return checked<T>(await page.request.post(route, { headers, data }))
  }
  async function put<T>(route: string, data: unknown) {
    return checked<T>(await page.request.put(route, { headers, data }))
  }

  const first = await post<Chapter>(`${branch}/chapters`, { title: '桥下钥匙' })
  const second = await post<Chapter>(`${branch}/chapters`, { title: '旧宅归铃' })
  const firstText = '学徒在桥下拾到三齿钥匙。'
  const secondText = '😀门内响起第二声。\r\n守铃人接过铜铃。'
  await put(`/api/chapters/${first.id}`, { base_revision: 0, content: firstText })
  const opening = await put<Chapter>(`/api/chapters/${second.id}`, { base_revision: 0, content: secondText })
  type StoryGraph = {
    story_revision: number
    records: {
      id: string
      revision: number
      payload: { title: string; sources: { chapter_id: string; quote: string }[] }
    }[]
  }
  function witnessed(title: string, revision: number, text: string, quote: string) {
    const start = text.indexOf(quote)
    return {
      kind: 'event',
      title,
      status: 'actual',
      sources: [{ chapter_id: second.id, revision, start, end: start + quote.length, quote }],
    }
  }
  const storyRoute = `${branch}/story-records`
  const initialGraph = await checked<StoryGraph>(await page.request.get(`${branch}/story-graph`))
  const oldEventTitle = '旧宅门内第二声'
  const oldQuote = '门内响起第二声'
  const oldGraph = await post<StoryGraph>(storyRoute, {
    base_story_revision: initialGraph.story_revision,
    record: witnessed(oldEventTitle, opening.revision, secondText, oldQuote),
  })
  const event = oldGraph.records.find(({ payload }) => payload.title === oldEventTitle)!
  const idea = '查清旧宅铜铃与桥下钥匙的来处。'
  await put(`${branch}/conception/project-brief`, {
    base_revision: 0,
    idempotency_key: `portable-checkpoint-project-${source.branch_id}`,
    idea,
    brief: {
      position: '归还铜铃',
      track: '线索悬疑',
      genre_ids: ['mystery'],
      audience: '悬疑读者',
      distinction: '按可回查的原句解开线索',
      length: '两卷各一章',
      ending: '铜铃交回原主。',
    },
  })
  const conception = `${branch}/conception`
  type SettingDraft = {
    revision: number
    story_revision: number
    categories: { id: string; items: unknown[] }[]
  }
  const setup = await checked<SettingDraft>(await page.request.get(conception))
  const persons = setup.categories.find(({ id }) => id === 'characters')!
  persons.items = [{ id: 'keeper', title: '守铃人', detail: '负责接收旧宅的铜铃。' }]
  const savedSetup = await put<SettingDraft>(conception, {
    base_revision: setup.revision,
    base_story_revision: setup.story_revision,
    categories: setup.categories,
  })
  let adopted = await post<{ confirmation: { id: string } }>(conception + '/confirm', {
    base_revision: savedSetup.revision,
    base_story_revision: savedSetup.story_revision,
    idempotency_key: `portable-checkpoint-settings-${source.branch_id}`,
  })

  const route = `${branch}/outline-workspace`
  const outline = {
    summary: '找到钥匙，带铜铃去旧宅。',
    volumes: [first, second].map(({ id }, index) => ({
      id: `portable-vol-${index + 1}`,
      title: index === 0 ? '拾钥卷' : '归铃卷',
      summary: '核对并执行卷内线索。',
      units: [
        {
          id: `portable-unit-${index + 1}`,
          title: '行动',
          summary: '找到线索。',
          chapters: [
            {
              chapter_id: id,
              goal: '核对旧物',
              obstacle: '来路不明',
              turn: '确认原主人',
              result: '完成交接',
              setting_ids: ['keeper'],
            },
          ],
        },
      ],
    })),
  }
  async function confirmOutline(base: number) {
    const draft = await put<{ draft: { revision: number } }>(route, {
      base_revision: base,
      confirmation_id: adopted.confirmation.id,
      plan: outline,
    })
    return post<{ confirmed: { revision: number } }>(route + '/confirm', {
      base_revision: draft.draft.revision,
    })
  }
  const earlierPlan = await confirmOutline(0)
  const inspections = `${branch}/workflow-inspections`
  const { preview } = await checked<{ preview: { basis_sha256: string } }>(
    await page.request.get(inspections),
  )
  const originalReport = await post<{ id: string }>(inspections, { basis_sha256: preview.basis_sha256 })
  const snapshots = `${branch}/work-snapshots`
  type Snapshot = { id: string; manifest_sha256: string }
  const volume = await post<Snapshot>(snapshots, {
    label: '末卷旧恢复点',
    scope: 'volume',
    scope_id: 'portable-vol-2',
  })
  const book = await post<Snapshot>(snapshots, { label: '整书旧恢复点', scope: 'book' })
  const secret = '未采用设定草稿只留本机。'
  const state = await checked<SettingDraft>(await page.request.get(conception))
  state.categories
    .find(({ id }) => id === 'characters')!
    .items.push({ id: 'scratch', title: '待确认稿', detail: secret, adoption_state: 'pending' })
  await put(conception, {
    base_revision: state.revision,
    base_story_revision: state.story_revision,
    categories: state.categories,
  })
  const pending = await post<Snapshot>(snapshots, { label: '含未确认编辑的恢复点', scope: 'book' })
  const rewritten = '当前稿决定明天归还铜铃。'
  const updated = await put<Chapter>(`/api/chapters/${second.id}`, { base_revision: 1, content: rewritten })
  const latestGraph = await checked<StoryGraph>(await page.request.get(`${branch}/story-graph`))
  const currentTitle = '改到明天归铃'
  await put(`${storyRoute}/${event.id}`, {
    base_story_revision: latestGraph.story_revision,
    base_revision: event.revision,
    record: witnessed(currentTitle, updated.revision, rewritten, '明天归还铜铃'),
  })
  const withPending = await checked<SettingDraft>(await page.request.get(conception))
  adopted = await post<{ confirmation: { id: string } }>(conception + '/confirm', {
    base_revision: withPending.revision,
    base_story_revision: withPending.story_revision,
    idempotency_key: `portable-checkpoint-settings-reconfirmed-${source.branch_id}`,
  })
  outline.volumes[1].summary = '修改后的卷安排。'
  await confirmOutline(earlierPlan.confirmed.revision)

  type Entry = { id: string; body: string; sha256: string }
  type Projection = {
    source_manifest_sha256: string
    scoped_chapter_ids: string[]
    reports: { workflow_inspection?: string }
    shared: Record<string, string | null>
    restore_sources?: { story_heads: Record<string, { revision: number }> }
  }
  type Pack = { snapshot_history: { entries: Entry[] } }
  const pack = await checked<Pack>(await page.request.get(`${branch}/manuscript-package`))
  const pendingArchive = pack.snapshot_history.entries.find(({ id }) => id === pending.id)
  expect(pendingArchive?.body).toBeDefined()
  expect(pendingArchive?.body).not.toContain(secret)
  const archived = [volume, book].map((point) => {
    const entry = pack.snapshot_history.entries.find(({ id }) => id === point.id)!
    expect(entry.sha256).toBe(digest(entry.body))
    expect(entry.body).not.toContain(secret)
    const body: Projection = JSON.parse(entry.body)
    expect(body.source_manifest_sha256).toBe(point.manifest_sha256)
    expect(body.reports.workflow_inspection).toBe(originalReport.id)
    expect(Object.values(body.shared).every(Boolean)).toBe(true)
    expect(body.restore_sources?.story_heads[event.id]?.revision).toBe(event.revision)
    return { entry, body }
  })
  expect(archived.map(({ body }) => body.scoped_chapter_ids)).toEqual([[second.id], [first.id, second.id]])

  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(source.id)
  await page.getByText('离线写作与交换', { exact: true }).click()
  const sourceFile = testInfo.outputPath('portable-snapshot-source.ainovel-backup')
  const sourceBackup = page.getByRole('button', { name: '下载独立作品备份', exact: true })
  await expect(sourceBackup).toBeEnabled()
  const download = page.waitForEvent('download', { timeout: 15_000 })
  await sourceBackup.press('Enter')
  await (await download).saveAs(sourceFile)

  const webFile = testInfo.outputPath('portable-snapshot-web.ainovel-backup')
  const webContext = await browser.newContext({ baseURL, viewport: { width: 320, height: 900 } })
  try {
    const web = await webContext.newPage()
    await web.goto('/offline/')
    await expect(web.getByText('离线页面就绪', { exact: true })).toBeVisible()
    await expect.poll(() => web.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
    await webContext.setOffline(true)
    await web.reload()
    await web.getByLabel('从独立备份恢复作品', { exact: true }).setInputFiles(sourceFile)
    await expect(web.getByText('已校验并恢复独立作品备份。', { exact: true })).toBeVisible()
    const history = web.getByLabel('已导入的卷书恢复点', { exact: true })
    await history.getByText('已导入的卷书恢复点', { exact: true }).press('Enter')
    const picker = history.getByRole('combobox', { name: '选择已归档恢复点', exact: true })
    const chapters = history.getByRole('list', { name: '恢复点章节', exact: true })
    await picker.selectOption(volume.id)
    await expect(chapters.getByRole('button')).toHaveCount(1)
    await chapters.getByRole('button', { name: '查看「旧宅归铃」原稿', exact: true }).press('Enter')
    const preserved = history.getByRole('region', { name: '归档恢复点原稿', exact: true })
    await expect(preserved).toContainText(digest(secondText))
    expect(await preserved.locator('pre').textContent()).toBe(secondText)
    await picker.selectOption(book.id)
    await expect(chapters.getByRole('button')).toHaveCount(2)
    await expect(preserved).toHaveCount(0)
    await chapters.getByRole('button', { name: '查看「桥下钥匙」原稿', exact: true }).press('Enter')
    await expect(preserved).toContainText(firstText)
    expect(await web.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)
    const webBackup = web.getByRole('button', { name: '下载独立作品备份', exact: true })
    await expect(webBackup).toBeEnabled()
    const outgoing = web.waitForEvent('download', { timeout: 15_000 })
    await webBackup.press('Enter')
    await (await outgoing).saveAs(webFile)
  } finally {
    await webContext.close()
  }

  const target = await isolatedDesktop(testInfo.outputPath('portable-snapshot-target'), page.request)
  let context: BrowserContext | undefined
  try {
    context = await browser.newContext({ baseURL: target.origin, viewport: { width: 420, height: 900 } })
    const desktop = await context.newPage()
    await desktop.goto('/')
    await desktop.getByText('离线写作与交换', { exact: true }).click()
    const input = desktop.getByLabel('导入独立作品备份', { exact: true })
    const applied = desktop.waitForResponse((response) =>
      response.url().endsWith('/api/manuscript-backups/import'),
    )
    await input.setInputFiles(webFile)
    const response = await applied
    expect(response.status()).toBe(201)
    await expect(desktop.getByText('已从独立备份导入新作品。', { exact: true })).toBeVisible()
    const resulting = await checked<Pack>(await desktop.request.get(`${branch}/manuscript-package`))
    for (const { entry } of archived)
      expect(resulting.snapshot_history.entries.find(({ id }) => id === entry.id)).toEqual(entry)
    expect((await checked<Chapter>(await desktop.request.get(`/api/chapters/${second.id}`))).content).toBe(
      rewritten,
    )
    expect((await desktop.request.get(`${snapshots}/${volume.id}/preview`)).status()).toBe(404)
    await desktop.getByRole('button', { name: '故事与审阅', exact: true }).click()
    const panel = desktop.getByRole('region', { name: '作品快照与回滚', exact: true })
    await panel.getByText('跨端检查点归档', { exact: true }).press('Enter')
    await panel.getByRole('combobox', { name: '已归档检查点', exact: true }).selectOption(volume.id)
    await panel.getByRole('combobox', { name: '核对原稿章节', exact: true }).selectOption(second.id)
    const original = panel.getByRole('region', { name: '归档章稿对照', exact: true })
    await expect(original).toContainText('已核验归档原稿')
    expect(await original.locator('pre').first().textContent()).toBe(secondText)

    await panel.getByRole('button', { name: '预览归档另存', exact: true }).press('Enter')
    const fork = panel.getByRole('region', { name: '归档另存预览', exact: true })
    await expect(fork.getByRole('heading', { name: '从「末卷旧恢复点」另存工作分支' })).toBeFocused()
    await expect(fork).toContainText('2 章')
    await expect(fork).toContainText('1 条故事记录')
    expect(await desktop.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(420)
    const branchName = '从归铃卷旧点恢复'
    await fork.getByRole('textbox', { name: '恢复分支名称', exact: true }).fill(branchName)
    const confirm = fork.getByRole('button', { name: '建立恢复分支', exact: true })
    await expect(confirm).toBeDisabled()
    await fork.getByRole('checkbox', { name: '我已核对另存范围', exact: true }).check()
    await expect(confirm).toBeEnabled()
    await confirm.press('Enter')

    const selectedBranch = desktop.getByRole('combobox', { name: '工作版本', exact: true })
    await expect(selectedBranch.locator('option:checked')).toHaveText(branchName)
    const newBranchId = await selectedBranch.inputValue()
    expect(newBranchId).not.toBe(source.branch_id)
    const recoveredDirectory = await checked<Chapter[]>(
      await desktop.request.get(`/api/branches/${newBranchId}/chapters`),
    )
    const recovered = await Promise.all(
      recoveredDirectory.map(async ({ id }) =>
        checked<Chapter>(await desktop.request.get(`/api/chapters/${id}`)),
      ),
    )
    expect(recovered.map(({ title, content }) => [title, content])).toEqual([
      ['桥下钥匙', firstText],
      ['旧宅归铃', secondText],
    ])
    const forkedStory = await checked<StoryGraph>(
      await desktop.request.get(`/api/branches/${newBranchId}/story-graph`),
    )
    expect(forkedStory.records[0].payload).toMatchObject({
      title: oldEventTitle,
      sources: [{ chapter_id: recovered[1].id, quote: oldQuote }],
    })
    const sourceStory = await checked<StoryGraph>(await desktop.request.get(`${branch}/story-graph`))
    expect(sourceStory.records[0].payload.title).toBe(currentTitle)
    expect((await checked<Chapter>(await desktop.request.get(`/api/chapters/${second.id}`))).content).toBe(
      rewritten,
    )
  } finally {
    await Promise.all([context?.close(), target.stop()])
  }
})
