import { expect, test } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { writeFile } from 'node:fs/promises'

test('六项核对通过后确认完结，改稿后仍可离线恢复并下载原交付包', async ({
  page,
  context,
  browser,
}, testInfo) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novelResponse = await page.request.post('/api/novels', {
    headers,
    data: { title: '后台验证 · 遗铃完本交付' },
  })
  expect(novelResponse.ok(), await novelResponse.text()).toBe(true)
  const novel = await novelResponse.json()
  const branch = `/api/branches/${novel.branch_id}`
  const text = '😀学徒捡起铜铃。\r\n他看见铃身的旧痕。'
  const createChapter = async (title: string, content: string) => {
    const draft = await page.request.post(`${branch}/chapters`, { headers, data: { title } })
    expect(draft.ok(), await draft.text()).toBe(true)
    const { id } = await draft.json()
    const saved = await page.request.put(`/api/chapters/${id}`, {
      headers,
      data: { base_revision: 0, content },
    })
    expect(saved.ok(), await saved.text()).toBe(true)
    return saved.json()
  }
  const chapter = await createChapter('拾铃', text)
  await createChapter('归铃', '师姐归来，两人把铜铃交回主人。')

  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  let releaseDirectory = () => {}
  const directoryGate = new Promise<void>((resolve) => {
    releaseDirectory = resolve
  })
  await page.route(
    `**${branch}/feedback`,
    async (route) => {
      await directoryGate
      await route.continue()
    },
    { times: 1 },
  )
  await page.getByRole('button', { name: '故事与审阅', exact: true }).click()
  const panel = page.getByRole('region', { name: '完本与交付', exact: true })
  const previewButton = panel.getByRole('button', { name: '预览交付材料' })
  await expect(panel.getByText('稿件齐全与通读', { exact: true })).toBeVisible()
  await expect(previewButton).toBeDisabled()
  releaseDirectory()
  await previewButton.click()
  await expect(panel.getByText('请填写简介并选择有保存稿的试读章，生成交付预览。')).toBeVisible()
  await panel.getByRole('textbox', { name: '通读记录', exact: true }).fill('已通读两章保存稿。')
  await panel.getByRole('textbox', { name: '主冲突与终局说明', exact: true }).fill('寻找师姐与归还铜铃收束。')
  await panel.getByRole('textbox', { name: '关键人物与关系说明', exact: true }).fill('师姐回到学徒身边。')
  await panel
    .getByRole('textbox', { name: '伏笔与读者承诺说明', exact: true })
    .fill('铜铃刻痕提示了师姐的去向。')
  await panel.getByRole('textbox', { name: '简介', exact: true }).fill('学徒捡到一枚带刻痕的铜铃。')
  await panel.getByText('添加正文依据', { exact: true }).click()
  await panel.getByLabel('完本引文章节').selectOption(chapter.id)
  const excerpt = panel.getByRole('textbox', { name: /保存稿修订.*选择要引用的文字/ })
  await expect(excerpt).toHaveValue(text.replaceAll('\r\n', '\n'))
  await excerpt.press('ControlOrMeta+a')
  await panel.getByRole('button', { name: '引用选区' }).click()

  const review = panel.getByRole('checkbox', { name: '已核对这次交付材料与剧透范围' })
  const confirmButton = panel.getByRole('button', { name: '确认完结', exact: true })
  await previewButton.click()
  await review.check()
  await expect(confirmButton).toBeDisabled()

  const inspections = `${branch}/workflow-inspections`
  const { preview } = await (await page.request.get(inspections)).json()
  const inspection = await page.request.post(inspections, {
    headers,
    data: {
      basis_sha256: preview.basis_sha256,
      author_reviews: preview.items
        .filter((item: { status: string }) => item.status === 'pending')
        .map((item: { check_id: string }) => ({
          check_id: item.check_id,
          status: 'green',
          basis_note: '已核对章纲与全部保存稿。',
          reason: '铜铃经学徒和师姐查到主人，结果清楚。',
          evidence: [
            { chapter_id: chapter.id, revision: chapter.revision, start: 0, end: text.length, quote: text },
          ],
        })),
      length_targets: { min_chars: 1, expected_chapters: 2 },
    },
  })
  expect(inspection.ok(), await inspection.text()).toBe(true)
  const reloaded = page.waitForResponse(
    (response) => response.request().method() === 'GET' && response.url().endsWith('/book-completion'),
  )
  await panel.getByRole('button', { name: '核对最新完本依据' }).click()
  expect((await reloaded).ok()).toBe(true)
  await previewButton.click()
  await review.check()
  const proposed = panel.getByRole('list', { name: '本次交付预览的六项判定', exact: true })
  await expect(proposed.getByText('待核对', { exact: true })).toHaveCount(4)
  await expect(confirmButton).toBeDisabled()
  await panel.getByRole('checkbox', { name: '已通读本次稿件', exact: true }).check()
  await panel.getByRole('combobox', { name: '主冲突与终局判定', exact: true }).selectOption('verified')
  await panel
    .getByRole('combobox', { name: '关键人物与关系判定', exact: true })
    .selectOption('author_decision')
  await panel.getByRole('combobox', { name: '伏笔与读者承诺判定', exact: true }).selectOption('verified')
  await previewButton.click()
  const materials = panel.getByRole('region', { name: '交付预览', exact: true })
  const sampleLabel = materials.locator('summary').filter({ hasText: /^试读\.md$/ })
  const sample = sampleLabel.locator('..')
  await sampleLabel.click()
  await expect(sample.locator('pre')).toContainText('学徒捡起铜铃')
  await expect(sample.locator('pre')).not.toContainText('师姐归来')
  await expect(confirmButton).toBeDisabled()
  await expect(proposed.getByText('待核对', { exact: true })).toHaveCount(1)
  let releaseReview = () => {}
  const reviewGate = new Promise<void>((resolve) => {
    releaseReview = resolve
  })
  await page.route(
    `**${branch}/book-completion/preview`,
    async (route) => {
      expect(route.request().postDataJSON().materials_reviewed).toBe(true)
      await reviewGate
      await route.continue()
    },
    { times: 1 },
  )
  await review.check()
  await expect(materials.getByRole('status')).toContainText('正在核对六项完本判定')
  await expect(confirmButton).toBeDisabled()
  await expect(proposed.getByText('待核对', { exact: true })).toHaveCount(1)
  releaseReview()
  await expect(proposed.getByText('绿 · 通过', { exact: true })).toHaveCount(6)
  await expect(confirmButton).toBeEnabled()
  const confirmed = page.waitForResponse((response) => response.url().endsWith('/book-completion/confirm'))
  await confirmButton.click()
  const response = await confirmed
  expect(response.ok(), await response.text()).toBe(true)
  expect(response.request().postDataJSON().ending.sources).toEqual([
    { chapter_id: chapter.id, revision: chapter.revision, start: 0, end: text.length, quote: text },
  ])
  expect(response.request().postDataJSON().author_decisions).toEqual({
    reading: 'verified',
    ending: 'verified',
    characters: 'author_decision',
    promises: 'verified',
  })
  await expect(panel.getByRole('heading', { name: '当前版本已完结' })).toBeVisible()
  await panel.getByRole('button', { name: '核对最新完本依据' }).click()
  const official = panel.getByRole('list', { name: '当前六项完本判定', exact: true })
  await expect(official.getByText('绿 · 通过', { exact: true })).toHaveCount(6)
  await expect(panel.getByRole('list', { name: '本次拟确认的六项完本判定', exact: true })).toHaveCount(0)
  const download = panel.getByRole('link', { name: '下载交付包', exact: true })
  const url = (await download.getAttribute('href'))!
  const packageResponse = await page.request.get(url)
  expect(packageResponse.ok()).toBe(true)
  expect(packageResponse.headers()['content-type']).toBe('application/zip')
  const originalZip = testInfo.outputPath('desktop-delivery.zip')
  await writeFile(originalZip, await packageResponse.body())

  const changed = await page.request.put(`/api/chapters/${chapter.id}`, {
    headers,
    data: { base_revision: chapter.revision, content: '学徒没有捡到铜铃。' },
  })
  expect(changed.ok(), await changed.text()).toBe(true)
  await panel.getByRole('button', { name: '核对最新完本依据' }).click()
  await expect(panel.getByRole('heading', { name: '历史完结记录 · 当前依据已变化' })).toBeVisible()
  await expect(official).toHaveCount(0)
  await expect(panel.getByRole('list', { name: '本次拟确认的六项完本判定', exact: true })).toBeVisible()
  await panel.getByText('完结判定与文件校验值', { exact: true }).click()
  const historical = panel.getByRole('list', { name: '历史六项完本判定', exact: true })
  await expect(historical.getByText('绿 · 通过', { exact: true })).toHaveCount(6)
  await expect(panel.getByRole('heading', { name: '关键人物与关系 · 作者已裁定', exact: true })).toBeVisible()
  await expect(panel.getByRole('link', { name: '下载历史交付包' })).toHaveAttribute('href', url)
  expect((await page.request.get(url)).ok()).toBe(true)

  const manuscript = await page.request.get(`${branch}/manuscript-package`)
  expect(manuscript.ok(), await manuscript.text()).toBe(true)
  const pack = await manuscript.json()
  const history = pack.workflow_history
  expect(history.inspections).toHaveLength(1)
  expect(history.completions).toHaveLength(1)

  await page.goto('/offline/')
  await expect(page.getByText('离线页面就绪', { exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
  await context.setOffline(true)
  const sourceFile = page.getByLabel('导入新离线作品')
  const corrupted = structuredClone(pack)
  corrupted.workflow_history.completions[0].materials['简介.md'] = '被改过的交付简介'
  await sourceFile.setInputFiles({
    name: '校验失败的档案.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(corrupted)),
  })
  await expect(page.getByRole('alert').filter({ hasText: '历史交付材料' })).toBeVisible()
  const waiting = structuredClone(pack)
  waiting.workflow_history.completions[0].review.author_decisions.ending = 'pending'
  await sourceFile.setInputFiles({
    name: '作者仍待核对.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(waiting)),
  })
  await expect(page.getByRole('alert').filter({ hasText: '历史完本的作者裁定尚未完成' })).toBeVisible()
  await sourceFile.setInputFiles({
    name: '完整作品.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(pack)),
  })
  await expect(page.getByRole('status').filter({ hasText: '已校验并导入稿件及历史。' })).toBeVisible()
  await page.reload()
  await page.getByRole('combobox', { name: '离线作品', exact: true }).selectOption(novel.id)
  const archive = page.getByLabel('历次体检与完本交付')
  await archive.getByText('历次体检与完本交付', { exact: true }).click()
  const historyPicker = archive.getByLabel('选择历史报告')
  await historyPicker.selectOption(history.inspections[0].id)
  await expect(archive.getByRole('list', { name: '十处对账' }).locator('li > details')).toHaveCount(10)
  await archive.getByText(/立项及作者约定与正文/).click()
  await expect(archive.getByRole('region', { name: '原作者核对' })).toContainText('铜铃经学徒和师姐查到主人')
  await archive
    .getByRole('button', { name: /查看来源修订/ })
    .first()
    .click()
  await expect(archive.getByRole('region', { name: '历史报告引用正文' })).toContainText('他看见铃身的旧痕。')
  await historyPicker.selectOption(history.completions[0].id)
  await expect(archive.getByRole('region', { name: '历史完本清单' }).getByRole('listitem')).toHaveCount(6)
  await archive.getByText('查看这次原裁定', { exact: true }).press('Enter')
  await expect(
    archive.getByRole('heading', { name: '关键人物与关系 · 作者已裁定', exact: true }),
  ).toBeVisible()
  await archive.getByRole('button', { name: '查看主冲突与终局原文 1', exact: true }).press('Enter')
  await expect(archive.getByRole('region', { name: '历史报告引用正文' }).locator('mark')).toContainText(
    '他看见铃身的旧痕。',
  )
  const output = page.waitForEvent('download')
  await archive.getByRole('button', { name: '下载历史交付 ZIP' }).click()
  const browserZip = testInfo.outputPath('browser-delivery.zip')
  await (await output).saveAs(browserZip)
  const python = process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python'
  execFileSync(python, [
    '-c',
    [
      'import json, sys, zipfile',
      'with zipfile.ZipFile(sys.argv[1]) as desktop, zipfile.ZipFile(sys.argv[2]) as browser:',
      '    assert set(desktop.namelist()) == set(browser.namelist()) and len(browser.namelist()) == 5',
      '    assert desktop.testzip() is None and browser.testzip() is None',
      '    assert json.loads(desktop.read("manifest.json")) == json.loads(browser.read("manifest.json"))',
      '    assert all(desktop.read(name) == browser.read(name) for name in desktop.namelist() if name != "manifest.json")',
    ].join('\n'),
    originalZip,
    browserZip,
  ])

  if (process.env.AINOVEL_SYNC_E2E === '1') {
    await context.setOffline(false)
    const cloud = process.env.AINOVEL_TEST_SYNC_URL ?? 'http://127.0.0.1:8888'
    async function grant(device_id: string) {
      const response = await page.request.post(`${cloud}/api/sync/works/${novel.branch_id}/devices`, {
        headers: { Authorization: 'Bearer e2e-test-only-provisioning-token' },
        data: { device_id },
      })
      expect(response.ok(), await response.text()).toBe(true)
      return (await response.json()).token
    }
    const desktopToken = await grant('desktop-workflow-history')
    const webToken = await grant('web-workflow-history')
    for (const action of ['enable', 'run']) {
      const response = await page.request.post(`${branch}/desktop-sync/${action}`, {
        headers,
        data: { origin: cloud, token: desktopToken },
      })
      expect(response.ok(), await response.text()).toBe(true)
    }
    const fresh = await browser.newContext()
    try {
      const web = await fresh.newPage()
      await web.goto(new URL('/offline/', page.url()).href)
      const initial = structuredClone(pack)
      delete initial.workflow_history
      delete initial.evidence
      await web.getByLabel('导入新离线作品').setInputFiles({
        name: '仅稿件基线.json',
        mimeType: 'application/json',
        buffer: Buffer.from(JSON.stringify(initial)),
      })
      await expect(web.getByLabel('历次体检与完本交付')).toHaveCount(0)
      await web.getByText('作品云同步', { exact: true }).click()
      await web.getByLabel('同步服务地址').fill(cloud)
      await web.getByLabel('设备编号').fill('web-workflow-history')
      await web.getByLabel('设备凭据').fill(webToken)
      await web.getByRole('button', { name: '启用作品同步', exact: true }).click()
      await web.getByRole('button', { name: '同步当前工作版本', exact: true }).click()
      const remoteArchive = web.getByLabel('历次体检与完本交付')
      await remoteArchive.getByText('历次体检与完本交付', { exact: true }).click()
      await remoteArchive.getByLabel('选择历史报告').selectOption(history.completions[0].id)
      await expect(
        remoteArchive.getByText('源自同步设备 desktop-workflow-history', { exact: false }),
      ).toBeVisible()
      await remoteArchive.getByText('查看这次原裁定', { exact: true }).press('Enter')
      await expect(
        remoteArchive.getByRole('heading', { name: '关键人物与关系 · 作者已裁定', exact: true }),
      ).toBeVisible()
      await remoteArchive
        .getByRole('button', { name: /查看来源修订/ })
        .first()
        .click()
      await expect(remoteArchive.getByRole('region', { name: '历史报告引用正文' })).toContainText(
        '他看见铃身的旧痕。',
      )
      await web.getByRole('button', { name: '1. 拾铃', exact: true }).click()
      await expect(web.getByRole('textbox', { name: '章节正文' })).toHaveValue('学徒没有捡到铜铃。')
    } finally {
      await fresh.close()
    }
  }
})

test('AI 完本建议载入空白项，作者通读仍待确认', async ({ page }) => {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const novel = await page.request.post('/api/novels', { headers, data: { title: '遗铃 · 完本建议' } })
  expect(novel.ok(), await novel.text()).toBe(true)
  const { id, branch_id } = await novel.json()
  const draft = await page.request.post(`/api/branches/${branch_id}/chapters`, {
    headers,
    data: { title: '寻铃' },
  })
  expect(draft.ok(), await draft.text()).toBe(true)
  const chapter = await draft.json()
  const body = '学徒将铜铃交回主人。'
  const saved = await page.request.put(`/api/chapters/${chapter.id}`, {
    headers,
    data: { base_revision: 0, content: body },
  })
  expect(saved.ok(), await saved.text()).toBe(true)
  const endingDraft = await page.request.post(`/api/branches/${branch_id}/chapters`, {
    headers,
    data: { title: '归还' },
  })
  expect(endingDraft.ok(), await endingDraft.text()).toBe(true)
  const ending = '在末章，学徒才发现铃匠就是失散的师姐。'
  const endingSaved = await page.request.put(`/api/chapters/${(await endingDraft.json()).id}`, {
    headers,
    data: { base_revision: 0, content: ending },
  })
  expect(endingSaved.ok(), await endingSaved.text()).toBe(true)
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(id)
  await page.getByRole('button', { name: '完本台', exact: true }).click()
  const panel = page.getByRole('region', { name: '完本与交付', exact: true })
  await panel.getByRole('textbox', { name: '关键人物与关系说明' }).fill('保留这项作者已有的看法。')
  const spoiler = panel.getByLabel('简介与试读的剧透范围')
  const sample = panel.getByLabel('从首章起选入的试读章数')
  await spoiler.selectOption('whole')
  await sample.selectOption('2')
  await panel.getByText('AI 准备完本建议', { exact: true }).press('Enter')
  await panel.getByRole('button', { name: '生成完本核对建议与简介' }).click()
  await panel.getByRole('button', { name: '载入 AI 建议到空白项' }).click()
  const intro = panel.getByRole('textbox', { name: '简介', exact: true })
  await expect(intro).toHaveValue('学徒从铜铃的来历追起。')
  await expect(panel.getByRole('textbox', { name: '关键人物与关系说明' })).toHaveValue(
    '保留这项作者已有的看法。',
  )
  await expect(panel.getByRole('textbox', { name: '通读记录', exact: true })).toHaveValue('')
  await expect(panel.getByRole('checkbox', { name: '已通读本次稿件', exact: true })).not.toBeChecked()
  await expect(panel.getByRole('combobox', { name: '主冲突与终局判定', exact: true })).toHaveValue('pending')
  await expect(panel.getByText(ending, { exact: false }).first()).toBeVisible()
  const preview = panel.getByRole('button', { name: '预览交付材料' })
  await spoiler.selectOption('opening')
  await expect(panel.getByText(/已载入的 AI 简介采用另一剧透或试读范围/)).toBeVisible()
  await expect(preview).toBeDisabled()
  await expect(intro).toHaveValue('学徒从铜铃的来历追起。')
  await spoiler.selectOption('whole')
  await expect(preview).toBeEnabled()
  await sample.selectOption('1')
  await expect(preview).toBeDisabled()

  await spoiler.selectOption('opening')
  await intro.fill('手写简介，学徒携铃去找失主。')
  await panel.getByRole('button', { name: '载入 AI 建议到空白项' }).click()
  await expect(intro).toHaveValue('手写简介，学徒携铃去找失主。')
  await expect(spoiler).toHaveValue('opening')
  await expect(sample).toHaveValue('1')
  await preview.click()
  await expect(panel.getByRole('button', { name: '确认完结', exact: true })).toBeDisabled()
})
