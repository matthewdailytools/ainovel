import { expect, test } from '@playwright/test'
import { createHash } from 'node:crypto'

for (const unresolved of [false, true]) {
  test(`全自动写前章纲：${unresolved ? '两轮仍未知时停在正文前' : '明确后冻结并完成阶段'}，原粗纲与旧稿保留`, async ({
    page,
  }) => {
    await page.goto('/')
    const session = await (await page.request.get('/api/session')).json()
    const headers = { 'X-Ainovel-CSRF': session.csrf }
    const novel = await (
      await page.request.post('/api/novels', {
        headers,
        data: { title: `后台验证 · 写前章纲 ${unresolved}` },
      })
    ).json()
    const chapter = await (
      await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
        headers,
        data: { title: '作者原稿' },
      })
    ).json()
    const original = await (
      await page.request.put(`/api/chapters/${chapter.id}`, {
        headers,
        data: { base_revision: 0, content: '作者旧稿保留😀𠮷' },
      })
    ).json()
    await page.reload()
    await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
    await page.getByRole('button', { name: '整书创作', exact: true }).click()
    await page.getByRole('radio', { name: /^全自动/ }).check()
    await page.getByRole('combobox', { name: '本次任务范围', exact: true }).selectOption('segment')
    await page
      .getByLabel('作品设定、粗纲与结局方向')
      .fill(`plan-preparation-e2e ${unresolved ? 'fixture-unresolved' : ''}：只测量记录，疑点不提前解释。`)
    await page.getByLabel('计划章数', { exact: true }).fill('2')
    await page.getByLabel('每章字数下限').fill('200')
    await page.getByLabel('每章字数上限').fill('600')
    await page.getByRole('combobox', { name: '采纳政策', exact: true }).selectOption('automatic')
    await page.getByRole('checkbox', { name: '额外逐项核对本次情节', exact: true }).check()
    const response = page.waitForResponse(
      (r) => r.url().endsWith('/api/workflows') && r.request().method() === 'POST',
    )
    await page.getByRole('button', { name: '创建独立版本并启动', exact: true }).click()
    const started = await (await response).json()
    expect(started.payload.plots).toEqual([])
    expect(started.branch_id).not.toBe(novel.branch_id)
    expect(started.result.workflow_plan_preparation_version).toBe('workflow-plan-preparation/1')
    await expect
      .poll(async () => (await (await page.request.get(`/api/tasks/${started.id}`)).json()).status, {
        timeout: 30_000,
      })
      .toBe(unresolved ? 'needs_review' : 'segment_complete')
    const task = await (await page.request.get(`/api/tasks/${started.id}`)).json()
    const preparation = task.result.plot_preparations['1']
    expect(preparation.original_plot).toBe(task.result.plan.chapters[0].plot)
    expect(preparation.original_plot_sha256).toBe(
      createHash('sha256').update(preparation.original_plot).digest('hex'),
    )
    expect(preparation.status).toBe(unresolved ? 'needs_review' : 'ready')
    expect(preparation.round).toBe(unresolved ? 2 : 1)
    expect(preparation.choices).toHaveLength(unresolved ? 2 : 1)
    const records = page.getByText('AI工作章纲执行记录', { exact: true })
    await records.click()
    await page.getByText(new RegExp(`^第1章 · ${unresolved ? '执行安排待核对' : '已冻结执行安排'}`)).click()
    await expect(page.getByRole('heading', { name: '原AI章纲', exact: true })).toBeVisible()
    await expect(page.getByText('最多两轮开放选择', { exact: false })).toBeVisible()
    if (unresolved) {
      const chapters = await (await page.request.get(`/api/branches/${started.branch_id}/chapters`)).json()
      for (const row of chapters) {
        const saved = await (await page.request.get(`/api/chapters/${row.id}`)).json()
        expect(saved.content).toBe('')
      }
      const artifacts = await (await page.request.get(`/api/artifacts?branch_id=${started.branch_id}`)).json()
      expect(artifacts).toEqual([])
      await expect(page.getByRole('button', { name: '从保存的检查点恢复', exact: true })).toBeDisabled()
    } else {
      expect(preparation.effective_plot.startsWith(preparation.original_plot)).toBe(true)
      expect(
        preparation.prepared_contract.items.every(
          (item: { certainty: string }) => item.certainty === 'explicit',
        ),
      ).toBe(true)
      await expect(page.getByRole('heading', { name: '本章执行章纲', exact: true })).toBeVisible()
    }
    await page.setViewportSize({ width: 320, height: 800 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
    await page.setViewportSize({ width: 1280, height: 900 })
    await page.evaluate(() => {
      document.documentElement.style.fontSize = '200%'
    })
    await records.focus()
    await expect(records).toBeFocused()
    expect(await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).toEqual(original)
  })
}
