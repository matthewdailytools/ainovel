import { test, expect, type Page } from '@playwright/test'
import { createHash } from 'node:crypto'

const instruction = '她检查闸门，借旧尺复测并记下读数，不修门。'
const originalText = '她检查闸门，不修门。😀𠮷'
const uncertainPlot = 'plot-check-e2e-unknown：沈砚用旧尺测量潮水并记录，是否公开读数尚未确定。'
const exclusivePlot = 'plot-scope-exclusive-e2e：本章中，沈砚把潮水读数只告诉师傅，并让师傅记下。'
const sha = (text: string) => createHash('sha256').update(text).digest('hex')

type Audit = {
  version: string
  status: string
  source: { kind: string; text_sha256: string }
  candidate_sha256: string
  candidate_revision: number
  target?: { kind: string; chapter_id: string; revision: number; content_sha256: string }
  items: { author_evidence: string; certainty: string; recipient_scope?: string; scope_condition?: string }[]
  checks: {
    result: string
    evidence: string
    reason?: string
    original_model_check?: { result: string; evidence: string }
    guard_override?: { code: string; expected_recipient: string; observations: { recipient_id: string }[] }
  }[]
  findings?: unknown[]
  direction_guard?: {
    flows: { object_id: string; supplier_id: string; recipient_id: string; stage: string; evidence: string }[]
  }
}
type Artifact = {
  id: string
  kind: string
  status: string
  candidate_revision: number
  payload: {
    text?: string
    plot_audit?: Audit
    previous_plot_audit?: Audit
    review_passed?: boolean
    memory?: object
  }
}
type Task = {
  id: string
  branch_id: string
  status: string
  payload: { action?: string; plot_contract_check: boolean; plots?: { plot: string }[] }
  result: {
    artifact_id?: string
    waiting_artifact?: string
    next_chapter?: number
    pending_structure?: object
    plot_contract_version?: string
  }
}

async function read<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(path)
  expect(response.ok(), await response.text()).toBeTruthy()
  return response.json()
}

async function createFromUi(page: Page, title: string) {
  await page.goto('/')
  await page.getByText('＋ 新建作品', { exact: true }).click()
  await page.getByLabel('作品名称', { exact: true }).fill(title)
  const novelResponse = page.waitForResponse(
    (r) => r.url().endsWith('/api/novels') && r.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '创建并开始写作', exact: true }).click()
  const novel: { id: string; branch_id: string } = await (await novelResponse).json()
  await expect(page.getByRole('combobox', { name: '工作版本', exact: true })).toHaveValue(novel.branch_id)
  const chapterResponse = page.waitForResponse(
    (r) => r.url().endsWith(`/api/branches/${novel.branch_id}/chapters`) && r.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '＋ 新章', exact: true }).click()
  const chapter: { id: string } = await (await chapterResponse).json()
  await page.getByRole('textbox', { name: '章节正文', exact: true }).fill(originalText)
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect
    .poll(async () => (await read<{ revision: number }>(page, `/api/chapters/${chapter.id}`)).revision)
    .toBe(1)
  const original = await read<{ content: string; revision: number }>(page, `/api/chapters/${chapter.id}`)
  return { novel, chapter, original }
}

async function moduleFromUi(page: Page, action: string) {
  await page.getByRole('combobox', { name: '这次要做什么', exact: true }).selectOption(action)
  await page.getByLabel('情节或具体要求', { exact: true }).fill(instruction)
  const option = page.getByRole('checkbox', { name: '额外逐项核对本次情节', exact: true })
  await expect(option).not.toBeChecked()
  await option.check()
  const response = page.waitForResponse(
    (r) => r.url().endsWith('/api/modules') && r.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '生成候选 →', exact: true }).click()
  const submitted = await response
  expect(submitted.ok(), await submitted.text()).toBeTruthy()
  const task: Task = await submitted.json()
  expect(task.payload.action).toBe(action)
  expect(task.payload.plot_contract_check).toBe(true)
  await expect.poll(async () => (await read<Task>(page, `/api/tasks/${task.id}`)).status).toBe('succeeded')
  const finished = await read<Task>(page, `/api/tasks/${task.id}`)
  expect(finished.result.plot_contract_version).toBe('author-plot-audit/2')
  const artifact = await read<Artifact>(page, `/api/artifacts/${finished.result.artifact_id}`)
  expect(artifact.payload.plot_audit?.version).toBe(finished.result.plot_contract_version)
  const select = page.getByRole('combobox', { name: '比较候选', exact: true })
  await expect(select.locator(`option[value="${artifact.id}"]`)).toBeAttached()
  await select.selectOption(artifact.id)
  return { task: finished, artifact }
}

test('普通补写：显式情节核对展示原话、AI解释和正文证据；编辑失效不改原稿', async ({ page }, testInfo) => {
  const { chapter, original } = await createFromUi(page, '后台验证 · 普通补写情节核对')
  const { artifact, task } = await moduleFromUi(page, 'writing.expand_plot')
  expect(task.result.plot_contract_version).toBe('author-plot-audit/2')
  const audit = artifact.payload.plot_audit!
  expect(audit).toMatchObject({
    version: task.result.plot_contract_version,
    status: 'model_pass',
    source: { kind: 'author_instruction', text_sha256: sha(instruction) },
    candidate_revision: 0,
    candidate_sha256: sha(artifact.payload.text!),
  })
  expect(audit.items[0].author_evidence).toBe(instruction)
  expect(audit.items[0]).toMatchObject({ recipient_scope: 'unspecified', scope_condition: '' })
  expect(artifact.payload.text).toContain(audit.checks[0].evidence)
  const panel = page.getByRole('region', { name: '情节逐项核对', exact: true })
  await panel.getByText('情节逐项核对 · 模型标一致，仍须作者核对', { exact: true }).click()
  await expect(panel.getByText('本次原始指令摘录', { exact: true })).toBeVisible()
  await expect(panel.getByText(instruction, { exact: true })).toBeVisible()
  await expect(panel.getByText('P01 · AI解释（仍须核对）', { exact: true })).toBeVisible()
  await expect(panel.getByText('本稿观察：保留测量，没有修门', { exact: true })).toBeVisible()
  await expect(panel.getByText(audit.checks[0].evidence, { exact: true })).toBeVisible()
  const candidateEditor = page.getByRole('textbox', { name: '候选内容', exact: true })
  await expect(candidateEditor).toBeEditable()
  await candidateEditor.fill(artifact.payload.text! + '\n作者仅编辑未采纳候选。')
  await expect(
    panel.getByText('情节逐项核对 · 历史报告已失效，不能作为当前稿结论', { exact: true }),
  ).toBeVisible()
  await page.getByRole('button', { name: '保存候选编辑（原检查失效）', exact: true }).click()
  await expect
    .poll(async () => (await read<Artifact>(page, `/api/artifacts/${artifact.id}`)).candidate_revision)
    .toBe(1)
  const edited = await read<Artifact>(page, `/api/artifacts/${artifact.id}`)
  expect(edited.payload.plot_audit).toBeUndefined()
  expect(edited.payload.previous_plot_audit).toEqual(audit)
  expect(edited.payload.review_passed).toBe(false)
  expect(await read(page, `/api/chapters/${chapter.id}`)).toEqual(original)
  await expect(
    panel.getByText('情节逐项核对 · 历史报告已失效，不能作为当前稿结论', { exact: true }),
  ).toBeVisible()
  await testInfo.attach('plot-check-edited-history.json', {
    body: JSON.stringify({ before: artifact, after: edited }, null, 2),
    contentType: 'application/json',
  })
})

test('保存稿审查：显式情节核对绑定当前章、修订、内容哈希，原稿保持', async ({ page }, testInfo) => {
  const { chapter, original } = await createFromUi(page, '后台验证 · 保存稿情节核对')
  const { artifact } = await moduleFromUi(page, 'review.chapter')
  expect(artifact.kind).toBe('review')
  const audit = artifact.payload.plot_audit!
  expect(audit.source).toEqual({ kind: 'author_instruction', text_sha256: sha(instruction) })
  expect(audit.target).toEqual({
    kind: 'saved_chapter',
    chapter_id: chapter.id,
    revision: 1,
    content_sha256: sha(originalText),
  })
  expect(audit.candidate_sha256).toBe(sha(originalText))
  const panel = page.getByRole('region', { name: '情节逐项核对', exact: true })
  await panel.getByText('情节逐项核对 · 模型标一致，仍须作者核对', { exact: true }).click()
  await expect(panel).toContainText(`检查对象：保存稿 ${chapter.id} · 修订1`)
  await expect(panel.getByText(originalText, { exact: true })).toBeVisible()
  expect(await read(page, `/api/chapters/${chapter.id}`)).toEqual(original)
  await testInfo.attach('saved-chapter-plot-binding.json', {
    body: JSON.stringify(artifact, null, 2),
    contentType: 'application/json',
  })
})

test('半自动情节核对：冻结author_plot且歧义未知停供，不自动采纳或生成后章', async ({ page }, testInfo) => {
  const { novel, chapter, original } = await createFromUi(page, '后台验证 · 半自动情节未知')
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  await page.getByRole('radio', { name: /^半自动/ }).check()
  await page.getByRole('combobox', { name: '本次任务范围', exact: true }).selectOption('segment')
  await page.getByLabel('作品设定、粗纲与结局方向').fill('先测量潮水并记录，后续仍待查证。')
  await page.getByLabel('计划章数', { exact: true }).fill('2')
  await page.getByLabel('每章字数下限').fill('200')
  await page.getByLabel('每章字数上限').fill('600')
  await page
    .getByLabel('作者批量情节（每行一章）')
    .fill(`量潮 | ${uncertainPlot}\n后章 | 继续核对记录，不提前公开`)
  await page.getByRole('combobox', { name: '采纳政策', exact: true }).selectOption('automatic')
  const option = page.getByRole('checkbox', { name: '额外逐项核对本次情节', exact: true })
  await expect(option).not.toBeChecked()
  await option.check()
  const response = page.waitForResponse(
    (r) => r.url().endsWith('/api/workflows') && r.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '创建独立版本并启动', exact: true }).click()
  const submitted = await response
  expect(submitted.ok(), await submitted.text()).toBeTruthy()
  const started: Task = await submitted.json()
  expect(started.branch_id).not.toBe(novel.branch_id)
  expect(started.payload.plot_contract_check).toBe(true)
  expect(started.payload.plots?.[0].plot).toBe(uncertainPlot)
  await expect
    .poll(async () => (await read<Task>(page, `/api/tasks/${started.id}`)).status)
    .toBe('needs_review')
  const task = await read<Task>(page, `/api/tasks/${started.id}`)
  expect(task.result.next_chapter).toBe(1)
  expect(task.result.pending_structure).toBeUndefined()
  const candidate = await read<Artifact>(page, `/api/artifacts/${task.result.waiting_artifact}`)
  expect(candidate.status).toBe('candidate')
  expect(candidate.payload.review_passed).toBe(false)
  expect(candidate.payload.memory).toEqual({})
  const audit = candidate.payload.plot_audit!
  expect(audit.status).toBe('unverified')
  expect(task.result.plot_contract_version).toBe('author-plot-audit/2')
  expect(audit.version).toBe(task.result.plot_contract_version)
  expect(audit.source).toEqual({ kind: 'author_plot', text_sha256: sha(uncertainPlot) })
  expect(audit.items[0]).toMatchObject({ author_evidence: uncertainPlot, certainty: 'ambiguous' })
  expect(audit.checks[0].result).toBe('unknown')
  await expect(
    page.getByText(/本任务冻结的情节逐项核对：已开启（额外模型费用）。核对来源是作者提供的本章情节。/),
  ).toBeVisible()
  const panel = page.getByRole('region', { name: '情节逐项核对', exact: true })
  await panel.getByText('情节逐项核对 · 待核对（含歧义或未知）', { exact: true }).click()
  await expect(panel.getByText('作者情节摘录', { exact: true })).toBeVisible()
  await expect(panel.getByText(uncertainPlot, { exact: true })).toBeVisible()
  await expect(panel.getByText('P01 · AI解释（存在歧义）', { exact: true })).toBeVisible()
  await expect(panel).toContainText('未能核实：作者情节保留公开安排的歧义，不升级为硬条件')
  const chapters = await read<{ id: string }[]>(page, `/api/branches/${started.branch_id}/chapters`)
  expect(chapters).toHaveLength(1)
  expect(await read(page, `/api/chapters/${chapters[0].id}`)).toMatchObject({ content: '', revision: 0 })
  expect(await read(page, `/api/branches/${started.branch_id}/facts`)).toEqual([])
  expect(await read(page, `/api/chapters/${chapter.id}`)).toEqual(original)
  const transcript = await read<{ steps: { result: { text: string } }[] }>(
    page,
    `/api/tasks/${started.id}/transcript`,
  )
  expect(transcript.steps).toHaveLength(4) // draft, original chapter review, author-only parse, comparison; no forced repair/memory/next chapter.
  await testInfo.attach('assisted-unknown-plot-policy.json', {
    body: JSON.stringify({ task, candidate, transcript }, null, 2),
    contentType: 'application/json',
  })
})

test('v2仅向守卫：正向与unknown接收者并存保留原pass并降未知，不自动采纳或猜身份', async ({
  page,
}, testInfo) => {
  const { novel, chapter, original } = await createFromUi(page, '后台验证 · v2限定接收者')
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  await page.getByRole('radio', { name: /^半自动/ }).check()
  await page.getByRole('combobox', { name: '本次任务范围', exact: true }).selectOption('segment')
  await page.getByLabel('作品设定、粗纲与结局方向').fill('以铺子里交付读数为本批起点，后续调查尚未完成。')
  await page.getByLabel('计划章数', { exact: true }).fill('2')
  await page.getByLabel('每章字数下限').fill('200')
  await page.getByLabel('每章字数上限').fill('600')
  await page.getByLabel('作者批量情节（每行一章）').fill(`交付 | ${exclusivePlot}\n以后 | 继续核对来源`)
  await page.getByRole('combobox', { name: '采纳政策', exact: true }).selectOption('automatic')
  await page.getByRole('checkbox', { name: '额外逐项核对本次情节', exact: true }).check()
  const response = page.waitForResponse(
    (r) => r.url().endsWith('/api/workflows') && r.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '创建独立版本并启动', exact: true }).click()
  const submitted = await response
  expect(submitted.ok(), await submitted.text()).toBeTruthy()
  const started: Task = await submitted.json()
  expect(started.branch_id).not.toBe(novel.branch_id)
  expect(started.result.plot_contract_version).toBe('author-plot-audit/2')
  await expect
    .poll(async () => (await read<Task>(page, `/api/tasks/${started.id}`)).status)
    .toBe('needs_review')
  const task = await read<Task>(page, `/api/tasks/${started.id}`)
  expect(task.result.next_chapter).toBe(1)
  const candidate = await read<Artifact>(page, `/api/artifacts/${task.result.waiting_artifact}`)
  const audit = candidate.payload.plot_audit!
  expect(audit.version).toBe('author-plot-audit/2')
  expect(audit.status).toBe('unverified')
  expect(audit.items[0]).toMatchObject({
    author_evidence: exclusivePlot,
    certainty: 'explicit',
    recipient_scope: 'only',
    scope_condition: '本章中',
  })
  expect(audit.checks[0].result).toBe('unknown')
  expect(audit.checks[0].original_model_check?.result).toBe('pass')
  expect(audit.checks[0].guard_override).toMatchObject({
    code: 'restricted_recipient_unverified',
    expected_recipient: '师傅',
    observations: [{ recipient_id: 'unknown' }],
  })
  expect(audit.findings).toEqual([])
  const flows = audit.direction_guard!.flows
  expect(flows).toHaveLength(2)
  expect(new Set(flows.map((flow) => flow.object_id)).size).toBe(1)
  expect(new Set(flows.map((flow) => flow.supplier_id)).size).toBe(1)
  expect(flows[0].recipient_id).not.toBe('unknown')
  expect(flows[1].recipient_id).toBe('unknown')
  expect(flows.every((flow) => flow.stage === 'completed')).toBe(true)
  expect(candidate.payload.text).toContain(flows[0].evidence)
  expect(candidate.payload.text).toContain(flows[1].evidence)
  expect(flows[0].evidence).toContain('完整告诉师傅')
  expect(flows[1].evidence).toContain('完整告诉门边另一个人')
  expect(audit.checks[0].evidence).toBe(flows[1].evidence)
  expect(candidate.status).toBe('candidate')
  expect(candidate.payload.review_passed).toBe(false)
  expect(candidate.payload.memory).toEqual({})
  const panel = page.getByRole('region', { name: '情节逐项核对', exact: true })
  await panel.getByText('情节逐项核对 · 待核对（含歧义或未知）', { exact: true }).click()
  await expect(panel).toContainText('接收者范围：仅限指定接收者')
  const guard = panel.getByRole('region', { name: '流向补充核对', exact: true })
  await expect(guard).toContainText('restricted_recipient_unverified')
  await expect(guard).toContainText('接收者编号：unknown')
  await expect(guard).toContainText('unknown表示身份未确认，不能根据备注猜测其具体人物')
  await panel.getByText('改判前模型结论（仅历史，不代表最终核对）', { exact: true }).click()
  await expect(panel).toContainText('原模型曾标pass')
  await expect(panel).toContainText('不能猜测unknown是谁或直接判违背')
  const chapters = await read<{ id: string }[]>(page, `/api/branches/${started.branch_id}/chapters`)
  expect(chapters).toHaveLength(1)
  expect(await read(page, `/api/chapters/${chapters[0].id}`)).toMatchObject({ content: '', revision: 0 })
  expect(await read(page, `/api/chapters/${chapter.id}`)).toEqual(original)
  const transcript = await read<{ steps: unknown[] }>(page, `/api/tasks/${task.id}/transcript`)
  expect(transcript.steps).toHaveLength(5)
  await testInfo.attach('v2-exclusive-recipient-unknown.json', {
    body: JSON.stringify({ task, candidate, transcript }, null, 2),
    contentType: 'application/json',
  })
})
