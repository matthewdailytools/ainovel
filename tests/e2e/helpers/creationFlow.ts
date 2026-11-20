import { expect, type Page } from '@playwright/test'

export function creationFlow(page: Page) {
  return page.locator('details.creation-flow')
}

export async function openCreationFlow(page: Page, keyboard = false) {
  const flow = creationFlow(page)
  const trigger = flow.locator(':scope > summary')
  await expect(trigger).toBeVisible()
  if ((await flow.getAttribute('open')) === null) {
    if (keyboard) await trigger.press('Enter')
    else await trigger.click()
  }
  await expect(flow).toHaveAttribute('open', '')
  return flow
}

export async function openBench(page: Page, name: string, keyboard = false) {
  const flow = await openCreationFlow(page, keyboard)
  const bench = flow
    .getByRole('navigation', { name: '七台工作流', exact: true })
    .getByRole('button', { name, exact: true })
  if (keyboard) {
    await expect(bench).toBeEnabled()
    await bench.press('Enter')
  } else await bench.click()
}
