import { expect, test, type Page } from '@playwright/test'
import { createHash } from 'node:crypto'
import type { ConceptionState } from '../../frontend/conception'

const idea = '学徒靠铜铃的断痕寻找师姐。'

async function authorHeaders(page: Page) {
  const { csrf }: { csrf: string } = await (await page.request.get('/api/session')).json()
  return { 'X-Ainovel-CSRF': csrf }
}

async function startFromPanel(page: Page, branch: string) {
  const start = page
    .getByRole('region', { name: '七台自动接力' })
    .getByRole('button', { name: '启动七台接力' })
  await expect(start).toBeEnabled()
  const response = page.waitForResponse(
    (entry) => entry.request().method() === 'POST' && entry.url().endsWith(`${branch}/seven-stage-runs`),
  )
  const [entry] = await Promise.all([response, start.press('Enter')])
  return entry
}

async function taskRecord(page: Page, id: string) {
  const response = await page.request.get('/api/tasks/' + id)
  expect(response.ok(), await response.text()).toBe(true)
  return response.json()
}

async function remainingChildren(page: Page, runId: string) {
  const parent = await taskRecord(page, runId)
  const ids: string[] = parent.result.seven_stage_run.children
  return Promise.all(
    ids.map(async (id) => {
      const { kind, payload } = await taskRecord(page, id)
      const transcript = await page.request.get(`/api/tasks/${id}/transcript`)
      expect(transcript.ok(), await transcript.text()).toBe(true)
      const {
        steps,
      }: { steps: { result: { call_id: string; usage: { input: number; output: number } } }[] } =
        await transcript.json()
      expect(steps.length).toBeGreaterThan(0)
      for (const { result } of steps) {
        expect(result.call_id).toBeTruthy()
        expect(result.usage.input).toBeGreaterThan(0)
        expect(result.usage.output).toBeGreaterThan(0)
      }
      return { kind, payload, steps }
    }),
  )
}

async function createEmptyWork(page: Page, title: string) {
  await page.goto('/')
  await page.getByText('＋ 新建作品', { exact: true }).click()
  await page.getByRole('textbox', { name: '作品名称', exact: true }).fill(title)
  const creation = page.waitForResponse(
    (response) => response.request().method() === 'POST' && response.url().endsWith('/api/novels'),
  )
  await page.getByRole('button', { name: '创建并开始写作', exact: true }).click()
  const response = await creation
  expect(response.ok(), await response.text()).toBe(true)
  const { branch_id } = await response.json()
  await expect(
    page.getByRole('combobox', { name: '作品', exact: true }).locator('option:checked'),
  ).toHaveText(title)
  return '/api/branches/' + branch_id
}

async function authorProject(
  page: Page,
  branch: string,
  ending: string,
  opening = '学徒向守闸人借尺，核对闸门上的公开刻度。',
) {
  await page.getByRole('button', { name: '立项台', exact: true }).click()
  const project = page.locator('#bench-project')
  await project.getByText('立项台', { exact: true }).press('Enter')
  await project.getByRole('combobox', { name: '主类型', exact: true }).selectOption('mystery')
  for (const [label, value] of [
    ['创作思路', opening],
    ['核心定位', '用公开记录复测门前的异响。'],
    ['赛道', '短篇求证悬疑'],
    ['读者定位', '偏好线索能回查的读者'],
    ['差异化', '学徒只检查与记录，把维修交给守闸人。'],
    ['篇幅目标', '一卷一章'],
    ['终局', ending],
  ]) {
    await project.getByRole('textbox', { name: label, exact: true }).fill(value)
  }
  const confirmed = page.waitForResponse(
    (entry) =>
      entry.request().method() === 'PUT' && entry.url().endsWith(`${branch}/conception/project-brief`),
  )
  await project.getByRole('button', { name: '确认立项', exact: true }).click()
  const signed = await confirmed
  expect(signed.ok(), await signed.text()).toBe(true)
  await expect(project.getByText('立项台 · 已确认修订 1')).toBeVisible()
  return (await signed.json()) as NonNullable<ConceptionState['project']>
}

async function openEmptyRun(page: Page, title: string) {
  const branch = await createEmptyWork(page, title)
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  const panel = page.getByRole('region', { name: '七台自动接力', exact: true })
  await expect(panel.getByLabel('接力起点')).toHaveValue('idea')
  await expect(panel.getByRole('option', { name: '从创意开始', exact: true })).toBeEnabled()
  await panel.getByRole('textbox', { name: '自动立项的创作思路' }).fill(idea)
  await panel.getByLabel('本次接力总预算（所选账户币种）').fill('1')
  return { panel, branch }
}

async function submitEmptyRun(page: Page, panel: Awaited<ReturnType<typeof openEmptyRun>>['panel']) {
  const created = page.waitForResponse(
    (response) => response.request().method() === 'POST' && response.url().endsWith('/seven-stage-runs'),
  )
  await panel.getByRole('button', { name: '启动七台接力' }).click()
  const response = await created
  expect(response.ok(), await response.text()).toBe(true)
  expect(response.request().postDataJSON()).toMatchObject({ start_from: 'idea', idea })
  return response.json()
}

test('从同一句创意逐台推进，经作者确认交付两章正文', async ({ page }) => {
  const branch = await createEmptyWork(page, '后台验证 · 逐台写完遗铃')
  const ending = '师姐与学徒在门内共同修复铜铃。'
  const get = async (path: string) => {
    const response = await page.request.get(path)
    expect(response.ok(), await response.text()).toBe(true)
    return response.json()
  }
  await page.getByRole('button', { name: '立项台', exact: true }).click()
  await page.locator('#bench-project').getByText('立项台', { exact: true }).click()
  await page.getByRole('textbox', { name: '创作思路', exact: true }).fill(idea)
  await page.getByRole('button', { name: 'AI 分析立项', exact: true }).click()
  await page.getByRole('button', { name: '载入 AI 立项建议', exact: true }).click()
  const endingField = page.getByRole('textbox', { name: '终局', exact: true })
  await expect(endingField).toHaveValue('学徒独自带铃离山。')
  await endingField.fill(ending)
  await page.getByRole('button', { name: '确认立项', exact: true }).click()
  await expect(page.getByText('立项台 · 已确认修订 1', { exact: true })).toBeVisible()
  const { project }: ConceptionState = await get(branch + '/conception')
  expect(project).toMatchObject({ idea, revision: 1, brief: { ending, genre_ids: ['xianxia', 'mystery'] } })
  expect(project?.candidate_id).toBeTruthy()

  // Reference analysis is optional. Leave it empty while adopting an actual setting candidate.
  const references = branch + '/outline-workspace/reference-options'
  expect(await get(references)).toEqual([])
  await page.getByRole('button', { name: '去完善世界设定', exact: true }).click()
  await page
    .getByRole('textbox', { name: '构思要求', exact: true })
    .fill('补写镜廊证人令的悬疑线索揭露边界。')
  const settingQueued = page.waitForRequest(
    (request) => request.method() === 'POST' && request.url().endsWith(`${branch}/conception/generate`),
  )
  await page.getByRole('button', { name: '生成当前类别', exact: true }).click()
  expect((await settingQueued).postDataJSON()).toMatchObject({
    category_id: 'world',
    project_confirmation_id: project!.id,
  })
  const candidate = page.getByRole('region', { name: '核对 AI 草案', exact: true })
  await candidate.getByRole('checkbox', { name: '选择 证人令披露规则', exact: true }).check()
  await candidate.getByRole('button', { name: '采用所选构思', exact: true }).click()
  await page
    .getByRole('textbox', { name: '保留事项说明', exact: true })
    .fill('两章只使用镜廊的线索披露规则。')
  const settingSaved = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' && response.url().endsWith(`${branch}/conception/confirm`),
  )
  await page.getByRole('button', { name: '确认当前构思', exact: true }).click()
  const settingResponse = await settingSaved
  expect(settingResponse.ok(), await settingResponse.text()).toBe(true)
  const { categories, confirmation }: ConceptionState = await settingResponse.json()
  expect(confirmation?.current).toBe(true)
  const rule = categories.find(({ id }) => id === 'world')?.items[0]
  expect(rule?.title).toBe('证人令披露规则')

  const source = '😀渡口掌柜先问用途，又把水路图收回袖中。他只肯给半幅图，却记下了来客的目的地。'
  const sourceName = `逐台验收·分段披露-${branch.split('/').at(-1)}.txt`
  const library = await page.request.post('/api/library/import', {
    headers: await authorHeaders(page),
    multipart: { file: { name: sourceName, mimeType: 'text/plain', buffer: Buffer.from(source) } },
  })
  expect(library.ok(), await library.text()).toBe(true)
  await page.getByRole('button', { name: '资料书库', exact: true }).click()
  await page.getByText(sourceName).first().click()
  const reader = page.getByRole('textbox', { name: '资料原文' })
  await expect(reader).toHaveValue(source)
  await reader.press('ControlOrMeta+a')
  await page.getByRole('button', { name: '拆解这段写法' }).click()
  await page.getByRole('button', { name: /明确使用顶部当前工作版本/ }).click()
  await page.getByRole('textbox', { name: '想学习什么' }).fill('分析阶段性收获怎么引导下一次行动')
  const saveCard = page.getByRole('button', { name: '保存本次规则与方案' })
  await saveCard.click()
  await page.getByRole('button', { name: '拆解选段写法' }).click()
  await page.getByRole('button', { name: /载入分析卡供编辑和选择/ }).click()
  await page.getByRole('checkbox', { name: /本次采用/ }).check()
  const referenceSaved = page.waitForResponse(
    (response) => response.request().method() === 'PATCH' && response.url().includes('/reference-sessions/'),
  )
  await saveCard.click()
  const referenceResponse = await referenceSaved
  expect(referenceResponse.ok(), await referenceResponse.text()).toBe(true)
  const cardSession = await referenceResponse.json()
  const [reference] = await get(references)
  expect(reference).toMatchObject({
    session_id: cardSession.id,
    card_id: cardSession.payload.cards[0].id,
    revision: cardSession.revision,
  })

  await page.getByRole('button', { name: '大纲与台账', exact: true }).click()
  const generation = page.getByRole('region', { name: '大纲生成', exact: true })
  await generation.getByLabel('新建规划章数').fill('2')
  await generation.getByRole('button', { name: '建立章节目录', exact: true }).click()
  await expect(generation.getByText('章节目录已建立。')).toBeVisible()
  const chapters: { id: string; title: string }[] = await get(branch + '/chapters')
  expect(chapters).toHaveLength(2)
  await generation.getByText('已保存的手法卡（可选）', { exact: true }).click()
  await generation.getByRole('combobox', { name: '选用手法卡' }).selectOption(reference.version_id)
  await generation.getByRole('button', { name: '生成四级大纲候选', exact: true }).click()
  await generation.getByRole('button', { name: '载入候选供编辑', exact: true }).click()
  await page.getByRole('button', { name: '保存大纲草稿', exact: true }).click()
  await page.getByRole('button', { name: '确认已保存大纲', exact: true }).click()
  await expect.poll(async () => (await get(branch + '/outline-workspace')).confirmed?.id).toBeTruthy()
  const { confirmed: outline } = await get(branch + '/outline-workspace')
  expect(outline).toMatchObject({
    setting_basis: {
      confirmation_id: confirmation!.id,
      project_id: project!.id,
      generation_id: expect.any(String),
      items: [expect.objectContaining({ id: rule!.id, detail: rule!.detail })],
    },
    plan: { summary: ending },
  })
  const planned = outline.plan.volumes[0].units[0].chapters
  expect(planned.map(({ chapter_id }: { chapter_id: string }) => chapter_id)).toEqual(
    chapters.map(({ id }) => id),
  )
  expect(planned[0].reference_cards).toEqual([expect.objectContaining({ version_id: reference.version_id })])

  const bodies = [
    '😀学徒拾起铜铃，发现铃身的断痕和镜廊门上的旧记号相合。证人先请他核对铃身，只指出门的位置，没有透露姓名。',
    '师姐听见铃声，从镜廊门内走来。学徒把铃身的断痕对给她看，师姐承认那是她留下的记号。师姐与学徒在门内共同修复铜铃。',
  ]
  await page.getByRole('button', { name: '写作', exact: true }).click()
  const directory = page.getByRole('navigation', { name: '全书目录' })
  const writing = page.getByRole('region', { name: '正文写作' })
  for (const [index, chapter] of chapters.entries()) {
    await directory.getByRole('button', { name: chapter.title }).click()
    await expect(writing.getByRole('textbox', { name: '章节标题' })).toHaveValue(chapter.title)
    await expect(page.getByText('检验铃身的断痕', { exact: true })).toBeVisible()
    const editor = writing.getByRole('textbox', { name: '章节正文' })
    await editor.press('ControlOrMeta+a')
    await page.keyboard.insertText(bodies[index])
    await writing.getByRole('button', { name: '保存', exact: true }).click()
    await expect.poll(async () => (await get(`/api/chapters/${chapter.id}`)).content).toBe(bodies[index])
  }
  const chapterProofs = await Promise.all(chapters.map(async ({ id }) => get(`/api/chapters/${id}`)))
  expect(chapterProofs.map(({ revision }) => revision)).toEqual([1, 1])

  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  const inspection = page.getByRole('region', { name: '十处对账体检', exact: true })
  await inspection.getByLabel('章数', { exact: true }).fill('2')
  await inspection.getByLabel('有效字符下限', { exact: true }).fill('1')
  await inspection.getByRole('button', { name: '保存篇幅目标', exact: true }).click()
  await expect(inspection.getByText('当前采用修订 1', { exact: true })).toBeVisible()
  const { preview } = await get(branch + '/workflow-inspections')
  expect(preview.items).toHaveLength(10)
  for (const { check_id, title, status } of preview.items) {
    if (status !== 'pending') continue
    await inspection.getByRole('combobox', { name: '核对项目' }).selectOption(check_id)
    await inspection.getByRole('combobox', { name: '作者判定' }).selectOption('green')
    await inspection
      .getByLabel('对照的章纲、约定或规则', { exact: true })
      .fill(`本次章纲与立项约定 · ${title}`)
    await inspection
      .getByLabel('判定理由', { exact: true })
      .fill('线索由断痕引到镜廊，师姐认领记号并完成铃的修复。')
    await inspection.getByRole('combobox', { name: '引用章节', exact: true }).selectOption(chapters[1].id)
    const excerpt = inspection.getByRole('textbox', { name: '保存稿（选择要引用的文字）', exact: true })
    await expect(excerpt).toHaveValue(bodies[1])
    await excerpt.press('ControlOrMeta+a')
    await inspection.getByRole('button', { name: '引用选区' }).press('Enter')
    await expect(inspection.getByRole('button', { name: '移除引文 1' })).toBeVisible()
  }
  const submittedInspection = page.waitForResponse(
    (response) => response.request().method() === 'POST' && response.url().endsWith('/workflow-inspections'),
  )
  await inspection.getByRole('button', { name: '生成体检单' }).click()
  const inspectionResponse = await submittedInspection
  expect(inspectionResponse.ok(), await inspectionResponse.text()).toBe(true)
  const report = await inspectionResponse.json()
  expect(report.id).toBeTruthy()
  expect(report.scope).toBe('book')
  expect(report.length_targets).toMatchObject({ expected_chapters: 2, min_chars: 1 })
  expect(report.items).toHaveLength(10)
  expect(
    report.items.every(({ status }: { status: string }) => ['green', 'not_applicable'].includes(status)),
  ).toBe(true)
  const authorReview = report.items.find(({ author_review }: { author_review: unknown }) => author_review)
  expect(authorReview.author_review.evidence).toEqual([
    { chapter_id: chapters[1].id, revision: 1, start: 0, end: bodies[1].length, quote: bodies[1] },
  ])

  const completion = page.getByRole('region', { name: '完本与交付', exact: true })
  await completion.getByRole('button', { name: '核对最新完本依据' }).click()
  for (const [name, value] of [
    ['通读记录', '已通读两章保存稿。'],
    ['主冲突与终局说明', ending],
    ['关键人物与关系说明', '师姐发现学徒带回了铃，并和学徒一起修复。'],
    ['伏笔与读者承诺说明', '铃身断痕对应镜廊记号，师姐在末章承认留下记号。'],
    ['简介', '学徒依照一枚铜铃上的断痕寻找失散的师姐。'],
  ]) {
    await completion.getByRole('textbox', { name, exact: true }).fill(value)
  }
  await completion.getByText('添加正文依据', { exact: true }).click()
  for (const [purpose, name] of [
    ['ending', '主冲突与终局判定'],
    ['characters', '关键人物与关系判定'],
    ['promises', '伏笔与读者承诺判定'],
  ]) {
    await completion.getByLabel('引用用于').selectOption(purpose)
    await completion.getByLabel('完本引文章节').selectOption(chapters[1].id)
    const excerpt = completion.getByRole('textbox', { name: /保存稿修订.*选择要引用的文字/ })
    await expect(excerpt).toHaveValue(bodies[1])
    await excerpt.press('ControlOrMeta+a')
    await completion.getByRole('button', { name: '引用选区' }).press('Enter')
    await completion.getByRole('combobox', { name, exact: true }).selectOption('verified')
  }
  await completion.getByRole('checkbox', { name: '已通读本次稿件', exact: true }).check()
  await completion.getByRole('button', { name: '预览交付材料' }).click()
  const confirm = completion.getByRole('button', { name: '确认完结', exact: true })
  await expect(confirm).toBeDisabled()
  await completion.getByRole('checkbox', { name: '已核对这次交付材料与剧透范围' }).check()
  const result = page.waitForResponse((response) => response.url().endsWith('/book-completion/confirm'))
  await confirm.click()
  const completed = await result
  expect(completed.ok(), await completed.text()).toBe(true)
  const delivery = await completed.json()
  expect(delivery.checks.map(({ status }: { status: string }) => status)).toEqual(Array(6).fill('green'))
  expect(delivery.manifest).toMatchObject({
    project_id: project!.id,
    setting_id: confirmation!.id,
    outline_id: outline.id,
    inspection_id: report.id,
    chapters: chapterProofs.map(({ id, revision, content }) => ({
      id,
      revision,
      sha256: createHash('sha256').update(content).digest('hex'),
    })),
  })
  expect(Object.keys(delivery.manifest.files).sort()).toEqual(
    ['正文.md', '设定册.md', '简介.md', '试读.md'].sort(),
  )
  const download = completion.getByRole('link', { name: '下载交付包', exact: true })
  await expect(download).toBeVisible()
  const zip = await page.request.get((await download.getAttribute('href'))!)
  expect(zip.headers()['content-type']).toBe('application/zip')
  expect(await get(branch + '/seven-stage-runs')).toEqual([])
})

test('从一句创意自动生成至待核对完本，作者确认后接力关单', async ({ page }) => {
  const { panel, branch } = await openEmptyRun(page, '后台验证 · 从一句创意到完本')
  await panel.getByRole('checkbox', { name: '授权确认本次生成的规划产物' }).check()
  await panel.getByRole('checkbox', { name: '授权采纳通过检查的空章正文候选' }).check()
  const run = await submitEmptyRun(page, panel)
  await expect(panel.getByRole('status')).toHaveText('校验 · 等待处理检查点', { timeout: 30_000 })
  const taskInfo = async () => {
    const response = await page.request.get('/api/tasks/' + run.id)
    expect(response.ok(), await response.text()).toBe(true)
    return (await response.json()).result.seven_stage_run
  }
  const first = await taskInfo()
  expect(first.stage).toBe('inspection')
  expect(first.reference_analysis_id).toBeUndefined()
  expect(first.project_id && first.setting_id && first.outline_id).toBeTruthy()
  expect(first.inspection_candidate_id).toBeTruthy()
  expect(first.completed_chapters).toHaveLength(2)
  expect(first.completed_chapters.map(({ revision }: { revision: number }) => revision)).toEqual([1, 1])

  const chapterSources = await Promise.all(
    first.completed_chapters.map(async ({ id }: { id: string }) => {
      const row = await page.request.get('/api/chapters/' + id)
      expect(row.ok(), await row.text()).toBe(true)
      const saved = await row.json()
      expect(saved.content).toContain('先把公开刻度抄进本子')
      return {
        chapter_id: id,
        revision: saved.revision,
        start: 0,
        end: saved.content.length,
        quote: saved.content,
      }
    }),
  )
  const conception = await (await page.request.get(branch + '/conception')).json()
  expect(conception).toMatchObject({
    project: { id: first.project_id, idea },
    confirmation: { id: first.setting_id, current: true },
  })
  const outline = await (await page.request.get(branch + '/outline-workspace')).json()
  expect(outline.confirmed).toMatchObject({
    id: first.outline_id,
    setting_basis: { confirmation_id: first.setting_id, project_id: first.project_id },
  })

  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  await expect(page.getByRole('region', { name: '十处对账体检' })).toBeVisible()
  const endpoint = branch + '/workflow-inspections'
  const { preview, latest } = await (await page.request.get(endpoint)).json()
  expect(latest.id).toBe(first.inspection_id)
  const { csrf } = await (await page.request.get('/api/session')).json()
  const signed = await page.request.post(endpoint, {
    headers: { 'X-Ainovel-CSRF': csrf },
    data: {
      basis_sha256: preview.basis_sha256,
      length_targets: { min_chars: 1, expected_chapters: 2 },
      author_reviews: preview.items
        .filter(({ status }: { status: string }) => status === 'pending')
        .map(({ check_id }: { check_id: string }) => ({
          check_id,
          status: 'green',
          basis_note: '已逐章核对立项、章纲及保存稿。',
          reason: '两章的保存稿提供了需要复查的线索与行动。',
          evidence: chapterSources,
        })),
    },
  })
  expect(signed.ok(), await signed.text()).toBe(true)
  const approved = await signed.json()
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  await panel.getByRole('button', { name: '继续接力' }).click()
  await expect(panel.getByRole('status')).toHaveText('完本 · 等待处理检查点', { timeout: 20_000 })
  const prepared = await taskInfo()
  expect(prepared).toMatchObject({ inspection_id: approved.id, stage: 'completion' })
  expect(prepared.completed_chapters).toEqual(first.completed_chapters)
  expect(prepared.completion_preparation_id).toBeTruthy()
  expect(prepared.completion_id).toBeUndefined()

  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  const completion = page.getByRole('region', { name: '完本与交付', exact: true })
  await expect(completion.getByRole('link', { name: '下载交付包', exact: true })).toHaveCount(0)
  await completion.getByText('AI 准备完本建议', { exact: true }).click()
  await completion.getByRole('button', { name: '载入 AI 建议到空白项' }).click()
  await expect(completion.getByRole('textbox', { name: '简介', exact: true })).toHaveValue(
    '学徒从铜铃的来历追起。',
  )
  await completion.getByRole('textbox', { name: '通读记录', exact: true }).fill('作者已通读两章保存稿。')
  await completion
    .getByRole('textbox', { name: '关键人物与关系说明', exact: true })
    .fill('已核对守闸人与学徒的行动。')
  await completion
    .getByRole('textbox', { name: '伏笔与读者承诺说明', exact: true })
    .fill('作者确认本次短篇没有待处理的伏笔。')
  await completion.getByText('添加正文依据', { exact: true }).click()
  await completion.getByLabel('引用用于').selectOption('characters')
  await completion.getByLabel('完本引文章节').selectOption(chapterSources[0].chapter_id)
  const excerpt = completion.getByRole('textbox', { name: /保存稿修订.*选择要引用的文字/ })
  await expect(excerpt).toBeVisible()
  await excerpt.press('ControlOrMeta+a')
  await completion.getByRole('button', { name: '引用选区' }).click()
  await completion.getByRole('checkbox', { name: '已通读本次稿件', exact: true }).check()
  for (const name of ['主冲突与终局判定', '关键人物与关系判定']) {
    await completion.getByRole('combobox', { name, exact: true }).selectOption('verified')
  }
  await completion
    .getByRole('combobox', { name: '伏笔与读者承诺判定', exact: true })
    .selectOption('author_decision')
  await completion.getByRole('button', { name: '预览交付材料' }).click()
  const confirm = completion.getByRole('button', { name: '确认完结', exact: true })
  await expect(confirm).toBeDisabled()
  await completion.getByRole('checkbox', { name: '已核对这次交付材料与剧透范围' }).check()
  const final = page.waitForResponse((response) => response.url().endsWith('/book-completion/confirm'))
  await confirm.click()
  const signedCompletion = await final
  expect(signedCompletion.ok(), await signedCompletion.text()).toBe(true)
  const approvedCompletion = await signedCompletion.json()
  expect(approvedCompletion.checks).toHaveLength(6)
  expect(approvedCompletion.checks.every(({ status }: { status: string }) => status === 'green')).toBe(true)
  expect(Object.keys(approvedCompletion.manifest.files).sort()).toEqual(
    ['正文.md', '设定册.md', '简介.md', '试读.md'].sort(),
  )
  const output = completion.getByRole('link', { name: '下载交付包', exact: true })
  await expect(output).toBeVisible()
  const zip = await page.request.get((await output.getAttribute('href'))!)
  expect(zip.headers()['content-type']).toBe('application/zip')

  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  await panel.getByRole('button', { name: '继续接力' }).click()
  await expect(panel.getByRole('status')).toHaveText('已核对完本 · 已完成接力')
  expect(await taskInfo()).toMatchObject({
    stage: 'done',
    completion_id: approvedCompletion.id,
    completed_chapters: first.completed_chapters,
  })
  const done = await taskInfo()
  expect(done.children).toEqual(prepared.children)
  await expect(panel.getByRole('option', { name: /从已确认立项接力/ })).toBeEnabled()
  await expect(panel.getByRole('option', { name: /从已确认设定接力/ })).toBeEnabled()
  await expect(panel.getByRole('option', { name: /从已确认大纲接力/ })).toBeEnabled()
  await expect(panel.getByRole('option', { name: '从已通过的全书体检接力' })).toBeEnabled()
})

test('从创意启动但未授权确认规划时停在立项草案', async ({ page }) => {
  const { panel, branch } = await openEmptyRun(page, '后台验证 · 作者确认停点')
  const run = await submitEmptyRun(page, panel)
  await expect(panel.getByRole('status')).toHaveText('立项 · 等待处理检查点', { timeout: 20_000 })
  await expect(panel.getByText('请在立项台确认这份立项，再继续。')).toBeVisible()
  const state = (await (await page.request.get('/api/tasks/' + run.id)).json()).result.seven_stage_run
  expect(state.children).toHaveLength(1)
  expect((await (await page.request.get(branch + '/conception')).json()).project).toBeNull()
  expect(await (await page.request.get(branch + '/chapters')).json()).toEqual([])
  await page.getByRole('button', { name: '立项台', exact: true }).click()
  const project = page.locator('#bench-project')
  await project.getByText('立项台', { exact: true }).click()
  await expect(project.getByRole('button', { name: '载入 AI 立项建议', exact: true })).toBeVisible()
})

test('接力停在拆解台时可采用局部重拆的正式卡并继续到大纲', async ({ page }) => {
  const branch = await createEmptyWork(page, '后台验证 · 拆解重做后接力')
  await authorProject(page, branch, '学徒核实门前刻度，将结果留入公开档册。')
  const headers = await authorHeaders(page)
  const quote = '😀渡口掌柜先问用途，又把水路图收回袖中。他只肯给半幅图，却记下了来客的目的地。'
  const library = await page.request.post('/api/library/import', {
    headers,
    multipart: { file: { name: '局部拆解接力.txt', mimeType: 'text/plain', buffer: Buffer.from(quote) } },
  })
  expect(library.ok(), await library.text()).toBe(true)
  const [unit] = await (
    await page.request.get(`/api/library/books/${(await library.json()).id}/units`)
  ).json()
  const branchId = branch.split('/').at(-1)
  const goal = '分析阶段性收获怎么引导下一次行动'
  const created = await page.request.post('/api/reference-sessions', {
    headers,
    data: { unit_id: unit.id, start: 0, end: quote.length, quote, goal, branch_id: branchId },
  })
  expect(created.status(), await created.text()).toBe(201)
  const session = await created.json()
  const sessionPath = `/api/reference-sessions/${session.id}`

  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  const panel = page.getByRole('region', { name: '七台自动接力', exact: true })
  await expect(panel.getByLabel('接力起点')).toHaveValue('project')
  await panel.getByLabel('本次接力总预算（所选账户币种）').fill('1')
  await panel.getByLabel('新建章节数').fill('1')
  await panel.getByLabel('拆解资料的用法').selectOption('analyze')
  await panel.getByLabel('自动拆解的保存选段').selectOption(`${session.id}/${session.revision}`)
  await panel.getByRole('checkbox', { name: '授权确认本次生成的规划产物' }).check()
  const started = await startFromPanel(page, branch)
  expect(started.status(), await started.text()).toBe(202)
  const run = await started.json()
  await expect(panel.getByRole('status')).toHaveText('拆解 · 等待处理检查点')
  const first = (await taskRecord(page, run.id)).result.seven_stage_run

  const redo = await page.request.post(`${sessionPath}/actions`, {
    headers,
    data: {
      action: 'analyze',
      base_revision: session.revision,
      account: 'offline',
      client_request_id: `manual-reference-redo-${run.id}`,
      analysis_scope: { start: quote.indexOf('他只肯'), end: quote.length },
    },
  })
  expect(redo.status(), await redo.text()).toBe(202)
  const redoId = (await redo.json()).id
  await expect.poll(async () => (await taskRecord(page, redoId)).status).toBe('succeeded')
  const analysisId = (await taskRecord(page, redoId)).result.artifact_id
  const analysis = await (await page.request.get(`/api/artifacts/${analysisId}`)).json()
  const [card] = analysis.payload.cards
  expect(card.evidence).toContain('他只肯')
  const saved = await page.request.patch(sessionPath, {
    headers,
    data: {
      base_revision: session.revision,
      branch_id: branchId,
      goal,
      cards: [card],
      selected_ids: [card.id],
    },
  })
  expect(saved.ok(), await saved.text()).toBe(true)
  const revision = (await saved.json()).revision

  await panel.getByRole('button', { name: '继续接力', exact: true }).press('Enter')
  await expect(panel.getByText(/已采用 1 张手法卡/)).toBeVisible()
  await expect
    .poll(async () => (await taskRecord(page, run.id)).result.seven_stage_run.outline_id, { timeout: 25_000 })
    .toBeTruthy()
  const final = (await taskRecord(page, run.id)).result.seven_stage_run
  const selected = { session_id: session.id, card_id: card.id, revision }
  expect(final.reference_analysis_id).toBe(first.reference_analysis_id)
  expect(final.reference_review_analysis_ids).toContain(analysisId)
  expect(final.reference_references).toEqual([expect.objectContaining(selected)])
  expect(final.children).not.toContain(redoId)
  const children = await Promise.all(final.children.map((id: string) => taskRecord(page, id)))
  expect(children.filter(({ payload }) => payload.action === 'reference.analyze')).toHaveLength(1)
  const preparation = children.find(({ payload }) => payload.outline_generation)
  expect(preparation?.payload.outline_generation.reference_cards).toEqual([expect.objectContaining(selected)])
})

test('手法卡读取失败时仍可跳过拆解启动接力，重试后可选卡', async ({ page }) => {
  const branch = await createEmptyWork(page, '后台验证 · 可选手法卡读取恢复')
  const project = await authorProject(page, branch, '学徒将复测记录交给守闸人。')
  let unavailable = true
  await page.route(`**${branch}/outline-workspace/reference-options`, (route) =>
    unavailable
      ? route.fulfill({ status: 503, json: { code: 'offline', message: '隔离替身暂时无法提供手法卡。' } })
      : route.continue(),
  )
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  const panel = page.getByRole('region', { name: '七台自动接力', exact: true })
  const warning = panel.getByText('已保存的手法卡暂时无法读取。', { exact: false })
  const cards = panel.getByRole('option', { name: '选用已保存的手法卡', exact: true })
  await expect(warning).toBeVisible()
  await expect(cards).toBeDisabled()
  await expect(panel.getByLabel('拆解资料的用法')).toHaveValue('skip')
  await expect(panel.getByLabel('接力起点')).toHaveValue('project')
  await panel.getByLabel('新建章节数').fill('1')
  await panel.getByLabel('本次接力总预算（所选账户币种）').fill('1')

  const started = await startFromPanel(page, branch)
  expect(started.status(), await started.text()).toBe(202)
  const request = started.request().postDataJSON()
  expect(request).toMatchObject({ start_from: 'project', source_id: project.id, chapter_count: 1 })
  expect(request).not.toHaveProperty('reference_cards')
  expect(request).not.toHaveProperty('reference_session_id')
  await expect(panel.getByRole('status')).toHaveText('设定 · 等待处理检查点')

  page.once('dialog', (confirmation) => void confirmation.accept())
  await panel.getByRole('button', { name: '停止本次接力', exact: true }).click()
  const retry = panel.getByRole('button', { name: '重新读取手法卡', exact: true })
  await expect(retry).toBeEnabled()
  unavailable = false
  await retry.press('Enter')
  await expect(cards).toBeEnabled()
  await expect(warning).toHaveCount(0)
  await expect(panel.getByLabel('接力起点')).toHaveValue('project')
  await expect(panel.getByLabel('本次接力总预算（所选账户币种）')).toHaveValue('1')
})

test('从页面提交已确认立项，拒绝旧修订后只执行设定及其后续任务', async ({ page }) => {
  const branch = await createEmptyWork(page, '后台验证 · 从已确认立项接力')
  const old = await authorProject(page, branch, '学徒把复测记录留在门房。')
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  const panel = page.getByRole('region', { name: '七台自动接力', exact: true })
  await expect(panel.getByLabel('接力起点')).toHaveValue('project')
  await expect(panel.getByRole('option', { name: '从已确认立项接力 · 修订 1' })).toBeEnabled()
  await expect(panel.getByRole('option', { name: '从已确认设定接力', exact: true })).toBeDisabled()
  await panel.getByLabel('新建章节数').fill('1')
  await panel.getByLabel('本次接力总预算（所选账户币种）').fill('1')
  await panel.getByRole('checkbox', { name: '授权确认本次生成的规划产物' }).check()
  await panel.getByRole('checkbox', { name: '授权采纳通过检查的空章正文候选' }).check()

  const ending = '守闸人核对记录，当面把旧铜铃交还给学徒。'
  const changed = await page.request.put(branch + '/conception/project-brief', {
    headers: await authorHeaders(page),
    data: {
      base_revision: old.revision,
      idempotency_key: 'project-relay-browser-later-version',
      idea: old.idea,
      brief: { ...old.brief, ending },
    },
  })
  expect(changed.ok(), await changed.text()).toBe(true)
  const current = await changed.json()
  const rejected = await startFromPanel(page, branch)
  expect(rejected.request().postDataJSON()).toMatchObject({ start_from: 'project', source_id: old.id })
  expect(rejected.status()).toBe(409)
  expect((await rejected.json()).code).toBe('project_brief_changed')
  await expect(panel.getByRole('alert')).toContainText('请选择当前已确认的立项')
  expect(await (await page.request.get(branch + '/seven-stage-runs')).json()).toEqual([])

  await panel.getByRole('button', { name: '核对接力起点' }).click()
  await expect(panel.getByRole('option', { name: '从已确认立项接力 · 修订 2' })).toBeEnabled()
  const response = await startFromPanel(page, branch)
  expect(response.ok(), await response.text()).toBe(true)
  expect(response.request().postDataJSON()).toMatchObject({
    start_from: 'project',
    source_id: current.id,
    chapter_count: 1,
    auto_confirm_planning: true,
    auto_adopt_body: true,
  })
  const run = await response.json()
  expect(run.result.seven_stage_run).toMatchObject({ stage: 'settings', project_id: current.id })
  await expect(panel.getByRole('status')).toHaveText('校验 · 等待处理检查点', { timeout: 30_000 })
  const {
    result: { seven_stage_run: state },
  } = await taskRecord(page, run.id)
  expect(state).toMatchObject({ stage: 'inspection', project_id: current.id })
  expect(state.inspection_candidate_id && state.inspection_id).toBeTruthy()
  expect(state.completion_preparation_id).toBeUndefined()
  const children = await remainingChildren(page, run.id)
  expect(
    children.map(({ kind, payload }) => [kind, payload.project_confirmation_id ?? payload.action]),
  ).toEqual([
    ['conception_generate', current.id],
    ['module', 'outline.plan'],
    ['module', 'writing.expand_plot'],
    ['module', 'review.chapter'],
  ])
  expect(children[0].payload.target_category_ids).toEqual(['core', 'characters', 'world', 'relationships'])

  const after: ConceptionState = await (await page.request.get(branch + '/conception')).json()
  expect(after.project).toEqual(current)
  expect(after.confirmation).toMatchObject({ id: state.setting_id, current: true })
  const { confirmed } = await (await page.request.get(branch + '/outline-workspace')).json()
  expect(confirmed).toMatchObject({
    id: state.outline_id,
    setting_basis: { project_id: current.id, confirmation_id: state.setting_id },
    plan: { summary: ending },
  })
  expect(state.completed_chapters).toHaveLength(1)
  const [chapter] = state.completed_chapters
  const saved = await (await page.request.get('/api/chapters/' + chapter.id)).json()
  expect(saved.revision).toBe(chapter.revision)
  expect(saved.content).toContain('先把公开刻度抄进本子')
  const { latest } = await (await page.request.get(branch + '/workflow-inspections')).json()
  expect(latest).toMatchObject({ id: state.inspection_id, scope: 'book', current: true })
})

test('从作者确认的设定开始接力，冻结立项与设定并在校验台待签', async ({ page }) => {
  const branch = await createEmptyWork(page, '后台验证 · 作者设定接力')
  const ending = '作者决定只留下复测读数，闸门由守闸人处理。'
  const rule = '学徒只用守闸人借出的尺复测公开刻度。'
  await authorProject(page, branch, ending, idea)
  await page.locator('#bench-project').getByRole('button', { name: '去完善世界设定', exact: true }).click()
  const settings = page.getByRole('region', { name: '按类别完善小说设定' })
  await expect(settings.getByRole('heading', { name: '世界设定', exact: true })).toBeVisible()
  await settings.getByRole('button', { name: '添加构思项目', exact: true }).click()
  await settings.getByRole('textbox', { name: '项目名称', exact: true }).fill('公开刻度复测规则')
  await settings.getByRole('textbox', { name: '设定内容', exact: true }).fill(rule)
  await settings.getByRole('button', { name: '保存构思设定', exact: true }).click()
  await page.getByRole('textbox', { name: '保留事项说明', exact: true }).fill('作者已确认复测范围。')
  const accepted = page.waitForResponse(
    (entry) => entry.request().method() === 'POST' && entry.url().endsWith(`${branch}/conception/confirm`),
  )
  await page.getByRole('button', { name: '确认当前构思', exact: true }).click()
  const signed = await accepted
  expect(signed.ok(), await signed.text()).toBe(true)
  const source: ConceptionState = await signed.json()
  expect(source.confirmation?.current).toBe(true)
  expect(source.project?.idea).toBe(idea)
  expect(source.project?.brief.ending).toBe(ending)
  const [item] = source.categories.find(({ id }) => id === 'world')!.items
  expect(item.detail).toBe(rule)
  expect(await (await page.request.get(branch + '/chapters')).json()).toEqual([])

  const quote = '😀渡口掌柜先问用途，又把水路图收回袖中。他只肯给半幅图，却记下了来客的目的地。'
  const filename = `设定接力手法卡-${branch.split('/').at(-1)}.txt`
  const imported = await page.request.post('/api/library/import', {
    headers: await authorHeaders(page),
    multipart: { file: { name: filename, mimeType: 'text/plain', buffer: Buffer.from(quote) } },
  })
  expect(imported.ok(), await imported.text()).toBe(true)
  await page.getByRole('button', { name: '资料书库', exact: true }).click()
  await page.getByText(filename).first().click()
  const original = page.getByRole('textbox', { name: '资料原文' })
  await expect(original).toHaveValue(quote)
  await original.press('ControlOrMeta+A')
  await page.getByRole('button', { name: '拆解这段写法' }).click()
  await page.getByRole('button', { name: '拆解选段写法' }).click()
  await page.getByRole('button', { name: /载入分析卡供编辑和选择/ }).click()
  await page.getByRole('button', { name: /明确使用顶部当前工作版本/ }).click()
  const savedCard = page.waitForResponse(
    (entry) => entry.request().method() === 'PATCH' && entry.url().includes('/api/reference-sessions/'),
  )
  await page.getByRole('button', { name: '保存本次规则与方案' }).click()
  const storedCard = await savedCard
  expect(storedCard.ok(), await storedCard.text()).toBe(true)
  const session = await storedCard.json()
  expect(session.payload.selected_ids).toEqual([])
  const options = await page.request.get(branch + '/outline-workspace/reference-options')
  expect(options.ok(), await options.text()).toBe(true)
  const [{ session_id, card_id, revision, version_id }] = await options.json()
  expect(session_id).toBe(session.id)
  const reference = { session_id, card_id, revision, version_id }

  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  const panel = page.getByRole('region', { name: '七台自动接力', exact: true })
  await expect(panel.getByLabel('接力起点')).toHaveValue('settings')
  await expect(panel.getByRole('option', { name: /从已确认设定接力/ })).toBeEnabled()
  await expect(panel.getByRole('option', { name: /从已确认大纲接力/ })).toBeDisabled()
  await panel.getByLabel('新建章节数').fill('1')
  await panel.getByLabel('本次接力总预算（所选账户币种）').fill('1')
  await panel.getByLabel('拆解资料的用法').selectOption('cards')
  await expect(panel.getByRole('button', { name: '启动七台接力' })).toBeDisabled()
  await panel.getByText('已保存的手法卡（可选）', { exact: true }).click()
  await panel.getByRole('combobox', { name: '选用手法卡', exact: true }).selectOption(version_id)
  await expect(panel.getByText(/适用条件 ·/)).toBeVisible()
  await expect(panel.getByText(/须替换 ·/)).toBeVisible()
  await panel.getByRole('checkbox', { name: '授权确认本次生成的规划产物' }).check()
  await panel.getByRole('checkbox', { name: '授权采纳通过检查的空章正文候选' }).check()
  const response = await startFromPanel(page, branch)
  expect(response.ok(), await response.text()).toBe(true)
  expect(response.request().postDataJSON()).toMatchObject({
    start_from: 'settings',
    source_id: source.confirmation!.id,
    chapter_count: 1,
    auto_confirm_planning: true,
    auto_adopt_body: true,
    reference_cards: [reference],
  })
  expect(response.request().postDataJSON().reference_session_id).toBeUndefined()
  const run = await response.json()
  const basis = { project_id: source.project!.id, setting_id: source.confirmation!.id }
  expect(run.result.seven_stage_run).toMatchObject(basis)

  await expect(panel.getByRole('status')).toHaveText('校验 · 等待处理检查点', { timeout: 30_000 })
  const task = await (await page.request.get(`/api/tasks/${run.id}`)).json()
  const result = task.result.seven_stage_run
  expect(result).toMatchObject({ ...basis, stage: 'inspection' })
  expect(result.reference_references).toEqual([reference])
  expect(result.reference_analysis_id).toBeUndefined()
  expect(result.inspection_candidate_id).toBeTruthy()
  expect(result.completion_id).toBeUndefined()
  const children = await Promise.all(
    (result.children as string[]).map(async (id) => {
      const entry = await page.request.get(`/api/tasks/${id}`)
      expect(entry.ok(), await entry.text()).toBe(true)
      return entry.json()
    }),
  )
  expect(children).toHaveLength(3)
  expect(children.map(({ kind, payload }) => [kind, payload.action])).toEqual([
    ['module', 'outline.plan'],
    ['module', 'writing.expand_plot'],
    ['module', 'review.chapter'],
  ])
  expect(children[0].payload.outline_generation.confirmation_id).toBe(basis.setting_id)
  expect(children[0].payload.outline_generation.reference_cards).toEqual([reference])
  expect(children[2].payload.inspection_semantic.scope).toBe('book')

  const outline = await (await page.request.get(branch + '/outline-workspace')).json()
  expect(outline.confirmed).toMatchObject({
    id: result.outline_id,
    setting_basis: {
      project_id: basis.project_id,
      confirmation_id: basis.setting_id,
      items: [expect.objectContaining({ id: item.id, detail: rule })],
    },
    plan: { summary: ending },
  })
  expect(outline.confirmed.plan.volumes[0].units[0].chapters[0].reference_cards).toEqual([reference])
  expect((await (await page.request.get(`/api/reference-sessions/${session_id}`)).json()).revision).toBe(
    session.revision,
  )
  const after: ConceptionState = await (await page.request.get(branch + '/conception')).json()
  expect(after.project).toEqual(source.project)
  expect(after.confirmation).toMatchObject({ id: basis.setting_id, current: true })
  expect(result.completed_chapters).toHaveLength(1)
  const [chapter] = result.completed_chapters
  expect(chapter.revision).toBe(1)
  const saved = await (await page.request.get(`/api/chapters/${chapter.id}`)).json()
  expect(saved.revision).toBe(1)
  expect(saved.content).toContain('先把公开刻度抄进本子')
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  await expect(page.getByRole('region', { name: '十处对账体检' })).toBeVisible()
  const report = await (await page.request.get(branch + '/workflow-inspections')).json()
  expect(report.latest).toMatchObject({ id: result.inspection_id, scope: 'book', current: true })
  expect(
    report.latest.items.find(({ check_id }: { check_id: string }) => check_id === '01')?.basis.project.id,
  ).toBe(basis.project_id)
})

async function relayOutline(page: Page, title: string) {
  await page.goto('/')
  const headers = await authorHeaders(page)
  const created = await page.request.post('/api/novels', { headers, data: { title } })
  expect(created.ok(), await created.text()).toBe(true)
  const novel = await created.json()
  const branch = `/api/branches/${novel.branch_id}`
  const chapterResponse = await page.request.post(`${branch}/chapters`, {
    headers,
    data: { title: '门前复测' },
  })
  expect(chapterResponse.ok(), await chapterResponse.text()).toBe(true)
  const chapter = await chapterResponse.json()
  const outline = await page.request.put(`${branch}/outline-workspace`, {
    headers,
    data: {
      base_revision: 0,
      plan: {
        summary: '先核对公开刻度，开篇保留疑问。',
        volumes: [
          {
            id: 'v',
            title: '第一卷',
            summary: '靠公开记录求证。',
            units: [
              {
                id: 'u',
                title: '复测',
                summary: '先借尺，再记录。',
                chapters: [
                  {
                    chapter_id: chapter.id,
                    goal: '核对刻度。',
                    obstacle: '尚未取得可用的尺。',
                    turn: '守闸人同意借尺。',
                    result: '她只检查，没有修门。',
                  },
                ],
              },
            ],
          },
        ],
      },
    },
  })
  expect(outline.ok(), await outline.text()).toBe(true)
  const confirmed = await page.request.post(`${branch}/outline-workspace/confirm`, {
    headers,
    data: { base_revision: (await outline.json()).draft.revision },
  })
  expect(confirmed.ok(), await confirmed.text()).toBe(true)
  const outlineId = (await confirmed.json()).confirmed.id

  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  const panel = page.getByRole('region', { name: '七台自动接力', exact: true })
  await expect(panel.getByLabel('接力起点')).toHaveValue('outline')
  await panel.getByLabel('本次接力总预算（所选账户币种）').fill('1')
  return { branch, chapter, headers, outlineId, panel }
}

test('从确认大纲接力时可人工编辑并采纳候选，再由同一批次续至 M6', async ({ page }) => {
  const { branch, chapter, headers, outlineId, panel } = await relayOutline(page, '后台验证 · 作者采纳再接力')
  const target = await page.request.put(branch + '/workflow-inspections/length-targets', {
    headers,
    data: { scope: 'chapter', scope_id: chapter.id, targets: { min_chars: 10000 } },
  })
  expect(target.ok(), await target.text()).toBe(true)
  await expect(panel.getByRole('checkbox', { name: '授权采纳通过检查的空章正文候选' })).not.toBeChecked()
  const submitted = await startFromPanel(page, branch)
  expect(submitted.ok(), await submitted.text()).toBe(true)
  expect(submitted.request().postDataJSON()).toMatchObject({
    start_from: 'outline',
    source_id: outlineId,
    auto_adopt_body: false,
  })
  const run = await submitted.json()
  await expect(panel.getByRole('status')).toHaveText('写作 · 等待处理检查点', { timeout: 25_000 })
  const {
    result: { seven_stage_run: stopped },
  } = await taskRecord(page, run.id)
  expect(stopped.children).toHaveLength(1)
  expect(stopped.completed_chapters).toEqual([])
  const child = await taskRecord(page, stopped.children[0])
  const candidate = child.result.artifact_id
  const chapterUrl = `/api/chapters/${chapter.id}`
  const readChapter = async () => (await page.request.get(chapterUrl)).json()
  expect(await readChapter()).toMatchObject({ revision: 0, content: '' })

  await page.getByRole('button', { name: '写作', exact: true }).click()
  const comparison = page.getByRole('combobox', { name: '比较候选', exact: true })
  await comparison.selectOption(candidate)
  const source = await (await page.request.get('/api/artifacts/' + candidate)).json()
  const editor = page.getByRole('textbox', { name: '候选内容', exact: true })
  await expect(editor).toHaveValue(source.payload.text)
  const lengthWarning = page.getByText(/生成时篇幅核算 · 本章候选 .*低于采用的下限 10000/)
  await expect(lengthWarning).toBeVisible()
  const edited = source.payload.text + '\n\n作者补记：复查刻度后，学徒把尺归还给守闸人。'
  await editor.fill(edited)
  await expect(lengthWarning).toHaveCount(0)
  await page.getByRole('button', { name: '保存候选编辑（原检查失效）', exact: true }).click()
  const accept = page.getByRole('button', { name: '采纳到正文', exact: true })
  const rereadMemory = page.getByRole('button', { name: '读取新版记忆并重新选择', exact: true })
  await expect(rereadMemory).toBeEnabled()
  await expect(accept).toBeDisabled()
  await rereadMemory.press('Enter')
  await expect(accept).toBeEnabled()
  expect(await readChapter()).toMatchObject({ revision: 0, content: '' })
  await accept.click()
  await expect.poll(readChapter).toMatchObject({ revision: 1, content: edited })

  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  const resumed = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' && response.url().endsWith(`/seven-stage-runs/${run.id}/resume`),
  )
  await panel.getByRole('button', { name: '继续接力', exact: true }).press('Enter')
  const receipt = await resumed
  expect(receipt.ok(), await receipt.text()).toBe(true)
  await expect(panel.getByRole('status')).toHaveText('校验 · 等待处理检查点', { timeout: 25_000 })
  const {
    result: { seven_stage_run: inspected },
  } = await taskRecord(page, run.id)
  expect(inspected).toMatchObject({
    outline_id: outlineId,
    completed_chapters: [{ id: chapter.id, revision: 1, artifact_id: candidate }],
  })
  expect(inspected.children).toHaveLength(2)
  expect(inspected.children[0]).toBe(child.id)
  const receipts = await remainingChildren(page, run.id)
  expect(receipts.map(({ payload }) => payload.action)).toEqual(['writing.expand_plot', 'review.chapter'])
  const { latest } = await (await page.request.get(branch + '/workflow-inspections')).json()
  expect(latest).toMatchObject({ id: inspected.inspection_id, current: true, scope: 'book' })
  expect(latest.chapters).toEqual([expect.objectContaining({ id: chapter.id, revision: 1 })])
})

test('从确认大纲、保存正文和有效全书体检接力，逐段到达校验与完本停点', async ({ page }) => {
  const { branch, chapter, headers, outlineId, panel } = await relayOutline(page, '后台验证 · 七台接力')
  await panel.getByRole('checkbox', { name: '授权采纳通过检查的空章正文候选' }).check()
  const queued = page.waitForResponse(
    (response) => response.request().method() === 'POST' && response.url().endsWith('/seven-stage-runs'),
  )
  await panel.getByRole('button', { name: '启动七台接力' }).click()
  const response = await queued
  expect(response.ok(), await response.text()).toBe(true)
  expect(response.request().postDataJSON()).toMatchObject({
    start_from: 'outline',
    source_id: outlineId,
    auto_adopt_body: true,
    budget_limit: 1,
  })
  const run = await response.json()
  await expect(panel.getByRole('status')).toHaveText('校验 · 等待处理检查点', { timeout: 20_000 })
  const batchId = panel.getByRole('textbox', { name: '父批次编号', exact: true })
  await expect(batchId).toHaveValue(run.id)
  await expect(batchId).toHaveAttribute('readonly', '')
  const saved = await (await page.request.get(`/api/chapters/${chapter.id}`)).json()
  expect(saved.revision).toBe(1)
  expect(saved.content).toContain('先把公开刻度抄进本子')
  const task = await (await page.request.get(`/api/tasks/${run.id}`)).json()
  expect(task.result.seven_stage_run).toMatchObject({ stage: 'inspection', outline_id: outlineId })
  expect(task.result.seven_stage_run.children).toHaveLength(2)
  expect(task.result.seven_stage_run.inspection_candidate_id).toBeTruthy()
  await expect(panel.getByRole('button', { name: '继续接力' })).toBeVisible()

  async function stopRelay() {
    page.once('dialog', (confirmation) => void confirmation.accept())
    await panel.getByRole('button', { name: '停止本次接力' }).click()
    await expect(panel.getByLabel('接力起点')).toBeEnabled()
  }
  const chapterUrl = '/api/chapters/' + chapter.id
  async function revise(base_revision: number, content: string) {
    const written = await page.request.put(chapterUrl, { headers, data: { base_revision, content } })
    expect(written.ok(), await written.text()).toBe(true)
    return (await written.json()) as { revision: number; content: string }
  }
  const runs = branch + '/seven-stage-runs'
  const sources = async () => (await page.request.get(runs + '/sources')).json()
  await stopRelay()
  await panel.getByRole('button', { name: '核对接力起点' }).click()
  await expect(panel.getByRole('option', { name: '从已保存的完整正文接力' })).toBeEnabled()
  await expect(panel.getByRole('option', { name: '从已通过的全书体检接力' })).toBeDisabled()
  const originalBasis = await sources()
  await panel.getByLabel('接力起点').selectOption('writing')
  await expect(panel.getByRole('checkbox', { name: '授权采纳通过检查的空章正文候选' })).toHaveCount(0)
  const updated = await revise(saved.revision, saved.content + '\n\n作者留了一份核对记录。')
  const beforeWriting = await sources()
  expect(beforeWriting.writing_id).not.toBe(originalBasis.writing_id)
  const staleWriting = await startFromPanel(page, branch)
  expect(staleWriting.request().postDataJSON()).toMatchObject({
    start_from: 'writing',
    source_id: originalBasis.writing_id,
  })
  expect(staleWriting.status()).toBe(409)
  expect((await staleWriting.json()).code).toBe('inspection_changed')
  await expect(panel.getByRole('alert')).toContainText('请重新核对当前已保存正文和全书体检')
  expect((await (await page.request.get(runs)).json()).map(({ id }: { id: string }) => id)).toEqual([run.id])
  await panel.getByRole('button', { name: '核对接力起点' }).click()
  const next = await startFromPanel(page, branch)
  expect(next.ok(), await next.text()).toBe(true)
  expect(next.request().postDataJSON()).toMatchObject({
    start_from: 'writing',
    source_id: beforeWriting.writing_id,
    auto_adopt_body: false,
    auto_confirm_planning: false,
  })
  const writing = await next.json()
  await expect(batchId).toHaveValue(writing.id)
  const historyPicker = panel.getByRole('combobox', { name: '接力记录', exact: true })
  await historyPicker.selectOption(run.id)
  await expect(batchId).toHaveValue(run.id)
  await historyPicker.press('Tab')
  await expect(batchId).toBeFocused()
  await batchId.evaluate((input: HTMLInputElement) => {
    input.addEventListener(
      'copy',
      (event) => {
        event.preventDefault()
        input.dataset.copied = input.value.slice(input.selectionStart ?? 0, input.selectionEnd ?? 0)
      },
      { once: true },
    )
  })
  await batchId.press('ControlOrMeta+A')
  await batchId.press('ControlOrMeta+C')
  await expect(batchId).toHaveAttribute('data-copied', run.id)
  await historyPicker.selectOption(writing.id)
  await expect(batchId).toHaveValue(writing.id)
  const writtenChapters = [{ id: chapter.id, revision: updated.revision, existing: true }]
  expect(writing.result.seven_stage_run).toMatchObject({
    stage: 'inspection',
    outline_id: outlineId,
    completed_chapters: writtenChapters,
  })
  await expect(panel.getByRole('status')).toHaveText('校验 · 等待处理检查点', { timeout: 20_000 })
  const {
    result: { seven_stage_run: inspected },
  } = await taskRecord(page, writing.id)
  expect(inspected).toMatchObject({
    stage: 'inspection',
    outline_id: outlineId,
    completed_chapters: writtenChapters,
  })
  expect(inspected.inspection_candidate_id && inspected.inspection_id).toBeTruthy()
  const checks = await remainingChildren(page, writing.id)
  expect(checks).toHaveLength(1)
  expect(checks[0]).toMatchObject({
    kind: 'module',
    payload: { action: 'review.chapter', inspection_semantic: { scope: 'book' } },
  })
  expect(checks[0].steps).toHaveLength(1)
  const candidate = await (
    await page.request.get('/api/artifacts/' + inspected.inspection_candidate_id)
  ).json()
  expect(candidate.payload.chapters).toEqual([
    expect.objectContaining({ id: chapter.id, revision: updated.revision }),
  ])
  expect(await (await page.request.get(chapterUrl)).json()).toMatchObject(updated)

  const inspections = branch + '/workflow-inspections'
  const { latest } = await (await page.request.get(inspections)).json()
  expect(latest).toMatchObject({ id: inspected.inspection_id, scope: 'book', current: true })
  expect(
    latest.items.some(({ current_status }: { current_status: string }) => current_status === 'pending'),
  ).toBe(true)
  await stopRelay()

  async function signInspection(content: { revision: number; content: string }) {
    const { preview } = await (await page.request.get(inspections)).json()
    const response = await page.request.post(inspections, {
      headers,
      data: {
        basis_sha256: preview.basis_sha256,
        author_reviews: preview.items
          .filter(({ status }: { status: string }) => status === 'pending')
          .map(({ check_id }: { check_id: string }) => ({
            check_id,
            status: 'green',
            basis_note: '逐项核对采用章纲及当前保存原文。',
            reason: '正文显示学徒只做检查，未改动闸门。',
            evidence: [
              {
                chapter_id: chapter.id,
                revision: content.revision,
                start: 0,
                end: content.content.length,
                quote: content.content,
              },
            ],
          })),
      },
    })
    expect(response.ok(), await response.text()).toBe(true)
    const report = await response.json()
    expect(report).toMatchObject({ scope: 'book', current: true })
    expect(
      report.items.every(({ current_status }: { current_status: string }) =>
        ['green', 'not_applicable'].includes(current_status),
      ),
    ).toBe(true)
    expect((await sources()).inspection_id).toBe(report.id)
    return report
  }
  const signedEarlier = await signInspection(updated)
  await panel.getByRole('button', { name: '核对接力起点' }).click()
  await panel.getByLabel('接力起点').selectOption('inspection')
  const finalManuscript = await revise(updated.revision, updated.content + '\n\n守闸人签收复测记录。')
  expect((await sources()).inspection_id).toBeNull()
  const staleInspection = await startFromPanel(page, branch)
  expect(staleInspection.request().postDataJSON()).toMatchObject({
    start_from: 'inspection',
    source_id: signedEarlier.id,
  })
  expect(staleInspection.status()).toBe(409)
  expect((await staleInspection.json()).code).toBe('inspection_changed')
  await expect(panel.getByRole('alert')).toContainText('请重新核对当前已保存正文和全书体检')
  expect((await (await page.request.get(runs)).json()).map(({ id }: { id: string }) => id).sort()).toEqual(
    [run.id, writing.id].sort(),
  )
  const historical = await (await page.request.get(inspections + '/' + signedEarlier.id)).json()
  expect(historical).toMatchObject({ id: signedEarlier.id, current: false })
  await panel.getByRole('button', { name: '核对接力起点' }).click()
  await expect(panel.getByRole('option', { name: '从已通过的全书体检接力' })).toBeDisabled()
  await expect(panel.getByRole('button', { name: '启动七台接力' })).toBeDisabled()

  const signedCurrent = await signInspection(finalManuscript)
  await panel.getByRole('button', { name: '核对接力起点' }).click()
  await expect(panel.getByRole('option', { name: '从已通过的全书体检接力' })).toBeEnabled()
  const finish = await startFromPanel(page, branch)
  expect(finish.ok(), await finish.text()).toBe(true)
  expect(finish.request().postDataJSON()).toMatchObject({
    start_from: 'inspection',
    source_id: signedCurrent.id,
    auto_adopt_body: false,
    auto_confirm_planning: false,
  })
  const completion = await finish.json()
  const finalChapters = [{ id: chapter.id, revision: finalManuscript.revision, existing: true }]
  expect(completion.result.seven_stage_run).toMatchObject({
    stage: 'completion',
    inspection_id: signedCurrent.id,
    outline_id: outlineId,
    completed_chapters: finalChapters,
  })
  await expect(panel.getByRole('status')).toHaveText('完本 · 等待处理检查点', { timeout: 20_000 })
  const {
    result: { seven_stage_run: prepared },
  } = await taskRecord(page, completion.id)
  expect(prepared).toMatchObject({
    stage: 'completion',
    inspection_id: signedCurrent.id,
    outline_id: outlineId,
    completed_chapters: finalChapters,
  })
  expect(prepared.completion_preparation_id).toBeTruthy()
  expect(prepared.completion_id).toBeUndefined()
  const preparation = await remainingChildren(page, completion.id)
  expect(preparation).toHaveLength(1)
  expect(preparation[0]).toMatchObject({
    kind: 'module',
    payload: { completion_preparation: { spoiler_scope: 'opening' } },
  })
  expect(preparation[0].steps).toHaveLength(2)
  expect((await (await page.request.get(inspections)).json()).latest.id).toBe(signedCurrent.id)
  expect(await (await page.request.get(chapterUrl)).json()).toMatchObject(finalManuscript)
  const releases = page.getByRole('region', { name: '完本与交付', exact: true })
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  await expect(releases).toBeVisible()
  await releases.getByText('AI 准备完本建议', { exact: true }).click()
  await releases.getByRole('button', { name: '载入 AI 建议到空白项' }).click()
  await expect(releases.getByRole('textbox', { name: '简介', exact: true })).toHaveValue(
    '学徒从铜铃的来历追起。',
  )
  await releases.getByRole('button', { name: '预览交付材料' }).click()
  await expect(releases.getByRole('button', { name: '确认完结', exact: true })).toBeDisabled()
  await expect(releases.getByRole('link', { name: '下载交付包', exact: true })).toHaveCount(0)

  const chapterScope = { scope: 'chapter', scope_id: chapter.id }
  const { preview: chapterPreview } = await (
    await page.request.get(inspections, { params: chapterScope })
  ).json()
  const signedRed = await page.request.post(inspections, {
    headers,
    data: {
      ...chapterScope,
      basis_sha256: chapterPreview.basis_sha256,
      author_reviews: [
        {
          check_id: '03',
          status: 'red',
          basis_note: '对照保存稿和人物已确认的身份。',
          reason: '本章存在需复检的人物状态冲突。',
          evidence: [
            {
              chapter_id: chapter.id,
              revision: finalManuscript.revision,
              start: 0,
              end: finalManuscript.content.length,
              quote: finalManuscript.content,
            },
          ],
        },
      ],
    },
  })
  expect(signedRed.ok(), await signedRed.text()).toBe(true)
  await releases.getByRole('button', { name: '核对最新完本依据' }).click()
  await expect(releases.getByText('依据已更新，请重新生成。', { exact: true })).toBeVisible()
  await expect(releases.getByRole('button', { name: '载入 AI 建议到空白项', exact: true })).toBeDisabled()
  const inspectionGate = releases
    .getByRole('list', { name: '本次拟确认的六项完本判定', exact: true })
    .getByRole('listitem')
    .filter({ hasText: '一致性与复检' })
  await expect(inspectionGate).toContainText('红 · 阻断')
  await expect(releases.getByRole('button', { name: '确认完结', exact: true })).toHaveCount(0)

  page.once('dialog', (confirmation) => void confirmation.accept())
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  await expect(panel.getByRole('status')).toHaveText('完本 · 等待处理检查点')
  const resume = panel.getByRole('button', { name: '继续接力', exact: true })
  await expect(resume).toBeEnabled()
  await resume.press('Enter')
  await expect(panel.getByText(/全书体检已有变化，请核对后继续/)).toBeVisible({ timeout: 20_000 })
  await expect(resume).toBeEnabled()
  await resume.press('Enter')
  await expect(panel.getByRole('status')).toHaveText('校验 · 等待处理检查点', { timeout: 20_000 })
  const reopened = await taskRecord(page, completion.id)
  expect(reopened.result.seven_stage_run.stage).toBe('inspection')
  expect(reopened.result.seven_stage_run.completion_id).toBeUndefined()
  const afterRed = await remainingChildren(page, completion.id)
  expect(afterRed.filter(({ payload }) => payload.completion_preparation)).toHaveLength(1)
  expect(await (await page.request.get(chapterUrl)).json()).toMatchObject(finalManuscript)
})
