import { expect, test } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { openBench } from './helpers/creationFlow'

async function backupExcerpts(file: string) {
  const records = (await readFile(file, 'utf8'))
    .trimEnd()
    .split('\n')
    .slice(1, -1)
    .map((line) => JSON.parse(line.slice(65)))
  return records.find(([name]) => name === 'reference_history.excerpts')?.[2] as
    Record<string, string> | undefined
}

test('拆解台核对保存卡片，稿件包仅选带已预览的原句', async ({ page, browser }) => {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const novel = await page.request.post('/api/novels', { headers, data: { title: '手法卡稿件包验收' } })
  expect(novel.ok(), await novel.text()).toBe(true)
  const { id, branch_id } = await novel.json()
  const text = '😀渡口的守夜人没有交地图，只交了半幅旧图。'
  const imported = await page.request.post('/api/library/import', {
    headers,
    multipart: { file: { name: '参考卡样本.txt', mimeType: 'text/plain', buffer: Buffer.from(text) } },
  })
  expect(imported.ok(), await imported.text()).toBe(true)
  const [unit] = await (
    await page.request.get(`/api/library/books/${(await imported.json()).id}/units`)
  ).json()
  const goal = '分析有限证据如何改变行动'
  const reference = await page.request.post('/api/reference-sessions', {
    headers,
    data: { unit_id: unit.id, start: 0, end: text.length, quote: text, goal, branch_id },
  })
  expect(reference.ok(), await reference.text()).toBe(true)
  const session = await reference.json()
  const cards = [
    { id: 'limited-proof', technique: '解析一层可信证据', evidence: '😀渡口' },
    { id: 'other-proof', technique: '用余下疑点引出下一步', evidence: '半幅旧图' },
  ].map((card) => ({
    ...card,
    observation: '线索留下部分空白',
    interpretation: '读者可由细节继续观察',
    conditions: '主角仍有后续行动',
    exceptions: '替换原人物、地点与物件',
  }))
  const saved = await page.request.patch(`/api/reference-sessions/${session.id}`, {
    headers,
    data: { base_revision: 0, branch_id, goal, cards },
  })
  expect(saved.ok(), await saved.text()).toBe(true)
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(id)
  await openBench(page, '拆解台')
  await page.getByText('已保存手法卡与历史', { exact: true }).press('Enter')
  const card = page.getByText('解析一层可信证据 · 1 个修订', { exact: true })
  await card.press('Enter')
  const revision = card.locator('..').getByText('修订 1 · 可复用 · 当前版本', { exact: true })
  await revision.press('Enter')
  const detail = revision.locator('..')
  await expect(detail.getByText('参考卡样本.txt · 本机有可核对的原文')).toBeVisible()
  await detail.getByRole('button', { name: '查看证据原句' }).press('Enter')
  await expect(detail.getByText('😀渡口', { exact: true })).toBeVisible()
  await detail.getByText('查看来源编号', { exact: true }).press('Enter')
  await expect(detail.getByText(/手法卡版本 SHA-256 [a-f0-9]{64}/)).toBeVisible()

  await page.getByText('离线写作与交换', { exact: true }).click()
  const defaultDownload = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出当前稿件包', exact: true }).click()
  const defaultPackage = JSON.parse(await readFile((await (await defaultDownload).path())!, 'utf8'))
  expect(defaultPackage.reference_history.excerpts).toBeUndefined()
  expect(JSON.stringify(defaultPackage)).not.toContain(text)
  const plainBackup = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载独立作品备份', exact: true }).press('Enter')
  expect(await backupExcerpts((await (await plainBackup).path())!)).toBeUndefined()

  await page.getByText('选带指定手法卡原文', { exact: true }).click()
  const first = page.getByRole('checkbox', { name: /解析一层可信证据 · 修订 1/ })
  await first.check()
  await page.getByRole('button', { name: '预览选带的原文', exact: true }).click()
  const preview = page.getByRole('region', { name: '选带原文预览' })
  await expect(preview.getByText('😀渡口', { exact: true })).toBeVisible()
  await first.uncheck()
  await expect(preview).toHaveCount(0)
  await first.check()
  await page.getByRole('button', { name: '预览选带的原文', exact: true }).click()
  const optedDownload = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载含所选原文的稿件包' }).click()
  const opted = JSON.parse(await readFile((await (await optedDownload).path())!, 'utf8'))
  expect(Object.values(opted.reference_history.excerpts)).toEqual(['😀渡口'])
  expect(JSON.stringify(opted)).not.toContain('半幅旧图')
  const optedBackup = page.waitForEvent('download')
  await preview.getByRole('button', { name: '下载含所选原文的独立备份', exact: true }).press('Enter')
  expect(await backupExcerpts((await (await optedBackup).path())!)).toEqual(opted.reference_history.excerpts)

  if (process.env.AINOVEL_SYNC_E2E !== '1') return
  const branch = `/api/branches/${branch_id}`
  const cloud = 'http://127.0.0.1:8888'
  async function grant(device_id: string) {
    const response = await page.request.post(`${cloud}/api/sync/works/${branch_id}/devices`, {
      headers: { Authorization: 'Bearer e2e-test-only-provisioning-token' },
      data: { device_id },
    })
    expect(response.ok(), await response.text()).toBe(true)
    return (await response.json()).token as string
  }
  const desktopToken = await grant('desktop-reference')
  const webToken = await grant('web-reference')
  async function desktopSync(action: 'enable' | 'run') {
    const response = await page.request.post(`${branch}/desktop-sync/${action}`, {
      headers,
      data: { origin: cloud, token: desktopToken },
    })
    expect(response.ok(), await response.text()).toBe(true)
    return response.json()
  }
  await desktopSync('enable')
  await desktopSync('run')

  const reader = await browser.newContext()
  try {
    const web = await reader.newPage()
    await web.goto('http://127.0.0.1:8877/offline/')
    await expect(web.getByText('离线页面就绪')).toBeVisible()
    const baseline = structuredClone(defaultPackage)
    delete baseline.reference_history
    await web.getByLabel('导入新离线作品').setInputFiles({
      name: '共同作品.json',
      mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify(baseline)),
    })
    await web.getByText('作品云同步', { exact: true }).click()
    await web.getByLabel('同步服务地址').fill(cloud)
    await web.getByLabel('设备编号').fill('web-reference')
    await web.getByLabel('设备凭据').fill(webToken)
    await web.getByRole('button', { name: '启用作品同步', exact: true }).click()
    const sync = web.getByRole('button', { name: '同步当前工作版本', exact: true })
    await sync.click()
    const webArchive = web.getByLabel('离线手法卡历史')
    await webArchive.getByText('手法卡历史', { exact: true }).click()
    const choose = webArchive.getByRole('combobox', { name: '手法卡修订' })
    await choose.selectOption({ label: '解析一层可信证据 · 修订 1 · 可引用' })
    await expect(webArchive.getByText('档案保留了引用地址。这里没有附带原文。')).toBeVisible()
    await reader.setOffline(true)
    await webArchive.getByRole('button', { name: '编辑手法卡摘要' }).click()
    await webArchive.getByLabel('可迁移手法').fill('依据缺口推动下一次会面')
    await webArchive.getByRole('button', { name: '确认手法卡修订' }).click()
    await expect(webArchive.getByRole('status')).toHaveText('已保存手法卡修订，原出处和历史仍可查。')

    const edited = await page.request.patch(`/api/reference-sessions/${session.id}`, {
      headers,
      data: {
        base_revision: 1,
        branch_id,
        goal,
        cards: [{ ...cards[0], technique: '用矛盾线索制造回旋' }, cards[1]],
      },
    })
    expect(edited.ok(), await edited.text()).toBe(true)
    expect((await desktopSync('run')).conflicts).toHaveLength(0)
    await reader.setOffline(false)
    await sync.click()
    await web.getByRole('button', { name: /^比较冲突/ }).click()
    const comparison = web.getByRole('region', { name: '云同步冲突比较' })
    await expect(comparison).toContainText('依据缺口推动下一次会面')
    await expect(comparison).toContainText('用矛盾线索制造回旋')
    await expect(comparison).toContainText('解析一层可信证据')
    web.once('dialog', (dialog) => void dialog.accept())
    await comparison.getByRole('button', { name: '采用另一提交版' }).click()
    await expect(comparison).toHaveCount(0)
    await expect(choose.getByRole('option', { name: '用矛盾线索制造回旋 · 修订 2 · 可引用' })).toHaveCount(1)
    await desktopSync('run')
    const nativeCards = await (await page.request.get(`${branch}/reference-cards`)).json()
    const adopted = nativeCards.find(
      (row: { adopted: boolean; version: { card_id: string } }) =>
        row.adopted && row.version.card_id === 'limited-proof',
    )
    expect(adopted.version.card.technique).toBe('依据缺口推动下一次会面')
    const cloudData = await page.request.get(`${cloud}/api/sync/works/${branch_id}/pull?after=0&limit=100`, {
      headers: { Authorization: `Bearer ${webToken}` },
    })
    expect(cloudData.ok(), await cloudData.text()).toBe(true)
    const raw = await cloudData.text()
    expect(raw).not.toContain('😀渡口')
    expect(raw).not.toContain('半幅旧图')
  } finally {
    await reader.close()
  }
})
