import { expect, test, type APIResponse } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import type { ConceptionState } from '../../frontend/conception'

const cloud = 'http://127.0.0.1:8888'
const title = '同步范围验收 · 雨夜钟'
const savedDesktop = '守夜人确认钟响来自南门。'
const privateDesktopText = '桌面草稿：钟响是被风吹出来的。'
const savedSetting = '南门的钟由当夜守卫敲响。'
const privateDesktopSetting = '桌面设定草案：南门也可用蝙蝠报信。'
const savedWeb = '守夜人记下南门钟响的时刻。'
const privateWebText = '网页草稿：守夜人听见三次回声。'
const privateWebSetting = '网页设定草案：钟声只在雨后传过河。'
const privateInputs = [privateDesktopText, privateDesktopSetting, privateWebText, privateWebSetting]

test.skip(process.env.AINOVEL_SYNC_E2E !== '1', '使用隔离 SQLite、云端进程和 Chromium 运行双端验收')

async function okay(response: APIResponse) {
  expect(response.ok(), await response.text()).toBe(true)
  return response.json()
}

test('两端首次同步说明范围，只传正式章稿与设定并保留未确认草稿', async ({ page, browser, baseURL }) => {
  await page.goto('/')
  const { csrf } = await okay(await page.request.get('/api/session'))
  const headers = { 'X-Ainovel-CSRF': csrf }
  const novel = await okay(await page.request.post('/api/novels', { headers, data: { title } }))
  const branch = `/api/branches/${novel.branch_id}`
  const seed = await (await page.request.get(branch + '/manuscript-package')).body()
  const chapter = await okay(
    await page.request.post(branch + '/chapters', { headers, data: { title: '听钟' } }),
  )
  const chapterPath = `/api/chapters/${chapter.id}`
  const conception = branch + '/conception'
  const service = `${cloud}/api/sync/works/${novel.branch_id}`
  async function grant(device_id: string): Promise<string> {
    return (
      await okay(
        await page.request.post(service + '/devices', {
          headers: { Authorization: 'Bearer e2e-test-only-provisioning-token' },
          data: { device_id },
        }),
      )
    ).token
  }
  const desktopToken = await grant('draft-scope-desktop')
  const webDevice = 'draft-scope-web'
  const webToken = await grant(webDevice)
  const bearer = { Authorization: `Bearer ${webToken}` }

  await okay(
    await page.request.put(conception + '/project-brief', {
      headers,
      data: {
        base_revision: 0,
        idempotency_key: randomUUID(),
        idea: '守夜人调查雨夜钟声的来历。',
        brief: {
          position: '调查雨夜钟声',
          track: '线索式悬疑',
          genre_ids: ['xianxia'],
          audience: '喜欢线索推理的读者',
          distinction: '从打更记录追索铃声',
          length: '一卷三章',
          ending: '找到敲钟人。',
        },
      },
    }),
  )
  async function saveSettingDraft(detail: string): Promise<ConceptionState> {
    const state: ConceptionState = await okay(await page.request.get(conception))
    const world = state.categories.find(({ id }) => id === 'world')!
    world.items = [{ id: 'night-bell', title: '南门钟', detail, links: [] }]
    return okay(
      await page.request.put(conception, {
        headers,
        data: {
          base_revision: state.revision,
          base_story_revision: state.story_revision,
          categories: state.categories,
        },
      }),
    )
  }
  const setting = await saveSettingDraft(savedSetting)
  await okay(
    await page.request.post(conception + '/confirm', {
      headers,
      data: {
        base_revision: setting.revision,
        base_story_revision: setting.story_revision,
        idempotency_key: randomUUID(),
      },
    }),
  )
  await saveSettingDraft(privateDesktopSetting)

  async function saveChapter(base_revision: number, content: string) {
    return okay(await page.request.put(chapterPath, { headers, data: { base_revision, content } }))
  }
  const previous = await saveChapter(0, '守夜人在雨里听见了钟声。')
  const nativeWriter = await page.context().newPage()
  await nativeWriter.goto('/')
  await nativeWriter.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  const nativeEditor = nativeWriter.getByRole('textbox', { name: '章节正文', exact: true })
  await expect(nativeEditor).toHaveText(previous.content)
  const formal = await saveChapter(previous.revision, savedDesktop)
  await nativeEditor.fill(privateDesktopText)
  await nativeWriter.getByRole('button', { name: '保存', exact: true }).click()
  await expect(nativeWriter.getByText(/保存失败，浏览器草稿保留/)).toBeVisible()
  await expect(nativeEditor).toHaveText(privateDesktopText)

  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  const native = page.getByRole('region', { name: '作品云同步', exact: true })
  const nativeScope = native.getByRole('region', { name: '本次作品同步范围', exact: true })
  await expect(nativeScope).toContainText('正式章稿与历史、已确认立项与设定')
  await expect(nativeScope).toContainText('未确认草稿')
  await expect(nativeScope).toContainText('留在本机')
  await expect(nativeScope).toContainText('原始资料和附件按所选文件上传')
  await native.getByLabel('同步服务地址').fill(cloud)
  await native.getByLabel('设备凭据').fill(desktopToken)
  await native.getByRole('button', { name: '启用作品同步', exact: true }).click()
  const nativeRun = native.getByRole('button', { name: '同步当前工作版本', exact: true })
  await expect(nativeRun).toBeEnabled()
  const nativeReply = page.waitForResponse((response) => response.url().endsWith('/desktop-sync/run'))
  await nativeRun.click()
  const synced = await okay(await nativeReply)
  expect(synced).toMatchObject({ pending: 0, conflicts: [], deferred: [] })
  const initial = await okay(await page.request.get(service + '/checkpoint', { headers: bearer }))
  const readCloud = async () =>
    okay(
      await page.request.get(service + '/pull', {
        headers: { ...bearer, 'X-Ainovel-Ledger': initial.ledger_id },
      }),
    )
  const nativeChanges = await readCloud()
  expect(nativeChanges.has_more).toBe(false)
  const nativeWire = JSON.stringify(nativeChanges.changes)
  expect(nativeWire).toContain(savedDesktop)
  expect(nativeWire).toContain(savedSetting)
  for (const draft of privateInputs) expect(nativeWire).not.toContain(draft)

  const isolated = await browser.newContext({ baseURL })
  try {
    const web = await isolated.newPage()
    await web.goto('/offline/')
    await web
      .getByLabel('导入新离线作品')
      .setInputFiles({ name: '未同步空作品.json', mimeType: 'application/json', buffer: seed })
    const runName = '同步当前工作版本'
    async function connect(tab: typeof web) {
      await tab.getByRole('combobox', { name: '离线作品', exact: true }).selectOption(novel.id)
      await tab.getByText('作品云同步', { exact: true }).click()
      await tab.getByLabel('同步服务地址').fill(cloud)
      await tab.getByLabel('设备编号').fill(webDevice)
      await tab.getByLabel('设备凭据').fill(webToken)
      return tab.getByRole('button', { name: runName, exact: true })
    }
    const webRun = await connect(web)
    const webScope = web.getByRole('region', { name: '本次作品同步范围', exact: true })
    await expect(webScope).toContainText('正式章稿与历史、已确认立项与设定')
    await expect(webScope).toContainText('未确认草稿')
    await expect(webScope).toContainText('留在本机')
    await expect(webScope).toContainText('原始资料和附件按所选文件上传')
    await web.getByRole('button', { name: '启用作品同步', exact: true }).click()
    await webRun.click()
    await expect(web.getByText(`已启用 · 游标 ${initial.cursor} · 0 条待发送或确认`)).toBeVisible()
    await web.getByRole('button', { name: '1. 听钟', exact: true }).click()
    const webEditor = web.getByRole('textbox', { name: '章节正文', exact: true })
    await expect(webEditor).toHaveValue(savedDesktop)
    await webEditor.fill(savedWeb)
    await web.getByRole('button', { name: '保存正文到浏览器', exact: true }).click()
    await expect(web.getByText(`本地已保存 · 修订 ${formal.revision + 1}`)).toBeVisible()

    const settings = web.getByLabel('离线设定台')
    await settings.getByText('设定台', { exact: true }).click()
    await settings.getByRole('button', { name: '编辑当前设定', exact: true }).click()
    await settings.getByRole('combobox', { name: '设定类别' }).selectOption('world')
    const webSetting = settings.getByRole('textbox', { name: '设定内容', exact: true })
    await expect(webSetting).toHaveValue(savedSetting)
    await webSetting.fill(privateWebSetting)
    await webEditor.fill(privateWebText)
    await expect(web.getByText('输入已暂存为草稿。', { exact: true })).toBeVisible()
    await expect(webRun).toBeDisabled()

    const sender = await isolated.newPage()
    const requests: string[] = []
    sender.on('request', (request) => {
      if (request.url() === service + '/push' && request.method() === 'POST')
        requests.push(request.postData() ?? '')
    })
    await sender.goto('/offline/')
    const send = await connect(sender)
    await send.click()
    await expect.poll(() => requests.length).toBeGreaterThan(0)
    await expect(sender.getByText(/正式保存仍待同步/)).toHaveCount(0)
    await expect(send).toBeEnabled()
    const webWire = requests.join('\n')
    expect(webWire).toContain(savedWeb)
    for (const draft of privateInputs) expect(webWire).not.toContain(draft)

    const allChanges = await readCloud()
    expect(allChanges.has_more).toBe(false)
    expect(allChanges.cursor).toBeGreaterThan(initial.cursor)
    const completeWire = JSON.stringify(allChanges.changes)
    expect(completeWire).toContain(savedWeb)
    for (const draft of privateInputs) expect(completeWire).not.toContain(draft)

    await expect(webSetting).toHaveValue(privateWebSetting)
    await expect(webEditor).toHaveValue(privateWebText)
    await expect(nativeEditor).toHaveText(privateDesktopText)
    expect((await okay(await page.request.get(chapterPath))).content).toBe(savedDesktop)
    const localSetting: ConceptionState = await okay(await page.request.get(conception))
    expect(localSetting.categories.find(({ id }) => id === 'world')?.items[0].detail).toBe(
      privateDesktopSetting,
    )
  } finally {
    await isolated.close()
    await nativeWriter.close()
  }
})
