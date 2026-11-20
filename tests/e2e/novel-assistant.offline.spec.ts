import { expect, test, type Locator, type Page } from '@playwright/test'
import { openBench } from './helpers/creationFlow'

const first = '记住这一轮的线索是银铃。'
const second = '请沿用前一轮的银铃线索，给出人物设定和总纲建议。'
const originalSetting = '师姐曾经离宗。'
const editedSuggestion = '师姐离宗后借旧法辨认银铃。'

async function createWork(page: Page, title: string) {
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const created = await page.request.post('/api/novels', { headers, data: { title } })
  expect(created.ok(), await created.text()).toBe(true)
  const { id, branch_id } = (await created.json()) as { id: string; branch_id: string }
  return { id, branchId: branch_id, route: `/api/branches/${branch_id}`, headers }
}

async function sendCard(card: Locator, stage: string, value?: string) {
  await card.getByRole('button', { name: '选择保存去向' }).click()
  if (value) await card.getByLabel('候选内容').fill(value)
  await card.getByRole('button', { name: '预览变更' }).click()
  await card.getByRole('button', { name: `确认保存到${stage}` }).click()
  await expect(card.getByText('已送达', { exact: false })).toBeVisible()
}

test('跨台复用银铃对话，两张成果独立送达，草稿与对话均能重新打开', async ({ page }) => {
  const work = await createWork(page, '跨台对话 · 银铃')
  const other = await createWork(page, '跨台对话 · 独立作品')
  const conception = work.route + '/conception'
  const setup = await (await page.request.get(conception)).json()
  setup.categories.push({
    id: 'lead-cast',
    title: '人物补充',
    items: [{ id: 'senior', title: '师姐', detail: originalSetting }],
  })
  const updated = await page.request.put(conception, {
    headers: work.headers,
    data: {
      base_revision: setup.revision,
      base_story_revision: setup.story_revision,
      categories: setup.categories,
    },
  })
  expect(updated.ok(), await updated.text()).toBe(true)

  await page.goto('/')
  const workSelect = page.getByRole('combobox', { name: '作品', exact: true })
  await workSelect.selectOption(work.id)
  await openBench(page, '设定台')
  await page.getByRole('button', { name: '人物补充', exact: false }).first().click()
  const setting = page.getByRole('article', { name: '师姐', exact: true })
  await setting.getByRole('button', { name: '与 AI 讨论 · 师姐 · 设定内容' }).click()
  await page.getByRole('button', { name: '用此对象继续作品对话' }).click()
  const chat = page.getByRole('complementary', { name: '作品 AI 对话' })
  const input = chat.getByRole('textbox', { name: '对话输入' })
  await expect(chat.getByText(/^本轮引用.*师姐/)).toBeVisible()
  await chat.getByText(/^本轮引用/).click()
  await chat.getByRole('button', { name: '固定当前引用' }).click()
  await chat.getByText(/^本轮引用/).click()
  await input.fill(first)
  await chat.getByRole('button', { name: '发送', exact: true }).click()
  await expect(chat.getByText('已记录这轮创作讨论。', { exact: true })).toBeVisible()

  await input.fill(second)
  await openBench(page, '大纲台')
  await expect(input).toHaveValue(second)
  await expect(chat.getByText(/^本轮引用.*大纲台.*另有 1 项/)).toBeVisible()
  await chat.getByRole('button', { name: '发送', exact: true }).click()
  await expect(chat.getByText('沿用前一轮的银铃线索，已整理两份建议。')).toBeVisible()
  await expect(chat.getByText(first, { exact: true })).toBeVisible()
  const round = chat
    .getByRole('article')
    .filter({ has: page.getByRole('heading', { name: '第 2 轮', exact: true }) })
  const personCard = round.getByRole('region', { name: '人物设定建议' })
  const outlineCard = round.getByRole('region', { name: '总纲建议' })
  await sendCard(personCard, '设定台', editedSuggestion)
  await sendCard(outlineCard, '大纲台')
  await outlineCard.getByRole('button', { name: '查看去向' }).click()
  const outlineInbox = page.getByRole('region', { name: /^作品对话建议/ })
  await expect(outlineInbox.getByText('学徒追着银铃的流转找到师姐。')).toBeVisible()
  await outlineInbox.getByRole('button', { name: '与编辑草稿比较' }).click()
  await expect(outlineInbox.getByText('当前编辑草稿')).toBeVisible()
  await expect(page.getByLabel('总纲', { exact: true })).toHaveValue('')
  await outlineInbox.getByRole('button', { name: '取消', exact: true }).click()

  await outlineInbox.getByRole('button', { name: '回到原对话' }).click()
  await expect(round).toBeFocused()
  await personCard.getByRole('button', { name: '查看去向' }).click()
  const settingInbox = page.getByRole('region', { name: /^作品对话建议/ })
  await expect(settingInbox.getByText(editedSuggestion)).toBeVisible()
  const detail = setting.getByRole('textbox', { name: '设定内容', exact: true })
  await expect(detail).toHaveValue(originalSetting)
  // A later local edit must be shown before the replacement can be confirmed.
  await settingInbox.getByRole('button', { name: '与编辑草稿比较' }).click()
  await expect(
    settingInbox.locator('.novel-assistant-inbox-compare').getByText(originalSetting),
  ).toBeVisible()
  const localEdit = '作者新加的未保存一句。'
  await detail.fill(localEdit)
  await settingInbox.getByRole('button', { name: '载入编辑草稿' }).click()
  await expect(settingInbox.getByText('当前编辑草稿已变化，请重新比较。')).toBeVisible()
  await expect(settingInbox.getByText(localEdit, { exact: true })).toBeVisible()
  await expect(detail).toHaveValue(localEdit)
  await settingInbox.getByRole('button', { name: '载入编辑草稿' }).click()
  await expect(detail).toHaveValue(editedSuggestion)
  const serverSetting = await (await page.request.get(conception)).json()
  const category = serverSetting.categories.find((entry: { id: string }) => entry.id === 'lead-cast')
  expect(category.items[0].detail).toBe(originalSetting)

  const followUp = '还想核对铃为何会响。'
  await settingInbox.getByRole('button', { name: '回到原对话' }).click()
  await input.fill(followUp)
  await expect(chat.getByText('输入已保存', { exact: true })).toBeVisible()
  const reopened = await page.context().newPage()
  await reopened.goto('/')
  const reopenedWork = reopened.getByRole('combobox', { name: '作品', exact: true })
  await reopenedWork.selectOption(other.id)
  const reopenedChat = reopened.getByRole('complementary', { name: '作品 AI 对话' })
  await reopenedChat.getByRole('button', { name: 'AI 对话', exact: true }).click()
  await expect(reopenedChat.getByText('输入你想讨论的创作问题。')).toBeVisible()
  await expect(reopenedChat.getByRole('textbox', { name: '对话输入' })).toHaveValue('')
  await reopenedWork.selectOption(work.id)
  await expect(reopenedChat.getByRole('textbox', { name: '对话输入' })).toHaveValue(followUp)
  await expect(reopenedChat.getByText('沿用前一轮的银铃线索，已整理两份建议。')).toBeVisible()
  await openBench(reopened, '设定台')
  const reopenedInbox = reopened.getByRole('region', { name: /^作品对话建议/ })
  await reopenedInbox.getByText(/^作品对话建议/).click()
  await expect(reopenedInbox.getByText(editedSuggestion)).toBeVisible()
  await reopened.close()
})

test('送达正文建议会打开指定章节，两个章稿保持原文', async ({ page }) => {
  const work = await createWork(page, '跨台对话 · 正文去向')
  async function addChapter(title: string, content: string) {
    const created = await page.request.post(work.route + '/chapters', {
      headers: work.headers,
      data: { title },
    })
    expect(created.ok(), await created.text()).toBe(true)
    const { id } = (await created.json()) as { id: string }
    const saved = await page.request.put('/api/chapters/' + id, {
      headers: work.headers,
      data: { base_revision: 0, content },
    })
    expect(saved.ok(), await saved.text()).toBe(true)
    return id
  }
  const firstText = '作者写好的第一章。'
  const secondText = '作者写好的第二章。'
  const firstChapter = await addChapter('旧桥', firstText)
  const secondChapter = await addChapter('旧法', secondText)

  await page.goto('/')
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(work.id)
  const editor = page.getByRole('textbox', { name: '章节正文', exact: true })
  await expect(editor).toHaveText(firstText)
  const chat = page.getByRole('complementary', { name: '作品 AI 对话' })
  await chat.getByRole('button', { name: 'AI 对话', exact: true }).click()
  await chat.getByRole('textbox', { name: '对话输入' }).fill('请给一个夜里听到铃声的正文候选。')
  await chat.getByRole('button', { name: '发送', exact: true }).click()
  const card = chat.getByRole('region', { name: '正文候选建议' })
  await card.getByRole('button', { name: '选择保存去向' }).click()
  await card.getByLabel('目标对象').selectOption('scope:' + secondChapter)
  await card.getByLabel('目标字段').selectOption('text')
  await card.getByRole('button', { name: '预览变更' }).click()
  await card.getByRole('button', { name: '确认保存到写作台' }).click()
  await expect(editor).toHaveText(firstText)
  await card.getByRole('button', { name: '查看去向' }).click()
  const inbox = page.getByRole('region', { name: /^作品对话建议/ })
  await expect(inbox.getByText('银铃在窗下轻轻响了一声。')).toBeVisible()
  await expect(inbox.getByRole('button', { name: '复制建议' })).toBeEnabled()
  await expect(editor).toHaveText(secondText)
  for (const [id, content] of [
    [firstChapter, firstText],
    [secondChapter, secondText],
  ]) {
    expect((await (await page.request.get('/api/chapters/' + id)).json()).content).toBe(content)
  }
})
