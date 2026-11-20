import { readFile } from 'node:fs/promises'
import { expect, test, type APIResponse, type Page } from '@playwright/test'
import type { ManuscriptPack } from '../../frontend/offlineStore'

const ownerKey = 'ainovel-outline-draft-window'
const saved = '大纲草稿已暂存到本机。'
const partial = '  初拟总纲：捡回渡口的木牌。😀  '
const first = '先核对木牌，从渡口查起。'
const scratchA = '甲窗草稿：将揭晓移到最后一章。'
const scratchB = '乙窗草稿：先排查错画的记号。'
const second = '由刻木人交代木牌的去向。'
const beforeSettings = '首次采用设定前写下的未确认大纲。'
const promiseTitle = '背面刻痕'
const pendingTitle = '  尚未排入大纲的记号😀  '
const pendingNotes = '  先记一笔，尚未安排。  '
const technique = '先辨认半个刻痕'
const confirmName = '确认大纲并保存到浏览器'

async function okay(response: APIResponse) {
  expect(response.ok(), await response.text()).toBe(true)
  return response.json()
}

async function openOutline(page: Page, projectId: string) {
  await page.getByRole('combobox', { name: '离线作品', exact: true }).selectOption(projectId)
  const panel = page.getByLabel('离线大纲台')
  await panel.getByText('大纲台', { exact: true }).press('Enter')
  return panel
}

async function restoreTabIdentity(page: Page, previous: string | null) {
  await page.addInitScript(
    ({ key, previous }) => {
      if (previous && !sessionStorage.getItem(key)) sessionStorage.setItem(key, previous)
    },
    { key: ownerKey, previous },
  )
}

async function exchange(page: Page): Promise<ManuscriptPack> {
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: '导出稿件交换包', exact: true }).click(),
  ])
  const path = await download.path()
  if (!path) throw new Error('缺少稿件交换文件。')
  return JSON.parse(await readFile(path, 'utf8'))
}

test('离线大纲草稿重开恢复四层输入和冻结依据，双窗只采用通过版本核对的一版', async ({ page, context }) => {
  await page.goto('/offline/')
  await expect(page.getByText('离线页面就绪', { exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)

  const { csrf } = await okay(await page.request.get('/api/session'))
  const headers = { 'X-Ainovel-CSRF': csrf }
  const { id, branch_id } = await okay(
    await page.request.post('/api/novels', { headers, data: { title: '大纲草稿 · 渡口木牌' } }),
  )
  const branch = `/api/branches/${branch_id}`
  const chapter = await okay(
    await page.request.post(branch + '/chapters', { headers, data: { title: '辨认渡口木牌' } }),
  )
  const sourceText = '😀船工把木牌推到灯下，只露出背面的一半刻痕。'
  const library = await okay(
    await page.request.post('/api/library/import', {
      headers,
      multipart: { file: { name: '木牌样本.txt', mimeType: 'text/plain', buffer: Buffer.from(sourceText) } },
    }),
  )
  const [unit] = await okay(await page.request.get(`/api/library/books/${library.id}/units`))
  const goal = '分析逐步交代实物证据的手法'
  const session = await okay(
    await page.request.post('/api/reference-sessions', {
      headers,
      data: { unit_id: unit.id, start: 0, end: sourceText.length, quote: sourceText, goal, branch_id },
    }),
  )
  await okay(
    await page.request.patch(`/api/reference-sessions/${session.id}`, {
      headers,
      data: {
        base_revision: 0,
        branch_id,
        goal,
        cards: [
          {
            id: 'wood-mark',
            observation: '先交给调查者部分证据',
            interpretation: '给后文核对保留空间',
            technique,
            conditions: '人物仍能调查后半处线索',
            exceptions: '替换参考文本的人物、道具与动作',
            evidence: '一半刻痕',
          },
        ],
      },
    }),
  )
  const [ready] = await okay(await page.request.get(branch + '/outline-workspace/reference-options'))
  expect(ready.version_id).toMatch(/^[a-f0-9]{64}$/)
  const seed = await okay(await page.request.get(branch + '/manuscript-package'))
  await context.setOffline(true)
  await page.getByLabel('导入新离线作品').setInputFiles({
    name: '渡口木牌合成基线.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(seed)),
  })
  const original = await openOutline(page, id)
  await original.getByRole('button', { name: '新建大纲', exact: true }).click()
  await original.getByRole('textbox', { name: '总纲', exact: true }).fill(partial)
  await original.getByRole('button', { name: '添加卷', exact: true }).click()
  await original.getByRole('textbox', { name: '卷纲', exact: true }).fill('  用旧账册当路线索引  ')
  await original.getByRole('button', { name: '添加单元', exact: true }).click()
  await original.getByRole('textbox', { name: '单元名称', exact: true }).fill('  查证刻痕  ')
  await original.getByRole('button', { name: '添加章纲', exact: true }).click()
  await original.getByRole('textbox', { name: '目标', exact: true }).fill('请船工辨认木牌')
  await original.getByText('手法卡摘要 · 0 项', { exact: true }).click()
  await original.getByRole('checkbox', { name: `${technique} · 修订 1`, exact: true }).check()
  await original.getByRole('textbox', { name: '新伏笔名称', exact: true }).fill(promiseTitle)
  await original.getByRole('textbox', { name: '计划说明', exact: true }).fill('由刻木人核对木牌背面。')
  await original.getByRole('button', { name: '添加新伏笔计划', exact: true }).click()
  await original.getByLabel('必须在回收窗口内兑现', { exact: true }).check()
  await original.getByLabel('我已核对必达承诺的新增、调整或撤销', { exact: true }).check()
  await original.getByRole('textbox', { name: '新伏笔名称', exact: true }).fill(pendingTitle)
  await original.getByRole('textbox', { name: '计划说明', exact: true }).fill(pendingNotes)
  await expect(original.getByText(saved, { exact: true })).toBeVisible()
  const early = await exchange(page)
  expect(early.outline_history).toBeUndefined()
  expect(JSON.stringify(early)).not.toContain(pendingTitle.trim())

  const owner = await page.evaluate((key) => sessionStorage.getItem(key), ownerKey)
  expect(owner).toBeTruthy()
  page.once('dialog', (dialog) => dialog.accept())
  await page.reload()
  const main = page
  const mainPanel = await openOutline(main, id)
  const summary = mainPanel.getByRole('textbox', { name: '总纲', exact: true })
  const continueOutline = mainPanel.getByRole('button', { name: '继续编辑本机大纲草稿', exact: true })
  await expect(continueOutline).toBeEnabled()
  await continueOutline.press('Enter')
  await expect(summary).toBeFocused()
  await expect(summary).toHaveValue(partial)
  await expect(mainPanel.getByRole('textbox', { name: '卷名称', exact: true })).toHaveValue('')
  await expect(mainPanel.getByRole('textbox', { name: '卷纲', exact: true })).toHaveValue(
    '  用旧账册当路线索引  ',
  )
  await expect(mainPanel.getByRole('textbox', { name: '单元名称', exact: true })).toHaveValue('  查证刻痕  ')
  await expect(mainPanel.getByRole('textbox', { name: '单元纲', exact: true })).toHaveValue('')
  await expect(mainPanel.getByRole('textbox', { name: '目标', exact: true })).toHaveValue('请船工辨认木牌')
  await expect(mainPanel.getByRole('textbox', { name: '结果', exact: true })).toHaveValue('')
  await expect(mainPanel.getByRole('textbox', { name: '新伏笔名称', exact: true })).toHaveValue(pendingTitle)
  await expect(mainPanel.getByRole('textbox', { name: '计划说明', exact: true })).toHaveValue(pendingNotes)
  await mainPanel.getByText('手法卡摘要 · 1 项', { exact: true }).click()
  await expect(mainPanel.getByRole('checkbox', { name: `${technique} · 修订 1`, exact: true })).toBeChecked()
  const window = mainPanel.getByRole('group', { name: promiseTitle, exact: true })
  await expect(window.getByRole('combobox', { name: '最迟回收', exact: true })).toHaveValue(chapter.id)
  await expect(window.getByLabel('必须在回收窗口内兑现', { exact: true })).toBeChecked()
  const signature = mainPanel.getByLabel('我已核对必达承诺的新增、调整或撤销', { exact: true })
  await expect(signature).not.toBeChecked()
  const confirm = mainPanel.getByRole('button', { name: confirmName, exact: true })
  await signature.check()
  await expect(confirm).toBeDisabled()
  await mainPanel.getByRole('button', { name: '清空尚未加入的伏笔输入', exact: true }).click()
  for (const [label, value] of [
    ['总纲', first],
    ['卷名称', '渡口旧木牌'],
    ['单元纲', '查清刻痕和旧账册如何对应'],
    ['阻碍', '木牌残缺'],
    ['转折', '账中找到另一半记号'],
    ['结果', '锁定当年的刻木人'],
  ]) {
    await mainPanel.getByRole('textbox', { name: label, exact: true }).fill(value)
  }
  await confirm.click()
  await expect(mainPanel.getByText('已确认并保存大纲修订。', { exact: true })).toBeVisible()
  await mainPanel.getByRole('button', { name: '编辑当前大纲', exact: true }).click()
  await summary.fill(scratchA)
  await expect(mainPanel.getByText(saved, { exact: true })).toBeVisible()

  const other = await context.newPage()
  await restoreTabIdentity(other, owner)
  await other.goto('/offline/')
  const otherPanel = await openOutline(other, id)
  const otherSummary = otherPanel.getByRole('textbox', { name: '总纲', exact: true })
  await otherPanel.getByRole('button', { name: '取用另一窗口的大纲草稿副本', exact: true }).click()
  await expect(otherSummary).toHaveValue(scratchA)
  expect(await other.evaluate((key) => sessionStorage.getItem(key), ownerKey)).not.toBe(owner)
  await otherSummary.fill(scratchB)
  await expect(otherPanel.getByText(saved, { exact: true })).toBeVisible()
  await expect(summary).toHaveValue(scratchA)

  other.once('dialog', (dialog) => dialog.accept())
  await other.reload()
  await openOutline(other, id)
  await otherPanel.getByRole('button', { name: '继续编辑本机大纲草稿', exact: true }).click()
  await expect(otherSummary).toHaveValue(scratchB)
  await otherSummary.fill(second)
  await otherPanel.getByRole('button', { name: confirmName, exact: true }).click()
  await expect(otherPanel.getByText('已确认并保存大纲修订。', { exact: true })).toBeVisible()

  await confirm.click()
  await expect(mainPanel.getByRole('alert').filter({ hasText: '本机已有另一版确认大纲' })).toBeVisible()
  await expect(mainPanel.getByRole('alert').filter({ hasText: '大纲或上游依据已更新' })).toBeVisible()
  await expect(confirm).toBeDisabled()
  await expect(summary).toHaveValue(scratchA)
  const pack = await exchange(main)
  const history = pack.outline_history!
  const adopted = history.versions.find(({ id }) => id === history.adopted_revision_id)!
  const former = history.versions.find(({ id }) => id === adopted.parent_id)!
  expect(history.versions).toHaveLength(2)
  expect(former.plan.summary).toBe(first)
  expect(adopted.plan.summary).toBe(second)
  expect(adopted.reference_cards).toMatchObject([{ version_id: ready.version_id, technique }])
  expect(adopted.plan.promises).toMatchObject([
    { closes_chapter_id: chapter.id, fulfillment: 'author_required' },
  ])
  expect(Object.values(adopted.promise_sources).map(({ title }) => title)).toEqual([promiseTitle])

  await otherPanel.getByRole('button', { name: '编辑当前大纲', exact: true }).click()
  await otherSummary.fill(beforeSettings)
  await expect(otherPanel.getByText(saved, { exact: true })).toBeVisible()
  const settings = main.getByLabel('离线设定台')
  await settings.getByText('设定台', { exact: true }).click()
  await settings.getByRole('button', { name: '开始建立设定' }).click()
  await settings.getByRole('button', { name: '添加设定条目' }).click()
  await settings.getByLabel('项目名称').fill('木牌归属规则')
  await settings.getByLabel('设定内容').fill('同一渡口只登记一批木牌。')
  await settings.getByRole('button', { name: '体检本地设定草稿' }).click()
  await settings.getByRole('button', { name: '确认设定并保存到浏览器' }).click()
  await expect(settings.getByText('已确认并保存新的设定修订。')).toBeVisible()
  const otherConfirm = otherPanel.getByRole('button', { name: confirmName, exact: true })
  await otherConfirm.click()
  await expect(otherPanel.getByRole('alert').filter({ hasText: '本机采用的设定已有新确认' })).toBeVisible()
  await expect(otherConfirm).toBeDisabled()
  await expect(otherSummary).toHaveValue(beforeSettings)

  other.once('dialog', (dialog) => dialog.accept())
  await other.reload()
  await openOutline(other, id)
  await otherPanel.getByRole('button', { name: '继续编辑本机大纲草稿', exact: true }).click()
  await expect(otherSummary).toHaveValue(beforeSettings)
  await expect(otherConfirm).toBeDisabled()
  expect((await exchange(other)).outline_history).toEqual(history)

  const privateInputs = [
    partial.trim(),
    pendingTitle.trim(),
    pendingNotes.trim(),
    scratchA,
    scratchB,
    beforeSettings,
  ]
  for (const text of privateInputs) expect(JSON.stringify(pack)).not.toContain(text)

  if (process.env.AINOVEL_SYNC_E2E !== '1') return
  await context.setOffline(false)
  other.once('dialog', (dialog) => dialog.accept())
  await other.reload()
  await openOutline(other, id)
  await expect(otherPanel.getByRole('button', { name: '继续编辑本机大纲草稿', exact: true })).toBeVisible()
  const cloud = process.env.AINOVEL_TEST_SYNC_URL ?? 'http://127.0.0.1:8888'
  const service = `${cloud}/api/sync/works/${branch_id}`
  const device = 'web-outline-draft'
  const { token } = await okay(
    await other.request.post(service + '/devices', {
      headers: { Authorization: 'Bearer e2e-test-only-provisioning-token' },
      data: { device_id: device },
    }),
  )
  await other.getByText('作品云同步', { exact: true }).click()
  await other.getByLabel('同步服务地址').fill(cloud)
  await other.getByLabel('设备编号').fill(device)
  await other.getByLabel('设备凭据').fill(token)
  await other.getByRole('button', { name: '启用作品同步' }).click()
  const sent: string[] = []
  other.on('request', (request) => {
    if (request.url() === service + '/push' && request.method() === 'POST')
      sent.push(request.postData() ?? '')
  })
  const sync = other.getByRole('button', { name: '同步当前工作版本' })
  await sync.click()
  await expect.poll(() => sent.some((body) => body.includes(second))).toBe(true)
  await expect(sync).toBeEnabled()
  const authorization = { Authorization: `Bearer ${token}` }
  const { ledger_id } = await okay(
    await other.request.get(service + '/checkpoint', { headers: authorization }),
  )
  const { changes } = await okay(
    await other.request.get(service + '/pull', {
      headers: { ...authorization, 'X-Ainovel-Ledger': ledger_id },
    }),
  )
  const wire = JSON.stringify(changes)
  for (const text of [first, second, ready.version_id, adopted.id]) expect(wire).toContain(text)
  for (const text of privateInputs) {
    expect(wire).not.toContain(text)
    expect(sent.join('')).not.toContain(text)
  }
})
