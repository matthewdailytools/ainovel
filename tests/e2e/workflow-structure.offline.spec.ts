import { test, expect } from '@playwright/test'

for (const mode of ['半自动', '全自动']) {
  test(`${mode}整书：界面启动、正文与结构维护检查点及原稿保留`, async ({ page }) => {
    await page.goto('/')
    const session = await (await page.request.get('/api/session')).json()
    const headers = { 'X-Ainovel-CSRF': session.csrf }
    const novel = await (
      await page.request.post('/api/novels', {
        headers,
        data: { title: `后台验证 · ${mode}结构维护`, brief: '沈砚测量潮水，记录证据，公开核验后收束。' },
      })
    ).json()
    const original = await (
      await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
        headers,
        data: { title: '作者原稿' },
      })
    ).json()
    await page.request.put(`/api/chapters/${original.id}`, {
      headers,
      data: { base_revision: 0, content: '作者保留原稿😀' },
    })
    await page.reload()
    await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
    await page.getByRole('button', { name: '整书创作', exact: true }).click()
    await page.getByRole('radio', { name: new RegExp(`^${mode}`) }).check()
    await page.getByLabel('作品设定、粗纲与结局方向').fill('沈砚测量潮水，记录证据，公开核验后收束。')
    await page.getByLabel('计划章数', { exact: true }).fill('2')
    await page.getByLabel('每章字数下限').fill('200')
    await page.getByLabel('每章字数上限').fill('600')
    if (mode === '半自动')
      await page
        .getByLabel('作者批量情节（每行一章）')
        .fill('量潮 | 用旧尺测量潮水\n核验 | 公开测量记录并收束')
    await page.getByRole('combobox', { name: '采纳政策', exact: true }).selectOption('automatic')
    await expect(page.getByRole('checkbox', { name: '每章采用后整理人物、时点、事件和伏笔' })).toBeChecked()
    const responsePromise = page.waitForResponse(
      (r) => r.url().endsWith('/api/workflows') && r.request().method() === 'POST',
    )
    await page.getByRole('button', { name: '创建独立版本并启动', exact: true }).click()
    const response = await responsePromise
    expect(response.ok(), await response.text()).toBeTruthy()
    const started = await response.json()
    expect(started.branch_id).not.toBe(novel.branch_id)
    await expect
      .poll(async () => (await (await page.request.get(`/api/tasks/${started.id}`)).json()).status, {
        timeout: 30_000,
      })
      .toBe('production_complete')
    await expect(
      page.getByText('已完成 2 次章节结构维护；完整采用回执保留在检查点报告中。', { exact: true }),
    ).toBeVisible()
    const task = await (await page.request.get(`/api/tasks/${started.id}`)).json()
    expect(task.result.pending_structure).toBeUndefined()
    expect(task.result.structure_history).toHaveLength(2)
    const graph = await (await page.request.get(`/api/branches/${started.branch_id}/story-graph`)).json()
    expect(graph.records).toHaveLength(1)
    expect(graph.records[0].payload.title).toBe('沈砚')
    const retained = await (await page.request.get(`/api/chapters/${original.id}`)).json()
    expect(retained.content).toBe('作者保留原稿😀')
    expect(retained.revision).toBe(1)
  })
}
