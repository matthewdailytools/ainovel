import { expect, test, type APIResponse, type Locator, type Page, type Response } from '@playwright/test'
import { createHash } from 'node:crypto'
import { authorSettingPlan } from './fixtures/authorSettingPlan'

async function accepted(response: APIResponse | Response) {
  expect(response.ok(), await response.text()).toBe(true)
  return response.json()
}

type LedgerEvent = { sequence: number; object_id: string; status: string; payload: unknown }

function appliedEvent(events: LedgerEvent[], objectId: string) {
  const event = events.find((entry) => entry.object_id === objectId)
  if (!event) throw new Error(`云端账本缺少 ${objectId} 的回执。`)
  expect(event.status).toBe('applied')
  return event
}

test('M6-08 经真实云同步回读精确原文，撤回后新设备仍能查看历史', async ({ page, browser }) => {
  test.skip(process.env.AINOVEL_SYNC_E2E !== '1', '此用例使用隔离的真实同步账本')
  const { novel, chapter, original, branch, headers } = await authorSettingPlan(page, '体检云回源 · 归还赤钥')
  const baseline = await accepted(await page.request.get(branch + '/manuscript-package'))
  expect(baseline.setting_use_history).toBeUndefined()
  expect(baseline.workflow_history).toBeUndefined()
  const cloud = process.env.AINOVEL_TEST_SYNC_URL ?? 'http://127.0.0.1:8888'
  const service = `${cloud}/api/sync/works/${novel.branch_id}`
  const inspectionEndpoint = `${branch}/workflow-inspections`
  const evidenceEndpoint = `/api/chapters/${chapter.id}/setting-uses`
  const quote = '😀学徒递上赤钥，守铃人阿陶'
  const digest = (text: string) => createHash('sha256').update(text).digest('hex')

  async function grant(device_id: string) {
    const { token } = await accepted(
      await page.request.post(service + '/devices', {
        headers: { Authorization: 'Bearer e2e-test-only-provisioning-token' },
        data: { device_id },
      }),
    )
    return token as string
  }

  const desktopDevice = 'desktop-inspection-setting-use'
  const desktopToken = await grant(desktopDevice)
  async function syncDesktop(action: 'enable' | 'run') {
    const state = await accepted(
      await page.request.post(`${branch}/desktop-sync/${action}`, {
        headers,
        data: { origin: cloud, token: desktopToken },
      }),
    )
    expect(state.conflicts).toHaveLength(0)
    expect(state.deferred).toHaveLength(0)
  }

  async function ledger() {
    const authorization = { Authorization: `Bearer ${desktopToken}` }
    const { ledger_id } = await accepted(
      await page.request.get(service + '/checkpoint', { headers: authorization }),
    )
    const { changes, has_more } = await accepted(
      await page.request.get(service + '/pull?after=0&limit=100', {
        headers: { ...authorization, 'X-Ainovel-Ledger': ledger_id },
      }),
    )
    expect(has_more).toBe(false)
    return changes as LedgerEvent[]
  }

  async function generateInspection() {
    const inspection = page.getByRole('region', { name: '十处对账体检', exact: true })
    const submit = inspection.getByRole('button', { name: '生成体检单', exact: true })
    await expect(submit).toBeEnabled()
    const created = page.waitForResponse(
      (response) => response.request().method() === 'POST' && response.url().endsWith(inspectionEndpoint),
    )
    await submit.press('Enter')
    return accepted(await created)
  }

  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  const editor = page.getByRole('textbox', { name: '章节正文', exact: true })
  await expect(editor).toHaveText(original)
  const title = page.getByText('设定的正文使用证据', { exact: true })
  await title.press('Enter')
  const writing = title.locator('..')
  await writing.getByRole('combobox', { name: '本章引用的设定' }).selectOption('keeper')
  await editor.press('Control+Home')
  for (const _character of quote) await editor.press('Shift+ArrowRight')
  await expect(writing.locator('blockquote')).toHaveText(quote)
  await writing.getByRole('button', { name: '确认该选区为正文证据', exact: true }).press('Enter')
  await expect(writing.getByText('已确认当前正文使用该设定。', { exact: true })).toBeVisible()
  const { records } = await accepted(await page.request.get(evidenceEndpoint))
  const proof = records[0].version

  await page.getByRole('button', { name: '校验台', exact: true }).press('Enter')
  const originalReport = await generateInspection()
  const check08 = (report: typeof originalReport) =>
    report.items.find(({ check_id }: { check_id: string }) => check_id === '08')
  const use = check08(originalReport).basis.author_setting_uses[0]
  expect(use).toMatchObject({
    version_id: proof.id,
    item_id: 'keeper',
    chapter_sha256: digest(original),
    evidence: {
      chapter_id: chapter.id,
      revision_id: proof.evidence.revision_id,
      start: 0,
      end: quote.length,
      quote_sha256: digest(quote),
    },
  })
  expect(JSON.stringify(use)).not.toContain(quote)

  await syncDesktop('enable')
  await syncDesktop('run')
  const initialLedger = await ledger()
  const source = appliedEvent(initialLedger, `revision:${use.evidence.revision_id}`)
  const immutableProof = appliedEvent(initialLedger, `setting-use-version:${proof.id}`)
  const frozen = appliedEvent(initialLedger, `workflow-inspection:${originalReport.id}`)
  expect(source.sequence).toBeLessThan(immutableProof.sequence)
  expect(immutableProof.sequence).toBeLessThan(frozen.sequence)
  expect(JSON.stringify(frozen.payload)).not.toContain(quote)

  async function openRemote(workspace: Page, device: string) {
    const token = await grant(device)
    await workspace.goto(new URL('/offline/', page.url()).href)
    await expect(workspace.getByText('离线页面就绪', { exact: true })).toBeVisible()
    await expect.poll(() => workspace.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
    await workspace.getByLabel('导入新离线作品').setInputFiles({
      name: '仅稿件基线.json',
      mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify(baseline)),
    })
    const archive = workspace.getByLabel('历次体检与完本交付')
    await expect(archive).toHaveCount(0)
    await workspace.getByText('作品云同步', { exact: true }).click()
    await workspace.getByLabel('同步服务地址').fill(cloud)
    await workspace.getByLabel('设备编号').fill(device)
    await workspace.getByLabel('设备凭据').fill(token)
    await workspace.getByRole('button', { name: '启用作品同步', exact: true }).click()
    await workspace.getByRole('button', { name: '同步当前工作版本', exact: true }).click()
    await expect(archive).toBeVisible()
    await archive.getByText('历次体检与完本交付', { exact: true }).press('Enter')
    return archive
  }

  async function historicalProof(archive: Locator) {
    await archive.getByLabel('选择历史报告').selectOption(originalReport.id)
    await expect(archive.getByText(`源自同步设备 ${desktopDevice}`, { exact: false })).toBeVisible()
    const summary = archive.getByText(/^已确认引用与改稿影响 ·/)
    if ((await summary.locator('..').getAttribute('open')) === null) await summary.press('Enter')
    const viewSource = archive.getByRole('button', { name: /^查看「守铃人阿陶」原文/ })
    await expect(viewSource).toContainText(`位置 0–${quote.length}`)
    await viewSource.press('Enter')
    const excerpt = archive.getByRole('region', { name: '历史报告引用正文' })
    await expect(excerpt.locator('mark')).toHaveText(quote)
    await expect(excerpt.locator('pre')).toHaveText(original)
    await expect(excerpt).toContainText(`来源修订 ${use.evidence.revision_id} · SHA-256 ${digest(original)}`)
  }

  const first = await browser.newContext()
  try {
    await historicalProof(await openRemote(await first.newPage(), 'web-before-withdrawal'))
  } finally {
    await first.close()
  }

  await page.getByRole('button', { name: '写作台', exact: true }).press('Enter')
  await title.press('Enter')
  const adopted = writing.getByText('守铃人阿陶 · 当前正文证据', { exact: true })
  await adopted.press('Enter')
  page.once('dialog', (dialog) => void dialog.accept())
  await adopted.locator('..').getByRole('button', { name: '撤回这条正文证据', exact: true }).press('Enter')
  await expect(writing.getByText('已撤回正文使用证据。', { exact: true })).toBeVisible()
  const old = await accepted(await page.request.get(`${inspectionEndpoint}/${originalReport.id}`))
  expect(old.current).toBe(false)
  expect(check08(old).current_status).toBe('outdated')

  await page.getByRole('button', { name: '校验台', exact: true }).press('Enter')
  const afterWithdrawal = await generateInspection()
  expect(check08(afterWithdrawal).basis.author_setting_uses).toBeUndefined()
  await syncDesktop('run')
  const finalLedger = await ledger()
  const withdrawal = finalLedger.find(
    (entry) => entry.object_id.startsWith('setting-use-version:') && entry.sequence > frozen.sequence,
  )
  expect(withdrawal?.payload).toMatchObject({ version: { action: 'withdrawn', parent_id: proof.id } })
  appliedEvent(finalLedger, `workflow-inspection:${afterWithdrawal.id}`)

  const fresh = await browser.newContext()
  try {
    const archive = await openRemote(await fresh.newPage(), 'web-after-withdrawal')
    await expect(
      archive.getByLabel('选择历史报告').locator(`option[value="${afterWithdrawal.id}"]`),
    ).toHaveCount(1)
    await fresh.setOffline(true)
    await historicalProof(archive)
  } finally {
    await fresh.close()
  }

  const exported = await accepted(await page.request.get(branch + '/manuscript-package'))
  expect(exported.workflow_history.inspections.map(({ id }: { id: string }) => id)).toContain(
    originalReport.id,
  )
  expect(
    exported.setting_use_history.versions.map(({ version }: { version: { id: string } }) => version.id),
  ).toContain(proof.id)
})
