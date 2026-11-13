import { test, expect } from '@playwright/test'

for (const mode of ['半自动', '全自动']) {
  test(`${mode}阶段批次：未回收计划保持，阶段完成不等于整书完结`, async ({ page }) => {
    await page.goto('/')
    const session = await (await page.request.get('/api/session')).json()
    const headers = { 'X-Ainovel-CSRF': session.csrf }
    const created = await page.request.post('/api/novels', {
      headers,
      data: { title: `后台验证 · ${mode}阶段批次`, brief: '先测量潮水并记录，后卷才解释暗号与公开核验。' },
    })
    expect(created.ok(), await created.text()).toBeTruthy()
    const novel = await created.json()
    const original = await (
      await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
        headers,
        data: { title: '作者保留原稿' },
      })
    ).json()
    await page.request.put(`/api/chapters/${original.id}`, {
      headers,
      data: { base_revision: 0, content: '独立版本的作者原稿😀' },
    })
    const graph = await (await page.request.get(`/api/branches/${novel.branch_id}/story-graph`)).json()
    const added = await page.request.post(`/api/branches/${novel.branch_id}/story-records`, {
      headers,
      data: {
        base_story_revision: graph.story_revision,
        record: { kind: 'promise', title: '后卷才解释的暗号', stages: [{ status: 'planned' }] },
      },
    })
    expect(added.ok(), await added.text()).toBeTruthy()
    await page.reload()
    await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
    await page.getByRole('button', { name: '整书创作', exact: true }).click()
    const scope = page.getByRole('combobox', { name: '本次任务范围', exact: true })
    await expect(scope).toHaveValue('whole_book')
    await scope.selectOption('segment')
    await expect(page.getByText(/完成本批情节、篇幅、状态维护与范围审查后记录阶段完成/)).toBeVisible()
    await page.getByText('新版本继承人物与结构计划 · 已选0项', { exact: true }).click()
    await page.getByRole('checkbox', { name: '继承伏笔计划：后卷才解释的暗号', exact: true }).check()
    await page.getByRole('radio', { name: new RegExp(`^${mode}`) }).check()
    await page
      .getByLabel('作品设定、粗纲与结局方向')
      .fill('先测量潮水并记录，本批不解释暗号，不提前公开核验。')
    await page.getByLabel('计划章数', { exact: true }).fill('1')
    await page.getByLabel('每章字数下限').fill('200')
    await page.getByLabel('每章字数上限').fill('600')
    if (mode === '半自动')
      await page.getByLabel('作者批量情节（每行一章）').fill('量潮 | 测量并记录 | 记录读数')
    await page.getByRole('combobox', { name: '采纳政策', exact: true }).selectOption('automatic')
    const responsePromise = page.waitForResponse(
      (response) => response.url().endsWith('/api/workflows') && response.request().method() === 'POST',
    )
    await page.getByRole('button', { name: '创建独立版本并启动', exact: true }).click()
    const response = await responsePromise
    expect(response.ok(), await response.text()).toBeTruthy()
    const task = await response.json()
    expect(task.payload.completion_scope).toBe('segment')
    await expect
      .poll(async () => (await (await page.request.get(`/api/tasks/${task.id}`)).json()).status)
      .toBe('segment_complete')
    await expect(page.getByText('本批阶段完成，非全书完结', { exact: true })).toBeVisible()
    await expect(
      page.getByText('已完成 1 次章节结构维护；完整采用回执保留在检查点报告中。', { exact: true }),
    ).toBeVisible()
    const completion = await (await page.request.get(`/api/branches/${task.branch_id}/completion`)).json()
    expect(completion.segment_complete).toBe(true)
    expect(completion.production_complete).toBe(false)
    expect(completion.accepted).toBe(false)
    expect(completion.model_report.closed).toBe(false)
    expect(completion.promises.closed).toBe(false)
    const maintained = await (await page.request.get(`/api/branches/${task.branch_id}/story-graph`)).json()
    const promise = maintained.records.find((record: { kind: string }) => record.kind === 'promise')
    expect(promise.payload.stages.map((stage: { status: string }) => stage.status)).toEqual(['planned'])
    const retained = await (await page.request.get(`/api/chapters/${original.id}`)).json()
    expect(retained.content).toBe('独立版本的作者原稿😀')
    expect(retained.revision).toBe(1)
  })
}
