import { expect, test, type APIResponse, type Page } from '@playwright/test'

const cloud = process.env.AINOVEL_TEST_SYNC_URL ?? 'http://127.0.0.1:8888'
const title = '本机设定草稿 · 渡口'
const adopted = '渡船只在退潮以后靠岸。'
const draftA = '窗口甲的未签设定：风停时也可以离岸。'
const draftB = '窗口乙的未签设定：每次离岸前先敲木板。'

test.skip(process.env.AINOVEL_SYNC_E2E !== '1', '使用隔离 Chromium、SQLite 和云账本')

async function okay(response: APIResponse) {
  expect(response.ok(), await response.text()).toBe(true)
  return response.json()
}

async function openSettings(tab: Page, id: string) {
  await tab.getByRole('combobox', { name: '离线作品', exact: true }).selectOption(id)
  const panel = tab.getByLabel('离线设定台')
  await panel.getByText('设定台', { exact: true }).press('Enter')
  return panel
}

test('未确认设定断网重开可取回，分窗口保存，只有正式修订进入云账本', async ({ page, context }) => {
  await page.goto('/offline/')
  await expect(page.getByText('离线页面就绪', { exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
  const { csrf } = await okay(await page.request.get('/api/session'))
  const { id, branch_id } = await okay(
    await page.request.post('/api/novels', { headers: { 'X-Ainovel-CSRF': csrf }, data: { title } }),
  )
  const seed = await page.request.get(`/api/branches/${branch_id}/manuscript-package`)
  expect(seed.ok(), await seed.text()).toBe(true)
  const service = `${cloud}/api/sync/works/${branch_id}`
  const device_id = 'web-settings-draft'
  const { token } = await okay(
    await page.request.post(service + '/devices', {
      headers: { Authorization: 'Bearer e2e-test-only-provisioning-token' },
      data: { device_id },
    }),
  )
  const bearer = { Authorization: `Bearer ${token}` }
  await context.setOffline(true)
  await page
    .getByLabel('导入新离线作品')
    .setInputFiles({ name: '新作品.json', mimeType: 'application/json', buffer: await seed.body() })
  const panel = await openSettings(page, id)
  const setting = panel.getByRole('region', { name: '靠岸时机', exact: true }).getByLabel('设定内容')
  const saved = '设定草稿已暂存到本机。'
  await panel.getByRole('button', { name: '开始建立设定' }).click()
  await panel.getByRole('button', { name: '添加设定条目' }).click()
  await panel.getByLabel('项目名称').fill('靠岸时机')
  await setting.fill(adopted)
  await panel.getByRole('button', { name: '体检本地设定草稿' }).click()
  await panel.getByRole('button', { name: '确认设定并保存到浏览器' }).click()
  await expect(panel.getByText('已确认并保存新的设定修订。')).toBeVisible()
  const formal = await panel.getByText(/^当前确认修订 /).innerText()
  await panel.getByRole('button', { name: '编辑当前设定' }).click()
  await setting.fill(draftA)
  await panel.getByLabel('确认说明').fill('仍待和领航人核对')
  await panel.getByRole('button', { name: '添加设定条目' }).click()
  await panel.getByRole('region', { name: '未命名设定', exact: true }).getByLabel('设定内容').fill('尚未定名')
  await expect(panel.getByText(saved, { exact: true })).toBeVisible()

  async function reopen(tab: Page) {
    tab.once('dialog', (dialog) => dialog.accept())
    await tab.reload()
    return openSettings(tab, id)
  }
  await reopen(page)
  await expect(panel.getByText(formal, { exact: true })).toBeVisible()
  await panel.getByRole('button', { name: '继续编辑本机设定草稿' }).press('Enter')
  await expect(setting).toHaveValue(draftA)
  await expect(panel.getByLabel('确认说明')).toHaveValue('仍待和领航人核对')
  await expect(
    panel.getByRole('region', { name: '未命名设定', exact: true }).getByLabel('设定内容'),
  ).toHaveValue('尚未定名')
  await expect(panel.getByRole('button', { name: '确认设定并保存到浏览器' })).toHaveCount(0)
  await expect(panel.getByText(saved, { exact: true })).toBeVisible()

  const owner = await page.evaluate(() => sessionStorage.getItem('ainovel-settings-draft-window'))
  expect(owner).toBeTruthy()
  const other = await context.newPage()
  await other.addInitScript((stored) => {
    const key = 'ainovel-settings-draft-window'
    if (stored && !sessionStorage.getItem(key)) sessionStorage.setItem(key, stored)
  }, owner)
  await other.goto('/offline/')
  const otherPanel = await openSettings(other, id)
  const otherSetting = otherPanel
    .getByRole('region', { name: '靠岸时机', exact: true })
    .getByLabel('设定内容')
  await otherPanel.getByRole('button', { name: '取用另一窗口的设定草稿副本' }).click()
  await expect(otherSetting).toHaveValue(draftA)
  await otherSetting.fill(draftB)
  await expect(otherPanel.getByText(saved, { exact: true })).toBeVisible()
  await reopen(other)
  await otherPanel.getByRole('button', { name: '继续编辑本机设定草稿' }).click()
  await expect(otherSetting).toHaveValue(draftB)
  await reopen(page)
  await panel.getByRole('button', { name: '继续编辑本机设定草稿' }).click()
  await expect(setting).toHaveValue(draftA)
  other.once('dialog', (dialog) => dialog.accept())
  await otherPanel.getByRole('button', { name: '放弃本次修改' }).click()
  await expect(otherPanel.getByText(formal, { exact: true })).toBeVisible()

  await context.setOffline(false)
  const sent: string[] = []
  other.on('request', (request) => {
    if (request.url() === service + '/push' && request.method() === 'POST')
      sent.push(request.postData() ?? '')
  })
  await other.getByText('作品云同步', { exact: true }).click()
  await other.getByLabel('同步服务地址').fill(cloud)
  await other.getByLabel('设备编号').fill(device_id)
  await other.getByLabel('设备凭据').fill(token)
  await other.getByRole('button', { name: '启用作品同步' }).click()
  const run = other.getByRole('button', { name: '同步当前工作版本' })
  await run.click()
  await expect.poll(() => sent.some((body) => body.includes(adopted))).toBe(true)
  await expect(run).toBeEnabled()
  const { ledger_id } = await okay(await page.request.get(service + '/checkpoint', { headers: bearer }))
  const events = await okay(
    await page.request.get(service + '/pull', { headers: { ...bearer, 'X-Ainovel-Ledger': ledger_id } }),
  )
  const wire = JSON.stringify(events.changes)
  expect(wire).toContain(adopted)
  for (const text of [draftA, draftB, '仍待和领航人核对', '尚未定名']) {
    expect(wire).not.toContain(text)
    expect(sent.join('\n')).not.toContain(text)
  }
  await expect(setting).toHaveValue(draftA)
  await expect(panel.getByText(formal, { exact: true })).toBeVisible()
})
