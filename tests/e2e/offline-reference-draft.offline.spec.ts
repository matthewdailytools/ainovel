import { expect, test, type APIResponse, type Page } from '@playwright/test'
import { readFile } from 'node:fs/promises'

const cloud = process.env.AINOVEL_TEST_SYNC_URL ?? 'http://127.0.0.1:8888'
const original = '让线索分成两步。'
const privateA = '草稿甲：删掉替换说明，准备再查。'
const privateB = '草稿乙：另试一条线索。'
const adopted = '正式修订：让每次取得的半幅地图都能独立验证。'
const quote = '😀渡口暗号'

test.skip(process.env.AINOVEL_SYNC_E2E !== '1', '使用隔离 Chromium 和临时云账本')

async function okay(response: APIResponse) {
  expect(response.ok(), await response.text()).toBe(true)
  return response.json()
}

async function openCard(tab: Page, projectId: string) {
  await tab.getByRole('combobox', { name: '离线作品', exact: true }).selectOption(projectId)
  const cards = tab.getByLabel('离线手法卡历史')
  await cards.getByText('手法卡历史', { exact: true }).press('Enter')
  return cards
}

test('手法卡草稿断网重开，复制标签分别保留，正式确认校验原修订并按边界同步', async ({ page, context }) => {
  await page.goto('/offline/')
  await expect(page.getByText('离线页面就绪', { exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
  const { csrf } = await okay(await page.request.get('/api/session'))
  const headers = { 'X-Ainovel-CSRF': csrf }
  const { id, branch_id } = await okay(
    await page.request.post('/api/novels', { headers, data: { title: '手法卡本机草稿 · 渡口' } }),
  )
  const imported = await okay(
    await page.request.post('/api/library/import', {
      headers,
      multipart: {
        file: { name: '渡口资料.txt', mimeType: 'text/plain', buffer: Buffer.from(`${quote}无人听清。`) },
      },
    }),
  )
  const [unit] = await okay(await page.request.get(`/api/library/books/${imported.id}/units`))
  const goal = '拆解不完整线索'
  const session = await okay(
    await page.request.post('/api/reference-sessions', {
      headers,
      data: { unit_id: unit.id, start: 0, end: quote.length, quote, goal, branch_id },
    }),
  )
  await okay(
    await page.request.patch(`/api/reference-sessions/${session.id}`, {
      headers,
      data: {
        base_revision: 0,
        branch_id,
        goal,
        cards: [
          {
            id: 'map-gap',
            observation: '只拿到一半地图。',
            interpretation: '部分答案推动下一步。',
            technique: original,
            conditions: '仍能根据已得线索寻找下一步。',
            exceptions: '改掉原人物和线索。',
            evidence: quote,
          },
        ],
      },
    }),
  )
  const pack = `/api/branches/${branch_id}/manuscript-package`
  const initial = await okay(await page.request.get(pack))
  const first = initial.reference_history.versions[0]
  const packageWithQuote = await okay(
    await page.request.get(pack, { params: { include_reference_evidence_for: first.id } }),
  )
  expect(packageWithQuote.reference_history.excerpts[first.id]).toBe(quote)
  const service = `${cloud}/api/sync/works/${branch_id}`
  const device_id = 'web-draft-m2'
  const { token } = await okay(
    await page.request.post(service + '/devices', {
      headers: { Authorization: 'Bearer e2e-test-only-provisioning-token' },
      data: { device_id },
    }),
  )
  await context.setOffline(true)
  await page.getByLabel('导入新离线作品').setInputFiles({
    name: '渡口合成基线.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(packageWithQuote)),
  })
  const cards = await openCard(page, id)
  await cards.getByRole('checkbox', { name: /随下一次稿件包导出/ }).check()
  await cards.getByRole('button', { name: '编辑手法卡摘要' }).click()
  await cards.getByLabel('可迁移手法').fill(privateA)
  await cards.getByLabel('须替换处').fill('')
  const saved = '手法卡草稿已暂存到本机。'
  await expect(cards.getByText(saved, { exact: true })).toBeVisible()

  async function reopen(tab: Page) {
    tab.once('dialog', (dialog) => dialog.accept())
    await tab.reload()
    await openCard(tab, id)
  }
  await reopen(page)
  await expect(cards.getByRole('checkbox', { name: /随下一次稿件包导出/ })).not.toBeChecked()
  await cards.getByRole('button', { name: '继续编辑本机手法卡草稿' }).press('Enter')
  await expect(cards.getByLabel('可迁移手法')).toHaveValue(privateA)
  await expect(cards.getByLabel('须替换处')).toHaveValue('')
  await expect(cards.getByRole('button', { name: '确认手法卡修订' })).toBeDisabled()
  await expect(cards.getByText(saved, { exact: true })).toBeVisible()

  const owner = await page.evaluate(() => sessionStorage.getItem('ainovel-reference-draft-window'))
  expect(owner).toBeTruthy()
  const other = await context.newPage()
  await other.addInitScript((stored) => {
    const key = 'ainovel-reference-draft-window'
    if (stored && !sessionStorage.getItem(key)) sessionStorage.setItem(key, stored)
  }, owner)
  await other.goto('/offline/')
  const otherCards = await openCard(other, id)
  await otherCards.getByRole('button', { name: '取用另一窗口的手法卡草稿副本' }).click()
  await otherCards.getByLabel('可迁移手法').fill(privateB)
  await expect(otherCards.getByText(saved)).toBeVisible()
  await reopen(other)
  await otherCards.getByRole('button', { name: '继续编辑本机手法卡草稿' }).click()
  await expect(otherCards.getByLabel('可迁移手法')).toHaveValue(privateB)
  await otherCards.getByLabel('可迁移手法').fill(adopted)
  await otherCards.getByLabel('须替换处').fill('换成两道自创门印。')
  await otherCards.getByRole('button', { name: '确认手法卡修订' }).click()
  await expect(otherCards.getByText('已保存手法卡修订，原出处和历史仍可查。')).toBeVisible()

  await reopen(page)
  await cards.getByRole('button', { name: '继续编辑本机手法卡草稿' }).click()
  await expect(cards.getByLabel('可迁移手法')).toHaveValue(privateA)
  await expect(cards.getByText(/采用版已在别处更新/)).toBeVisible()
  await expect(cards.getByRole('button', { name: '确认手法卡修订' })).toBeDisabled()

  const download = other.waitForEvent('download')
  await other.getByRole('button', { name: '导出稿件交换包' }).click()
  const exported = await readFile((await (await download).path())!, 'utf8')
  expect(exported).toContain(adopted)
  expect(exported).not.toContain(privateA)
  expect(exported).not.toContain(privateB)
  expect(exported).not.toContain(quote)
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
  const sync = other.getByRole('button', { name: '同步当前工作版本' })
  await sync.click()
  await expect.poll(() => sent.some((row) => row.includes(adopted))).toBe(true)
  await expect(sync).toBeEnabled()
  const bearer = { Authorization: `Bearer ${token}` }
  const { ledger_id } = await okay(await page.request.get(service + '/checkpoint', { headers: bearer }))
  const pulled = await okay(
    await page.request.get(service + '/pull', { headers: { ...bearer, 'X-Ainovel-Ledger': ledger_id } }),
  )
  const received = pulled.changes.find(
    (row: { payload?: { version?: { card?: { technique?: string } } } }) =>
      row.payload?.version?.card?.technique === adopted,
  )
  expect(received.payload.version).toMatchObject({ parent_id: first.id, citation: first.citation })
  for (const withheld of [privateA, privateB, quote]) {
    expect(JSON.stringify(pulled.changes)).not.toContain(withheld)
    expect(sent.join('\n')).not.toContain(withheld)
  }
})
