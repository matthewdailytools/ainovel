import { test, expect } from '@playwright/test'
import { createHash } from 'node:crypto'

const SOURCE = '😀渡口掌柜先问用途，又把水路图收回袖中。他只肯给半幅图，却记下了来客的目的地。'

test('书库选段到拆解、方案、试写、对照采纳和历史恢复', async ({ page }, testInfo) => {
  await page.goto('/')
  const initial = await (await page.request.get('/api/session')).json()
  const novel = await (
    await page.request.post('/api/novels', {
      headers: { 'X-Ainovel-CSRF': initial.csrf },
      data: { title: '后台验证 · 修城器师' },
    })
  ).json()
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await expect(page.getByRole('combobox', { name: '工作版本', exact: true })).not.toHaveValue('')
  const chapterResponse = page.waitForResponse(
    (r) => r.url().endsWith(`/api/branches/${novel.branch_id}/chapters`) && r.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '新建第一章', exact: true }).click()
  const chapter = await (await chapterResponse).json()
  await page
    .getByRole('textbox', { name: '章节正文', exact: true })
    .fill('原稿：她来到闸门前，准备检查外露结构。')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByText('已保存', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '资料书库', exact: true }).click()
  // Import through the real authenticated API, then exercise reading/selection in UI.
  const session = await (await page.request.get('/api/session')).json()
  const response = await page.request.post('/api/library/import', {
    headers: { 'X-Ainovel-CSRF': session.csrf },
    multipart: { file: { name: '自拟参考.txt', mimeType: 'text/plain', buffer: Buffer.from(SOURCE) } },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  await page.reload()
  await page.getByRole('button', { name: '资料书库', exact: true }).click()
  await page.getByText('自拟参考.txt', { exact: false }).first().click()
  await expect(page.getByRole('textbox', { name: '资料原文' })).toHaveValue(SOURCE)
  const reader = page.getByRole('textbox', { name: '资料原文' })
  await reader.focus()
  await reader.press('ControlOrMeta+A')
  await page.getByRole('button', { name: '拆解这段写法', exact: true }).click()
  await expect(page.getByRole('heading', { name: '参考仿写工作区' })).toBeVisible()
  await page.getByRole('button', { name: '拆解选段写法', exact: true }).click()
  await page.getByRole('button', { name: '载入分析卡供编辑和选择（尚未采用）', exact: true }).click()
  await page.getByRole('checkbox', { name: /本次采用/ }).check()
  await page.getByRole('button', { name: /明确使用顶部当前工作版本/ }).click()
  await page.getByLabel('本次目标章节').selectOption({ index: 1 })
  await page.getByLabel('本次原创情节与结果').fill('检查闸门外露磨损，但拿不到维修记录，不修门。')
  await page.getByLabel('固定条件（每行一条）').fill('不修门')
  page.once('dialog', async (dialog) => {
    expect(dialog.message()).toContain('参考规则、方案、候选或反馈')
    await dialog.dismiss()
  })
  await page.getByRole('button', { name: '返回资料书库', exact: true }).click()
  await expect(page.getByLabel('本次原创情节与结果')).toHaveValue(
    '检查闸门外露磨损，但拿不到维修记录，不修门。',
  )
  page.once('dialog', (dialog) => dialog.dismiss())
  await page.getByRole('button', { name: '写作', exact: true }).click()
  await expect(page.getByRole('heading', { name: '参考仿写工作区' })).toBeVisible()
  await page.getByRole('button', { name: '保存本次规则与方案', exact: true }).click()
  await page.getByRole('button', { name: '生成本书应用方案', exact: true }).click()
  await page.getByRole('button', { name: '载入方案供编辑确认', exact: true }).click()
  await page.getByRole('button', { name: '保存本次规则与方案', exact: true }).click()
  await page.getByRole('button', { name: '按保存方案原创试写', exact: true }).click()
  await expect(page.getByLabel('参考试写候选')).toHaveValue(/螺栓/)
  await page.reload()
  await page.getByRole('button', { name: '资料书库', exact: true }).click()
  await page.getByRole('button', { name: /自拟参考.txt · .* · 修订/ }).click()
  await expect(page.getByLabel('参考试写候选')).toHaveValue(/螺栓/)
  await page.getByText('阶段任务与历史规则版本', { exact: true }).click()
  await expect(page.getByText('reference.draft · 候选已生成', { exact: false })).toBeVisible()
  await expect(page.getByRole('region', { name: '参考写法对照', exact: true })).toBeVisible()
  await page.setViewportSize({ width: 320, height: 800 })
  const overflow = await page.evaluate(() =>
    [...document.querySelectorAll('body *')]
      .filter(
        (element) =>
          (element.getBoundingClientRect().right > innerWidth + 1 ||
            element.scrollWidth > element.clientWidth + 1) &&
          element.getBoundingClientRect().width > 0,
      )
      .map((element) => ({
        tag: element.tagName,
        className: element.className,
        right: element.getBoundingClientRect().right,
        scroll: element.scrollWidth,
        client: element.clientWidth,
        text: element.textContent?.slice(0, 30),
      }))
      .slice(-20),
  )
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
    JSON.stringify(overflow),
  ).toBeTruthy()
  const plotOption = page.getByRole('checkbox', { name: /额外核对情节角色、对象与方向/ })
  await plotOption.focus()
  await expect(plotOption).toBeFocused()
  await plotOption.press('Space')
  await expect(plotOption).toBeChecked()
  await page.setViewportSize({ width: 1280, height: 900 })
  const auditedRequest = page.waitForRequest(
    (request) => request.url().endsWith('/actions') && request.method() === 'POST',
  )
  await page.getByRole('button', { name: '重新对照候选', exact: true }).click()
  expect((await auditedRequest).postDataJSON().plot_contract_check).toBe(true)
  await page.getByText('独立对照报告（核对固定对象及版本）', { exact: true }).click()
  const plotAudit = page.getByText('情节逐项核对 · 模型标一致，仍须作者核对', { exact: true })
  await expect(plotAudit).toBeVisible()
  await plotAudit.click()
  await expect(page.getByText('本稿观察：保留测量，没有修门', { exact: true })).toBeVisible()
  await page.evaluate(() => {
    document.documentElement.style.fontSize = '200%'
  })
  await expect(page.getByText('本稿观察：保留测量，没有修门', { exact: true })).toBeVisible()
  await page.evaluate(() => {
    document.documentElement.style.fontSize = ''
  })

  const passedTitle = '情节逐项核对 · 模型标一致，仍须作者核对'
  const historicalTitle = '情节逐项核对 · 历史报告已失效，不能作为当前稿结论'
  const hash = (text: string) => createHash('sha256').update(text).digest('hex')
  const independent = page.getByText('独立对照报告（核对固定对象及版本）', { exact: true }).locator('..')
  async function showIndependent(title: string) {
    const outer = independent.locator(':scope > summary')
    if (!(await independent.evaluate((element) => (element as HTMLDetailsElement).open))) await outer.click()
    const summary = independent.getByText(title, { exact: true })
    await expect(summary).toBeVisible()
    return summary
  }
  async function checkReference(button: string, saved: boolean) {
    await page.getByRole('checkbox', { name: /额外核对情节角色、对象与方向/ }).check()
    const posted = page.waitForResponse(
      (r) => r.url().endsWith('/actions') && r.request().method() === 'POST',
    )
    await page.getByRole('button', { name: button, exact: true }).click()
    const response = await posted
    expect(response.ok(), await response.text()).toBeTruthy()
    const task = await response.json()
    expect(task.payload.plot_contract_check).toBe(true)
    expect(task.payload.action).toBe('reference.check')
    await expect
      .poll(async () => (await (await page.request.get(`/api/tasks/${task.id}`)).json()).status)
      .toBe('succeeded')
    const finished = await (await page.request.get(`/api/tasks/${task.id}`)).json()
    const report = await (await page.request.get(`/api/artifacts/${finished.result.artifact_id}`)).json()
    expect(report.payload.target.kind).toBe(saved ? 'saved_manuscript' : 'candidate')
    expect(report.payload.plot_audit.version).toBe('author-plot-audit/1')
    await showIndependent(passedTitle)
    return report
  }
  async function returnToReference() {
    await page.getByRole('button', { name: '资料书库', exact: true }).click()
    await page.getByRole('button', { name: /自拟参考.txt · .* · 修订/ }).click()
    await expect(page.getByLabel('参考试写候选')).toBeVisible()
  }
  async function showWritingReport(id: string, title: string) {
    const select = page.getByRole('combobox', { name: '比较候选', exact: true })
    await expect(select.locator(`option[value="${id}"]`)).toBeAttached()
    await select.selectOption(id)
    await expect(
      page.getByRole('region', { name: '情节逐项核对', exact: true }).getByText(title, { exact: true }),
    ).toBeVisible()
  }

  // Candidate revision is the checked target's revision, not the independent
  // report artifact's own revision 0. Every write below is an actual UI action.
  const trial = page.getByLabel('参考试写候选')
  const revision1 = (await trial.inputValue()) + '\n她把记录收好。'
  await trial.fill(revision1)
  await page.getByRole('button', { name: '保存候选编辑（旧检查失效）', exact: true }).click()
  const checked1 = await checkReference('重新对照候选', false)
  expect(checked1.candidate_revision).toBe(0)
  expect(checked1.payload.target.candidate.revision).toBe(1)
  expect(checked1.payload.plot_audit.candidate_revision).toBe(1)
  expect(checked1.payload.target.content_sha256).toBe(hash(revision1))
  expect(checked1.payload.plot_audit.candidate_sha256).toBe(hash(revision1))
  await page.getByRole('button', { name: '回到写作页继续编辑或恢复版本' }).click()
  await showWritingReport(checked1.id, passedTitle)
  await returnToReference()
  const revision2 = revision1 + '\n她又核对一次笔记。'
  await page.getByLabel('参考试写候选').fill(revision2)
  await page.getByRole('button', { name: '保存候选编辑（旧检查失效）', exact: true }).click()
  await showIndependent(historicalTitle)
  const stillChecked1 = await (await page.request.get(`/api/artifacts/${checked1.id}`)).json()
  expect(stillChecked1).toEqual(checked1)
  const checked2 = await checkReference('重新对照候选', false)
  expect(checked2.payload.target.candidate.revision).toBe(2)
  expect(checked2.payload.plot_audit.candidate_revision).toBe(2)
  expect(checked2.payload.plot_audit.candidate_sha256).toBe(hash(revision2))

  await page.getByRole('button', { name: '对照后采纳到目标正文', exact: true }).click()
  await expect(page.getByText('已采纳为新正文修订，原稿历史保留。故事记录可按需整理。')).toBeVisible()
  await page.getByRole('button', { name: '回到写作页继续编辑或恢复版本' }).click()
  await expect(page.getByRole('textbox', { name: '章节正文', exact: true })).toContainText('螺栓')
  await page.getByRole('button', { name: '版本历史', exact: true }).click()
  const originalHistory = page
    .locator('.history details')
    .filter({ hasText: '原稿：她来到闸门前，准备检查外露结构。' })
  await originalHistory.locator('summary').click()
  await expect(originalHistory.locator('pre')).toHaveText('原稿：她来到闸门前，准备检查外露结构。')

  await returnToReference()
  const checkedSaved = await checkReference('对照检查当前已保存手写稿', true)
  const savedBefore = await (await page.request.get(`/api/chapters/${chapter.id}`)).json()
  expect(checkedSaved.payload.plot_audit.target).toEqual({
    kind: 'saved_chapter',
    chapter_id: chapter.id,
    revision: savedBefore.revision,
    content_sha256: hash(savedBefore.content),
  })
  expect(checkedSaved.payload.plot_audit.candidate_revision).toBe(0)
  await page.getByRole('button', { name: '回到写作页继续编辑或恢复版本' }).click()
  await showWritingReport(checkedSaved.id, passedTitle)
  const changedSaved = savedBefore.content + '\n作者更改了当前保存稿。'
  await page.getByRole('textbox', { name: '章节正文', exact: true }).fill(changedSaved)
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect
    .poll(async () => (await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).revision)
    .toBe(savedBefore.revision + 1)
  await expect(
    page
      .getByRole('region', { name: '情节逐项核对', exact: true })
      .getByText(historicalTitle, { exact: true }),
  ).toBeVisible()
  await returnToReference()
  await showIndependent(historicalTitle)
  expect(await (await page.request.get(`/api/artifacts/${checkedSaved.id}`)).json()).toEqual(checkedSaved)
  await testInfo.attach('reference-independent-target-bindings.json', {
    body: JSON.stringify(
      { candidateRevision1: checked1, candidateRevision2: checked2, saved: checkedSaved },
      null,
      2,
    ),
    contentType: 'application/json',
  })
})
