import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { assertActualDoubleText } from './helpers/actualTextScale'

type Fixture = {
  id: string
  branch_id: string
  chapter_id: string
  scene_id: string
  second_scene_id: string
  original_payload: Record<string, unknown>
  original_content: string
  original_history: string
}
const PREFIX = '/test/unknown-scene-reason/fixtures'
const SUMMARY = '更正未知原因，仍保持时点未知'
const REASON = '更正后的未知原因'
const SAVE = '保存更正，仍保持时点未知'
const LOOKUP = '查询原更正回执'
const NEW_REASON = '前后安排可以相容；精确全局位置尚未绑定，继续保持未知。'
const browserErrors = new WeakMap<Page, string[]>()
test.beforeEach(async ({ page }) => {
  const errors: string[] = []
  browserErrors.set(page, errors)
  page.on('pageerror', (error) => errors.push(error.message))
})
test.afterEach(async ({ page }, info) => {
  const errors = browserErrors.get(page) || []
  await info.attach('unknown-reason-browser-boundary', {
    contentType: 'application/json',
    body: JSON.stringify({
      pageErrors: errors,
      fixtureContent: 'original engineering fixture only',
      fixtureStartsWorker: false,
      realModelCalls: 0,
    }),
  })
  expect(errors).toEqual([])
})
const path = (fixture: Fixture, id = fixture.scene_id) =>
  `/api/branches/${fixture.branch_id}/chapters/${fixture.chapter_id}/scene-times/${id}`
async function post(request: APIRequestContext, url: string) {
  const response = await request.get('/api/session'),
    session = (await response.json()) as { csrf: string }
  const result = await request.post(url, {
    headers: { 'X-Ainovel-CSRF': session.csrf, Origin: new URL(response.url()).origin },
  })
  expect(result.ok()).toBeTruthy()
  return result
}
async function fixture(request: APIRequestContext) {
  return (await (await post(request, PREFIX)).json()) as Fixture
}
async function target(page: Page, value: Fixture) {
  await page.goto('/')
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(value.id)
  await page.getByRole('combobox', { name: '工作版本', exact: true }).selectOption(value.branch_id)
}
async function workbench(page: Page, value: Fixture, read = true, restore: boolean | 'receipt' = false) {
  await target(page, value)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  await page.getByText('重新核对已保存的提取场景', { exact: true }).click()
  const area = page.getByRole('region', { name: '提取场景重核工作台', exact: true })
  await area.getByLabel('场景所在章节').selectOption(value.chapter_id)
  await area.getByRole('button', { name: /^次日记录 · 场景修订/ }).click()
  const panel = area.getByRole('region', { name: '未知原因更正', exact: true })
  const summary = area.getByText(SUMMARY, { exact: true })
  if (restore === 'receipt')
    await expect(panel.getByRole('region', { name: '未知原因更正回执', exact: true })).toBeVisible()
  else if (restore) await expect(panel.getByRole('button', { name: LOOKUP, exact: true })).toBeVisible()
  else if ((await summary.locator('..').getAttribute('open')) === null) await summary.click()
  if (read) await panel.getByRole('button', { name: '读取未知原因更正依据', exact: true }).click()
  return panel
}
async function edit(page: Page, panel: ReturnType<Page['locator']>, reason = NEW_REASON) {
  const text = panel.getByRole('textbox', { name: '作为更正依据的原场景选段', exact: true })
  await text.focus()
  await page.keyboard.press('Control+A')
  await panel.getByRole('button', { name: '加入所选原文依据', exact: true }).click()
  await panel.getByRole('textbox', { name: REASON, exact: true }).fill(reason)
  await expect(panel.getByRole('button', { name: SAVE, exact: true })).toBeEnabled()
}
async function invariants(request: APIRequestContext, value: Fixture, revision: number) {
  const result = await request.get(`${PREFIX}/${value.scene_id}/invariants`)
  expect(result.ok()).toBeTruthy()
  const data = (await result.json()) as {
    chapter: { content: string; revision: number }
    tasks: unknown[]
    usage: unknown[]
    scenes: { scenes: { id: string; revision: number; payload: Record<string, unknown> }[] }
    history: { revision: number; payload: string }[]
  }
  expect(data.chapter.content).toBe(value.original_content)
  expect(data.chapter.revision).toBe(1)
  expect(data.tasks).toEqual([])
  expect(data.usage).toEqual([])
  expect(data.history[0].payload).toBe(value.original_history)
  const row = data.scenes.scenes.find((item) => item.id === value.scene_id)!
  expect(row.revision).toBe(revision)
  for (const key of [
    'title',
    'source_revision',
    'start',
    'end',
    'story_order',
    'story_time',
    'audience',
    'character_id',
    'note',
  ])
    expect(row.payload[key]).toEqual(value.original_payload[key])
  const audit = row.payload.order_basis as Record<string, unknown>,
    original = value.original_payload.order_basis as Record<string, unknown>
  expect(audit.kind).toBe('unknown')
  expect(row.payload.story_order).toBeNull()
  expect(audit.refs).toEqual(original.refs)
  expect(audit.covered_events).toEqual(original.covered_events)
  return row
}
function gate() {
  let release = () => {}
  const promise = new Promise<void>((resolve) => {
    release = resolve
  })
  return { release, promise }
}

test('故事工作台更正未知理由，原scene全同提交，双击仅一次，原稿/审计/history不变', async ({
  page,
  request,
}, info) => {
  const value = await fixture(request),
    panel = await workbench(page, value)
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await edit(page, panel)
  await panel.screenshot({ path: info.outputPath('unknown-reason-desktop-preview.png') })
  const puts: unknown[] = []
  page.on('request', (r) => {
    if (new URL(r.url()).pathname === path(value) && r.method() === 'PUT')
      puts.push(r.postDataJSON() as unknown)
  })
  await panel.getByRole('button', { name: SAVE, exact: true }).evaluate((button) => {
    if (!(button instanceof HTMLButtonElement)) throw new Error('Expected button')
    button.click()
    button.click()
  })
  await expect(
    panel.getByRole('heading', { name: '未知原因更正已保存，时点仍未知', exact: true }),
  ).toBeVisible()
  expect(puts).toHaveLength(1)
  expect(puts[0]).toMatchObject({
    scene: value.original_payload,
    archived: false,
    unknown_reason_correction: { actor: 'author', reason: NEW_REASON },
  })
  const body = puts[0] as Record<string, unknown>
  expect(Object.keys(body).sort()).toEqual([
    'archived',
    'base_revision',
    'base_story_revision',
    'scene',
    'unknown_reason_correction',
  ])
  const row = await invariants(request, value, 1)
  expect((row.payload.order_basis as Record<string, unknown>).reason).toBe(NEW_REASON)
  expect(row.payload.unknown_reason_corrected).toMatchObject({
    actor: 'author',
    model_review_performed: false,
    literary_review_performed: false,
    story_time_inferred: false,
  })
  expect(errors).toEqual([])
})

test('未知PUT刷新仅GET原nonce，404及null都不重发，真实回执恢复来源', async ({ page, request }) => {
  const value = await fixture(request),
    puts: { unknown_reason_correction: { client_request_id: string } }[] = []
  await page.route('**' + path(value), async (route) => {
    if (route.request().method() !== 'PUT') return route.continue()
    puts.push(route.request().postDataJSON() as (typeof puts)[number])
    const response = await route.fetch()
    expect(response.status()).toBe(200)
    await route.abort('connectionreset')
  })
  let reads = 0
  await page.route('**' + path(value) + '/unknown-reason-corrections/*', async (route) => {
    reads++
    if (reads === 1)
      return route.fulfill({ status: 404, json: { code: 'not_found', message: '此刻未读到回执' } })
    if (reads === 2)
      return route.fulfill({
        json: {
          scene_id: value.scene_id,
          client_request_id: puts[0].unknown_reason_correction.client_request_id,
          receipt: null,
        },
      })
    return route.continue()
  })
  let panel = await workbench(page, value)
  await edit(page, panel)
  await panel.getByRole('button', { name: SAVE, exact: true }).click()
  await expect(panel.getByRole('button', { name: LOOKUP, exact: true })).toBeVisible()
  panel = await workbench(page, value, false, true)
  await expect(panel.getByRole('textbox', { name: REASON, exact: true })).toHaveValue(NEW_REASON)
  for (let i = 0; i < 3; i++) {
    await panel.getByRole('button', { name: LOOKUP, exact: true }).click()
    if (i < 2) await expect(panel.getByRole('button', { name: LOOKUP, exact: true })).toBeEnabled()
  }
  await expect(
    panel.getByRole('heading', { name: '未知原因更正已保存，时点仍未知', exact: true }),
  ).toBeVisible()
  expect(puts).toHaveLength(1)
  expect(reads).toBe(3)
  await invariants(request, value, 1)
})

test('已知PUT成功但回执GET404仍保留原nonce，只读查证后成功', async ({ page, request }) => {
  const value = await fixture(request)
  let reads = 0,
    writes = 0
  page.on('request', (r) => {
    if (new URL(r.url()).pathname === path(value) && r.method() === 'PUT') writes++
  })
  await page.route('**' + path(value) + '/unknown-reason-corrections/*', async (route) => {
    reads++
    return reads === 1
      ? route.fulfill({ status: 404, json: { code: 'not_found', message: '稍后核对原回执' } })
      : route.continue()
  })
  const panel = await workbench(page, value)
  await edit(page, panel)
  await panel.getByRole('button', { name: SAVE, exact: true }).click()
  await expect(panel.getByRole('button', { name: LOOKUP, exact: true })).toBeVisible()
  await expect(panel.getByRole('button', { name: SAVE, exact: true })).toBeDisabled()
  await panel.getByRole('button', { name: LOOKUP, exact: true }).click()
  await expect(
    panel.getByRole('heading', { name: '未知原因更正已保存，时点仍未知', exact: true }),
  ).toBeVisible()
  expect(writes).toBe(1)
  await invariants(request, value, 1)
})

test('旧更正回执明确标为历史，当前理由以最新场景为准，授权driver单独标来源', async ({ page, request }) => {
  const value = await fixture(request)
  let panel = await workbench(page, value)
  await edit(page, panel)
  await panel.getByRole('button', { name: SAVE, exact: true }).click()
  await expect(
    panel.getByRole('heading', { name: '未知原因更正已保存，时点仍未知', exact: true }),
  ).toBeVisible()
  const preview = (await (await request.get(path(value) + '/unknown-reason-correction-preview')).json()) as {
    scene: { payload: unknown }
    source: unknown
    request_basis: {
      base_revision: number
      base_story_revision: number
      base_payload_sha256: string
      source_revision: number
      content_sha256: string
    }
  }
  const session = await request.get('/api/session'),
    auth = (await session.json()) as { csrf: string }
  const secondReason = '授权操作者再次更正：时点仍未知，不补出事件冲突。'
  const result = await request.put(path(value), {
    headers: { 'X-Ainovel-CSRF': auth.csrf, Origin: new URL(session.url()).origin },
    data: {
      base_story_revision: preview.request_basis.base_story_revision,
      base_revision: preview.request_basis.base_revision,
      scene: preview.scene.payload,
      archived: false,
      unknown_reason_correction: {
        version: 'scene-unknown-reason-correction/1',
        client_request_id: 'authorized-operator-second-' + value.scene_id,
        actor: 'authorized_operator',
        base_payload_sha256: preview.request_basis.base_payload_sha256,
        source_revision: preview.request_basis.source_revision,
        content_sha256: preview.request_basis.content_sha256,
        reason: secondReason,
        evidence: [preview.source],
      },
    },
  })
  expect(result.status()).toBe(200)
  panel = await workbench(page, value, false, 'receipt')
  await expect(
    panel.getByText('这是历史更正回执，不能代表当前场景版本或当前未知理由已核实。', { exact: true }),
  ).toBeVisible()
  await expect(panel).toContainText('当前场景保存理由：' + secondReason)
  const row = await invariants(request, value, 2)
  expect(row.payload.unknown_reason_corrected).toMatchObject({
    actor: 'authorized_operator',
    reason: secondReason,
  })
})

test('409保留理由与选段，显式读取并确认新依据后才用新nonce保存', async ({ page, request }) => {
  const value = await fixture(request),
    bodies: Record<string, unknown>[] = []
  let panel = await workbench(page, value)
  page.on('request', (r) => {
    if (new URL(r.url()).pathname === path(value) && r.method() === 'PUT')
      bodies.push(r.postDataJSON() as Record<string, unknown>)
  })
  await edit(page, panel)
  await post(request, `${PREFIX}/${value.scene_id}/advance-story`)
  await panel.getByRole('button', { name: SAVE, exact: true }).click()
  await expect(panel.getByRole('alert')).toBeVisible()
  await expect(panel.getByRole('textbox', { name: REASON, exact: true })).toHaveValue(NEW_REASON)
  await expect(panel.getByRole('button', { name: SAVE, exact: true })).toBeDisabled()
  panel = await workbench(page, value, false)
  await expect(panel.getByRole('textbox', { name: REASON, exact: true })).toHaveValue(NEW_REASON)
  await expect(panel.getByRole('button', { name: SAVE, exact: true })).toBeDisabled()
  expect(bodies).toHaveLength(1)
  await panel.getByRole('button', { name: '重新读取更正依据（保留草稿）', exact: true }).click()
  await expect(panel.getByRole('button', { name: '确认使用新依据，保留更正原因', exact: true })).toBeVisible()
  expect(bodies).toHaveLength(1)
  await panel.getByRole('button', { name: '确认使用新依据，保留更正原因', exact: true }).click()
  await panel.getByRole('button', { name: SAVE, exact: true }).click()
  await expect(
    panel.getByRole('heading', { name: '未知原因更正已保存，时点仍未知', exact: true }),
  ).toBeVisible()
  expect(bodies).toHaveLength(2)
  expect((bodies[0].unknown_reason_correction as Record<string, unknown>).client_request_id).not.toBe(
    (bodies[1].unknown_reason_correction as Record<string, unknown>).client_request_id,
  )
  await invariants(request, value, 1)
})

test('旧preview晚返回不污染另一场景或其原因草稿', async ({ page, request }) => {
  const value = await fixture(request),
    wait = gate()
  await page.route('**' + path(value) + '/unknown-reason-correction-preview', async (route) => {
    const response = await route.fetch()
    await wait.promise
    await route.fulfill({ response })
  })
  const panel = await workbench(page, value, false)
  await panel.getByRole('button', { name: '读取未知原因更正依据', exact: true }).click()
  const area = page.getByRole('region', { name: '提取场景重核工作台', exact: true })
  await area.getByRole('button', { name: /^另一条旁记 ·/ }).click()
  await area.getByText(SUMMARY, { exact: true }).click()
  const next = area.getByRole('region', { name: '未知原因更正', exact: true })
  await next.getByRole('button', { name: '读取未知原因更正依据', exact: true }).click()
  await next.getByRole('textbox', { name: REASON, exact: true }).fill('另一场景的原因不能被旧响应覆盖。')
  wait.release()
  await expect(next.getByRole('textbox', { name: REASON, exact: true })).toHaveValue(
    '另一场景的原因不能被旧响应覆盖。',
  )
  await expect(next.getByRole('region', { name: '未知原因更正回执', exact: true })).toHaveCount(0)
  await invariants(request, value, 0)
})

test('旧PUT晚返回不把成功展示给另一场景，切回只查原nonce恢复', async ({ page, request }) => {
  const value = await fixture(request),
    wait = gate()
  let writes = 0
  page.on('dialog', (dialog) => void dialog.accept())
  await page.route('**' + path(value), async (route) => {
    if (route.request().method() !== 'PUT') return route.continue()
    writes++
    const response = await route.fetch()
    await wait.promise
    await route.fulfill({ response })
  })
  let panel = await workbench(page, value)
  await edit(page, panel)
  await panel.getByRole('button', { name: SAVE, exact: true }).click()
  await expect.poll(() => writes).toBe(1)
  const area = page.getByRole('region', { name: '提取场景重核工作台', exact: true })
  await area.getByRole('button', { name: /^另一条旁记 ·/ }).click()
  await area.getByText(SUMMARY, { exact: true }).click()
  panel = area.getByRole('region', { name: '未知原因更正', exact: true })
  await panel.getByRole('button', { name: '读取未知原因更正依据', exact: true }).click()
  await panel.getByRole('textbox', { name: REASON, exact: true }).fill('新目标草稿保持。')
  wait.release()
  await expect(panel.getByRole('region', { name: '未知原因更正回执', exact: true })).toHaveCount(0)
  await expect(panel.getByRole('textbox', { name: REASON, exact: true })).toHaveValue('新目标草稿保持。')
  await area.getByRole('button', { name: /^次日记录 ·/ }).click()
  panel = area.getByRole('region', { name: '未知原因更正', exact: true })
  await expect(panel.getByRole('button', { name: LOOKUP, exact: true })).toBeVisible()
  await panel.getByRole('button', { name: LOOKUP, exact: true }).click()
  await expect(
    panel.getByRole('heading', { name: '未知原因更正已保存，时点仍未知', exact: true }),
  ).toBeVisible()
  expect(writes).toBe(1)
  await invariants(request, value, 1)
})

test('写作页320实际文字翻倍、键盘与IME，更正不改变原场景表单字段', async ({ page, request }, info) => {
  const value = await fixture(request)
  await page.setViewportSize({ width: 320, height: 900 })
  await target(page, value)
  await page.getByRole('button', { name: /^01 次日记录/ }).click()
  await page.getByText('场景时点与人物当前状态', { exact: true }).click()
  const editor = page.getByRole('region', { name: '场景时点绑定', exact: true })
  await editor.getByRole('button', { name: /^次日记录 ·/ }).click()
  await editor.getByText(SUMMARY, { exact: true }).click()
  const panel = editor.getByRole('region', { name: '未知原因更正', exact: true })
  await panel.getByRole('button', { name: '读取未知原因更正依据', exact: true }).click()
  await edit(page, panel)
  const reason = panel.getByRole('textbox', { name: REASON, exact: true }),
    save = panel.getByRole('button', { name: SAVE, exact: true })
  await assertActualDoubleText(
    page,
    { reason, save, source: panel.getByRole('textbox', { name: '作为更正依据的原场景选段', exact: true }) },
    info,
  )
  let writes = 0
  page.on('request', (r) => {
    if (new URL(r.url()).pathname === path(value) && r.method() === 'PUT') writes++
  })
  await reason.focus()
  await reason.dispatchEvent('compositionstart', { data: '知' })
  await page.keyboard.press('Enter')
  await reason.dispatchEvent('compositionend', { data: '知' })
  expect(writes).toBe(0)
  await panel.screenshot({ path: info.outputPath('unknown-reason-320-actual2x.png') })
  await save.focus()
  await page.keyboard.press('Enter')
  await expect(
    panel.getByRole('heading', { name: '未知原因更正已保存，时点仍未知', exact: true }),
  ).toBeVisible()
  await expect(editor.getByRole('textbox', { name: '场景名称', exact: true })).toHaveValue(
    String(value.original_payload.title),
  )
  await expect(editor.getByRole('textbox', { name: '时点确认说明', exact: true })).toHaveValue(
    String(value.original_payload.note),
  )
  await expect(editor.getByRole('textbox', { name: '场景故事时间说明', exact: true })).toHaveValue(
    String(value.original_payload.story_time),
  )
  expect(writes).toBe(1)
  await invariants(request, value, 1)
})
