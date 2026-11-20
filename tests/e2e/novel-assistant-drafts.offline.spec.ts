import { expect, test, type Page } from '@playwright/test'
import { assertActualDoubleText } from './helpers/actualTextScale'

async function openConversation(page: Page) {
  const opener = page.getByRole('button', { name: 'AI 对话', exact: true })
  await opener.click()
  const conversation = page.getByRole('complementary', { name: '作品 AI 对话' })
  await expect(conversation.getByRole('textbox', { name: '对话输入' })).toBeEnabled()
  return conversation
}

test('作品对话在窄屏支持键盘，在两窗口保留各自输入直到明确选择', async ({ page, context }) => {
  await page.setViewportSize({ width: 320, height: 780 })
  await page.goto('/')
  await page.getByText('＋ 新建作品', { exact: true }).click()
  await page.getByRole('textbox', { name: '作品名称', exact: true }).fill('作品对话草稿窄屏核对')
  await page.getByRole('button', { name: '创建并开始写作', exact: true }).click()

  const first = await openConversation(page)
  const title = first.getByRole('heading', { name: 'AI 对话', exact: true })
  const close = first.getByRole('button', { name: '收起对话' })
  await expect(title).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(close).toBeFocused()
  const panel = await first.boundingBox()
  expect(panel?.x).toBeGreaterThanOrEqual(0)
  expect((panel?.x ?? 0) + (panel?.width ?? 0)).toBeLessThanOrEqual(320)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)

  const secondPage = await context.newPage()
  await secondPage.goto('/')
  await secondPage
    .getByRole('combobox', { name: '作品', exact: true })
    .selectOption({ label: '作品对话草稿窄屏核对' })
  const second = await openConversation(secondPage)
  const firstInput = first.getByRole('textbox', { name: '对话输入' })
  const secondInput = second.getByRole('textbox', { name: '对话输入' })
  await firstInput.fill('第一窗口的作者输入。')
  await expect(first.getByText('输入已保存', { exact: true })).toBeVisible()
  await secondInput.fill('第二窗口的独立修改。')
  await expect(second.getByRole('alert')).toContainText('另一窗口已保存新的对话草稿')
  await expect(firstInput).toHaveValue('第一窗口的作者输入。')
  await expect(secondInput).toHaveValue('第二窗口的独立修改。')
  await second.getByRole('button', { name: '保留我的输入' }).click()
  await expect(second.getByText('输入已保存', { exact: true })).toBeVisible()

  await close.press('Escape')
  const opener = page.getByRole('button', { name: 'AI 对话', exact: true })
  await expect(opener).toBeFocused()
  await expect(title).toBeHidden()
  await opener.press('Enter')
  await expect(firstInput).toHaveValue('第二窗口的独立修改。')
  await expect(first.getByRole('article')).toHaveCount(0)
  await close.focus()
  await page.keyboard.press('Shift+Tab')
  await expect(title).toBeHidden()
  await expect(opener).not.toBeFocused()
})

test('窄屏作品对话中长名称在文字放大后仍为记录与输入留出空间', async ({ page }, info) => {
  const work =
    '一枚银铃穿过山河与城墙背后无人知晓的那条旧路然而说书人仍在镇口等待最后一位归来的旅客与他尚未寄出的信'
  await page.setViewportSize({ width: 320, height: 780 })
  await page.goto('/')
  await page.getByText('＋ 新建作品', { exact: true }).click()
  await page.getByRole('textbox', { name: '作品名称', exact: true }).fill(work)
  await page.getByRole('button', { name: '创建并开始写作', exact: true }).click()
  const chat = await openConversation(page)
  const title = chat.getByRole('heading', { name: 'AI 对话', exact: true })
  const workName = chat.locator('.novel-assistant-work-title').filter({ hasText: work })
  const close = chat.getByRole('button', { name: '收起对话' })
  const input = chat.getByRole('textbox', { name: '对话输入' })
  const send = chat.getByRole('button', { name: '发送', exact: true })
  await input.fill('请记住银铃与长夜中的旧路线索。')
  await send.click()
  const reply = chat.getByText('已记录这轮创作讨论。', { exact: true })
  await expect(reply).toBeVisible()
  await input.fill('还想进一步核对这条尚未写进作品的线索。')
  const sizes = await assertActualDoubleText(page, { title, workName, close, input, send, reply }, info)
  const space = await chat.evaluate((element) => {
    const transcript = element.querySelector('.novel-assistant-transcript')!
    const composer = element.querySelector('.novel-assistant-composer')!
    return { transcript: transcript.clientHeight, composer: composer.clientHeight }
  })
  expect(space.transcript).toBeGreaterThanOrEqual(sizes.reply.after * 4)
  expect(space.composer).toBeGreaterThanOrEqual(sizes.input.after * 5)
  await title.focus()
  await page.keyboard.press('Tab')
  await expect(close).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(workName).toBeFocused()
  await page.keyboard.press('End')
  await expect.poll(() => workName.evaluate((element) => element.scrollTop)).toBeGreaterThan(0)
  await input.focus()
  await expect(input).toBeFocused()
  await page.keyboard.insertText('再核对。')
  await expect(input).toHaveValue(/再核对。/)
  await input.press('Escape')
  await expect(page.getByRole('button', { name: 'AI 对话', exact: true })).toBeFocused()
})
