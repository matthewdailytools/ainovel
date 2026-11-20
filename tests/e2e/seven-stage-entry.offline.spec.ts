import { expect, test, type Page } from '@playwright/test'

const idea = '学徒靠铜铃的断痕寻找师姐。'
const startActions = (page: Page) => page.getByRole('navigation', { name: '创作入口', exact: true })
const runPanel = (page: Page) => page.getByRole('region', { name: '七台自动接力', exact: true })
const runButton = (page: Page) => runPanel(page).getByRole('button', { name: '启动七台接力' })

async function seed(page: Page, title: string, confirm = false) {
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const response = await page.request.post('/api/novels', { headers, data: { title } })
  expect(response.status()).toBe(201)
  const { id, branch_id }: { id: string; branch_id: string } = await response.json()
  let projectId: string | undefined
  if (confirm) {
    const signed = await page.request.put(`/api/branches/${branch_id}/conception/project-brief`, {
      headers,
      data: {
        base_revision: 0,
        idempotency_key: 'confirm-' + branch_id,
        idea,
        brief: {
          position: '用证据解释异象',
          track: '短篇悬疑',
          genre_ids: ['mystery'],
          audience: '偏好线索回读的读者',
          distinction: '推理依靠旧物与目击记录',
          length: '一卷两章',
          ending: '把铜铃还给师姐。',
        },
      },
    })
    expect(signed.ok(), await signed.text()).toBe(true)
    projectId = (await signed.json()).id
  }
  return { id, branchId: branch_id, projectId }
}

function observeStarts(page: Page) {
  const starts: unknown[] = []
  page.on('request', (request) => {
    if (request.method() === 'POST' && new URL(request.url()).pathname.endsWith('/seven-stage-runs'))
      starts.push(request.postDataJSON())
  })
  return starts
}

test('空工作台可选逐台创作，新建后直接打开可编辑的立项', async ({ page }) => {
  let ownNovel = ''
  await page.route('**/api/novels', async (route) => {
    const response = await route.fetch()
    if (route.request().method() === 'POST') {
      ownNovel = (await response.json()).id
      await route.fulfill({ response })
      return
    }
    const rows: { id: string }[] = await response.json()
    await route.fulfill({ response, json: rows.filter(({ id }) => id === ownNovel) })
  })
  await page.setViewportSize({ width: 320, height: 780 })
  const starts = observeStarts(page)
  await page.goto('/')
  const entry = startActions(page)
  await expect(entry.getByRole('button', { name: '从已完成阶段接力' })).toBeDisabled()
  await entry.getByRole('button', { name: '逐台创作' }).press('Enter')
  const title = page.getByRole('textbox', { name: '第一部作品的名字' })
  await expect(title).toBeFocused()
  await title.fill('入口测试 · 从空白进入立项')
  await page.getByRole('button', { name: '创建并进入立项台' }).press('Enter')
  const project = page.locator('#bench-project')
  await expect(project.getByRole('textbox', { name: '创作思路' })).toBeVisible()
  await expect(page.getByRole('combobox', { name: '作品', exact: true })).toHaveValue(ownNovel)
  await expect(
    page.getByRole('navigation', { name: '七台工作流' }).getByRole('button', { name: '立项台' }),
  ).toHaveAttribute('aria-current', 'location')
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)
  expect(starts).toEqual([])
})

test('创意入口要求空白工作版本，显式新建后才可提交一次接力', async ({ page }) => {
  const previous = await seed(page, '入口测试 · 已确认立项的原作品', true)
  const starts = observeStarts(page)
  await page.goto('/')
  const works = page.getByRole('combobox', { name: '作品', exact: true })
  await works.selectOption(previous.id)
  await startActions(page).getByRole('button', { name: '从创意开始', exact: true }).press('Enter')
  await expect(runPanel(page).getByRole('heading', { name: '七台自动接力' })).toBeFocused()
  await expect(runPanel(page).getByText(/此工作版本已有创作内容/)).toBeVisible()
  await expect(runButton(page)).toBeDisabled()
  expect(starts).toEqual([])
  await runPanel(page).getByRole('button', { name: '新建作品从创意开始' }).click()
  const title = page.getByRole('textbox', { name: '作品名称', exact: true })
  await expect(title).toBeFocused()
  await title.fill('入口测试 · 全新创意')
  const response = page.waitForResponse(
    (item) => item.request().method() === 'POST' && item.url().endsWith('/api/novels'),
  )
  await page.getByRole('button', { name: '创建并进入自动接力' }).press('Enter')
  const created = await response
  expect(created.status()).toBe(201)
  const { id, branch_id: branchId } = await created.json()
  await expect(works).toHaveValue(id)
  await expect(runPanel(page).getByLabel('接力起点')).toHaveValue('idea')
  await expect(runPanel(page).getByLabel('授权确认本次生成的规划产物')).not.toBeChecked()
  await runPanel(page).getByLabel('自动立项的创作思路').fill(idea)
  await runPanel(page).getByLabel('本次接力总预算（所选账户币种）').fill('1')
  expect(starts).toEqual([])
  const started = page.waitForResponse(
    (item) =>
      item.request().method() === 'POST' && item.url().endsWith(`/branches/${branchId}/seven-stage-runs`),
  )
  await runButton(page).press('Enter')
  expect((await started).status()).toBe(202)
  await expect(runPanel(page).getByText(/^立项 · 等待处理检查点$/)).toBeVisible()
  expect(starts).toEqual([
    expect.objectContaining({ start_from: 'idea', idea, auto_confirm_planning: false }),
  ])
  const unchanged = await page.request.get(`/api/branches/${previous.branchId}/conception/project-brief`)
  expect((await unchanged.json()).id).toBe(previous.projectId)
})

test('接力入口只给当前版本的已完成起点，切换作品不沿用旧起点', async ({ page }) => {
  const confirmed = await seed(page, '入口测试 · 已有立项', true)
  const blank = await seed(page, '入口测试 · 暂无接力起点')
  const starts = observeStarts(page)
  await page.goto('/')
  const works = page.getByRole('combobox', { name: '作品', exact: true })
  await works.selectOption(confirmed.id)
  const relay = startActions(page).getByRole('button', { name: '从已完成阶段接力' })
  await relay.press('Enter')
  const panel = runPanel(page)
  await expect(panel.getByLabel('接力起点')).toHaveValue('project')
  await expect(panel.getByRole('option', { name: '从创意开始', exact: true })).toHaveCount(0)
  await expect(panel.getByRole('option', { name: /从已确认立项接力/ })).toBeEnabled()
  await works.selectOption(blank.id)
  await relay.click()
  await expect(panel.getByText(/当前没有有效的接力起点/)).toBeVisible()
  await expect(panel.getByLabel('接力起点')).toHaveValue('')
  await expect(panel.getByRole('option', { name: /从已确认立项接力/ })).toHaveCount(0)
  await expect(runButton(page)).toBeDisabled()
  await expect(page.getByRole('radio', { name: /^半自动/ })).toBeVisible()
  await expect(page.getByRole('radio', { name: /^全自动/ })).toBeVisible()
  expect(starts).toEqual([])
  await works.selectOption(confirmed.id)
  await relay.click()
  await expect(panel.getByLabel('接力起点')).toHaveValue('project')
  await panel.getByLabel('本次接力总预算（所选账户币种）').fill('1')
  const started = page.waitForResponse(
    (item) =>
      item.request().method() === 'POST' &&
      item.url().endsWith(`/branches/${confirmed.branchId}/seven-stage-runs`),
  )
  await runButton(page).click()
  expect((await started).status()).toBe(202)
  expect(starts).toEqual([expect.objectContaining({ start_from: 'project', source_id: confirmed.projectId })])
})

test('新建创意作品回执迟到时保持后来选择的页面', async ({ page }) => {
  const initial = await seed(page, '入口测试 · 开始时已有稿', true)
  const starts = observeStarts(page)
  await page.goto('/')
  const works = page.getByRole('combobox', { name: '作品', exact: true })
  await works.selectOption(initial.id)
  await startActions(page).getByRole('button', { name: '从创意开始', exact: true }).click()
  await runPanel(page).getByRole('button', { name: '新建作品从创意开始' }).click()
  const title = '入口测试 · 迟到的创意作品'
  await page.getByRole('textbox', { name: '作品名称', exact: true }).fill(title)
  let release = () => {}
  const hold = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route('**/api/novels', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    const response = await route.fetch()
    await hold
    await route.fulfill({ response })
  })
  const request = page.waitForRequest(
    (item) => item.method() === 'POST' && item.url().endsWith('/api/novels'),
  )
  await page.getByRole('button', { name: '创建并进入自动接力' }).click()
  await request
  try {
    await page.getByRole('button', { name: '写作', exact: true }).click()
    await expect(page.getByRole('button', { name: '新建第一章', exact: true })).toBeVisible()
  } finally {
    release()
  }
  await expect(works.getByRole('option', { name: title })).toHaveCount(1)
  await expect(works).toHaveValue(initial.id)
  await expect(page.getByRole('button', { name: '新建第一章', exact: true })).toBeVisible()
  await expect(runPanel(page)).toHaveCount(0)
  expect(starts).toEqual([])
})
