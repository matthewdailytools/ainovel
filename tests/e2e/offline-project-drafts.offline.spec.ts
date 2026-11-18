import { readFile } from 'node:fs/promises'
import { expect, test, type APIResponse, type Page } from '@playwright/test'

const cloud = process.env.AINOVEL_TEST_SYNC_URL ?? 'http://127.0.0.1:8888'
const ownerKey = 'ainovel-project-draft-window'
const saved = '立项草稿已暂存到本机。'
const partial = '临时草稿：学徒拾到一枚铜扣。😀'
const first = '第一版正式立项：守夜人的铜扣。'
const scratchA = '窗口甲未确认：从渡口回查。'
const scratchB = '窗口乙未确认：由工匠追查。'
const adoptedB = '第二版正式立项：追查铜扣垫片。'

test.skip(process.env.AINOVEL_SYNC_E2E !== '1', '使用隔离 Chromium 和云账本')

async function okay(response: APIResponse) {
  expect(response.ok(), await response.text()).toBe(true)
  return response.json()
}

async function openBrief(page: Page, projectId?: string) {
  if (projectId) await page.getByRole('combobox', { name: '离线作品', exact: true }).selectOption(projectId)
  const panel = page.getByLabel('离线立项台')
  await panel.getByText('立项台', { exact: true }).press('Enter')
  return panel
}

async function manuscript(page: Page) {
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: '导出稿件交换包', exact: true }).click(),
  ])
  const path = await download.path()
  if (!path) throw new Error('缺少稿件交换文件。')
  return JSON.parse(await readFile(path, 'utf8'))
}

test('立项草稿强制重开后可继续，窗口各自保存，过期提交和同步保护正式版本', async ({
  page,
  context,
  browserName,
}) => {
  test.skip(browserName !== 'chromium', '使用 Chromium 的真实渲染进程崩溃验证')
  await page.goto('/offline/')
  await expect(page.getByText('离线页面就绪', { exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
  await context.setOffline(true)
  await page.getByLabel('新作品名').fill('待确认立项 · 渡口')
  await page.getByRole('button', { name: '本地新建作品' }).click()
  const original = await openBrief(page)
  await original.getByRole('textbox', { name: '创作思路', exact: true }).fill(partial)
  await original.getByRole('textbox', { name: '核心定位', exact: true }).fill('  从铜扣的失主查起  ')
  await expect(original.getByText(saved, { exact: true })).toBeVisible()
  const owner = await page.evaluate((key) => sessionStorage.getItem(key), ownerKey)
  const seed = await manuscript(page)
  expect(JSON.stringify(seed)).not.toContain(partial)
  expect(seed.project_history).toBeUndefined()
  expect(owner).toBeTruthy()

  const renderer = await context.newCDPSession(page)
  const crashed = page.waitForEvent('crash')
  void renderer.send('Page.crash').catch(() => undefined)
  await crashed
  await page.close({ runBeforeUnload: false })
  const main = await context.newPage()
  await main.addInitScript(
    ({ key, previous }) => {
      if (previous && !sessionStorage.getItem(key)) sessionStorage.setItem(key, previous)
    },
    { key: ownerKey, previous: owner },
  )
  await main.goto('/offline/')
  const mainPanel = await openBrief(main, seed.novel.id)
  const mainIdea = mainPanel.getByRole('textbox', { name: '创作思路', exact: true })
  await mainPanel.getByRole('button', { name: '继续编辑本机立项草稿' }).press('Enter')
  await expect(mainIdea).toBeFocused()
  await expect(mainIdea).toHaveValue(partial)
  await expect(mainPanel.getByRole('textbox', { name: '核心定位', exact: true })).toHaveValue(
    '  从铜扣的失主查起  ',
  )
  await expect(mainPanel.getByRole('combobox', { name: '主类型', exact: true })).toHaveValue('')
  await expect(mainPanel.getByText('还没有已确认立项。')).toBeVisible()

  await mainIdea.fill(first)
  await mainPanel.getByRole('combobox', { name: '主类型', exact: true }).selectOption('xianxia')
  for (const [label, value] of [
    ['赛道', '线索调查'],
    ['读者定位', '喜欢伏笔的读者'],
    ['差异化', '器物磨损形成证据'],
    ['篇幅目标', '两章'],
    ['终局', '归还铜扣'],
  ]) {
    await mainPanel.getByRole('textbox', { name: label, exact: true }).fill(value)
  }
  const confirm = mainPanel.getByRole('button', { name: '确认立项并保存到浏览器', exact: true })
  await confirm.click()
  await expect(mainPanel.getByText(/^当前立项修订 /)).toBeVisible()
  await expect(confirm).toBeDisabled()
  await mainIdea.fill(scratchA)
  await expect(mainPanel.getByText(saved, { exact: true })).toBeVisible()

  const other = await context.newPage()
  await other.addInitScript(
    ({ key, previous }) => {
      if (previous && !sessionStorage.getItem(key)) sessionStorage.setItem(key, previous)
    },
    { key: ownerKey, previous: owner },
  )
  await other.goto('/offline/')
  const otherPanel = await openBrief(other, seed.novel.id)
  const otherIdea = otherPanel.getByRole('textbox', { name: '创作思路', exact: true })
  await otherPanel.getByRole('button', { name: '取用另一窗口的立项草稿副本' }).click()
  await expect(otherIdea).toHaveValue(scratchA)
  expect(await other.evaluate((key) => sessionStorage.getItem(key), ownerKey)).not.toBe(owner)
  await otherIdea.fill(scratchB)
  await expect(otherPanel.getByText(saved, { exact: true })).toBeVisible()
  await expect(mainIdea).toHaveValue(scratchA)

  other.once('dialog', (dialog) => dialog.accept())
  await other.reload()
  await openBrief(other, seed.novel.id)
  await otherPanel.getByRole('button', { name: '继续编辑本机立项草稿' }).click()
  await expect(otherIdea).toHaveValue(scratchB)
  await otherIdea.fill(adoptedB)
  await otherPanel.getByRole('button', { name: '确认立项并保存到浏览器', exact: true }).click()
  await expect(otherPanel.getByRole('button', { name: '确认立项并保存到浏览器' })).toBeDisabled()

  await confirm.click()
  await expect(mainPanel.getByRole('alert').filter({ hasText: '立项在其他窗口已有新确认' })).toBeVisible()
  const stale = mainPanel.getByRole('region', { name: '立项草稿基线已变化' })
  await expect(stale).toBeVisible()
  await expect(confirm).toBeDisabled()
  await expect(mainIdea).toHaveValue(scratchA)
  await stale.getByText('对照现行立项', { exact: true }).click()
  await expect(stale.locator('pre')).toContainText(adoptedB)
  main.once('dialog', (dialog) => dialog.accept())
  await stale.getByRole('button', { name: '保留输入，改用现行立项基线' }).press('Enter')
  await expect(stale).toHaveCount(0)
  await expect(mainIdea).toHaveValue(scratchA)
  await expect(mainPanel.getByText(saved, { exact: true })).toBeVisible()

  const exchanged = await manuscript(main)
  expect(exchanged.project_history.versions.map((row: { idea: string }) => row.idea).sort()).toEqual(
    [first, adoptedB].sort(),
  )
  for (const text of [partial, scratchA, scratchB]) expect(JSON.stringify(exchanged)).not.toContain(text)

  await context.setOffline(false)
  const service = `${cloud}/api/sync/works/${seed.branch.id}`
  const device = 'web-project-draft'
  const { token } = await okay(
    await other.request.post(service + '/devices', {
      headers: { Authorization: 'Bearer e2e-test-only-provisioning-token' },
      data: { device_id: device },
    }),
  )
  await other.getByText('作品云同步', { exact: true }).click()
  await other.getByLabel('同步服务地址').fill(cloud)
  await other.getByLabel('设备编号').fill(device)
  await other.getByLabel('设备凭据').fill(token)
  await other.getByRole('button', { name: '启用作品同步' }).click()
  const sent: string[] = []
  other.on('request', (request) => {
    if (request.url() === service + '/push' && request.method() === 'POST')
      sent.push(request.postData() ?? '')
  })
  const sync = other.getByRole('button', { name: '同步当前工作版本' })
  await sync.click()
  await expect.poll(() => sent.some((body) => body.includes(adoptedB))).toBe(true)
  await expect(sync).toBeEnabled()
  const headers = { Authorization: `Bearer ${token}` }
  const { ledger_id } = await okay(await other.request.get(service + '/checkpoint', { headers }))
  const events = await okay(
    await other.request.get(service + '/pull', { headers: { ...headers, 'X-Ainovel-Ledger': ledger_id } }),
  )
  const wire = JSON.stringify(events.changes)
  expect(wire).toContain(first)
  expect(wire).toContain(adoptedB)
  for (const text of [partial, scratchA, scratchB]) {
    expect(wire).not.toContain(text)
    expect(sent.join('')).not.toContain(text)
  }
})
