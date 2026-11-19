import { expect, test, type APIResponse, type Page } from '@playwright/test'
import { createHash, randomUUID } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import type { components } from '../../frontend/api-schema'

const cloud = process.env.AINOVEL_TEST_SYNC_URL ?? 'http://127.0.0.1:8888'
const provision = { Authorization: 'Bearer e2e-test-only-provisioning-token' }
const title = '渡口暮钟 · 正式事实与篇幅同步'
const firstTitle = '塔外观察'
const secondTitle = '钟楼问询'
const oldText = '学徒在钟楼外等候问询。'
const localText = '学徒决定在门边多等片刻。'
const quote = '钟楼在暮色中响了两声'
const desktopText = `😀船靠岸时，${quote}。`
const statement = '暮钟当天响了两声。'
const volumeId = 'formal-length-volume'

type Chapter = { id: string; revision: number; content: string }
type Targets = { min_chars: number | null; max_chars: number | null; expected_chapters: number | null }
type Scope = { scope: 'book' | 'volume' | 'chapter'; scope_id: string | null }
type CloudChange = {
  object_id: string
  head_revision: string
  payload: { kind: string; revision_id?: string; version?: Record<string, unknown> } | null
}
type PageSlice = { cursor: number; has_more: boolean; changes: CloudChange[] }
type Package = components['schemas']['ManuscriptPackage']

test.skip(process.env.AINOVEL_SYNC_E2E !== '1', '需要隔离云账本、桌面 SQLite 和独立 Web')

async function checked<T>(response: APIResponse): Promise<T> {
  expect(response.ok(), await response.text()).toBe(true)
  return response.json()
}

async function prepareDesktop(page: Page) {
  await page.goto('/')
  const { csrf } = await checked<{ csrf: string }>(await page.request.get('/api/session'))
  const headers = { 'X-Ainovel-CSRF': csrf }
  const get = <T>(path: string) => page.request.get(path).then(checked<T>)
  const post = <T>(path: string, data: unknown) => page.request.post(path, { headers, data }).then(checked<T>)
  const put = <T>(path: string, data: unknown) => page.request.put(path, { headers, data }).then(checked<T>)
  const novel = await post<{ id: string; branch_id: string }>('/api/novels', { title })
  const branch = `/api/branches/${novel.branch_id}`
  const first = await post<Chapter>(`${branch}/chapters`, { title: firstTitle })
  const second = await post<Chapter>(`${branch}/chapters`, { title: secondTitle })
  const save = (chapter: Chapter, content: string) =>
    put<Chapter>(`/api/chapters/${chapter.id}`, { base_revision: chapter.revision, content })
  await save(first, '入夜前，塔外还看得见来往的船。')
  const baselineChapter = await save(second, oldText)
  const outline = `${branch}/outline-workspace`
  const plan = await put<{ draft: { revision: number } }>(outline, {
    base_revision: 0,
    confirmation_id: null,
    plan: {
      summary: '调查暮钟是否有正式见证。',
      volumes: [
        {
          id: volumeId,
          title: '渡口卷',
          summary: '核对钟声次数。',
          units: [
            {
              id: 'formal-length-unit',
              title: '核对证人',
              summary: '询问当晚的经过。',
              chapters: [first, second].map(({ id }) => ({
                chapter_id: id,
                goal: '核对钟声',
                obstacle: '雾遮住钟楼',
                turn: '取得新线索',
                result: '确认见证范围',
              })),
            },
          ],
        },
      ],
    },
  })
  await post(`${outline}/confirm`, { base_revision: plan.draft.revision })
  const scopes: Scope[] = [
    { scope: 'book', scope_id: null },
    { scope: 'volume', scope_id: volumeId },
    { scope: 'chapter', scope_id: second.id },
  ]
  const snapshot = await post<{ id: string }>(`${branch}/work-snapshots`, {
    label: '正式台账未填写',
    scope: 'book',
  })
  const archive = await page.request.get(`${branch}/manuscript-backup`)
  expect(archive.ok()).toBe(true)
  const backup = await archive.body()
  await archive.dispose()
  const measurements = `${branch}/workflow-inspections`
  const target = ({ scope, scope_id }: Scope) => {
    const query = new URLSearchParams({ scope, ...(scope_id ? { scope_id } : {}) })
    return get<{ preview: { length_target: { id: string; targets: Targets } | null } }>(
      `${measurements}?${query}`,
    )
  }
  const facts = () =>
    get<
      Array<{
        chapter_id: string
        source_revision: number
        statement: string
        evidence: string
        evidence_start: number
        evidence_end: number
        stale: number
      }>
    >(`${branch}/facts`)
  async function update() {
    const chapter = await save(baselineChapter, desktopText)
    await post('/api/facts', {
      chapter_id: chapter.id,
      source_revision: chapter.revision,
      subject: '暮钟',
      kind: 'event',
      truth: 'fact',
      evidence: quote,
      statement,
    })
    for (const [index, scope] of scopes.entries()) {
      await put(`${measurements}/length-targets`, { ...scope, targets: { min_chars: [90, 50, 12][index] } })
    }
    return chapter
  }
  return { novel, branch, get, post, put, facts, target, scopes, snapshot, backup, update, baselineChapter }
}

test('正式事实及章卷书篇幅双向同步，稿件分歧先保留并可从检查点清除', async ({ page, browser, baseURL }) => {
  test.setTimeout(120_000)
  const desktop = await prepareDesktop(page)
  const endpoint = `${cloud}/api/sync/works/${desktop.novel.branch_id}`
  const nativeDevice = 'published-rule-desktop'
  const webDevice = 'published-rule-web'
  const grant = async (device_id: string) =>
    (
      await checked<{ token: string }>(
        await page.request.post(`${endpoint}/devices`, { headers: provision, data: { device_id } }),
      )
    ).token
  const nativeToken = await grant(nativeDevice)
  const webToken = await grant(webDevice)
  const auth = { Authorization: `Bearer ${webToken}` }
  const checkpoint = () =>
    page.request.get(`${endpoint}/checkpoint`, { headers: auth }).then(checked<{ cursor: number }>)
  const startNative = (action: 'enable' | 'run') =>
    desktop.post<{
      cursor: number
      conflicts: unknown[]
      deferred: unknown[]
      pending: number
    }>(`${desktop.branch}/desktop-sync/${action}`, { origin: cloud, token: nativeToken })
  await startNative('enable')
  await startNative('run')
  const baseline = await checkpoint()

  const context = await browser.newContext({ baseURL, viewport: { width: 420, height: 900 } })
  try {
    const web = await context.newPage()
    await web.goto('/offline/')
    await expect.poll(() => web.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
    await web.getByLabel('从独立备份恢复作品', { exact: true }).setInputFiles({
      name: 'common.ainovel-backup',
      mimeType: 'application/octet-stream',
      buffer: desktop.backup,
    })
    const work = web.getByRole('combobox', { name: '离线作品', exact: true })
    const status = (cursor: number) =>
      web.getByText(`已启用 · 游标 ${cursor} · 0 条待发送或确认`, { exact: true })
    const sync = web.getByRole('button', { name: '同步当前工作版本', exact: true })
    const chapterButton = web.getByRole('button', { name: `2. ${secondTitle}`, exact: true })
    const editor = web.getByRole('textbox', { name: '章节正文', exact: true })
    const facts = web.getByText(/^正式故事事实（\d+）$/)
    const rules = web.getByText('本机规则核算', { exact: true })
    async function credentials() {
      await work.selectOption(desktop.novel.id)
      await web.getByText('作品云同步', { exact: true }).click()
      await web.getByLabel('同步服务地址').fill(cloud)
      await web.getByLabel('设备编号').fill(webDevice)
      await web.getByLabel('设备凭据').fill(webToken)
    }
    async function syncOnce() {
      await expect(sync).toBeEnabled()
      await sync.press('Enter')
      await expect(sync).toBeEnabled()
    }
    await credentials()
    await web.getByRole('button', { name: '启用作品同步', exact: true }).click()
    await syncOnce()
    await expect(status(baseline.cursor)).toBeVisible()
    await chapterButton.click()
    await expect(editor).toHaveValue(oldText)
    await context.setOffline(true)
    await editor.fill(localText)
    await web.getByRole('button', { name: '保存正文到浏览器' }).click()
    await expect(web.getByText(/本地已保存 · 修订 2/)).toBeVisible()

    const formalChapter = await desktop.update()
    for (const [index, scope] of desktop.scopes.entries()) {
      expect((await desktop.target(scope)).preview.length_target?.targets.min_chars).toBe([90, 50, 12][index])
    }
    const desktopFacts = (await desktop.facts()).filter(({ stale }) => stale === 0)
    expect(desktopFacts).toEqual([
      expect.objectContaining({
        chapter_id: formalChapter.id,
        evidence: quote,
        source_revision: formalChapter.revision,
        evidence_start: desktopText.indexOf(quote),
        evidence_end: desktopText.indexOf(quote) + quote.length,
      }),
    ])
    expect(await startNative('run')).toMatchObject({ conflicts: [], deferred: [], pending: 0 })
    const published = await checkpoint()
    const actual = await checked<PageSlice>(
      await page.request.get(`${endpoint}/pull?after=${baseline.cursor}&limit=100`, { headers: auth }),
    )
    expect(actual.changes.map(({ payload }) => payload?.kind)).toEqual(
      expect.arrayContaining([
        'fact-set-version',
        'fact-set-head',
        'length-target-version',
        'length-target-head',
      ]),
    )

    await context.setOffline(false)
    const after: number[] = []
    let durable = baseline.cursor
    await web.route(`${endpoint}/pull?*`, async (route) => {
      const url = new URL(route.request().url())
      after.push(Number(url.searchParams.get('after')))
      if (after.length === 1) {
        url.searchParams.set('limit', '1')
        const first = await route.fetch({ url: url.toString() })
        const data = await checked<PageSlice>(first)
        expect(data.has_more).toBe(true)
        durable = data.cursor
        await route.fulfill({ response: first })
      } else if (after.length === 2) {
        await route.abort('connectionreset')
      } else await route.continue()
    })
    await syncOnce()
    expect(after).toEqual([baseline.cursor, durable])
    await expect(web.getByText(/已收到云端更新，\d+ 项待应用到本机/)).toBeVisible()
    await expect(editor).toHaveValue(localText)
    await context.setOffline(true)
    await web.reload()
    await credentials()
    await expect(web.getByText(/已收到云端更新，\d+ 项待应用到本机/)).toBeVisible()
    await chapterButton.press('Enter')
    await expect(editor).toHaveValue(localText)
    await context.setOffline(false)
    await syncOnce()
    expect(after[2]).toBe(durable)
    await expect(editor).toHaveValue(localText)
    await expect(web.getByText(/^fact-set-head:[a-f0-9]{64} · /)).toBeVisible()
    await rules.press('Enter')
    await expect(web.getByText(/冻结篇幅目标 至少 90 字符/)).toBeVisible()
    await expect(facts).toHaveCount(0)
    await web.getByRole('button', { name: /^比较冲突/ }).click()
    const compare = web.getByRole('region', { name: '云同步冲突比较' })
    web.once('dialog', (dialog) => dialog.accept())
    await compare.getByRole('button', { name: '采用云端当前版', exact: true }).click()
    await expect(editor).toHaveValue(desktopText)
    await syncOnce()
    await expect(facts).toHaveText('正式故事事实（1）')
    await facts.press('Enter')
    await expect(web.getByText(statement, { exact: true })).toBeVisible()
    await expect(web.locator('blockquote').filter({ hasText: quote })).toHaveText(quote)
    await web.getByText('章与卷的篇幅', { exact: true }).press('Enter')
    await expect(web.getByRole('list', { name: '分卷篇幅' })).toContainText('冻结篇幅目标 至少 50 字符')
    await expect(web.getByRole('list', { name: '分章篇幅' })).toContainText('冻结篇幅目标 至少 12 字符')
    await expect(web.getByText(/^fact-set-head:[a-f0-9]{64} · /)).toHaveCount(0)

    const beforeRestore = await checkpoint()
    expect(beforeRestore.cursor).toBeGreaterThanOrEqual(published.cursor)
    await context.setOffline(true)
    const archive = web.getByLabel('已导入的卷书恢复点', { exact: true })
    await archive.getByText('已导入的卷书恢复点', { exact: true }).press('Enter')
    const picker = archive.getByRole('combobox', { name: '选择已归档恢复点' })
    const preview = archive.getByRole('button', { name: '预览归档原位恢复', exact: true })
    const impact = archive.getByRole('region', { name: '归档原位恢复预览' })
    const recovery = archive.getByRole('status').filter({ hasText: '已恢复为新的当前修订' }).locator('code')
    async function confirmRestore() {
      await impact.getByRole('checkbox', { name: '我已核对恢复影响', exact: true }).check()
      await impact.getByRole('button', { name: '原位恢复为新修订', exact: true }).press('Enter')
      await expect(recovery).toHaveText(/^[a-z0-9_-]+$/)
    }
    await picker.selectOption(desktop.snapshot.id)
    await preview.press('Enter')
    await impact.getByText('恢复范围说明', { exact: true }).press('Enter')
    await expect(impact.getByText('更新 3 项篇幅目标。', { exact: true })).toBeVisible()
    await confirmRestore()
    const recoveryId = (await recovery.textContent())!
    await expect(editor).toHaveValue(oldText)
    await expect(facts).toHaveCount(0)
    await expect(web.getByText(/篇幅目标已清除/)).toHaveCount(3)
    expect(await checkpoint()).toEqual(beforeRestore)

    await context.setOffline(false)
    await syncOnce()
    const restore = await checkpoint()
    expect(restore.cursor).toBeGreaterThan(beforeRestore.cursor)
    const cleared = await checked<PageSlice>(
      await page.request.get(`${endpoint}/pull?after=${beforeRestore.cursor}&limit=100`, { headers: auth }),
    )
    expect(cleared.changes.map(({ payload }) => payload?.kind)).toEqual(
      expect.arrayContaining([
        'fact-set-version',
        'fact-set-head',
        'length-target-version',
        'length-target-head',
      ]),
    )
    const recorded = cleared.changes.map(({ payload }) => payload)
    expect(recorded).toContainEqual(
      expect.objectContaining({
        kind: 'fact-set-version',
        version: expect.objectContaining({ chapter_id: formalChapter.id, facts: [] }),
      }),
    )
    for (const scope of desktop.scopes) {
      expect(recorded).toContainEqual(
        expect.objectContaining({
          kind: 'length-target-version',
          version: expect.objectContaining({ ...scope, targets: null }),
        }),
      )
    }
    expect(await startNative('run')).toMatchObject({ conflicts: [], deferred: [], pending: 0 })
    const restoredChapter = await desktop.get<Chapter>(`/api/chapters/${formalChapter.id}`)
    expect(restoredChapter.content).toBe(oldText)
    expect(restoredChapter.revision).toBeGreaterThan(formalChapter.revision)
    expect((await desktop.facts()).filter(({ stale }) => stale === 0)).toEqual([])
    expect(await desktop.facts()).toContainEqual(expect.objectContaining({ evidence: quote, stale: 1 }))
    for (const scope of desktop.scopes) expect((await desktop.target(scope)).preview.length_target).toBeNull()

    const desktopAccepted = await checkpoint()
    await context.setOffline(true)
    await picker.selectOption(recoveryId)
    await preview.press('Enter')
    await impact.getByText('恢复范围说明', { exact: true }).press('Enter')
    await expect(impact.getByText('按归档恢复 1 条事实及其章稿引用。', { exact: true })).toBeVisible()
    await confirmRestore()
    await expect(editor).toHaveValue(desktopText)
    await expect(facts).toHaveText('正式故事事实（1）')
    expect(await checkpoint()).toEqual(desktopAccepted)

    await context.setOffline(false)
    await syncOnce()
    expect(await startNative('run')).toMatchObject({ conflicts: [], deferred: [], pending: 0 })
    const reestablished = await desktop.get<Chapter>(`/api/chapters/${formalChapter.id}`)
    expect(reestablished.content).toBe(desktopText)
    expect(reestablished.revision).toBeGreaterThan(restoredChapter.revision)
    expect((await desktop.facts()).filter(({ stale }) => stale === 0)).toEqual([
      expect.objectContaining({
        chapter_id: formalChapter.id,
        statement,
        evidence: quote,
        source_revision: reestablished.revision,
        evidence_start: desktopText.indexOf(quote),
        evidence_end: desktopText.indexOf(quote) + quote.length,
      }),
    ])
    for (const [index, scope] of desktop.scopes.entries()) {
      expect((await desktop.target(scope)).preview.length_target?.targets.min_chars).toBe([90, 50, 12][index])
    }
  } finally {
    await context.close()
  }
})

function syncFixture(page: Page, desktop: Awaited<ReturnType<typeof prepareDesktop>>) {
  const branchId = desktop.novel.branch_id
  const endpoint = `${cloud}/api/sync/works/${branchId}`
  const grant = async (device_id: string) =>
    (
      await checked<{ token: string }>(
        await page.request.post(`${endpoint}/devices`, { headers: provision, data: { device_id } }),
      )
    ).token
  const auth = (token: string) => ({ Authorization: `Bearer ${token}` })
  const checkpoint = (token: string) =>
    page.request
      .get(`${endpoint}/checkpoint`, { headers: auth(token) })
      .then(checked<{ ledger_id: string; cursor: number }>)
  const changes = async (token: string, after: number) => {
    const { ledger_id } = await checkpoint(token)
    return checked<PageSlice>(
      await page.request.get(`${endpoint}/pull?after=${after}&limit=100`, {
        headers: { ...auth(token), 'X-Ainovel-Ledger': ledger_id },
      }),
    )
  }
  const native = (action: 'enable' | 'run', token: string) =>
    desktop.post<{
      cursor: number
      pending: number
      conflicts: unknown[]
      deferred: unknown[]
    }>(`${desktop.branch}/desktop-sync/${action}`, { origin: cloud, token })
  const syncWeb = async (web: Page) => {
    const button = web.getByRole('button', { name: '同步当前工作版本', exact: true })
    await expect(button).toBeEnabled()
    const checking = web.waitForResponse((response) => response.url() === `${endpoint}/checkpoint`)
    await button.click()
    expect((await checking).ok()).toBe(true)
    await expect(button).toBeEnabled()
  }
  const enterWebCredentials = async (web: Page, device: string, token: string) => {
    const work = web.getByRole('combobox', { name: '离线作品', exact: true })
    await expect(work.locator(`option[value="${desktop.novel.id}"]`)).toHaveCount(1)
    await work.selectOption(desktop.novel.id)
    await web.getByText('作品云同步', { exact: true }).click()
    await web.getByLabel('同步服务地址').fill(cloud)
    await web.getByLabel('设备编号').fill(device)
    await web.getByLabel('设备凭据').fill(token)
  }
  const openWeb = async (web: Page, device: string, token: string, backup: Buffer) => {
    await web.goto('/offline/')
    await expect.poll(() => web.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
    await web.getByLabel('从独立备份恢复作品', { exact: true }).setInputFiles({
      name: '同步共同基线.ainovel-backup',
      mimeType: 'application/octet-stream',
      buffer: backup,
    })
    await expect(web.getByText('已校验并恢复独立作品备份。', { exact: true })).toBeVisible()
    await enterWebCredentials(web, device, token)
    await web.getByRole('button', { name: '启用作品同步', exact: true }).click()
    await syncWeb(web)
  }
  return { endpoint, auth, grant, checkpoint, changes, native, syncWeb, openWeb, enterWebCredentials }
}

async function exportWebPackage(web: Page): Promise<Package> {
  const downloading = web.waitForEvent('download')
  await web.getByRole('button', { name: '导出稿件交换包', exact: true }).click()
  return JSON.parse(await readFile((await (await downloading).path())!, 'utf-8'))
}

async function noUnapplied(web: Page, cursor: number) {
  await expect(web.getByText(`已启用 · 游标 ${cursor} · 0 条待发送或确认`, { exact: true })).toBeVisible()
  await expect(web.getByText(/已收到云端更新，\d+ 项待应用到本机/)).toHaveCount(0)
}

for (const sender of ['桌面', '独立 Web']) {
  const move = sender === '桌面' ? '桌面移除' : '独立 Web 移除'
  test(`${move}旧卷时向另一端清空绑定历史大纲的篇幅目标`, async ({ page, browser, baseURL }) => {
    test.setTimeout(90_000)
    const desktop = await prepareDesktop(page)
    const sync = syncFixture(page, desktop)
    const outlinePath = `${desktop.branch}/outline-workspace`
    type Outline = { id: string; revision: number; plan: components['schemas']['OutlinePlan'] }
    const readOutline = async () => (await desktop.get<{ confirmed: Outline }>(outlinePath)).confirmed
    const story = await readOutline()
    const replacementId = 'replacement-volume'
    const plan = await desktop.put<{ draft: { revision: number } }>(outlinePath, {
      base_revision: story.revision,
      confirmation_id: null,
      plan: {
        ...story.plan,
        volumes: story.plan.volumes?.map((volume) => ({ ...volume, id: replacementId, title: '替代卷' })),
      },
    })
    await desktop.post(`${outlinePath}/confirm`, { base_revision: plan.draft.revision })
    const replacement = await desktop.post<{ id: string }>(`${desktop.branch}/work-snapshots`, {
      label: '只保留替代卷',
      scope: 'book',
    })
    async function restoreOnDesktop(snapshotId: string) {
      const route = `${desktop.branch}/work-snapshots/${snapshotId}`
      const preview = await desktop.get<{ basis_sha256: string; in_place_blockers: string[] }>(
        `${route}/preview`,
      )
      expect(preview.in_place_blockers).toEqual([])
      await desktop.post(`${route}/restore`, {
        basis_sha256: preview.basis_sha256,
        mode: 'in_place',
        idempotency_key: randomUUID(),
      })
    }
    await restoreOnDesktop(desktop.snapshot.id)
    const priorOutline = await readOutline()
    expect(priorOutline.plan.volumes?.map(({ id }) => id)).toEqual([volumeId])
    const backup = await page.request.get(`${desktop.branch}/manuscript-backup`)
    expect(backup.ok()).toBe(true)
    const common = await backup.body()
    await backup.dispose()
    await desktop.put(`${desktop.branch}/workflow-inspections/length-targets`, {
      scope: 'volume',
      scope_id: volumeId,
      targets: { min_chars: 50 },
    })

    const desktopToken = await sync.grant('retired-volume-desktop')
    const webName = 'retired-volume-web'
    const webToken = await sync.grant(webName)
    const settledNative = { conflicts: [], deferred: [], pending: 0 }
    await sync.native('enable', desktopToken)
    expect(await sync.native('run', desktopToken)).toMatchObject(settledNative)
    const context = await browser.newContext({ baseURL })
    try {
      const web = await context.newPage()
      await sync.openWeb(web, webName, webToken, common)
      const showVolume = async () => {
        const list = web.getByRole('list', { name: '分卷篇幅' })
        if (!(await list.isVisible())) await web.getByText('章与卷的篇幅', { exact: true }).click()
        return list
      }
      await web.getByText('本机规则核算', { exact: true }).click()
      await expect(await showVolume()).toContainText('冻结篇幅目标 至少 50 字符')
      const before = await sync.checkpoint(webToken)
      const scope = { scope: 'volume', scope_id: volumeId, targets: null }
      if (sender === '桌面') {
        await restoreOnDesktop(replacement.id)
        expect(await sync.native('run', desktopToken)).toMatchObject(settledNative)
        await sync.syncWeb(web)
      } else {
        await context.setOffline(true)
        const history = web.getByLabel('已导入的卷书恢复点', { exact: true })
        await history.getByText('已导入的卷书恢复点', { exact: true }).click()
        await history.getByRole('combobox', { name: '选择已归档恢复点' }).selectOption(replacement.id)
        await history.getByRole('button', { name: '预览归档原位恢复' }).click()
        const preview = history.getByRole('region', { name: '归档原位恢复预览' })
        await preview.getByRole('checkbox', { name: '我已核对恢复影响', exact: true }).check()
        await preview.getByRole('button', { name: '原位恢复为新修订', exact: true }).click()
        await expect(history.getByRole('status').filter({ hasText: '已恢复为新的当前修订' })).toBeVisible()
        expect((await exportWebPackage(web)).restore_state?.length_targets).toContainEqual(scope)
        expect(await sync.checkpoint(webToken)).toEqual(before)
        await context.setOffline(false)
        await sync.syncWeb(web)
        expect(await sync.native('run', desktopToken)).toMatchObject(settledNative)
      }

      const moved = await readOutline()
      expect(moved.plan.volumes?.map(({ id }) => id)).toEqual([replacementId])
      expect(moved.id).not.toBe(priorOutline.id)
      const { changes } = await sync.changes(webToken, before.cursor)
      const clear = changes.find(({ payload }) => payload?.kind === 'length-target-version')?.payload?.version
      expect(clear).toMatchObject({ ...scope, outline_version_id: priorOutline.id })
      expect(changes.map(({ payload }) => payload)).toContainEqual(
        expect.objectContaining({
          kind: 'length-target-head',
          revision_id: clear?.id,
        }),
      )
      const storedDesktop = await desktop.get<Package>(`${desktop.branch}/manuscript-package`)
      expect(storedDesktop.restore_state?.length_targets).toContainEqual(scope)
      expect(await showVolume()).toContainText('替代卷')
      const storedWeb = await exportWebPackage(web)
      expect(storedWeb.outline_history?.adopted_revision_id).toBe(
        storedDesktop.outline_history?.adopted_revision_id,
      )
      expect(storedWeb.restore_state?.length_targets).toContainEqual(scope)
      const settled = await sync.checkpoint(webToken)
      await sync.syncWeb(web)
      await noUnapplied(web, settled.cursor)
      expect(await sync.checkpoint(webToken)).toEqual(settled)
    } finally {
      await context.close()
    }
  })
}

test('已归档章节可采用引用历史章稿的空事实，断网重开后仍保持归档', async ({ page, browser, baseURL }) => {
  test.setTimeout(90_000)
  const desktop = await prepareDesktop(page)
  const sync = syncFixture(page, desktop)
  const { id: chapterId, revision: oldRevision } = desktop.baselineChapter
  const packPath = `${desktop.branch}/manuscript-package`
  const baseline = await desktop.get<Package>(packPath)
  const oldSource = baseline.revisions.find(
    ({ chapter_id, revision }) => chapter_id === chapterId && revision === oldRevision,
  )!
  const desktopToken = await sync.grant('archived-fact-desktop')
  const webName = 'archived-fact-web'
  const webToken = await sync.grant(webName)
  const authorToken = await sync.grant('archived-fact-author')
  await sync.native('enable', desktopToken)
  expect(await sync.native('run', desktopToken)).toMatchObject({ conflicts: [], deferred: [], pending: 0 })
  const context = await browser.newContext({ baseURL })
  try {
    const web = await context.newPage()
    await sync.openWeb(web, webName, webToken, desktop.backup)
    const current = await desktop.update()
    const book = await desktop.get<Package>(packPath)
    const latest = book.revisions.find(
      ({ chapter_id, revision }) => chapter_id === chapterId && revision === current.revision,
    )!
    expect(latest.id).not.toBe(oldSource.id)
    expect(await sync.native('run', desktopToken)).toMatchObject({ conflicts: [], deferred: [], pending: 0 })
    await sync.syncWeb(web)
    await web.getByText('本机规则核算', { exact: true }).click()
    await expect(web.getByText('正式故事事实（1）', { exact: true })).toBeVisible()
    const before = await sync.checkpoint(webToken)
    await desktop.post(`/api/chapters/${chapterId}/archive`, {
      base_revision: current.revision,
      archived: true,
    })
    expect(await sync.native('run', desktopToken)).toMatchObject({ conflicts: [], deferred: [], pending: 0 })
    const archived = await sync.changes(authorToken, before.cursor)
    expect(archived.changes).toContainEqual(
      expect.objectContaining({ object_id: `chapter:${chapterId}`, payload: null }),
    )
    const preceding = [...archived.changes]
      .reverse()
      .find(({ payload }) => payload?.kind === 'fact-set-head')!
    const digest = (value: unknown) =>
      createHash('sha256').update(JSON.stringify(value), 'utf-8').digest('hex')
    const branch_id = desktop.novel.branch_id
    const format = 'ainovel-manuscript-sync/1'
    // This device clears the archival facts using a real, older revision of the same chapter.
    const draft = {
      chapter_id: chapterId,
      chapter_revision_id: oldSource.id,
      facts: [],
      parent_id: preceding.payload!.revision_id,
    }
    const id = digest([branch_id, draft])
    const send = (object_id: string, payload: unknown, parent_revision: string | null) =>
      page.request
        .post(`${sync.endpoint}/push`, {
          headers: { ...sync.auth(authorToken), 'X-Ainovel-Ledger': before.ledger_id },
          data: { change_id: randomUUID(), object_id, payload, parent_revision, tombstone: false },
        })
        .then(checked<{ status: string }>)
    expect(
      await send(
        `fact-set-version:${id}`,
        {
          format,
          branch_id,
          kind: 'fact-set-version',
          version: { id, ...draft },
        },
        null,
      ),
    ).toMatchObject({ status: 'applied' })
    expect(
      await send(
        preceding.object_id,
        {
          format,
          branch_id,
          kind: 'fact-set-head',
          chapter_id: chapterId,
          revision_id: id,
        },
        preceding.head_revision,
      ),
    ).toMatchObject({ status: 'applied' })
    const published = await sync.checkpoint(webToken)
    await sync.syncWeb(web)
    await noUnapplied(web, published.cursor)
    expect(await sync.checkpoint(webToken)).toEqual(published)
    await expect(web.getByRole('button', { name: `2. ${secondTitle}`, exact: true })).toHaveCount(0)
    await context.setOffline(true)
    await web.reload()
    await sync.enterWebCredentials(web, webName, webToken)
    await noUnapplied(web, published.cursor)
    const saved = await exportWebPackage(web)
    expect(saved.chapters.find(({ id }) => id === chapterId)?.archived).toBe(1)
    expect(saved.revisions.filter(({ chapter_id }) => chapter_id === chapterId).map(({ id }) => id)).toEqual(
      book.revisions.filter(({ chapter_id }) => chapter_id === chapterId).map(({ id }) => id),
    )
    expect(saved.restore_state?.facts ?? []).toEqual([])
    await context.setOffline(false)
    await sync.syncWeb(web)
    expect(await sync.checkpoint(webToken)).toEqual(published)
  } finally {
    await context.close()
  }
})
