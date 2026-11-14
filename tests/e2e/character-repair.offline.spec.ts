import { test, expect, type Page } from '@playwright/test'

const fixtureBrief = 'character-field-repair-e2e：沈砚只用旧尺量潮并留下记录，不增加超自然能力。'
const originalText = '作者保留原稿😀𠮷：这一版仍在渡口等候。'
const unsupportedNote = '工程替身错误备注：沈砚已经掌握飞行能力。'
const groundedNote = '沈砚用旧尺测量潮水，留下可以回查的记录。'

type Step = {
  status: string
  parent_artifact_id: string
  child_artifact_id?: string
  diff?: { record_id: string; field: string; before: string; after: string; source_ids: string[] }[]
  response: { fields: { decision: string }[] }
}
type Chain = { version: string; current_artifact_id: string; rounds: Step[] }
type Task = {
  id: string
  branch_id: string
  status: string
  error: string
  payload: { character_field_repair: boolean; structured_maintenance: boolean; adoption: string }
  result: {
    character_field_repair_version?: string
    character_field_repairs: Record<string, Chain>
    pending_structure?: { artifact_id: string }
    structure_history: { artifact_id: string; policy: string }[]
  }
}
type Artifact = {
  id: string
  status: string
  kind: string
  chapter_id: string
  payload: {
    text?: string
    records: { id: string; record: { notes: string; profile?: string; sources: unknown[] } }[]
    source_manifest: { id: string; revision: number }[]
    character_semantic_review: {
      status: string
      input_sha256: string
      response: { fields: { result: string }[] }
    }
  }
  basis: {
    character_field_repair?: { version: string; parent_artifact_id: string; diff: Step['diff'] }
    character_field_origin?: { version: string; initial_scope?: unknown }
  }
}

async function get<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(path)
  expect(response.ok(), await response.text()).toBeTruthy()
  return response.json()
}

async function startFromUi(page: Page, cannot = false, late = false) {
  await page.goto('/')
  await page.getByText('＋ 新建作品', { exact: true }).click()
  await page
    .getByLabel('作品名称', { exact: true })
    .fill(`后台验证 · 人物字段${cannot ? '不能修订' : '有限修订'}`)
  const create = page.waitForResponse(
    (r) => r.url().endsWith('/api/novels') && r.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '创建并开始写作', exact: true }).click()
  const created = await create
  expect(created.ok(), await created.text()).toBeTruthy()
  const novel: { id: string; branch_id: string } = await created.json()
  await expect(page.getByRole('combobox', { name: '工作版本', exact: true })).toHaveValue(novel.branch_id)
  const chapterResponse = page.waitForResponse(
    (r) => r.url().endsWith(`/api/branches/${novel.branch_id}/chapters`) && r.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '＋ 新章', exact: true }).click()
  const chapter = await (await chapterResponse).json()
  await page.getByRole('textbox', { name: '章节正文', exact: true }).fill(originalText)
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect
    .poll(async () => (await get<{ content: string }>(page, `/api/chapters/${chapter.id}`)).content)
    .toBe(originalText)
  const original = await get<{ content: string; revision: number }>(page, `/api/chapters/${chapter.id}`)

  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  const policy = page.getByRole('checkbox', { name: '允许有限修订未采纳人物字段', exact: true })
  await expect(policy).not.toBeChecked()
  await page.getByRole('radio', { name: /^全自动/ }).check()
  await page.getByRole('combobox', { name: '本次任务范围', exact: true }).selectOption('segment')
  await page
    .getByLabel('作品设定、粗纲与结局方向')
    .fill(
      fixtureBrief + (cannot ? ' fixture-cannot-repair' : '') + (late ? ' fixture-late-character-field' : ''),
    )
  await page.getByLabel('计划章数', { exact: true }).fill('1')
  await page.getByLabel('每章字数下限').fill('200')
  await page.getByLabel('每章字数上限').fill('600')
  await page.getByRole('combobox', { name: '采纳政策', exact: true }).selectOption('automatic')
  await expect(page.getByRole('checkbox', { name: '每章采用后整理人物、时点、事件和伏笔' })).toBeChecked()
  await policy.check()
  const submit = page.waitForResponse(
    (r) => r.url().endsWith('/api/workflows') && r.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '创建独立版本并启动', exact: true }).click()
  const submitted = await submit
  expect(submitted.ok(), await submitted.text()).toBeTruthy()
  const started: Task = await submitted.json()
  expect(started.branch_id).not.toBe(novel.branch_id)
  expect(started.payload).toMatchObject({
    character_field_repair: true,
    structured_maintenance: true,
    adoption: 'automatic',
  })
  await expect
    .poll(async () => (await get<Task>(page, `/api/tasks/${started.id}`)).status, { timeout: 30_000 })
    .toBe(cannot ? 'needs_review' : 'segment_complete')
  await expect(page.getByLabel('本批次人物字段修订政策', { exact: true })).toHaveText(
    '人物字段有限修订：已启用，最多2轮；初稿时确定全部AI新人物非空说明、档案范围，每轮只修当前未获支持项，范围不临时扩大。增加调用费用，保留原候选与修订记录。',
  )
  expect(started.result.character_field_repair_version).toBe('character-field-repair/2')
  return { started, original, originalId: chapter.id }
}

async function extractionPanel(page: Page) {
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  await page.getByText('按保存稿整理人物、事件、状态和伏笔候选', { exact: true }).click()
  return page.getByRole('region', { name: '结构化整理与确认', exact: true })
}

test('人物修订v2：复检新增未知只在原范围内修第二轮，档案与来源不被重写', async ({ page }) => {
  const { started, original, originalId } = await startFromUi(page, false, true)
  const task = await get<Task>(page, `/api/tasks/${started.id}`)
  const entries = Object.entries(task.result.character_field_repairs)
  expect(entries).toHaveLength(1)
  const [rootId, chain] = entries[0]
  expect(chain.version).toBe('character-field-repair/2')
  expect(chain.rounds).toHaveLength(2)
  expect(chain.rounds[0].diff?.map((row) => row.field)).toEqual(['profile'])
  expect(chain.rounds[1].diff?.map((row) => row.field)).toEqual(['notes'])
  const root = await get<Artifact>(page, `/api/artifacts/${rootId}`)
  const first = await get<Artifact>(page, `/api/artifacts/${chain.rounds[0].child_artifact_id}`)
  const current = await get<Artifact>(page, `/api/artifacts/${chain.current_artifact_id}`)
  const scope = root.basis.character_field_origin?.initial_scope
  expect(scope).toBeDefined()
  expect(scope).toMatchObject({
    fields: expect.arrayContaining([
      expect.objectContaining({ record_id: root.payload.records[0].id, field: 'notes' }),
      expect.objectContaining({ record_id: root.payload.records[0].id, field: 'profile' }),
    ]),
  })
  expect(first.basis.character_field_origin?.initial_scope).toEqual(scope)
  expect(current.basis.character_field_origin?.initial_scope).toEqual(scope)
  expect(root.payload.records[0].record.notes).toBe(unsupportedNote)
  expect(first.payload.records[0].record.notes).toBe(unsupportedNote)
  expect(first.payload.records[0].record.profile).toBe(groundedNote)
  expect(current.payload.records[0].record.profile).toBe(first.payload.records[0].record.profile)
  expect(current.payload.records[0].record.notes).toBe(groundedNote)
  expect(current.payload.character_semantic_review.status).toBe('supported')
  expect(current.status).toBe('accepted')
  for (const child of [first, current])
    expect(child.payload.records[0].record.sources).toEqual(root.payload.records[0].record.sources)
  expect(await get(page, `/api/chapters/${originalId}`)).toEqual(original)
  const panel = await extractionPanel(page)
  await panel.getByRole('combobox', { name: '保存的结构候选', exact: true }).selectOption(current.id)
  await expect(
    panel.getByText(
      '可修范围在初稿生成时已确定；本轮仅修改当前未获支持项，不改其他字段。原范围与来源随候选链保留。',
      { exact: true },
    ),
  ).toBeVisible()
  await expect(
    panel.getByText('当前修订候选已采用，整条候选链只读，原候选和每轮记录保留。', { exact: true }),
  ).toBeVisible()
})

test('人物有限修订：真实界面开启、独立复检、原稿及父报告保留，历史父稿只读', async ({ page }, testInfo) => {
  const { started, original, originalId } = await startFromUi(page)
  const task = await get<Task>(page, `/api/tasks/${started.id}`)
  const chains = Object.entries(task.result.character_field_repairs)
  expect(chains).toHaveLength(1)
  const [rootId, chain] = chains[0]
  expect(chain.rounds).toHaveLength(1)
  const round = chain.rounds[0]
  expect(round.status).toBe('candidate_created')
  expect(round.parent_artifact_id).toBe(rootId)
  expect(round.child_artifact_id).toBe(chain.current_artifact_id)
  const parent = await get<Artifact>(page, `/api/artifacts/${rootId}`)
  const child = await get<Artifact>(page, `/api/artifacts/${chain.current_artifact_id}`)
  expect(parent.status).toBe('candidate')
  expect(parent.payload.records[0].record.notes).toBe(unsupportedNote)
  expect(parent.payload.character_semantic_review.status).toBe('needs_review')
  expect(parent.payload.character_semantic_review.response.fields.map((field) => field.result)).toEqual([
    'unknown',
  ])
  expect(child.status).toBe('accepted')
  expect(child.payload.records[0].record.notes).toBe(groundedNote)
  expect(child.payload.character_semantic_review.status).toBe('supported')
  expect(child.payload.character_semantic_review.input_sha256).not.toBe(
    parent.payload.character_semantic_review.input_sha256,
  )
  expect(child.payload.records[0].record.sources).toEqual(parent.payload.records[0].record.sources)
  expect(round.diff).toEqual([
    {
      record_id: parent.payload.records[0].id,
      field: 'notes',
      before: unsupportedNote,
      after: groundedNote,
      source_ids: ['CHR0001'],
      reason: '确定性工程替身：只保留本项来源明确出现的测量与记录，不制造能力。',
    },
  ])
  expect(task.result.pending_structure).toBeUndefined()
  expect(task.result.structure_history[0]).toMatchObject({
    artifact_id: child.id,
    policy: 'worker_automatic',
  })

  const panel = await extractionPanel(page)
  const select = panel.getByRole('combobox', { name: '保存的结构候选', exact: true })
  await select.selectOption(child.id)
  const repair = panel.getByRole('region', { name: '人物字段有限修订', exact: true })
  await expect(repair).toBeVisible()
  await expect(repair).toContainText(unsupportedNote)
  await expect(repair).toContainText(groundedNote)
  await page.setViewportSize({ width: 320, height: 900 })
  await page.evaluate(() => {
    document.documentElement.style.fontSize = '200%'
  })
  await expect(repair).toBeVisible()
  expect(await repair.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
  const viewParent = repair.getByRole('button', { name: '查看原候选', exact: true })
  await viewParent.focus()
  await expect(viewParent).toBeFocused()
  await page.screenshot({
    path: testInfo.outputPath('character-repair-320px-200pct-keyboard.png'),
    fullPage: true,
  })
  await page.keyboard.press('Enter')
  await expect(select).toHaveValue(parent.id)
  await expect(panel.getByRole('button', { name: '刷新候选与复核报告', exact: true })).toBeEnabled()
  await expect(
    panel.getByText('当前修订候选已采用，整条候选链只读，原候选和每轮记录保留。', { exact: true }),
  ).toBeVisible()
  await expect(panel.getByRole('checkbox', { name: '采用人物：沈砚', exact: true })).toBeDisabled()
  await expect(
    panel.getByRole('button', { name: '确认采用所选结构（不修改正文）', exact: true }),
  ).toBeDisabled()
  expect(await get<Artifact>(page, `/api/artifacts/${parent.id}`)).toEqual(parent)
  expect(await get(page, `/api/chapters/${originalId}`)).toMatchObject(original)
  const artifacts = await get<Artifact[]>(page, `/api/artifacts?branch_id=${started.branch_id}`)
  const draft = artifacts.find((artifact) => artifact.kind === 'chapter_draft')
  expect(draft).toBeDefined()
  expect(await get(page, `/api/chapters/${draft!.chapter_id}`)).toMatchObject({
    content: draft!.payload.text,
    revision: 1,
  })
  await testInfo.attach('character-repair-chain.json', {
    body: JSON.stringify({ task, parent, child }, null, 2),
    contentType: 'application/json',
  })
})

test('人物字段不能修订：无子稿的当前父候选保留人工采用入口与unknown报告', async ({ page }, testInfo) => {
  const { started, original, originalId } = await startFromUi(page, true)
  const task = await get<Task>(page, `/api/tasks/${started.id}`)
  const [rootId, chain] = Object.entries(task.result.character_field_repairs)[0]
  expect(chain.rounds).toHaveLength(1)
  expect(chain.rounds[0].status).toBe('stopped')
  expect(chain.rounds[0].child_artifact_id).toBeUndefined()
  expect(chain.rounds[0].response.fields[0].decision).toBe('cannot_repair')
  expect(chain.current_artifact_id).toBe(rootId)
  expect(task.result.pending_structure?.artifact_id).toBe(rootId)
  const parent = await get<Artifact>(page, `/api/artifacts/${rootId}`)
  const panel = await extractionPanel(page)
  await panel.getByRole('combobox', { name: '保存的结构候选', exact: true }).selectOption(rootId)
  const choose = panel.getByRole('checkbox', { name: '采用人物：沈砚', exact: true })
  await expect(choose).toBeEnabled()
  await choose.check()
  // Scene ranges are also maintained by the real workflow and must be selected explicitly.
  for (const box of await panel.locator('article input[type=checkbox]').all()) await box.check()
  const adopt = panel.getByRole('button', { name: '确认采用所选结构（不修改正文）', exact: true })
  await expect(adopt).toBeEnabled()
  await adopt.click()
  await expect(
    panel.getByText('所选结构与场景已采用，旧绑定历史保留，正文未改变。', { exact: true }),
  ).toBeVisible()
  const after = await get<Artifact>(page, `/api/artifacts/${rootId}`)
  expect(after.status).toBe('accepted')
  expect(after.payload.character_semantic_review).toEqual(parent.payload.character_semantic_review)
  expect(after.payload.records[0].record.notes).toBe(unsupportedNote)
  expect(await get(page, `/api/chapters/${originalId}`)).toMatchObject(original)
  const updated = await get<Task>(page, `/api/tasks/${started.id}`)
  expect(updated.result.structure_history[0]).toMatchObject({
    artifact_id: parent.id,
    policy: 'author_confirmed',
  })
  await testInfo.attach('cannot-repair-current-parent.json', {
    body: JSON.stringify({ task: updated, parent: after }, null, 2),
    contentType: 'application/json',
  })
})
