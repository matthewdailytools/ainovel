import { test, expect, type Page, type TestInfo } from '@playwright/test'
import { writeFile } from 'node:fs/promises'

const IDEA = '我想写一个女修城器师靠借尺检查闸门追查旧账的故事，不修门，不战斗。'
const ORIGINAL = '作者原稿：她来到闸门前，先观察公开刻度。😀𠮷'
const DIRECTION = '今天只借旧尺复测并记录读数，查账的具体路径留待以后决定。'
const FIXED = ['本章只留下读数', '不修门', '不战斗', '不查看私藏账簿']

async function runAuthorTask(page: Page, testInfo: TestInfo, withDesigns: boolean) {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await (
    await page.request.post('/api/novels', {
      headers,
      data: { title: `后台验证 · 一句想法到本章正文 · ${withDesigns ? '按需设定' : '跳过规划'}` },
    })
  ).json()
  const chapter = await (
    await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
      headers,
      data: { title: '借尺检查章' },
    })
  ).json()
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  const editor = page.getByRole('textbox', { name: '章节正文', exact: true })
  await editor.fill(ORIGINAL)
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect
    .poll(async () => (await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).revision)
    .toBe(1)

  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  const intent = page.locator('#author-intent')
  await intent.getByText('编写或整理创作约定（确认后生效）', { exact: true }).click()
  await intent.getByRole('textbox', { name: '作者原话与创作想法', exact: true }).fill(IDEA)
  const compileResponse = page.waitForResponse(
    (r) => r.url().endsWith('/api/modules') && r.request().method() === 'POST',
  )
  await intent.getByRole('button', { name: '用当前模型整理为待确认提案', exact: true }).click()
  const compile = await (await compileResponse).json()
  await expect(intent.getByRole('heading', { name: 'AI 整理提案 · 尚未采用', exact: true })).toBeVisible()
  await expect(intent).toContainText('含 AI 推导，待确认')
  const compiled = await (await page.request.get(`/api/tasks/${compile.id}`)).json()
  expect(compiled.status).toBe('succeeded')
  expect(compiled.payload.action).toBe('intent.compile')
  expect(compiled.payload.instruction).toBe(IDEA)
  const proposalId = compiled.result.artifact_id
  expect(
    (await (await page.request.get(`/api/branches/${novel.branch_id}/author-contract`)).json()).configured,
  ).toBe(false)
  expect((await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content).toBe(ORIGINAL)

  await intent.getByRole('button', { name: '载入表单核对与编辑', exact: true }).click()
  await expect(
    intent.getByRole('textbox', { name: '禁止改变（每行一项，可留空）', exact: true }),
  ).toHaveValue('不修门\n不战斗')
  await intent.getByRole('textbox', { name: '故事方向（可留空）', exact: true }).fill(DIRECTION)
  await intent.getByRole('textbox', { name: '锁定结果（每行一项，可留空）', exact: true }).fill(FIXED[0])
  await intent
    .getByRole('textbox', { name: '禁止改变（每行一项，可留空）', exact: true })
    .fill(FIXED.slice(1).join('\n'))
  await expect(intent.getByRole('textbox', { name: '终局倾向（可留空）', exact: true })).toHaveValue('')
  const adoptionRequest = page.waitForRequest(
    (r) => r.url().endsWith(`/branches/${novel.branch_id}/definitions`) && r.method() === 'POST',
  )
  await intent.getByRole('button', { name: '确认并采用编辑后的创作约定', exact: true }).click()
  expect((await adoptionRequest).postDataJSON().intent_source_candidate_id).toBe(proposalId)
  await expect(intent).toContainText('已确认并采用。后续新任务继承本约定')
  const contract = await (await page.request.get(`/api/branches/${novel.branch_id}/author-contract`)).json()
  expect(contract.author_text).toBe(IDEA)
  expect(contract.intent_contract.direction).toBe(DIRECTION)
  expect(contract.intent_contract.fixed_results).toEqual([FIXED[0]])
  expect(contract.intent_contract.prohibitions).toEqual(FIXED.slice(1))
  expect(contract.intent_contract.ending).toBe('')
  expect(contract.provenance.source_candidate_id).toBe(proposalId)
  expect(contract.provenance.field_sources.direction.origin).toBe('author_edited')
  expect(contract.provenance.field_sources.emotion_target.origin).toBe('author_confirmed_proposal')

  await page.getByRole('button', { name: '写作', exact: true }).click()
  const action = page.getByRole('combobox', { name: '这次要做什么', exact: true })
  const instruction = page.getByRole('textbox', { name: '情节或具体要求', exact: true })
  await page.getByText('固定条件、写法与资料包', { exact: true }).click()
  const fixed = page.getByRole('textbox', { name: '必须保持的条件（每行一条）', exact: true })
  await fixed.fill(FIXED.join('\n'))
  const comparison = page.getByRole('combobox', { name: '比较候选', exact: true })
  const taskIds = [compile.id]
  const artifacts: { id: string; action: string; kind: string; status: string }[] = []
  const bookDefinitionIds: string[] = []
  let localDefinitionId: string | undefined
  async function generate(module: string, request: string, kind: string, scope: 'book' | 'chapter' = 'book') {
    await action.selectOption(module)
    const isDesign = ['world.generate', 'character.generate', 'outline.plan'].includes(module)
    if (isDesign) {
      const scopeField = page.getByRole('combobox', { name: '设定作用范围', exact: true })
      if (!bookDefinitionIds.length) await expect(scopeField).toHaveValue('book')
      await scopeField.selectOption(scope)
    }
    await instruction.fill(request)
    await fixed.fill(FIXED.join('\n'))
    const response = page.waitForResponse(
      (r) => r.url().endsWith('/api/modules') && r.request().method() === 'POST',
    )
    await page.getByRole('button', { name: '生成候选 →', exact: true }).click()
    const posted = await response
    expect(posted.status()).toBe(202)
    const payload = posted.request().postDataJSON()
    expect(payload.chapter_id).toBe(isDesign && scope === 'book' ? null : chapter.id)
    expect(payload.base_revision).toBe(isDesign && scope === 'book' ? null : 1)
    expect(payload.author_contract_basis.source_artifact_id).toBe(contract.source_artifact_id)
    expect(payload.fixed).toEqual(FIXED)
    const started = await posted.json()
    taskIds.push(started.id)
    await expect
      .poll(async () => (await (await page.request.get(`/api/tasks/${started.id}`)).json()).status)
      .toBe('succeeded')
    const finished = await (await page.request.get(`/api/tasks/${started.id}`)).json()
    const artifact = await (await page.request.get(`/api/artifacts/${finished.result.artifact_id}`)).json()
    expect(artifact.kind).toBe(kind)
    expect(artifact.basis.author_contract.source_artifact_id).toBe(contract.source_artifact_id)
    expect(artifact.basis.author_contract.intent_contract.direction).toBe(DIRECTION)
    expect(artifact.basis.author_contract.intent_contract.prohibitions).toEqual(FIXED.slice(1))
    await expect(comparison.locator(`option[value="${artifact.id}"]`)).toBeAttached()
    await comparison.selectOption(artifact.id)
    await expect(page.getByRole('textbox', { name: '候选内容', exact: true })).toHaveValue(
      artifact.payload.text,
    )
    expect((await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content).toBe(ORIGINAL)
    artifacts.push({ id: artifact.id, action: module, kind, status: artifact.status })
    return artifact
  }
  // Missing world/characters/outline never gates a direct writing request.
  await action.selectOption('writing.expand_plot')
  await instruction.fill(DIRECTION)
  await expect(page.getByRole('button', { name: '生成候选 →', exact: true })).toBeEnabled()
  if (withDesigns) {
    for (const [module, request, kind] of [
      ['world.generate', '只补足闸门公开刻度和检查职责，不补完整世界百科。', 'world'],
      ['character.generate', '只设计本章借尺检查者的名字与已具备能力，不新增修门能力。', 'characters'],
      ['outline.plan', '只给本章借尺复测粗纲，后续与终局保持开放。', 'outline'],
    ]) {
      const artifact = await generate(module, request, kind)
      expect(artifact.chapter_id).toBeNull()
      await expect(page.locator('.candidate')).toContainText('本书设定：作者采用后')
      await page.getByRole('button', { name: '采用为本书设定', exact: true }).click()
      await expect
        .poll(async () => (await (await page.request.get(`/api/artifacts/${artifact.id}`)).json()).status)
        .toBe('accepted')
      expect((await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).revision).toBe(1)
      await expect(page.getByRole('button', { name: '保存', exact: true })).toBeEnabled()
      bookDefinitionIds.push(artifact.id)
      const afterAdoption = await (await page.request.get(`/api/branches/${novel.branch_id}/context`)).json()
      expect(afterAdoption.definitions.some((row: { id: string }) => row.id === artifact.id)).toBe(true)
    }
    const scopeField = page.getByRole('combobox', { name: '设定作用范围', exact: true })
    await scopeField.selectOption('chapter')
    await page.getByRole('button', { name: '恢复最近任务的输入', exact: true }).click()
    await expect(scopeField).toHaveValue('book')
    const local = await generate(
      'world.generate',
      '只留本章测尺资料，不替换本书世界设定。',
      'world',
      'chapter',
    )
    localDefinitionId = local.id
    await expect(page.locator('.candidate')).toContainText('本章资料：采用只保存为本章参考')
    await page.getByRole('button', { name: '保存为本章参考', exact: true }).click()
    await expect
      .poll(async () => (await (await page.request.get(`/api/artifacts/${local.id}`)).json()).status)
      .toBe('accepted')
    await expect(page.getByRole('button', { name: '保存', exact: true })).toBeEnabled()
    const afterLocal = await (await page.request.get(`/api/branches/${novel.branch_id}/context`)).json()
    expect(afterLocal.definitions.some((row: { id: string }) => row.id === local.id)).toBe(false)
    expect(
      afterLocal.definitions.filter((row: { id: string }) => bookDefinitionIds.includes(row.id)),
    ).toHaveLength(3)
    await scopeField.selectOption('book')
    await page.getByRole('button', { name: '恢复最近任务的输入', exact: true }).click()
    await expect(scopeField).toHaveValue('chapter')
    await page.setViewportSize({ width: 320, height: 800 })
    await page.evaluate(() => {
      document.documentElement.style.fontSize = '200%'
    })
    await scopeField.focus()
    await expect(scopeField).toBeFocused()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    await page.evaluate(() => {
      document.documentElement.style.fontSize = ''
    })
    await page.setViewportSize({ width: 1280, height: 900 })
  }
  const draft = await generate('writing.expand_plot', DIRECTION, 'chapter_draft')
  const eventText = await (await page.request.get(`/api/tasks/${draft.task_id}/events`)).text()
  const eventRows = eventText
    .split('\n')
    .filter((line) => line.startsWith('data: '))
    .map((line) => JSON.parse(line.slice(6)))
  const writingDispatch = eventRows.find(
    (row) => row.kind === 'step_dispatched' && row.payload.action === 'writing.expand_plot',
  )
  const dispatchedDefinitionIds = writingDispatch.payload.request.source_versions.definitions.map(
    (row: { id: string }) => row.id,
  )
  for (const id of bookDefinitionIds) expect(dispatchedDefinitionIds).toContain(id)
  if (localDefinitionId) expect(dispatchedDefinitionIds).not.toContain(localDefinitionId)
  expect(draft.payload.review_passed).toBe(true)
  const checks = draft.payload.review.checks.map((check: { condition: string }) => check.condition)
  for (const condition of FIXED) expect(checks).toContain(condition)
  await page.getByText('查看本章检查依据', { exact: true }).click()
  await expect(page.locator('.candidate')).toContainText('不修门')
  await expect(page.locator('.candidate')).toContainText('不查看私藏账簿')
  await page.getByRole('button', { name: '拒绝', exact: true }).click()
  await expect
    .poll(async () => (await (await page.request.get(`/api/artifacts/${draft.id}`)).json()).status)
    .toBe('rejected')
  expect((await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content).toBe(ORIGINAL)
  const accepted = await generate('writing.expand_plot', DIRECTION, 'chapter_draft')
  expect(accepted.id).not.toBe(draft.id)
  await page.getByRole('button', { name: '采纳到正文', exact: true }).click()
  await expect
    .poll(async () => (await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content)
    .toBe(accepted.payload.text)
  await expect(editor).toContainText(accepted.payload.text)
  await expect(page.getByRole('button', { name: '保存', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: '版本历史', exact: true }).click()
  const previous = page.locator('.history details').filter({ hasText: ORIGINAL })
  await previous.locator('summary').click()
  await previous.getByRole('button', { name: '恢复为新修订', exact: true }).click()
  await expect
    .poll(async () => (await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content)
    .toBe(ORIGINAL)
  const restored = await (await page.request.get(`/api/chapters/${chapter.id}`)).json()
  expect(restored.revision).toBe(3)
  expect(
    (await (await page.request.get(`/api/branches/${novel.branch_id}/author-contract`)).json())
      .intent_contract,
  ).toEqual(contract.intent_contract)
  const context = await (await page.request.get(`/api/branches/${novel.branch_id}/context`)).json()
  const designKinds = context.definitions
    .map((definition: { kind: string }) => definition.kind)
    .filter((kind: string) => ['world', 'characters', 'outline'].includes(kind))
  expect(designKinds.sort()).toEqual(withDesigns ? ['characters', 'outline', 'world'] : [])
  expect(context.facts).toEqual([])
  const tasks = await (await page.request.get(`/api/tasks?branch_id=${novel.branch_id}`)).json()
  expect(tasks).toHaveLength(withDesigns ? 7 : 3)
  expect(tasks.every((row: { status: string }) => row.status === 'succeeded')).toBe(true)
  const summaryPath = testInfo.outputPath('u02-engineering-summary.json')
  await writeFile(
    summaryPath,
    JSON.stringify(
      {
        novel_id: novel.id,
        branch_id: novel.branch_id,
        chapter_id: chapter.id,
        proposal_id: proposalId,
        author_contract_id: contract.source_artifact_id,
        task_ids: taskIds,
        artifacts_at_generation: artifacts,
        whole_book_definition_ids: bookDefinitionIds,
        chapter_only_definition_id: localDefinitionId,
        dispatched_whole_book_definition_ids: dispatchedDefinitionIds,
        direct_design_scope_available: true,
        restored_revision: restored.revision,
        original_restored: restored.content === ORIGINAL,
        optional_design_kinds: designKinds,
        mode: 'offline deterministic model; no literary or paid-model validation',
      },
      null,
      2,
    ),
    'utf8',
  )
  const screenshotPath = testInfo.outputPath('restored-workbench.png')
  await page.screenshot({ fullPage: true, path: screenshotPath })
  await testInfo.attach('U02工程证据摘要', { contentType: 'application/json', path: summaryPath })
  await testInfo.attach('作者恢复原稿后的工作台', { contentType: 'image/png', path: screenshotPath })
}

test('U02完整任务：一句想法、AI提案、作者修改确认、按需世界人物粗纲、补写与恢复', async ({ page }, info) => {
  await runAuthorTask(page, info, true)
})

test('U02可选规划：确认一句想法后跳过全部世界人物粗纲，直接补写并恢复', async ({ page }, info) => {
  await runAuthorTask(page, info, false)
})

test('U02未建章节：本章范围明确阻止，本书设定仍可生成采用且不创建章节或事实', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const novel = await (
    await page.request.post('/api/novels', {
      headers: { 'X-Ainovel-CSRF': session.csrf },
      data: { title: '后台验证 · 未建章节的本书设定' },
    })
  ).json()
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await expect(page.getByRole('button', { name: '新建第一章', exact: true })).toBeVisible()
  await page.getByRole('combobox', { name: '这次要做什么', exact: true }).selectOption('world.generate')
  const scope = page.getByRole('combobox', { name: '设定作用范围', exact: true })
  await expect(scope).toHaveValue('book')
  await page
    .getByRole('textbox', { name: '情节或具体要求', exact: true })
    .fill('只设计闸门检查职责，不先建章节或完整百科。')
  await scope.selectOption('chapter')
  await page.getByRole('button', { name: '生成候选 →', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('本章资料需要先打开目标章节')
  expect(await (await page.request.get(`/api/tasks?branch_id=${novel.branch_id}`)).json()).toEqual([])
  await scope.selectOption('book')
  const response = page.waitForResponse(
    (r) => r.url().endsWith('/api/modules') && r.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '生成候选 →', exact: true }).click()
  const posted = await response
  expect(posted.request().postDataJSON().chapter_id).toBeNull()
  expect(posted.request().postDataJSON().base_revision).toBeNull()
  const task = await posted.json()
  await expect
    .poll(async () => (await (await page.request.get(`/api/tasks/${task.id}`)).json()).status)
    .toBe('succeeded')
  const finished = await (await page.request.get(`/api/tasks/${task.id}`)).json()
  await expect(page.getByRole('button', { name: '采用为本书设定', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: '采用为本书设定', exact: true }).click()
  await expect
    .poll(
      async () =>
        (await (await page.request.get(`/api/artifacts/${finished.result.artifact_id}`)).json()).status,
    )
    .toBe('accepted')
  expect(await (await page.request.get(`/api/branches/${novel.branch_id}/chapters`)).json()).toEqual([])
  const context = await (await page.request.get(`/api/branches/${novel.branch_id}/context`)).json()
  expect(context.definitions.some((row: { id: string }) => row.id === finished.result.artifact_id)).toBe(true)
  expect(context.facts).toEqual([])
})
