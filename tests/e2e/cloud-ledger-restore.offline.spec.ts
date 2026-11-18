import { expect, test, type APIResponse, type Page } from '@playwright/test'

const cloud = process.env.AINOVEL_LEDGER_SYNC_ORIGIN ?? 'http://127.0.0.1:8888'
const provisioning = { Authorization: 'Bearer e2e-test-only-provisioning-token' }
const replaced = '同步历史不一致'
const retained = '本机稿和待同步记录已保留'

test.skip(process.env.AINOVEL_SYNC_E2E !== '1', '需要隔离云端和 SQLite 备份恢复夹具')

async function checked(response: APIResponse) {
  expect(response.ok(), await response.text()).toBe(true)
  return response.json()
}

function syncForm(web: Page) {
  return {
    editor: web.getByRole('textbox', { name: '章节正文', exact: true }),
    service: web.getByLabel('同步服务地址'),
    device: web.getByLabel('设备编号'),
    token: web.getByLabel('设备凭据'),
    enable: web.getByRole('button', { name: '启用作品同步', exact: true }),
    sync: web.getByRole('button', { name: '同步当前工作版本', exact: true }),
    compare: web.getByRole('button', { name: /^比较冲突/ }),
    status: web.getByRole('status').filter({ hasText: /已启用 · 游标/ }),
    warning: web.getByRole('alert').filter({ hasText: replaced }),
  }
}

test('恢复低位云账本后旧键及换号仍挡写，独立 Web 可读回受保护历史', async ({ page, browser, baseURL }) => {
  await page.goto('/')
  const { csrf } = await checked(await page.request.get('/api/session'))
  const headers = { 'X-Ainovel-CSRF': csrf }
  const title = '遗铃 · 恢复云账本'
  const novel = await checked(await page.request.post('/api/novels', { headers, data: { title } }))
  const branch = `/api/branches/${novel.branch_id}`
  const work = `${cloud}/api/sync/works/${novel.branch_id}`
  const seed = '学徒把铜铃放在门边。'
  const oldRemote = '旧账本的桌面稿，师姐把铜铃收进木盒。'
  const oldLocal = '旧设备本机稿，学徒把铜铃送还原主。'
  const outbox = '恢复前已正式保存且尚未发送的第二章。'
  const first = await checked(
    await page.request.post(`${branch}/chapters`, { headers, data: { title: '旧渡口' } }),
  )
  const second = await checked(
    await page.request.post(`${branch}/chapters`, { headers, data: { title: '铃归处' } }),
  )

  async function saveDesktop(id: string, revision: number, content: string) {
    return checked(
      await page.request.put(`/api/chapters/${id}`, { headers, data: { base_revision: revision, content } }),
    )
  }
  async function grant(device_id: string): Promise<string> {
    return (
      await checked(
        await page.request.post(`${work}/devices`, { headers: provisioning, data: { device_id } }),
      )
    ).token
  }
  async function checkpoint(token: string): Promise<{ ledger_id: string; cursor: number }> {
    const value = await checked(
      await page.request.get(`${work}/checkpoint`, { headers: { Authorization: `Bearer ${token}` } }),
    )
    expect(value.ledger_id).toMatch(/^[a-f0-9]{32}$/)
    expect(Number.isSafeInteger(value.cursor)).toBe(true)
    return value
  }
  function native(action: 'enable' | 'run', token: string) {
    return page.request.post(`${branch}/desktop-sync/${action}`, { headers, data: { origin: cloud, token } })
  }
  function ledgerFixture(action: 'backup' | 'restore' | 'finish') {
    return page.request.post(`${cloud}/__e2e/ledger/${action}`, { headers: provisioning })
  }

  await saveDesktop(first.id, 0, seed)
  await saveDesktop(second.id, 0, '原始第二章。')
  const pack = await checked(await page.request.get(`${branch}/manuscript-package`))
  const empty = Buffer.from(JSON.stringify({ ...pack, chapters: [], revisions: [] }))
  const nativeToken = await grant('ledger-native')
  const oldToken = await grant('ledger-old-web')
  await checked(await native('enable', nativeToken))
  await checked(await native('run', nativeToken))
  const original = await checkpoint(oldToken)
  expect(original.cursor).toBeGreaterThan(2)

  async function openReplica(web: Page, device: string, token: string) {
    await web.goto(new URL('/offline/', baseURL).toString())
    await web.getByLabel('导入新离线作品').setInputFiles({
      name: 'empty-synthetic-work.json',
      mimeType: 'application/json',
      buffer: empty,
    })
    await web.getByRole('combobox', { name: '离线作品', exact: true }).selectOption({ label: title })
    await web.getByText('作品云同步', { exact: true }).click()
    const panel = syncForm(web)
    await panel.service.fill(cloud)
    await panel.device.fill(device)
    await panel.token.fill(token)
    await panel.enable.click()
    await panel.sync.click()
    await web.getByRole('button', { name: '1. 旧渡口', exact: true }).click()
    await expect(panel.editor).toHaveValue(seed)
    return panel
  }

  const oldBrowser = await browser.newContext({ viewport: { width: 420, height: 900 } })
  const restoredBrowser = await browser.newContext()
  try {
    const oldWeb = await oldBrowser.newPage()
    const old = await openReplica(oldWeb, 'ledger-old-web', oldToken)
    await expect(old.status).toContainText(`游标 ${original.cursor} · 0 条待发送或确认`)
    await checked(await ledgerFixture('backup'))

    await saveDesktop(first.id, 1, oldRemote)
    await checked(await native('run', nativeToken))
    await old.editor.fill(oldLocal)
    await oldWeb.getByRole('button', { name: '保存正文到浏览器', exact: true }).click()
    await expect(oldWeb.getByText(/本地已保存 · 修订 2/)).toBeVisible()
    await old.sync.press('Enter')
    await old.compare.click()
    const comparison = oldWeb.getByRole('region', { name: '云同步冲突比较', exact: true })
    await comparison.getByText('云端当前版', { exact: true }).click()
    await expect(comparison.getByText(oldRemote, { exact: true })).toBeVisible()
    await expect(old.editor).toHaveValue(oldLocal)
    const originalHigh = await checkpoint(oldToken)
    expect(originalHigh.cursor).toBeGreaterThan(original.cursor)
    await expect(old.status).toContainText(`游标 ${originalHigh.cursor}`)
    const oldConflict = await old.compare.getAttribute('title')

    await oldWeb.getByRole('button', { name: '2. 铃归处', exact: true }).click()
    await old.editor.fill(outbox)
    await oldWeb.getByRole('button', { name: '保存正文到浏览器', exact: true }).click()
    await expect(oldWeb.getByText(/本地已保存 · 修订 2/)).toBeVisible()
    await oldWeb.getByRole('button', { name: '1. 旧渡口', exact: true }).click()
    await expect(old.editor).toHaveValue(oldLocal)

    await checked(await ledgerFixture('restore'))
    const expired = await page.request.get(`${work}/checkpoint`, {
      headers: { Authorization: `Bearer ${oldToken}` },
    })
    expect([401, 403]).toContain(expired.status())
    const rotated = await grant('ledger-old-web')
    const newNativeToken = await grant('ledger-native')
    const restored = await checkpoint(rotated)
    expect(restored.ledger_id).not.toBe(original.ledger_id)
    expect(restored.cursor).toBe(original.cursor)
    const rejectedDesktop = await native('run', newNativeToken)
    expect(rejectedDesktop.status()).toBe(409)
    expect(await rejectedDesktop.json()).toMatchObject({ code: 'sync_ledger_replaced' })
    expect((await checked(await page.request.get(`/api/chapters/${first.id}`))).content).toBe(oldRemote)

    const independentToken = await grant('ledger-independent-web')
    const newWeb = await restoredBrowser.newPage()
    const independent = await openReplica(newWeb, 'ledger-independent-web', independentToken)
    await newWeb.getByText('本章版本历史', { exact: true }).click()
    await newWeb
      .getByRole('combobox', { name: '历史修订', exact: true })
      .selectOption({ label: '修订 1 · 作者编辑' })
    await expect(newWeb.locator('[aria-label="历史稿 · 修订 1"]')).toHaveText(seed)

    let newCursor = restored.cursor
    const replacementProse: string[] = []
    // A new history occupies the old client's exact sequence, so cursor monotonicity alone cannot detect replacement.
    for (let index = 0; newCursor < originalHigh.cursor && index < 4; index += 1) {
      const content = `恢复副本独有正文 ${index + 1}，钟房开窗。`
      replacementProse.push(content)
      await independent.editor.fill(content)
      await newWeb.getByRole('button', { name: '保存正文到浏览器', exact: true }).click()
      await independent.sync.click()
      await expect(independent.sync).toBeEnabled()
      const latest = await checkpoint(independentToken)
      expect(latest.cursor).toBeGreaterThan(newCursor)
      newCursor = latest.cursor
    }
    expect(newCursor).toBeGreaterThanOrEqual(originalHigh.cursor)
    const restoredChanges = await checked(
      await page.request.get(`${work}/pull?after=${restored.cursor}&limit=100`, {
        headers: { Authorization: `Bearer ${independentToken}`, 'X-Ainovel-Ledger': restored.ledger_id },
      }),
    )
    expect(
      restoredChanges.changes.some((change: { sequence: number }) => change.sequence === originalHigh.cursor),
    ).toBe(true)
    expect(JSON.stringify(restoredChanges)).toContain(replacementProse[0])
    const staleHeader = await page.request.get(`${work}/pull?after=${originalHigh.cursor}&limit=1`, {
      headers: { Authorization: `Bearer ${rotated}`, 'X-Ainovel-Ledger': original.ledger_id },
    })
    expect(staleHeader.status()).toBe(409)
    expect(await staleHeader.json()).toMatchObject({ code: 'sync_ledger_replaced' })

    const outgoing: string[] = []
    oldWeb.on('request', (request) => {
      if (request.method() !== 'OPTIONS' && request.url().startsWith(work)) {
        outgoing.push(new URL(request.url()).pathname)
      }
    })
    await old.token.fill(rotated)
    await old.sync.press('Enter')
    await expect(old.warning).toContainText(retained)
    await expect(old.status).toContainText(`游标 ${originalHigh.cursor}`)
    await expect(old.editor).toHaveValue(oldLocal)
    await expect(old.compare).toHaveAttribute('title', oldConflict!)

    await old.compare.click()
    await comparison.getByRole('button', { name: '手动合并正文', exact: true }).click()
    const merge = comparison.getByRole('textbox', { name: '合并正文', exact: true })
    const mergeDraft = '切换账本后仍保留手工合并草案。'
    await merge.fill(mergeDraft)
    oldWeb.once('dialog', (dialog) => dialog.accept())
    await comparison.getByRole('button', { name: '提交合并章稿', exact: true }).click()
    await expect(old.warning).toContainText(retained)
    await expect(merge).toHaveValue(mergeDraft)
    await expect(old.editor).toHaveValue(oldLocal)
    oldWeb.once('dialog', (dialog) => dialog.accept())
    await comparison.getByRole('button', { name: '放弃合并输入', exact: true }).click()

    const reissued = await grant('ledger-new-device-number')
    await old.device.fill('ledger-new-device-number')
    await old.token.fill(reissued)
    await old.enable.click()
    await expect(old.warning).toContainText(retained)
    await expect(old.sync).toBeDisabled()
    expect(outgoing.length).toBeGreaterThanOrEqual(3)
    expect(outgoing.filter((path) => !path.endsWith('/checkpoint'))).toEqual([])
    expect((await checkpoint(independentToken)).cursor).toBe(newCursor)

    await oldWeb.reload()
    await oldWeb.getByRole('combobox', { name: '离线作品', exact: true }).selectOption({ label: title })
    await oldWeb.getByRole('button', { name: '2. 铃归处', exact: true }).click()
    await expect(old.editor).toHaveValue(outbox)
    await oldWeb.getByText('作品云同步', { exact: true }).click()
    await old.service.fill(cloud)
    await old.device.fill('ledger-old-web')
    await expect(old.status).toContainText('正式保存仍待同步')
    await expect(old.compare).toHaveAttribute('title', oldConflict!)
  } finally {
    await oldBrowser.close()
    await restoredBrowser.close()
    expect((await ledgerFixture('finish')).status()).toBe(204)
  }
})
