import { test, expect } from '@playwright/test'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

test('验证历史：320窄屏实际字体翻倍后筛选、键盘与表格滚动仍可用', async ({ page }, info) => {
  await page.setViewportSize({ width: 320, height: 900 })
  await page.goto(pathToFileURL(resolve('docs/product-validation/index.html')).href)
  const query = page.getByRole('searchbox')
  const phase = page.locator('#phase')
  const result = page.locator('#result')
  const rows = page.locator('tbody tr')
  const total = await rows.count()
  expect(total).toBeGreaterThan(0)
  const before = await page.evaluate(() => ({
    body: parseFloat(getComputedStyle(document.body).fontSize),
    query: parseFloat(getComputedStyle(document.querySelector('#query')!).fontSize),
  }))
  await page.evaluate(() => {
    // Snapshot first: scaling inherited sizes while traversing would compound.
    const sizes = [...document.querySelectorAll('*')].map(
      (element) => [element, parseFloat(getComputedStyle(element).fontSize)] as const,
    )
    for (const [element, size] of sizes) {
      if (element instanceof HTMLElement) element.style.fontSize = `${size * 2}px`
    }
  })
  const after = await page.evaluate(() => ({
    body: parseFloat(getComputedStyle(document.body).fontSize),
    query: parseFloat(getComputedStyle(document.querySelector('#query')!).fontSize),
    viewport: innerWidth,
    document: document.documentElement.scrollWidth,
  }))
  await info.attach('actual-text-scale', {
    body: JSON.stringify({ before, after }),
    contentType: 'application/json',
  })
  expect(after.body).toBe(before.body * 2)
  expect(after.query).toBe(before.query * 2)
  expect(after.document).toBeLessThanOrEqual(after.viewport)
  for (const control of [query, phase, result]) {
    const rect = await control.boundingBox()
    expect(rect).not.toBeNull()
    expect(rect!.x).toBeGreaterThanOrEqual(0)
    expect(rect!.x + rect!.width).toBeLessThanOrEqual(after.viewport)
  }
  await query.focus()
  await expect(query).toBeFocused()
  await query.fill('不存在的验证记录-actual-font-scale')
  await expect(page.locator('tbody tr:visible')).toHaveCount(0)
  await page.keyboard.press('Tab')
  await expect(phase).toBeFocused()
  await query.fill('')
  await phase.selectOption('quality')
  const quality = page.locator('tbody tr:visible')
  expect(await quality.count()).toBeGreaterThan(0)
  expect((await quality.locator('td:nth-child(6)').allTextContents()).every((v) => v === '产出质量')).toBe(
    true,
  )
  await phase.selectOption('')
  await expect(page.locator('tbody tr:visible')).toHaveCount(total)
  const scroll = page.getByRole('region', { name: '验证历史表，可左右滚动查看' })
  await expect(scroll).toHaveCSS('overflow-x', 'auto')
  await scroll.focus()
  await expect(scroll).toBeFocused()
  await page.keyboard.press('ArrowRight')
  await expect.poll(() => scroll.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0)
})
