import { expect, test, type APIResponse } from '@playwright/test'
import { openWritingPanel } from './helpers/writingPanels'

async function read(response: APIResponse) {
  expect(response.ok(), await response.text()).toBeTruthy()
  return response.json()
}

test('分卷树、设定编辑与按节点对话共用写作工作区，快速刷新恢复原位置', async ({ page }, info) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')
  const session = await read(await page.request.get('/api/session'))
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await read(
    await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 三栏写作工作区' } }),
  )
  const branch = `/api/branches/${novel.branch_id}`
  async function chapter(title: string, content: string) {
    const created = await read(await page.request.post(`${branch}/chapters`, { headers, data: { title } }))
    return read(
      await page.request.put(`/api/chapters/${created.id}`, { headers, data: { base_revision: 0, content } }),
    )
  }
  await chapter('前章 · 渡口', '渡口立着一支刻过水位的木桩。')
  const lastLine = '孟桥在闸门前听水。'
  const text = Array.from({ length: 80 }, (_, index) => `第 ${index + 1} 次换灯后，水位还停在旧木桩下。`)
    .concat(lastLine)
    .join('\n')
  const next = await chapter('后章 · 闸门', text)

  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  const directory = page.getByRole('complementary', { name: '作品目录', exact: true })
  const tree = directory.getByRole('navigation', { name: '作品树' })
  const chat = page.getByRole('region', { name: 'AI 对话', exact: true })
  const editorPane = page.getByRole('region', { name: '正文写作', exact: true })
  const settingPane = page.getByRole('region', { name: '设定编辑', exact: true })
  const second = tree.getByRole('button', { name: /后章 · 闸门/ })
  const world = tree.getByRole('button', { name: '世界设定', exact: true })

  await directory.getByText('分卷管理', { exact: true }).click()
  await directory.getByRole('textbox', { name: '卷名', exact: true }).fill('卷一 · 河埠')
  await directory.getByRole('combobox', { name: '起始章', exact: true }).selectOption(next.id)
  await directory.getByRole('button', { name: '保存分卷', exact: true }).click()
  const volume = tree.getByText('卷一 · 河埠', { exact: true })
  await expect(volume).toBeVisible()
  await expect(
    tree.locator('details.manuscript-volume').getByRole('button', { name: /后章 · 闸门/ }),
  ).toBeVisible()
  await volume.press('Enter')
  await expect(second).toBeHidden()
  await volume.press('Enter')
  await directory.getByText('分卷管理', { exact: true }).click()
  await second.press('Enter')
  await expect(second).toHaveAttribute('aria-current', 'true')

  const editor = editorPane.getByRole('textbox', { name: '章节正文', exact: true })
  const scroll = () => editor.evaluate((el) => el.closest('.cm-scroller')!.scrollTop)
  const caret = () =>
    editor.evaluate(() => ({
      text: getSelection()?.focusNode?.textContent,
      offset: getSelection()?.focusOffset,
    }))
  await editor.press('Control+End')
  await editor.press('ArrowLeft')
  await expect.poll(caret).toEqual({ text: lastLine, offset: lastLine.length - 1 })
  await expect.poll(scroll).toBeGreaterThan(100)
  const chapterScroll = await scroll()
  const left = (await directory.boundingBox())!
  const middle = (await editorPane.boundingBox())!
  const right = (await chat.boundingBox())!
  expect(left.x + left.width).toBeLessThanOrEqual(middle.x + 1)
  expect(middle.x + middle.width).toBeLessThanOrEqual(right.x + 1)
  expect(await directory.evaluate((el) => getComputedStyle(el).position)).toBe('sticky')
  await page.screenshot({ path: info.outputPath('desktop-writing-workspace.png') })

  await world.press('Enter')
  await expect(settingPane).toBeFocused()
  await expect(editorPane).toBeHidden()
  await expect(world).toHaveAttribute('aria-current', 'true')
  const canonical = '闸门只在水位低于旧木桩时开启。'
  await settingPane.getByRole('textbox', { name: '世界设定正文', exact: true }).fill(canonical)
  await settingPane.getByRole('button', { name: '保存并采用', exact: true }).click()
  await expect(settingPane.getByText('已保存并采用', { exact: true })).toBeVisible()
  expect((await read(await page.request.get(`${branch}/definitions/world`))).text).toBe(canonical)
  await chat.getByText('对话设置与发送范围', { exact: true }).click()
  await chat.getByRole('combobox', { name: '回复长度', exact: true }).selectOption('concise')
  await expect(chat.getByText(/当前类别已采用的设定/)).toBeVisible()
  const question = chat.getByRole('textbox', { name: '对话问题', exact: true })
  await question.fill('何时能开启闸门？')
  await chat.getByRole('button', { name: '发送问题', exact: true }).click()
  await expect(chat.getByText(/序号 1.*何时能开启闸门/)).toBeVisible()
  await question.fill('再说一次限制。')
  await chat.getByRole('button', { name: '发送问题', exact: true }).click()
  await expect(chat.getByText(/序号 2.*再说一次限制/)).toBeVisible()

  await second.click()
  await expect(settingPane).toBeHidden()
  await expect(chat.getByText('何时能开启闸门？', { exact: true })).toHaveCount(0)
  await editor.focus()
  await expect.poll(caret).toEqual({ text: lastLine, offset: lastLine.length - 1 })
  await expect.poll(scroll).toBe(chapterScroll)
  await question.fill('暂时不发送的问题。')
  page.once('dialog', (dialog) => dialog.dismiss())
  await directory.getByRole('button', { name: '＋ 新章', exact: true }).click()
  await expect(tree.locator('.chapter-list button')).toHaveCount(2)
  await expect(question).toHaveValue('暂时不发送的问题。')
  await question.fill('这段的地点是什么？')
  await chat.getByRole('button', { name: '发送问题', exact: true }).click()
  await expect(chat.getByText(/序号 1.*这段的地点是什么/)).toBeVisible()
  const untouched = await read(await page.request.get(`/api/chapters/${next.id}`))
  expect({ revision: untouched.revision, content: untouched.content }).toEqual({
    revision: next.revision,
    content: text,
  })

  await editor.focus()
  await editor.press('ArrowLeft')
  const note = directory.getByRole('textbox', { name: '下次从这里继续', exact: true })
  await note.fill('先补开闸的因果。')
  await page.reload()
  await expect(second).toHaveAttribute('aria-current', 'true')
  await expect(note).toHaveValue('先补开闸的因果。')
  await editor.focus()
  await expect.poll(caret).toEqual({ text: lastLine, offset: lastLine.length - 2 })
  await expect.poll(scroll).toBe(chapterScroll)
  await expect(chat.getByText(/序号 1.*这段的地点是什么/)).toBeVisible()

  await page.setViewportSize({ width: 320, height: 900 })
  await page.addStyleTag({ content: 'html { font-size: 200%; }' })
  await openWritingPanel(page, '目录')
  await world.click()
  await expect(settingPane.getByRole('textbox', { name: '世界设定正文', exact: true })).toHaveValue(canonical)
  await openWritingPanel(page, 'AI 对话')
  await expect(chat.getByText(/序号 2.*再说一次限制/)).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(321)
  await page.screenshot({ path: info.outputPath('mobile-writing-workspace.png') })
})
