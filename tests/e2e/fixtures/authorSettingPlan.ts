import { expect, type Page } from '@playwright/test'

export async function authorSettingPlan(page: Page, title: string, includeWorldRules = false) {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const created = await page.request.post('/api/novels', { headers, data: { title } })
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
  if (includeWorldRules) {
    state.categories.find(({ id }: { id: string }) => id === 'world').items = [
      { id: 'key-rule', title: '赤钥交接规则', detail: '交接只能当面完成。' },
      { id: 'pending-rule', title: '未采用规则', detail: '钥匙不能染色。', adoption_state: 'pending' },
    ]
  }
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
      idempotency_key: `adopt-keeper-for-proof-${novel.branch_id}`,
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
  return { novel, branch, chapter, original, headers, readPlan }
}
