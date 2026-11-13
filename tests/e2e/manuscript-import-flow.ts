import { expect, type Page } from '@playwright/test'

export async function manuscriptImportFlow(page: Page) {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await (
    await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 拆章导入' } })
  ).json()
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await expect(page.getByRole('combobox', { name: '工作版本', exact: true })).not.toHaveValue('')
  await page.getByText('导入与导出', { exact: true }).click()
  await page.getByRole('button', { name: '长篇拆章预览与导入', exact: true }).click()
  const workspace = page.getByRole('region', { name: '长篇拆章导入', exact: true })
  await workspace.getByLabel('选择长篇TXT或Markdown（20MB内）').setInputFiles({
    name: '自拟旧稿.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('前言😀\n第一章 渡口\n她登上渡船。\n第二章 铜牌\n她收起铜牌。\n'),
  })
  await expect(workspace.getByRole('combobox', { name: '选择预览段', exact: true })).toBeVisible()
  expect(await (await page.request.get(`/api/branches/${novel.branch_id}/chapters`)).json()).toEqual([])
  await workspace.getByRole('combobox', { name: '本段导入方式', exact: true }).selectOption('skip')
  await workspace.getByRole('combobox', { name: '选择预览段', exact: true }).selectOption('1')
  await workspace.getByRole('button', { name: '合并下一段（沿用本段映射）', exact: true }).click()
  await workspace.getByRole('textbox', { name: '章节标题', exact: true }).fill('渡口与铜牌')
  page.once('dialog', async (dialog) => {
    expect(dialog.message()).toContain('导入章界与映射')
    await dialog.dismiss()
  })
  await workspace.getByRole('button', { name: '关闭导入工作区', exact: true }).click()
  await expect(workspace.getByRole('textbox', { name: '章节标题', exact: true })).toHaveValue('渡口与铜牌')
  page.once('dialog', (dialog) => dialog.dismiss())
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  await expect(workspace).toBeVisible()
  await workspace.getByRole('button', { name: '保存章界与映射草稿', exact: true }).click()
  await expect(workspace.getByText('章界和映射草稿已保存，刷新后可从预览历史恢复。')).toBeVisible()
  await workspace.getByRole('button', { name: '关闭导入工作区', exact: true }).click()
  await expect(workspace).toHaveCount(0)
  await page.reload()
  await page.getByText('导入与导出', { exact: true }).click()
  await page.getByRole('button', { name: '长篇拆章预览与导入', exact: true }).click()
  await workspace.getByText('已保存预览与导入回执', { exact: true }).click()
  await workspace.getByRole('button', { name: /自拟旧稿.txt · 未确认/ }).click()
  await expect(workspace.getByRole('combobox', { name: '选择预览段', exact: true })).toContainText(
    '渡口与铜牌',
  )
  await workspace.getByRole('button', { name: '确认以上范围并导入（替换保留原稿）', exact: true }).click()
  await expect(workspace.getByRole('region', { name: '长篇导入回执', exact: true })).toContainText(
    '跳过 · 卷首／前言',
  )
  const chapters = await (await page.request.get(`/api/branches/${novel.branch_id}/chapters`)).json()
  expect(chapters).toHaveLength(1)
  const saved = await (await page.request.get(`/api/chapters/${chapters[0].id}`)).json()
  expect(saved.content).toBe('第一章 渡口\n她登上渡船。\n第二章 铜牌\n她收起铜牌。\n')
  await page.reload()
  await page.getByText('导入与导出', { exact: true }).click()
  await page.getByRole('button', { name: '长篇拆章预览与导入', exact: true }).click()
  await workspace.getByText('已保存预览与导入回执', { exact: true }).click()
  await workspace.getByRole('button', { name: /自拟旧稿.txt · 已导入/ }).click()
  await expect(workspace.getByRole('heading', { name: '导入回执', exact: true })).toBeVisible()
}
