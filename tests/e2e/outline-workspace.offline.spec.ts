import { expect, test } from '@playwright/test'

test('新作可确认四级章纲，并从台账跳到对应章节', async ({ page }) => {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const novel = await page.request.post('/api/novels', { headers, data: { title: '遗铃 · 大纲验收' } })
  expect(novel.ok(), await novel.text()).toBe(true)
  const { id, branch_id } = await novel.json()
  const chapter = await page.request.post(`/api/branches/${branch_id}/chapters`, {
    headers,
    data: { title: '门外寻铃' },
  })
  expect(chapter.ok(), await chapter.text()).toBe(true)
  const graph = await (await page.request.get(`/api/branches/${branch_id}/story-graph`)).json()
  const promise = await page.request.post(`/api/branches/${branch_id}/story-records`, {
    headers,
    data: {
      base_story_revision: graph.story_revision,
      record: { kind: 'promise', title: '铃声为何消失', stages: [{ status: 'planned' }] },
    },
  })
  expect(promise.ok(), await promise.text()).toBe(true)
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(id)
  await page.getByRole('button', { name: '大纲与台账', exact: true }).click()
  await page.getByLabel('总纲', { exact: true }).fill('学徒查找失散的师姐。')
  await page.getByRole('button', { name: '新增卷', exact: true }).click()
  await page.getByLabel('卷名', { exact: true }).fill('拾铃卷')
  await page.getByLabel('卷纲', { exact: true }).fill('追索铃身的断痕。')
  await page.getByRole('button', { name: '新增单元', exact: true }).click()
  await page.getByLabel('单元名', { exact: true }).fill('辨铃')
  await page.getByLabel('单元纲', { exact: true }).fill('比较门内外铃声。')
  await page.getByLabel('加入已有章节').selectOption({ label: '1. 门外寻铃' })
  for (const [field, value] of Object.entries({
    目标: '发现铃身的断痕',
    阻碍: '门外听不见铃声',
    转折: '发现门上的旧记号',
    结果: '取得下一步线索',
  })) {
    await page.getByLabel(field, { exact: true }).fill(value)
  }
  await page.getByRole('button', { name: '新增伏笔窗口' }).click()
  await page.getByRole('combobox', { name: '伏笔', exact: true }).selectOption({ label: '铃声为何消失' })
  for (const field of ['埋设章', '开始回收', '最迟回收']) {
    await page.getByRole('combobox', { name: field, exact: true }).selectOption({ label: '门外寻铃' })
  }
  await page.getByRole('button', { name: '保存大纲草稿' }).click()
  await page.getByRole('button', { name: '确认已保存大纲' }).click()
  await expect(page.getByText('已确认大纲修订 2', { exact: true })).toBeVisible()
  await expect(page.getByText(/铃声为何消失 · 计划/)).toBeVisible()
  await page.getByRole('button', { name: '门外寻铃', exact: true }).click()
  await expect(page.getByText('本章章纲 · 修订 2')).toBeVisible()
  await expect(page.getByText('发现铃身的断痕', { exact: true })).toBeVisible()
  await page.locator('.cm-content[contenteditable=true]').focus()
  await page.keyboard.insertText('学徒迈过门槛，铜铃响了。')
  await expect(page.getByLabel('今日事项')).toContainText('1 条伏笔处于回收窗口或已到期')
})
