import { expect, test } from '@playwright/test'

test('作者单独确认必达承诺，本机体检阻断，独立 Web 断网仍可读并确认修改', async ({ page, context }) => {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const created = await page.request.post('/api/novels', { headers, data: { title: '遗铃 · 必达承诺' } })
  expect(created.ok(), await created.text()).toBe(true)
  const novel = await created.json()
  const branch = `/api/branches/${novel.branch_id}`
  const chapterResponse = await page.request.post(branch + '/chapters', {
    headers,
    data: { title: '铃声落幕' },
  })
  expect(chapterResponse.ok(), await chapterResponse.text()).toBe(true)
  const chapter = (await chapterResponse.json()).id
  const prose = await page.request.put(`/api/chapters/${chapter}`, {
    headers,
    data: { base_revision: 0, content: '学徒握紧铜铃，走出门去。' },
  })
  expect(prose.ok(), await prose.text()).toBe(true)
  const draft = await page.request.put(branch + '/outline-workspace', {
    headers,
    data: {
      base_revision: 0,
      plan: {
        summary: '追踪失落铜铃。',
        volumes: [
          {
            id: 'v1',
            title: '遗铃',
            summary: '循声查访。',
            units: [
              {
                id: 'u1',
                title: '访查',
                summary: '查清来历。',
                chapters: [
                  {
                    chapter_id: chapter,
                    goal: '查到失主',
                    obstacle: '失主无名',
                    turn: '发现铃纹',
                    result: '找到线索',
                  },
                ],
              },
            ],
          },
        ],
        new_promises: [
          {
            title: '归还铜铃',
            plant_chapter_id: chapter,
            opens_chapter_id: chapter,
            closes_chapter_id: chapter,
          },
        ],
      },
    },
  })
  expect(draft.ok(), await draft.text()).toBe(true)
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '大纲与台账', exact: true }).click()
  await page.getByRole('checkbox', { name: '必须在回收窗口内兑现' }).press('Space')
  await page.getByRole('button', { name: '保存大纲草稿' }).click()
  const adopt = page.getByRole('button', { name: '确认已保存大纲', exact: true })
  const acknowledge = page.getByRole('checkbox', { name: '我已核对必达承诺的新增、调整或撤销' })
  await expect(acknowledge).toBeVisible()
  await expect(adopt).toBeDisabled()
  await acknowledge.press('Space')
  await adopt.press('Enter')
  await expect(page.getByText(/归还铜铃 · 计划 · 必达承诺尚未兑现/)).toBeVisible()

  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  const inspection = page.getByRole('region', { name: '十处对账体检', exact: true })
  await inspection.getByText('本次依据与未核查项', { exact: true }).click()
  await expect(inspection.getByText(/必达承诺到期仍缺回收窗口内的有效原文：归还铜铃/)).toBeVisible()
  const finish = page.getByRole('region', { name: '完本与交付', exact: true })
  await expect(finish.getByText(/仍待兑现的必达承诺：归还铜铃/)).toBeVisible()

  const exported = await page.request.get(branch + '/manuscript-package')
  expect(exported.ok(), await exported.text()).toBe(true)
  const pack = await exported.json()
  expect(pack.outline_history.versions[0].plan.promises[0].fulfillment).toBe('author_required')
  await page.goto('/offline/')
  await expect(page.getByText('离线页面就绪', { exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
  await context.setOffline(true)
  await page.getByLabel('导入新离线作品').setInputFiles({
    name: '必达承诺.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(pack)),
  })
  await expect(page.getByText('已校验并导入稿件及历史。')).toBeVisible()
  await page.reload()
  await page.getByRole('combobox', { name: '离线作品', exact: true }).selectOption(novel.id)
  await page.getByText('本机规则核算', { exact: true }).click()
  await expect(page.getByText(/归还铜铃 · 回收已到期 · 必达承诺尚未兑现/)).toBeVisible()
  await page.getByText('大纲台', { exact: true }).click()
  await page.getByRole('button', { name: '编辑当前大纲', exact: true }).click()
  const required = page.getByRole('checkbox', { name: '必须在回收窗口内兑现' })
  await expect(required).toBeChecked()
  await required.press('Space')
  const offlineAdopt = page.getByRole('button', { name: '确认大纲并保存到浏览器', exact: true })
  await expect(offlineAdopt).toBeDisabled()
  await acknowledge.press('Space')
  await offlineAdopt.press('Enter')
  await expect(page.getByText('已确认并保存大纲修订。', { exact: true })).toBeVisible()
  await expect(page.getByText(/归还铜铃 · 回收已到期 · 必达承诺尚未兑现/)).toHaveCount(0)
})
