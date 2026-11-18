import { chromium, expect, test, type Page } from '@playwright/test'
import { randomBytes } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import type { AddressInfo } from 'node:net'
import { resolve } from 'node:path'

async function createWorkspace(page: Page) {
  await page.goto('/offline/')
  await page.getByLabel('新作品名').fill('遗铃 · 离线版')
  await page.getByRole('button', { name: '本地新建作品' }).click()
  await page.getByLabel('新章节名').fill('门外拾铃')
  await page.getByRole('button', { name: '本地新建章节' }).click()
  await expect(page.getByRole('textbox', { name: '章节正文' })).toHaveValue('')
}

async function selectManuscript(page: Page) {
  await page.getByRole('combobox', { name: '离线作品', exact: true }).selectOption({ label: '遗铃 · 离线版' })
  await page.getByRole('button', { name: '1. 门外拾铃', exact: true }).click()
}

test('离线重开可写，双标签冲突留稿，清理浏览器后从交换包恢复', async ({ page, context }) => {
  await createWorkspace(page)
  await expect(page.getByText('离线页面就绪', { exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
  await page.close()
  await context.setOffline(true)

  const author = await context.newPage()
  const other = await context.newPage()
  await Promise.all([author.goto('/offline/'), other.goto('/offline/')])
  await Promise.all([selectManuscript(author), selectManuscript(other)])
  const original = '捡起不响的铜铃。😀'
  const conflicting = '第二窗口保留的线索。'
  await author.getByLabel('章节正文').fill(original)
  await author.getByRole('button', { name: '保存正文到浏览器' }).click()
  await expect(author.getByRole('status').filter({ hasText: /^本地已保存 · 修订 1$/ })).toBeVisible()
  await other.getByLabel('章节正文').fill(conflicting)
  await other.getByRole('button', { name: '保存正文到浏览器' }).click()
  await expect(other.getByRole('alert')).toContainText('本章已有新修订')
  await expect(other.getByLabel('章节正文')).toHaveValue(conflicting)
  await expect(other.getByRole('region', { name: '并发稿件' })).toContainText(original)
  await other.getByRole('button', { name: '保留我的输入，改用新基线' }).click()
  await other.getByRole('button', { name: '保存正文到浏览器' }).click()
  await expect(other.getByRole('status').filter({ hasText: /^本地已保存 · 修订 2$/ })).toBeVisible()
  const started = other.waitForEvent('download')
  await other.getByRole('button', { name: '导出稿件交换包' }).click()
  const file = await started
  const bytes = await readFile((await file.path())!)
  const pack = JSON.parse(bytes.toString('utf8'))
  expect(pack.revisions.map((item: { content: string }) => item.content)).toEqual(['', original, conflicting])

  await context.setOffline(false)
  const { csrf } = await (await other.request.get('/api/session')).json()
  const imported = await other.request.post('/api/manuscript-packages/import', {
    headers: { 'X-Ainovel-CSRF': csrf },
    data: pack,
  })
  expect(imported.ok(), await imported.text()).toBe(true)
  const fromDesktop = await (
    await other.request.get(`/api/branches/${pack.branch.id}/manuscript-package`)
  ).json()
  expect(fromDesktop).toEqual(pack)
  await context.setOffline(true)
  await author.close()
  await other.evaluate(
    () =>
      new Promise<void>((resolve, reject) => {
        const request = indexedDB.deleteDatabase('ainovel-offline-workspace')
        request.onsuccess = () => resolve()
        request.onerror = () => reject(request.error)
        request.onblocked = () => reject(new Error('Offline database unexpectedly blocked'))
      }),
  )
  await other.reload()
  await expect(
    other.getByRole('combobox', { name: '离线作品', exact: true }).getByRole('option'),
  ).toHaveCount(1)
  await other.getByLabel('导入新离线作品').setInputFiles({
    name: '遗铃.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(fromDesktop)),
  })
  await expect(other.getByRole('status').filter({ hasText: /^已校验并导入稿件及历史。$/ })).toBeVisible()
  await other.getByRole('button', { name: '1. 门外拾铃', exact: true }).click()
  await expect(other.getByLabel('章节正文')).toHaveValue(conflicting)
  await other.getByText('本章版本历史').click()
  await other.getByLabel('历史修订').selectOption({ label: '修订 1 · 作者编辑' })
  await expect(other.locator('pre')).toHaveText(original)
  const again = other.waitForEvent('download')
  await other.getByRole('button', { name: '导出稿件交换包' }).click()
  const roundTrip = await again
  expect(JSON.parse(await readFile((await roundTrip.path())!, 'utf8'))).toEqual(pack)
})

test('离线打开随稿体检依据，改稿后标明来源版本', async ({ page, context }) => {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const created = await page.request.post('/api/novels', {
    headers,
    data: { title: '遗铃 · 随稿依据' },
  })
  expect(created.ok()).toBe(true)
  const { branch_id } = await created.json()
  const prefix = `/api/branches/${branch_id}`
  const chapter = await page.request.post(prefix + '/chapters', { headers, data: { title: '拾铃' } })
  const { id } = await chapter.json()
  const saved = await page.request.put('/api/chapters/' + id, {
    headers,
    data: { base_revision: 0, content: '学徒捡起铜铃。' },
  })
  expect(saved.ok()).toBe(true)
  const route = prefix + '/workflow-inspections'
  const { preview } = await (await page.request.get(route)).json()
  const checked = await page.request.post(route, { headers, data: { basis_sha256: preview.basis_sha256 } })
  expect(checked.ok()).toBe(true)
  const pack = await (await page.request.get(prefix + '/manuscript-package')).body()
  await page.goto('/offline/')
  await expect(page.getByText('离线页面就绪', { exact: true })).toBeVisible()
  await context.setOffline(true)
  await page.getByLabel('导入新离线作品').setInputFiles({
    name: '遗铃.json',
    mimeType: 'application/json',
    buffer: pack,
  })
  await page.getByText('十处对账体检', { exact: true }).click()
  const evidence = page.getByRole('region', { name: '随稿依据' })
  await expect(evidence.getByText('依据快照 · 只读')).toBeVisible()
  await expect(evidence.locator('pre')).toContainText('章纲必达与章稿')
  await page.getByRole('button', { name: '1. 拾铃', exact: true }).click()
  await page.getByRole('textbox', { name: '章节正文', exact: true }).fill('师姐捡起铜铃。')
  await page.getByRole('button', { name: '保存正文到浏览器', exact: true }).click()
  await expect(evidence.getByText('已有章稿改版，以下为来源版本的只读记录。')).toBeVisible()
})

test('浏览器返回容量错误时保留当前输入与上一保存修订', async ({ page, context }) => {
  await createWorkspace(page)
  await page.evaluate(() => {
    const append = IDBObjectStore.prototype.add
    IDBObjectStore.prototype.add = function (value, key) {
      if (this.name === 'revisions')
        throw new DOMException('Injected storage quota failure', 'QuotaExceededError')
      return append.call(this, value, key)
    }
  })
  const editor = page.getByLabel('章节正文')
  const text = '空间不足时保留这段正文。'
  await editor.fill(text)
  await page.getByRole('button', { name: '保存正文到浏览器' }).click()
  await expect(page.getByRole('alert')).toContainText('空间不足')
  await expect(editor).toHaveValue(text)
  await expect(page.getByText('修订 0 · 有未保存输入', { exact: true })).toBeVisible()
  const reopen = await context.newPage()
  await reopen.goto('/offline/')
  await selectManuscript(reopen)
  await expect(reopen.getByLabel('章节正文')).toHaveValue('')
})

test('Chromium 实际存储配额耗尽时正式章稿与待存输入仍可取回', async ({ browserName }) => {
  test.skip(browserName !== 'chromium', '按站点覆盖配额使用 Chromium DevTools Protocol。')
  const html = await readFile(resolve('dist/offline/index.html'))
  const server = createServer((request, response) => {
    const path = request.url || ''
    if (path === '/') {
      response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }).end('<!doctype html>')
    } else if (path === '/offline/') {
      response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }).end(html)
    } else if (/^\/assets\/[\w.-]+\.(?:css|js)$/.test(path)) {
      void readFile(resolve('dist', path.slice(1))).then(
        (bytes) =>
          response
            .writeHead(200, { 'Content-Type': path.endsWith('.css') ? 'text/css' : 'text/javascript' })
            .end(bytes),
        () => response.writeHead(404).end(),
      )
    } else {
      response.writeHead(404).end()
    }
  })
  await new Promise<void>((done) => server.listen(0, '127.0.0.1', done))
  const { port } = server.address() as AddressInfo
  const origin = `http://127.0.0.1:${port}`
  try {
    const isolated = await chromium.launchPersistentContext('', {
      baseURL: origin,
      serviceWorkers: 'block',
      args: ['--disable-features=StaticStorageQuota'],
    })
    try {
      const page = isolated.pages()[0]
      await page.goto('/')
      const cdp = await isolated.newCDPSession(page)
      const quotaSize = 128 * 1024
      // Override before the first write so IndexedDB cannot reuse cached quota.
      await cdp.send('Storage.overrideQuotaForOrigin', { origin, quotaSize })
      await expect
        .poll(async () => (await page.evaluate(() => navigator.storage.estimate())).quota)
        .toBe(quotaSize)
      await createWorkspace(page)
      const editor = page.getByLabel('章节正文')
      const saved = '学徒先把铜铃放在窗台上。'
      await editor.fill(saved)
      await page.getByRole('button', { name: '保存正文到浏览器' }).click()
      await expect(page.getByText('本地已保存 · 修订 1', { exact: true })).toBeVisible()
      const estimate = await page.evaluate(() => navigator.storage.estimate())
      expect(estimate.quota).toBe(quotaSize)
      expect(estimate.usage).toBeGreaterThan(0)
      expect(estimate.usage).toBeLessThan(quotaSize)

      const pending = '尚未保存的手稿：' + randomBytes(150_000).toString('base64')
      await editor.fill(pending)
      await page.getByRole('button', { name: '保存正文到浏览器' }).click()
      await expect(page.getByRole('alert').filter({ hasText: '浏览器空间不足，保存失败' })).toBeVisible()
      await expect(editor).toHaveValue(pending)
      await expect(page.getByText('修订 1 · 有未保存输入', { exact: true })).toBeVisible()
      const draftStarted = page.waitForEvent('download')
      await page.getByRole('button', { name: '下载当前草稿' }).click()
      const draft = await draftStarted
      expect(await readFile((await draft.path())!, 'utf8')).toBe(pending)

      const reopened = await isolated.newPage()
      await reopened.goto('/offline/')
      await selectManuscript(reopened)
      await expect(reopened.getByLabel('章节正文')).toHaveValue(saved)
      const packStarted = reopened.waitForEvent('download')
      await reopened.getByRole('button', { name: '导出稿件交换包' }).click()
      const pack = await packStarted
      const portable = JSON.parse(await readFile((await pack.path())!, 'utf8'))
      expect(portable.revisions.map((row: { content: string }) => row.content)).toEqual(['', saved])
    } finally {
      await isolated.close()
    }
  } finally {
    server.closeAllConnections()
    await new Promise<void>((done) => server.close(() => done()))
  }
})

test('离线重开可恢复暂存输入，同章保存稿更新时先比较再采用新基线', async ({ page, context }) => {
  await createWorkspace(page)
  await context.setOffline(true)
  const own = '只在门内才听到那段铃声。'
  await page.getByLabel('章节正文').fill(own)
  await expect(page.getByText('输入已暂存为草稿。', { exact: true })).toBeVisible()
  const other = await context.newPage()
  await other.goto('/offline/')
  await selectManuscript(other)
  await other.getByLabel('章节正文').fill('另一窗口先写下了保存稿。')
  await other.getByRole('button', { name: '保存正文到浏览器' }).click()
  await expect(other.getByText('本地已保存 · 修订 1', { exact: true })).toBeVisible()
  await page.close()
  const reopened = await context.newPage()
  await reopened.goto('/offline/')
  await selectManuscript(reopened)
  const editor = reopened.getByLabel('章节正文')
  await expect(editor).toHaveValue('另一窗口先写下了保存稿。')
  await reopened.getByText('有其他窗口暂存的草稿', { exact: true }).press('Enter')
  await reopened.getByRole('button', { name: '载入暂存草稿' }).click()
  await expect(editor).toHaveValue(own)
  await expect(reopened.getByRole('button', { name: '保存正文到浏览器' })).toBeDisabled()
  await reopened.getByRole('button', { name: '保留我的输入，改用新基线' }).click()
  await reopened.getByRole('button', { name: '保存正文到浏览器' }).click()
  await expect(reopened.getByText('本地已保存 · 修订 2', { exact: true })).toBeVisible()
})

test('浏览器存储升级中断时原稿完整，重试可读回', async ({ page }) => {
  const title = '遗铃 · 存储升级'
  const body = '铃本来就刻着师姐的名字。'
  await page.goto('/')
  await page.evaluate(
    async ({ title, body }) => {
      const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(body))
      const sha256 = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
      await new Promise<void>((resolve, reject) => {
        const request = indexedDB.open('ainovel-offline-workspace', 1)
        request.onupgradeneeded = () => {
          const db = request.result
          const projects = db.createObjectStore('projects', { keyPath: 'novel.id' })
          projects.createIndex('byBranch', 'branch.id', { unique: true })
          const chapters = db.createObjectStore('chapters', { keyPath: 'id' })
          chapters.createIndex('byBranch', 'branch_id')
          const revisions = db.createObjectStore('revisions', { keyPath: 'id' })
          revisions.createIndex('byChapter', 'chapter_id')
          revisions.createIndex('byRevision', ['chapter_id', 'revision'], { unique: true })
          projects.add({
            novel: { id: 'old-project', title, brief: '', created: 1 },
            branch: {
              id: 'old-branch',
              novel_id: 'old-project',
              name: '主线',
              mode: 'original',
              story_revision: 1,
              created: 1,
            },
          })
          chapters.add({
            id: 'old-chapter',
            branch_id: 'old-branch',
            title: '识字',
            position: 1,
            revision: 0,
            archived: 0,
            stale: 0,
          })
          revisions.add({
            id: 'old-revision',
            chapter_id: 'old-chapter',
            parent_id: null,
            revision: 0,
            content: body,
            sha256,
            cause: '保存稿',
            created: 1,
          })
        }
        request.onerror = () => reject(request.error)
        request.onsuccess = () => {
          request.result.close()
          resolve()
        }
      })
      sessionStorage.setItem('interrupt-ainovel-storage-upgrade', '1')
    },
    { title, body },
  )
  await page.addInitScript(() => {
    const original = IDBFactory.prototype.open
    IDBFactory.prototype.open = function (name, version) {
      const request = original.call(this, name, version)
      if (
        name === 'ainovel-offline-workspace' &&
        Number(version) > 1 &&
        sessionStorage.getItem('interrupt-ainovel-storage-upgrade')
      ) {
        request.addEventListener('upgradeneeded', () => queueMicrotask(() => request.transaction?.abort()))
      }
      return request
    }
  })
  await page.goto('/offline/')
  await expect(page.getByRole('alert')).toContainText('已保留原存储')
  const retained = await page.evaluate(
    () =>
      new Promise((resolve, reject) => {
        const request = indexedDB.open('ainovel-offline-workspace', 1)
        request.onerror = () => reject(request.error)
        request.onsuccess = () => {
          const db = request.result
          const read = db.transaction('revisions').objectStore('revisions').get('old-revision')
          read.onerror = () => reject(read.error)
          read.onsuccess = () => {
            db.close()
            resolve(read.result.content)
          }
        }
      }),
  )
  expect(retained).toBe(body)
  await page.evaluate(() => sessionStorage.removeItem('interrupt-ainovel-storage-upgrade'))
  await page.reload()
  await page.getByRole('combobox', { name: '离线作品', exact: true }).selectOption({ label: title })
  await page.getByRole('button', { name: '1. 识字', exact: true }).click()
  await expect(page.getByLabel('章节正文')).toHaveValue(body)
})

test('离线外壳完整更新后可重开，缺资源的新构建保留上一个版本', async ({ page, context }) => {
  const html = await readFile(resolve('dist/offline/index.html'), 'utf8')
  let version = 1
  const server = createServer((request, response) => {
    void (async () => {
      const path = request.url || ''
      if (path === '/offline/') {
        const extra = version === 1 ? '' : `<link rel="stylesheet" href="/assets/fixture-${version}.css">`
        response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' })
        response.end(html.replace('</head>', extra + '</head>').replace('</title>', ` ${version}</title>`))
      } else if (path === '/assets/fixture-2.css') {
        response.writeHead(200, { 'Content-Type': 'text/css' })
        response.end('h1 { color: rgb(5, 23, 42); }')
      } else if (path === '/offline/sw.js' || /^\/assets\/[\w.-]+\.(?:css|js)$/.test(path)) {
        const file = await readFile(resolve('dist', path.slice(1)))
        response.writeHead(200, { 'Content-Type': path.endsWith('.css') ? 'text/css' : 'text/javascript' })
        response.end(file)
      } else {
        response.writeHead(404).end()
      }
    })().catch(() => response.writeHead(404).end())
  })
  await new Promise<void>((done) => server.listen(0, '127.0.0.1', done))
  try {
    const { port } = server.address() as AddressInfo
    await page.goto(`http://127.0.0.1:${port}/offline/`)
    await expect(page).toHaveTitle('ainovel · 离线写作 1')
    await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
    version = 2
    await page.reload()
    await expect(page).toHaveTitle('ainovel · 离线写作 2')
    await expect(page.getByRole('heading', { name: '离线写作' })).toHaveCSS('color', 'rgb(5, 23, 42)')
    version = 3
    await page.reload()
    await expect(page).toHaveTitle('ainovel · 离线写作 2')
    await context.setOffline(true)
    await page.reload()
    await expect(page).toHaveTitle('ainovel · 离线写作 2')
    await expect(page.getByRole('heading', { name: '离线写作' })).toHaveCSS('color', 'rgb(5, 23, 42)')
  } finally {
    await new Promise<void>((done) => server.close(() => done()))
  }
})
