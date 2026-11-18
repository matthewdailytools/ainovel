import { expect, test, type Page } from '@playwright/test'
import type { ConceptionState } from '../../frontend/conception'

const idea = '学徒靠铜铃的断痕寻找师姐。'

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
    .fill('已核对没有未回收的约定。')
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

test('从作者确认的设定开始接力，冻结立项与设定并在校验台待签', async ({ page }) => {
  const branch = await createEmptyWork(page, '后台验证 · 作者设定接力')
  const ending = '作者决定只留下复测读数，闸门由守闸人处理。'
  const rule = '学徒只用守闸人借出的尺复测公开刻度。'
  await page.getByRole('button', { name: '立项台', exact: true }).click()
  const projectPanel = page.locator('#bench-project')
  await projectPanel.getByText('立项台', { exact: true }).press('Enter')
  await projectPanel.getByRole('combobox', { name: '主类型', exact: true }).selectOption('mystery')
  for (const [label, value] of [
    ['创作思路', '学徒向守闸人借尺，核对闸门上的公开刻度。'],
    ['核心定位', '用公开记录复测门前的异响。'],
    ['赛道', '短篇求证悬疑'],
    ['读者定位', '偏好线索能回查的读者'],
    ['差异化', '学徒只检查与记录，把维修交给守闸人。'],
    ['篇幅目标', '一卷一章'],
    ['终局', ending],
  ]) {
    await projectPanel.getByRole('textbox', { name: label, exact: true }).fill(value)
  }
  await projectPanel.getByRole('button', { name: '确认立项', exact: true }).click()
  await expect(projectPanel.getByText('立项台 · 已确认修订 1')).toBeVisible()
  await projectPanel.getByRole('button', { name: '去完善世界设定', exact: true }).click()
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
  expect(source.project?.brief.ending).toBe(ending)
  const [item] = source.categories.find(({ id }) => id === 'world')!.items
  expect(item.detail).toBe(rule)
  expect(await (await page.request.get(branch + '/chapters')).json()).toEqual([])

  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  const panel = page.getByRole('region', { name: '七台自动接力', exact: true })
  await expect(panel.getByLabel('接力起点')).toHaveValue('settings')
  await expect(panel.getByRole('option', { name: /从已确认设定接力/ })).toBeEnabled()
  await expect(panel.getByRole('option', { name: /从已确认大纲接力/ })).toBeDisabled()
  await panel.getByLabel('新建章节数').fill('1')
  await panel.getByLabel('本次接力总预算（所选账户币种）').fill('1')
  await panel.getByRole('checkbox', { name: '授权确认本次生成的规划产物' }).check()
  await panel.getByRole('checkbox', { name: '授权采纳通过检查的空章正文候选' }).check()
  const queued = page.waitForResponse(
    (entry) => entry.request().method() === 'POST' && entry.url().endsWith(`${branch}/seven-stage-runs`),
  )
  await panel.getByRole('button', { name: '启动七台接力' }).press('Enter')
  const response = await queued
  expect(response.ok(), await response.text()).toBe(true)
  expect(response.request().postDataJSON()).toMatchObject({
    start_from: 'settings',
    source_id: source.confirmation!.id,
    chapter_count: 1,
    auto_confirm_planning: true,
    auto_adopt_body: true,
  })
  const run = await response.json()
  const basis = { project_id: source.project!.id, setting_id: source.confirmation!.id }
  expect(run.result.seven_stage_run).toMatchObject(basis)

  await expect(panel.getByRole('status')).toHaveText('校验 · 等待处理检查点', { timeout: 30_000 })
  const task = await (await page.request.get(`/api/tasks/${run.id}`)).json()
  const result = task.result.seven_stage_run
  expect(result).toMatchObject({ ...basis, stage: 'inspection' })
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

test('从作者确认的大纲接力写作，在体检检查点等待处理', async ({ page }) => {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const created = await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 七台接力' } })
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
  const saved = await (await page.request.get(`/api/chapters/${chapter.id}`)).json()
  expect(saved.revision).toBe(1)
  expect(saved.content).toContain('先把公开刻度抄进本子')
  const task = await (await page.request.get(`/api/tasks/${run.id}`)).json()
  expect(task.result.seven_stage_run).toMatchObject({ stage: 'inspection', outline_id: outlineId })
  expect(task.result.seven_stage_run.children).toHaveLength(2)
  expect(task.result.seven_stage_run.inspection_candidate_id).toBeTruthy()
  await expect(panel.getByRole('button', { name: '继续接力' })).toBeVisible()

  page.once('dialog', (confirmation) => void confirmation.accept())
  await panel.getByRole('button', { name: '停止本次接力' }).click()
  await expect(panel.getByLabel('接力起点')).toBeEnabled()
  await panel.getByRole('button', { name: '核对接力起点' }).click()
  await expect(panel.getByRole('option', { name: '从已保存的完整正文接力' })).toBeEnabled()
  await expect(panel.getByRole('option', { name: '从已通过的全书体检接力' })).toBeDisabled()
  await panel.getByLabel('接力起点').selectOption('writing')
  await expect(panel.getByRole('checkbox', { name: '授权采纳通过检查的空章正文候选' })).toHaveCount(0)
  const nextRequest = page.waitForResponse(
    (entry) => entry.request().method() === 'POST' && entry.url().endsWith('/seven-stage-runs'),
  )
  await panel.getByRole('button', { name: '启动七台接力' }).click()
  const next = await nextRequest
  expect(next.ok(), await next.text()).toBe(true)
  expect(next.request().postDataJSON()).toMatchObject({ start_from: 'writing', auto_adopt_body: false })
  expect((await next.json()).result.seven_stage_run).toMatchObject({
    stage: 'inspection',
    completed_chapters: [{ id: chapter.id, revision: 1, existing: true }],
  })
})
