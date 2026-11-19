import { expect, test } from '@playwright/test'
import { readFile } from 'node:fs/promises'

const cloud = 'http://127.0.0.1:8888'
test.skip(process.env.AINOVEL_SYNC_E2E !== '1', '使用隔离云端进程运行双端验收')

test('桌面和离线 Web 比较正式大纲与伏笔分歧，保留原文和实际进度', async ({ page, browser }) => {
  await page.goto('/')
  const desktopOrigin = new URL(page.url()).origin
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const title = '两地大纲 · 潮锁'
  const created = await page.request.post('/api/novels', { headers, data: { title } })
  expect(created.ok(), await created.text()).toBe(true)
  const { branch_id } = await created.json()
  const branch = `/api/branches/${branch_id}`
  const made = await page.request.post(branch + '/chapters', { headers, data: { title: '渡口查证' } })
  expect(made.ok(), await made.text()).toBe(true)
  const { id: chapterId } = await made.json()
  const content = '锁背露出双笔铸记。'
  const manuscript = await page.request.put(`/api/chapters/${chapterId}`, {
    headers,
    data: { base_revision: 0, content },
  })
  expect(manuscript.ok(), await manuscript.text()).toBe(true)
  const source = {
    chapter_id: chapterId,
    revision: (await manuscript.json()).revision,
    start: 0,
    end: content.length,
    quote: content,
  }
  const settingsPath = branch + '/conception'
  const project = await page.request.put(settingsPath + '/project-brief', {
    headers,
    data: {
      base_revision: 0,
      idempotency_key: 'm4-common-project',
      idea: '沿着铸记追查潮锁的来历。',
      brief: {
        position: '追查潮锁来历',
        track: '线索式悬疑',
        genre_ids: ['xianxia'],
        audience: '喜欢推理的读者',
        distinction: '以铸记而非目击辨认锁',
        length: '十万字三卷',
        ending: '找到打造潮锁的工匠',
      },
    },
  })
  expect(project.ok(), await project.text()).toBe(true)
  const settings = await (await page.request.get(settingsPath)).json()
  settings.categories.find(({ id }: { id: string }) => id === 'world').items = [
    { id: 'mark', title: '铸记规则', detail: '潮锁以两道铸记区分产地。', links: [] },
  ]
  const inventory = '库房只余两盏旧灯。'
  settings.categories.push({
    id: 'resources',
    title: '资源台账',
    items: [{ id: 'lamps', title: '油灯库存', detail: inventory }],
  })
  const savedSettings = await page.request.put(settingsPath, {
    headers,
    data: {
      categories: settings.categories,
      base_revision: settings.revision,
      base_story_revision: settings.story_revision,
    },
  })
  expect(savedSettings.ok(), await savedSettings.text()).toBe(true)
  const revision = await savedSettings.json()
  const confirmedSettings = await page.request.post(settingsPath + '/confirm', {
    headers,
    data: {
      base_revision: revision.revision,
      base_story_revision: revision.story_revision,
      idempotency_key: 'm4-common-settings',
    },
  })
  expect(confirmedSettings.ok(), await confirmedSettings.text()).toBe(true)
  const settingsId = (await confirmedSettings.json()).confirmation.id
  const window = {
    plant_chapter_id: chapterId,
    opens_chapter_id: chapterId,
    closes_chapter_id: chapterId,
  }
  const commonText = '辨认潮锁上的双笔铸记。'
  const desktopText = '先查镇中的铸锁牌，再问旧匠。'
  const webText = '先找渡船上的旧锁，再查作坊名录。'
  const outlinePath = branch + '/outline-workspace'
  const original = '😀临河的守夜人掀开灯罩，只指了记号上的第一笔。'
  const imported = await page.request.post('/api/library/import', {
    headers,
    multipart: { file: { name: '结构参考.txt', mimeType: 'text/plain', buffer: Buffer.from(original) } },
  })
  expect(imported.ok(), await imported.text()).toBe(true)
  const library = await imported.json()
  const [sourceUnit] = await (await page.request.get('/api/library/books/' + library.id + '/units')).json()
  const goal = '分析分两步提供证据的手法'
  const newReference = await page.request.post('/api/reference-sessions', {
    headers,
    data: { unit_id: sourceUnit.id, start: 0, end: original.length, quote: original, goal, branch_id },
  })
  expect(newReference.ok(), await newReference.text()).toBe(true)
  const session = await newReference.json()
  const technique = '先给可核对的一半证据'
  const edited = await page.request.patch('/api/reference-sessions/' + session.id, {
    headers,
    data: {
      base_revision: 0,
      branch_id,
      goal,
      cards: [
        {
          id: 'half-evidence',
          observation: '先递出一半',
          interpretation: '留下下一次核对动作',
          technique,
          conditions: '人物能继续查证',
          exceptions: '替换原人物、动作和道具',
          evidence: '只指了记号上的第一笔',
        },
      ],
    },
  })
  expect(edited.ok(), await edited.text()).toBe(true)
  const [ready] = await (await page.request.get(outlinePath + '/reference-options')).json()
  expect(ready.version_id).toMatch(/^[0-9a-f]{64}$/)
  async function confirmOutline(base: number, plan: object) {
    const save = await page.request.put(outlinePath, {
      headers,
      data: { base_revision: base, confirmation_id: settingsId, plan },
    })
    expect(save.ok(), await save.text()).toBe(true)
    const confirmed = await page.request.post(outlinePath + '/confirm', {
      headers,
      data: { base_revision: (await save.json()).draft.revision },
    })
    expect(confirmed.ok(), await confirmed.text()).toBe(true)
    return confirmed.json()
  }
  const common = await confirmOutline(0, {
    summary: commonText,
    volumes: [
      {
        id: 'first-volume',
        title: '盐镇',
        summary: '追查双笔铸记',
        units: [
          {
            id: 'first-unit',
            title: '追索',
            summary: '从锁背辨认产地',
            chapters: [
              {
                chapter_id: chapterId,
                goal: '发现铸记',
                obstacle: '光线弱',
                turn: '发现双笔',
                result: '锁定产地',
                setting_ids: ['mark', 'lamps'],
              },
            ],
          },
        ],
      },
    ],
    new_promises: [{ title: '双笔铸记', notes: '辨认每笔指向的作坊。', ...window }],
  })
  const [{ promise_id: promiseId }] = common.confirmed.plan.promises
  const graphPath = branch + '/story-graph'
  const graph = await (await page.request.get(graphPath)).json()
  const promise = graph.records.find(({ id }: { id: string }) => id === promiseId)
  const planted = await page.request.put(branch + '/story-records/' + promiseId, {
    headers,
    data: {
      base_story_revision: graph.story_revision,
      base_revision: promise.revision,
      record: {
        ...promise.payload,
        stages: [{ status: 'planned' }, { status: 'planted', sources: [source] }],
      },
    },
  })
  expect(planted.ok(), await planted.text()).toBe(true)
  async function grant(device_id: string) {
    const response = await page.request.post(`${cloud}/api/sync/works/${branch_id}/devices`, {
      headers: { Authorization: 'Bearer e2e-test-only-provisioning-token' },
      data: { device_id },
    })
    expect(response.ok(), await response.text()).toBe(true)
    return (await response.json()).token as string
  }
  const desktopToken = await grant('desktop-m4')
  const webToken = await grant('web-m4')
  async function desktopSync(action: 'enable' | 'run') {
    const response = await page.request.post(`${branch}/desktop-sync/${action}`, {
      headers,
      data: { origin: cloud, token: desktopToken },
    })
    expect(response.ok(), await response.text()).toBe(true)
    return response.json()
  }
  await desktopSync('enable')
  expect((await desktopSync('run')).conflicts).toHaveLength(0)
  const pack = await (await page.request.get(branch + '/manuscript-package')).json()
  const context = await browser.newContext()
  try {
    const web = await context.newPage()
    await web.goto(new URL('/offline/', desktopOrigin).href)
    await web.getByLabel('导入新离线作品').setInputFiles({
      name: 'm4-base.json',
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
    await web.getByText('作品云同步', { exact: true }).click()
    await web.getByLabel('同步服务地址').fill(cloud)
    await web.getByLabel('设备编号').fill('web-m4')
    await web.getByLabel('设备凭据').fill(webToken)
    await web.getByRole('button', { name: '启用作品同步', exact: true }).click()
    const sync = web.getByRole('button', { name: '同步当前工作版本', exact: true })
    await sync.click()
    const panel = web.getByLabel('离线大纲台')
    await panel.getByText('大纲台', { exact: true }).click()
    await expect(panel.getByText(`已确认修订 ${common.confirmed.id}`, { exact: true })).toBeVisible()
    await context.setOffline(true)
    await panel.getByRole('button', { name: '编辑当前大纲' }).click()
    await panel.getByRole('textbox', { name: '总纲', exact: true }).fill(webText)
    await panel.getByText('引用设定 · 2 项', { exact: true }).click()
    const resources = panel.getByRole('checkbox', { name: '油灯库存 · 资源台账', exact: true })
    await expect(resources).toBeChecked()
    await resources.uncheck()
    await resources.check()
    await panel.getByText('手法卡摘要 · 0 项', { exact: true }).click()
    await panel.getByRole('checkbox', { name: technique + ' · 修订 1', exact: true }).check()
    await expect(panel.getByRole('combobox', { name: '最迟回收', exact: true })).toHaveValue(chapterId)
    await panel.getByRole('button', { name: '确认大纲并保存到浏览器' }).click()
    await expect(panel.getByText('已确认并保存大纲修订。')).toBeVisible()

    const remote = await confirmOutline(common.confirmed.revision, {
      ...common.confirmed.plan,
      summary: desktopText,
    })
    expect((await desktopSync('run')).conflicts).toHaveLength(0)
    await context.setOffline(false)
    await sync.click()
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
    const result = await desktopSync('run')
    expect(result.conflicts).toHaveLength(0)
    expect(result.deferred).toHaveLength(0)
    const adopted = await (await page.request.get(outlinePath)).json()
    expect(adopted.confirmed.plan.summary).toBe(webText)
    expect(adopted.confirmed.setting_basis).toMatchObject({
      confirmation_id: settingsId,
      items: expect.arrayContaining([
        {
          id: 'lamps',
          title: '油灯库存',
          detail: inventory,
          category_id: 'resources',
          category_title: '资源台账',
        },
      ]),
    })
    expect(adopted.confirmed.plan.volumes[0].units[0].chapters[0].setting_ids).toEqual(['mark', 'lamps'])
    expect(adopted.confirmed.setting_basis.reference_cards).toEqual([ready])
    expect(adopted.confirmed.plan.volumes[0].units[0].chapters[0].reference_cards).toEqual([
      {
        session_id: ready.session_id,
        card_id: ready.card_id,
        revision: ready.revision,
        version_id: ready.version_id,
      },
    ])
    const exported = await page.request.get(branch + '/manuscript-package')
    const exportedText = await exported.text()
    expect(exportedText).not.toContain(original)
    expect(exportedText).not.toContain('只指了记号上的第一笔')
    expect(JSON.parse(exportedText).reference_history.excerpts).toBeUndefined()
    expect(adopted.confirmed.plan.promises).toEqual([{ promise_id: promiseId, ...window }])
    expect(adopted.promises[0].state).toBe('planted')
    expect(adopted.promises[0].stages[1].sources).toEqual([source])
    await panel.getByText('大纲版本历史', { exact: true }).click()
    await panel.getByRole('combobox', { name: '浏览正式大纲修订' }).selectOption(remote.confirmed.id)
    await expect(panel.getByLabel('所选大纲修订', { exact: true })).toContainText(desktopText)
    await panel.getByRole('button', { name: '查看上一大纲修订' }).click()
    await expect(panel.getByLabel('所选大纲修订', { exact: true })).toContainText(commonText)
    await expect(panel.getByLabel('所选大纲修订', { exact: true })).toContainText('章纲引用设定 mark、lamps')

    const webProgress = '浏览器核对双笔铸记后回收。'
    const desktopProgress = '桌面补记保管人的证词。'
    const story = web.getByLabel('离线故事记录')
    await story.getByText('故事记录', { exact: true }).click()
    await story.getByRole('combobox', { name: '查看故事记录', exact: true }).selectOption(promiseId)
    await context.setOffline(true)
    await story.getByRole('button', { name: '编辑所选故事记录', exact: true }).click()
    await story.getByRole('button', { name: '添加伏笔阶段', exact: true }).click()
    const resolved = story.getByRole('group', { name: '阶段 3', exact: true })
    await resolved.getByRole('combobox', { name: '阶段状态', exact: true }).selectOption('resolved')
    await resolved.getByRole('textbox', { name: '阶段说明', exact: true }).fill(webProgress)
    const confirm = story.getByRole('button', { name: '确认故事记录并保存到浏览器', exact: true })
    await confirm.click()
    await expect(story.getByRole('alert')).toContainText('缺少原文证据')
    await resolved.getByText('阶段原文证据 · 0 处', { exact: true }).click()
    await resolved.getByRole('combobox', { name: '原文证据章节', exact: true }).selectOption(chapterId)
    await resolved.getByRole('button', { name: '读取证据章保存稿', exact: true }).click()
    await expect(resolved.getByRole('textbox', { name: '可选取的保存稿', exact: true })).toHaveValue(content)
    await resolved.getByRole('spinbutton', { name: '选区起点', exact: true }).fill('0')
    await resolved.getByRole('spinbutton', { name: '选区终点', exact: true }).fill(String(content.length))
    await resolved.getByRole('button', { name: '附上所选原文', exact: true }).click()
    await expect(resolved.getByText('阶段原文证据 · 1 处', { exact: true })).toBeVisible()
    await confirm.click()
    await expect(story.getByText('已确认并保存故事记录。')).toBeVisible()

    const latestGraph = await (await page.request.get(graphPath)).json()
    const currentPromise = latestGraph.records.find(({ id }: { id: string }) => id === promiseId)
    const alternative = await page.request.put(branch + '/story-records/' + promiseId, {
      headers,
      data: {
        base_story_revision: latestGraph.story_revision,
        base_revision: currentPromise.revision,
        record: { ...currentPromise.payload, notes: desktopProgress },
      },
    })
    expect(alternative.ok(), await alternative.text()).toBe(true)
    expect((await desktopSync('run')).conflicts).toHaveLength(0)
    await context.setOffline(false)
    await sync.click()
    await web.getByRole('button', { name: /^比较冲突/ }).click()
    for (const [label, text] of [
      ['云端当前版', desktopProgress],
      ['另一提交版', webProgress],
      ['共同依据', '已埋设'],
    ]) {
      const side = compare.locator('details').filter({ has: web.getByText(label, { exact: true }) })
      await side.getByText(label, { exact: true }).click()
      await expect(side.locator('pre')).toContainText(text)
    }
    web.once('dialog', (dialog) => dialog.accept())
    await compare.getByRole('button', { name: '采用另一提交版' }).click()
    await expect(web.getByRole('button', { name: /^比较冲突/ })).toHaveCount(0)
    expect((await desktopSync('run')).deferred).toHaveLength(0)
    const progressed = await (await page.request.get(outlinePath)).json()
    expect(progressed.promises[0].state).toBe('resolved')
    expect(progressed.promises[0].stages[2].sources).toEqual([source])
  } finally {
    await context.close()
  }

  // The desktop package carries a dependency on the progressed story record, not a substitute.
  expect(pack.outline_history.required_story_records).toEqual([promiseId])
  expect(pack.story_history.adopted_heads[promiseId]).toBeTruthy()
  // A partial exchange fixture cannot include a checkpoint sealed against the omitted story history.
  const { story_history: _absent, snapshot_history: _checkpoints, ...missingStoryHistory } = pack
  for (const [file, expectedAdopted] of [
    [pack, true],
    [missingStoryHistory, false],
  ] as const) {
    const archiveContext = await browser.newContext()
    try {
      const archive = await archiveContext.newPage()
      await archive.goto(new URL('/offline/', desktopOrigin).href)
      await expect(archive.getByText('离线页面就绪', { exact: true })).toBeVisible()
      await expect.poll(() => archive.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
      await archiveContext.setOffline(true)
      await archive.getByLabel('导入新离线作品').setInputFiles({
        name: '已确认大纲与故事.json',
        mimeType: 'application/json',
        buffer: Buffer.from(JSON.stringify(file)),
      })
      await expect(archive.getByRole('status').filter({ hasText: '已校验并导入稿件及历史。' })).toBeVisible()
      await archive.getByRole('combobox', { name: '离线作品', exact: true }).selectOption({ label: title })
      const pending = archive.getByLabel('离线大纲台')
      await pending.getByText('大纲台', { exact: true }).click()
      if (expectedAdopted) {
        await expect(pending.getByText(`已确认修订 ${common.confirmed.id}`, { exact: true })).toBeVisible()
        const story = archive.getByLabel('离线故事记录')
        await story.getByText('故事记录', { exact: true }).click()
        await story.getByRole('combobox', { name: '查看故事记录' }).selectOption(promiseId)
        await story.getByText('故事记录历史', { exact: true }).click()
        await expect(story.locator('pre')).toContainText('已埋设')
      } else {
        await expect(
          pending.getByText(
            `已保留稿件包的大纲修订 ${common.confirmed.id}。等待原伏笔记录 ${promiseId} 的实际进展或历史恢复。`,
          ),
        ).toBeVisible()
        await expect(pending.getByText(/^已确认修订 /)).toHaveCount(0)
      }
      await pending.getByText('大纲版本历史', { exact: true }).click()
      await expect(pending.getByLabel('所选大纲修订', { exact: true })).toContainText(commonText)
      await expect(pending.getByLabel('所选大纲修订', { exact: true })).toContainText(
        '章纲引用设定 mark、lamps',
      )
      const download = archive.waitForEvent('download')
      await archive.getByRole('button', { name: '导出稿件交换包', exact: true }).click()
      const transported = JSON.parse(await readFile((await (await download).path())!, 'utf-8'))
      expect(transported.outline_history).toEqual(pack.outline_history)
      expect(transported.settings_history).toEqual(pack.settings_history)
      expect(transported.story_history).toEqual(file.story_history)
      expect(transported.reference_history).toEqual(pack.reference_history)
    } finally {
      await archiveContext.close()
    }
  }
})
