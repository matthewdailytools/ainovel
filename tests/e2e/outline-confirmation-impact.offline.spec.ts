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
  await expect(changed.getByRole('listitem')).toHaveText(['第二章查账页 · 修改 · 结果'])
  await expect(candidates.getByRole('listitem')).toHaveText([`第一章到渡口 · 局部改写 · ${earlierCandidate}`])
  const confirm = page.getByRole('button', { name: '确认已保存大纲' })
  await expect(confirm).toBeEnabled()

  const laterCandidate = await writeCandidate('用一句短句强调守闸人借出的旧尺。')
  expect(laterCandidate).not.toBe(earlierCandidate)
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
  expect(unchanged.draft.plan.volumes[0].units[0].chapters[1].result).toBe('先核对登记人签名，再去找他。')

  const reread = preview.getByRole('button', { name: '重新读取大纲与影响' })
  await expect(reread).toBeFocused()
  await reread.press('Enter')
  await expect(candidates.getByRole('listitem')).toHaveCount(2)
  await expect(candidates).toContainText(earlierCandidate)
  await expect(candidates).toContainText(laterCandidate)
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
})
