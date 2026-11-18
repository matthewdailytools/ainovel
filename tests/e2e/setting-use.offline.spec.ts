import { expect, test, type APIResponse, type Page } from '@playwright/test'
import { createHash } from 'node:crypto'

import { authorSettingPlan } from './fixtures/authorSettingPlan'

test('章纲中的人物条目经作者选取正式原文后才成为实际使用证据', async ({ page }) => {
  const { novel, chapter, original, readPlan } = await authorSettingPlan(page, '检视设定正文证据')
  expect((await readPlan()).settings).toEqual([
    expect.objectContaining({ chapter_id: chapter.id, evidence: null }),
  ])

  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  const editor = page.getByRole('textbox', { name: '章节正文', exact: true })
  await expect(editor).toHaveText(original)
  const title = page.getByText('设定的正文使用证据', { exact: true })
  await title.press('Enter')
  const panel = title.locator('..')
  const select = panel.getByRole('combobox', { name: '本章引用的设定', exact: true })
  await select.selectOption('keeper')
  const approve = panel.getByRole('button', { name: '确认该选区为正文证据', exact: true })
  await expect(approve).toBeDisabled()
  await editor.press('Control+End')
  await editor.press('Control+Shift+Home')
  await expect(panel.locator('blockquote')).toHaveText(original)
  await approve.press('Enter')
  await expect(panel.getByRole('status')).toContainText('已确认当前正文使用该设定。')
  const proof = panel.getByText('守铃人阿陶 · 当前正文证据', { exact: true })
  await proof.press('Enter')
  await expect(proof.locator('..').locator('blockquote')).toHaveText(original)
  const useHistory = await (await page.request.get(`/api/chapters/${chapter.id}/setting-uses`)).json()
  expect(useHistory.records).toEqual([
    expect.objectContaining({
      quote: original,
      status: 'current',
      version: expect.objectContaining({
        item_id: 'keeper',
        evidence: expect.objectContaining({ start: 0, end: original.length }),
      }),
    }),
  ])
  expect((await readPlan()).settings).toEqual([
    expect.objectContaining({
      chapter_id: chapter.id,
      author_evidence: expect.objectContaining({ quote: original, status: 'current' }),
    }),
  ])
  await page.getByRole('button', { name: '大纲与台账', exact: true }).press('Enter')
  const actual = page.getByRole('listitem').filter({ hasText: '作者已确认正文使用' })
  await expect(actual).toContainText(original)

  await page.getByRole('button', { name: '写作', exact: true }).press('Enter')
  await title.press('Enter')
  await proof.press('Enter')
  page.once('dialog', (confirmation) => void confirmation.accept())
  await proof.locator('..').getByRole('button', { name: '撤回这条正文证据', exact: true }).press('Enter')
  await expect(panel.getByRole('status')).toContainText('已撤回正文使用证据。')
  const withdrawn = panel.getByText('守铃人阿陶 · 已撤回', { exact: true })
  await withdrawn.press('Enter')
  await expect(withdrawn.locator('..').locator('blockquote')).toHaveText(original)
  expect((await readPlan()).settings[0]).not.toHaveProperty('author_evidence')
})

test('单章恢复遇到另一窗口签认设定原文时要求重读，恢复后可查看历史证明', async ({ page }) => {
  const { novel, chapter, original, headers } = await authorSettingPlan(page, '恢复前核对设定证据')
  const chapterUrl = `/api/chapters/${chapter.id}`
  const current = original + '他把钥齿的记号抄入册中。'
  await accepted(
    await page.request.put(chapterUrl, { headers, data: { base_revision: 1, content: current } }),
  )
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await expect(page.getByRole('textbox', { name: '章节正文', exact: true })).toHaveText(current)
  await page.setViewportSize({ width: 360, height: 820 })
  const history = page.getByRole('button', { name: '版本历史', exact: true })
  await expect(history).toBeEnabled()
  await history.press('Enter')
  const old = page.locator('.history details').filter({
    has: page.locator('summary').filter({ hasText: '修订 1 · 作者编辑' }),
  })
  await old.locator('summary').press('Enter')
  await old.getByRole('button', { name: '预览恢复', exact: true }).press('Enter')
  const preview = old.getByRole('region', { name: '章节恢复预览' })
  await expect(preview).toBeVisible()
  await expect(preview.getByText(/设定正文使用 · 守铃人阿陶/)).toHaveCount(0)

  const another = await page.context().newPage()
  try {
    await another.goto('/')
    await another.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
    const editor = another.getByRole('textbox', { name: '章节正文', exact: true })
    await expect(editor).toHaveText(current)
    const trigger = another.getByText('设定的正文使用证据', { exact: true })
    await trigger.press('Enter')
    const evidence = trigger.locator('..')
    await evidence.getByRole('combobox', { name: '本章引用的设定' }).selectOption('keeper')
    await editor.press('Control+End')
    await editor.press('Control+Shift+Home')
    await expect(evidence.locator('blockquote')).toHaveText(current)
    await evidence.getByRole('button', { name: '确认该选区为正文证据' }).press('Enter')
    await expect(evidence.getByRole('status')).toContainText('已确认当前正文使用该设定。')
  } finally {
    await another.close()
  }

  const apply = old.getByRole('button', { name: '确认恢复为新修订', exact: true })
  await apply.press('Enter')
  await expect(page.locator('.history').getByRole('alert')).toContainText('请重新读取恢复预览')
  await expect(apply).toBeDisabled()
  expect((await accepted(await page.request.get(chapterUrl))).content).toBe(current)
  const retry = old.getByRole('button', { name: '重新读取恢复预览' })
  await expect(retry).toBeFocused()
  await retry.press('Enter')
  const affected = preview.getByRole('listitem').filter({ hasText: '设定正文使用 · 守铃人阿陶' })
  await expect(affected.locator('blockquote pre')).toHaveText(current)
  await expect(affected).toContainText(`修订 2 · 位置 0–${current.length}`)
  const { records } = await accepted(await page.request.get(chapterUrl + '/setting-uses'))
  const proof = records[0].version
  await expect(affected).toContainText(proof.evidence.revision_id)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await apply.press('Enter')
  await expect(page.getByRole('textbox', { name: '章节正文', exact: true })).toHaveText(original)
  expect(await accepted(await page.request.get(`${chapterUrl}/setting-uses/${proof.id}`))).toMatchObject({
    status: 'outdated',
    quote: current,
    version: proof,
  })
})

async function accepted(response: APIResponse) {
  expect(response.ok(), await response.text()).toBe(true)
  return response.json()
}

async function openOfflineEvidence(page: Page, novelId: string, chapterTitle: string) {
  await page.getByRole('combobox', { name: '离线作品', exact: true }).selectOption(novelId)
  const chapter = page
    .getByRole('navigation', { name: '离线章节目录' })
    .getByRole('button', { name: chapterTitle })
  await expect(chapter).toBeEnabled()
  await chapter.press('Enter')
  const evidence = page.getByLabel('离线设定正文证据')
  await evidence.getByText('设定的正文使用证据', { exact: true }).press('Enter')
  return evidence
}

test('桌面与独立 Web 双向同步设定使用证据，断网可从备份恢复撤回和原句', async ({ page, browser }) => {
  test.skip(process.env.AINOVEL_SYNC_E2E !== '1', '使用隔离云账本检验双向正式采用')
  const { novel, chapter, original, headers, branch, readPlan } = await authorSettingPlan(
    page,
    '跨端设定证据',
  )
  const source = await accepted(await page.request.get(branch + '/manuscript-package'))
  expect(source.setting_use_history).toBeUndefined()
  const endpoint = `/api/chapters/${chapter.id}/setting-uses`
  const { basis } = await accepted(await page.request.get(endpoint))
  const hash = (text: string) => createHash('sha256').update(text).digest('hex')
  const first = await accepted(
    await page.request.post(endpoint, {
      headers,
      data: {
        outline_version_id: basis.outline_version_id,
        settings_version_id: basis.settings_version_id,
        item_id: 'keeper',
        parent_id: null,
        chapter_sha256: basis.chapter_sha256,
        evidence: {
          chapter_id: chapter.id,
          revision_id: basis.chapter_revision_id,
          start: 0,
          end: original.length,
          quote_sha256: hash(original),
        },
        idempotency_key: `desktop-keeper-${chapter.id}`,
      },
    }),
  )
  expect(first.status).toBe('current')
  const cloud = process.env.AINOVEL_TEST_SYNC_URL ?? 'http://127.0.0.1:8888'
  const service = `${cloud}/api/sync/works/${novel.branch_id}`
  async function grant(device_id: string) {
    const { token } = await accepted(
      await page.request.post(service + '/devices', {
        headers: { Authorization: 'Bearer e2e-test-only-provisioning-token' },
        data: { device_id },
      }),
    )
    return token as string
  }
  const desktopToken = await grant('desktop-setting-use')
  const webToken = await grant('web-setting-use')
  async function syncDesktop(action: 'enable' | 'run') {
    return accepted(
      await page.request.post(`${branch}/desktop-sync/${action}`, {
        headers,
        data: { origin: cloud, token: desktopToken },
      }),
    )
  }
  await syncDesktop('enable')
  expect((await syncDesktop('run')).conflicts).toHaveLength(0)

  const context = await browser.newContext()
  const fresh = await browser.newContext()
  try {
    const offlineURL = new URL('/offline/', page.url()).href
    const web = await context.newPage()
    await web.goto(offlineURL)
    await expect(web.getByText('离线页面就绪', { exact: true })).toBeVisible()
    await expect.poll(() => web.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
    const importManuscript = web.getByLabel('导入新离线作品')
    await expect(importManuscript).toBeEnabled()
    await importManuscript.setInputFiles({
      name: '无证据基线.json',
      mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify(source)),
    })
    const board = await openOfflineEvidence(web, novel.id, chapter.title)
    const outline = web.getByLabel('离线大纲台')
    await outline.getByText('大纲台', { exact: true }).press('Enter')
    await outline.getByText('非世界设定使用台账', { exact: true }).press('Enter')
    await expect(outline.getByText('归还赤钥 · 守铃人阿陶 · 仅计划采用', { exact: true })).toBeVisible()

    await web.getByText('作品云同步', { exact: true }).click()
    await web.getByLabel('同步服务地址').fill(cloud)
    await web.getByLabel('设备编号').fill('web-setting-use')
    await web.getByLabel('设备凭据').fill(webToken)
    await web.getByRole('button', { name: '启用作品同步', exact: true }).click()
    const sync = web.getByRole('button', { name: '同步当前工作版本', exact: true })
    await sync.click()
    const fromDesktop = board.getByText('守铃人阿陶 · 当前正文证据', { exact: true })
    await fromDesktop.press('Enter')
    await expect(fromDesktop.locator('..').locator('blockquote')).toHaveText(original)
    const ledger = outline.getByText('归还赤钥 · 守铃人阿陶 · 当前正文证据', { exact: true })
    await ledger.press('Enter')
    await expect(ledger.locator('..').locator('blockquote')).toHaveText(original)
    await expect(sync).toBeEnabled()
    await context.setOffline(true)

    const editor = web.getByRole('textbox', { name: '章节正文', exact: true })
    const choice = board.getByRole('combobox', { name: '本章引用的设定' })
    const confirm = board.getByRole('button', { name: '确认该选区为正文证据', exact: true })
    const quote = '守铃人阿陶接过。'
    const unconfirmed = '以下是尚未保存也未签认的草稿。'
    await choice.selectOption('keeper')
    await editor.fill(unconfirmed)
    await editor.press('Control+A')
    await expect(confirm).toBeDisabled()
    await editor.fill(original)
    await editor.press('Control+End')
    for (const character of quote) {
      expect(character.length).toBe(1)
      await editor.press('Shift+ArrowLeft')
    }
    await expect(
      board.getByRole('group', { name: '确认保存稿中的设定使用' }).locator('blockquote'),
    ).toHaveText(quote)
    await confirm.press('Enter')
    await expect(board.getByText('已确认当前正文使用该设定。', { exact: true })).toBeVisible()
    const fromWeb = board.getByText('守铃人阿陶 · 当前正文证据', { exact: true })
    await fromWeb.press('Enter')
    await expect(fromWeb.locator('..').locator('blockquote')).toHaveText(quote)
    await expect(board.getByText('守铃人阿陶 · 已由新证据接替', { exact: true })).toBeVisible()

    await context.setOffline(false)
    const outbound: string[] = []
    web.on('request', (request) => {
      if (request.method() === 'POST' && request.url() === service + '/push')
        outbound.push(request.postData() ?? '')
    })
    await sync.click()
    await expect.poll(() => outbound.some((body) => body.includes('"kind":"setting-use-head"'))).toBe(true)
    await expect(sync).toBeEnabled()
    expect(outbound.join('')).not.toContain(unconfirmed)
    for (const body of outbound.filter((entry) => entry.includes('"kind":"setting-use-version"'))) {
      expect(body).not.toContain(original)
      expect(body).not.toContain(quote)
    }
    expect((await syncDesktop('run')).conflicts).toHaveLength(0)
    const native = await accepted(await page.request.get(endpoint))
    const second = native.records.find(({ status }: { status: string }) => status === 'current')
    expect(second).toMatchObject({
      quote,
      version: {
        parent_id: first.version.id,
        evidence: {
          revision_id: basis.chapter_revision_id,
          start: original.length - quote.length,
          end: original.length,
          quote_sha256: hash(quote),
        },
      },
    })
    expect((await readPlan()).settings[0].author_evidence).toMatchObject({ quote })

    const withdrawal = await accepted(
      await page.request.post(`${endpoint}/${second.version.id}/withdraw`, {
        headers,
        data: { idempotency_key: `withdraw-web-proof-${chapter.id}` },
      }),
    )
    expect(withdrawal.version).toMatchObject({
      action: 'withdrawn',
      parent_id: second.version.id,
      evidence: second.version.evidence,
    })
    expect((await syncDesktop('run')).conflicts).toHaveLength(0)
    await sync.click()
    await expect(board.getByText('守铃人阿陶 · 已撤回', { exact: true })).toBeVisible()
    const downloadBackup = web.getByRole('button', { name: '下载独立作品备份', exact: true })
    await expect(downloadBackup).toBeEnabled()
    const [backedUp] = await Promise.all([
      web.waitForEvent('download', { timeout: 10_000 }),
      downloadBackup.press('Enter'),
    ])
    const path = await backedUp.path()
    if (!path) throw new Error('缺少独立作品备份文件。')

    const restored = await fresh.newPage()
    await restored.goto(offlineURL)
    await expect(restored.getByText('离线页面就绪', { exact: true })).toBeVisible()
    await expect.poll(() => restored.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
    await fresh.setOffline(true)
    const importBackup = restored.getByLabel('从独立备份恢复作品')
    await expect(importBackup).toBeEnabled()
    await importBackup.setInputFiles(path)
    const restoredEvidence = await openOfflineEvidence(restored, novel.id, chapter.title)
    const withdrawn = restoredEvidence.getByText('守铃人阿陶 · 已撤回', { exact: true })
    await withdrawn.press('Enter')
    await expect(withdrawn.locator('..')).toContainText(basis.chapter_revision_id)
    await expect(withdrawn.locator('..').locator('blockquote')).toHaveText(quote)
    const historic = restoredEvidence.getByText('守铃人阿陶 · 已由新证据接替', { exact: true })
    await expect(historic).toHaveCount(2)
    for (const record of await historic.all()) await record.press('Enter')
    await expect(restoredEvidence.locator('blockquote').filter({ hasText: original })).toHaveCount(1)
  } finally {
    await context.close()
    await fresh.close()
  }
})
