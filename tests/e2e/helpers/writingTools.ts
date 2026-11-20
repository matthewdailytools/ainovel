import { expect, type Page } from '@playwright/test'

export function writingTools(page: Page) {
  return page.getByRole('tablist', { name: '写作工具', exact: true })
}

export async function openWritingSources(page: Page) {
  const sources = writingTools(page).getByRole('tab', { name: '资料与依据', exact: true })
  if ((await sources.getAttribute('aria-selected')) !== 'true') await sources.click()
  await expect(sources).toHaveAttribute('aria-selected', 'true')
}
