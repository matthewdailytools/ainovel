import { expect, test, type APIResponse } from '@playwright/test'

async function checked(response: APIResponse) {
  expect(response.ok(), await response.text()).toBe(true)
  return response.json()
}

test('确认新大纲前分别显示实际章纲变化与旧稿候选，并对新增的冻结候选要求复核', async ({ page }) => {
  await page.goto('/')
  const { csrf } = await checked(await page.request.get('/api/session'))
  const headers = { 'X-Ainovel-CSRF': csrf }
  const title = '隔离验收 · 只修改第二章章纲'
  const work = await checked(await page.request.post('/api/novels', { headers, data: { title } }))
  const branch = `/api/branches/${work.branch_id}`
  const chapters = []
  for (const [chapterTitle, content] of [
    ['第一章到渡口', '学徒借旧尺核对公开刻度。'],
    ['第二章查账页', '守闸人展开登记簿。'],
  ]) {
    const made = await checked(
      await page.request.post(branch + '/chapters', { headers, data: { title: chapterTitle } }),
    )
    const saved = await checked(
      await page.request.put(`/api/chapters/${made.id}`, {
        headers,
        data: { base_revision: 0, content },
      }),
    )
    chapters.push(saved)
  }
  const [first, second] = chapters
  const outline = branch + '/outline-workspace'
  const original = await checked(
    await page.request.put(outline, {
      headers,
      data: {
        base_revision: 0,
        plan: {
          summary: '把复测结果同登记簿逐项核对。',
          volumes: [
            {
              id: 'volume',
              title: '渡口卷',
              summary: '还原首次复测。',
              units: [
                {
                  id: 'unit',
                  title: '先测再查',
                  summary: '先取得记录，再从账页追人。',
                  chapters: [
                    {
                      chapter_id: first.id,
                      goal: '确认公开刻度',
                      obstacle: '没有干净的尺',
                      turn: '守闸人借出旧尺',
                      result: '抄下复测读数',
                    },
                  ],
                },
              ],
            },
            {
              id: 'ledger-volume',
              title: '登记卷',
              summary: '查清登记人的去向。',
              units: [
                {
                  id: 'ledger-unit',
                  title: '找人',
                  summary: '从账页找到签字的人。',
                  chapters: [
                    {
                      chapter_id: second.id,
                      goal: '核对账页',
                      obstacle: '印章模糊',
                      turn: '找到封底编号',
                      result: '找到上次登记人',
                    },
                  ],
                },
              ],
            },
          ],
        },
      },
    }),
  )
  const active = await checked(
    await page.request.post(outline + '/confirm', {
      headers,
      data: { base_revision: original.draft.revision },
    }),
  )

  const inspection = branch + '/workflow-inspections'
  const completion = branch + '/book-completion'
  async function sign(scopeId?: string) {
    const { preview } = await checked(
      await page.request.get(inspection, {
        params: scopeId ? { scope: 'volume', scope_id: scopeId } : { scope: 'book' },
      }),
    )
    const chapter = scopeId === 'ledger-volume' ? second : first
    return checked(
      await page.request.post(inspection, {
        headers,
        data: {
          scope: preview.scope,
          scope_id: preview.scope_id,
          basis_sha256: preview.basis_sha256,
          length_targets: { min_chars: 1, expected_chapters: preview.chapters.length },
          author_reviews: preview.items
            .filter((row: { status: string }) => row.status === 'pending')
            .map(({ check_id }: { check_id: string }) => ({
              check_id,
              status: 'green',
              basis_note: '核对章纲与保存稿。',
              reason: '复测线索与正文一致。',
              evidence: [
                {
                  chapter_id: chapter.id,
                  revision: chapter.revision,
                  start: 0,
                  end: chapter.content.length,
                  quote: chapter.content,
                },
              ],
            })),
        },
      }),
    )
  }
  const unaffected = await sign('volume')
  const affectedVolume = await sign('ledger-volume')
  const affectedBook = await sign()
  const { basis_sha256 } = await checked(await page.request.get(completion))
  const evidence = (chapter: typeof first) => [
    {
      chapter_id: chapter.id,
      revision: chapter.revision,
      start: 0,
      end: chapter.content.length,
      quote: chapter.content,
    },
  ]
  const delivery = await checked(
    await page.request.post(completion + '/confirm', {
      headers,
      data: {
        basis_sha256,
        read_note: '已通读保存稿。',
        author_decisions: {
          reading: 'verified',
          ending: 'verified',
          characters: 'verified',
          promises: 'author_decision',
        },
        ending: { note: '登记编号已核实，两章的主要目标兑现。', sources: evidence(second) },
        characters: { note: '学徒与守闸人完成交接。', sources: evidence(first) },
        promises: { note: '作者确认此稿未采用单列的伏笔承诺。' },
        intro: '学徒到渡口调查一次复测。',
        spoiler_scope: 'opening',
        sample_chapter_ids: [first.id],
        materials_reviewed: true,
      },
    }),
  )

  async function writeCandidate(instruction: string) {
    const task = await checked(
      await page.request.post('/api/modules', {
        headers,
        data: {
          action: 'writing.rewrite_selection',
          novel_id: work.id,
          branch_id: work.branch_id,
          chapter_id: first.id,
          base_revision: first.revision,
          selection_start: 0,
          selection_end: first.content.length,
          selection_text: first.content,
          outline_revision_id: active.confirmed.id,
          instruction,
          account: 'offline',
        },
      }),
    )
    const status = async () => checked(await page.request.get('/api/tasks/' + task.id))
    await expect.poll(async () => (await status()).status).toBe('succeeded')
    return (await status()).result.artifact_id as string
  }
  const earlierCandidate = await writeCandidate('只改语序，保留复测动作。')
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(work.id)
  await page.getByRole('button', { name: '大纲与台账', exact: true }).click()
  await page.getByRole('textbox', { name: '结果', exact: true }).nth(1).fill('先核对登记人签名，再去找他。')
  await page.getByRole('button', { name: '保存大纲草稿' }).click()
  const preview = page.getByRole('region', { name: '大纲确认影响', exact: true })
  const changed = preview.getByRole('region', { name: '章纲内容直接变化', exact: true })
  const candidates = preview.getByRole('region', { name: '旧大纲候选', exact: true })
  const reports = preview.getByRole('region', { name: '受影响报告', exact: true })
  await expect(changed.getByRole('listitem')).toHaveText(['第二章查账页 · 修改 · 结果'])
  await expect(candidates.getByRole('listitem')).toHaveText([`第一章到渡口 · 局部改写 · ${earlierCandidate}`])
  await expect(reports.getByRole('listitem').filter({ hasText: affectedVolume.id })).toContainText(
    '卷 · 登记卷',
  )
  await expect(reports).toContainText(affectedBook.id)
  await expect(reports.getByRole('listitem').filter({ hasText: delivery.id })).toContainText(
    '完本记录 · 全书',
  )
  await expect(reports).not.toContainText(unaffected.id)
  await expect(
    reports.getByRole('listitem').filter({ hasText: /检查项 (?:\d{2}、)*09(?:、|$)/ }),
  ).toHaveCount(0)
  const confirm = page.getByRole('button', { name: '确认已保存大纲' })
  await expect(confirm).toBeEnabled()

  const laterCandidate = await writeCandidate('用一句短句强调守闸人借出的旧尺。')
  expect(laterCandidate).not.toBe(earlierCandidate)
  const laterReport = await sign('ledger-volume')
  const refused = page.waitForResponse(
    (response) => response.request().method() === 'POST' && response.url().endsWith(outline + '/confirm'),
  )
  await confirm.press('Enter')
  const conflict = await refused
  expect(conflict.status()).toBe(409)
  expect((await conflict.json()).code).toBe('outline_impact_changed')
  await expect(preview.getByRole('alert')).toContainText('请重新核对后确认')
  await expect(confirm).toBeDisabled()
  const unchanged = await checked(await page.request.get(outline))
  expect(unchanged.confirmed.id).toBe(active.confirmed.id)
  expect(unchanged.draft.plan.volumes[1].units[0].chapters[0].result).toBe('先核对登记人签名，再去找他。')

  const reread = preview.getByRole('button', { name: '重新读取大纲与影响' })
  await expect(reread).toBeFocused()
  await reread.press('Enter')
  await expect(candidates.getByRole('listitem')).toHaveCount(2)
  await expect(candidates).toContainText(earlierCandidate)
  await expect(candidates).toContainText(laterCandidate)
  await expect(reports).toContainText(laterReport.id)
  await expect(confirm).toBeEnabled()
  await confirm.press('Enter')
  await expect(page.getByText('已确认大纲修订 4', { exact: true })).toBeVisible()
  const rejected = await page.request.post(`/api/artifacts/${earlierCandidate}/accept`, {
    headers,
    data: { candidate_revision: 0, idempotency_key: 'reject-original-outline-draft-at-adoption' },
  })
  expect(rejected.status()).toBe(409)
  expect((await rejected.json()).code).toBe('outline_changed')
  const preserved = await checked(await page.request.get('/api/artifacts/' + earlierCandidate))
  expect(preserved.basis.outline.id).toBe(active.confirmed.id)
  expect((await checked(await page.request.get('/api/chapters/' + first.id))).content).toBe(first.content)
  const oldVolume = await checked(await page.request.get(inspection + '/' + affectedVolume.id))
  expect(oldVolume.items.find((row: { check_id: string }) => row.check_id === '09').current_status).toBe(
    'green',
  )
  expect((await checked(await page.request.get(completion))).latest.current).toBe(false)

  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  const panel = page.getByRole('region', { name: '十处对账体检', exact: true })
  const result = panel.getByRole('region', { name: '体检单结果', exact: true })
  const pacing = result.getByRole('listitem').filter({ hasText: /10 节奏、回报与章节衔接/ })
  await expect(pacing.getByText('过期', { exact: true })).toBeVisible()
  await pacing.getByText('卷纲与单元节奏安排', { exact: true }).press('Enter')
  await expect(pacing.getByRole('region', { name: '登记卷', exact: true })).toContainText(
    '从账页找到签字的人。',
  )
})
