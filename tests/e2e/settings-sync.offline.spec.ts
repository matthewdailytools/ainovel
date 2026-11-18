import { expect, test } from '@playwright/test'
import type { ConceptionState } from '../../frontend/conception'

const cloud = 'http://127.0.0.1:8888'
test.skip(process.env.AINOVEL_SYNC_E2E !== '1', '使用隔离云端进程运行双端验收')

test('桌面和独立 Web 同时确认设定后比较共同依据并保留双方历史', async ({ page, browser }) => {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const title = '双端设定 · 遗铃'
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
      idempotency_key: 'common-setting-project',
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
        idempotency_key: key,
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
  const context = await browser.newContext()
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
    await run.click()
    await expect(panel.getByText(`当前确认修订 ${common.confirmation!.id}`)).toBeVisible()

    await context.setOffline(true)
    await panel.getByRole('button', { name: '编辑当前设定' }).click()
    const east = panel
      .getByRole('region', { name: '遗铃', exact: true })
      .filter({ hasText: '条目编号 east-bell' })
    const detail = east.getByRole('textbox', { name: '设定内容', exact: true })
    await expect(detail).toHaveValue(commonText)
    await detail.fill(webText)
    await panel.getByRole('button', { name: '体检本地设定草稿' }).click()
    const confirm = panel.getByRole('button', { name: '确认设定并保存到浏览器' })
    await expect(confirm).toBeDisabled()
    for (const label of ['同名条目的内容或约束不同', '关联规则的适用范围或例外文字不同']) {
      const finding = panel.locator('details').filter({ has: web.getByText(label, { exact: true }) })
      await finding.getByText(label, { exact: true }).click()
      await expect(finding.getByText(/世界设定 · 第 2 项 · west-bell/)).toBeVisible()
      await finding.getByRole('checkbox', { name: '已核对此项，按当前内容保留' }).check()
    }
    await expect(confirm).toBeEnabled()
    await confirm.click()
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
    web.once('dialog', (dialog) => dialog.accept())
    await compare.getByRole('button', { name: '采用另一提交版' }).click()
    await expect(web.getByRole('button', { name: /^比较冲突/ })).toHaveCount(0)

    const received = await desktopSync('run')
    expect(received.conflicts).toHaveLength(0)
    expect(received.deferred).toHaveLength(0)
    const adopted: ConceptionState = await (await page.request.get(settings)).json()
    const world = adopted.categories.find((category) => category.id === 'world')!
    expect(world.items.map(({ id, detail }) => [id, detail])).toEqual([
      ['east-bell', webText],
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
  } finally {
    await context.close()
  }
})
