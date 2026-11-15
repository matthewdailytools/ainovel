import { expect, test } from '@playwright/test'
import { createHash } from 'node:crypto'
import type { Task } from '../../frontend/api'

test('未触发全书约定：写作与复检只读呈现、键盘和320px实际文字翻倍', async ({ page }, info) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await (
    await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 未触发约定只读呈现' } })
  ).json()
  const chapter = await (
    await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
      headers,
      data: { title: '原稿保留' },
    })
  ).json()
  const original = await (
    await page.request.put(`/api/chapters/${chapter.id}`, {
      headers,
      data: { base_revision: 0, content: '作者原稿保留😀𠮷，不由展示夹具采纳。' },
    })
  ).json()
  const budgetBefore = (await (await page.request.get('/api/config')).json()).budget
  const condition = '若涉及母亲，应保持她尚不知情。'
  const text = '沈砚将旧尺交还老陈，收好当日的测量记录。😀𠮷'
  const hash = (value: string) => createHash('sha256').update(value).digest('hex')
  // These browser-only fixtures verify presentation, never backend qualification
  // or model quality. The isolated saved chapter remains real and unchanged.
  const review = {
    conditional_policy_version: 'conditional-book-check/1',
    conditional_book_conditions: [condition],
    checks: [
      {
        condition,
        result: 'not_triggered',
        scope: 'book',
        applicability: 'not_triggered',
        chapter_obligation: 'not_required',
        explanation: '本章未涉及母亲；全书结束仍须核对。',
        evidence: '',
      },
    ],
    findings: [],
    summary: '本章未触发全书条件，未宣称该条件已兑现。',
  }
  const audit = {
    version: 'author-plot-audit/2',
    source: { kind: 'workflow_plan', text_sha256: hash('只将旧尺交还老陈。') },
    candidate_sha256: hash(text),
    candidate_revision: 1,
    status: 'model_pass',
    summary: '只读展示夹具，不能替代模型验收。',
    policy: '模型辅助核对，作者决定采用。',
    interpretation_note: '本章原章纲。',
    items: [
      {
        id: 'P01',
        author_evidence: '只将旧尺交还老陈。',
        actor: '沈砚',
        action: '归还',
        object: '旧尺',
        direction: '沈砚→老陈',
        result: '归还',
        certainty: 'explicit',
        recipient_scope: 'only',
        scope_condition: '本次归还',
      },
    ],
    checks: [
      {
        item_id: 'P01',
        result: 'pass',
        observed: '已归还',
        evidence: text,
        reason: '方向一致。',
        repair: '',
      },
    ],
    findings: [],
  }
  const candidate = {
    id: 'ui-conditional-candidate',
    branch_id: novel.branch_id,
    task_id: 'ui-conditional-workflow',
    kind: 'chapter_draft',
    chapter_id: chapter.id,
    base_revision: original.revision,
    candidate_revision: 1,
    status: 'candidate',
    payload: { text, review, plot_audit: audit, review_passed: true, fixed: [condition] },
    basis: { story_revision: 0 },
  }
  const report = {
    ...candidate,
    id: 'ui-conditional-report',
    task_id: 'ui-conditional-recheck',
    kind: 'candidate_recheck',
    candidate_revision: 0,
    payload: {
      version: 'candidate-recheck/1',
      target: {
        kind: 'candidate',
        artifact_id: candidate.id,
        revision: 1,
        chapter_id: chapter.id,
        base_revision: original.revision,
        content_sha256: hash(text),
      },
      source_workflow_id: candidate.task_id,
      review,
      plot_audit: audit,
      review_passed: true,
      memory_generated: true,
      note: '只读UI夹具：未修改或采纳原稿。',
    },
  }
  await page.route(`**/api/artifacts?branch_id=${novel.branch_id}`, (route) =>
    route.fulfill({ json: [report, candidate] }),
  )
  await page.route(`**/api/artifacts/${candidate.id}`, (route) => route.fulfill({ json: candidate }))
  await page.route(`**/api/artifacts/${report.id}`, (route) => route.fulfill({ json: report }))
  const writes: string[] = [],
    errors: string[] = []
  page.on('request', (request) => {
    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(request.method())) writes.push(request.url())
  })
  page.on('pageerror', (error) => errors.push(error.message))
  const measurements = []
  for (const id of [candidate.id, report.id]) {
    await page.reload()
    await page.setViewportSize({ width: 320, height: 900 })
    await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
    await page.getByRole('button', { name: '写作', exact: true }).click()
    const selector = page.getByRole('combobox', { name: '比较候选', exact: true })
    await selector.selectOption(id)
    const notice = page.getByRole('region', { name: '本章未触发的全书约定', exact: true })
    await expect(notice).toContainText(condition)
    await expect(notice).toContainText('本章未触发，整书待核对')
    await expect(notice).not.toContainText('通过')
    await page.evaluate(() => {
      const sizes = [...document.querySelectorAll('*')].map(
        (element) => [element, parseFloat(getComputedStyle(element).fontSize)] as const,
      )
      for (const [element, size] of sizes)
        if (element instanceof HTMLElement) element.style.fontSize = `${size * 2}px`
    })
    const bounds = await notice.boundingBox()
    const bodyFont = await page.locator('body').evaluate((element) => getComputedStyle(element).fontSize)
    expect(parseFloat(bodyFont)).toBeGreaterThanOrEqual(28)
    expect(bounds!.x).toBeGreaterThanOrEqual(0)
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(320)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)
    measurements.push({ id, bodyFont, bounds })
    if (id === candidate.id) {
      const summary = page.getByText('查看本章检查依据', { exact: true })
      await summary.focus()
      await summary.press('Enter')
      await expect(summary.locator('..').locator('pre')).toBeVisible()
    } else {
      const panel = page.getByRole('region', { name: '候选复检报告', exact: true })
      await expect(panel).toContainText('本轮含未触发约定')
      await expect(panel).not.toContainText('全部检查通过后生成了记忆候选')
      await expect(panel.getByRole('button', { name: /采纳|恢复|拒绝/ })).toHaveCount(0)
      const target = panel.getByRole('button', { name: '查看目标候选', exact: true })
      await expect(target).toBeEnabled()
      await target.focus()
      await expect(target).toBeFocused()
      await target.press('Enter')
      await expect(page.getByRole('textbox', { name: '候选内容', exact: true })).toHaveValue(text)
    }
  }
  expect(writes).toEqual([])
  expect(errors).toEqual([])
  expect(await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).toEqual(original)
  expect((await (await page.request.get('/api/config')).json()).budget).toEqual(budgetBefore)
  await info.attach('conditional-review-ui-summary', {
    contentType: 'application/json',
    body: JSON.stringify({ browserFixtures: true, measurements, writes, errors, modelCalls: 0 }),
  })
})

for (const selectOtherProfile of [false, true]) {
  test(`旧批次新约定续跑：${selectOtherProfile ? '主动选择同服务商配置' : '默认沿用配置'}，失败保留输入和幂等标识`, async ({
    page,
  }, info) => {
    await page.goto('/')
    const session = await (await page.request.get('/api/session')).json()
    const headers = { 'X-Ainovel-CSRF': session.csrf }
    const novel = await (
      await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 新约定续跑入口夹具' } })
    ).json()
    const chapter = await (
      await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
        headers,
        data: { title: '已保存的原稿' },
      })
    ).json()
    const original = await (
      await page.request.put(`/api/chapters/${chapter.id}`, {
        headers,
        data: { base_revision: 0, content: '本章原稿与版本不由续跑入口夹具改变😀𠮷。' },
      })
    ).json()
    const configuration = await (await page.request.get('/api/config')).json()
    const budget = configuration.budget
    const profile = configuration.accounts.find((entry: { id: string }) => entry.id === 'offline')
    expect(profile).toBeDefined()
    await page.route('**/api/config', (route) =>
      route.fulfill({
        json: {
          ...configuration,
          accounts: [
            ...configuration.accounts,
            { ...profile, id: 'offline-base', model: 'offline-base' },
            { ...profile, id: 'offline-other-provider', kind: 'other-provider' },
            { ...profile, id: 'offline-other-currency', currency: 'OTHER' },
          ],
        },
      }),
    )
    // The endpoint responses below are UI fixtures. Domain/ledger safety is
    // covered by backend tests, not inferred from these presentation assertions.
    let source: Task = {
      id: 'ui-conditional-legacy-workflow',
      novel_id: novel.id,
      branch_id: novel.branch_id,
      kind: 'workflow',
      status: 'needs_review',
      created: 1,
      cancel: 0,
      error: '',
      payload: {
        account: 'offline',
        mode: 'automatic',
        chapters: 2,
        budget_limit: 1,
        completion_scope: 'segment',
      },
      result: { next_chapter: 2, waiting_artifact: 'ui-conditional-adopted-draft' },
    }
    let continued: Task | undefined
    const resumeRequests: { pause_after_maintenance?: boolean }[] = []
    const policyRequests: { client_request_id: string; reason: string; account?: string }[] = []
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    await page.route('**/api/tasks', (route) =>
      route.fulfill({ json: continued ? [continued, source] : [source] }),
    )
    await page.route('**/api/artifacts/ui-conditional-adopted-draft', (route) =>
      route.fulfill({
        json: {
          id: 'ui-conditional-adopted-draft',
          task_id: source.id,
          branch_id: novel.branch_id,
          chapter_id: chapter.id,
          kind: 'chapter_draft',
          status: 'accepted',
          candidate_revision: 0,
          base_revision: 0,
          payload: { text: original.content },
          basis: {},
        },
      }),
    )
    await page.route(`**/api/tasks/${source.id}/resume`, async (route) => {
      resumeRequests.push(route.request().postDataJSON())
      if (resumeRequests.length === 1)
        return route.fulfill({
          status: 503,
          json: { code: 'fixture', message: 'UI夹具：维护请求暂未完成，保留选择。' },
        })
      source = { ...source, status: 'paused', result: { next_chapter: 2 } }
      await route.fulfill({ json: source })
    })
    await page.route(`**/api/tasks/${source.id}/continue-conditional-policy`, async (route) => {
      policyRequests.push(route.request().postDataJSON())
      if (policyRequests.length === 1)
        return route.fulfill({
          status: 503,
          json: { code: 'fixture', message: 'UI夹具：续跑请求暂未完成，保留原因。' },
        })
      await new Promise((resolve) => setTimeout(resolve, 500))
      continued = {
        ...source,
        id: 'ui-conditional-new-workflow',
        status: 'queued',
        payload: {
          ...source.payload,
          account: policyRequests.at(-1)?.account || source.payload.account,
          budget_limit: 0.6,
        },
        result: {
          next_chapter: 2,
          continued_from: source.id,
          conditional_book_policy_version: 'conditional-book-check/1',
        },
      }
      source = { ...source, status: 'continued', result: { ...source.result, continued_to: continued.id } }
      await route.fulfill({ status: 202, json: continued })
    })
    await page.reload()
    await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
    await page.getByRole('button', { name: '整书创作', exact: true }).click()
    const policySummary = page.getByText('按新约定从检查点继续', { exact: true })
    await expect(policySummary).toHaveCount(0)
    const pause = page.getByRole('checkbox', { name: '完成已采纳章节维护后暂停', exact: true })
    await expect(pause).not.toBeChecked()
    await pause.focus()
    await pause.press('Space')
    const resume = page.getByRole('button', { name: '从保存的检查点恢复', exact: true })
    await expect(resume).toBeEnabled()
    await resume.click()
    await expect(page.getByText('UI夹具：维护请求暂未完成，保留选择。', { exact: true })).toBeVisible()
    await expect(pause).toBeChecked()
    await resume.click()
    await expect(policySummary).toBeVisible()
    expect(resumeRequests).toHaveLength(2)
    expect(resumeRequests.every((request) => request.pause_after_maintenance === true)).toBeTruthy()
    await policySummary.focus()
    await policySummary.press('Enter')
    const details = policySummary.locator('..')
    await expect(details).toContainText('不重置预算')
    await expect(details.locator('input[type="number"]')).toHaveCount(0)
    const account = details.getByRole('combobox', { name: '新约定续跑配置', exact: true })
    await expect(account).toHaveValue('')
    await expect(account.locator('option[value="offline-base"]')).toHaveCount(1)
    await expect(account.locator('option[value="offline-other-provider"]')).toHaveCount(0)
    await expect(account.locator('option[value="offline-other-currency"]')).toHaveCount(0)
    if (selectOtherProfile) await account.selectOption('offline-base')
    const reason = details.getByRole('textbox', { name: '按新约定续跑的原因', exact: true })
    const button = details.getByRole('button', { name: '创建按新约定续跑任务', exact: true })
    await expect(button).toBeDisabled()
    await page.setViewportSize({ width: 320, height: 900 })
    await page.evaluate(() => {
      const sizes = [...document.querySelectorAll('*')].map(
        (element) => [element, parseFloat(getComputedStyle(element).fontSize)] as const,
      )
      for (const [element, size] of sizes)
        if (element instanceof HTMLElement) element.style.fontSize = `${size * 2}px`
    })
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)
    await reason.fill('按已明确的新约定继续，保留整书核对。')
    await button.click()
    await expect(page.getByText('UI夹具：续跑请求暂未完成，保留原因。', { exact: true })).toBeVisible()
    await expect(reason).toHaveValue('按已明确的新约定继续，保留整书核对。')
    await expect(account).toHaveValue(selectOtherProfile ? 'offline-base' : '')
    await button.focus()
    await page.keyboard.press('Enter')
    await page.keyboard.press('Enter')
    await expect(page.getByRole('combobox', { name: '本作品批次', exact: true })).toHaveValue(
      'ui-conditional-new-workflow',
    )
    await expect(policySummary).toHaveCount(0)
    expect(policyRequests).toHaveLength(2)
    expect(policyRequests[1]).toEqual(policyRequests[0])
    expect(Object.keys(policyRequests[1]).sort()).toEqual(
      selectOtherProfile ? ['account', 'client_request_id', 'reason'] : ['client_request_id', 'reason'],
    )
    if (selectOtherProfile) expect(policyRequests[1].account).toBe('offline-base')
    expect(errors).toEqual([])
    expect(await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).toEqual(original)
    expect((await (await page.request.get('/api/config')).json()).budget).toEqual(budget)
    await info.attach('conditional-policy-continuation-ui', {
      contentType: 'application/json',
      body: JSON.stringify({ browserFixtures: true, resumeRequests, policyRequests, errors, modelCalls: 0 }),
    })
  })
}
