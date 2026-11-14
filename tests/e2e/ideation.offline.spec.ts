import { expect, test, type Page } from '@playwright/test'

const idea = '一个修补旧物的学徒，可以看见失主遗失的记忆。不恋爱，不战斗。😀𠮷'
const edited = '学徒修好一只旧匣，听到师父求救的声音。为寻找师父，他要决定愿意失去哪段记忆。'
const fields = {
  emotion_target: '疑虑与小进展',
  core_hook: '师父去了哪里',
  direction: '原来的方向',
  ending: '开放结局',
  fixed_results: ['保住旧匣'],
  prohibitions: ['不恋爱', '不战斗'],
  open_details: ['旧物细节可以补足'],
}

async function openIdea(page: Page, title: string, withContract = false) {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const novel = await (await page.request.post('/api/novels', { headers, data: { title } })).json()
  if (withContract) {
    const response = await page.request.post(`/api/branches/${novel.branch_id}/definitions`, {
      headers,
      data: { kind: 'intent', text: '作者原话应保留。', intent_contract: fields, base_story_revision: 0 },
    })
    expect(response.ok()).toBeTruthy()
  }
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '小说构思', exact: true }).click()
  await expect(page.getByRole('heading', { name: '小说构思', exact: true })).toBeVisible()
  await expect(page.getByRole('textbox', { name: '你的想法', exact: true })).toBeEnabled()
  return { novel, headers }
}

test('一句想法生成、编辑、刷新恢复、采用并去写作；保留原有约定', async ({ page }, testInfo) => {
  const { novel } = await openIdea(page, '后台验证 · 简单小说构思', true)
  const input = page.getByRole('textbox', { name: '你的想法', exact: true })
  await input.focus()
  await input.dispatchEvent('compositionstart')
  await input.fill(idea)
  await input.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true })
  await input.dispatchEvent('compositionend')
  expect(
    await page.request.get(`/api/tasks?branch_id=${novel.branch_id}`).then((r) => r.json()),
  ).toHaveLength(0)
  await page.keyboard.press('Tab')
  await expect(page.getByRole('button', { name: '帮我展开', exact: true })).toBeFocused()
  const request = page.waitForRequest((r) => r.url().endsWith('/api/modules') && r.method() === 'POST')
  await page.keyboard.press('Enter')
  const payload = (await request).postDataJSON()
  expect(payload.action).toBe('ideation.structure')
  expect(payload.max_output_tokens).toBe(8192)
  expect(payload.chapter_id).toBeUndefined()
  expect(payload.instruction).toBe(idea)
  expect(payload.reference_ids).toBeUndefined()
  const plan = page.getByRole('textbox', { name: '修改构思方案', exact: true })
  await expect(plan).toHaveValue(/一句话故事：修补旧物/)
  for (const part of [
    '人物目标与动机',
    '核心矛盾与风险',
    '开端',
    '发展',
    '转折',
    '高潮',
    '结局与人物变化',
    '主要伏笔与承诺回收',
  ]) {
    expect(await plan.inputValue()).toContain(part)
  }
  expect(
    (await (await page.request.get(`/api/branches/${novel.branch_id}/author-contract`)).json())
      .intent_contract.direction,
  ).toBe(fields.direction)
  await plan.fill(edited)
  page.once('dialog', (dialog) => void dialog.accept())
  await page.reload()
  await page.getByRole('button', { name: '小说构思', exact: true }).click()
  await expect(plan).toHaveValue(edited)
  await expect(input).toHaveValue(idea)
  await page.getByRole('button', { name: '采用构思', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('已采用为本书故事方向')
  const contract = await (await page.request.get(`/api/branches/${novel.branch_id}/author-contract`)).json()
  expect(contract.intent_contract).toEqual({ ...fields, direction: edited })
  expect(contract.author_text).toBe('作者原话应保留。')
  expect(contract.provenance.source_candidate_id).toBeTruthy()
  const originalCandidate = await (
    await page.request.get(`/api/artifacts/${contract.provenance.source_candidate_id}`)
  ).json()
  expect(originalCandidate.payload.idea_structure_version).toBe('1.0.0')
  expect(originalCandidate.payload.idea_structure.beats.map((beat: { stage: string }) => beat.stage)).toEqual(
    ['开端', '发展', '转折', '高潮', '结局'],
  )
  const fresh = await page.context().newPage()
  await fresh.goto('/')
  await fresh.getByRole('button', { name: '小说构思', exact: true }).click()
  await expect(fresh.getByRole('textbox', { name: '修改构思方案', exact: true })).toHaveValue(edited)
  await expect(fresh.getByRole('button', { name: '已采用', exact: true })).toBeDisabled()
  await fresh.close()
  expect(await (await page.request.get(`/api/branches/${novel.branch_id}/chapters`)).json()).toHaveLength(0)
  await page.screenshot({ path: testInfo.outputPath('ideation-desktop.png'), fullPage: true })
  await page.setViewportSize({ width: 320, height: 850 })
  await expect(page.getByRole('button', { name: '去写作 →', exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await page.screenshot({ path: testInfo.outputPath('ideation-320.png'), fullPage: true })
  await page.setViewportSize({ width: 768, height: 1024 })
  await page.addStyleTag({
    content: 'html { font-size: 30px; } .ideation-page label, .ideation-page .small { font-size: 28px; }',
  })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await page.getByRole('button', { name: '去写作 →', exact: true }).click()
  await expect(page.getByRole('button', { name: '写作', exact: true })).toHaveAttribute(
    'aria-current',
    'page',
  )
})

test('未配置模型也能直接采用；空输入不会提交', async ({ page }) => {
  await page.route('**/api/config', async (route) => {
    const response = await route.fetch()
    const config = await response.json()
    await route.fulfill({
      response,
      json: { ...config, accounts: config.accounts.map((a: object) => ({ ...a, configured: false })) },
    })
  })
  const { novel } = await openIdea(page, '后台验证 · 直接采用想法')
  await expect(page.getByRole('button', { name: '直接采用这段想法', exact: true })).toBeDisabled()
  await page.getByRole('textbox', { name: '你的想法', exact: true }).fill(idea)
  await expect(page.getByRole('button', { name: '帮我展开', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: '直接采用这段想法', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('已采用为本书故事方向')
  expect(
    (await (await page.request.get(`/api/branches/${novel.branch_id}/author-contract`)).json())
      .intent_contract.direction,
  ).toBe(idea)
  expect(await (await page.request.get(`/api/tasks?branch_id=${novel.branch_id}`)).json()).toHaveLength(0)
})

test('另一窗口改约后先保留输入，重新读取再采用时保留新锁定', async ({ page }) => {
  const { novel, headers } = await openIdea(page, '后台验证 · 构思并发保护', true)
  await page.getByRole('textbox', { name: '你的想法', exact: true }).fill(idea)
  const current = await (await page.request.get(`/api/branches/${novel.branch_id}/author-contract`)).json()
  const update = await page.request.post(`/api/branches/${novel.branch_id}/definitions`, {
    headers,
    data: {
      kind: 'intent',
      text: '另一窗口的新原话',
      intent_contract: { ...fields, fixed_results: ['旧匣不能损坏'] },
      base_story_revision: current.story_revision,
    },
  })
  expect(update.ok()).toBeTruthy()
  await page.getByRole('button', { name: '直接采用这段想法', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('本书设定已更新')
  await expect(page.getByRole('textbox', { name: '你的想法', exact: true })).toHaveValue(idea)
  await page.getByRole('button', { name: '重新读取当前约定', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('已重新读取当前约定')
  await page.getByText('当前创作约定', { exact: true }).click()
  await expect(page.getByText('旧匣不能损坏', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: '直接采用这段想法', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('已采用为本书故事方向')
  const updated = await (await page.request.get(`/api/branches/${novel.branch_id}/author-contract`)).json()
  expect(updated.intent_contract.fixed_results).toEqual(['旧匣不能损坏'])
  expect(updated.author_text).toBe('另一窗口的新原话')
})

test('提交失败保留想法；重试复用请求标识，候选可从历史载入', async ({ page }) => {
  await openIdea(page, '后台验证 · 构思重试')
  const keys: string[] = []
  await page.route('**/api/modules', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    keys.push(route.request().postDataJSON().client_request_id)
    if (keys.length === 1)
      return route.fulfill({ status: 503, json: { code: 'offline', message: '工程故障替身：请重试。' } })
    await route.continue()
  })
  await page.getByRole('textbox', { name: '你的想法', exact: true }).fill(idea)
  await page.getByRole('button', { name: '帮我展开', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('请重试')
  await expect(page.getByRole('textbox', { name: '你的想法', exact: true })).toHaveValue(idea)
  await page.getByRole('button', { name: '帮我展开', exact: true }).click()
  await expect(page.getByRole('textbox', { name: '修改构思方案', exact: true })).toHaveValue(/一句话故事/)
  expect(keys).toHaveLength(2)
  expect(keys[0]).toBe(keys[1])
  await page.getByRole('button', { name: '再想一个', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('构思已生成并保存')
  await page.getByText('历史构思（2）', { exact: true }).click()
  const history = page.getByRole('combobox', { name: '载入历史构思', exact: true })
  await expect(history.locator('option')).toHaveCount(3)
  await history.selectOption({ index: 2 })
  await expect(page.getByRole('status')).toContainText('已载入历史构思')
})

test('取消构思后保留想法及中断片段；不产生采用设定', async ({ page }) => {
  const { novel } = await openIdea(page, '后台验证 · 构思取消')
  await page.getByRole('textbox', { name: '你的想法', exact: true }).fill('后台慢速构思：' + idea)
  await page.getByRole('button', { name: '帮我展开', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('执行中')
  await page.getByRole('button', { name: '取消生成', exact: true }).click()
  await expect(page.getByText('中断的构思', { exact: true })).toBeVisible()
  await expect(page.getByRole('textbox', { name: '你的想法', exact: true })).toHaveValue(
    '后台慢速构思：' + idea,
  )
  await page.getByText('中断的构思', { exact: true }).click()
  await page.getByRole('button', { name: '查看保留的生成片段', exact: true }).click()
  await expect(page.locator('pre.stream')).toContainText('学徒听见了旧匣里的求救声')
  expect(
    (await (await page.request.get(`/api/branches/${novel.branch_id}/author-contract`)).json()).configured,
  ).toBe(false)
})

test('生成中刷新后继续等待完整方案，新候选不会显示为已采用', async ({ page }) => {
  const { novel } = await openIdea(page, '后台验证 · 完整构思刷新续看')
  await page.getByRole('textbox', { name: '你的想法', exact: true }).fill('后台慢速构思：' + idea)
  await page.getByRole('button', { name: '帮我展开', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('执行中')
  await page.reload()
  await page.getByRole('button', { name: '小说构思', exact: true }).click()
  const plan = page.getByRole('textbox', { name: '修改构思方案', exact: true })
  await expect(plan).toHaveValue(/结局与人物变化/, { timeout: 20000 })
  await expect(page.getByRole('button', { name: '采用构思', exact: true })).toBeEnabled()
  await expect(page.getByRole('heading', { name: '构思方案', exact: true })).toBeVisible()
  expect(
    (await (await page.request.get(`/api/branches/${novel.branch_id}/author-contract`)).json()).configured,
  ).toBe(false)
})
