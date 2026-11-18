import { expect, test } from '@playwright/test'
import { authorSettingPlan } from './fixtures/authorSettingPlan'

test('独立 Web 预览单章恢复的正文、伏笔和检查，另一窗口保存后须重读', async ({ page, context }) => {
  const session = await page.request.get('/api/session')
  const headers = { 'X-Ainovel-CSRF': (await session.json()).csrf }
  const draft = await page.request.post('/api/novels', { headers, data: { title: '遗铃 · 断网还原' } })
  expect(draft.ok()).toBe(true)
  const { branch_id } = await draft.json()
  const branch = `/api/branches/${branch_id}`
  const first = '故事从不响的旧铃开始。'
  const saved = '😀学徒在门内摇响铜铃。'
  async function addChapter(title: string, texts: string[]) {
    const created = await page.request.post(`${branch}/chapters`, { headers, data: { title } })
    expect(created.ok()).toBe(true)
    let chapter = await created.json()
    for (const content of texts) {
      const edit = await page.request.put(`/api/chapters/${chapter.id}`, {
        headers,
        data: { base_revision: chapter.revision, content },
      })
      expect(edit.ok()).toBe(true)
      chapter = await edit.json()
    }
    return chapter
  }
  const source = await addChapter('拾铃', [first, saved])
  const other = await addChapter('归还', ['师姐带着旧铃回家。'])
  const { story_revision } = await (await page.request.get(`${branch}/story-graph`)).json()
  const promiseResponse = await page.request.post(`${branch}/story-records`, {
    headers,
    data: {
      base_story_revision: story_revision,
      record: {
        kind: 'promise',
        title: '铃声的秘密',
        stages: [
          { status: 'planned' },
          {
            status: 'planted',
            sources: [
              {
                chapter_id: source.id,
                revision: source.revision,
                start: 2,
                end: saved.length,
                quote: saved.slice(2),
              },
            ],
          },
        ],
      },
    },
  })
  expect(promiseResponse.ok(), await promiseResponse.text()).toBe(true)
  const {
    records: [promise],
  } = await promiseResponse.json()
  const volumes = [source, other].map((chapter, index) => ({
    id: `vol-${index}`,
    title: ['拾铃卷', '归还卷'][index],
    summary: '循铃声寻找失散的人。',
    units: [
      {
        id: `unit-${index}`,
        title: ['听声', '归来'][index],
        summary: '核对证据。',
        chapters: [
          {
            chapter_id: chapter.id,
            goal: '找到线索',
            obstacle: '旧路封住',
            turn: '重听铃声',
            result: '找到归处',
          },
        ],
      },
    ],
  }))
  const outline = await page.request.put(`${branch}/outline-workspace`, {
    headers,
    data: {
      base_revision: 0,
      plan: {
        summary: '寻铃并归还。',
        volumes,
        promises: [
          {
            promise_id: promise.id,
            plant_chapter_id: source.id,
            opens_chapter_id: other.id,
            closes_chapter_id: other.id,
          },
        ],
      },
    },
  })
  expect(outline.ok(), await outline.text()).toBe(true)
  const confirmation = await page.request.post(`${branch}/outline-workspace/confirm`, {
    headers,
    data: { base_revision: (await outline.json()).draft.revision },
  })
  expect(confirmation.ok(), await confirmation.text()).toBe(true)
  const route = `${branch}/workflow-inspections`
  const reports: string[] = []
  for (const volume of volumes) {
    const scope = { scope: 'volume', scope_id: volume.id }
    const targets = await page.request.put(`${route}/length-targets`, {
      headers,
      data: { ...scope, targets: { min_chars: 1, expected_chapters: 1 } },
    })
    expect(targets.ok(), await targets.text()).toBe(true)
    const { preview } = await targets.json()
    const report = await page.request.post(route, {
      headers,
      data: { ...scope, basis_sha256: preview.basis_sha256 },
    })
    expect(report.ok(), await report.text()).toBe(true)
    reports.push((await report.json()).id)
  }
  const pack = await page.request.get(`${branch}/manuscript-package`)
  expect(pack.ok()).toBe(true)
  await page.setViewportSize({ width: 320, height: 900 })
  await page.goto('/offline/')
  await expect(page.getByText('离线页面就绪', { exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
  await context.setOffline(true)
  await page.getByLabel('导入新离线作品').setInputFiles({
    name: '遗铃.json',
    mimeType: 'application/json',
    buffer: await pack.body(),
  })
  const openChapter = async () => page.getByRole('button', { name: '1. 拾铃', exact: true }).click()
  await openChapter()
  await page.getByText('本章版本历史', { exact: true }).press('Enter')
  await page.getByLabel('历史修订').selectOption({ label: '修订 1 · 作者编辑' })
  const preview = page.getByRole('region', { name: '章节恢复预览', exact: true })
  await expect(preview.locator('[aria-label="当前稿 · 修订 2"]')).toHaveText(saved)
  await expect(preview.locator('[aria-label="历史稿 · 修订 1"]')).toHaveText(first)
  await expect(preview.getByRole('region', { name: '引用与伏笔影响' })).toContainText('铃声的秘密')
  await expect(preview.getByRole('region', { name: '引用与伏笔影响' })).toContainText(saved.slice(2))
  const affected = preview.getByRole('region', { name: '受影响报告' })
  await expect(affected).toContainText(reports[0])
  await expect(affected).not.toContainText(reports[1])
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)

  const peer = await context.newPage()
  await peer.goto('/offline/')
  await peer
    .getByRole('combobox', { name: '离线作品', exact: true })
    .selectOption({ label: '遗铃 · 断网还原' })
  await peer.getByRole('button', { name: '1. 拾铃', exact: true }).click()
  const next = '另一窗口已保存她听见第二声铃。'
  await peer.getByRole('textbox', { name: '章节正文', exact: true }).fill(next)
  await peer.getByRole('button', { name: '保存正文到浏览器' }).click()
  await expect(peer.getByRole('status')).toContainText('修订 3')
  const restore = page.getByRole('button', { name: '恢复为最新修订', exact: true })
  await restore.press('Enter')
  await expect(page.getByRole('alert')).toContainText('请重新读取恢复预览')
  await expect(restore).toHaveCount(0)
  await expect(page.getByRole('textbox', { name: '章节正文', exact: true })).toHaveValue(saved)
  await page.getByRole('button', { name: '重新读取恢复预览', exact: true }).press('Enter')
  await expect(preview.locator('[aria-label="当前稿 · 修订 3"]')).toHaveText(next)
  await expect(affected).not.toContainText(reports[0])
  await restore.press('Enter')
  await expect(page.getByRole('status')).toContainText('修订 4')
  await expect(page.getByRole('textbox', { name: '章节正文', exact: true })).toHaveValue(first)
  await page.getByLabel('历史修订').selectOption({ label: '修订 3 · 作者编辑' })
  await expect(preview.locator('[aria-label="历史稿 · 修订 3"]')).toHaveText(next)
})

test('独立 Web 另一窗口正式签注章稿后，旧恢复预览须重读原文依据', async ({ page, context }) => {
  const { novel, branch, chapter, original } = await authorSettingPlan(page, '赤钥 · 回滚前的设定使用')
  const pack = await page.request.get(`${branch}/manuscript-package`)
  expect(pack.ok(), await pack.text()).toBe(true)
  const sourceRows: { id: string; chapter_id: string; revision: number }[] = (await pack.json()).revisions
  const originalId = sourceRows.find((row) => row.chapter_id === chapter.id && row.revision === 1)!.id
  const targetId = sourceRows.find((row) => row.chapter_id === chapter.id && row.revision === 0)!.id
  await page.setViewportSize({ width: 320, height: 900 })
  await page.goto('/offline/')
  await expect(page.getByText('离线页面就绪', { exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
  await context.setOffline(true)
  await page.getByLabel('导入新离线作品').setInputFiles({
    name: '赤钥.json',
    mimeType: 'application/json',
    buffer: await pack.body(),
  })
  await page.getByRole('button', { name: '1. 归还赤钥', exact: true }).press('Enter')
  await page.getByText('本章版本历史', { exact: true }).press('Enter')
  await page.getByLabel('历史修订').selectOption(targetId)
  const preview = page.getByRole('region', { name: '章节恢复预览' })
  const effects = preview.getByRole('region', { name: '引用与伏笔影响' })
  await expect(preview).toBeVisible()
  await expect(effects.getByText(/设定正文使用/)).toHaveCount(0)

  const peer = await context.newPage()
  await peer.goto('/offline/')
  await peer.getByRole('combobox', { name: '离线作品', exact: true }).selectOption(novel.id)
  await peer.getByRole('button', { name: '1. 归还赤钥', exact: true }).press('Enter')
  const peerProof = peer.getByLabel('离线设定正文证据')
  await peerProof.getByText('设定的正文使用证据', { exact: true }).press('Enter')
  await peerProof.getByRole('combobox', { name: '本章引用的设定' }).selectOption('keeper')
  await peer.getByRole('textbox', { name: '章节正文' }).press('Control+A')
  await peerProof.getByRole('button', { name: '确认该选区为正文证据' }).press('Enter')
  await expect(peerProof.getByRole('status')).toHaveText('已确认当前正文使用该设定。')

  const restore = page.getByRole('button', { name: '恢复为最新修订' })
  await restore.press('Enter')
  await expect(page.getByRole('alert')).toContainText('请重新读取恢复预览')
  const editor = page.getByRole('textbox', { name: '章节正文' })
  await expect(editor).toHaveValue(original)
  await page.getByRole('button', { name: '重新读取恢复预览' }).press('Enter')
  const source = effects.getByRole('listitem').filter({ hasText: '设定正文使用 · 守铃人阿陶' })
  await expect(source).toContainText(originalId)
  await expect(source).toContainText(`位置 0–${original.length}`)
  await expect(source.locator('pre')).toHaveText(original)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)
  await restore.press('Enter')
  await expect(page.getByRole('status').filter({ hasText: '本地已保存' })).toContainText('修订 2')
  await expect(editor).toHaveValue('')
  const proofs = page.getByLabel('离线设定正文证据')
  await proofs.getByText('设定的正文使用证据', { exact: true }).press('Enter')
  const old = proofs.getByText('守铃人阿陶 · 待按当前正文或设定重新核对', { exact: true })
  await old.press('Enter')
  await expect(old.locator('..').locator('blockquote')).toHaveText(original)
})
