import { expect, test } from '@playwright/test'

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
