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
    const webBeforeMerge = 'Web 合并前稿：学徒先看到刻痕。'
    const desktopBeforeMerge = '桌面合并前稿：师姐随后辨认刻痕。'
    const mergedProse = '学徒先看到刻痕，师姐随后辨认出旧印记。'
    await editor.fill(webBeforeMerge)
    await web.getByRole('button', { name: '保存正文到浏览器', exact: true }).click()
    await expect(web.getByText(/本地已保存 · 修订 \d+/)).toBeVisible()
    await save(restored.revision, desktopBeforeMerge)
    await desktop('run')
    await context.setOffline(false)
    await sync.click()
    await web.getByRole('button', { name: /^比较冲突/ }).click()
    await context.setOffline(true)
    await compare.getByRole('button', { name: '另存为本地工作版本', exact: true }).click()
    await compare.getByLabel('另存章稿来源').selectOption('current')
    await compare.getByLabel('新工作版本名称').fill('遗铃 · 留存桌面草稿')
    await compare.getByRole('button', { name: '保存工作版本副本', exact: true }).click()
    await expect(
      web.getByRole('status').filter({ hasText: '已另存本机工作版本「遗铃 · 留存桌面草稿」' }),
    ).toBeVisible()
    await expect(editor).toHaveValue(webBeforeMerge)
    await expect(compare).toBeVisible()
    await context.setOffline(false)
    await compare.getByRole('button', { name: '手动合并正文', exact: true }).click()
    const merge = compare.getByRole('textbox', { name: '合并正文', exact: true })
    await expect(merge).toHaveValue(webBeforeMerge)
    await merge.fill(mergedProse)
    let mergeRequestId: string | undefined
    await web.route(
      '**/conflicts/*/resolve',
      async (route) => {
        mergeRequestId = route.request().postDataJSON().change_id
        const submitted = await route.fetch()
        expect(submitted.ok(), await submitted.text()).toBe(true)
        await route.abort('connectionreset')
      },
      { times: 1 },
    )
    web.once('dialog', (dialog) => void dialog.accept())
    await compare.getByRole('button', { name: '提交合并章稿' }).click()
    await expect(editor).toHaveValue(mergedProse)
    await expect(compare).toHaveCount(0)
    expect(mergeRequestId).toBeTruthy()
    const mergeEvents = await page.request.get(
      `${cloud}/api/sync/works/${branch_id}/pull?after=0&limit=100`,
      {
        headers: { Authorization: `Bearer ${webToken}` },
      },
    )
    expect(mergeEvents.ok(), await mergeEvents.text()).toBe(true)
    const { changes: afterMergeChanges } = await mergeEvents.json()
    expect(
      afterMergeChanges
        .filter(
          (row: { status: string; payload?: { head?: { content: string } } }) =>
            row.status === 'resolved' && row.payload?.head?.content === mergedProse,
        )
        .map((row: { change_id: string }) => row.change_id),
    ).toEqual([mergeRequestId])
    await desktop('run')
    const merged = await (await page.request.get(`/api/chapters/${chapterId}`)).json()
    expect(merged.content).toBe(mergedProse)
    const afterMerge = await (await page.request.get(branchPath + '/manuscript-package')).json()
    const texts = [...afterMerge.revisions, ...(afterMerge.source_revisions ?? [])].map(
      (row: { content: string }) => row.content,
    )
    expect(texts).toEqual(expect.arrayContaining([webBeforeMerge, desktopBeforeMerge, mergedProse]))

    await context.setOffline(true)
    await editor.fill('Web 归档冲突稿。')
    await web.getByRole('button', { name: '保存正文到浏览器', exact: true }).click()
    await expect(web.getByText(/本地已保存 · 修订 \d+/)).toBeVisible()
    const archived = await page.request.post(`/api/chapters/${chapterId}/archive`, {
      headers,
      data: { base_revision: merged.revision, archived: true },
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
    await web.getByRole('button', { name: '停用作品同步', exact: true }).click()
    await expect(web.getByRole('status').filter({ hasText: '本地模式' })).toBeVisible()
    await context.setOffline(true)
    await web.getByLabel('新章节名').fill('停用后继续写')
    await web.getByRole('button', { name: '本地新建章节' }).click()
    await editor.fill('这段内容保存在本机。')
    await web.getByRole('button', { name: '保存正文到浏览器', exact: true }).click()
    await expect(web.getByRole('status').filter({ hasText: '本地已保存 · 修订 1' })).toBeVisible()
    await web.reload()
    await web
      .getByRole('combobox', { name: '离线作品', exact: true })
      .selectOption({ label: '遗铃 · 留存桌面草稿' })
    await web.getByRole('button', { name: '1. 拾铃', exact: true }).click()
    await expect(editor).toHaveValue(desktopBeforeMerge)
    await web.getByText('冲突另存来源', { exact: true }).click()
    await expect(web.getByRole('region', { name: '随稿依据' })).toContainText(branch_id)
    await expect(web.getByRole('region', { name: '随稿依据' })).toContainText('比较时所选修订')
    await editor.fill('副本继续独立编辑。')
    await web.getByRole('button', { name: '保存正文到浏览器', exact: true }).click()
    await expect(web.getByRole('status').filter({ hasText: '本地已保存 · 修订' })).toBeVisible()
    const forkExport = web.waitForEvent('download')
    await web.getByRole('button', { name: '导出稿件交换包', exact: true }).click()
    const forkFile = await readFile((await (await forkExport).path())!)
    const forkPack = JSON.parse(forkFile.toString('utf8'))
    expect(forkPack.branch.id).not.toBe(branch_id)
    const forkReceipt = forkPack.evidence.find(({ title }: { title: string }) => title === '冲突另存来源')
    const forkOrigin = JSON.parse(forkReceipt.body)
    const originDigest = createHash('sha256').update(desktopBeforeMerge).digest('hex')
    expect(forkOrigin).toMatchObject({
      原工作版本: branch_id,
      原章节: chapterId,
      '原章稿 SHA-256': originDigest,
    })
    expect(forkReceipt.chapters[0]).toMatchObject({
      revision_id: forkOrigin.本机副本修订,
      sha256: originDigest,
    })
    expect(forkPack.revisions).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: forkOrigin.本机副本修订, content: desktopBeforeMerge }),
        expect.objectContaining({ chapter_id: forkOrigin.本机副本章节, content: '副本继续独立编辑。' }),
      ]),
    )
    await web.evaluate(
      () =>
        new Promise<void>((resolve, reject) => {
          const request = indexedDB.deleteDatabase('ainovel-offline-workspace')
          request.onsuccess = () => resolve()
          request.onerror = () => reject(request.error)
          request.onblocked = () => reject(new Error('Offline database unexpectedly blocked'))
        }),
    )
    await web.reload()
    await expect(
      web.getByRole('combobox', { name: '离线作品', exact: true }).getByRole('option'),
    ).toHaveCount(1)
    await web.getByLabel('导入新离线作品').setInputFiles({
      name: 'fork.json',
      mimeType: 'application/json',
      buffer: forkFile,
    })
    await web
      .getByRole('combobox', { name: '离线作品', exact: true })
      .selectOption({ label: '遗铃 · 留存桌面草稿' })
    await web.getByRole('button', { name: '1. 拾铃', exact: true }).click()
    await expect(editor).toHaveValue('副本继续独立编辑。')
    await web.getByText('冲突另存来源', { exact: true }).click()
    await web.getByRole('button', { name: /^查看来源修订/ }).click()
    await expect(web.getByRole('region', { name: '来源章稿正文' })).toContainText(desktopBeforeMerge)
  } finally {
    await context.close()
  }
})

test('独立 Web 从持久游标续传，还能安全撤销此设备的凭据', async ({ page, browser }) => {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const novel = await page.request.post('/api/novels', {
    headers,
    data: { title: '遗铃 · 断点续传', brief: '双端验证同步断点。' },
  })
  expect(novel.ok()).toBe(true)
  const { branch_id } = await novel.json()
  const branch = `/api/branches/${branch_id}`
  const emptyPack = await (await page.request.get(branch + '/manuscript-package')).body()
  const chapter = await page.request.post(branch + '/chapters', { headers, data: { title: '灯下辨铃' } })
  expect(chapter.ok()).toBe(true)
  const { id } = await chapter.json()
  const sourceText = '匠人把两枚铜铃放在同一盏灯下。'
  const saved = await page.request.put(`/api/chapters/${id}`, {
    headers,
    data: { base_revision: 0, content: sourceText },
  })
  expect(saved.ok()).toBe(true)
  const endpoint = `${cloud}/api/sync/works/${branch_id}`
  async function grant(device_id: string) {
    const response = await page.request.post(endpoint + '/devices', {
      headers: { Authorization: 'Bearer e2e-test-only-provisioning-token' },
      data: { device_id },
    })
    expect(response.ok()).toBe(true)
    return (await response.json()).token as string
  }
  const desktopToken = await grant('desktop')
  const webToken = await grant('web')
  for (const action of ['enable', 'run']) {
    const response = await page.request.post(`${branch}/desktop-sync/${action}`, {
      headers,
      data: { origin: cloud, token: desktopToken },
    })
    expect(response.ok(), await response.text()).toBe(true)
  }
  const web = await browser.newPage()
  try {
    const sync = web.getByRole('button', { name: '同步当前工作版本', exact: true })
    async function openSync() {
      await web
        .getByRole('combobox', { name: '离线作品', exact: true })
        .selectOption({ label: '遗铃 · 断点续传' })
      await web.getByText('作品云同步', { exact: true }).click()
      await web.getByLabel('同步服务地址').fill(cloud)
      await web.getByLabel('设备编号').fill('web')
      await web.getByLabel('设备凭据').fill(webToken)
    }
    await web.goto('http://127.0.0.1:8877/offline/')
    await web.getByLabel('导入新离线作品').setInputFiles({
      name: 'base.json',
      mimeType: 'application/json',
      buffer: emptyPack,
    })
    await openSync()
    await web.getByRole('button', { name: '启用作品同步', exact: true }).click()
    await expect(sync).toBeEnabled()
    const pullRequests: number[] = []
    let savedCursor = 0
    let interrupted = false
    await web.route(`${endpoint}/pull?*`, async (route) => {
      const url = new URL(route.request().url())
      pullRequests.push(Number(url.searchParams.get('after')))
      if (pullRequests.length === 1) {
        url.searchParams.set('limit', '1')
        const first = await route.fetch({ url: url.toString() })
        const data = await first.json()
        expect(data.has_more).toBe(true)
        savedCursor = data.cursor
        await route.fulfill({ response: first })
      } else if (!interrupted) {
        interrupted = true
        await route.abort('connectionreset')
      } else {
        await route.continue()
      }
    })
    await sync.click()
    await expect(web.getByRole('alert')).toBeVisible()
    expect(savedCursor).toBeGreaterThan(0)
    expect(pullRequests).toEqual([0, savedCursor])
    await web.reload()
    await openSync()
    await expect(web.getByText(`已启用 · 游标 ${savedCursor} · 0 条待发送或确认`)).toBeVisible()
    await sync.click()
    await expect(web.getByRole('button', { name: '1. 灯下辨铃', exact: true })).toBeVisible()
    await web.getByRole('button', { name: '1. 灯下辨铃', exact: true }).click()
    const editor = web.getByRole('textbox', { name: '章节正文', exact: true })
    await expect(editor).toHaveValue(sourceText)
    expect(pullRequests[2]).toBe(savedCursor)

    await web.getByRole('button', { name: '停用作品同步', exact: true }).click()
    await expect(web.getByRole('status').filter({ hasText: '本地模式' })).toBeVisible()
    const pullWith = (token: string) =>
      page.request.get(endpoint + '/pull?after=0&limit=1', { headers: { Authorization: `Bearer ${token}` } })
    expect((await pullWith(webToken)).ok()).toBe(true)
    const edited = '暂存在本地的第二稿。'
    await editor.fill(edited)
    await web.getByRole('button', { name: '保存正文到浏览器', exact: true }).click()
    await expect(web.getByRole('status').filter({ hasText: '本地已保存 · 修订 2' })).toBeVisible()

    const revoke = web.getByRole('button', { name: '撤销此设备的云端凭据', exact: true })
    const credential = web.getByLabel('设备凭据')
    async function confirmRevoke() {
      web.once('dialog', (dialog) => void dialog.accept())
      await revoke.click()
    }
    await web.getByLabel('设备编号').fill('desktop')
    await confirmRevoke()
    await expect(web.getByRole('alert')).toContainText('设备编号与凭据不匹配')
    expect((await pullWith(webToken)).ok()).toBe(true)

    await web.getByLabel('设备编号').fill('web')
    await web.route(
      `${endpoint}/device-session?*`,
      async (route) => {
        const confirmed = await route.fetch()
        expect(confirmed.status()).toBe(204)
        await route.abort('connectionreset')
      },
      { times: 1 },
    )
    await confirmRevoke()
    await expect(web.getByRole('alert')).toContainText('尚未取得云端撤销回执')
    await expect(credential).toHaveValue(webToken)
    expect((await pullWith(webToken)).status()).toBe(401)
    expect((await pullWith(desktopToken)).ok()).toBe(true)
    await confirmRevoke()
    await expect(web.getByRole('status').filter({ hasText: '已撤销此设备在云端的凭据' })).toBeVisible()
    await expect(credential).toHaveValue('')
    await expect(editor).toHaveValue(edited)

    const cursorBeforeResend = pullRequests.at(-1)!
    const replacement = await grant('web')
    await credential.fill(replacement)
    await web.getByRole('button', { name: '启用作品同步', exact: true }).click()
    const finalStatus = web.getByRole('status').filter({ hasText: /已启用 · 游标 \d+ · 0 条待发送或确认/ })
    await sync.click()
    await expect(sync).toBeEnabled()
    await expect
      .poll(async () => Number((await finalStatus.innerText()).match(/游标 (\d+)/)?.[1]))
      .toBeGreaterThan(cursorBeforeResend)
    const received = await page.request.post(`${branch}/desktop-sync/run`, {
      headers,
      data: { origin: cloud, token: desktopToken },
    })
    expect(received.ok(), await received.text()).toBe(true)
    expect((await (await page.request.get(`/api/chapters/${id}`)).json()).content).toBe(edited)
  } finally {
    await web.close()
  }
})
