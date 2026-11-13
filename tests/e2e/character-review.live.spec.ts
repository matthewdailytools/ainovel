import { test, expect } from '@playwright/test'
import { readFile, writeFile } from 'node:fs/promises'
import { homedir } from 'node:os'
import { isAbsolute, relative, resolve } from 'node:path'

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

test('日常实例：人物字段待核报告、原文与刷新只读核验，不采用或派发任务', async ({
  page,
  context,
  request,
}, testInfo) => {
  const manifestPath = process.env.AINOVEL_CHARACTER_REVIEW_FIXTURES
  if (!manifestPath) {
    test.skip(true, '仅显式选择已完成的独立语义评测作品，默认不调用模型')
    return
  }
  const folder = resolve(homedir(), '.ainovel/acceptance/character-evidence-quality')
  const difference = relative(folder, resolve(manifestPath))
  expect(isAbsolute(difference) || difference.startsWith('..')).toBeFalsy()
  const manifest: unknown = JSON.parse(await readFile(manifestPath, 'utf8'))
  expect(object(manifest) && object(manifest.cases)).toBeTruthy()
  if (!object(manifest) || !object(manifest.cases)) throw new Error('缺少明确测试作品清单')
  const fixture = manifest.cases['missing-not-dead']
  if (!object(fixture) || fixture.state !== 'finished') throw new Error('来源样例尚未完成，不能冒充报告验证')
  for (const key of ['novel_id', 'branch_id', 'chapter_id', 'artifact_id', 'task_id']) {
    expect(fixture[key]).toMatch(/^[a-f0-9]{32}$/)
  }
  const novelId = String(fixture.novel_id)
  const branchId = String(fixture.branch_id)
  const chapterId = String(fixture.chapter_id)
  const artifactId = String(fixture.artifact_id)
  const novels: unknown = await (await request.get('/api/novels')).json()
  if (!Array.isArray(novels)) throw new Error('作品目录读取失败')
  const novel = novels.find((item: unknown) => object(item) && item.id === novelId)
  if (!object(novel)) throw new Error('独立测试作品不存在')
  expect(novel.title).toMatch(/^【自动验证·人物来源】/)
  const before = await (await request.get(`/api/chapters/${chapterId}`)).json()
  const tasksBefore = await (await request.get(`/api/tasks?branch_id=${branchId}`)).json()
  const saved = await (await request.get(`/api/artifacts/${artifactId}`)).json()
  expect(saved.status).toBe('candidate')
  const report: unknown = saved.payload.character_semantic_review
  if (!object(report) || !Array.isArray(report.results) || !object(report.results[0])) {
    throw new Error('待核字段缺少真实已保存的复核报告')
  }
  expect(report.status).toBe('needs_review')
  const historical = process.env.AINOVEL_CHARACTER_REVIEW_HISTORY === '1'
  expect(report.version).toBe(`character-field-entailment/${historical ? 1 : 2}`)
  const result = report.results[0]
  expect(['unknown', 'contradicted']).toContain(result.result)

  await context.addInitScript(
    ({ novelId, branchId }) => {
      localStorage.setItem('ainovel-novel', novelId)
      localStorage.setItem(`ainovel-branch-${novelId}`, branchId)
    },
    { novelId, branchId },
  )
  const unexpectedWrites: string[] = []
  const positionWrites: string[] = []
  await context.route(/\/api(?:\/|\?|$)/, async (route) => {
    const browserRequest = route.request()
    if (!['POST', 'PUT', 'PATCH', 'DELETE'].includes(browserRequest.method())) {
      await route.continue()
      return
    }
    const path = new URL(browserRequest.url()).pathname
    if (
      browserRequest.method() === 'PUT' &&
      path === `/api/branches/${branchId}/reading-state` &&
      browserRequest.postDataJSON()?.chapter_id === chapterId
    ) {
      positionWrites.push(path)
      await route.continue()
      return
    }
    unexpectedWrites.push(path)
    await route.abort('blockedbyclient')
  })
  await page.goto('/')
  await expect(page.getByRole('combobox', { name: '工作版本', exact: true })).toHaveValue(branchId)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  await page.getByText('按保存稿整理人物、事件、状态和伏笔候选', { exact: true }).click()
  const panel = page.getByRole('region', { name: '结构化整理与确认', exact: true })
  await panel.getByRole('combobox', { name: '保存的结构候选', exact: true }).selectOption(artifactId)
  const review = panel.getByRole('region', { name: '人物字段来源复核', exact: true })
  await expect(review).toContainText(historical ? '旧版历史报告' : '人物字段待核对')
  await review.getByText(/^查看字段结论、理由与来源/).click()
  await expect(review).toContainText(String(result.reason))
  await expect(review).toContainText(result.result === 'unknown' ? '未知' : '矛盾')
  await expect(review).toContainText('模型辅助核对')
  if (Array.isArray(result.evidence) && result.evidence.length) {
    await expect(review).toContainText('保存稿修订1')
  } else {
    await expect(review).toContainText('未提供原文证据。')
  }
  await panel.getByText('核对具体内容、关联和原文', { exact: true }).click()
  await expect(
    panel
      .locator('blockquote')
      .filter({ hasText: String(saved.payload.records[0].record.sources[0].quote) })
      .last(),
  ).toBeVisible()
  await panel.getByRole('button', { name: '刷新候选与复核报告', exact: true }).click()
  await expect(review).toContainText(historical ? '旧版历史报告' : '人物字段待核对')
  const desktop = testInfo.outputPath('character-review-desktop.png')
  await page.screenshot({ path: desktop, fullPage: true })
  await page.setViewportSize({ width: 320, height: 800 })
  await page.evaluate(() => {
    document.documentElement.style.fontSize = '200%'
  })
  const refresh = panel.getByRole('button', { name: '刷新候选与复核报告', exact: true })
  await refresh.focus()
  await expect(refresh).toBeFocused()
  const narrow = testInfo.outputPath('character-review-narrow.png')
  await page.screenshot({ path: narrow, fullPage: true })
  const after = await (await request.get(`/api/chapters/${chapterId}`)).json()
  const tasksAfter = await (await request.get(`/api/tasks?branch_id=${branchId}`)).json()
  const afterArtifact = await (await request.get(`/api/artifacts/${artifactId}`)).json()
  expect(after.content).toBe(before.content)
  expect(after.revision).toBe(before.revision)
  expect(tasksAfter).toEqual(tasksBefore)
  expect(afterArtifact).toEqual(saved)
  expect(unexpectedWrites).toEqual([])
  const summary = testInfo.outputPath('character-review-readonly.json')
  await writeFile(
    summary,
    JSON.stringify(
      {
        source: 'existing explicitly labelled semantic fixture; this browser only reads',
        novelId,
        branchId,
        chapterId,
        artifactId,
        reportStatus: report.status,
        reportVersion: report.version,
        historical,
        fieldResult: result.result,
        unexpectedWrites,
        positionWrites,
        manuscriptUnchanged: true,
        artifactUnchanged: true,
        tasksUnchanged: true,
      },
      null,
      2,
    ),
    'utf8',
  )
  await testInfo.attach('character-review-summary', { path: summary, contentType: 'application/json' })
  await testInfo.attach('character-review-desktop', { path: desktop, contentType: 'image/png' })
  await testInfo.attach('character-review-narrow', { path: narrow, contentType: 'image/png' })
})
