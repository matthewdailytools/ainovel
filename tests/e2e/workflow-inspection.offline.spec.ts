import { expect, test } from '@playwright/test'

test('全书篇幅核对直接显示未达标的章目标和采用修订', async ({ page }) => {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const work = await page.request.post('/api/novels', { headers, data: { title: '拾铃 · 各层篇幅' } })
  expect(work.ok(), await work.text()).toBe(true)
  const { id: novelId, branch_id: branchId } = await work.json()
  const branch = `/api/branches/${branchId}`
  const created = await page.request.post(`${branch}/chapters`, { headers, data: { title: '门内' } })
  expect(created.ok(), await created.text()).toBe(true)
  const { id: chapterId } = await created.json()
  const saved = await page.request.put(`/api/chapters/${chapterId}`, {
    headers,
    data: { base_revision: 0, content: '## 标题\n> **铃响。**' },
  })
  expect(saved.ok(), await saved.text()).toBe(true)

  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novelId)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  const panel = page.getByRole('region', { name: '十处对账体检', exact: true })
  const scope = panel.getByRole('combobox', { name: '检查范围', exact: true })
  const minimum = panel.getByLabel('有效字符下限', { exact: true })
  const saveTarget = panel.getByRole('button', { name: '保存篇幅目标', exact: true })
  await minimum.fill('1')
  await saveTarget.click()
  await expect(panel.getByText('当前采用修订 1', { exact: true })).toBeVisible()
  await scope.selectOption('chapter')
  await panel.getByRole('combobox', { name: '选择章节', exact: true }).selectOption(chapterId)
  await expect(panel.getByText('尚未采用数值目标', { exact: true })).toBeVisible()
  await minimum.fill('80')
  await saveTarget.click()
  await expect(panel.getByText('当前采用修订 1', { exact: true })).toBeVisible()
  await scope.selectOption('book')
  await panel.getByText('本次依据与未核查项', { exact: true }).click()
  const length = panel.getByRole('listitem').filter({ hasText: '09 字数、章数与篇幅' })
  await expect(length.getByText('章 · 门内', { exact: true })).toBeVisible()
  await expect(length.getByText('目标修订 1 · 有效字符下限 80', { exact: true })).toBeVisible()
  await expect(
    length.getByText('1 章、3 个有效字符。正文字符数超出冻结目标。', { exact: true }),
  ).toBeVisible()
  await expect(panel.getByText('已保存体检单', { exact: true })).toHaveCount(0)
  const completion = page.getByRole('region', { name: '完本与交付', exact: true })
  await completion.getByRole('button', { name: '核对最新完本依据' }).click()
  await expect(completion.getByText(/1 个章或卷未达到各自已采用的篇幅目标/)).toBeVisible()
})

test('第 08 项区分章纲计划与采纳稿，按固定手法卡版本读取本机原文', async ({ page }) => {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const work = await page.request.post('/api/novels', { headers, data: { title: '拾铃 · 手法对账' } })
  expect(work.ok(), await work.text()).toBe(true)
  const { id: novelId, branch_id: branchId } = await work.json()
  const branch = `/api/branches/${branchId}`
  const chapter = await page.request.post(`${branch}/chapters`, { headers, data: { title: '拾铃' } })
  expect(chapter.ok(), await chapter.text()).toBe(true)
  const { id: chapterId } = await chapter.json()
  const chapterUrl = `/api/chapters/${chapterId}`
  const content = '学徒在旧门外发现半枚铃痕。'
  const saved = await page.request.put(chapterUrl, { headers, data: { base_revision: 0, content } })
  expect(saved.ok(), await saved.text()).toBe(true)
  const { revision } = await saved.json()

  const original = '守夜人只认出一段绳结。'
  const newer = '他到河下游找结绳的人。'
  const quote = original + newer
  const upload = await page.request.post('/api/library/import', {
    headers,
    multipart: { file: { name: '手法对账样本.txt', mimeType: 'text/plain', buffer: Buffer.from(quote) } },
  })
  expect(upload.ok(), await upload.text()).toBe(true)
  const { id: bookId } = await upload.json()
  const [unit] = await (await page.request.get(`/api/library/books/${bookId}/units`)).json()
  const reference = await page.request.post('/api/reference-sessions', {
    headers,
    data: {
      unit_id: unit.id,
      start: 0,
      end: quote.length,
      quote,
      goal: '观察局部收获怎样引出下一步',
      branch_id: branchId,
      chapter_id: chapterId,
    },
  })
  expect(reference.ok(), await reference.text()).toBe(true)
  const { id: sessionId, payload } = await reference.json()
  const referenceUrl = `/api/reference-sessions/${sessionId}`
  const card = {
    id: 'partial-clue',
    observation: '角色仅看清局部。',
    interpretation: '局部线索仍可指向具体行动。',
    technique: '先辨认一条局部线索',
    conditions: '此前还不能确认来历。',
    exceptions: '替换人物、地点和道具。',
    evidence: original,
  }
  const edit = { goal: payload.goal, branch_id: branchId, chapter_id: chapterId }
  const cardSaved = await page.request.patch(referenceUrl, {
    headers,
    data: { ...edit, base_revision: 0, cards: [card] },
  })
  expect(cardSaved.ok(), await cardSaved.text()).toBe(true)
  const [{ version }] = await (await page.request.get(`${branch}/reference-cards`)).json()
  const outlineUrl = `${branch}/outline-workspace`
  const outline = await page.request.put(outlineUrl, {
    headers,
    data: {
      base_revision: 0,
      plan: {
        summary: '学徒循铃痕找到旧铺。',
        volumes: [
          {
            id: 'volume',
            title: '拾铃卷',
            summary: '查明铃的来处。',
            units: [
              {
                id: 'unit',
                title: '认铃',
                summary: '辨认刻痕。',
                chapters: [
                  {
                    chapter_id: chapterId,
                    goal: '看清刻痕',
                    obstacle: '痕迹不全',
                    turn: '认出旧铺常用刻法',
                    result: '找到可问的人',
                    reference_cards: [
                      {
                        session_id: sessionId,
                        card_id: card.id,
                        revision: version.revision,
                        version_id: version.id,
                      },
                    ],
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
  const { draft } = await outline.json()
  const signedOutline = await page.request.post(`${outlineUrl}/confirm`, {
    headers,
    data: { base_revision: draft.revision },
  })
  expect(signedOutline.ok(), await signedOutline.text()).toBe(true)
  const { confirmed } = await signedOutline.json()
  const cardChanged = await page.request.patch(referenceUrl, {
    headers,
    data: { ...edit, base_revision: version.revision, cards: [{ ...card, evidence: newer }] },
  })
  expect(cardChanged.ok(), await cardChanged.text()).toBe(true)

  const queued = await page.request.post('/api/modules', {
    headers,
    data: {
      novel_id: novelId,
      branch_id: branchId,
      chapter_id: chapterId,
      base_revision: revision,
      action: 'writing.rewrite_selection',
      instruction: '聚焦能够行动的线索。',
      selection_start: 0,
      selection_end: content.length,
      selection_text: content,
      outline_revision_id: confirmed.id,
      account: 'offline',
    },
  })
  expect(queued.ok(), await queued.text()).toBe(true)
  const { id: taskId } = await queued.json()
  const taskUrl = `/api/tasks/${taskId}`
  await expect.poll(async () => (await (await page.request.get(taskUrl)).json()).status).toBe('succeeded')
  const { result: taskResult } = await (await page.request.get(taskUrl)).json()
  const applied = await page.request.post(`/api/artifacts/${taskResult.artifact_id}/accept`, {
    headers,
    data: { idempotency_key: 'inspection-08-browser', candidate_revision: 0 },
  })
  expect(applied.ok(), await applied.text()).toBe(true)
  const accepted = await applied.json()

  const evidenceUrl = `${branch}/reference-cards/${version.id}/evidence`
  const requested: string[] = []
  page.on('request', (request) => {
    if (request.url().includes('/reference-cards/') && request.url().endsWith('/evidence'))
      requested.push(new URL(request.url()).pathname)
  })
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novelId)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  const panel = page.getByRole('region', { name: '十处对账体检', exact: true })
  await panel.getByText('本次依据与未核查项', { exact: true }).click()
  const planned = panel.getByRole('region', { name: '章纲计划手法卡', exact: true })
  const adopted = panel.getByRole('region', { name: '已采纳稿的手法卡依据', exact: true })
  await expect(planned.getByText('拾铃 · 手法卡 partial-clue · 修订 1', { exact: true })).toBeVisible()
  await expect(adopted.getByText(`章节编号 ${chapterId}`, { exact: true })).toBeVisible()
  await expect(adopted.getByText(`章稿修订 ${accepted.revision}`, { exact: true })).toBeVisible()
  await page.setViewportSize({ width: 420, height: 900 })
  expect(await adopted.evaluate((section) => section.scrollWidth <= section.clientWidth)).toBe(true)
  expect(requested).toEqual([])
  await adopted.getByText('查看手法卡原文', { exact: true }).press('Enter')
  await expect(adopted.locator('blockquote')).toHaveText(original)
  expect(requested).toEqual([evidenceUrl])
  await expect(adopted.getByText(newer, { exact: true })).toHaveCount(0)

  await panel.getByRole('button', { name: '生成体检单', exact: true }).click()
  const report = panel.getByRole('region', { name: '体检单结果', exact: true })
  const recorded = report.getByRole('region', { name: '已采纳稿的手法卡依据', exact: true })
  await expect(recorded.getByText(`章稿修订 ${accepted.revision}`, { exact: true })).toBeVisible()
  const edited = await page.request.put(chapterUrl, {
    headers,
    data: { base_revision: accepted.revision, content: '作者重新写了取得铃痕的过程。' },
  })
  expect(edited.ok(), await edited.text()).toBe(true)
  await panel.getByRole('button', { name: '重新读取依据', exact: true }).click()
  await expect(report.getByText('部分依据已更新，过期项待复检', { exact: true })).toBeVisible()
  await expect(recorded.getByText(`章稿修订 ${accepted.revision}`, { exact: true })).toBeVisible()
  expect(requested).toEqual([evidenceUrl])
  const item = report.getByRole('listitem').filter({ hasText: '08 已确认引用与改稿影响' })
  await item.getByText('对照依据', { exact: true }).click()
  await expect(item.locator('pre')).toContainText(taskResult.artifact_id)
  await expect(item.locator('pre')).not.toContainText(original)
  await page.route(`**${evidenceUrl}`, (route) =>
    route.fulfill({ json: { version_id: version.id, source_state: 'metadata_only', excerpt: null } }),
  )
  await recorded.getByText('查看手法卡原文', { exact: true }).click()
  await expect(recorded.getByRole('status')).toHaveText('本机没有这一手法卡修订的可核验原文。')
  expect(requested).toEqual([evidenceUrl, evidenceUrl])
})

test('语义候选显示送审证据，作者另行裁定后签入正式体检', async ({ page }) => {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const created = await page.request.post('/api/novels', { headers, data: { title: '遗铃 · 语义对账' } })
  expect(created.ok()).toBe(true)
  const novel = await created.json()
  const chapter = await (
    await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
      headers,
      data: { title: '拾铃' },
    })
  ).json()
  const content = '😀学徒拾起铜铃。'
  const saved = await page.request.put(`/api/chapters/${chapter.id}`, {
    headers,
    data: { base_revision: 0, content },
  })
  expect(saved.ok(), await saved.text()).toBe(true)
  const route = `/api/branches/${novel.branch_id}/workflow-inspections`
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  const panel = page.getByRole('region', { name: '十处对账体检', exact: true })
  await panel.getByText('AI 语义对账候选 · 01、02、10', { exact: true }).click()
  await panel.getByRole('button', { name: '生成语义对账候选' }).click()
  const candidate = panel.getByRole('region', { name: '语义候选证据', exact: true })
  await expect(candidate.getByText(/实际送审 1 章/)).toBeVisible()
  await expect(candidate.getByText('01 立项及作者约定 · 证据不足')).toBeVisible()
  expect((await (await page.request.get(route)).json()).latest).toBeNull()
  await candidate.getByRole('button', { name: '载入 01 依据供作者核对' }).click()
  const verdict = panel.getByRole('combobox', { name: '作者判定', exact: true })
  await expect(verdict).toHaveValue('')
  await verdict.selectOption('green')
  await panel.getByLabel('对照的章纲、约定或规则').fill('本轮作者原约定仅要求拾铃。')
  await panel
    .getByRole('textbox', { name: '判定理由', exact: true })
    .fill('核对作者原约定，确认当前保存稿满足拾铃目标。')
  await panel.getByRole('checkbox', { name: /已核对候选的冲突或证据缺口/ }).check()
  await panel.getByRole('button', { name: '生成体检单' }).click()
  const result = panel.getByRole('region', { name: '体检单结果' })
  await expect(result.getByText('依据仍为当前版本')).toBeVisible()
  const current = (await (await page.request.get(route)).json()).latest
  expect(current.items[0].author_review).toMatchObject({
    status: 'green',
    author_overrides_candidate: true,
    candidate_result: 'unknown',
    evidence: [{ chapter_id: chapter.id, quote: content }],
  })
})

test('十处对账保留作者选区和修订，正文改变后显示原判定与过期状态', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const created = await page.request.post('/api/novels', {
    headers,
    data: { title: '后台验证 · 遗铃十处对账' },
  })
  expect(created.ok()).toBe(true)
  const novel = await created.json()
  const chapterResponse = await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
    headers,
    data: { title: '拾铃' },
  })
  expect(chapterResponse.ok()).toBe(true)
  const chapter = await chapterResponse.json()
  const text = '😀学徒拾起铜铃。'
  const saved = await page.request.put(`/api/chapters/${chapter.id}`, {
    headers,
    data: { base_revision: 0, content: text },
  })
  expect(saved.ok()).toBe(true)
  const revision = (await saved.json()).revision

  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  const panel = page.getByRole('region', { name: '十处对账体检', exact: true })
  await panel.getByText('本次依据与未核查项', { exact: true }).click()
  await expect(panel.getByRole('button', { name: /^核对 \d{2}$/ })).toHaveCount(10)
  await panel.getByRole('button', { name: '核对 03' }).click()
  const verdict = panel.getByRole('combobox', { name: '作者判定', exact: true })
  await expect(verdict.locator('option[value="not_applicable"]')).toHaveCount(0)
  await verdict.selectOption('green')
  await panel.getByLabel('对照的章纲、约定或规则', { exact: true }).fill('人物档案中的学徒身份。')
  await panel.getByLabel('判定理由', { exact: true }).fill('本章以学徒称谓写明动作主体。')
  await panel.getByRole('combobox', { name: '引用章节', exact: true }).selectOption(chapter.id)
  const excerpt = panel.getByRole('textbox', { name: '保存稿（选择要引用的文字）', exact: true })
  await expect(excerpt).toHaveValue(text)
  await excerpt.press('ControlOrMeta+a')
  await panel.getByRole('button', { name: '引用选区' }).press('Enter')
  await expect(panel.getByRole('button', { name: '移除引文 1' })).toBeVisible()
  await panel.getByLabel('章数', { exact: true }).fill('1')
  await panel.getByLabel('有效字符下限', { exact: true }).fill('1')
  await expect(panel.getByRole('button', { name: '生成体检单' })).toBeDisabled()
  await panel.getByRole('button', { name: '保存篇幅目标', exact: true }).click()
  await expect(panel.getByText('当前采用修订 1', { exact: true })).toBeVisible()
  await expect(
    panel.getByRole('group', { name: '此范围的篇幅目标' }).getByText(/符合本次配置目标/),
  ).toBeVisible()
  const createdReport = page.waitForResponse(
    (response) => response.request().method() === 'POST' && response.url().endsWith('/workflow-inspections'),
  )
  await panel.getByRole('button', { name: '生成体检单' }).click()
  const reportResponse = await createdReport
  expect(reportResponse.ok(), await reportResponse.text()).toBe(true)
  const report = await reportResponse.json()
  const review = report.items.find((item: { check_id: string }) => item.check_id === '03')
  expect(review.author_review.evidence).toEqual([
    {
      chapter_id: chapter.id,
      revision,
      start: 0,
      end: text.length,
      quote: text,
    },
  ])
  const result = panel.getByRole('region', { name: '体检单结果', exact: true })
  await expect(result.getByText('依据仍为当前版本')).toBeVisible()
  await expect(result.getByText(/作者核对 · 通过/)).toBeVisible()
  await panel.getByLabel('章数', { exact: true }).fill('2')
  await panel.getByRole('button', { name: '保存篇幅目标', exact: true }).click()
  await expect(panel.getByText('当前采用修订 2', { exact: true })).toBeVisible()
  await expect(result.getByText('部分依据已更新，过期项待复检')).toBeVisible()
  const length = result.getByRole('listitem').filter({ hasText: '09 字数、章数与篇幅' })
  await expect(length.getByText('原判定 通过')).toBeVisible()

  const changed = await page.request.put(`/api/chapters/${chapter.id}`, {
    headers,
    data: { base_revision: revision, content: '师姐举起铜铃。' },
  })
  expect(changed.ok()).toBe(true)
  await panel.getByRole('button', { name: '重新读取依据' }).click()
  await expect(result.getByText('部分依据已更新，过期项待复检')).toBeVisible()
  const identity = result.getByRole('listitem').filter({ hasText: '03 人物身份、关系与状态' })
  await expect(identity.getByText('原判定 通过')).toBeVisible()
  await expect(identity.locator('blockquote')).toContainText(text)
})
