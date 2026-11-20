import { expect, test, type APIResponse } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { authorSettingPlan } from './fixtures/authorSettingPlan'
import { openWritingSources } from './helpers/writingTools'

async function checked<T>(response: APIResponse): Promise<T> {
  expect(response.ok(), await response.text()).toBe(true)
  return response.json()
}

test('写作右栏定位旧章纲的正式设定和正文证据，关闭后保留未保存稿与焦点', async ({ page }) => {
  const { novel, branch, chapter, original, headers, readPlan } = await authorSettingPlan(
    page,
    '写作里的设定原条目',
  )
  const frozen = await readPlan()
  const confirmation = frozen.confirmed.setting_basis.confirmation_id
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  const editor = page.getByRole('textbox', { name: '章节正文', exact: true })
  await expect(editor).toHaveText(original)
  await openWritingSources(page)
  const usageTitle = page.getByText('设定的正文使用证据', { exact: true })
  await usageTitle.press('Enter')
  const usage = usageTitle.locator('..')
  await usage.getByRole('combobox', { name: '本章引用的设定' }).selectOption('keeper')
  await editor.press('Control+End')
  await editor.press('Control+Shift+Home')
  const saveProof = usage.getByRole('button', { name: '确认该选区为正文证据', exact: true })
  await expect(saveProof).toBeEnabled()
  await saveProof.press('Enter')
  await expect(usage.getByRole('status')).toHaveText('已确认当前正文使用该设定。')

  type State = {
    revision: number
    story_revision: number
    categories: Array<{ id: string; items: Array<{ id: string; detail: string }> }>
  }
  const setting = `${branch}/conception`
  const state = await checked<State>(await page.request.get(setting))
  const changed = '阿陶将归还的钥匙转交看门人。'
  state.categories.find(({ id }) => id === 'characters')!.items.find(({ id }) => id === 'keeper')!.detail =
    changed
  const stored = await checked<State>(
    await page.request.put(setting, {
      headers,
      data: {
        base_revision: state.revision,
        base_story_revision: state.story_revision,
        categories: state.categories,
      },
    }),
  )
  const latest = await checked<{ confirmation: { id: string } }>(
    await page.request.post(`${setting}/confirm`, {
      headers,
      data: {
        base_revision: stored.revision,
        base_story_revision: stored.story_revision,
        idempotency_key: randomUUID(),
      },
    }),
  )
  expect(latest.confirmation.id).not.toBe(confirmation)
  await page.reload()
  await expect(editor).toHaveText(original)
  const chapterPath = `/api/chapters/${chapter.id}`
  const isChapterSave = (url: string, method: string) =>
    method === 'PUT' && new URL(url).pathname === chapterPath
  let releaseSave = () => {}
  const holdSave = new Promise<void>((resolve) => {
    releaseSave = resolve
  })
  // Keep the normal autosave in flight until the frozen evidence and the browser draft have been checked.
  await page.route(`**${chapterPath}`, async (route) => {
    if (isChapterSave(route.request().url(), route.request().method())) await holdSave
    await route.continue()
  })
  const autosave = page.waitForRequest((request) => isChapterSave(request.url(), request.method()))
  const unsaved = '这一句仍在编辑。'
  try {
    await editor.press('Control+End')
    await page.keyboard.insertText(unsaved)
    await expect(editor).toHaveText(original + unsaved)
    await page.setViewportSize({ width: 320, height: 860 })
    await openWritingSources(page)
    const citations = page.getByText('本章章纲引用的设定', { exact: true })
    await citations.press('Enter')
    const item = citations
      .locator('..')
      .getByText(`守铃人阿陶 · 人物 · 修订 ${frozen.confirmed.setting_basis.revision}`, { exact: true })
    await item.press('Enter')
    await expect(item.locator('..')).toContainText('阿陶负责收存归还的钥匙。')
    await expect(item.locator('..')).not.toContainText(changed)
    const open = item.locator('..').getByRole('button', { name: '查看原条目与正文证据', exact: true })
    await open.press('Enter')
    const reference = page.getByRole('region', { name: '冻结设定的正文证据' })
    await expect(reference.getByRole('heading', { name: '「守铃人阿陶」的采用来源与正文证据' })).toBeFocused()
    await expect(reference).toContainText(confirmation)
    await expect(reference).toContainText('条目编号 keeper')
    const proof = reference.getByText('守铃人阿陶 · 待按当前正文或设定重新核对', { exact: true })
    await proof.press('Enter')
    await expect(reference.locator('blockquote')).toHaveText(original)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    await reference.getByRole('button', { name: '返回本章设定引用', exact: true }).press('Enter')
    await expect(open).toBeFocused()
    await expect(editor).toHaveText(original + unsaved)
    expect((await autosave).postDataJSON()).toEqual({ content: original + unsaved, base_revision: 1 })
    expect(await checked(await page.request.get(chapterPath))).toMatchObject({
      content: original,
      revision: 1,
    })
  } finally {
    releaseSave()
  }
  const saved = await (await autosave).response()
  expect(saved?.ok()).toBe(true)
  expect(await saved!.json()).toMatchObject({ content: original + unsaved, revision: 2 })
  expect(await checked(await page.request.get(chapterPath))).toMatchObject({
    content: original + unsaved,
    revision: 2,
  })
})
