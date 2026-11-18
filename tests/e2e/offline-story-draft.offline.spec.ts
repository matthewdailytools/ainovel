import { expect, test, type APIResponse, type Page } from '@playwright/test'
import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'

const cloud = process.env.AINOVEL_TEST_SYNC_URL ?? 'http://127.0.0.1:8888'
const quote = '😀渡口'
const prose = `在${quote}，船工当面交还旧铜铃。`
const privateA = '甲窗口仍待核对谁先开口。'
const privateB = '乙窗口暂记试改的铜铃颜色。'
const adopted = '由两名船工共同见证归还。'

test.skip(process.env.AINOVEL_SYNC_E2E !== '1', '使用隔离 Chromium 和临时云账本')

async function okay(response: APIResponse) {
  expect(response.ok(), await response.text()).toBe(true)
  return response.json()
}

async function openStory(tab: Page, id: string) {
  await tab.getByRole('combobox', { name: '离线作品', exact: true }).selectOption(id)
  const panel = tab.getByLabel('离线故事记录')
  await panel.getByText('故事记录', { exact: true }).press('Enter')
  return panel
}

test('伏笔台账草稿保留实写选区和并发依据，只有正式确认才进入云账本', async ({ page, context }) => {
  await page.goto('/offline/')
  await expect(page.getByText('离线页面就绪', { exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
  const { csrf } = await okay(await page.request.get('/api/session'))
  const headers = { 'X-Ainovel-CSRF': csrf }
  const { id, branch_id } = await okay(
    await page.request.post('/api/novels', { headers, data: { title: '台账草稿 · 渡口铜铃' } }),
  )
  const branch = `/api/branches/${branch_id}`
  const chapter = await okay(
    await page.request.post(branch + '/chapters', { headers, data: { title: '船工归铃' } }),
  )
  await okay(
    await page.request.put(`/api/chapters/${chapter.id}`, {
      headers,
      data: { content: prose, base_revision: 0 },
    }),
  )
  const pack = await okay(await page.request.get(branch + '/manuscript-package'))
  const current = pack.revisions.find((row: { revision: number }) => row.revision === 1)
  const service = `${cloud}/api/sync/works/${branch_id}`
  const device_id = 'web-story-draft'
  const { token } = await okay(
    await page.request.post(service + '/devices', {
      headers: { Authorization: 'Bearer e2e-test-only-provisioning-token' },
      data: { device_id },
    }),
  )
  await context.setOffline(true)
  await page.getByLabel('导入新离线作品').setInputFiles({
    name: '渡口合成基线.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(pack)),
  })
  const board = await openStory(page, id)
  const confirmName = '确认故事记录并保存到浏览器'
  const saved = '故事记录草稿已暂存到本机。'
  await board.getByRole('button', { name: '添加故事记录' }).click()
  await board.getByLabel('记录名称', { exact: true }).fill('归还旧铜铃')
  await board.getByRole('button', { name: confirmName }).click()
  await expect(board.getByText('已确认并保存故事记录。')).toBeVisible()
  const recordId = await board.getByRole('combobox', { name: '查看故事记录', exact: true }).inputValue()

  await board.getByRole('button', { name: '编辑所选故事记录' }).click()
  await board.getByRole('button', { name: '添加伏笔阶段' }).click()
  const resolved = board.getByRole('group', { name: '阶段 2', exact: true })
  await resolved.getByRole('combobox', { name: '阶段状态' }).selectOption('resolved')
  await resolved.getByLabel('阶段说明').fill(privateA)
  const start = prose.indexOf(quote)
  const end = start + quote.length
  await resolved.getByText('阶段原文证据 · 0 处', { exact: true }).click()
  await resolved.getByRole('combobox', { name: '原文证据章节', exact: true }).selectOption(chapter.id)
  await resolved.getByRole('button', { name: '读取证据章保存稿' }).click()
  await expect(resolved.getByLabel('可选取的保存稿')).toHaveValue(prose)
  await resolved.getByLabel('选区起点').fill(String(start))
  await resolved.getByLabel('选区终点').fill(String(end))
  await resolved.getByRole('button', { name: '附上所选原文' }).click()
  await expect(resolved.getByText('阶段原文证据 · 1 处', { exact: true })).toBeVisible()
  await expect(board.getByText(saved, { exact: true })).toBeVisible()
  await board.getByLabel('记录名称', { exact: true }).fill('')
  await expect(board.getByText(saved, { exact: true })).toBeVisible()

  async function reopen(tab: Page) {
    tab.once('dialog', (dialog) => dialog.accept())
    await tab.reload()
    await openStory(tab, id)
  }
  await reopen(page)
  const resumeDraft = board.getByRole('button', { name: '继续编辑本机故事记录草稿' })
  await expect(resumeDraft).toBeEnabled()
  await resumeDraft.press('Enter')
  await expect(board.getByLabel('记录名称', { exact: true })).toHaveValue('')
  await expect(board.getByRole('button', { name: confirmName })).toBeDisabled()
  await expect(resolved.getByLabel('阶段说明')).toHaveValue(privateA)
  await resolved.getByText('阶段原文证据 · 1 处', { exact: true }).click()
  await expect(
    resolved.getByText(`章节 ${chapter.id} · 保存修订 ${current.id} · 选区 ${start}–${end}`),
  ).toBeVisible()
  await expect(resolved.getByLabel('可选取的保存稿')).toHaveCount(0)
  await expect(board.getByText(saved, { exact: true })).toBeVisible()

  const owner = await page.evaluate(() => sessionStorage.getItem('ainovel-story-draft-window'))
  expect(owner).toBeTruthy()
  const other = await context.newPage()
  await other.addInitScript((stored) => {
    const key = 'ainovel-story-draft-window'
    if (stored && !sessionStorage.getItem(key)) sessionStorage.setItem(key, stored)
  }, owner)
  await other.goto('/offline/')
  const otherBoard = await openStory(other, id)
  const otherStage = otherBoard.getByRole('group', { name: '阶段 2', exact: true })
  await otherBoard.getByRole('button', { name: '取用另一窗口的故事记录草稿副本' }).click()
  await otherStage.getByLabel('阶段说明').fill(privateB)
  await expect(otherBoard.getByText(saved)).toBeVisible()
  await reopen(other)
  await otherBoard.getByRole('button', { name: '继续编辑本机故事记录草稿' }).click()
  await expect(otherStage.getByLabel('阶段说明')).toHaveValue(privateB)
  await otherBoard.getByLabel('记录名称', { exact: true }).fill('归还旧铜铃')
  await otherStage.getByLabel('阶段说明').fill(adopted)
  await otherBoard.getByRole('button', { name: confirmName }).click()
  await expect(otherBoard.getByText('已确认并保存故事记录。')).toBeVisible()
  await reopen(page)
  await board.getByRole('button', { name: '继续编辑本机故事记录草稿' }).click()
  await expect(resolved.getByLabel('阶段说明')).toHaveValue(privateA)
  await expect(board.getByText(/故事版本已变化/)).toBeVisible()
  await expect(board.getByRole('button', { name: confirmName })).toBeDisabled()

  const download = other.waitForEvent('download')
  await other.getByRole('button', { name: '导出稿件交换包' }).click()
  const exported = await readFile((await (await download).path())!, 'utf8')
  expect(exported).toContain(adopted)
  expect(exported).not.toContain(privateA)
  expect(exported).not.toContain(privateB)
  await context.setOffline(false)
  const posted: string[] = []
  other.on('request', (request) => {
    if (request.url() === service + '/push' && request.method() === 'POST')
      posted.push(request.postData() ?? '')
  })
  await other.getByText('作品云同步', { exact: true }).click()
  await other.getByLabel('同步服务地址').fill(cloud)
  await other.getByLabel('设备编号').fill(device_id)
  await other.getByLabel('设备凭据').fill(token)
  await other.getByRole('button', { name: '启用作品同步' }).click()
  const sync = other.getByRole('button', { name: '同步当前工作版本' })
  await sync.click()
  await expect.poll(() => posted.some((row) => row.includes(adopted))).toBe(true)
  await expect(sync).toBeEnabled()
  const bearer = { Authorization: `Bearer ${token}` }
  const { ledger_id } = await okay(await page.request.get(service + '/checkpoint', { headers: bearer }))
  const pulled = await okay(
    await page.request.get(service + '/pull', { headers: { ...bearer, 'X-Ainovel-Ledger': ledger_id } }),
  )
  const event = pulled.changes.find(
    (row: { payload?: { kind?: string } }) =>
      row.payload?.kind === 'story-version' && JSON.stringify(row.payload).includes(adopted),
  )
  expect(event.payload.version).toMatchObject({
    record_id: recordId,
    evidence: {
      stages: [
        [],
        [
          {
            chapter_id: chapter.id,
            revision_id: current.id,
            start,
            end,
            quote_sha256: createHash('sha256').update(quote).digest('hex'),
          },
        ],
      ],
    },
  })
  expect(JSON.stringify(event.payload)).not.toContain(quote)
  for (const withheld of [privateA, privateB]) {
    expect(JSON.stringify(pulled.changes)).not.toContain(withheld)
    expect(posted.join('\n')).not.toContain(withheld)
  }
})
