import { expect, test, type Page, type BrowserContext } from '@playwright/test'

const SUMMARY = '按新版流向来源政策继续后续章节'
const REASON = '本次新版流向政策续跑原因'
const SUBMIT = '创建并开始新版流向政策子批次'
type Fixture = {
  id: string
  fixture_id: string
  branch_id: string
  parent_id: string
  first_chapter_id: string
  future_chapter_id: string
  candidate_id: string
  structure_id: string
  maintenance_receipt: { record_ids: string[] }
}
type Row = Record<string, unknown>
type Task = { id: string; kind: string; status: string; payload: Row; result: Row }
const guards = new WeakMap<Page, { blocked: string[]; external: string[]; writes: string[] }>()
const region = (page: Page) => page.getByRole('region', { name: '新版流向政策续跑', exact: true })
async function setup(page: Page, context: BrowserContext) {
  const origin = test.info().project.use.baseURL || 'http://127.0.0.1:8877'
  const session = await (await page.request.get(origin + '/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const seeded = await page.request.post(origin + '/test/flow-policy/fixtures', {
    headers,
    data: { client_request_id: crypto.randomUUID() },
  })
  expect(seeded.status(), await seeded.text()).toBe(201)
  const fixture: Fixture = await seeded.json(),
    guard = { blocked: [] as string[], external: [] as string[], writes: [] as string[] }
  guards.set(page, guard)
  await context.route('**/*', (route) => {
    const request = route.request(),
      url = new URL(request.url())
    if (url.origin !== origin) {
      guard.external.push(request.url())
      return route.abort()
    }
    if (request.method() === 'GET') return route.continue()
    const policy =
      request.method() === 'POST' && url.pathname === `/api/tasks/${fixture.parent_id}/continue-flow-policy`
    const reading =
      request.method() === 'PUT' && url.pathname === `/api/branches/${fixture.branch_id}/reading-state`
    if (policy || reading) {
      guard.writes.push(request.method() + ' ' + url.pathname)
      return route.continue()
    }
    guard.blocked.push(request.method() + ' ' + url.pathname)
    return route.abort()
  })
  await page.goto(origin)
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(fixture.id)
  await expect(page.getByRole('combobox', { name: '工作版本', exact: true })).toHaveValue(fixture.branch_id)
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  await page.getByRole('combobox', { name: '本作品批次', exact: true }).selectOption(fixture.parent_id)
  return { fixture, headers, origin }
}
async function readAndDecide(page: Page) {
  await page.getByText(SUMMARY, { exact: true }).click()
  const preview = page.waitForResponse(
    (r) => r.url().endsWith('/continue-flow-policy-preview') && r.request().method() === 'GET',
  )
  await region(page).getByRole('button', { name: '读取新版政策续跑依据', exact: true }).click()
  const response = await preview
  expect(response.status(), await response.text()).toBe(200)
  await region(page)
    .getByRole('textbox', { name: REASON })
    .fill('原生隔离验证：只为后续新审查启用新版出处核对，原稿和旧费用保留。')
  await region(page)
    .getByRole('checkbox', {
      name: '明确为新子任务选择 source-flow-entailment/2，接受后续新增核对及费用',
      exact: true,
    })
    .check()
  return response.json()
}
test.afterEach(async ({ page, context }) => {
  await context.unrouteAll({ behavior: 'wait' })
  const guard = guards.get(page)
  if (guard) {
    expect(guard.blocked).toEqual([])
    expect(guard.external).toEqual([])
  }
})

test('真实API与Worker从维护完成的旧检查点创建并开始子任务，原稿费用保持', async ({ page, context }, info) => {
  const { fixture, origin } = await setup(page, context)
  const before = await (
    await page.request.get(`${origin}/test/flow-policy/fixtures/${fixture.fixture_id}/proof`)
  ).json()
  expect(before.current.task.status).toBe('paused')
  expect(before.current.task.result.next_chapter).toBe(2)
  expect(before.current.task.result.repair_round).toBe(1)
  expect(before.current.structure.status).toBe('accepted')
  expect(fixture.maintenance_receipt.record_ids).toHaveLength(1)
  const preview = await readAndDecide(page)
  expect(preview.budget.remaining_run_limit).toBe(4.9)
  await region(page).screenshot({ path: info.outputPath('native-preview.png') })
  const posted = page.waitForResponse(
    (r) => r.url().endsWith('/continue-flow-policy') && r.request().method() === 'POST',
  )
  await region(page).getByRole('button', { name: SUBMIT, exact: true }).click()
  const response = await posted
  expect(response.status(), await response.text()).toBe(202)
  const child: Task = await response.json()
  expect(child.status).toBe('queued')
  expect(child.result.flow_source_entailment_version).toBe('source-flow-entailment/2')
  expect(child.result.repair_round).toBe(1)
  expect(child.result.plan).toEqual(before.current.task.result.plan)
  expect(child.payload.adoption).toBe('manual')
  expect(child.payload.budget_limit).toBe(4.9)
  await expect(page.getByRole('region', { name: '新版流向政策子任务回执' })).toBeVisible()
  await region(page).getByRole('button', { name: '打开新版政策子任务', exact: true }).click()
  await expect(page.getByRole('combobox', { name: '本作品批次', exact: true })).toHaveValue(child.id)
  let finished: Task = child
  await expect
    .poll(
      async () => {
        finished = await (await page.request.get(`${origin}/api/tasks/${child.id}`)).json()
        return finished.status
      },
      { timeout: 25000 },
    )
    .toBe('needs_review')
  expect(finished.result.next_chapter).toBe(2)
  expect(typeof finished.result.waiting_artifact).toBe('string')
  const candidate = await (
    await page.request.get(`${origin}/api/artifacts/${finished.result.waiting_artifact}`)
  ).json()
  expect(candidate.kind).toBe('chapter_draft')
  expect(candidate.status).toBe('candidate')
  expect(candidate.payload.review_passed).toBe(true)
  const chapters = await (
    await page.request.get(`${origin}/api/branches/${fixture.branch_id}/chapters`)
  ).json()
  expect(chapters.find((c: { position: number }) => c.position === 2).revision).toBe(0)
  const nextChapter = chapters.find((c: { position: number }) => c.position === 2)
  const nextBody = await (await page.request.get(`${origin}/api/chapters/${nextChapter.id}`)).json()
  expect(nextBody.revision).toBe(0)
  expect(nextBody.content).toBe('')
  const proof = await (
    await page.request.get(`${origin}/test/flow-policy/fixtures/${fixture.fixture_id}/proof`)
  ).json()
  expect(Object.values(proof.protected)).not.toContain(false)
  expect(proof.old_calls_unchanged).toBe(true)
  expect(
    proof.parent_payload_unchanged && proof.parent_plan_unchanged && proof.parent_legacy_flow_preserved,
  ).toBe(true)
  expect(proof.current.task.status).toBe('continued')
  expect(proof.current.task.result).toEqual({ ...before.current.task.result, continued_to: child.id })
  expect(proof.current.calls).toEqual(before.current.calls)
  expect(proof.transport_sends).toEqual([])
  await info.attach('native-checkpoint-proof', {
    contentType: 'application/json',
    body: JSON.stringify({ fixture, child, finished, proof, preview }, null, 2),
  })
})

test('真实后章模块已发送未知且无候选时，旧预览提交409并保留输入', async ({ page, context }, info) => {
  const { fixture, headers, origin } = await setup(page, context)
  await readAndDecide(page)
  const reason = await region(page).getByRole('textbox', { name: REASON }).inputValue()
  const moduleResponse = await page.request.post(`${origin}/api/modules`, {
    headers,
    data: {
      novel_id: fixture.id,
      branch_id: fixture.branch_id,
      chapter_id: fixture.future_chapter_id,
      base_revision: 0,
      action: 'writing.expand_plot',
      instruction: 'flow-policy-native-timeout：隔离后章正文派发，不能借新政策重发。',
      account: 'offline',
      client_request_id: crypto.randomUUID(),
      budget_limit: 1,
      max_output_tokens: 256,
    },
  })
  expect(moduleResponse.status(), await moduleResponse.text()).toBe(202)
  const moduleTask = await moduleResponse.json()
  await expect
    .poll(
      async () => (await (await page.request.get(`${origin}/api/tasks/${moduleTask.id}`)).json()).status,
      { timeout: 15000 },
    )
    .toBe('blocked_runtime')
  const calls = await (
    await page.request.get(`${origin}/test/flow-policy/fixtures/${fixture.fixture_id}/proof`)
  ).json()
  expect(calls.transport_sends.filter((r: { task_id: string }) => r.task_id === moduleTask.id)).toHaveLength(
    1,
  )
  const unknown = calls.current.calls.filter((r: { task_id: string }) => r.task_id === moduleTask.id)
  expect(unknown).toHaveLength(1)
  expect(unknown[0].status).toBe('unknown')
  expect(unknown[0].charged).toBeNull()
  expect(
    (await (await page.request.get(`${origin}/api/artifacts?branch_id=${fixture.branch_id}`)).json()).filter(
      (r: { task_id: string }) => r.task_id === moduleTask.id,
    ),
  ).toEqual([])
  const posted = page.waitForResponse(
    (r) => r.url().endsWith('/continue-flow-policy') && r.request().method() === 'POST',
  )
  await region(page).getByRole('button', { name: SUBMIT, exact: true }).click()
  const response = await posted
  expect(response.status(), await response.text()).toBe(409)
  expect((await response.json()).code).toBe('flow_checkpoint')
  await expect(region(page).getByRole('textbox', { name: REASON })).toHaveValue(reason)
  await expect(
    region(page).getByRole('checkbox', {
      name: '明确为新子任务选择 source-flow-entailment/2，接受后续新增核对及费用',
      exact: true,
    }),
  ).toBeChecked()
  await expect(region(page).getByRole('button', { name: SUBMIT, exact: true })).toBeDisabled()
  const proof = await (
    await page.request.get(`${origin}/test/flow-policy/fixtures/${fixture.fixture_id}/proof`)
  ).json()
  expect(proof.current.task).toEqual(proof.before.task)
  expect(proof.current.calls).toEqual(calls.current.calls)
  expect(proof.transport_sends).toEqual(calls.transport_sends)
  await info.attach('native-future-dispatch-proof', {
    contentType: 'application/json',
    body: JSON.stringify({ fixture, moduleTask, response: await response.json(), proof }, null, 2),
  })
})
