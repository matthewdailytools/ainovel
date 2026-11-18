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
