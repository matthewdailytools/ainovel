import { expect, test } from '@playwright/test'

const cloud = 'http://127.0.0.1:8888'
test.skip(process.env.AINOVEL_SYNC_E2E !== '1', '使用隔离云端进程运行双端验收')

test('桌面和离线 Web 同章修改产生可解决冲突，历史稿仍可查', async ({ page, browser }) => {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const novelResponse = await page.request.post('/api/novels', {
    headers,
    data: { title: '遗铃 · 跨设备', brief: '查明铜铃来历。' },
  })
  expect(novelResponse.ok()).toBe(true)
  const { branch_id } = await novelResponse.json()
  const branchPath = `/api/branches/${branch_id}`
  const chapterResponse = await page.request.post(branchPath + '/chapters', {
    headers,
    data: { title: '拾铃' },
  })
  expect(chapterResponse.ok()).toBe(true)
  const { id: chapterId } = await chapterResponse.json()
  async function save(revision: number, content: string) {
    const response = await page.request.put(`/api/chapters/${chapterId}`, {
      headers,
      data: { base_revision: revision, content },
    })
    expect(response.ok(), await response.text()).toBe(true)
  }
  await save(0, '原稿：找到了铜铃。')
  const original = await (await page.request.get(branchPath + '/manuscript-package')).body()
  async function grant(device_id: string) {
    const response = await page.request.post(`${cloud}/api/sync/works/${branch_id}/devices`, {
      headers: { Authorization: 'Bearer e2e-test-only-provisioning-token' },
      data: { device_id },
    })
    expect(response.ok()).toBe(true)
    return (await response.json()).token as string
  }
  const desktopToken = await grant('desktop')
  const webToken = await grant('web')
  async function desktop(action: 'enable' | 'run') {
    const response = await page.request.post(branchPath + '/desktop-sync/' + action, {
      headers,
      data: { origin: cloud, token: desktopToken },
    })
    expect(response.ok(), await response.text()).toBe(true)
    return response.json()
  }
  await desktop('enable')
  expect((await desktop('run')).cursor).toBe(2)
  const context = await browser.newContext()
  try {
    const web = await context.newPage()
    await web.goto('http://127.0.0.1:8877/offline/')
    await web.getByLabel('导入新离线作品').setInputFiles({
      name: 'manuscript.json',
      mimeType: 'application/json',
      buffer: original,
    })
    await web
      .getByRole('combobox', { name: '离线作品', exact: true })
      .selectOption({ label: '遗铃 · 跨设备' })
    await web.getByRole('button', { name: '1. 拾铃', exact: true }).click()
    await web.getByText('作品云同步', { exact: true }).click()
    await web.getByLabel('同步服务地址').fill(cloud)
    await web.getByLabel('设备编号').fill('web')
    await web.getByLabel('设备凭据').fill(webToken)
    await web.getByRole('button', { name: '启用作品同步', exact: true }).click()
    const sync = web.getByRole('button', { name: '同步当前工作版本', exact: true })
    await sync.click()
    await expect(web.getByText(/已启用 · 游标 2/)).toBeVisible()
    const editor = web.getByRole('textbox', { name: '章节正文', exact: true })
    await context.setOffline(true)
    await editor.fill('Web 版本：学徒拾铃。')
    await web.getByRole('button', { name: '保存正文到浏览器', exact: true }).click()
    await expect(web.getByText(/本地已保存 · 修订 2/)).toBeVisible()
    await save(1, '桌面版本：师姐拾铃。')
    expect((await desktop('run')).cursor).toBe(3)
    await context.setOffline(false)
    await sync.click()
    await web.getByRole('button', { name: /^比较冲突/ }).click()
    await expect(editor).toHaveValue('Web 版本：学徒拾铃。')
    const compare = web.getByRole('region', { name: '云同步冲突比较' })
    await compare.getByText('云端当前版', { exact: true }).click()
    await expect(compare.getByText('桌面版本：师姐拾铃。', { exact: true })).toBeVisible()
    web.once('dialog', (dialog) => dialog.accept())
    await compare.getByRole('button', { name: '采用云端当前版', exact: true }).click()
    await expect(editor).toHaveValue('桌面版本：师姐拾铃。')
    await expect(web.getByRole('button', { name: /^比较冲突/ })).toHaveCount(0)
    expect((await desktop('run')).conflicts).toHaveLength(0)

    await web.getByText('本章版本历史', { exact: true }).click()
    const history = web.getByRole('combobox', { name: '历史修订', exact: true })
    await history.selectOption({ label: '修订 2 · 作者编辑' })
    await expect(web.getByText('Web 版本：学徒拾铃。', { exact: true })).toBeVisible()
    await history.selectOption({ label: '修订 1 · 作者编辑' })
    await web.getByRole('button', { name: '恢复为最新修订', exact: true }).click()
    await expect(editor).toHaveValue('原稿：找到了铜铃。')
    await sync.click()
    const rolledBack = await (await page.request.get(`/api/chapters/${chapterId}`)).json()
    expect(rolledBack.content).not.toBe('原稿：找到了铜铃。')
    await expect(web.getByText(/已启用 · 游标 6/)).toBeVisible()
    await desktop('run')
    const restored = await (await page.request.get(`/api/chapters/${chapterId}`)).json()
    expect(restored.content).toBe('原稿：找到了铜铃。')
    expect(restored.revision).toBeGreaterThan(2)

    await context.setOffline(true)
    await editor.fill('Web 归档冲突稿。')
    await web.getByRole('button', { name: '保存正文到浏览器', exact: true }).click()
    await expect(web.getByText(/本地已保存 · 修订 5/)).toBeVisible()
    const archived = await page.request.post(`/api/chapters/${chapterId}/archive`, {
      headers,
      data: { base_revision: restored.revision, archived: true },
    })
    expect(archived.ok(), await archived.text()).toBe(true)
    await desktop('run')
    await context.setOffline(false)
    await sync.click()
    await web.getByRole('button', { name: /^比较冲突/ }).click()
    await expect(editor).toHaveValue('Web 归档冲突稿。')
    await compare.getByText('云端当前版', { exact: true }).click()
    await expect(compare.getByText('无此对象或已归档', { exact: true })).toBeVisible()
    web.once('dialog', (dialog) => dialog.accept())
    await compare.getByRole('button', { name: '采用云端当前版', exact: true }).click()
    await expect(web.getByRole('button', { name: '1. 拾铃', exact: true })).toHaveCount(0)
    expect((await desktop('run')).conflicts).toHaveLength(0)
  } finally {
    await context.close()
  }
})
