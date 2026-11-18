import { expect, test } from '@playwright/test'

test('断网按正式保存稿及时更新篇幅和伏笔窗口', async ({ page, context }) => {
  await page.goto('/offline/')
  await expect(page.getByText('离线页面就绪', { exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
  await context.setOffline(true)

  await page.getByLabel('新作品名').fill('潮锁 · 离线规则')
  await page.getByRole('button', { name: '本地新建作品' }).click()
  await page.getByLabel('新章节名').fill('渡口')
  await page.getByRole('button', { name: '本地新建章节' }).click()
  await page.getByText('本机规则核算', { exact: true }).click()
  await expect(page.getByText('全书 1 章 · 0 个有效字符 · 空稿 1 章 · 待复检 0 章')).toBeVisible()
  await expect(page.getByText('确认大纲后可核算回收窗口。')).toBeVisible()

  const outline = page.getByLabel('离线大纲台')
  await outline.getByText('大纲台', { exact: true }).click()
  await outline.getByRole('button', { name: '新建大纲', exact: true }).click()
  await outline.getByRole('textbox', { name: '总纲', exact: true }).fill('追查潮锁的来历。')
  await outline.getByRole('button', { name: '添加卷', exact: true }).click()
  await outline.getByRole('textbox', { name: '卷名称', exact: true }).fill('盐镇')
  await outline.getByRole('textbox', { name: '卷纲', exact: true }).fill('找到制锁的工匠')
  await outline.getByRole('button', { name: '添加单元', exact: true }).click()
  await outline.getByRole('textbox', { name: '单元名称', exact: true }).fill('核对记号')
  await outline.getByRole('textbox', { name: '单元纲', exact: true }).fill('区分真假记号')
  await outline.getByRole('button', { name: '添加章纲', exact: true }).click()
  for (const [name, value] of [
    ['目标', '找工匠'],
    ['阻碍', '账册遗失'],
    ['转折', '发现刻痕'],
    ['结果', '锁定证人'],
  ]) {
    await outline.getByRole('textbox', { name, exact: true }).fill(value)
  }
  await outline.getByRole('textbox', { name: '新伏笔名称', exact: true }).fill('双笔铸记')
  await outline.getByRole('button', { name: '添加新伏笔计划', exact: true }).click()
  await outline.getByRole('button', { name: '确认大纲并保存到浏览器', exact: true }).click()
  await expect(page.getByText('当前保存进度暂无待处理提醒。')).toBeVisible()

  await page
    .getByRole('textbox', { name: '章节正文', exact: true })
    .fill('## 排版标题\n- **铜铃** [响了](https://example.invalid)\n---\n> 入门。😀\u200b\u088f')
  await expect(page.getByText('全书 1 章 · 0 个有效字符 · 空稿 1 章 · 待复检 0 章')).toBeVisible()
  await page.getByRole('button', { name: '保存正文到浏览器', exact: true }).click()
  await expect(page.getByText('全书 1 章 · 8 个有效字符 · 空稿 0 章 · 待复检 0 章')).toBeVisible()
  await expect(page.getByText('双笔铸记 · 回收已到期 · 最迟第 1 章 渡口', { exact: true })).toBeVisible()
  await page.getByText('章与卷的篇幅', { exact: true }).click()
  await expect(page.getByRole('list', { name: '分章篇幅' })).toContainText('渡口 · 修订 1 · 8 个有效字符')
  await expect(page.getByRole('list', { name: '分卷篇幅' })).toContainText('盐镇 · 1 章 · 8 个有效字符')
})
