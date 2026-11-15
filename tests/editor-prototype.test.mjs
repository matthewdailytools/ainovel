import assert from 'node:assert/strict'
import { readFile, mkdir } from 'node:fs/promises'
import { createServer } from 'node:http'
import { once } from 'node:events'
import { join } from 'node:path'
import { chromium } from '@playwright/test'
import { test, before, after } from 'node:test'

const root = new URL('../', import.meta.url)
const storageKey = 'ainovel.editor-prototype.v1'
let server, browser, address
const pages = new Set()
const errors = []
const resources = new Map([
  ['/novel-editor-prototype.html', ['novel-editor-prototype.html', 'text/html']],
  ['/prototypes/editor/editor.css', ['prototypes/editor/editor.css', 'text/css']],
  ['/prototypes/editor/editor.js', ['prototypes/editor/editor.js', 'text/javascript']],
])

before(async () => {
  server = createServer(async (req, res) => {
    const resource = resources.get(req.url)
    if (!resource) {
      res.writeHead(404)
      res.end()
      return
    }
    res.setHeader('Content-Type', `${resource[1]}; charset=utf-8`)
    res.end(await readFile(new URL(resource[0], root)))
  }).listen(0, '127.0.0.1')
  await once(server, 'listening')
  address = `http://127.0.0.1:${server.address().port}/novel-editor-prototype.html`
  browser = await chromium.launch({ headless: true })
})
after(async () => {
  for (const page of pages) await page.context().close()
  await browser?.close()
  await new Promise((resolve) => server.close(resolve))
  assert.deepEqual(errors, [], 'prototype must not produce uncaught browser errors')
})
async function open(width = 1440) {
  const context = await browser.newContext({ viewport: { width, height: 1000 }, acceptDownloads: true })
  const page = await context.newPage()
  pages.add(page)
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto(address)
  await saved(page)
  return page
}
async function saved(page) {
  await page
    .getByRole('status')
    .filter({ hasText: /^已保存到此浏览器$/ })
    .waitFor()
}
async function selectSample(page) {
  await page.locator('#sample-selection').click()
}
async function generate(page) {
  await selectSample(page)
  await page.locator('#generate').click()
  await page.locator('#candidate').waitFor({ state: 'visible' })
}
async function closeDialog(page, id) {
  await page.locator(`[data-close="${id}"]`).click()
}
async function save(page) {
  await page.locator('#save').click()
  await saved(page)
}
async function useFault(page, id, checked) {
  await page.locator('#settings-open').click()
  if (!(await page.locator('.demo-controls').evaluate((el) => el.open)))
    await page.locator('.demo-controls > summary').click()
  await page.locator(id).setChecked(checked)
  await closeDialog(page, 'settings-dialog')
}

test('editing, chapter identity, title and note persist through reload without executing input', async () => {
  const page = await open()
  await page.locator('#manuscript').fill('正文测试：雨夜。\n\n<script>throw new Error("unsafe")</script>😀')
  await page.locator('#chapter-title').fill('第三章 原型验证')
  await page.locator('[data-panel="notes"]').click()
  await page.locator('#chapter-note').fill('下次从渡船继续。')
  await save(page)
  await page.reload()
  await saved(page)
  assert.equal(await page.locator('#chapter-title').inputValue(), '第三章 原型验证')
  assert.match(await page.locator('#manuscript').inputValue(), /<script>/)
  assert.equal(await page.locator('#chapter-note').inputValue(), '下次从渡船继续。')
  await page.locator('#new-chapter').click()
  await page.locator('#new-title').fill('新章标题')
  await page.getByRole('button', { name: '创建并开始写作' }).click()
  await page.waitForFunction(() => document.getElementById('chapter-title').value === '新章标题')
  assert.equal(await page.locator('#manuscript').inputValue(), '')
  await page.locator('#manuscript').fill('新章正文')
  await save(page)
  await page.locator('[data-chapter="chapter-3"]').click()
  await page.waitForFunction(() => document.getElementById('chapter-title').value === '第三章 原型验证')
  assert.match(await page.locator('#manuscript').inputValue(), /正文测试/)
})

test('candidate preview does not change draft; adoption and restore preserve both revisions', async () => {
  const page = await open()
  const original = await page.locator('#manuscript').inputValue()
  await generate(page)
  assert.equal(await page.locator('#manuscript').inputValue(), original)
  const proposed = await page.locator('#candidate-after').textContent()
  await page.locator('#accept').click()
  await saved(page)
  assert.ok((await page.locator('#manuscript').inputValue()).includes(proposed))
  await page.locator('#history-open').click()
  await page.locator('#history-version').selectOption('1')
  assert.equal(await page.locator('#history-before').textContent(), `第三章 雨夜渡口\n\n${original}`)
  await page.locator('#restore').click()
  await page.locator('#history-dialog').waitFor({ state: 'hidden' })
  assert.equal(await page.locator('#manuscript').inputValue(), original)
  const versions = await page.evaluate(
    (key) => JSON.parse(localStorage.getItem(key)).chapters.find((ch) => ch.id === 'chapter-3').versions,
    storageKey,
  )
  assert.equal(versions.length, 3)
  assert.equal(versions[1].cause, '采用演示候选')
  assert.equal(versions[2].cause, '恢复历史修订 1')
})

test('stale candidate, cancellation, failure and target switching do not overwrite draft', async () => {
  const page = await open()
  await generate(page)
  await page.locator('#manuscript').fill('作者最新改稿。')
  assert.equal(await page.locator('#accept').isDisabled(), true)
  await page.locator('#candidate-warning').waitFor({ state: 'visible' })
  await page.locator('#reject').click()
  await selectSample(page)
  await page.locator('#generate').click()
  await page.locator('#cancel-generation').click()
  await page.waitForTimeout(1000)
  assert.equal(await page.locator('#candidate').isVisible(), false)
  assert.equal(await page.locator('#manuscript').inputValue(), '作者最新改稿。')
  await useFault(page, '#fail-generation', true)
  await selectSample(page)
  await page.locator('#generate').click()
  await page
    .getByRole('status')
    .filter({ hasText: /演示：生成失败/ })
    .waitFor()
  assert.match(await page.locator('#instruction').inputValue(), /收紧/)
  await page.locator('#generate').click()
  await page.locator('#candidate').waitFor({ state: 'visible' })
  await page.locator('#reject').click()
  await selectSample(page)
  await page.locator('#generate').click()
  await page.locator('[data-chapter="chapter-2"]').click()
  await page.waitForTimeout(1100)
  assert.equal(await page.locator('#candidate').isVisible(), false)
  assert.equal(await page.locator('#chapter-title').inputValue(), '第二章 河上来信')
})

test('literal find and confirmed replace only modify current chapter and retain history', async () => {
  const page = await open()
  await page.locator('#manuscript').fill('雨雨。雨夜。😀')
  await save(page)
  await page.locator('#find-open').click()
  await page.locator('#find-text').fill('雨')
  assert.match(await page.locator('#find-status').textContent(), /3 处匹配/)
  await page.locator('#replace-text').fill('雪')
  await page.locator('#replace-one').click()
  await page.waitForFunction(() => document.getElementById('manuscript').value === '雪雨。雨夜。😀')
  await page.locator('#replace-all').click()
  assert.match(await page.locator('#replace-confirmation-text').textContent(), /2 处/)
  assert.equal(await page.locator('#manuscript').inputValue(), '雪雨。雨夜。😀')
  await page.locator('#confirm-replace').click()
  await page.waitForFunction(() => document.getElementById('manuscript').value === '雪雪。雪夜。😀')
  await page.keyboard.press('Escape')
  await page.waitForFunction(() => document.activeElement === document.getElementById('manuscript'))
  assert.equal(await page.locator('#manuscript').evaluate((el) => document.activeElement === el), true)
  await saved(page)
  const stored = await page.evaluate((key) => JSON.parse(localStorage.getItem(key)), storageKey)
  assert.equal(stored.chapters.find((ch) => ch.id === 'chapter-1').versions.length, 1)
  assert.equal(stored.chapters.find((ch) => ch.id === 'chapter-3').versions.length, 4)
})

test('save failure retains latest content, blocks chapter switch, supports export and retry', async () => {
  const page = await open()
  await useFault(page, '#fail-save', true)
  await page.locator('#manuscript').fill('保存失败时仍需保留的最新稿。')
  await page.locator('#save').click()
  await page
    .getByRole('status')
    .filter({ hasText: /保存失败/ })
    .waitFor()
  await page.locator('[data-chapter="chapter-2"]').click()
  assert.equal(await page.locator('#manuscript').inputValue(), '保存失败时仍需保留的最新稿。')
  const downloading = page.waitForEvent('download')
  await page.locator('#export').click()
  const download = await downloading
  assert.equal(download.suggestedFilename(), '第三章 雨夜渡口.txt')
  const text = await readFile(await download.path(), 'utf8')
  assert.match(text, /保存失败时仍需保留的最新稿。/)
  assert.match(text, /原型写作便笺 · 非正文/)
  await useFault(page, '#fail-save', false)
  await save(page)
  assert.equal(await page.locator('#persistent-error').isVisible(), false)
})

test('cross-tab updates lock old writer and malformed storage is never overwritten', async () => {
  const first = await open()
  const second = await first.context().newPage()
  await second.goto(address)
  await saved(second)
  assert.equal(await first.locator('#persistent-error').isVisible(), false)
  await second.locator('#manuscript').fill('另一窗口已保存的新稿。')
  await save(second)
  await first.locator('#persistent-error').waitFor({ state: 'visible' })
  await first.locator('#manuscript').fill('本窗口的待合并稿')
  await first.locator('#save').click()
  assert.equal(await first.locator('#manuscript').inputValue(), '本窗口的待合并稿')
  const persisted = await second.evaluate(
    (key) => JSON.parse(localStorage.getItem(key)).chapters.find((ch) => ch.id === 'chapter-3').text,
    storageKey,
  )
  assert.notEqual(persisted, '本窗口的待合并稿')
  const context = await browser.newContext()
  const bad = await context.newPage()
  pages.add(bad)
  await bad.addInitScript((key) => localStorage.setItem(key, '{broken'), storageKey)
  await bad.goto(address)
  await bad.locator('#persistent-error').waitFor({ state: 'visible' })
  await bad.locator('#save').click()
  assert.equal(await bad.evaluate((key) => localStorage.getItem(key), storageKey), '{broken')
})

test('continuation inserts after selection and identical history restoration still creates a revision', async () => {
  const page = await open()
  await page.locator('#manuscript').fill('甲😀乙。')
  await save(page)
  await page.locator('#manuscript').evaluate((el) => el.setSelectionRange(1, 3))
  await page.locator('#action').selectOption('continue')
  await page.locator('#generate').click()
  await page.locator('#candidate').waitFor({ state: 'visible' })
  const proposal = await page.locator('#candidate-after').textContent()
  await page.locator('#accept').click()
  await saved(page)
  assert.equal(await page.locator('#manuscript').inputValue(), `甲😀${proposal}乙。`)
  await page.locator('#history-open').click()
  const latest = await page.locator('#history-version').inputValue()
  await page.locator('#restore').click()
  await page.locator('#history-dialog').waitFor({ state: 'hidden' })
  assert.equal(await page.locator('#revision-status').textContent(), `浏览器修订 ${Number(latest) + 1}`)
})

test('storage permission failure never reports saved and retains the latest editable draft', async () => {
  const context = await browser.newContext()
  const page = await context.newPage()
  pages.add(page)
  await page.addInitScript(() => {
    Storage.prototype.setItem = () => {
      throw new DOMException('denied', 'QuotaExceededError')
    }
  })
  await page.goto(address)
  await page.locator('#persistent-error').waitFor({ state: 'visible' })
  await page.locator('#manuscript').fill('拒绝存储时仍在窗口里的稿件。')
  await page.locator('#save').click()
  await page
    .getByRole('status')
    .filter({ hasText: /^保存失败/ })
    .waitFor()
  assert.equal(await page.locator('#manuscript').inputValue(), '拒绝存储时仍在窗口里的稿件。')
  assert.equal(await page.evaluate((key) => localStorage.getItem(key), storageKey), null)
})

test('keyboard, composition guard, context selection, focus and responsive text remain usable', async () => {
  const page = await open()
  await page.locator('[data-panel="context"]').click()
  await page.locator('#include-character').uncheck()
  await page.locator('[data-panel="assist"]').click()
  assert.ok(!(await page.locator('#send-scope').textContent()).includes('沈砚人物卡'))
  await page.locator('#manuscript').focus()
  await page.locator('#manuscript').dispatchEvent('compositionstart')
  await page.keyboard.press('Control+Shift+Enter')
  assert.equal(await page.locator('#focus-toggle').getAttribute('aria-pressed'), 'false')
  await page.locator('#manuscript').dispatchEvent('compositionend')
  await page.keyboard.press('Control+Shift+Enter')
  assert.equal(await page.locator('#focus-toggle').getAttribute('aria-pressed'), 'true')
  await page.keyboard.press('Control+Shift+Enter')
  await page.locator('#settings-open').click()
  const trigger = page.locator('#settings-open')
  await page.keyboard.press('Tab')
  assert.equal(
    await page.locator('#settings-dialog').evaluate((el) => el.contains(document.activeElement)),
    true,
  )
  await page.keyboard.press('Escape')
  assert.equal(await trigger.evaluate((el) => document.activeElement === el), true)
  await page.locator('#settings-open').click()
  await page.locator('#font-size').selectOption('36')
  await page.locator('#theme').selectOption('dark')
  await closeDialog(page, 'settings-dialog')
  assert.equal(await page.locator('#manuscript').evaluate((el) => getComputedStyle(el).fontSize), '36px')
  for (const width of [1440, 768, 320]) {
    await page.setViewportSize({ width, height: 1000 })
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      true,
      `overflow at ${width}px`,
    )
    if (width < 1100) {
      await page.locator('#inspector-toggle').click()
      await page.locator('#back-from-inspector').click()
    }
    if (width === 320) {
      await page.locator('#nav-toggle').click()
      await page.locator('#back-from-nav').click()
    }
  }
  await page.setViewportSize({ width: 1440, height: 1000 })
  // 200% UI text reflow, separate from the 200% manuscript setting.
  await page.evaluate(() => {
    const sizes = [...document.querySelectorAll('body *')].map((el) => [
      el,
      parseFloat(getComputedStyle(el).fontSize),
    ])
    for (const [el, size] of sizes) el.style.setProperty('font-size', `${size * 2}px`, 'important')
  })
  for (const width of [1440, 768, 320]) {
    await page.setViewportSize({ width, height: 1000 })
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      true,
      `200% UI overflow at ${width}px`,
    )
  }
})

test('save UI screenshots and measure foreground, borders and accent contrast', async () => {
  const page = await open()
  const directory = new URL('../.cache/editor-prototype/', import.meta.url)
  await mkdir(directory, { recursive: true })
  await page.screenshot({
    path: join(directory.pathname.replace(/^\/(\w:)/, '$1'), 'desktop.png'),
    fullPage: true,
  })
  await generate(page)
  await page.locator('#toast').waitFor({ state: 'hidden' })
  await page.screenshot({
    path: join(directory.pathname.replace(/^\/(\w:)/, '$1'), 'candidate.png'),
    fullPage: true,
  })
  await page.setViewportSize({ width: 320, height: 1000 })
  await page.screenshot({
    path: join(directory.pathname.replace(/^\/(\w:)/, '$1'), 'mobile.png'),
    fullPage: true,
  })
  function luminance(hex) {
    const values = hex
      .match(/\w\w/g)
      .map((value) => parseInt(value, 16) / 255)
      .map((value) => (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4))
    return values[0] * 0.2126 + values[1] * 0.7152 + values[2] * 0.0722
  }
  function ratio(a, b) {
    const x = luminance(a),
      y = luminance(b)
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
  }
  for (const [text, bg] of [
    ['26382f', 'fffdf8'],
    ['59665e', 'f3f3ed'],
    ['fffdf8', '285b48'],
    ['e4e8df', '1d2922'],
    ['b2bdb2', '16211b'],
    ['1d2922', '9dc7a9'],
    ['814319', 'fff2df'],
    ['f1c696', '432f1d'],
  ])
    assert.ok(ratio(text, bg) >= 4.5, `${text}/${bg}`)
  for (const [edge, bg] of [
    ['819084', 'fffdf8'],
    ['7e9284', '1d2922'],
    ['285b48', 'fffdf8'],
    ['9dc7a9', '1d2922'],
  ])
    assert.ok(ratio(edge, bg) >= 3, `${edge}/${bg}`)
})
