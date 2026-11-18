import { expect, test } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'

const cloud = 'http://127.0.0.1:8888'
test.skip(process.env.AINOVEL_SYNC_E2E !== '1', '使用隔离云端进程运行双端验收')

test('桌面和离线 Web 同章修改产生可解决冲突，历史稿仍可查', async ({ page, browser }) => {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const novelResponse = await page.request.post('/api/novels', {
    headers,
    data: { title: '遗铃 · 跨设备', brief: '查明铜铃来历。' },
  })
  expect(novelResponse.ok()).toBe(true)
  const { id: novelId, branch_id } = await novelResponse.json()
  const branchPath = `/api/branches/${branch_id}`
  const chapterResponse = await page.request.post(branchPath + '/chapters', {
    headers,
    data: { title: '拾铃' },
  })
  expect(chapterResponse.ok()).toBe(true)
  const { id: chapterId } = await chapterResponse.json()
  async function save(revision: number, content: string) {
    const response = await page.request.put(`/api/chapters/${chapterId}`, {
      headers,
      data: { base_revision: revision, content },
    })
    expect(response.ok(), await response.text()).toBe(true)
  }
  await save(0, '原稿：找到了铜铃。')
  const original = await (await page.request.get(branchPath + '/manuscript-package')).body()
  const startingPack = JSON.parse(original.toString('utf8'))
  const startingHead = startingPack.revisions.at(-1)
  async function grant(device_id: string) {
    const response = await page.request.post(`${cloud}/api/sync/works/${branch_id}/devices`, {
      headers: { Authorization: 'Bearer e2e-test-only-provisioning-token' },
      data: { device_id },
    })
    expect(response.ok()).toBe(true)
    return (await response.json()).token as string
  }
  const desktopToken = await grant('desktop')
  const webToken = await grant('web')
  async function desktop(action: 'enable' | 'run') {
    const response = await page.request.post(branchPath + '/desktop-sync/' + action, {
      headers,
      data: { origin: cloud, token: desktopToken },
    })
    expect(response.ok(), await response.text()).toBe(true)
    return response.json()
  }
  await desktop('enable')
  const seeded = await desktop('run')
  expect(seeded.cursor).toBeGreaterThan(2)
  const context = await browser.newContext()
  try {
    const web = await context.newPage()
    await web.goto('http://127.0.0.1:8877/offline/')
    await web.getByLabel('导入新离线作品').setInputFiles({
      name: 'manuscript.json',
      mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify({ ...startingPack, chapters: [], revisions: [] })),
    })
    await web
      .getByRole('combobox', { name: '离线作品', exact: true })
      .selectOption({ label: '遗铃 · 跨设备' })
    await web.getByText('作品云同步', { exact: true }).click()
    await web.getByLabel('同步服务地址').fill(cloud)
    await web.getByLabel('设备编号').fill('web')
    await web.getByLabel('设备凭据').fill(webToken)
    await web.getByRole('button', { name: '启用作品同步', exact: true }).click()
    const sync = web.getByRole('button', { name: '同步当前工作版本', exact: true })
    await sync.click()
    await expect(web.getByText(`已启用 · 游标 ${seeded.cursor} · 0 条待发送或确认`)).toBeVisible()
    await web.getByRole('button', { name: '1. 拾铃', exact: true }).click()
    const inspection = branchPath + '/workflow-inspections'
    const { preview } = await (await page.request.get(inspection)).json()
    const recorded = await page.request.post(inspection, {
      headers,
      data: { basis_sha256: preview.basis_sha256 },
    })
    expect(recorded.ok(), await recorded.text()).toBe(true)
    expect((await desktop('run')).cursor).toBeGreaterThan(seeded.cursor)
    await sync.click()
    await web.getByText('十处对账体检', { exact: true }).click()
    await expect(web.getByRole('region', { name: '随稿依据' }).getByText('依据快照 · 只读')).toBeVisible()
    const editor = web.getByRole('textbox', { name: '章节正文', exact: true })
    await context.setOffline(true)
    await editor.fill('Web 版本：学徒拾铃。')
    await web.getByRole('button', { name: '保存正文到浏览器', exact: true }).click()
    await expect(web.getByText(/本地已保存 · 修订 2/)).toBeVisible()
    await save(1, '桌面版本：师姐拾铃。')
    expect((await desktop('run')).conflicts).toHaveLength(0)
    await context.setOffline(false)
    await sync.click()
    await web.getByRole('button', { name: /^比较冲突/ }).click()
    await expect(editor).toHaveValue('Web 版本：学徒拾铃。')
    const compare = web.getByRole('region', { name: '云同步冲突比较' })
    await compare.getByText('云端当前版', { exact: true }).click()
    await expect(compare.getByText('桌面版本：师姐拾铃。', { exact: true })).toBeVisible()
    await compare.getByText('共同依据', { exact: true }).click()
    await expect(compare.getByText('原稿：找到了铜铃。', { exact: true })).toBeVisible()
    web.once('dialog', (dialog) => dialog.accept())
    await compare.getByRole('button', { name: '采用云端当前版', exact: true }).click()
    await expect(editor).toHaveValue('桌面版本：师姐拾铃。')
    await expect(web.getByRole('button', { name: /^比较冲突/ })).toHaveCount(0)
    expect((await desktop('run')).conflicts).toHaveLength(0)

    await web.getByText('本章版本历史', { exact: true }).click()
    const history = web.getByRole('combobox', { name: '历史修订', exact: true })
    await history.selectOption({ label: '修订 2 · 作者编辑' })
    await expect(web.getByText('Web 版本：学徒拾铃。', { exact: true })).toBeVisible()
    await history.selectOption({ label: '修订 1 · 作者编辑' })
    await web.getByRole('button', { name: '恢复为最新修订', exact: true }).click()
    await expect(editor).toHaveValue('原稿：找到了铜铃。')
    await sync.click()
    const rolledBack = await (await page.request.get(`/api/chapters/${chapterId}`)).json()
    expect(rolledBack.content).not.toBe('原稿：找到了铜铃。')
    await expect(sync).toBeEnabled()
    await desktop('run')
    const restored = await (await page.request.get(`/api/chapters/${chapterId}`)).json()
    expect(restored.content).toBe('原稿：找到了铜铃。')
    expect(restored.revision).toBeGreaterThan(2)

    await context.setOffline(true)
    await editor.fill('Web 归档冲突稿。')
    await web.getByRole('button', { name: '保存正文到浏览器', exact: true }).click()
    await expect(web.getByText(/本地已保存 · 修订 5/)).toBeVisible()
    const archived = await page.request.post(`/api/chapters/${chapterId}/archive`, {
      headers,
      data: { base_revision: restored.revision, archived: true },
    })
    expect(archived.ok(), await archived.text()).toBe(true)
    await desktop('run')
    await context.setOffline(false)
    await sync.click()
    await web.getByRole('button', { name: /^比较冲突/ }).click()
    await expect(editor).toHaveValue('Web 归档冲突稿。')
    await compare.getByText('云端当前版', { exact: true }).click()
    await expect(compare.getByText('无此对象或已归档', { exact: true })).toBeVisible()
    web.once('dialog', (dialog) => dialog.accept())
    await compare.getByRole('button', { name: '采用云端当前版', exact: true }).click()
    await expect(web.getByRole('button', { name: '1. 拾铃', exact: true })).toHaveCount(0)
    expect((await desktop('run')).conflicts).toHaveLength(0)

    await web.getByText('选定云附件', { exact: true }).click()
    const file = Buffer.concat([Buffer.alloc(512 * 1024, 0x61), Buffer.from('独立选择上传的铜铃资料。')])
    const digest = createHash('sha256').update(file).digest('hex')
    const attachments = `${cloud}/api/sync/works/${branch_id}/attachments`
    const blockedPart = `${attachments}/${digest}/parts/1`
    await web.route(blockedPart, (route) => route.abort())
    await web.getByLabel('选择云附件').setInputFiles({
      name: '铃声资料.txt',
      mimeType: 'text/plain',
      buffer: file,
    })
    await web.getByRole('button', { name: '上传选中文件', exact: true }).click()
    await expect(web.getByRole('alert')).toBeVisible()
    const partial = await page.request.post(attachments, {
      headers: { Authorization: `Bearer ${webToken}` },
      data: { name: '铃声资料.txt', sha256: digest, size: file.length },
    })
    expect(partial.ok(), await partial.text()).toBe(true)
    expect((await partial.json()).received).toEqual([0])
    await web.unroute(blockedPart)
    await web.getByRole('button', { name: '上传选中文件', exact: true }).click()
    await expect(web.getByText('附件已通过校验，可在其他设备下载。')).toBeVisible()
    await page.reload()
    await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novelId)
    await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
    const desktopPanel = page.getByRole('region', { name: '作品云同步', exact: true })
    await desktopPanel.getByLabel('同步服务地址').fill(cloud)
    await desktopPanel.getByLabel('设备凭据').fill(desktopToken)
    await desktopPanel.getByText('选定云附件', { exact: true }).click()
    await desktopPanel.getByRole('button', { name: '读取云附件', exact: true }).click()
    const download = page.waitForEvent('download')
    await desktopPanel.getByRole('button', { name: '下载 铃声资料.txt', exact: true }).click()
    expect(await readFile((await (await download).path())!)).toEqual(file)

    const evidenceBody = '缺少来源时仍应保留云端依据。'
    const sourceText = '远端旧章稿的确切依据。'
    const sourceHash = createHash('sha256').update(sourceText).digest('hex')
    const deferred = await page.request.post(`${cloud}/api/sync/works/${branch_id}/push`, {
      headers: { Authorization: `Bearer ${desktopToken}` },
      data: {
        change_id: 'missing-source',
        object_id: 'evidence:missing-source',
        parent_revision: null,
        tombstone: false,
        payload: {
          format: 'ainovel-manuscript-sync/1',
          kind: 'evidence',
          document: {
            id: 'missing-source',
            kind: 'inspection',
            title: '无法核验的云端依据',
            body: evidenceBody,
            sha256: createHash('sha256').update(evidenceBody).digest('hex'),
            chapters: [{ id: chapterId, revision: 2, revision_id: 'delayed-source', sha256: sourceHash }],
          },
        },
      },
    })
    expect(deferred.ok(), await deferred.text()).toBe(true)
    await sync.click()
    await expect(web.getByText(/evidence:missing-source · 缺少哈希一致的原章稿修订/)).toBeVisible()
    await expect(web.getByText('无法核验的云端依据', { exact: true })).toHaveCount(0)
    const source = await page.request.post(`${cloud}/api/sync/works/${branch_id}/push`, {
      headers: { Authorization: `Bearer ${desktopToken}` },
      data: {
        change_id: 'send-delayed-source',
        object_id: 'revision:delayed-source',
        parent_revision: null,
        tombstone: false,
        payload: {
          format: 'ainovel-manuscript-sync/1',
          kind: 'revision',
          branch_id,
          revision: {
            id: 'delayed-source',
            chapter_id: chapterId,
            revision: 2,
            parent_id: startingHead.id,
            content: sourceText,
            sha256: sourceHash,
            cause: '来源报告所指修订',
            created: startingHead.created + 1,
          },
        },
      },
    })
    expect(source.ok(), await source.text()).toBe(true)
    await sync.click()
    await expect(web.getByText(/evidence:missing-source/)).toHaveCount(0)
    await web.getByText('无法核验的云端依据', { exact: true }).click()
    await web.getByRole('button', { name: '查看来源修订 2 · delayed-' }).click()
    await expect(web.getByRole('region', { name: '来源章稿正文' }).getByText(sourceText)).toBeVisible()
    await expect(web.getByRole('button', { name: '1. 拾铃', exact: true })).toHaveCount(0)
  } finally {
    await context.close()
  }
})
