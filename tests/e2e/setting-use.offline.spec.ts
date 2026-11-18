import { expect, test } from '@playwright/test'

test('章纲中的人物条目经作者选取正式原文后才成为实际使用证据', async ({ page }) => {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const created = await page.request.post('/api/novels', { headers, data: { title: '检视设定正文证据' } })
  expect(created.ok(), await created.text()).toBe(true)
  const novel = await created.json()
  const branch = `/api/branches/${novel.branch_id}`
  const added = await page.request.post(branch + '/chapters', { headers, data: { title: '归还赤钥' } })
  expect(added.ok(), await added.text()).toBe(true)
  const chapter = await added.json()
  const original = '😀学徒递上赤钥，守铃人阿陶接过。'
  const saved = await page.request.put(`/api/chapters/${chapter.id}`, {
    headers,
    data: { base_revision: 0, content: original },
  })
  expect(saved.ok(), await saved.text()).toBe(true)
  const conception = branch + '/conception'
  const state = await (await page.request.get(conception)).json()
  state.categories.find(({ id }: { id: string }) => id === 'characters').items = [
    { id: 'keeper', title: '守铃人阿陶', detail: '阿陶负责收存归还的钥匙。' },
  ]
  const draft = await page.request.put(conception, {
    headers,
    data: {
      base_revision: state.revision,
      base_story_revision: state.story_revision,
      categories: state.categories,
    },
  })
  expect(draft.ok(), await draft.text()).toBe(true)
  const savedSettings = await draft.json()
  const adopted = await page.request.post(conception + '/confirm', {
    headers,
    data: {
      base_revision: savedSettings.revision,
      base_story_revision: savedSettings.story_revision,
      idempotency_key: 'adopt-keeper-for-proof',
      note: '采用人物身份。',
    },
  })
  expect(adopted.ok(), await adopted.text()).toBe(true)
  const { confirmation } = await adopted.json()
  const outline = branch + '/outline-workspace'
  const plan = await page.request.put(outline, {
    headers,
    data: {
      base_revision: 0,
      confirmation_id: confirmation.id,
      plan: {
        summary: '归还信物。',
        volumes: [
          {
            id: 'first-volume',
            title: '第一卷',
            summary: '守铃人收存信物。',
            units: [
              {
                id: 'first-unit',
                title: '归还',
                summary: '沿旧约交接。',
                chapters: [
                  {
                    chapter_id: chapter.id,
                    goal: '归还钥匙。',
                    obstacle: '要查对来者身份。',
                    turn: '阿陶发现钥齿的记号。',
                    result: '钥匙交回。',
                    setting_ids: ['keeper'],
                  },
                ],
              },
            ],
          },
        ],
      },
    },
  })
  expect(plan.ok(), await plan.text()).toBe(true)
  const confirmed = await page.request.post(outline + '/confirm', {
    headers,
    data: { base_revision: (await plan.json()).draft.revision },
  })
  expect(confirmed.ok(), await confirmed.text()).toBe(true)
  const readPlan = async () => (await page.request.get(outline)).json()
  expect((await readPlan()).settings).toEqual([
    expect.objectContaining({ chapter_id: chapter.id, evidence: null }),
  ])

  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  const editor = page.getByRole('textbox', { name: '章节正文', exact: true })
  await expect(editor).toHaveText(original)
  const title = page.getByText('设定的正文使用证据', { exact: true })
  await title.press('Enter')
  const panel = title.locator('..')
  const select = panel.getByRole('combobox', { name: '本章引用的设定', exact: true })
  await select.selectOption('keeper')
  const approve = panel.getByRole('button', { name: '确认该选区为正文证据', exact: true })
  await expect(approve).toBeDisabled()
  await editor.press('Control+End')
  await editor.press('Control+Shift+Home')
  await expect(panel.locator('blockquote')).toHaveText(original)
  await approve.press('Enter')
  await expect(panel.getByRole('status')).toContainText('已确认当前正文使用该设定。')
  const proof = panel.getByText('守铃人阿陶 · 当前正文证据', { exact: true })
  await proof.press('Enter')
  await expect(proof.locator('..').locator('blockquote')).toHaveText(original)
  const useHistory = await (await page.request.get(`/api/chapters/${chapter.id}/setting-uses`)).json()
  expect(useHistory.records).toEqual([
    expect.objectContaining({
      quote: original,
      status: 'current',
      version: expect.objectContaining({
        item_id: 'keeper',
        evidence: expect.objectContaining({ start: 0, end: original.length }),
      }),
    }),
  ])
  expect((await readPlan()).settings).toEqual([
    expect.objectContaining({
      chapter_id: chapter.id,
      author_evidence: expect.objectContaining({ quote: original, status: 'current' }),
    }),
  ])
  await page.getByRole('button', { name: '大纲与台账', exact: true }).press('Enter')
  const actual = page.getByRole('listitem').filter({ hasText: '作者已确认正文使用' })
  await expect(actual).toContainText(original)

  await page.getByRole('button', { name: '写作', exact: true }).press('Enter')
  await title.press('Enter')
  await proof.press('Enter')
  page.once('dialog', (confirmation) => void confirmation.accept())
  await proof.locator('..').getByRole('button', { name: '撤回这条正文证据', exact: true }).press('Enter')
  await expect(panel.getByRole('status')).toContainText('已撤回正文使用证据。')
  const withdrawn = panel.getByText('守铃人阿陶 · 已撤回', { exact: true })
  await withdrawn.press('Enter')
  await expect(withdrawn.locator('..').locator('blockquote')).toHaveText(original)
  expect((await readPlan()).settings[0]).not.toHaveProperty('author_evidence')
})
