import { test, expect } from '@playwright/test'
import { readFile, writeFile } from 'node:fs/promises'
import { homedir } from 'node:os'
import { isAbsolute, relative, resolve } from 'node:path'

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

test('日常实例：真实情节报告只读显示及历史规则说明，不改稿或派发模型', async ({
  page,
  context,
  request,
}, testInfo) => {
  const file = process.env.AINOVEL_PLOT_REVIEW_RUN
  if (!file) {
    test.skip(true, '仅显式选择已完成的独立情节评测记录，默认不执行日常检查')
    return
  }
  const folder = resolve(homedir(), '.ainovel/acceptance/shared-plot-quality')
  const difference = relative(folder, resolve(file))
  expect(isAbsolute(difference) || difference.startsWith('..')).toBeFalsy()
  const manifest: unknown = JSON.parse(await readFile(file, 'utf8'))
  if (!object(manifest) || manifest.status !== 'succeeded' || !object(manifest.payload))
    throw new Error('评测尚未完成，不能把运行中状态当作已保存报告')
  const payload = manifest.payload
  for (const key of ['novel_id', 'branch_id', 'chapter_id']) expect(payload[key]).toMatch(/^[a-f0-9]{32}$/)
  expect(manifest.task_id).toMatch(/^[a-f0-9]{32}$/)
  const novelId = String(payload.novel_id)
  const branchId = String(payload.branch_id)
  const chapterId = String(payload.chapter_id)
  const taskId = String(manifest.task_id)
  const novels: unknown = await (await request.get('/api/novels')).json()
  if (!Array.isArray(novels)) throw new Error('作品目录读取失败')
  const novel = novels.find((item: unknown) => object(item) && item.id === novelId)
  if (!object(novel) || typeof novel.title !== 'string' || !novel.title.startsWith('长篇质量验证 · '))
    throw new Error('仅允许明确标记的独立验证作品')
  const before = await (await request.get(`/api/chapters/${chapterId}`)).json()
  const task = await (await request.get(`/api/tasks/${taskId}`)).json()
  expect(task.status).toBe('succeeded')
  expect(task.payload).toMatchObject({ action: 'review.chapter', plot_contract_check: true })
  expect(task.branch_id).toBe(branchId)
  const artifactId = task.result.artifact_id
  expect(artifactId).toMatch(/^[a-f0-9]{32}$/)
  const artifact = await (await request.get(`/api/artifacts/${artifactId}`)).json()
  const audit = artifact.payload.plot_audit
  expect(artifact.kind).toBe('review')
  expect(audit.target).toMatchObject({ chapter_id: chapterId, revision: before.revision })
  expect(['author-plot-audit/1', 'author-plot-audit/2']).toContain(audit.version)

  await context.addInitScript(
    ({ novelId, branchId }) => {
      localStorage.setItem('ainovel-novel', novelId)
      localStorage.setItem(`ainovel-branch-${novelId}`, branchId)
    },
    { novelId, branchId },
  )
  const unexpectedWrites: string[] = []
  const readingWrites: string[] = []
  await context.route(/\/api(?:\/|\?|$)/, async (route) => {
    const incoming = route.request()
    if (!['POST', 'PUT', 'PATCH', 'DELETE'].includes(incoming.method())) {
      await route.continue()
      return
    }
    const path = new URL(incoming.url()).pathname
    if (
      incoming.method() === 'PUT' &&
      path === `/api/branches/${branchId}/reading-state` &&
      incoming.postDataJSON()?.chapter_id === chapterId
    ) {
      readingWrites.push(path)
      await route.continue()
      return
    }
    unexpectedWrites.push(path)
    await route.abort('blockedbyclient')
  })
  await page.goto('/')
  await expect(page.getByRole('combobox', { name: '工作版本', exact: true })).toHaveValue(branchId)
  await expect(page.getByRole('textbox', { name: '章节标题', exact: true })).toHaveValue(before.title)
  const comparison = page.getByRole('combobox', { name: '比较候选', exact: true })
  await expect(comparison.locator(`option[value="${artifactId}"]`)).toBeAttached()
  await comparison.selectOption(artifactId)
  const panel = page.getByRole('region', { name: '情节逐项核对', exact: true })
  await panel.locator('summary').first().click()
  await expect(panel).toContainText(audit.summary)
  await expect(panel).toContainText('模型辅助核对不等于作者认可')
  await expect(panel).toContainText(payload.instruction as string)
  if (audit.version === 'author-plot-audit/1')
    await expect(panel).toContainText(/未含限定接收者补充|未执行v2/)
  for (const check of audit.checks) await expect(panel).toContainText(check.reason)
  const desktop = testInfo.outputPath('plot-review-desktop.png')
  await page.screenshot({ path: desktop })
  await page.setViewportSize({ width: 320, height: 800 })
  await page.evaluate(() => {
    document.documentElement.style.fontSize = '200%'
  })
  await panel.locator('summary').first().focus()
  await expect(panel.locator('summary').first()).toBeFocused()
  const narrow = testInfo.outputPath('plot-review-narrow.png')
  await page.screenshot({ path: narrow })
  expect(await (await request.get(`/api/chapters/${chapterId}`)).json()).toEqual(before)
  expect(await (await request.get(`/api/artifacts/${artifactId}`)).json()).toEqual(artifact)
  expect(await (await request.get(`/api/tasks/${taskId}`)).json()).toEqual(task)
  expect(unexpectedWrites).toEqual([])
  const summary = testInfo.outputPath('plot-review-readonly.json')
  await writeFile(
    summary,
    JSON.stringify(
      {
        taskId,
        artifactId,
        version: audit.version,
        status: audit.status,
        unexpectedWrites,
        readingWrites,
        manuscriptUnchanged: true,
        artifactUnchanged: true,
        taskUnchanged: true,
      },
      null,
      2,
    ),
    'utf8',
  )
  await testInfo.attach('plot-review-readonly', { path: summary, contentType: 'application/json' })
  await testInfo.attach('plot-review-desktop', { path: desktop, contentType: 'image/png' })
  await testInfo.attach('plot-review-narrow', { path: narrow, contentType: 'image/png' })
})
