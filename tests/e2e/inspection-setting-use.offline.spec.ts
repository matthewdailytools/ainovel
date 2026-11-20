import { expect, test } from '@playwright/test'
import { authorSettingPlan } from './fixtures/authorSettingPlan'
import { openBench } from './helpers/creationFlow'
import { openWritingSources } from './helpers/writingTools'

test('写作确认的非世界设定只改变第 08 项，撤回后本机与独立 Web 可打开历史选区', async ({ page, context }) => {
  const { novel, chapter, original, branch } = await authorSettingPlan(page, '体检回源 · 归还赤钥')
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  const editor = page.getByRole('textbox', { name: '章节正文', exact: true })
  await expect(editor).toHaveText(original)
  await openWritingSources(page)
  const summary = page.getByText('设定的正文使用证据', { exact: true })
  await summary.press('Enter')
  const writing = summary.locator('..')
  await writing.getByRole('combobox', { name: '本章引用的设定' }).selectOption('keeper')
  await editor.press('Control+End')
  await editor.press('Control+Shift+Home')
  await writing.getByRole('button', { name: '确认该选区为正文证据', exact: true }).press('Enter')
  await expect(writing.getByText('已确认当前正文使用该设定。', { exact: true })).toBeVisible()

  await openBench(page, '校验台', true)
  const inspection = page.getByRole('region', { name: '十处对账体检', exact: true })
  const send = page.waitForResponse(
    (response) => response.request().method() === 'POST' && response.url().endsWith('/workflow-inspections'),
  )
  await inspection.getByRole('button', { name: '生成体检单', exact: true }).press('Enter')
  const written = await send
  expect(written.ok(), await written.text()).toBe(true)
  const report = await written.json()
  const use = report.items.find(({ check_id }: { check_id: string }) => check_id === '08').basis
    .author_setting_uses[0]
  expect(JSON.stringify(use)).not.toContain(original)
  expect(use.evidence).toMatchObject({ chapter_id: chapter.id, start: 0, end: original.length })
  const list = inspection.getByRole('region', { name: '体检单结果', exact: true })
  const check = list.getByRole('listitem').filter({ hasText: '08 已确认引用与改稿影响' }).first()
  async function seeNativeOriginal() {
    await check.getByText('查看体检原文', { exact: true }).press('Enter')
    await check.getByText(/^打开归还赤钥原文/).press('Enter')
    await expect(check.locator('mark')).toHaveText(original)
    await expect(check.getByText('作者确认设定使用 · 守铃人阿陶', { exact: true })).toBeVisible()
  }
  await seeNativeOriginal()

  await openBench(page, '写作台', true)
  await openWritingSources(page)
  await summary.press('Enter')
  const proof = writing.getByText('守铃人阿陶 · 当前正文证据', { exact: true })
  await proof.press('Enter')
  page.once('dialog', (dialog) => void dialog.accept())
  await proof.locator('..').getByRole('button', { name: '撤回这条正文证据', exact: true }).press('Enter')
  await expect(writing.getByText('已撤回正文使用证据。', { exact: true })).toBeVisible()

  await openBench(page, '校验台', true)
  await expect(check.getByText('过期', { exact: true })).toBeVisible()
  await expect(
    list
      .getByRole('listitem')
      .filter({ hasText: '05 设定规则、数值与资源' })
      .getByText('过期', { exact: true }),
  ).toHaveCount(0)
  await seeNativeOriginal()

  const response = await page.request.get(branch + '/manuscript-package')
  expect(response.ok(), await response.text()).toBe(true)
  const offline = await context.newPage()
  await offline.goto('/offline/')
  await expect(offline.getByText('离线页面就绪', { exact: true })).toBeVisible()
  await expect.poll(() => offline.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
  await context.setOffline(true)
  const input = offline.getByLabel('导入新离线作品')
  const pack = await response.json()
  const missing = structuredClone(pack)
  delete missing.setting_use_history
  await input.setInputFiles({
    name: '缺少原证明.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(missing)),
  })
  await expect(offline.getByText('报告缺少原作者确认的设定使用版本。', { exact: true })).toBeVisible()
  const mismatched = structuredClone(pack)
  const frozenUse = mismatched.workflow_history.inspections[0].report.items[7].basis.author_setting_uses[0]
  frozenUse.title = '另一个设定的名称'
  await input.setInputFiles({
    name: '设定标题错绑.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(mismatched)),
  })
  await expect(offline.getByText('报告的设定正文使用依据与原作者确认不一致。', { exact: true })).toBeVisible()
  await input.setInputFiles({
    name: '归还赤钥.json',
    mimeType: 'application/json',
    buffer: await response.body(),
  })
  const history = offline.getByLabel('历次体检与完本交付')
  await history.getByText('历次体检与完本交付', { exact: true }).press('Enter')
  await history.getByText(/^已确认引用与改稿影响 ·/).press('Enter')
  await history.getByRole('button', { name: /^查看「守铃人阿陶」原文/ }).press('Enter')
  const source = history.getByRole('region', { name: '历史报告引用正文' })
  await expect(source.locator('mark')).toHaveText(original)
  await expect(source).toContainText(use.evidence.revision_id)
})
