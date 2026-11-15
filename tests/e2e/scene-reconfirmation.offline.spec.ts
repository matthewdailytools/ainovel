import { test, expect, type APIRequestContext, type Page } from '@playwright/test'
import { randomUUID } from 'node:crypto'

type Fixture = {
  id: string
  fixture_id: string
  branch_id: string
  chapter_id: string
  empty_chapter_id: string
  scene_id: string
  event_id: string
  original_payload: Record<string, unknown>
  original_content: string
}
const prefix = '/test/scene-reconfirmation/fixtures'
let currentFixture: Fixture
let initialGlobal: { global_task_ids: string[]; global_call_ids: string[] }
function getFixture(): Fixture {
  return currentFixture
}
function scenePath(fixture: Fixture) {
  return `/api/branches/${fixture.branch_id}/chapters/${fixture.chapter_id}/scene-times/${fixture.scene_id}`
}
async function post(request: APIRequestContext, path: string, data?: object) {
  const session = await request.get('/api/session')
  const { csrf } = await session.json()
  const response = await request.post(path, {
    headers: { 'X-Ainovel-CSRF': csrf, Origin: new URL(session.url()).origin },
    data,
  })
  expect(response.ok()).toBeTruthy()
  return response.json()
}
async function openTarget(page: Page, fixture: Fixture) {
  await page.goto('/')
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(fixture.id)
  await page.getByRole('combobox', { name: '工作版本', exact: true }).selectOption(fixture.branch_id)
}
async function openWorkbench(page: Page, fixture: Fixture) {
  await openTarget(page, fixture)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  await page.getByText('重新核对已保存的提取场景', { exact: true }).click()
  const area = page.getByRole('region', {
    name: '提取场景重核工作台',
    exact: true,
  })
  await area.getByLabel('场景所在章节').selectOption(fixture.chapter_id)
  await area.getByRole('button', { name: /^回铺 · 场景修订/ }).click()
  await area.getByRole('button', { name: '重新核对提取场景', exact: true }).click()
  const panel = area.getByRole('region', {
    name: '提取场景人工重新核对',
    exact: true,
  })
  await expect(panel.getByLabel('人工重新核对理由')).toBeVisible()
  return panel
}
async function invariants(request: APIRequestContext, fixture: Fixture) {
  const response = await request.get(`${prefix}/${fixture.fixture_id}/invariants`)
  expect(response.ok()).toBeTruthy()
  const value = await response.json()
  expect(value.chapter.content).toBe(fixture.original_content)
  expect(value.chapter.revision).toBe(1)
  expect(value.tasks).toEqual([])
  expect(value.usage).toEqual([])
  expect(value.global_task_ids).toEqual(initialGlobal.global_task_ids)
  expect(value.global_call_ids).toEqual(initialGlobal.global_call_ids)
  const scene = value.scenes.scenes.find((row: { id: string }) => row.id === fixture.scene_id)
  for (const key of [
    'start',
    'end',
    'story_order',
    'audience',
    'character_id',
    'source_revision',
    'title',
    'note',
  ])
    expect(scene.payload[key]).toEqual(fixture.original_payload[key])
  expect(scene.payload.order_basis.covered_events).toEqual(
    (fixture.original_payload.order_basis as { covered_events: unknown[] }).covered_events,
  )
  return scene
}

test.describe.serial('提取场景重核：隔离离线真实API', () => {
  test.beforeAll(async ({ request }) => {
    currentFixture = await post(request, prefix, { client_request_id: randomUUID() })
  })
  test.beforeEach(async ({ request }) => {
    const response = await request.get(`${prefix}/${currentFixture.fixture_id}/invariants`)
    expect(response.ok()).toBeTruthy()
    initialGlobal = await response.json()
  })
  test.afterEach(async ({ request }) => {
    await invariants(request, currentFixture)
  })
  test('长篇工作台默认只刷新引用，保存同ID新修订，旧历史和回执可查', async ({ page, request }) => {
    const fixture = await getFixture()
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    const panel = await openWorkbench(page, fixture)
    await expect(panel.getByRole('list', { name: '原引用与当前修订对照' })).toContainText('事件修订 0 → 1')
    await expect(panel.getByLabel('保留原时间说明', { exact: true })).toBeChecked()
    await panel.getByLabel('人工重新核对理由').fill('作者重读原范围，只刷新原事件引用，暂保留原时间描述。')
    const sent = page.waitForRequest((req) => req.method() === 'POST' && req.url().endsWith('/reconfirm'))
    await panel.getByRole('button', { name: '确认重新核对并保存为新场景修订' }).click()
    const body = (await sent).postDataJSON()
    expect(body.story_time).toBe(fixture.original_payload.story_time)
    expect(body.time_evidence).toEqual([])
    expect(Object.keys(body).sort()).toEqual(
      [
        'base_story_revision',
        'base_revision',
        'base_payload_sha256',
        'source_revision',
        'content_sha256',
        'refs',
        'story_time',
        'time_evidence',
        'reason',
        'client_request_id',
      ].sort(),
    )
    await expect(panel.getByRole('heading', { name: '人工核对记录已保存' })).toBeVisible()
    await expect(panel).toContainText('修订 0 → 1')
    const row = await invariants(request, fixture)
    expect(row.id).toBe(fixture.scene_id)
    expect(row.revision).toBe(1)
    expect(row.payload.author_reconfirmed.authority).toBe('author_confirmation_not_model_review')
    const history = await (await request.get(scenePath(fixture) + '/history')).json()
    expect(history.map((entry: { revision: number }) => entry.revision).sort()).toEqual([0, 1])
    const saved = await (
      await request.get(scenePath(fixture) + '/reconfirmations/' + body.client_request_id)
    ).json()
    expect(saved.receipt.story_time).toBe(fixture.original_payload.story_time)
    expect(saved.receipt.covered_events_unchanged).toBe(true)
    expect(errors).toEqual([])
  })

  test('场景面板修改时间必须选原文，320px实际30px文字及键盘可操作', async ({ page, request }, testInfo) => {
    const fixture = await getFixture()
    await page.setViewportSize({ width: 320, height: 860 })
    await openTarget(page, fixture)
    await page.getByRole('button', { name: /^01 回铺章/ }).click()
    await expect(page.getByRole('textbox', { name: '章节标题', exact: true })).toHaveValue(
      '回铺章——保留长中文标题及原文范围',
    )
    await page.getByText('场景时点与人物当前状态', { exact: true }).click()
    const editor = page.getByRole('region', {
      name: '场景时点绑定',
      exact: true,
    })
    await editor.getByRole('button', { name: /^回铺 ·/ }).click()
    await editor.getByRole('button', { name: '重新核对提取场景', exact: true }).click()
    const panel = editor.getByRole('region', {
      name: '提取场景人工重新核对',
      exact: true,
    })
    await expect(panel.getByLabel('人工重新核对理由')).toBeVisible()
    const radio = panel.getByLabel('由我修改描述性时间（需要原文依据）', {
      exact: true,
    })
    await radio.focus()
    await page.keyboard.press('Space')
    await expect(radio).toBeChecked()
    await panel.getByLabel('本次确认的故事时间', { exact: true }).fill('回铺时天未黑，具体时段未明')
    await panel.getByLabel('人工重新核对理由').fill('原文只写回铺时天未黑，不能推断一直到傍晚。')
    const save = panel.getByRole('button', {
      name: '确认重新核对并保存为新场景修订',
    })
    await expect(save).toBeDisabled()
    const text = panel.getByLabel('原场景冻结原文')
    await text.focus()
    const a = fixture.original_content.indexOf('回铺'),
      z = fixture.original_content.indexOf('。') + 1
    await panel.getByLabel('原文选段起点（从0开始）').fill(String(a))
    await panel.getByLabel('原文选段终点', { exact: true }).fill(String(z))
    await expect(panel.getByRole('blockquote', { name: '当前连续选段预览' })).toHaveText(
      fixture.original_content.slice(a, z),
    )
    const quoteButton = panel.getByRole('button', {
      name: '使用当前连续选段作为时间依据',
    })
    await expect(quoteButton).toBeEnabled()
    await quoteButton.focus()
    await page.keyboard.press('Enter')
    await expect(save).toBeEnabled()
    const numericBefore = await panel
      .getByLabel('原文选段终点', { exact: true })
      .evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize))
    const quoteBefore = await panel
      .getByRole('blockquote', { name: '当前连续选段预览' })
      .evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize))
    await page.evaluate(() => {
      const elements = [...document.querySelectorAll<HTMLElement>('body, body *')]
      const sizes = elements.map(
        (element) => [element, Number.parseFloat(getComputedStyle(element).fontSize)] as const,
      )
      for (const [element, size] of sizes)
        if (size > 0) element.style.setProperty('font-size', `${size * 2}px`, 'important')
    })
    expect(await page.locator('body').evaluate((element) => getComputedStyle(element).fontSize)).toBe('30px')
    expect(
      await panel
        .getByLabel('原文选段终点', { exact: true })
        .evaluate((element) => getComputedStyle(element).fontSize),
    ).toBe(`${numericBefore * 2}px`)
    expect(
      await panel
        .getByRole('blockquote', { name: '当前连续选段预览' })
        .evaluate((element) => getComputedStyle(element).fontSize),
    ).toBe(`${quoteBefore * 2}px`)
    await panel.getByLabel('原文选段起点（从0开始）').focus()
    await page.keyboard.press('Tab')
    await expect(panel.getByLabel('原文选段终点', { exact: true })).toBeFocused()
    await quoteButton.focus()
    await page.keyboard.press('Enter')
    const colors = await quoteButton.evaluate((element) => {
      const style = getComputedStyle(element),
        bounds = element.getBoundingClientRect()
      return {
        text: style.color,
        background: style.backgroundColor,
        outline: style.outlineColor,
        outlineWidth: style.outlineWidth,
        fullyVisible: bounds.top >= 0 && bounds.bottom <= innerHeight,
      }
    })
    const luminance = (value: string) =>
      value
        .match(/[\d.]+/g)!
        .slice(0, 3)
        .map(Number)
        .map((component) => component / 255)
        .map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
        .reduce((sum, v, index) => sum + v * [0.2126, 0.7152, 0.0722][index], 0)
    const contrast = (a: string, b: string) =>
      (Math.max(luminance(a), luminance(b)) + 0.05) / (Math.min(luminance(a), luminance(b)) + 0.05)
    const ratios = {
      text: contrast(colors.text, colors.background),
      focus: contrast(colors.outline, colors.background),
    }
    expect(ratios.text).toBeGreaterThanOrEqual(4.5)
    expect(ratios.focus).toBeGreaterThanOrEqual(3)
    expect(Number.parseFloat(colors.outlineWidth)).toBeGreaterThan(0)
    expect(colors.fullyVisible).toBe(true)
    await testInfo.attach('窄屏实际字体与焦点对比度', {
      body: JSON.stringify({
        bodyFont: 30,
        numericBefore,
        numericAfter: numericBefore * 2,
        quoteBefore,
        quoteAfter: quoteBefore * 2,
        colors,
        ratios,
      }),
      contentType: 'application/json',
    })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({
      path: testInfo.outputPath('scene-320-font30.png'),
      fullPage: true,
    })
    await page.screenshot({ path: testInfo.outputPath('scene-320-font30-viewport.png') })
    const sent = page.waitForRequest((req) => req.method() === 'POST' && req.url().endsWith('/reconfirm'))
    await save.focus()
    await page.keyboard.press('Enter')
    const body = (await sent).postDataJSON()
    expect(body.time_evidence).toEqual([
      {
        chapter_id: fixture.chapter_id,
        revision: 1,
        start: a,
        end: z,
        quote: fixture.original_content.slice(a, z),
      },
    ])
    await expect(panel.getByRole('heading', { name: '人工核对记录已保存' })).toBeVisible()
    const row = await invariants(request, fixture)
    expect(row.revision).toBe(2)
    await expect(editor).toContainText('修订0')
    await expect(editor).toContainText('修订1')
  })

  test('事件并发变化409保留草稿，显式采用新依据再保存', async ({ page, request }) => {
    const fixture = await getFixture(),
      panel = await openWorkbench(page, fixture)
    await panel.getByLabel('由我修改描述性时间（需要原文依据）', { exact: true }).check()
    await panel.getByLabel('本次确认的故事时间', { exact: true }).fill('回铺时天还没有黑，具体时段未知')
    const reason = '第二次人工核对，保存失败仍需保留这份理由与原文选择。'
    await panel.getByLabel('人工重新核对理由').fill(reason)
    await page.getByText('重新核对已保存的提取场景', { exact: true }).click()
    await page.getByText('重新核对已保存的提取场景', { exact: true }).click()
    await expect(panel.getByLabel('人工重新核对理由')).toHaveValue(reason)
    const originalSource = panel.getByLabel('原场景冻结原文')
    await originalSource.focus()
    await page.keyboard.press('Control+A')
    await expect(originalSource).toHaveValue(fixture.original_content.replace(/\r\n?/g, '\n'))
    await panel.getByRole('button', { name: '使用当前连续选段作为时间依据' }).click()
    await post(request, `${prefix}/${fixture.fixture_id}/advance-event`)
    const response = page.waitForResponse(
      (res) => res.request().method() === 'POST' && res.url().endsWith('/reconfirm'),
    )
    const sent = page.waitForRequest((req) => req.method() === 'POST' && req.url().endsWith('/reconfirm'))
    await panel.getByRole('button', { name: '确认重新核对并保存为新场景修订' }).click()
    expect((await sent).postDataJSON().time_evidence[0].quote).toBe(fixture.original_content)
    expect((await response).status()).toBe(409)
    await expect(panel.getByLabel('人工重新核对理由')).toHaveValue(reason)
    await expect(panel.getByLabel('本次确认的故事时间', { exact: true })).toHaveValue(
      '回铺时天还没有黑，具体时段未知',
    )
    await expect(panel.getByRole('button', { name: '确认重新核对并保存为新场景修订' })).toBeDisabled()
    await panel.getByRole('button', { name: '重新读取核对依据（保留草稿）' }).click()
    const adopt = panel.getByRole('button', {
      name: '采用这份新依据继续核对（保留草稿）',
    })
    await expect(adopt).toBeVisible()
    await expect(panel.getByLabel('人工重新核对理由')).toHaveValue(reason)
    await adopt.focus()
    await page.keyboard.press('Enter')
    await expect(panel.getByRole('list', { name: '原引用与当前修订对照' })).toContainText('事件修订 1 → 2')
    await panel.getByRole('button', { name: '确认重新核对并保存为新场景修订' }).click()
    await expect(panel.getByRole('heading', { name: '人工核对记录已保存' })).toBeVisible()
    const row = await invariants(request, fixture)
    expect(row.revision).toBe(3)
  })

  test('保存成功但响应丢失时锁定原请求，查询回执恢复且不重复写修订', async ({ page, request }) => {
    const fixture = await getFixture(),
      panel = await openWorkbench(page, fixture)
    await panel.getByLabel('人工重新核对理由').fill('核对原引用，模拟回执响应丢失。')
    let nonce = ''
    await page.route('**/scene-times/*/reconfirm', async (route) => {
      nonce = route.request().postDataJSON().client_request_id
      await route.fetch()
      await route.abort('failed')
    })
    await panel.getByRole('button', { name: '确认重新核对并保存为新场景修订' }).click()
    await expect(panel.getByRole('region', { name: '保存结果待核实', exact: true })).toBeVisible()
    await expect(panel.getByLabel('人工重新核对理由')).toBeDisabled()
    await panel.getByRole('button', { name: '查询本次人工确认回执', exact: true }).click()
    await expect(panel.getByRole('heading', { name: '人工核对记录已保存' })).toBeVisible()
    const row = await invariants(request, fixture)
    expect(row.revision).toBe(4)
    const receipt = await (await request.get(scenePath(fixture) + '/reconfirmations/' + nonce)).json()
    expect(receipt.receipt.revision).toBe(4)
  })

  test('缺能力、未知事件或已归档时不开放重核绕路，正文与历史不变', async ({ page, request }) => {
    const fixture = await getFixture()
    let mode = 'missing'
    let posts = 0
    page.on('request', (req) => {
      if (req.method() === 'POST' && req.url().endsWith('/reconfirm')) posts++
    })
    await page.route('**/scene-times', async (route) => {
      const response = await route.fetch(),
        data = await response.json()
      data.scenes = data.scenes.map(
        (row: {
          id: string
          archived: number
          payload_sha256?: string
          payload: { story_order: number | null; order_basis: unknown }
        }) => {
          if (row.id !== fixture.scene_id) return row
          if (mode === 'missing') delete row.payload_sha256
          if (mode === 'archived') row.archived = 1
          if (mode === 'unknown') {
            row.payload.story_order = null
            row.payload.order_basis = {
              version: 'scene-event-refs/2',
              kind: 'unknown',
              reason: '尚无可靠事件时点',
              refs: [],
              covered_events: [],
            }
          }
          return row
        },
      )
      await route.fulfill({ response, json: data })
    })
    for (mode of ['missing', 'unknown', 'archived']) {
      await openTarget(page, fixture)
      await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
      await page.getByText('重新核对已保存的提取场景', { exact: true }).click()
      const area = page.getByRole('region', { name: '提取场景重核工作台', exact: true })
      await area.getByLabel('场景所在章节').selectOption(fixture.chapter_id)
      await area.getByRole('button', { name: /^回铺 · 场景修订/ }).click()
      await expect(area.getByRole('button', { name: '重新核对提取场景', exact: true })).toHaveCount(0)
    }
    expect(posts).toBe(0)
    expect((await invariants(request, fixture)).revision).toBe(4)
  })

  test('切换章节后旧预览晚到不覆盖当前目标或开放旧场景提交', async ({ page, request }) => {
    const fixture = await getFixture()
    let release: () => void = () => {}
    const waiting = new Promise<void>((resolve) => {
      release = resolve
    })
    let arrived: () => void = () => {}
    const seen = new Promise<void>((resolve) => {
      arrived = resolve
    })
    await page.route('**/reconfirmation-preview', async (route) => {
      const response = await route.fetch()
      arrived()
      await waiting
      await route.fulfill({ response })
    })
    await openTarget(page, fixture)
    await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
    await page.getByText('重新核对已保存的提取场景', { exact: true }).click()
    const area = page.getByRole('region', { name: '提取场景重核工作台', exact: true })
    await area.getByLabel('场景所在章节').selectOption(fixture.chapter_id)
    await area.getByRole('button', { name: /^回铺 · 场景修订/ }).click()
    await area.getByRole('button', { name: '重新核对提取场景', exact: true }).click()
    await seen
    await area.getByLabel('场景所在章节').selectOption(fixture.empty_chapter_id)
    await expect(area).toContainText('本章没有已保存的场景')
    const response = page.waitForResponse((res) => res.url().endsWith('/reconfirmation-preview'))
    release()
    await response
    await expect(area.getByLabel('人工重新核对理由')).toHaveCount(0)
    await expect(area.getByRole('button', { name: '确认重新核对并保存为新场景修订' })).toHaveCount(0)
    expect((await invariants(request, fixture)).revision).toBe(4)
  })
  test('成功响应中的引用见证不对应原请求时保持待核实，查原回执后才能完成', async ({ page, request }) => {
    const fixture = await getFixture(),
      panel = await openWorkbench(page, fixture)
    await panel.getByLabel('人工重新核对理由').fill('本次人工核对必须读取确属原请求的事件见证。')
    let writes = 0
    await page.route('**/scene-times/*/reconfirm', async (route) => {
      writes++
      const response = await route.fetch(),
        body = await response.json()
      body.current_refs[0].record_sha256 = 'b'.repeat(64)
      await route.fulfill({ response, json: body })
    })
    await panel.getByRole('button', { name: '确认重新核对并保存为新场景修订' }).click()
    await expect(panel.getByRole('region', { name: '保存结果待核实', exact: true })).toBeVisible()
    await expect(panel.getByRole('heading', { name: '人工核对记录已保存' })).toHaveCount(0)
    await panel.getByRole('button', { name: '查询本次人工确认回执', exact: true }).click()
    await expect(panel.getByRole('heading', { name: '人工核对记录已保存' })).toBeVisible()
    expect(writes).toBe(1)
    expect((await invariants(request, fixture)).revision).toBe(5)
  })
})
