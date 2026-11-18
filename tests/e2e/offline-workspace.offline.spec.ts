import { expect, test, type Page } from '@playwright/test'
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
  await expect(author.getByRole('status')).toHaveText('本地已保存 · 修订 1')
  await other.getByLabel('章节正文').fill(conflicting)
  await other.getByRole('button', { name: '保存正文到浏览器' }).click()
  await expect(other.getByRole('alert')).toContainText('本章已有新修订')
  await expect(other.getByLabel('章节正文')).toHaveValue(conflicting)
  await expect(other.getByRole('region', { name: '并发稿件' })).toContainText(original)
  await other.getByRole('button', { name: '保留我的输入，改用新基线' }).click()
  await other.getByRole('button', { name: '保存正文到浏览器' }).click()
  await expect(other.getByRole('status')).toHaveText('本地已保存 · 修订 2')
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
  await expect(other.getByRole('status')).toHaveText('已校验并导入稿件及历史。')
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
