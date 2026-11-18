import { expect, test } from '@playwright/test'
import { createHash } from 'node:crypto'

import { authorSettingPlan } from './fixtures/authorSettingPlan'

test('卷快照预览列出受影响报告，检查并发基线后仅恢复该卷稿件', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const created = await page.request.post('/api/novels', {
    headers,
    data: { title: '后台验证 · 遗铃作品快照' },
  })
  expect(created.ok()).toBe(true)
  const novel = await created.json()
  const branch = `/api/branches/${novel.branch_id}`
  async function createChapter(title: string) {
    const response = await page.request.post(`${branch}/chapters`, { headers, data: { title } })
    expect(response.ok(), await response.text()).toBe(true)
    return (await response.json()) as { id: string; title: string }
  }
  const chapter = await createChapter('铃响')
  const otherChapter = await createChapter('守灯')
  const first = '师姐在门内接过铜铃。😀'
  async function save(base_revision: number, content: string): Promise<number> {
    const response = await page.request.put(`/api/chapters/${chapter.id}`, {
      headers,
      data: { base_revision, content },
    })
    expect(response.ok(), await response.text()).toBe(true)
    return (await response.json()).revision
  }
  const firstRevision = await save(0, first)
  const otherText = '守门人把油灯放在桥下。'
  const otherSaved = await page.request.put(`/api/chapters/${otherChapter.id}`, {
    headers,
    data: { base_revision: 0, content: otherText },
  })
  expect(otherSaved.ok(), await otherSaved.text()).toBe(true)
  const volumes = [chapter, otherChapter].map(({ id, title }, index) => ({
    id: `volume-${index + 1}`,
    title: `${title}卷`,
    summary: `围绕${title}寻找线索。`,
    units: [
      {
        id: `unit-${index + 1}`,
        title,
        summary: '发现可核对的线索。',
        chapters: [
          { chapter_id: id, goal: '找线索', obstacle: '夜暗', turn: '有人引路', result: '看清线索' },
        ],
      },
    ],
  }))
  const outlinePath = `${branch}/outline-workspace`
  const planned = await page.request.put(outlinePath, {
    headers,
    data: { base_revision: 0, plan: { summary: '学徒循铃声和灯寻找回路。', volumes } },
  })
  expect(planned.ok(), await planned.text()).toBe(true)
  const { draft } = await planned.json()
  const signed = await page.request.post(`${outlinePath}/confirm`, {
    headers,
    data: { base_revision: draft.revision },
  })
  expect(signed.ok(), await signed.text()).toBe(true)
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  const panel = page.getByRole('region', { name: '作品快照与回滚', exact: true })
  await panel.getByRole('combobox', { name: '快照范围' }).selectOption('volume')
  await panel.getByRole('combobox', { name: '快照所用卷' }).selectOption(volumes[0].id)
  await panel.getByRole('textbox', { name: '快照名称', exact: true }).fill('首稿检查点')
  await panel.getByRole('button', { name: '保存快照', exact: true }).press('Enter')
  await expect(panel.getByRole('status')).toHaveText('已保存作品快照。')

  const second = '学徒在门外收起铜铃。'
  const secondRevision = await save(firstRevision, second)
  const reportsPath = `${branch}/workflow-inspections`
  const reportPath = (id: string) => `${reportsPath}?scope=volume&scope_id=${id}`
  async function inspect(id: string) {
    const { preview } = await (await page.request.get(reportPath(id))).json()
    const report = await page.request.post(reportsPath, {
      headers,
      data: { scope: 'volume', scope_id: id, basis_sha256: preview.basis_sha256 },
    })
    expect(report.ok(), await report.text()).toBe(true)
    return (await report.json()) as { id: string; current: boolean }
  }
  const firstReport = await inspect(volumes[0].id)
  const otherReport = await inspect(volumes[1].id)
  expect(firstReport.current && otherReport.current).toBe(true)
  await panel.getByRole('button', { name: '预览恢复差异' }).click()
  const effect = panel.getByRole('region', { name: '恢复差异预览' })
  await expect(effect).toBeFocused()
  await expect(effect.getByRole('listitem').filter({ hasText: '恢复快照稿' })).toHaveCount(1)
  const affected = effect.getByRole('region', { name: '受影响报告' })
  await expect(affected).toContainText(firstReport.id)
  await expect(affected).toContainText('铃响卷')
  await expect(affected).not.toContainText(otherReport.id)
  await expect(affected).not.toContainText('守灯卷')
  await effect.getByRole('button', { name: '比较铃响正文' }).click()
  const compare = effect.getByRole('region', { name: '章稿对照' })
  await expect(compare.getByText(first)).toBeVisible()
  await expect(compare.getByText(second)).toBeVisible()
  const third = '师姐改将铜铃挂在檐下。'
  const thirdRevision = await save(secondRevision, third)
  const submit = effect.getByRole('button', { name: '提交恢复' })
  await expect(submit).toBeDisabled()
  await effect.getByRole('checkbox', { name: '我已核对恢复影响' }).check()
  await submit.click()
  await expect(panel.getByRole('alert')).toContainText('请重新预览')
  await expect(panel.getByRole('button', { name: '预览恢复差异' })).toBeFocused()
  await expect(submit).toBeDisabled()
  await expect(effect.getByRole('checkbox', { name: '我已核对恢复影响' })).not.toBeChecked()
  const currentFirstReport = await inspect(volumes[0].id)

  await panel.getByRole('button', { name: '预览恢复差异' }).click()
  await expect(affected).toContainText(currentFirstReport.id)
  await effect.getByRole('checkbox', { name: '我已核对恢复影响' }).check()
  const committed = page.waitForResponse(
    (response) => response.request().method() === 'POST' && response.url().endsWith('/restore'),
  )
  await submit.click()
  const result = await committed
  expect(result.ok(), await result.text()).toBe(true)
  const receipt = await result.json()
  expect(receipt.recovery_snapshot_id).toBeTruthy()
  const after = await (await page.request.get(`/api/chapters/${chapter.id}`)).json()
  expect(after.content).toBe(first)
  expect(after.revision).toBeGreaterThan(thirdRevision)
  const history = await (await page.request.get(`/api/chapters/${chapter.id}/history`)).json()
  expect(history.find((row: { revision: number }) => row.revision === thirdRevision).content).toBe(third)
  expect((await (await page.request.get(`/api/chapters/${otherChapter.id}`)).json()).content).toBe(otherText)
  expect((await (await page.request.get(reportPath(volumes[0].id))).json()).latest.current).toBe(false)
  expect((await (await page.request.get(reportPath(volumes[1].id))).json()).latest).toMatchObject({
    id: otherReport.id,
    current: true,
  })
})

test('作品快照要求重新预览晚到的正文使用证明，并展示原位恢复与另存的影响', async ({ page }) => {
  const { novel, chapter, original, headers } = await authorSettingPlan(page, '作品恢复核对正文证明')
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).press('Enter')
  await page.setViewportSize({ width: 360, height: 840 })
  const panel = page.getByRole('region', { name: '作品快照与回滚', exact: true })
  await panel.getByRole('textbox', { name: '快照名称' }).fill('保持原稿的恢复点')
  await panel.getByRole('button', { name: '保存快照' }).press('Enter')
  await expect(panel.getByRole('status')).toHaveText('已保存作品快照。')
  const retry = panel.getByRole('button', { name: '预览恢复差异' })
  await retry.press('Enter')
  const preview = panel.getByRole('region', { name: '恢复差异预览' })
  await expect(preview).toBeFocused()
  const affected = preview.getByRole('region', { name: '原位恢复后待重核的设定正文使用' })
  await expect(affected).toHaveCount(0)

  const endpoint = `/api/chapters/${chapter.id}/setting-uses`
  const { basis } = await (await page.request.get(endpoint)).json()
  const confirmed = await page.request.post(endpoint, {
    headers,
    data: {
      outline_version_id: basis.outline_version_id,
      settings_version_id: basis.settings_version_id,
      item_id: 'keeper',
      parent_id: null,
      chapter_sha256: basis.chapter_sha256,
      evidence: {
        chapter_id: chapter.id,
        revision_id: basis.chapter_revision_id,
        start: 0,
        end: original.length,
        quote_sha256: createHash('sha256').update(original).digest('hex'),
      },
      idempotency_key: `work-snapshot-proof-${chapter.id}`,
    },
  })
  expect(confirmed.status(), await confirmed.text()).toBe(201)
  const proof = (await confirmed.json()).version
  const reviewed = preview.getByRole('checkbox', { name: '我已核对恢复影响' })
  const apply = preview.getByRole('button', { name: '提交恢复' })
  await reviewed.check()
  await apply.press('Enter')
  await expect(panel.getByRole('alert')).toContainText('请重新预览')
  await expect(retry).toBeFocused()
  await expect(apply).toBeDisabled()

  await retry.press('Enter')
  await expect(affected).toContainText('归还赤钥 · 守铃人阿陶')
  await expect(affected).toContainText(proof.id)
  const mode = preview.getByRole('combobox', { name: '恢复方式' })
  await mode.selectOption('new_branch')
  await expect(affected).toHaveCount(0)
  await expect(preview.getByText('既有报告和证明可在来源分支查看。', { exact: false })).toBeVisible()
  await mode.selectOption('in_place')
  await expect(reviewed).not.toBeChecked()
  await expect(affected).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await reviewed.check()
  await apply.press('Enter')
  await expect(panel.getByRole('status')).toContainText('已恢复为新的当前修订')
  const proofAfter = await page.request.get(`${endpoint}/${proof.id}`)
  expect(proofAfter.ok(), await proofAfter.text()).toBe(true)
  expect(await proofAfter.json()).toMatchObject({ status: 'outdated', quote: original })
})
