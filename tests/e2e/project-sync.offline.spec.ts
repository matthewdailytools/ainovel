import { expect, test } from '@playwright/test'

const cloud = 'http://127.0.0.1:8888'
test.skip(process.env.AINOVEL_SYNC_E2E !== '1', '使用隔离云端进程运行双端验收')

test('桌面和离线 Web 同时确认立项后比较共同依据并采用 Web 版本', async ({ page, browser }) => {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const created = await page.request.post('/api/novels', {
    headers,
    data: { title: '两地立项 · 铜铃', brief: '追查铜铃案。' },
  })
  expect(created.ok(), await created.text()).toBe(true)
  const { branch_id } = await created.json()
  const branch = `/api/branches/${branch_id}`
  const projectPath = branch + '/conception/project-brief'
  const brief = {
    position: '调查被调包的铜铃',
    track: '线索式悬疑',
    genre_ids: ['xianxia'],
    audience: '喜欢推理的读者',
    distinction: '证据在首章埋下',
    length: '三卷十万字',
    ending: '查明铜铃被调包的始末',
  }
  async function confirmDesktop(base_revision: number, idea: string) {
    const response = await page.request.put(projectPath, {
      headers,
      data: { base_revision, idempotency_key: `m1-e2e-confirm-${base_revision}`, idea, brief },
    })
    expect(response.ok(), await response.text()).toBe(true)
    return response.json()
  }
  const common = await confirmDesktop(0, '共同立项，追查铜铃去向。')
  async function grant(device_id: string) {
    const response = await page.request.post(`${cloud}/api/sync/works/${branch_id}/devices`, {
      headers: { Authorization: 'Bearer e2e-test-only-provisioning-token' },
      data: { device_id },
    })
    expect(response.ok(), await response.text()).toBe(true)
    return (await response.json()).token as string
  }
  const desktop = await grant('desktop-m1')
  const webToken = await grant('web-m1')
  async function runDesktop(action: string) {
    const response = await page.request.post(branch + `/desktop-sync/${action}`, {
      headers,
      data: { origin: cloud, token: desktop },
    })
    expect(response.ok(), await response.text()).toBe(true)
    return response.json()
  }
  await runDesktop('enable')
  await runDesktop('run')
  const pack = await (await page.request.get(branch + '/manuscript-package')).json()
  const context = await browser.newContext()
  try {
    const web = await context.newPage()
    await web.goto('http://127.0.0.1:8877/offline/')
    await web.getByLabel('导入新离线作品').setInputFiles({
      name: 'base.json',
      mimeType: 'application/json',
      buffer: Buffer.from(
        JSON.stringify({
          format: pack.format,
          novel: pack.novel,
          branch: pack.branch,
          chapters: [],
          revisions: [],
        }),
      ),
    })
    await web
      .getByRole('combobox', { name: '离线作品', exact: true })
      .selectOption({ label: '两地立项 · 铜铃' })
    await web.getByText('立项台', { exact: true }).click()
    const panel = web.getByLabel('离线立项台')
    const idea = panel.getByRole('textbox', { name: '创作思路', exact: true })
    await expect(idea).toHaveValue('')
    await web.getByText('作品云同步', { exact: true }).click()
    await web.getByLabel('同步服务地址').fill(cloud)
    await web.getByLabel('设备编号').fill('web-m1')
    await web.getByLabel('设备凭据').fill(webToken)
    await web.getByRole('button', { name: '启用作品同步', exact: true }).click()
    const run = web.getByRole('button', { name: '同步当前工作版本', exact: true })
    await run.click()
    await expect(idea).toHaveValue(common.idea)
    await expect(panel.getByText('desktop-m1', { exact: true })).toBeVisible()

    await context.setOffline(true)
    await idea.fill('Web 立项，从渡船查起。')
    await panel.getByRole('button', { name: '确认立项并保存到浏览器' }).click()
    await expect(panel.getByRole('button', { name: '确认立项并保存到浏览器' })).toBeDisabled()
    const local = await confirmDesktop(1, '桌面立项，从旧铃匠查起。')
    await runDesktop('run')
    await context.setOffline(false)
    await run.click()
    await web.getByRole('button', { name: /^比较冲突/ }).click()
    const compare = web.getByRole('region', { name: '云同步冲突比较' })
    for (const [title, text] of [
      ['云端当前版', local.idea],
      ['另一提交版', 'Web 立项，从渡船查起。'],
      ['共同依据', common.idea],
    ]) {
      const side = compare.locator('details').filter({ has: web.getByText(title, { exact: true }) })
      await side.getByText(title, { exact: true }).click()
      await expect(side.locator('pre')).toContainText(text)
    }
    web.once('dialog', (dialog) => dialog.accept())
    await compare.getByRole('button', { name: '采用另一提交版' }).click()
    await expect(web.getByRole('button', { name: /^比较冲突/ })).toHaveCount(0)
    await runDesktop('run')
    const adopted = await (await page.request.get(projectPath)).json()
    expect(adopted.idea).toBe('Web 立项，从渡船查起。')
    expect(adopted.revision).toBe(3)
    expect(adopted.candidate_id).toBeNull()
    await panel.getByText('立项版本历史', { exact: true }).click()
    await panel.getByRole('combobox', { name: '历史立项' }).selectOption(local.id)
    await expect(panel.getByText(local.idea, { exact: false })).toBeVisible()
  } finally {
    await context.close()
  }
})
