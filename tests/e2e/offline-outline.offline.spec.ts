import { expect, test } from '@playwright/test'

test('独立 Web 断网新建四级大纲和伏笔计划，重开读取同一正式历史', async ({ page, context }) => {
  const title = '潮锁 · 离线大纲'
  await page.goto('/offline/')
  await page.getByLabel('新作品名').fill(title)
  await page.getByRole('button', { name: '本地新建作品' }).click()
  await page.getByLabel('新章节名').fill('渡口查证')
  await page.getByRole('button', { name: '本地新建章节' }).click()
  await expect(page.getByText('离线页面就绪', { exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
  await context.setOffline(true)

  const panel = page.getByLabel('离线大纲台')
  await panel.getByText('大纲台', { exact: true }).click()
  await panel.getByRole('button', { name: '新建大纲', exact: true }).click()
  await panel.getByRole('textbox', { name: '总纲', exact: true }).fill('查明潮锁的归属。')
  await panel.getByRole('button', { name: '添加卷', exact: true }).click()
  await panel.getByRole('textbox', { name: '卷名称', exact: true }).fill('盐镇')
  await panel.getByRole('textbox', { name: '卷纲', exact: true }).fill('追查锁匠')
  await panel.getByRole('button', { name: '添加单元', exact: true }).click()
  await panel.getByRole('textbox', { name: '单元名称', exact: true }).fill('查证铸记')
  await panel.getByRole('textbox', { name: '单元纲', exact: true }).fill('区分真锁和替代品')
  await panel.getByRole('button', { name: '添加章纲', exact: true }).click()
  for (const [label, value] of [
    ['目标', '找到锁匠'],
    ['阻碍', '账册不全'],
    ['转折', '找出双笔铸记'],
    ['结果', '锁定知情人'],
  ]) {
    await panel.getByRole('textbox', { name: label, exact: true }).fill(value)
  }
  await panel.getByRole('textbox', { name: '新伏笔名称', exact: true }).fill('双笔铸记')
  await panel.getByRole('textbox', { name: '计划说明', exact: true }).fill('在渡口核对双笔铸记。')
  await panel.getByRole('button', { name: '添加新伏笔计划', exact: true }).click()
  await expect(panel.getByRole('group', { name: '双笔铸记', exact: true })).toBeVisible()
  await panel.getByRole('button', { name: '确认大纲并保存到浏览器', exact: true }).click()
  await expect(panel.getByText('已确认并保存大纲修订。')).toBeVisible()

  await page.close()
  const reopened = await context.newPage()
  await reopened.goto('/offline/')
  await reopened.getByRole('combobox', { name: '离线作品', exact: true }).selectOption({ label: title })
  const saved = reopened.getByLabel('离线大纲台')
  await saved.getByText('大纲台', { exact: true }).click()
  await saved.getByText('大纲版本历史', { exact: true }).click()
  await expect(saved.locator('pre')).toContainText('总纲 查明潮锁的归属。')
  await expect(saved.locator('pre')).toContainText('单元 查证铸记')
  await expect(saved.locator('pre')).toContainText('伏笔 双笔铸记')
  await saved.getByRole('button', { name: '编辑当前大纲', exact: true }).click()
  await expect(saved.getByRole('combobox', { name: '最迟回收', exact: true })).toHaveValue(
    await saved.getByRole('combobox', { name: '对应章节', exact: true }).inputValue(),
  )
})
