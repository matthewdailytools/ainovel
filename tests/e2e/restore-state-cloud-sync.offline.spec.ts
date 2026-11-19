import { expect, test, type APIResponse, type Page } from '@playwright/test'

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
type CloudChange = { object_id: string; payload: { kind: string; version?: Record<string, unknown> } | null }
type PageSlice = { cursor: number; has_more: boolean; changes: CloudChange[] }

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
  return { novel, branch, get, post, facts, target, scopes, snapshot, backup, update, baselineChapter }
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
