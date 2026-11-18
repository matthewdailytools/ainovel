import { expect, test } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import type { ManuscriptPack } from '../../frontend/offlineStore'

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
  const later = '先查明两口旧锁的归属。'
  await saved.getByRole('textbox', { name: '总纲', exact: true }).fill(later)
  const confirm = saved.getByRole('button', { name: '确认大纲并保存到浏览器', exact: true })
  await confirm.click()
  await expect(saved.getByText('已确认并保存大纲修订。')).toBeVisible()
  await saved.getByRole('button', { name: '查看上一大纲修订' }).click()
  await saved.getByText('对照当前大纲', { exact: true }).click()
  await expect(saved.getByText(later, { exact: false }).first()).toBeVisible()
  const restore = saved.getByRole('button', { name: '恢复所选版本为大纲草稿', exact: true })
  await restore.press('Enter')
  await expect(saved.getByRole('textbox', { name: '总纲', exact: true })).toBeFocused()
  await expect(saved.getByRole('textbox', { name: '总纲', exact: true })).toHaveValue('查明潮锁的归属。')
  await confirm.click()
  await expect(saved.getByText('已确认历史内容为新的大纲修订。')).toBeVisible()

  const download = reopened.waitForEvent('download')
  await reopened.getByRole('button', { name: '导出稿件交换包', exact: true }).click()
  const pack = JSON.parse(await readFile((await (await download).path())!, 'utf8')) as ManuscriptPack
  const history = pack.outline_history!
  expect(history.versions).toHaveLength(3)
  const original = history.versions.find(({ parent_id }) => parent_id === null)!
  const recent = history.versions.find(({ parent_id }) => parent_id === original.id)!
  const restored = history.versions.find(({ id }) => id === history.adopted_revision_id)!
  expect(recent.plan.summary).toBe(later)
  expect(restored).toMatchObject({
    parent_id: recent.id,
    plan: original.plan,
    promise_sources: original.promise_sources,
  })

  const settings = reopened.getByLabel('离线设定台')
  await settings.getByText('设定台', { exact: true }).click()
  await settings.getByRole('button', { name: '开始建立设定' }).click()
  await settings.getByRole('button', { name: '添加设定条目' }).click()
  await settings.getByLabel('项目名称').fill('潮锁归属')
  await settings.getByLabel('设定内容').fill('潮锁只在同一片盐沼使用。')
  await settings.getByRole('button', { name: '体检本地设定草稿' }).click()
  await settings.getByRole('button', { name: '确认设定并保存到浏览器' }).click()
  await expect(settings.getByText('已确认并保存新的设定修订。')).toBeVisible()
  await expect(saved.getByText('选中修订的立项或设定依据已变化。可对照历史并核对当前大纲。')).toBeVisible()
  await expect(restore).toBeDisabled()
})
