import { expect, type Locator, type Page, type TestInfo } from '@playwright/test'

/** Scale a frozen computed-size snapshot, including fixed-pixel text, exactly once. */
export async function assertActualDoubleText(page: Page, targets: Record<string, Locator>, info: TestInfo) {
  const before: Record<string, number> = {}
  for (const [name, target] of Object.entries(targets)) {
    await expect(target, `${name} must actually be visible before scaling`).toBeVisible()
    before[name] = await target.evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize))
    expect(before[name], `${name} has a measurable original size`).toBeGreaterThan(0)
  }
  const scaledElements = await page.evaluate(() => {
    // Read every original size before the first write, avoiding inherited cascading multiplication.
    const sizes = [...document.querySelectorAll<HTMLElement>('body, body *')]
      .filter((element) => element instanceof HTMLElement && element.getClientRects().length > 0)
      .map((element) => [element, Number.parseFloat(getComputedStyle(element).fontSize)] as const)
    for (const [element, size] of sizes)
      if (Number.isFinite(size) && size > 0)
        element.style.setProperty('font-size', `${size * 2}px`, 'important')
    return sizes.length
  })
  const measurements: Record<string, { before: number; after: number; ratio: number }> = {}
  for (const [name, target] of Object.entries(targets)) {
    await expect(target, `${name} remains visible after actual text scaling`).toBeVisible()
    const after = await target.evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize))
    expect(after, `${name} must double its actual computed font size`).toBeCloseTo(before[name] * 2, 2)
    measurements[name] = { before: before[name], after, ratio: after / before[name] }
  }
  expect(await page.locator('body').evaluate((element) => getComputedStyle(element).fontSize)).toBe('30px')
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)
  await info.attach('actual-computed-text-scale', {
    contentType: 'application/json',
    body: JSON.stringify({ viewport: 320, scaledElements, measurements, productionCssChanged: false }),
  })
  return measurements
}
