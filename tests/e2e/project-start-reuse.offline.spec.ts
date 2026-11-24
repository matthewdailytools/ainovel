import { expect, test, type APIResponse, type Page } from '@playwright/test'
import { createHash } from 'node:crypto'

const idea = '木匠徒弟修好旧匣，就能看见失主遗忘的路。'
const opening = '作者新稿：宁禾听见风铃，没有过桥。😀'
const outline = '宁禾到桥头后先找风铃来源。'
const ending = '宁禾把旧匣交还，选择沿自己记下的路回家。'
const scene = '最后一镜是桥头只剩下风铃轻响。'
const firstEvidence = '😀渡口掌柜先问用途，又把水路图收回袖中。'
const secondEvidence = '掌柜只肯给半幅图，却记下了来客的目的地。'
const foreword = '卷首手记😀\n'
const crossing = `第一章 渡口\n${firstEvidence}\n第二章 匣底旧抬头\n${secondEvidence}\n`
const north = '第三章 北岸\n她把半幅图压在灯盏下。\n'
const oldManuscript = foreword + crossing + north
const filename = '自拟旧稿·半幅水路图.txt'
const inputFile = { name: filename, mimeType: 'text/plain', buffer: Buffer.from(oldManuscript) }

async function read(response: APIResponse) {
  expect(response.ok(), await response.text()).toBeTruthy()
  return response.json()
}

async function openIdea(page: Page) {
  await page.getByRole('button', { name: '小说构思', exact: true }).click()
  await page.getByRole('button', { name: '快速构思', exact: true }).click()
  await expect(page.getByRole('textbox', { name: '你的想法', exact: true })).toBeVisible()
}

test('可选立项恢复、旧稿调序导入与手法卡固定引用', async ({ page }, info) => {
  await page.goto('/')
  const { csrf } = await read(await page.request.get('/api/session'))
  const headers = { 'X-Ainovel-CSRF': csrf }
  const novel = await read(
    await page.request.post('/api/novels', {
      headers,
      data: { title: '后台验证 · 立项及旧稿复用' },
    }),
  )
  const branch = `/api/branches/${novel.branch_id}`
  const chaptersUrl = branch + '/chapters'
  const chapters = async () => read(await page.request.get(chaptersUrl))
  const manuscript = async (id: string) => read(await page.request.get(`/api/chapters/${id}`))
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await openIdea(page)
  await page.getByRole('textbox', { name: '你的想法', exact: true }).fill(idea)
  await page.getByRole('button', { name: '直接采用这段想法', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: '已采用为本书故事方向' })).toBeVisible()
  await page.getByRole('button', { name: '去写作 →', exact: true }).click()
  const editor = page.getByRole('textbox', { name: '章节正文', exact: true })
  await page.getByRole('button', { name: '新建第一章', exact: true }).click()
  await editor.fill(opening)
  await page.getByRole('button', { name: '保存', exact: true }).click()
  const [{ id: baselineId, title: baselineTitle }] = await chapters()
  await expect.poll(async () => (await manuscript(baselineId)).content).toBe(opening)
  await page.getByRole('button', { name: '章纲 · 暂无', exact: true }).click()
  await page.getByRole('button', { name: '记下作者章纲', exact: true }).click()
  await page.getByRole('textbox', { name: '本章安排', exact: true }).fill(outline)
  await page.getByRole('button', { name: '保存章纲', exact: true }).click()
  await expect(page.getByText(outline, { exact: true })).toBeVisible()
  const baseline = await manuscript(baselineId)
  const beforeBrief = await read(await page.request.get(branch + '/author-contract'))

  await openIdea(page)
  await page.getByText('立项简报（可选）', { exact: true }).click()
  await page.getByRole('textbox', { name: '核心定位', exact: true }).fill('从旧物记忆追查渡口失踪者')
  await page.getByRole('textbox', { name: '目标读者', exact: true }).fill('偏好古镇悬疑的网文读者')
  await page.getByRole('textbox', { name: '篇幅与更新计划', exact: true }).fill('三卷，每次更新一章')
  await page.getByRole('textbox', { name: '故事方向', exact: true }).fill(idea)
  await page.getByRole('textbox', { name: '预定结局', exact: true }).fill(ending)
  await page.getByRole('textbox', { name: '最后一镜', exact: true }).fill(scene)
  const briefUrl = branch + '/project-brief'
  const briefSaved = page.waitForResponse(
    (response) => response.url().endsWith(briefUrl) && response.request().method() === 'PUT',
  )
  await page.getByRole('button', { name: '保存立项草稿', exact: true }).click()
  expect((await briefSaved).ok()).toBeTruthy()
  await page.reload()
  await openIdea(page)
  await page.getByText('继续上次立项草稿', { exact: true }).click()
  await expect(page.getByRole('textbox', { name: '预定结局', exact: true })).toHaveValue(ending)
  await expect(page.getByRole('textbox', { name: '最后一镜', exact: true })).toHaveValue(scene)
  const briefAdopted = page.waitForResponse(
    (response) => response.url().endsWith(briefUrl + '/adopt') && response.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '确认立项简报', exact: true }).click()
  expect((await briefAdopted).ok()).toBeTruthy()
  expect((await read(await page.request.get(branch + '/author-contract'))).intent_contract).toEqual(
    beforeBrief.intent_contract,
  )
  expect(await manuscript(baselineId)).toEqual(baseline)
  await page.getByRole('button', { name: '写作', exact: true }).click()
  await expect(editor.locator('.cm-line')).toHaveText(opening)

  const workspace = page.getByRole('region', { name: '长篇拆章导入', exact: true })
  const mapping = workspace.getByRole('combobox', { name: '选择预览段', exact: true })
  const mappingTitle = workspace.getByRole('textbox', { name: '章节标题', exact: true })
  const appendOrder = workspace.getByRole('region', { name: '追加顺序', exact: true }).getByRole('listitem')
  async function openImport() {
    const menu = page.getByText('导入与导出', { exact: true })
    const button = page.getByRole('button', { name: '长篇拆章预览与导入', exact: true })
    if (!(await button.isVisible())) await menu.click()
    await button.click()
  }
  await openImport()
  await workspace.getByLabel('选择长篇TXT或Markdown（20MB内）').setInputFiles(inputFile)
  await expect(mapping.getByRole('option')).toHaveCount(4)
  expect(await manuscript(baselineId)).toEqual(baseline)
  expect(await chapters()).toHaveLength(1)
  await workspace.getByRole('combobox', { name: '本段导入方式', exact: true }).selectOption('skip')
  await mapping.selectOption('1')
  await workspace.getByRole('button', { name: '合并下一段（沿用本段映射）', exact: true }).click()
  await mappingTitle.fill('渡口与匣底手记')
  await expect(
    workspace.getByRole('textbox', { name: '本段原文（可将光标放到新的章界）', exact: true }),
  ).toHaveValue(crossing)
  await mapping.selectOption('2')
  await mappingTitle.fill('北岸的灯盏')
  await workspace.getByRole('button', { name: '本段在追加顺序前移', exact: true }).click()
  await expect(appendOrder).toHaveText(['北岸的灯盏', '渡口与匣底手记'])
  await workspace.getByRole('button', { name: '保存章界与映射草稿', exact: true }).click()
  await expect(workspace.getByRole('status')).toContainText('章界和映射草稿已保存')
  await page.reload()
  await openImport()
  await workspace.getByText('已保存预览与导入回执', { exact: true }).click()
  await workspace.getByRole('button', { name: new RegExp(filename + ' · 未确认') }).click()
  await expect(mapping.getByRole('option')).toHaveCount(3)
  await expect(mapping).toContainText('渡口与匣底手记')
  await expect(mapping).toContainText('北岸的灯盏')
  await expect(appendOrder).toHaveText(['北岸的灯盏', '渡口与匣底手记'])
  const confirmResponse = page.waitForResponse(
    (response) => response.url().endsWith('/confirm') && response.request().method() === 'POST',
  )
  await workspace.getByRole('button', { name: '确认以上范围并导入（替换保留原稿）', exact: true }).click()
  const result = await confirmResponse
  expect(result.ok(), await result.text()).toBeTruthy()
  const receipt = await result.json()
  const hash = createHash('sha256').update(inputFile.buffer).digest('hex')
  await expect(workspace.getByRole('region', { name: '长篇导入回执', exact: true })).toContainText(
    '跳过 · 卷首／前言',
  )
  await expect(workspace.getByText(`原文件SHA-256：${hash}`, { exact: true })).toBeVisible()
  const imported = await chapters()
  expect(imported.map((row: { title: string }) => row.title)).toEqual([
    baselineTitle,
    '北岸的灯盏',
    '渡口与匣底手记',
  ])
  const [northChapter, crossingChapter] = imported.slice(1)
  const northSaved = await manuscript(northChapter.id)
  const crossingSaved = await manuscript(crossingChapter.id)
  expect(northSaved.content).toBe(north)
  expect(crossingSaved.content).toBe(crossing)
  expect(receipt.results).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        chapter_id: crossingChapter.id,
        start: foreword.length,
        end: foreword.length + crossing.length,
        revision: crossingSaved.revision,
        position: 3,
      }),
      expect.objectContaining({
        chapter_id: northChapter.id,
        start: foreword.length + crossing.length,
        end: oldManuscript.length,
        revision: northSaved.revision,
        position: 2,
      }),
    ]),
  )
  expect(await manuscript(baselineId)).toEqual(baseline)
  await workspace.getByRole('button', { name: '关闭导入工作区', exact: true }).click()
  await expect(editor.locator('.cm-line')).toHaveText(opening)
  await page.getByRole('button', { name: '章纲 · 作者章纲', exact: true }).click()
  await expect(page.getByText(outline, { exact: true })).toBeVisible()

  const referencesUrl = (chapterId: string) => `${branch}/chapters/${chapterId}/technique-references`
  const firstReferences = referencesUrl(baselineId)
  const northReferences = referencesUrl(northChapter.id)
  const oldMethod = '把信息拆成询问和有限交付'
  const newMethod = '先交一半信息，再让代价留下痕迹'
  await page.getByRole('button', { name: '资料书库', exact: true }).click()
  await page.getByText('＋ 导入本地资料', { exact: true }).click()
  await page.getByLabel('选择 TXT／Markdown／EPUB', { exact: true }).setInputFiles(inputFile)
  const reader = page.getByRole('textbox', { name: '资料原文', exact: true })
  await expect(reader).toHaveValue(oldManuscript)
  await reader.press('ControlOrMeta+A')
  await page
    .getByRole('textbox', { name: '参考学习目标', exact: true })
    .fill('分析有限交付如何留下下一步行动。')
  const sessionCreated = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/reference-sessions') && response.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '拆解这段写法', exact: true }).click()
  const reference = await read(await sessionCreated)
  const referenceUrl = `/api/reference-sessions/${reference.id}`
  expect(reference.payload.source.file_hash).toBe(hash)
  await expect(page.getByRole('heading', { name: '参考仿写工作区', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '添加手工写法卡', exact: true }).click()

  async function editCard(method: string, evidence: string) {
    const summary = page.getByText('编辑卡片 1：观察、解释、写法与证据', { exact: true })
    const details = summary.locator('..')
    if (!(await details.evaluate((node: HTMLDetailsElement) => node.open))) await summary.click()
    await details
      .getByRole('textbox', { name: '原文观察', exact: true })
      .fill(evidence === firstEvidence ? '先问用途，再收回地图。' : '保留另一半图，同时记下目的地。')
    await details
      .getByRole('textbox', { name: '作用解释（分析判断）', exact: true })
      .fill('读者知道还有行动空间，也看到了信息交换的限制。')
    await details.getByRole('textbox', { name: '可迁移写法', exact: true }).fill(method)
    await details
      .getByRole('textbox', { name: '适用条件', exact: true })
      .fill('人物可以部分给出信息，对方仍能行动。')
    await details.getByRole('textbox', { name: '逐字原文证据', exact: true }).fill(evidence)
    const replacements = details.getByRole('textbox', { name: /^须替换的内容/ })
    await replacements.fill('替换地图、渡口与双方身份，使用本书的旧匣和失踪线索。')
  }

  async function saveCard() {
    const saved = page.waitForResponse(
      (response) => response.url().endsWith(referenceUrl) && response.request().method() === 'PATCH',
    )
    await page.getByRole('button', { name: '保存本次规则与方案', exact: true }).click()
    const value = await read(await saved)
    await expect(page.getByRole('status').filter({ hasText: '已保存采用规则' })).toBeVisible()
    return value
  }

  async function pin(chapterId: string, method: string, evidence: string, revision: number) {
    const pinUrl = referencesUrl(chapterId)
    const chapterSelect = page.getByRole('combobox', { name: '选择引用章节', exact: true })
    if (!(await chapterSelect.isVisible()))
      await page.getByRole('button', { name: '定位卡片原文', exact: true }).click()
    await chapterSelect.selectOption(chapterId)
    await page
      .getByRole('textbox', { name: '本章怎么用（可选）', exact: true })
      .fill('开头先交代看得见的线索。')
    const response = page.waitForResponse((r) => r.url().endsWith(pinUrl) && r.request().method() === 'POST')
    await page.getByRole('button', { name: '引用到所选章节', exact: true }).click()
    const pinned = await read(await response)
    const start = oldManuscript.indexOf(evidence)
    const rows = await read(await page.request.get(pinUrl))
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({
      id: pinned.id,
      chapter_id: chapterId,
      session_id: reference.id,
      session_revision: revision,
      card: { technique: method, evidence },
      unit: { file_hash: hash },
      evidence_start: start,
      evidence_end: start + evidence.length,
      application_note: '开头先交代看得见的线索。',
    })
    return rows[0]
  }

  await editCard(oldMethod, firstEvidence)
  await page.getByRole('button', { name: /明确使用顶部当前工作版本/ }).click()
  await page.getByRole('combobox', { name: '本次目标章节', exact: true }).selectOption(baselineId)
  const firstCardVersion = await saveCard()
  await page.getByRole('button', { name: '定位卡片原文', exact: true }).click()
  await expect(page.locator('mark').filter({ hasText: firstEvidence })).toHaveText(firstEvidence)
  const oldPin = await pin(baselineId, oldMethod, firstEvidence, firstCardVersion.revision)

  const analyzeResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith(referenceUrl + '/actions') && response.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '拆解选段写法', exact: true }).click()
  const analyze = await read(await analyzeResponse)
  await page.getByRole('button', { name: '载入分析卡供编辑和选择（尚未采用）', exact: true }).click()
  expect((await read(await page.request.get(`/api/tasks/${analyze.id}`))).status).toBe('succeeded')
  await editCard(newMethod, secondEvidence)
  const newCardVersion = await saveCard()
  expect(newCardVersion.revision).toBeGreaterThan(firstCardVersion.revision)
  const frozenPins = [{ ...oldPin, current_session_revision: newCardVersion.revision }]
  expect(await read(await page.request.get(firstReferences))).toEqual(frozenPins)
  expect(await read(await page.request.get(northReferences))).toEqual([])
  await pin(northChapter.id, newMethod, secondEvidence, newCardVersion.revision)
  expect(await read(await page.request.get(firstReferences))).toEqual(frozenPins)

  await page.getByRole('button', { name: '写作', exact: true }).click()
  const pins = page.getByRole('region', { name: '本章引用手法', exact: true })
  async function showPin(chapterTitle: string, method: string, evidence: string) {
    await page.locator('.chapter-list button').filter({ hasText: chapterTitle }).click()
    const card = pins.getByRole('article', { name: method, exact: true })
    await expect(card).toBeVisible()
    const sourceLocation = page.waitForResponse(
      (r) =>
        r.url().includes('/technique-references/') &&
        r.url().endsWith('/source') &&
        r.request().method() === 'GET',
    )
    await card.getByRole('button', { name: '定位卡片原文', exact: true }).press('Enter')
    await expect(card.locator('mark')).toHaveText(evidence)
    await expect(card.locator(`[title="${hash}"]`)).toBeVisible()
    const source = await read(await sourceLocation)
    const start = oldManuscript.indexOf(evidence)
    expect(source.passages).toEqual([
      expect.objectContaining({ quote: evidence, start, end: start + evidence.length }),
    ])
    return card
  }
  const prior = await showPin(baselineTitle, oldMethod, firstEvidence)
  await expect(pins.getByText(newMethod, { exact: true })).toHaveCount(0)
  await expect(editor.locator('.cm-line')).toHaveText(opening)
  await expect(prior).toContainText('历史引用')
  const useCard = (card: typeof prior) =>
    card.getByRole('checkbox', { name: '本次写作使用此卡', exact: true })
  await expect(useCard(prior)).not.toBeChecked()
  await useCard(prior).check()
  await page
    .getByRole('textbox', { name: '情节或具体要求', exact: true })
    .fill('为桥头第一段提供一个可选写法。')
  const adviseResponse = page.waitForResponse(
    (r) => r.url().endsWith('/api/modules') && r.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '生成候选 →', exact: true }).click()
  const advise = await adviseResponse
  expect(advise.request().postDataJSON().technique_reference_ids).toEqual([oldPin.id])
  const adviceTask = await read(advise)
  await expect
    .poll(async () => (await read(await page.request.get(`/api/tasks/${adviceTask.id}`))).status)
    .toBe('succeeded')
  const current = await showPin('北岸的灯盏', newMethod, secondEvidence)
  await expect(useCard(current)).not.toBeChecked()
  await expect(pins.getByText(oldMethod, { exact: true })).toHaveCount(0)
  expect(await manuscript(baselineId)).toEqual(baseline)
  expect(await manuscript(northChapter.id)).toEqual(northSaved)

  await page.setViewportSize({ width: 320, height: 800 })
  await page.evaluate(() => {
    document.documentElement.style.fontSize = '200%'
  })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
  await page.screenshot({ path: info.outputPath('project-start-reuse-320-200.png'), fullPage: true })
})
