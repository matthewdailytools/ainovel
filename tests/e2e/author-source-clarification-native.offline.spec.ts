import { expect, test, type BrowserContext, type TestInfo } from '@playwright/test'
import { spawn } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { existsSync } from 'node:fs'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import type { Task } from '../../frontend/api'
import { object } from '../../frontend/planRestart'
import type { ClarificationInput } from '../../frontend/authorSourceClarification'
import { assertActualDoubleText } from './helpers/actualTextScale'

const VERSION = 'author-clarification-native/1'
type Ready = {
  version: string
  run_token: string
  pid: number
  parent_pid: number
  origin: string
  source: Task
  story: { id: string; branch_id: string }
  addition: string
  mock_model_calls: number
  real_model_calls: number
  background_worker: boolean
}

async function startNative(info: TestInfo) {
  const evidence = info.outputPath('author-clarification-native'),
    token = randomUUID()
  await mkdir(dirname(evidence), { recursive: true })
  const python = resolve(
    process.env.AINOVEL_E2E_PYTHON ||
      (process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python'),
  )
  if (!existsSync(python)) throw new Error('Native E2E requires the project Python environment: ' + python)
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    PYTHONDONTWRITEBYTECODE: '1',
    PYTHONUTF8: '1',
    PYTHONIOENCODING: 'utf-8',
  }
  for (const name of Object.keys(env))
    if (/^(OPENAI|DEEPSEEK)_API_KEY/.test(name) || ['AINOVEL_DATA_DIR', 'AINOVEL_CONFIG'].includes(name))
      delete env[name]
  const child = spawn(
    python,
    [
      '-B',
      resolve('scripts/e2e_author_clarification_native.py'),
      '--evidence-dir',
      evidence,
      '--run-token',
      token,
    ],
    { cwd: process.cwd(), env, shell: false, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] },
  )
  let exited = false,
    forced = false,
    stdout = '',
    stderr = '',
    ended = false,
    spawnError: Error | undefined
  child.stdout.on('data', (chunk: Buffer) => {
    stdout += chunk.toString('utf8')
  })
  child.stderr.on('data', (chunk: Buffer) => {
    stderr += chunk.toString('utf8')
  })
  child.once('error', (error) => {
    spawnError = error
  })
  child.once('exit', () => {
    exited = true
  })
  const exit = new Promise<number | null>((done) =>
    child.once('close', (code) => {
      ended = true
      done(code)
    }),
  )
  const saveLogs = async () => {
    await mkdir(evidence, { recursive: true })
    await writeFile(resolve(evidence, 'stdout.log'), stdout)
    await writeFile(resolve(evidence, 'stderr.log'), stderr)
  }
  async function stop(passed: boolean) {
    async function terminateOwned() {
      if (exited) return
      forced = true
      if (process.platform === 'win32' && child.pid) {
        await new Promise<void>((done) => {
          const killer = spawn('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], {
            shell: false,
            windowsHide: true,
            stdio: 'ignore',
          })
          const timer = setTimeout(() => {
            killer.kill('SIGKILL')
            done()
          }, 2_500)
          const finished = () => {
            clearTimeout(timer)
            done()
          }
          killer.once('error', finished)
          killer.once('exit', finished)
          killer.once('close', finished)
        })
      } else child.kill('SIGTERM')
    }
    let code: number | null = null
    try {
      if (!ended) {
        try {
          await mkdir(evidence, { recursive: true })
          await writeFile(
            resolve(evidence, 'finish.json'),
            JSON.stringify({ version: VERSION, run_token: token, outcome: passed ? 'passed' : 'failed' }),
            { flag: 'wx' },
          )
        } catch (error) {
          stderr += '\nCould not request graceful stop: ' + String(error)
          await terminateOwned()
        }
        const deadline = Date.now() + 10_000
        while (!ended && Date.now() < deadline) await delay(40)
        if (!ended) {
          await terminateOwned()
          const force = Date.now() + 2_000
          while (!ended && Date.now() < force) await delay(40)
        }
        if (!ended && !exited) child.kill('SIGKILL')
      }
      code = await Promise.race([
        exit,
        delay(3_000).then(() => {
          throw new Error('Owned native process did not exit')
        }),
      ])
    } finally {
      await saveLogs()
    }
    await writeFile(
      resolve(evidence, 'process-closed.json'),
      JSON.stringify({ launcher_pid: child.pid, launcher_exit_observed: exited, exit_code: code, forced }),
    )
    if (passed && code !== 0) throw new Error('Native process audit failed: ' + stderr)
    return code
  }
  try {
    const deadline = Date.now() + 25_000
    while (Date.now() < deadline) {
      if (spawnError) throw spawnError
      if (ended) throw new Error('Native process ended before ready: ' + stderr)
      try {
        const value = JSON.parse(await readFile(resolve(evidence, 'ready.json'), 'utf8')) as Ready
        const origin = new URL(value.origin)
        if (
          value.version !== VERSION ||
          value.run_token !== token ||
          !(value.pid === child.pid || (process.platform === 'win32' && value.parent_pid === child.pid)) ||
          origin.protocol !== 'http:' ||
          origin.hostname !== '127.0.0.1' ||
          !origin.port ||
          ['8765', '8766', '8877'].includes(origin.port) ||
          origin.origin !== value.origin ||
          typeof value.source?.id !== 'string' ||
          !value.source.id ||
          typeof value.story?.id !== 'string' ||
          !value.story.id ||
          !object(value.source?.payload) ||
          !object(value.source?.result) ||
          value.source.kind !== 'workflow' ||
          value.source.status !== 'blocked_runtime' ||
          value.source.novel_id !== value.story?.id ||
          value.source.branch_id !== value.story?.branch_id ||
          value.mock_model_calls !== 2 ||
          value.real_model_calls !== 0 ||
          value.background_worker !== false
        )
          throw new Error('Native ready identity does not match the owned process')
        await writeFile(
          resolve(evidence, 'owned-process.json'),
          JSON.stringify({
            launcher_pid: child.pid,
            interpreter_pid: value.pid,
            parent_pid: value.parent_pid,
            origin: value.origin,
            verified_by: value.pid === child.pid ? 'exact_pid' : 'windows_direct_child',
          }),
        )
        return { ready: value, evidence, stop }
      } catch (error) {
        if (!(error && typeof error === 'object' && 'code' in error && error.code === 'ENOENT')) throw error
      }
      await delay(40)
    }
    throw new Error('Native process readiness timed out')
  } catch (error) {
    await stop(false).catch((cleanup) => {
      stderr += '\nCleanup: ' + String(cleanup)
    })
    throw error
  }
}

test('独立原生API：作者来源澄清未知提交只查原nonce，原稿、步骤和费用保持', async ({ browser }, info) => {
  const native = await startNative(info),
    { ready } = native
  let context: BrowserContext | undefined,
    passed = false,
    primary: unknown,
    cleanup: unknown
  try {
    context = await browser.newContext({ baseURL: ready.origin, viewport: { width: 1280, height: 900 } })
    const page = await context.newPage(),
      posts: ClarificationInput[] = [],
      lookupPaths: string[] = [],
      external: string[] = [],
      unexpectedWrites: string[] = [],
      pageErrors: string[] = []
    let child: Task | undefined
    page.on('pageerror', (error) => pageErrors.push(error.message))
    page.on('dialog', (dialog) => void dialog.accept())
    await context.route('**/*', async (route) => {
      const request = route.request(),
        url = new URL(request.url())
      if (url.origin !== ready.origin) {
        external.push(request.url())
        return route.abort('blockedbyclient')
      }
      const target = `/api/tasks/${ready.source.id}/author-source-clarifications`
      if (url.pathname === target && request.method() === 'POST') {
        posts.push(request.postDataJSON() as ClarificationInput)
        const response = await route.fetch({ maxRetries: 0, maxRedirects: 0 })
        expect(response.status()).toBe(202)
        child = (await response.json()) as Task
        return route.abort('connectionreset')
      }
      if (request.method() !== 'GET') {
        unexpectedWrites.push(request.method() + ' ' + url.pathname)
        return route.abort('blockedbyclient')
      }
      if (
        url.pathname === target &&
        url.searchParams.get('client_request_id') === posts[0]?.client_request_id
      ) {
        lookupPaths.push(url.pathname + url.search)
        if (lookupPaths.length === 1)
          return route.fulfill({
            status: 404,
            json: { code: 'missing', message: '本次测试的原回执暂未可见' },
          })
        if (lookupPaths.length === 2)
          return route.fulfill({
            json: {
              source_task_id: ready.source.id,
              client_request_id: posts[0].client_request_id,
              task: null,
            },
          })
      }
      return route.continue()
    })
    async function open() {
      await page.goto('/')
      await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(ready.story.id)
      await page.getByRole('combobox', { name: '工作版本', exact: true }).selectOption(ready.story.branch_id)
      await page.getByRole('button', { name: '整书创作', exact: true }).click()
      await page.getByRole('combobox', { name: '本作品批次', exact: true }).selectOption(ready.source.id)
      const summary = page.getByText('补充作者情节来源，保留原话与费用', { exact: true }),
        panel = summary.locator('..')
      await expect(summary).toBeVisible()
      if ((await panel.getAttribute('open')) === null) await summary.click()
      return panel
    }
    let panel = await open()
    const previewReply = page.waitForResponse(
      (response) =>
        response.url() === ready.origin + `/api/tasks/${ready.source.id}/author-source-clarification-preview`,
    )
    await panel.getByRole('button', { name: '读取来源补充依据', exact: true }).click()
    const response = await previewReply
    expect(response.status()).toBe(200)
    const preview = (await response.json()) as {
      request_basis: { preview_basis_sha256: string }
      budget: { source_task_settled: number; remaining_run_limit: number }
    }
    expect(preview.budget.source_task_settled).toBeGreaterThan(0)
    const input = panel.getByRole('textbox', { name: '本章补充说明（作者）', exact: true }),
      submit = panel.getByRole('button', { name: '创建并开始来源澄清子任务', exact: true })
    await input.fill(ready.addition)
    const statusBaseSize = await panel
      .locator('p')
      .first()
      .evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize))
    const buttonBaseSize = await submit.evaluate((element) =>
      Number.parseFloat(getComputedStyle(element).fontSize),
    )
    await input.scrollIntoViewIfNeeded()
    await page.screenshot({
      path: info.outputPath('author-native-desktop-input-viewport.png'),
      fullPage: false,
    })
    await page.setViewportSize({ width: 320, height: 900 })
    await assertActualDoubleText(
      page,
      { input, submit, summary: page.getByText('补充作者情节来源，保留原话与费用', { exact: true }) },
      info,
    )
    await input.scrollIntoViewIfNeeded()
    await page.screenshot({
      path: info.outputPath('author-native-320-double-text-input-viewport.png'),
      fullPage: false,
    })
    await submit.click()
    const lookup = panel.getByRole('button', { name: '查询原来源补充回执', exact: true })
    await expect(lookup).toBeVisible()
    expect(posts).toHaveLength(1)
    expect(posts[0]).toEqual({
      version: 'author-source-clarification/1',
      client_request_id: posts[0].client_request_id,
      preview_basis_sha256: preview.request_basis.preview_basis_sha256,
      clarification: ready.addition,
      actor: 'author',
      reason: '',
    })
    expect(child?.status).toBe('queued')
    expect(child?.payload.budget_limit).toBe(preview.budget.remaining_run_limit)
    const statusScale: Record<string, { before: number; after: number }> = {}
    for (const [name, locator] of Object.entries({
      lookup,
      error: panel.getByRole('alert'),
      status: panel.getByRole('status').last(),
    })) {
      const measurement = await locator.evaluate(
        (element, base) => {
          const before = Number.parseFloat(getComputedStyle(element).fontSize)
          element.style.fontSize = base * 2 + 'px'
          return { before, after: Number.parseFloat(getComputedStyle(element).fontSize) }
        },
        name === 'lookup' ? buttonBaseSize : statusBaseSize,
      )
      expect(measurement.after).toBeCloseTo((name === 'lookup' ? buttonBaseSize : statusBaseSize) * 2, 2)
      statusScale[name] = measurement
    }
    await info.attach('native-unknown-new-status-text-scale', {
      contentType: 'application/json',
      body: JSON.stringify(statusScale),
    })
    await lookup.scrollIntoViewIfNeeded()
    await page.screenshot({
      path: info.outputPath('author-native-320-unknown-submit-viewport.png'),
      fullPage: false,
    })
    await page.setViewportSize({ width: 1280, height: 900 })
    panel = await open()
    await expect(panel.getByRole('textbox', { name: '本章补充说明（作者）', exact: true })).toHaveValue(
      ready.addition,
    )
    await expect(panel.getByRole('button', { name: '创建并开始来源澄清子任务', exact: true })).toBeDisabled()
    await panel.getByRole('button', { name: '查询原来源补充回执', exact: true }).click()
    await expect(panel.getByRole('alert')).toContainText('暂未可见')
    await panel.getByRole('button', { name: '查询原来源补充回执', exact: true }).click()
    await expect(panel.getByText(/此刻未查到原提交/)).toBeVisible()
    const lookupPath =
      `/api/tasks/${ready.source.id}/author-source-clarifications?` +
      new URLSearchParams({ client_request_id: posts[0].client_request_id })
    const finalReply = page.waitForResponse((reply) => reply.url() === ready.origin + lookupPath)
    await panel.getByRole('button', { name: '查询原来源补充回执', exact: true }).click()
    const found = await finalReply
    expect(found.status()).toBe(200)
    expect(await found.json()).toMatchObject({
      source_task_id: ready.source.id,
      client_request_id: posts[0].client_request_id,
      task: { id: child?.id, status: 'queued' },
    })
    const receipt = page.getByRole('region', { name: '作者来源补充回执', exact: true })
    await expect(receipt).toBeVisible()
    await receipt.scrollIntoViewIfNeeded()
    await page.screenshot({
      path: info.outputPath('author-native-desktop-receipt-viewport.png'),
      fullPage: false,
    })
    expect(posts).toHaveLength(1)
    expect(lookupPaths).toEqual(Array(3).fill(lookupPath))
    expect(external).toEqual([])
    expect(unexpectedWrites).toEqual([])
    expect(pageErrors).toEqual([])
    passed = true
  } catch (error) {
    primary = error
  } finally {
    try {
      await context?.close()
    } catch (error) {
      cleanup = error
    }
    try {
      await native.stop(passed && !cleanup)
    } catch (error) {
      cleanup ??= error
    }
  }
  if (cleanup) await info.attach('native-cleanup-error', { contentType: 'text/plain', body: String(cleanup) })
  if (primary) throw primary
  if (cleanup) throw cleanup
  const proof = JSON.parse(await readFile(resolve(native.evidence, 'final-audit.json'), 'utf8')) as Record<
    string,
    unknown
  >
  expect(proof).toMatchObject({
    passed: true,
    real_model_calls: 0,
    mock_model_calls: 2,
    background_worker: false,
    child_status: 'queued',
    chapters_steps_artifacts_usage_unchanged: true,
  })
  await info.attach('native-source-clarification-audit', {
    contentType: 'application/json',
    path: resolve(native.evidence, 'final-audit.json'),
  })
})
