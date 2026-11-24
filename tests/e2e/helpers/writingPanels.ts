import type { Page } from '@playwright/test'

export async function openWritingPanel(page: Page, name: '目录' | 'AI 对话') {
  const opener = page.getByRole('button', { name, exact: true })
  if ((await opener.getAttribute('aria-expanded')) !== 'true') await opener.click()
}
