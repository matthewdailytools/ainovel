import { expect, test } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import type { ManuscriptPack } from '../../frontend/offlineStore'

test('独立 Web 断网新建设定，版本历史经桌面往返后可离线恢复', async ({ page, context }) => {
  const title = '遗铃 · 本地设定往返'
  const first = '只有铜铃回到门内才会响。'
  const second = '只有铜铃回到门内才会响，新月夜除外。'
  await page.goto('/offline/')
  await expect(page.getByText('离线页面就绪', { exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
  await context.setOffline(true)
  await page.getByLabel('新作品名').fill(title)
  await page.getByRole('button', { name: '本地新建作品' }).click()
  const panel = page.getByLabel('离线设定台')
  const open = () => panel.getByText('设定台', { exact: true }).click()
  const review = async () => {
    await panel.getByRole('button', { name: '体检本地设定草稿' }).click()
    await panel.getByRole('button', { name: '确认设定并保存到浏览器' }).click()
    await expect(panel.getByText('已确认并保存新的设定修订。')).toBeVisible()
  }

  await open()
  await panel.getByRole('button', { name: '开始建立设定' }).click()
  await panel.getByRole('button', { name: '添加设定条目' }).click()
  await panel.getByLabel('项目名称').fill('铜铃响声')
  await panel.getByLabel('设定内容').fill(first)
  await panel.getByLabel('约束强度').selectOption('hard')
  await panel.getByLabel('适用范围').fill('门内')
  await review()

  await page.reload()
  await page.getByRole('combobox', { name: '离线作品', exact: true }).selectOption({ label: title })
  await open()
  await panel.getByRole('button', { name: '编辑当前设定' }).click()
  await expect(panel.getByLabel('设定内容')).toHaveValue(first)
  await panel.getByLabel('设定内容').fill(second)
  await panel.getByLabel('例外').fill('新月夜')
  await review()

  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出稿件交换包' }).click()
  const pack = JSON.parse(await readFile((await (await download).path())!, 'utf8')) as ManuscriptPack
  const history = pack.settings_history!
  expect(history.versions).toHaveLength(2)
  const base = history.versions.find((version) => version.parent_id === null)!
  const adopted = history.versions.find((version) => version.id === history.adopted_revision_id)!
  expect(adopted.parent_id).toBe(base.id)

  await context.setOffline(false)
  const { csrf } = await (await page.request.get('/api/session')).json()
  const restore = await page.request.post('/api/manuscript-packages/import', {
    headers: { 'X-Ainovel-CSRF': csrf },
    data: pack,
  })
  expect(restore.ok(), await restore.text()).toBe(true)
  const conception = await (await page.request.get(`/api/branches/${pack.branch.id}/conception`)).json()
  expect(conception.confirmation).toBeTruthy()
  expect(conception.categories.find(({ id }: { id: string }) => id === 'world').items[0].detail).toBe(second)
  const fromDesktop = await (
    await page.request.get(`/api/branches/${pack.branch.id}/manuscript-package`)
  ).json()
  expect(fromDesktop.settings_history).toEqual({ ...history, versions: [base, adopted] })

  await context.setOffline(true)
  await page.evaluate(
    () =>
      new Promise<void>((resolve, reject) => {
        const request = indexedDB.deleteDatabase('ainovel-offline-workspace')
        request.onsuccess = () => resolve()
        request.onerror = () => reject(request.error)
        request.onblocked = () => reject(new Error('离线数据库仍在使用'))
      }),
  )
  await page.reload()
  await page.getByLabel('导入新离线作品').setInputFiles({
    name: '铜铃作品.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(fromDesktop)),
  })
  await open()
  await expect(panel.getByText(`当前确认修订 ${adopted.id}`)).toBeVisible()
  await panel.getByText('设定版本历史').click()
  const version = panel.getByRole('region', { name: '选中设定修订' })
  await expect(version.getByText('来自稿件交换包')).toBeVisible()
  await version.getByText('世界设定', { exact: true }).click()
  await expect(version.getByText(second)).toBeVisible()
  await panel.getByRole('button', { name: '查看上一修订' }).click()
  await version.getByText('世界设定', { exact: true }).click()
  await expect(version.getByText(first)).toBeVisible()
  await panel.getByText('恢复差异（1 项）', { exact: true }).click()
  await expect(panel.getByText(`当前 · 世界设定 · ${second}`, { exact: true })).toBeVisible()
  await panel.getByRole('button', { name: '恢复所选版本为设定草稿' }).press('Enter')
  await expect(panel.getByLabel('设定类别')).toBeFocused()
  await expect(panel.getByLabel('设定内容')).toHaveValue(first)
  await expect(panel.getByText(`当前确认修订 ${adopted.id}`)).toBeVisible()
  await expect(panel.getByRole('button', { name: '确认设定并保存到浏览器' })).toHaveCount(0)
  await panel.getByRole('button', { name: '体检本地设定草稿' }).click()
  await panel.getByRole('button', { name: '确认设定并保存到浏览器' }).click()
  await expect(panel.getByText('已确认历史内容为新的设定修订。')).toBeVisible()
  const recoveredExport = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出稿件交换包' }).click()
  const recoveredPack = JSON.parse(
    await readFile((await (await recoveredExport).path())!, 'utf8'),
  ) as ManuscriptPack
  const recoveredHistory = recoveredPack.settings_history!
  const restored = recoveredHistory.versions.find(({ id }) => id === recoveredHistory.adopted_revision_id)!
  expect(restored).toMatchObject({
    parent_id: adopted.id,
    note: `从设定修订 ${base.id} 恢复。`,
    categories: base.categories,
    origins: base.origins,
  })
  expect(recoveredHistory.versions).toHaveLength(3)
  expect(recoveredHistory.versions).toEqual(expect.arrayContaining([base, adopted]))
})
