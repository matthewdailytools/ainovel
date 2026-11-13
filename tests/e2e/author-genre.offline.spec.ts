import { test, expect } from '@playwright/test'

test('手动创作约定与女频无CP题材：保存、预览、重载及跨作品隔离，不调用模型', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novels = []
  for (const title of ['后台验证 · 创作约定甲', '后台验证 · 创作约定乙']) {
    const response = await page.request.post('/api/novels', { headers, data: { title } })
    expect(response.ok(), await response.text()).toBeTruthy()
    novels.push(await response.json())
  }
  await page.reload()
  const selector = page.getByRole('combobox', { name: '作品', exact: true })
  await selector.selectOption(novels[0].id)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  const intent = page.locator('#author-intent')
  const genre = page.locator('#genre-settings')
  const authorText = '女频无CP事业成长。让读者先紧张后释然，追问谁改了账本；交易最终失败，不战斗。😀𠮷'
  const fixedResult = '本次交易最终失败'
  const prohibitions = '不战斗\n不增加恋爱主线或后宫'
  const exceptions = '无CP，不增加恋爱主线\n交易必须失败，不战斗'
  const budgetBefore = (await (await page.request.get('/api/config')).json()).budget
  await intent.getByText('编写或整理创作约定（确认后生效）', { exact: true }).click()
  await intent.getByRole('textbox', { name: '作者原话与创作想法', exact: true }).fill(authorText)
  await intent.getByRole('textbox', { name: '情绪与阅读体验（可留空）', exact: true }).fill('先紧张后释然')
  await intent.getByRole('textbox', { name: '核心钩子与追问（可留空）', exact: true }).fill('谁改了账本')
  await intent.getByRole('textbox', { name: '故事方向（可留空）', exact: true }).fill('靠核验证据取得主动')
  await intent.getByRole('textbox', { name: '锁定结果（每行一项，可留空）', exact: true }).fill(fixedResult)
  await intent.getByRole('textbox', { name: '禁止改变（每行一项，可留空）', exact: true }).fill(prohibitions)
  await intent.getByRole('button', { name: '确认并采用编辑后的创作约定', exact: true }).click()
  await expect(
    intent.getByText('已确认并采用。后续新任务继承本约定；旧候选保留其生成时来源。', { exact: true }),
  ).toBeVisible()
  const contractA = await (
    await page.request.get(`/api/branches/${novels[0].branch_id}/author-contract`)
  ).json()
  expect(contractA.author_text).toBe(authorText)
  expect(contractA.intent_contract.fixed_results).toEqual([fixedResult])
  expect(contractA.intent_contract.prohibitions).toEqual(prohibitions.split('\n'))
  expect(contractA.source_artifact_id).toBeTruthy()

  await genre.getByText('编辑本书配置（保存后生效）', { exact: true }).click()
  await genre.getByRole('combobox', { name: '阅读取向', exact: true }).selectOption('女频')
  await genre.getByRole('combobox', { name: '主题材', exact: true }).selectOption('xianxia')
  await genre.getByRole('textbox', { name: '其他自定义辅题材（顿号分隔）', exact: true }).fill('水利考据')
  await genre.getByRole('textbox', { name: '作者例外与明确边界（每行一条）', exact: true }).fill(exceptions)
  await genre
    .getByRole('textbox', { name: '本书阅读承诺与补充说明', exact: true })
    .fill('不以频道自动推导爱情关系。')
  await genre.getByRole('button', { name: '预览装配结果（不调用模型）', exact: true }).click()
  await expect(genre.getByRole('heading', { name: '配置草稿预览 · 尚未保存', exact: true })).toBeVisible()
  await expect(genre.locator('.genre-preview')).toContainText('女频')
  await expect(genre.locator('.genre-preview')).toContainText('无CP，不增加恋爱主线')
  const unadopted = await (
    await page.request.get(`/api/branches/${novels[0].branch_id}/genre-profile`)
  ).json()
  expect(unadopted.configured).toBe(false)
  await genre.getByRole('button', { name: '保存并采用题材配置', exact: true }).click()
  await expect(
    genre.getByText('已保存并采用新配置。后续新任务将使用它；已有正文保持，旧任务或候选需核对版本影响。', {
      exact: true,
    }),
  ).toBeVisible()
  const profileA = await (await page.request.get(`/api/branches/${novels[0].branch_id}/genre-profile`)).json()
  expect(profileA.audience).toBe('女频')
  expect(profileA.genre_ids).toContain('xianxia')
  expect(profileA.custom_genres).toContain('水利考据')
  expect(profileA.author_exceptions).toEqual(exceptions.split('\n'))
  expect(profileA.source_artifact_id).toBeTruthy()

  await page.reload()
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  await intent.getByText('编写或整理创作约定（确认后生效）', { exact: true }).click()
  await expect(intent.getByRole('textbox', { name: '作者原话与创作想法', exact: true })).toHaveValue(
    authorText,
  )
  await expect(
    intent.getByRole('textbox', { name: '锁定结果（每行一项，可留空）', exact: true }),
  ).toHaveValue(fixedResult)
  await genre.getByText('编辑本书配置（保存后生效）', { exact: true }).click()
  await expect(genre.getByRole('combobox', { name: '阅读取向', exact: true })).toHaveValue('女频')
  await expect(
    genre.getByRole('textbox', { name: '作者例外与明确边界（每行一条）', exact: true }),
  ).toHaveValue(exceptions)
  await genre.getByRole('button', { name: '重新载入当前采用配置（替换本表单）', exact: true }).click()
  await genre.getByText('编辑本书配置（保存后生效）', { exact: true }).click()
  await expect(genre.getByRole('combobox', { name: '主题材', exact: true })).toHaveValue('xianxia')

  await selector.selectOption(novels[1].id)
  await expect(page.getByRole('combobox', { name: '工作版本', exact: true })).toHaveValue(novels[1].branch_id)
  await intent.getByText('编写或整理创作约定（确认后生效）', { exact: true }).click()
  await expect(intent.getByRole('textbox', { name: '作者原话与创作想法', exact: true })).toHaveValue('')
  await expect(
    intent.getByRole('textbox', { name: '锁定结果（每行一项，可留空）', exact: true }),
  ).toHaveValue('')
  await genre.getByText('编辑本书配置（保存后生效）', { exact: true }).click()
  await expect(genre.getByRole('combobox', { name: '阅读取向', exact: true })).toHaveValue('不限')
  await expect(
    genre.getByRole('textbox', { name: '作者例外与明确边界（每行一条）', exact: true }),
  ).toHaveValue('')
  const contractB = await (
    await page.request.get(`/api/branches/${novels[1].branch_id}/author-contract`)
  ).json()
  const profileB = await (await page.request.get(`/api/branches/${novels[1].branch_id}/genre-profile`)).json()
  expect(contractB.source_artifact_id).toBeNull()
  expect(profileB.source_artifact_id).toBeNull()
  await selector.selectOption(novels[0].id)
  await expect(page.getByRole('combobox', { name: '工作版本', exact: true })).toHaveValue(novels[0].branch_id)
  expect(
    (await (await page.request.get(`/api/branches/${novels[0].branch_id}/author-contract`)).json())
      .source_artifact_id,
  ).toBe(contractA.source_artifact_id)
  expect(
    (await (await page.request.get(`/api/branches/${novels[0].branch_id}/genre-profile`)).json())
      .source_artifact_id,
  ).toBe(profileA.source_artifact_id)
  const tasks = await (await page.request.get('/api/tasks')).json()
  expect(
    tasks.filter((row: { novel_id: string }) => novels.some((novel) => novel.id === row.novel_id)),
  ).toEqual([])
  expect((await (await page.request.get('/api/config')).json()).budget).toEqual(budgetBefore)
})
