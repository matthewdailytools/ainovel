import { expect, test, type APIRequestContext, type APIResponse } from '@playwright/test'
import { spawn } from 'node:child_process'
import { createHash, randomUUID } from 'node:crypto'
import { mkdir, readFile } from 'node:fs/promises'

const cloud = process.env.AINOVEL_TEST_SYNC_URL ?? 'http://127.0.0.1:8888'
const provisioning = { Authorization: 'Bearer e2e-test-only-provisioning-token' }
const title = '卷点原位恢复 · 渡口灯'
const oldQuote = '守灯人在渡口接过铜铃'
const currentQuote = '学徒把铜铃留在木盒'
const oldText = `😀雾退后，${oldQuote}。`
const currentText = `😀入夜后，${currentQuote}。`
const oldGoal = '在渡口当面归还铜铃'
const currentGoal = '把铜铃暂存在大厅木盒'
const untouchedText = '北岸的学徒还在找开门的钥匙。'
const concurrentText = '另一窗口在前卷确认了钥匙的位置。'
const hash = (value: string) => createHash('sha256').update(value, 'utf8').digest('hex')

test.skip(process.env.AINOVEL_SYNC_E2E !== '1', '使用两套隔离 SQLite、断网 Web 和回环同步服务')

const isolatedSource = `
import socket, sys
from pathlib import Path
from threading import Thread
import uvicorn
from ainovel.api import create_app
from ainovel.settings import CAPS, Settings

with socket.socket() as listener:
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    limits = {kind: maximum for kind, (_, maximum) in CAPS.items()}
    settings = Settings(Path(sys.argv[1]), {}, limits, test_mode=True, port=port)
    server = uvicorn.Server(uvicorn.Config(create_app(settings), host='127.0.0.1', port=port, access_log=False))

    def stop():
        sys.stdin.buffer.read()
        server.should_exit = True

    Thread(target=stop, daemon=True).start()
    print(port, flush=True)
    server.run(sockets=[listener])
`

async function sourceDesktop(directory: string) {
  await mkdir(directory, { recursive: true })
  const python = process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python'
  const child = spawn(python, ['-u', '-c', isolatedSource, directory], { windowsHide: true })
  let port = ''
  let diagnostics = ''
  const closed = new Promise((resolve) => child.once('close', resolve))
  child.stdout.setEncoding('utf8').on('data', (text) => (port += text))
  child.stderr.setEncoding('utf8').on('data', (text) => (diagnostics += text))
  child.on('error', (error) => (diagnostics += error.message))
  child.stdin.on('error', (error) => (diagnostics += error.message))
  async function stop() {
    child.stdin.end()
    const deadline = setTimeout(() => child.kill(), 5000)
    try {
      await closed
    } finally {
      clearTimeout(deadline)
    }
  }
  try {
    await expect.poll(() => port.trim(), { timeout: 15_000 }).toMatch(/^\d+$/)
    return { origin: `http://127.0.0.1:${port.trim()}`, stop }
  } catch (error) {
    await stop()
    throw new Error(`无法启动归档源实例。${diagnostics}`, { cause: error })
  }
}

async function okay(response: Pick<APIResponse, 'ok' | 'text' | 'json'>) {
  expect(response.ok(), await response.text()).toBe(true)
  return response.json()
}

async function apiClient(request: APIRequestContext) {
  const { csrf } = await okay(await request.get('/api/session'))
  const headers = { 'X-Ainovel-CSRF': csrf }
  return {
    headers,
    read: async (path: string) => okay(await request.get(path)),
    post: async (path: string, data: unknown) => okay(await request.post(path, { headers, data })),
    put: async (path: string, data: unknown) => okay(await request.put(path, { headers, data })),
  }
}

async function inspectChapter(api: Awaited<ReturnType<typeof apiClient>>, branch: string, chapter: string) {
  const inspections = `${branch}/workflow-inspections`
  const { preview } = await api.read(`${inspections}?scope=chapter&scope_id=${chapter}`)
  return api.post(inspections, { scope: 'chapter', scope_id: chapter, basis_sha256: preview.basis_sha256 })
}

test('导入的卷点原位回滚并经隔离云续传，保留卷外正式稿和历史依据', async ({
  page,
  browser,
  baseURL,
  playwright,
}, testInfo) => {
  test.setTimeout(120_000)
  const source = await sourceDesktop(testInfo.outputPath('restore-origin'))
  const sourceRequest = await playwright.request.newContext({ baseURL: source.origin })
  const webContext = await browser.newContext({ baseURL })
  try {
    await expect
      .poll(
        async () => {
          try {
            const response = await sourceRequest.get('/api/config', { timeout: 1000 })
            const ready = response.ok()
            await response.dispose()
            return ready
          } catch {
            return false
          }
        },
        { timeout: 15_000 },
      )
      .toBe(true)
    const origin = await apiClient(sourceRequest)
    const novel = await origin.post('/api/novels', { title })
    const branch = `/api/branches/${novel.branch_id}`
    const seed = await origin.read(`${branch}/manuscript-package`)
    const chapterTitles = ['岸边找钥匙', '辨认灯罩', '渡口寻铃', '听见旧铃声']
    const chapters = []
    for (const title of chapterTitles) {
      chapters.push(await origin.post(`${branch}/chapters`, { title }))
    }
    const [first, other, selected, last] = chapters
    await origin.put(`/api/chapters/${first.id}`, { base_revision: 0, content: untouchedText })
    await origin.put(`/api/chapters/${other.id}`, { base_revision: 0, content: '师姐记下旧灯罩上的暗号。' })
    await origin.put(`/api/chapters/${selected.id}`, { base_revision: 0, content: '学徒走到靠岸的渡口。' })
    const opening = await origin.put(`/api/chapters/${last.id}`, { base_revision: 0, content: oldText })

    const project = await origin.put(`${branch}/conception/project-brief`, {
      base_revision: 0,
      idempotency_key: randomUUID(),
      idea: '找回渡口失落的铜铃。',
      brief: {
        position: '追查铜铃归属',
        track: '失物悬疑',
        genre_ids: ['xianxia'],
        audience: '喜欢失物线索的读者',
        distinction: '凭早先的暗号认回铜铃',
        length: '两卷四章',
        ending: '铜铃归还给守灯人。',
      },
    })
    const conception = `${branch}/conception`
    const workspace = await origin.read(conception)
    const world = workspace.categories.find(({ id }: { id: string }) => id === 'world')
    world.items = [
      {
        id: 'bell-source-rule',
        title: '暗号归属',
        detail: '守灯人可以辨认旧灯罩上的暗号。',
        adoption_state: 'adopted',
        setting_policy: { strength: 'hard', scope: '渡口认物' },
      },
    ]
    const edited = await origin.put(conception, {
      base_revision: workspace.revision,
      base_story_revision: workspace.story_revision,
      categories: workspace.categories,
    })
    const { confirmation: setting } = await origin.post(`${conception}/confirm`, {
      base_revision: edited.revision,
      base_story_revision: edited.story_revision,
      idempotency_key: randomUUID(),
    })
    function promiseBody(revision: number, text: string, quote: string) {
      const start = text.indexOf(quote)
      expect(start).toBeGreaterThanOrEqual(2)
      return {
        kind: 'promise',
        title: '守灯人接回铜铃',
        stages: [
          { status: 'planned' },
          {
            status: 'resolved',
            sources: [{ chapter_id: last.id, revision, start, end: start + quote.length, quote }],
          },
        ],
      }
    }
    const withoutStory = await origin.read(`${branch}/story-graph`)
    const preparedStory = await origin.post(`${branch}/story-records`, {
      base_story_revision: withoutStory.story_revision,
      record: promiseBody(opening.revision, oldText, oldQuote),
    })
    const promise = preparedStory.records[0]
    const outlineRoute = `${branch}/outline-workspace`
    async function confirmPlan(base_revision: number, goal: string, crossesVolumes = false) {
      const draft = await origin.put(outlineRoute, {
        base_revision,
        confirmation_id: setting.id,
        plan: {
          summary: '从岸边暗号追到铜铃归属。',
          volumes: [
            [first, other],
            [selected, last],
          ].map((members, i) => ({
            id: `volume-${i + 1}`,
            title: i === 0 ? '辨灯卷' : '归铃卷',
            summary: '逐条核对原稿。',
            units: [
              {
                id: `unit-${i + 1}`,
                title: '旧灯和铜铃',
                summary: '用在场证据推进。',
                chapters: members.map((chapter) => ({
                  chapter_id: chapter.id,
                  goal: chapter.id === last.id ? goal : '从岸边向渡口找线索',
                  obstacle: '水面白雾遮住视线',
                  turn: '灯影照见岸边石阶',
                  result: '能跟踪旧铜铃的去处',
                  setting_ids: ['bell-source-rule'],
                })),
              },
            ],
          })),
          promises: [
            {
              promise_id: promise.id,
              plant_chapter_id: crossesVolumes ? other.id : selected.id,
              opens_chapter_id: last.id,
              closes_chapter_id: last.id,
              fulfillment: 'author_required',
            },
          ],
        },
      })
      return (
        await origin.post(`${outlineRoute}/confirm`, {
          base_revision: draft.draft.revision,
          acknowledge_required_promises: true,
        })
      ).confirmed
    }
    const oldOutline = await confirmPlan(0, oldGoal)
    const snapshots = `${branch}/work-snapshots`
    const oldReport = await inspectChapter(origin, branch, last.id)
    const check = await origin.post(snapshots, { label: '铜铃原卷点', scope: 'volume', scope_id: 'volume-2' })
    const crossed = await confirmPlan(oldOutline.revision, oldGoal, true)
    const blocked = await origin.post(snapshots, {
      label: '跨卷伏笔点',
      scope: 'volume',
      scope_id: 'volume-2',
    })
    const changed = await origin.put(`/api/chapters/${last.id}`, {
      base_revision: opening.revision,
      content: currentText,
    })
    const withStory = await origin.read(`${branch}/story-graph`)
    await origin.put(`${branch}/story-records/${promise.id}`, {
      base_story_revision: withStory.story_revision,
      base_revision: promise.revision,
      record: promiseBody(changed.revision, currentText, currentQuote),
    })
    await confirmPlan(crossed.revision, currentGoal)
    const downloaded = await sourceRequest.get(`${branch}/manuscript-backup`)
    expect(downloaded.ok(), await downloaded.text()).toBe(true)
    const backup = await downloaded.body()
    await downloaded.dispose()

    await page.setViewportSize({ width: 420, height: 900 })
    await page.goto('/')
    const target = await apiClient(page.request)
    await page.getByText('离线写作与交换', { exact: true }).click()
    const applied = page.waitForResponse((response) =>
      response.url().endsWith('/api/manuscript-backups/import'),
    )
    await page.getByLabel('导入独立作品备份').setInputFiles({
      name: '原位回滚验收.ainovel-backup',
      mimeType: 'application/octet-stream',
      buffer: backup,
    })
    expect((await applied).status()).toBe(201)
    await expect(page.getByText('已从独立备份导入新作品。', { exact: true })).toBeVisible()
    await expect(page.getByRole('combobox', { name: '工作版本', exact: true })).toHaveValue(novel.branch_id)
    await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
    const panel = page.getByRole('region', { name: '作品快照与回滚', exact: true })
    await panel.getByText('跨端检查点归档', { exact: true }).press('Enter')
    const select = panel.getByRole('combobox', { name: '已归档检查点', exact: true })
    const openPreview = panel.getByRole('button', { name: '预览归档原位恢复', exact: true })
    const preview = panel.getByRole('region', { name: '归档原位恢复预览', exact: true })
    const submit = preview.getByRole('button', { name: '原位恢复为新修订', exact: true })
    const archives = `${branch}/portable-checkpoints`
    const restoreRoute = `${archives}/${check.id}/restore`

    await select.selectOption(blocked.id)
    await openPreview.press('Enter')
    await expect(preview).toContainText(/伏笔窗口跨越|伏笔.*卷边界/)
    await expect(submit).toBeDisabled()
    await select.selectOption(check.id)
    await openPreview.press('Enter')
    const consent = preview.getByRole('checkbox', { name: '我已核对恢复影响', exact: true })
    await expect(consent).toBeEnabled()
    await consent.check()
    const preexistingRecovery = (await target.read(snapshots)).filter(
      ({ purpose }: { purpose: string }) => purpose === 'pre_restore',
    )
    expect(preexistingRecovery).toHaveLength(0)
    const parent = await target.read(`/api/chapters/${other.id}`)
    const kept = await target.put(`/api/chapters/${other.id}`, {
      base_revision: parent.revision,
      content: concurrentText,
    })
    const stale = page.waitForResponse(
      (response) => response.url().endsWith(restoreRoute) && response.request().method() === 'POST',
    )
    await expect(submit).toBeEnabled()
    await submit.press('Enter')
    const refused = await stale
    expect(refused.status()).toBe(409)
    expect((await refused.json()).code).toBe('snapshot_changed')
    expect((await target.read(`/api/chapters/${last.id}`)).content).toBe(currentText)
    expect(
      (await target.read(snapshots)).filter(({ purpose }: { purpose: string }) => purpose === 'pre_restore'),
    ).toEqual(preexistingRecovery)
    const impacted = await inspectChapter(target, branch, last.id)
    const unaffected = await inspectChapter(target, branch, first.id)

    const endpoint = `${cloud}/api/sync/works/${novel.branch_id}`
    const desktopDevice = 'portable-restore-desktop'
    const webDevice = 'portable-restore-web'
    async function grant(device_id: string) {
      return (
        await okay(
          await page.request.post(`${endpoint}/devices`, { headers: provisioning, data: { device_id } }),
        )
      ).token as string
    }
    const desktopToken = await grant(desktopDevice)
    const webToken = await grant(webDevice)
    const checkpoint = async (): Promise<{ ledger_id: string; cursor: number }> =>
      okay(
        await page.request.get(`${endpoint}/checkpoint`, {
          headers: { Authorization: `Bearer ${webToken}` },
        }),
      )
    const native = page.getByRole('region', { name: '作品云同步', exact: true })
    await native.getByLabel('同步服务地址').fill(cloud)
    await native.getByLabel('设备凭据').fill(desktopToken)
    await native.getByRole('button', { name: '启用作品同步', exact: true }).click()
    const nativeRun = native.getByRole('button', { name: '同步当前工作版本', exact: true })
    async function nativeSync() {
      await expect(nativeRun).toBeEnabled()
      const handled = page.waitForResponse(
        (response) => response.url().endsWith('/desktop-sync/run') && response.request().method() === 'POST',
      )
      await nativeRun.press('Enter')
      const state = await okay(await handled)
      expect(state.conflicts).toHaveLength(0)
      expect(state.deferred).toHaveLength(0)
    }
    await nativeSync()
    const baseline = await checkpoint()

    const web = await webContext.newPage()
    await web.goto('/offline/')
    await expect.poll(() => web.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
    await web.getByLabel('导入新离线作品').setInputFiles({
      name: '共同基线.json',
      mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify(seed)),
    })
    async function connectWeb() {
      await web.getByRole('combobox', { name: '离线作品', exact: true }).selectOption(novel.id)
      await web.getByText('作品云同步', { exact: true }).click()
      await web.getByLabel('同步服务地址').fill(cloud)
      await web.getByLabel('设备编号').fill(webDevice)
      await web.getByLabel('设备凭据').fill(webToken)
    }
    await connectWeb()
    await web.getByRole('button', { name: '启用作品同步', exact: true }).click()
    const webRun = web.getByRole('button', { name: '同步当前工作版本', exact: true })
    const syncStatus = (cursor: number) =>
      web.getByText(`已启用 · 游标 ${cursor} · 0 条待发送或确认`, { exact: true })
    const openLast = () => web.getByRole('button', { name: '4. 听见旧铃声', exact: true }).click()
    const editor = web.getByLabel('章节正文')
    await expect(webRun).toBeEnabled()
    await webRun.press('Enter')
    await expect(syncStatus(baseline.cursor)).toBeVisible()
    await openLast()
    await expect(editor).toHaveValue(currentText)

    const beforeRestore = await target.read(`${branch}/manuscript-package`)
    await openPreview.press('Enter')
    await expect(consent).toBeEnabled()
    const affected = preview.getByRole('region', { name: '受影响报告', exact: true })
    await expect(affected).toContainText(impacted.id)
    await expect(affected).not.toContainText(unaffected.id)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(420)
    await consent.check()
    const applying = page.waitForResponse(
      (response) => response.url().endsWith(restoreRoute) && response.request().method() === 'POST',
    )
    await expect(submit).toBeEnabled()
    await submit.press('Enter')
    const appliedRestore = await applying
    const receipt = await okay(appliedRestore)
    expect(receipt.branch_id).toBe(novel.branch_id)
    expect(receipt.snapshot_id).toBe(check.id)
    expect(receipt.recovery_snapshot_id).toBeTruthy()
    expect(await target.post(restoreRoute, appliedRestore.request().postDataJSON())).toEqual(receipt)
    expect(
      (await target.read(snapshots))
        .filter(({ purpose }: { purpose: string }) => purpose === 'pre_restore')
        .map(({ id }: { id: string }) => id),
    ).toEqual([receipt.recovery_snapshot_id])
    await expect(page.getByRole('combobox', { name: '工作版本', exact: true })).toHaveValue(novel.branch_id)
    const restored = await target.read(`/api/chapters/${last.id}`)
    expect(restored.content).toBe(oldText)
    expect(restored.revision).toBeGreaterThan(changed.revision)
    expect(await target.read(`/api/chapters/${other.id}`)).toMatchObject({
      content: concurrentText,
      revision: kept.revision,
    })
    expect((await target.read(`/api/chapters/${first.id}`)).content).toBe(untouchedText)
    const recovery = await target.read(`${snapshots}/${receipt.recovery_snapshot_id}/chapters/${last.id}`)
    expect(recovery.snapshot).toEqual({ revision: changed.revision, content: currentText })
    expect((await target.read(`${branch}/workflow-inspections/${impacted.id}`)).current).toBe(false)
    expect((await target.read(`${branch}/workflow-inspections/${unaffected.id}`)).current).toBe(true)

    await nativeSync()
    const published = await checkpoint()
    expect(published.ledger_id).toBe(baseline.ledger_id)
    expect(published.cursor).toBeGreaterThan(baseline.cursor + 1)
    const pulls: number[] = []
    let durable = 0
    await web.route(`${endpoint}/pull?*`, async (route) => {
      const url = new URL(route.request().url())
      pulls.push(Number(url.searchParams.get('after')))
      if (pulls.length === 1) {
        url.searchParams.set('limit', '1')
        const firstPage = await route.fetch({ url: url.toString() })
        expect(firstPage.ok(), await firstPage.text()).toBe(true)
        const data = await firstPage.json()
        expect(data.has_more).toBe(true)
        expect(data.changes).toHaveLength(1)
        expect(data.changes[0].object_id).toMatch(/^revision:/)
        durable = data.cursor
        await route.fulfill({ response: firstPage })
      } else if (pulls.length === 2) {
        await route.abort('connectionreset')
      } else {
        await route.continue()
      }
    })
    const waiting = web.getByText('已收到云端更新，1 项待应用到本机。当前正式稿可继续查看。', { exact: true })
    await expect(webRun).toBeEnabled()
    await webRun.press('Enter')
    await expect(web.getByRole('alert')).toBeVisible()
    expect(durable).toBe(baseline.cursor + 1)
    expect(pulls).toEqual([baseline.cursor, durable])
    await expect(waiting).toBeVisible()
    await expect(editor).toHaveValue(currentText)
    await webContext.setOffline(true)
    await web.reload()
    await connectWeb()
    await expect(syncStatus(durable)).toBeVisible()
    await expect(waiting).toBeVisible()
    await openLast()
    await expect(editor).toHaveValue(currentText)
    await webContext.setOffline(false)
    await expect(webRun).toBeEnabled()
    await webRun.press('Enter')
    await expect(syncStatus(published.cursor)).toBeVisible()
    await expect(waiting).toHaveCount(0)
    expect(pulls[2]).toBe(durable)
    await openLast()
    await expect(editor).toHaveValue(oldText)
    await web.getByRole('button', { name: '2. 辨认灯罩', exact: true }).click()
    await expect(editor).toHaveValue(concurrentText)

    const desktopAfter = await target.read(`${branch}/manuscript-package`)
    expect(desktopAfter.project_history.adopted_revision_id).toBe(project.id)
    expect(desktopAfter.settings_history.adopted_revision_id).toBe(setting.id)
    const restoredOutlineId = desktopAfter.outline_history.adopted_revision_id
    expect(restoredOutlineId).not.toBe(beforeRestore.outline_history.adopted_revision_id)
    const restoredOutline = desktopAfter.outline_history.versions.find(
      ({ id }: { id: string }) => id === restoredOutlineId,
    )
    expect(restoredOutline.plan.volumes[1].units[0].chapters[1]).toMatchObject({
      chapter_id: last.id,
      goal: oldGoal,
    })
    const ledgerReport = desktopAfter.workflow_history.inspections.find(
      ({ id }: { id: string }) => id === oldReport.id,
    )
    expect(ledgerReport).toBeTruthy()
    await webContext.setOffline(true)
    const exporting = web.waitForEvent('download')
    await web.getByRole('button', { name: '导出稿件交换包', exact: true }).press('Enter')
    const webPack = JSON.parse(await readFile((await (await exporting).path())!, 'utf8'))
    const currentWebStory = webPack.story_history.adopted_heads[promise.id]
    expect(currentWebStory).toBe(desktopAfter.story_history.adopted_heads[promise.id])
    const record = webPack.story_history.versions.find(({ id }: { id: string }) => id === currentWebStory)
    const proof = record.evidence.stages.at(-1)[0]
    const nativeVersion = desktopAfter.revisions.find(
      ({ chapter_id, revision }: { chapter_id: string; revision: number }) =>
        chapter_id === last.id && revision === restored.revision,
    )
    const webVersion = [...webPack.revisions, ...(webPack.source_revisions ?? [])].find(
      ({ id }: { id: string }) => id === proof.revision_id,
    )
    expect(webVersion.id).toBe(nativeVersion.id)
    expect(webVersion.sha256).toBe(hash(oldText))
    expect(webVersion.content.slice(proof.start, proof.end)).toBe(oldQuote)
    expect(proof.quote_sha256).toBe(hash(oldQuote))
    expect(webPack.outline_history.adopted_revision_id).toBe(desktopAfter.outline_history.adopted_revision_id)
    expect(webPack.snapshot_history).toBeUndefined()
  } finally {
    await Promise.all([webContext.close(), sourceRequest.dispose(), source.stop()])
  }
})
