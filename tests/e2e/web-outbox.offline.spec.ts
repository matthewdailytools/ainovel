import { expect, test } from '@playwright/test'

const cloud = 'http://127.0.0.1:8888'
test.skip(process.env.AINOVEL_SYNC_E2E !== '1', '使用隔离云端进程运行双端验收')

test('浏览器重开后用原变更编号重试，并保留等待同步的新稿', async ({ page, browser }) => {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const created = await page.request.post('/api/novels', {
    headers,
    data: { title: '退信后继续写', brief: '拾得一枚铃。' },
  })
  expect(created.ok(), await created.text()).toBe(true)
  const { branch_id } = await created.json()
  const branch = `/api/branches/${branch_id}`
  const chapter = await page.request.post(branch + '/chapters', { headers, data: { title: '拾铃' } })
  expect(chapter.ok(), await chapter.text()).toBe(true)
  const id = `chapter:${(await chapter.json()).id}`
  const service = `${cloud}/api/sync/works/${branch_id}`
  const grant = await page.request.post(service + '/devices', {
    headers: { Authorization: 'Bearer e2e-test-only-provisioning-token' },
    data: { device_id: 'web-outbox' },
  })
  expect(grant.ok(), await grant.text()).toBe(true)
  const { token } = await grant.json()
  const pack = await (await page.request.get(branch + '/manuscript-package')).body()
  const context = await browser.newContext()
  try {
    const web = await context.newPage()
    await web.goto('http://127.0.0.1:8877/offline/')
    await web
      .getByLabel('导入新离线作品')
      .setInputFiles({ name: '稿件.json', mimeType: 'application/json', buffer: pack })
    async function openChapterAndSync() {
      await web
        .getByRole('combobox', { name: '离线作品', exact: true })
        .selectOption({ label: '退信后继续写' })
      await web.getByRole('button', { name: '1. 拾铃', exact: true }).click()
      await web.getByText('作品云同步', { exact: true }).click()
      await web.getByLabel('同步服务地址').fill(cloud)
      await web.getByLabel('设备编号').fill('web-outbox')
      await web.getByLabel('设备凭据').fill(token)
    }
    const sync = web.getByRole('button', { name: '同步当前工作版本', exact: true })
    const editor = web.getByRole('textbox', { name: '章节正文', exact: true })
    const save = web.getByRole('button', { name: '保存正文到浏览器', exact: true })
    await openChapterAndSync()
    await web.getByRole('button', { name: '启用作品同步', exact: true }).click()
    await editor.fill('断线前的章稿。')
    await save.click()
    await expect(web.getByText(/本地已保存 · 修订 1/)).toBeVisible()

    const requests: string[] = []
    await web.route(service + '/push', async (route) => {
      const request = route.request().method() === 'POST' ? route.request().postDataJSON() : null
      if (request?.object_id === id) {
        requests.push(request.change_id)
        if (requests.length === 1) return route.abort()
      }
      await route.continue()
    })
    await sync.click()
    await expect(web.getByRole('alert')).toBeVisible()
    await expect.poll(() => requests.length).toBe(1)
    await web.reload()
    await openChapterAndSync()
    await expect(editor).toHaveValue('断线前的章稿。')
    await editor.fill('重新打开后继续保存的新稿。')
    await save.click()
    await expect(web.getByText(/本地已保存 · 修订 2/)).toBeVisible()
    await sync.click()
    await expect(web.getByText(/正式保存仍待同步/)).toBeVisible()
    expect(requests.slice(0, 2)).toEqual([requests[0], requests[0]])
    await sync.click()
    await expect(web.getByText(/正式保存仍待同步/)).toHaveCount(0)
    await expect(sync).toBeEnabled()
    const pulled = await page.request.get(service + '/pull', {
      headers: { Authorization: `Bearer ${token}` },
    })
    expect(pulled.ok(), await pulled.text()).toBe(true)
    const heads = (await pulled.json()).changes.filter((row: { object_id: string }) => row.object_id === id)
    expect(heads.map((row: { status: string }) => row.status)).toEqual(['applied', 'applied'])
    expect(heads[1].parent_revision).toBe(requests[0])
    expect(heads[1].payload.head.content).toBe('重新打开后继续保存的新稿。')
    expect(requests[2]).not.toBe(requests[0])
  } finally {
    await context.close()
  }
})
