import { expect, test, type Page } from '@playwright/test'
import { assertActualDoubleText } from './helpers/actualTextScale'

type Fixture = {
  novel_id: string
  branch_id: string
  chapter_id: string
  parent_id: string
  candidate_id: string
}
const sourceSubmit = '提交AI章纲范围纠偏（额外模型费用）'
const optionName = '核对材料的不同称谓'
async function fixture(
  page: Page,
  mode: 'same' | 'unknown' | 'different' | 'exact' | 'final_unknown' = 'same',
): Promise<Fixture> {
  const session = await (await page.request.get('/api/session')).json()
  const response = await page.request.post('/test/prepared-object-alias/fixtures', {
    headers: { 'X-Ainovel-CSRF': session.csrf },
    data: { client_request_id: crypto.randomUUID(), mode },
  })
  expect(response.status(), await response.text()).toBe(201)
  return response.json()
}
async function open(page: Page, row: Fixture) {
  await page.goto('/')
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(row.novel_id)
  await page.getByRole('button', { name: '写作', exact: true }).click()
  await page.getByRole('combobox', { name: '比较候选', exact: true }).selectOption(row.candidate_id)
  await page.getByText('核对AI章纲范围，再显式复检正文', { exact: true }).click()
  return page.getByRole('region', { name: 'AI章纲范围来源纠偏', exact: true })
}
async function prepare(page: Page, row: Fixture) {
  const panel = await open(page, row)
  await expect(panel.getByRole('checkbox', { name: optionName, exact: true })).not.toBeChecked()
  await panel.getByRole('checkbox', { name: optionName, exact: true }).check()
  await panel.getByRole('button', { name: '读取章纲范围依据', exact: true }).click()
  await panel.getByRole('checkbox', { name: /^P01/ }).check()
  return panel
}
async function task(page: Page, id: string) {
  await expect
    .poll(async () => (await (await page.request.get(`/api/tasks/${id}`)).json()).status, { timeout: 25000 })
    .toBe('succeeded')
  return (await page.request.get(`/api/tasks/${id}`)).json()
}
async function submit(page: Page, row: Fixture, panel: ReturnType<Page['getByRole']>) {
  const posted = page.waitForResponse(
    (response) =>
      response.url().endsWith('/prepared-source-scope-corrections') && response.request().method() === 'POST',
  )
  await panel.getByRole('button', { name: sourceSubmit, exact: true }).click()
  const response = await posted
  expect(response.status(), await response.text()).toBe(202)
  expect(response.request().postDataJSON().verify_object_aliases).toBe(true)
  const value = await task(page, (await response.json()).id)
  const receipt = await (await page.request.get(`/api/artifacts/${value.result.artifact_id}`)).json()
  expect(receipt.payload.version).toBe('prepared-source-scope-correction/2')
  expect(receipt.payload.snapshot.artifact_id).toBe(row.candidate_id)
  return { task: value, receipt }
}

test('显式不同称谓：默认旧版、换选项重读、独立关联与正文/2，原称谓及稿件保持', async ({ page }, info) => {
  page.on('dialog', (dialog) => void dialog.accept())
  const row = await fixture(page),
    panel = await open(page, row)
  const oldResponse = page.waitForResponse((response) =>
    response.url().endsWith('/prepared-source-scope-preview'),
  )
  await panel.getByRole('button', { name: '读取章纲范围依据', exact: true }).click()
  const oldPreview = await (await oldResponse).json()
  expect(oldPreview.version).toBe('prepared-source-scope-correction/1')
  expect(oldPreview).not.toHaveProperty('verify_object_aliases')
  await panel.getByRole('checkbox', { name: /^P01/ }).check()
  await panel.getByRole('checkbox', { name: optionName, exact: true }).check()
  await expect(panel.getByRole('button', { name: sourceSubmit, exact: true })).toBeDisabled()
  const freshResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname.endsWith('/prepared-source-scope-preview') &&
      new URL(response.url()).searchParams.get('verify_object_aliases') === 'true',
  )
  await panel.getByRole('button', { name: '重新读取章纲范围依据', exact: true }).click()
  const freshPreview = await (await freshResponse).json()
  expect(freshPreview.version).toBe('prepared-source-scope-correction/2')
  expect(freshPreview.basis_sha256).not.toBe(oldPreview.basis_sha256)
  await panel.getByRole('button', { name: '确认使用新依据，保留输入', exact: true }).click()
  const before = await (await page.request.get(`/api/artifacts/${row.candidate_id}`)).json()
  const parent = await (await page.request.get(`/api/tasks/${row.parent_id}`)).json()
  const result = await submit(page, row, panel)
  expect(result.receipt.payload.object_alias_resolution.state).toBe('supported')
  expect(result.receipt.payload.corrected_contract.items[0].transfer_object).toBe(
    result.receipt.payload.snapshot.prepared_contract.items[0].transfer_object,
  )
  expect(result.receipt.payload.inventory.inventory.observations[0].transfer_object).not.toBe(
    result.receipt.payload.corrected_contract.items[0].transfer_object,
  )
  expect(
    (await (await page.request.get(`/api/tasks/${result.task.id}/transcript`)).json()).steps,
  ).toHaveLength(3)
  const aliases = panel.getByRole('region', { name: '材料称谓关联证据', exact: true })
  await expect(aliases).toContainText('同一次传递的同一份材料')
  await expect(aliases).toContainText('原文关联已核对')
  await expect(aliases).toContainText('来源解释阶段：已完成')
  await expect(aliases).not.toContainText('阶段：completed')
  const colors = await aliases.locator('h4').evaluate((element) => {
    let background: Element | null = element
    while (background && getComputedStyle(background).backgroundColor === 'rgba(0, 0, 0, 0)')
      background = background.parentElement
    return {
      foreground: getComputedStyle(element).color,
      background: background ? getComputedStyle(background).backgroundColor : 'rgb(255, 255, 255)',
    }
  })
  const luminance = (value: string) => {
    const channels = value
      .match(/[\d.]+/g)!
      .slice(0, 3)
      .map(Number)
      .map((channel) => channel / 255)
    const linear = channels.map((channel) =>
      channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
    )
    return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722
  }
  const foreground = luminance(colors.foreground),
    background = luminance(colors.background)
  const contrast = (Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05)
  expect(contrast).toBeGreaterThanOrEqual(4.5)
  await info.attach('object-alias-text-contrast', {
    contentType: 'application/json',
    body: JSON.stringify({ ...colors, contrast }),
  })
  await aliases.scrollIntoViewIfNeeded()
  await page.screenshot({ path: info.outputPath('object-alias-source-desktop.png') })
  const bodyRead = panel.getByRole('button', { name: '读取就绪回执对应的正文复检依据', exact: true })
  await expect(bodyRead).toBeEnabled()
  await panel.getByRole('checkbox', { name: optionName, exact: true }).uncheck()
  await expect(bodyRead).toBeDisabled()
  await expect(panel).toContainText('此回执的材料称谓选项与当前选择不同')
  await panel.getByRole('checkbox', { name: optionName, exact: true }).check()
  await bodyRead.focus()
  await bodyRead.press('Enter')
  const bodyPosted = page.waitForResponse(
    (response) =>
      response.url().endsWith('/recheck-source-correction') && response.request().method() === 'POST',
  )
  await panel.getByRole('button', { name: '提交独立正文复检（额外模型费用）', exact: true }).click()
  const bodyResponse = await bodyPosted
  expect(bodyResponse.request().postDataJSON()).not.toHaveProperty('verify_object_aliases')
  const body = await task(page, (await bodyResponse.json()).id)
  expect(body.result.source_corrected_recheck_version).toBe('scope-corrected-candidate-recheck/2')
  const after = await (await page.request.get(`/api/artifacts/${row.candidate_id}`)).json()
  expect(after.payload.text).toBe(before.payload.text)
  expect(after.candidate_revision).toBe(0)
  expect((await (await page.request.get(`/api/tasks/${row.parent_id}`)).json()).result).toEqual(parent.result)
  expect((await (await page.request.get(`/api/chapters/${row.chapter_id}`)).json()).content).toBe('')
  const compare = page.getByRole('combobox', { name: '比较候选', exact: true })
  await expect(compare.locator(`option[value="${body.result.artifact_id}"]`)).toBeAttached()
  await compare.selectOption(body.result.artifact_id)
  const report = page.getByRole('region', { name: 'AI章纲范围只读记录', exact: true })
  await expect(report).toContainText('目标候选正文与修订吻合')
  await expect(report).not.toContainText('报告格式无法核对')
  await report.getByRole('button', { name: '查看原候选', exact: true }).click()
  await page
    .getByRole('textbox', { name: '候选内容', exact: true })
    .fill(after.payload.text + '\n作者未采纳编辑😀。')
  await page.getByRole('button', { name: '保存候选编辑（原检查失效）', exact: true }).click()
  await compare.selectOption(body.result.artifact_id)
  await expect(report).toContainText('目标候选已变化，以下仅为历史记录')
})

test('关联未知停在来源，不把不同称谓相似当同实例或派发全义保持', async ({ page }) => {
  page.on('dialog', (dialog) => void dialog.accept())
  const row = await fixture(page, 'unknown'),
    panel = await prepare(page, row)
  const result = await submit(page, row, panel)
  expect(result.receipt.payload.state).toBe('unverified')
  expect(result.receipt.payload.object_alias_resolution.state).toBe('unverified')
  expect(result.receipt.payload.preservation).toBeNull()
  expect(
    (await (await page.request.get(`/api/tasks/${result.task.id}/transcript`)).json()).steps,
  ).toHaveLength(2)
  await expect(panel.getByRole('region', { name: '材料称谓关联证据', exact: true })).toContainText(
    '关联尚未唯一核实',
  )
  await expect(
    panel.getByRole('button', { name: '读取就绪回执对应的正文复检依据', exact: true }),
  ).toBeDisabled()
})

test('原称谓可直接对应时不派额外关联，仍保留全部条件核对', async ({ page }) => {
  page.on('dialog', (dialog) => void dialog.accept())
  const row = await fixture(page, 'exact'),
    panel = await prepare(page, row)
  const result = await submit(page, row, panel)
  expect(result.receipt.payload.object_alias_resolution.state).toBe('not_needed')
  expect(result.receipt.payload.object_alias_resolution.required_item_ids).toEqual([])
  expect(result.receipt.payload.preservation.item_checks[0].object_alias_check).toBe('not_needed')
  expect(
    (await (await page.request.get(`/api/tasks/${result.task.id}/transcript`)).json()).steps,
  ).toHaveLength(2)
  await expect(panel.getByRole('region', { name: '材料称谓关联证据', exact: true })).toContainText(
    '本次无需额外关联',
  )
})

test('先前关联有据但末次独立称谓复核未知时，保留回执且阻止正文复检', async ({ page }) => {
  page.on('dialog', (dialog) => void dialog.accept())
  const row = await fixture(page, 'final_unknown'),
    panel = await prepare(page, row)
  const result = await submit(page, row, panel)
  expect(result.receipt.payload.state).toBe('unverified')
  expect(result.receipt.payload.object_alias_resolution.state).toBe('supported')
  expect(result.receipt.payload.preservation.item_checks[0].object_alias_check).toBe('unknown')
  expect(
    (await (await page.request.get(`/api/tasks/${result.task.id}/transcript`)).json()).steps,
  ).toHaveLength(3)
  await expect(panel.getByRole('region', { name: '材料称谓关联证据', exact: true })).toContainText(
    '原文关联已核对',
  )
  await expect(panel).toContainText('末次独立称谓复核：仍待核对')
  await expect(panel).not.toContainText('回执结构无法核对')
  await expect(
    panel.getByRole('button', { name: '读取就绪回执对应的正文复检依据', exact: true }),
  ).toBeDisabled()
})

test('新政策未知POST刷新恢复true及原nonce，404/null不重复派发', async ({ page }) => {
  page.on('dialog', (dialog) => void dialog.accept())
  const row = await fixture(page),
    panel = await prepare(page, row)
  let posts = 0,
    nonce = '',
    id = ''
  await page.route(
    `**/api/artifacts/${row.candidate_id}/prepared-source-scope-corrections`,
    async (route) => {
      if (route.request().method() !== 'POST') return route.continue()
      posts++
      nonce = route.request().postDataJSON().client_request_id
      expect(route.request().postDataJSON().verify_object_aliases).toBe(true)
      const response = await route.fetch()
      id = (await response.json()).id
      await route.abort('failed')
    },
  )
  await panel.getByRole('button', { name: sourceSubmit, exact: true }).click()
  await expect(panel).toContainText('原提交标识')
  await page.reload()
  await page.getByRole('combobox', { name: '比较候选', exact: true }).selectOption(row.candidate_id)
  await page.getByText('核对AI章纲范围，再显式复检正文', { exact: true }).click()
  const restored = page.getByRole('region', { name: 'AI章纲范围来源纠偏', exact: true })
  await expect(restored.getByRole('checkbox', { name: optionName, exact: true })).toBeChecked()
  let queries = 0
  await page.route(`**/api/artifacts/${row.candidate_id}/prepared-source-scope-corrections?*`, (route) => {
    expect(new URL(route.request().url()).searchParams.get('client_request_id')).toBe(nonce)
    queries++
    if (queries === 1) return route.fulfill({ status: 404, json: { code: 'not_found', message: '暂未找到' } })
    if (queries === 2)
      return route.fulfill({
        json: { source_candidate_id: row.candidate_id, client_request_id: nonce, task: null },
      })
    return route.continue()
  })
  const lookup = restored.getByRole('button', { name: '只查询原提交回执（不重发）', exact: true })
  await lookup.click()
  await expect(restored).toContainText('继续只查原标识')
  await lookup.click()
  await expect(restored).toContainText('保留原标识继续只读查询')
  await lookup.click()
  await expect(restored).toContainText(id)
  expect(posts).toBe(1)
  expect(queries).toBe(3)
})

test('新选项键盘与中文组合不误提交，320实际文字2倍可读', async ({ page }, info) => {
  page.on('dialog', (dialog) => void dialog.accept())
  const row = await fixture(page),
    panel = await prepare(page, row)
  const reason = panel.getByRole('textbox', { name: '本次AI章纲范围纠偏原因（可留空）', exact: true })
  const submitButton = panel.getByRole('button', { name: sourceSubmit, exact: true })
  let posts = 0
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().endsWith('/prepared-source-scope-corrections')) posts++
  })
  await reason.fill('只核材料同一次传递😀𠮷，保留原称谓')
  await reason.dispatchEvent('compositionstart')
  await submitButton.click()
  expect(posts).toBe(0)
  await reason.dispatchEvent('compositionend')
  await page.setViewportSize({ width: 320, height: 900 })
  await assertActualDoubleText(
    page,
    {
      paragraph: panel.locator('p').first(),
      option: panel.getByRole('checkbox', { name: optionName, exact: true }).locator('..'),
      input: reason,
      button: submitButton,
    },
    info,
  )
  await reason.focus()
  await expect(reason).toBeFocused()
  await page.keyboard.press('Tab')
  expect(await page.evaluate(() => document.activeElement !== document.body)).toBe(true)
  await panel.getByRole('checkbox', { name: optionName, exact: true }).scrollIntoViewIfNeeded()
  await page.screenshot({ path: info.outputPath('object-alias-option-320-actual2x.png') })
  await submitButton.scrollIntoViewIfNeeded()
  await page.screenshot({ path: info.outputPath('object-alias-submit-320-actual2x.png') })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  expect(posts).toBe(0)
})

test('新政策409保留选项范围原因，显式重读确认后才可再次提交', async ({ page }) => {
  page.on('dialog', (dialog) => void dialog.accept())
  const row = await fixture(page),
    panel = await prepare(page, row)
  const reason = panel.getByRole('textbox', { name: '本次AI章纲范围纠偏原因（可留空）', exact: true })
  await reason.fill('只核原文材料同指😀')
  await page.route(`**/api/artifacts/${row.candidate_id}/prepared-source-scope-corrections`, (route) =>
    route.fulfill({ status: 409, json: { code: 'prepared_scope_basis', message: '新政策依据已变化' } }),
  )
  await panel.getByRole('button', { name: sourceSubmit, exact: true }).click()
  await expect(panel).toContainText('新政策依据已变化')
  await expect(reason).toHaveValue('只核原文材料同指😀')
  await expect(panel.getByRole('checkbox', { name: optionName, exact: true })).toBeChecked()
  await expect(panel.getByRole('checkbox', { name: /^P01/ })).toBeChecked()
  await expect(panel.getByRole('button', { name: sourceSubmit, exact: true })).toBeDisabled()
  await panel.getByRole('button', { name: '重新读取章纲范围依据', exact: true }).click()
  await expect(panel.getByRole('button', { name: sourceSubmit, exact: true })).toBeDisabled()
  await panel.getByRole('button', { name: '确认使用新依据，保留输入', exact: true }).click()
  await expect(panel.getByRole('button', { name: sourceSubmit, exact: true })).toBeEnabled()
  expect((await (await page.request.get(`/api/tasks?branch_id=${row.branch_id}`)).json()).length).toBe(1)
})

test('提交摘要仍在计算时切换称谓选项，不能派发旧政策请求', async ({ page }) => {
  page.on('dialog', (dialog) => void dialog.accept())
  const row = await fixture(page),
    panel = await prepare(page, row)
  let posts = 0
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().endsWith('/prepared-source-scope-corrections')) posts++
  })
  await page.evaluate(() => {
    const original = crypto.subtle.digest.bind(crypto.subtle)
    crypto.subtle.digest = async (...args) => {
      crypto.subtle.digest = original
      document.documentElement.dataset.aliasDigest = 'held'
      await new Promise<void>((resolve) => Object.assign(window, { __releaseObjectAliasDigest: resolve }))
      const result = await original(...args)
      document.documentElement.dataset.aliasDigest = 'released'
      return result
    }
  })
  await panel.getByRole('button', { name: sourceSubmit, exact: true }).click()
  await expect(page.locator('html')).toHaveAttribute('data-alias-digest', 'held')
  await panel.getByRole('checkbox', { name: optionName, exact: true }).uncheck()
  await page.evaluate(() =>
    (window as unknown as { __releaseObjectAliasDigest: () => void }).__releaseObjectAliasDigest(),
  )
  await expect(page.locator('html')).toHaveAttribute('data-alias-digest', 'released')
  await panel.getByRole('button', { name: '重新读取章纲范围依据', exact: true }).click()
  await expect(panel.getByRole('button', { name: '确认使用新依据，保留输入', exact: true })).toBeVisible()
  expect(posts).toBe(0)
  expect((await (await page.request.get(`/api/tasks?branch_id=${row.branch_id}`)).json()).length).toBe(1)
})
