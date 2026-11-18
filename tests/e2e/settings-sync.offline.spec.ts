import { expect, test, type Browser, type Page } from '@playwright/test'
import type { ConceptionState } from '../../frontend/conception'

const cloud = 'http://127.0.0.1:8888'
test.skip(process.env.AINOVEL_SYNC_E2E !== '1', '使用隔离云端进程运行双端验收')

async function reviewSettingsConflict(page: Page, browser: Browser, mergeDirectly: boolean) {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const title = mergeDirectly ? '双端合写设定 · 遗铃' : '双端设定 · 遗铃'
  const created = await page.request.post('/api/novels', {
    headers,
    data: { title, brief: '追查遗铃的两种来历。' },
  })
  expect(created.ok(), await created.text()).toBe(true)
  const { branch_id } = await created.json()
  const branch = `/api/branches/${branch_id}`
  const settings = `${branch}/conception`
  const project = await page.request.put(settings + '/project-brief', {
    headers,
    data: {
      base_revision: 0,
      idempotency_key: `${branch_id}-common-setting-project`,
      idea: '追查两座城里名称相同的遗铃。',
      brief: {
        position: '追查遗铃的两种来历',
        track: '线索式悬疑',
        genre_ids: ['xianxia'],
        audience: '喜欢推理的读者',
        distinction: '同名信物在两座城内的规则不同',
        length: '十万字三卷',
        ending: '查明遗铃由谁调换',
      },
    },
  })
  expect(project.ok(), await project.text()).toBe(true)
  const { id: projectId } = await project.json()
  const commonText = '只有东城守夜人能听见。'
  const desktopText = '桌面确认：东城午夜过后才听见。'
  const webText = '浏览器确认：东城下雨以后才听见。'
  const otherText = '只有西城铸铃师能听见。'
  const combinedText = '由作者重新核对：东城下雨且午夜之后才听得到。'

  async function desktopConfirm(detail: string, key: string) {
    const state: ConceptionState = await (await page.request.get(settings)).json()
    const world = state.categories.find((category) => category.id === 'world')!
    if (!world.items.length) {
      world.items = [
        {
          id: 'east-bell',
          title: '遗铃',
          detail,
          links: ['west-bell'],
          setting_policy: { strength: 'hard', scope: '东城', exceptions: '新月夜除外' },
        },
        {
          id: 'west-bell',
          title: '遗铃',
          detail: otherText,
          links: [],
          setting_policy: { strength: 'hard', scope: '西城', exceptions: '大雪夜除外' },
        },
      ]
    } else {
      world.items[0].detail = detail
    }
    const response = await page.request.put(settings, {
      headers,
      data: {
        base_revision: state.revision,
        base_story_revision: state.story_revision,
        categories: state.categories,
      },
    })
    expect(response.ok(), await response.text()).toBe(true)
    const saved: ConceptionState = await response.json()
    expect(saved.setting_findings.map((finding) => finding.kind)).toEqual(['same_name', 'policy_link'])
    const confirmed = await page.request.post(settings + '/confirm', {
      headers,
      data: {
        base_revision: saved.revision,
        base_story_revision: saved.story_revision,
        idempotency_key: `${branch_id}-${key}`,
        reviewed_setting_findings: saved.setting_findings.map((finding) => finding.id),
      },
    })
    expect(confirmed.ok(), await confirmed.text()).toBe(true)
    return (await confirmed.json()) as ConceptionState
  }

  async function grant(device_id: string) {
    const response = await page.request.post(`${cloud}/api/sync/works/${branch_id}/devices`, {
      headers: { Authorization: 'Bearer e2e-test-only-provisioning-token' },
      data: { device_id },
    })
    expect(response.ok(), await response.text()).toBe(true)
    return (await response.json()).token as string
  }
  const desktopToken = await grant('desktop-m3')
  const webToken = await grant('web-m3')
  async function desktopSync(action: 'enable' | 'run') {
    const response = await page.request.post(`${branch}/desktop-sync/${action}`, {
      headers,
      data: { origin: cloud, token: desktopToken },
    })
    expect(response.ok(), await response.text()).toBe(true)
    return response.json()
  }
  const common = await desktopConfirm(commonText, 'm3-confirm-common')
  await desktopSync('enable')
  const seeded = await desktopSync('run')
  expect(seeded.conflicts).toHaveLength(0)
  const pack = await (await page.request.get(branch + '/manuscript-package')).json()
  const context = await browser.newContext(mergeDirectly ? { viewport: { width: 420, height: 800 } } : {})
  try {
    const web = await context.newPage()
    await web.goto('http://127.0.0.1:8877/offline/')
    await web.getByLabel('导入新离线作品').setInputFiles({
      name: 'base.json',
      mimeType: 'application/json',
      buffer: Buffer.from(
        JSON.stringify({
          format: pack.format,
          novel: pack.novel,
          branch: pack.branch,
          chapters: [],
          revisions: [],
        }),
      ),
    })
    await web.getByRole('combobox', { name: '离线作品', exact: true }).selectOption({ label: title })
    const panel = web.getByLabel('离线设定台')
    await panel.getByText('设定台', { exact: true }).click()
    await expect(panel.getByText(/还没有已确认设定/)).toBeVisible()
    await web.getByText('作品云同步', { exact: true }).click()
    await web.getByLabel('同步服务地址').fill(cloud)
    await web.getByLabel('设备编号').fill('web-m3')
    await web.getByLabel('设备凭据').fill(webToken)
    await web.getByRole('button', { name: '启用作品同步', exact: true }).click()
    const run = web.getByRole('button', { name: '同步当前工作版本', exact: true })
    const readCursor = async () =>
      Number(
        (
          await web
            .getByRole('status')
            .filter({ hasText: /已启用 · 游标/ })
            .innerText()
        ).match(/游标 (\d+)/)?.[1],
      )
    await run.click()
    await expect(panel.getByText(`当前确认修订 ${common.confirmation!.id}`)).toBeVisible()

    await context.setOffline(true)
    await panel.getByRole('button', { name: '编辑当前设定' }).click()
    const east = panel
      .getByRole('region', { name: '遗铃', exact: true })
      .filter({ hasText: '条目编号 east-bell' })
    const detail = east.getByRole('textbox', { name: '设定内容', exact: true })
    await expect(detail).toHaveValue(commonText)
    const confirm = panel.getByRole('button', { name: '确认设定并保存到浏览器' })
    async function confirmSettingsDraft() {
      await panel.getByRole('button', { name: '体检本地设定草稿' }).click()
      await expect(confirm).toBeDisabled()
      for (const label of ['同名条目的内容或约束不同', '关联规则的适用范围或例外文字不同']) {
        const finding = panel.locator('details').filter({ has: web.getByText(label, { exact: true }) })
        await finding.getByText(label, { exact: true }).click()
        await expect(finding.getByText(/世界设定 · 第 2 项 · west-bell/)).toBeVisible()
        await finding.getByRole('checkbox', { name: '已核对此项，按当前内容保留' }).check()
      }
      await expect(confirm).toBeEnabled()
      await confirm.click()
      await expect(confirm).toHaveCount(0)
    }
    await detail.fill(webText)
    await confirmSettingsDraft()
    await expect(panel.getByText('已确认并保存新的设定修订。')).toBeVisible()

    const remote = await desktopConfirm(desktopText, 'm3-confirm-desktop')
    const desktopPushed = await desktopSync('run')
    expect(desktopPushed.conflicts).toHaveLength(0)
    await context.setOffline(false)
    await run.click()
    await web.getByRole('button', { name: /^比较冲突/ }).click()
    const compare = web.getByRole('region', { name: '云同步冲突比较' })
    for (const [label, text] of [
      ['云端当前版', desktopText],
      ['另一提交版', webText],
      ['共同依据', commonText],
    ]) {
      const side = compare.locator('details').filter({ has: web.getByText(label, { exact: true }) })
      await side.getByText(label, { exact: true }).click()
      await expect(side.locator('pre')).toContainText(text)
    }
    let mergedRevisionId = ''
    if (mergeDirectly) {
      const previousCursor = await readCursor()
      const adoptedSetting = panel.getByText(/^当前确认修订 /)
      const priorAdoption = await adoptedSetting.innerText()
      await context.setOffline(true)
      await compare.getByRole('button', { name: '打开设定合写草稿' }).press('Enter')
      await expect(panel.getByLabel('设定类别')).toBeFocused()
      const source = panel.getByRole('region', { name: '设定冲突合写依据' })
      const cloudSide = source
        .locator('details')
        .filter({ has: web.getByText('云端当前版设定', { exact: true }) })
      await cloudSide.getByText('云端当前版设定', { exact: true }).click()
      await cloudSide.getByText(/与本机底稿的差异/).click()
      await expect(cloudSide.getByText(desktopText, { exact: false })).toBeVisible()
      expect(await web.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(420)
      await cloudSide.getByRole('button', { name: '取用条目 east-bell' }).click()
      await expect(detail).toHaveValue(desktopText)
      await detail.fill(combinedText)
      await confirmSettingsDraft()
      await expect(panel.getByRole('status').filter({ hasText: '已保存合写设定' })).toBeVisible()
      await expect(adoptedSetting).not.toHaveText(priorAdoption)
      mergedRevisionId = (await adoptedSetting.innerText()).trim().split(' ').at(-1)!
      await expect(web.getByRole('button', { name: /^比较冲突/ })).toHaveCount(1)
      const untouched: ConceptionState = await (await page.request.get(settings)).json()
      expect(untouched.confirmation?.id).toBe(remote.confirmation!.id)

      await context.setOffline(false)
      await run.click()
      await expect.poll(readCursor).toBeGreaterThan(previousCursor)
      await web.getByRole('button', { name: /^比较冲突/ }).click()
      const local = compare.locator('details').filter({ has: web.getByText('本机当前版', { exact: true }) })
      await local.getByText('本机当前版', { exact: true }).click()
      await expect(local.locator('pre')).toContainText(combinedText)
      web.once('dialog', (dialog) => dialog.accept())
      await compare.getByRole('button', { name: '采用本机版', exact: true }).click()
    } else {
      web.once('dialog', (dialog) => dialog.accept())
      await compare.getByRole('button', { name: '采用另一提交版' }).click()
    }
    await expect(web.getByRole('button', { name: /^比较冲突/ })).toHaveCount(0)

    const received = await desktopSync('run')
    expect(received.conflicts).toHaveLength(0)
    expect(received.deferred).toHaveLength(0)
    const adopted: ConceptionState = await (await page.request.get(settings)).json()
    const world = adopted.categories.find((category) => category.id === 'world')!
    expect(world.items.map(({ id, detail }) => [id, detail])).toEqual([
      ['east-bell', mergeDirectly ? combinedText : webText],
      ['west-bell', otherText],
    ])
    expect(adopted.project?.id).toBe(projectId)
    expect(adopted.confirmation?.current).toBe(true)
    expect(adopted.setting_findings.every((finding) => finding.reviewed)).toBe(true)

    await panel.getByText('设定版本历史', { exact: true }).click()
    await panel.getByRole('combobox', { name: '浏览正式设定修订' }).selectOption(remote.confirmation!.id)
    const historical = panel.getByRole('region', { name: '选中设定修订' })
    await historical.getByText('世界设定', { exact: true }).click()
    await expect(historical.getByText(desktopText, { exact: true })).toBeVisible()
    await panel.getByRole('button', { name: '查看上一修订' }).click()
    await historical.getByText('世界设定', { exact: true }).click()
    await expect(historical.getByText(commonText, { exact: true })).toBeVisible()
    if (mergeDirectly) {
      const transported = await (await page.request.get(branch + '/manuscript-package')).json()
      expect(transported.settings_history.adopted_revision_id).toBe(mergedRevisionId)
      await panel.getByRole('combobox', { name: '浏览正式设定修订' }).selectOption(mergedRevisionId)
      await expect(historical.getByText(/作者确认说明 合写设定冲突/)).toContainText(remote.confirmation!.id)
      await panel.getByRole('button', { name: '查看上一修订' }).click()
      await historical.getByText('世界设定', { exact: true }).click()
      await expect(historical.getByText(webText, { exact: true })).toBeVisible()
      return
    }
    const previousCursor = await readCursor()
    await context.setOffline(true)
    await panel.getByRole('button', { name: '编辑当前设定' }).click()
    await expect(detail).toHaveValue(webText)
    await detail.fill(combinedText)
    await confirmSettingsDraft()
    await context.setOffline(false)
    await run.click()
    await expect.poll(readCursor).toBeGreaterThan(previousCursor)
    const resynced = await desktopSync('run')
    expect(resynced.conflicts).toHaveLength(0)
    expect(resynced.deferred).toHaveLength(0)
    const renewed: ConceptionState = await (await page.request.get(settings)).json()
    expect(renewed.categories.find(({ id }) => id === 'world')!.items.map(({ detail }) => detail)).toEqual([
      combinedText,
      otherText,
    ])
    expect(renewed.project?.id).toBe(projectId)
    // The competing revision remains reachable after confirming and sending the combined decision.
    await panel.getByRole('combobox', { name: '浏览正式设定修订' }).selectOption(remote.confirmation!.id)
    await historical.getByText('世界设定', { exact: true }).click()
    await expect(historical.getByText(desktopText, { exact: true })).toBeVisible()
  } finally {
    await context.close()
  }
}

test('桌面和独立 Web 同时确认设定后比较共同依据并保留双方历史', async ({ page, browser }) => {
  await reviewSettingsConflict(page, browser, false)
})

test('独立 Web 可断网合写正式设定，再用新修订解决云端分歧', async ({ page, browser }) => {
  await reviewSettingsConflict(page, browser, true)
})
