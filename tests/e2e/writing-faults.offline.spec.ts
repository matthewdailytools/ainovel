import { test, expect } from '@playwright/test'

async function setup(page: import('@playwright/test').Page, title: string, content = '') {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const novel = await (await page.request.post('/api/novels', { headers, data: { title } })).json()
  const chapter = await (
    await page.request.post(`/api/branches/${novel.branch_id}/chapters`, {
      headers,
      data: { title: '工程测试章' },
    })
  ).json()
  if (content)
    await page.request.put(`/api/chapters/${chapter.id}`, { headers, data: { base_revision: 0, content } })
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await expect(page.getByRole('textbox', { name: '章节正文', exact: true })).toBeVisible()
  return { novel, chapter, headers }
}

function barrier() {
  let release = () => {}
  const promise = new Promise<void>((resolve) => {
    release = resolve
  })
  return { promise, release }
}

for (const target of ['chapter', 'novel'] as const) {
  test(`生成中切${target === 'chapter' ? '章' : '作品'}：回执或完成候选晚返回不覆盖新目标`, async ({
    page,
  }) => {
    const original = '甲章原稿😀𠮷。\n这段只属于原目标。'
    const otherOriginal = '乙章原稿。\n这段只属于新目标。'
    const otherEdited = '乙章新写的正文😀𠮷。\n切换后仍能继续亲自写作。'
    const { novel, chapter, headers } = await setup(page, `后台验证 · 生成中切${target}`, original)
    const otherNovel =
      target === 'novel'
        ? await (
            await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 新目标作品' } })
          ).json()
        : novel
    const otherChapter = await (
      await page.request.post(`/api/branches/${otherNovel.branch_id}/chapters`, {
        headers,
        data: { title: '乙章独立目标' },
      })
    ).json()
    expect(
      (
        await page.request.put(`/api/chapters/${otherChapter.id}`, {
          headers,
          data: { base_revision: 0, content: otherOriginal },
        })
      ).ok(),
    ).toBeTruthy()
    await page.reload()
    await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
    const editor = page.getByRole('textbox', { name: '章节正文', exact: true })
    await expect(editor.locator('.cm-line')).toHaveText(original.split('\n'))
    await editor.focus()
    await editor.press('ControlOrMeta+Home')
    await editor.press('Shift+End')
    await page
      .getByRole('combobox', { name: '这次要做什么', exact: true })
      .selectOption('writing.rewrite_selection')
    await page
      .getByRole('textbox', { name: '情节或具体要求', exact: true })
      .fill('后台故障演练：慢速可取消。仅改甲章选句。')
    const entered = barrier()
    const release = barrier()
    let taskId = ''
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    // Hold the actual POST receipt to exercise an unmounted workbench. A
    // same-workbench chapter change waits for submission to finish, then runs
    // while the Worker is still generating its delayed candidate.
    await page.route('**/api/modules', async (route) => {
      if (route.request().method() !== 'POST') return route.continue()
      const response = await route.fetch()
      expect(response.ok()).toBeTruthy()
      taskId = (await response.json()).id
      entered.release()
      await release.promise
      await route.fulfill({ response })
    })
    try {
      const lateResponse = page.waitForResponse(
        (response) => response.url().endsWith('/api/modules') && response.request().method() === 'POST',
      )
      await page.getByRole('button', { name: '生成候选 →', exact: true }).click()
      await entered.promise
      await expect
        .poll(async () => (await (await page.request.get(`/api/tasks/${taskId}`)).json()).status)
        .toBe('running')
      if (target === 'chapter') {
        release.release()
        await (await lateResponse).finished()
        await expect(page.getByRole('button', { name: '保存', exact: true })).toBeEnabled()
      }
      let confirmation = ''
      page.once('dialog', async (dialog) => {
        confirmation = dialog.message()
        await dialog.accept()
      })
      if (target === 'novel')
        await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(otherNovel.id)
      else await page.locator('.chapter-list button').filter({ hasText: '乙章独立目标' }).click()
      if (target === 'novel') expect(confirmation).toContain('未提交的写作辅助设置')
      else expect(confirmation).toBe('')
      await expect(page.getByRole('textbox', { name: '章节标题', exact: true })).toHaveValue('乙章独立目标')
      await expect(editor.locator('.cm-line')).toHaveText(otherOriginal.split('\n'))
      await editor.fill(otherEdited)
      release.release()
      await (await lateResponse).finished()
      await page.getByRole('button', { name: '保存', exact: true }).click()
      await expect
        .poll(async () => (await (await page.request.get(`/api/chapters/${otherChapter.id}`)).json()).content)
        .toBe(otherEdited)
      await expect
        .poll(async () => (await (await page.request.get(`/api/tasks/${taskId}`)).json()).status, {
          timeout: 30_000,
        })
        .toBe(target === 'chapter' ? 'blocked_runtime' : 'succeeded')
      await expect
        .poll(
          async () =>
            (await (await page.request.get(`/api/artifacts?branch_id=${novel.branch_id}`)).json()).length,
        )
        .toBe(target === 'chapter' ? 0 : 1)
      const visiblePoll = await page.waitForResponse(
        (response) =>
          response.url().endsWith(`/api/artifacts?branch_id=${otherNovel.branch_id}`) &&
          response.request().method() === 'GET',
      )
      expect(await visiblePoll.json()).toEqual([])
      await expect(page.getByRole('textbox', { name: '章节标题', exact: true })).toHaveValue('乙章独立目标')
      await expect(editor.locator('.cm-line')).toHaveText(otherEdited.split('\n'))
      await expect(page.getByRole('button', { name: '采纳到正文', exact: true })).toHaveCount(0)
      // Refresh both task/artifact polling paths once the old result exists.
      // The current target must still render its own saved manuscript.
      await page.reload()
      await expect(page.getByRole('combobox', { name: '作品', exact: true })).toHaveValue(otherNovel.id)
      await expect(page.getByRole('textbox', { name: '章节标题', exact: true })).toHaveValue('乙章独立目标')
      await expect(editor.locator('.cm-line')).toHaveText(otherEdited.split('\n'))
      await expect(page.getByRole('button', { name: '采纳到正文', exact: true })).toHaveCount(0)
      expect((await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content).toBe(original)
      const artifacts = await (await page.request.get(`/api/artifacts?branch_id=${novel.branch_id}`)).json()
      if (target === 'novel') {
        expect(artifacts[0]).toMatchObject({
          task_id: taskId,
          chapter_id: chapter.id,
          base_revision: 1,
          status: 'candidate',
        })
        expect(await (await page.request.get(`/api/tasks?branch_id=${otherNovel.branch_id}`)).json()).toEqual(
          [],
        )
        expect(
          await (await page.request.get(`/api/artifacts?branch_id=${otherNovel.branch_id}`)).json(),
        ).toEqual([])
        await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
      } else {
        const stopped = await (await page.request.get(`/api/tasks/${taskId}`)).json()
        expect(stopped.error).toBe('生成期间故事版本改变，旧步骤结果保留但不作为当前候选。')
        const transcript = await (await page.request.get(`/api/tasks/${taskId}/transcript`)).json()
        expect(transcript.steps).toHaveLength(1)
        expect(transcript.steps[0].result.text).toBe('她先核对刻度，再记录读数。')
        expect(artifacts).toEqual([])
      }
      await page.locator('.chapter-list button').filter({ hasText: '工程测试章' }).click()
      await expect(editor.locator('.cm-line')).toHaveText(original.split('\n'))
      if (target === 'novel') {
        await expect(page.getByRole('button', { name: '采纳到正文', exact: true })).toBeEnabled()
        await page.getByRole('button', { name: '拒绝', exact: true }).click()
        await expect
          .poll(
            async () => (await (await page.request.get(`/api/artifacts/${artifacts[0].id}`)).json()).status,
          )
          .toBe('rejected')
      } else await expect(page.getByRole('button', { name: '采纳到正文', exact: true })).toHaveCount(0)
      expect((await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).revision).toBe(1)
      expect((await (await page.request.get(`/api/chapters/${otherChapter.id}`)).json()).content).toBe(
        otherEdited,
      )
      const tasks = await (await page.request.get(`/api/tasks?branch_id=${novel.branch_id}`)).json()
      expect(tasks).toHaveLength(1)
      expect(tasks[0].id).toBe(taskId)
      expect(errors).toEqual([])
    } finally {
      release.release()
      if (taskId) {
        const state = await (await page.request.get(`/api/tasks/${taskId}`)).json()
        if (['queued', 'running'].includes(state.status))
          await page.request.post(`/api/tasks/${taskId}/cancel`, { headers })
      }
    }
  })
}

test('中文组合输入：浏览器IME组合与Enter不触发生成、不插入额外换行，提交后逐字保存', async ({ page }) => {
  const { novel, chapter } = await setup(page, '后台验证 · 中文IME组合事件')
  const editor = page.getByRole('textbox', { name: '章节正文', exact: true })
  await editor.focus()
  const events: { type: string; composing?: boolean; trusted: boolean }[] = []
  await page.exposeFunction('recordCompositionEvent', (value: (typeof events)[number]) => events.push(value))
  await editor.evaluate((element) => {
    for (const type of ['compositionstart', 'compositionupdate', 'compositionend', 'keydown']) {
      element.addEventListener(type, (event) => {
        const detail = {
          type: event.type,
          composing: 'isComposing' in event ? Boolean(event.isComposing) : undefined,
          trusted: event.isTrusted,
        }
        void (
          window as unknown as { recordCompositionEvent(value: typeof detail): Promise<void> }
        ).recordCompositionEvent(detail)
      })
    }
  })
  const protocol = await page.context().newCDPSession(page)
  await protocol.send('Input.imeSetComposition', { text: '渡船', selectionStart: 2, selectionEnd: 2 })
  await protocol.send('Input.dispatchKeyEvent', {
    type: 'keyDown',
    key: 'Enter',
    code: 'Enter',
    windowsVirtualKeyCode: 229,
    nativeVirtualKeyCode: 229,
  })
  await protocol.send('Input.dispatchKeyEvent', {
    type: 'keyUp',
    key: 'Enter',
    code: 'Enter',
    windowsVirtualKeyCode: 229,
    nativeVirtualKeyCode: 229,
  })
  await protocol.send('Input.insertText', { text: '渡船😀𠮷' })
  await expect(editor.locator('.cm-line')).toHaveText(['渡船😀𠮷'])
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect
    .poll(async () => (await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content)
    .toBe('渡船😀𠮷')
  await expect.poll(() => events.some((event) => event.type === 'compositionend')).toBe(true)
  await test.info().attach('browser-ime-events', {
    body: JSON.stringify(events),
    contentType: 'application/json',
  })
  // CDP starts a browser IME composition rather than fabricating DOM events.
  // Its insertText completion can end with an untrusted compositionend, which
  // stays in the evidence; this is not an OS input-method acceptance result.
  expect(events.some((event) => event.type === 'compositionstart' && event.trusted)).toBeTruthy()
  expect(events.some((event) => event.type === 'compositionupdate' && event.trusted)).toBeTruthy()
  expect(events.some((event) => event.type === 'keydown' && event.composing && event.trusted)).toBeTruthy()
  const tasks = await (await page.request.get('/api/tasks')).json()
  expect(tasks.filter((task: { novel_id: string }) => task.novel_id === novel.id)).toEqual([])
  await protocol.detach()
})

test('后台任务：刷新重连同一任务、取消保留片段、明确恢复后候选不覆盖原稿', async ({ page }) => {
  const original = '她心里很着急。\n第二段保留😀𠮷。'
  const { novel, chapter, headers } = await setup(page, '后台验证 · 取消重连恢复', original)
  const editor = page.getByRole('textbox', { name: '章节正文', exact: true })
  await editor.focus()
  await editor.press('ControlOrMeta+Home')
  await editor.press('Shift+End')
  await page
    .getByRole('combobox', { name: '这次要做什么', exact: true })
    .selectOption('writing.rewrite_selection')
  await page
    .getByRole('textbox', { name: '情节或具体要求', exact: true })
    .fill('后台故障演练：慢速可取消。只改当前选句，其他正文保持。')
  const response = page.waitForResponse(
    (value) => value.url().endsWith('/api/modules') && value.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '生成候选 →', exact: true }).click()
  const task = await (await response).json()
  await page.getByText('查看流式草稿（尚未采纳）', { exact: true }).click()
  await expect(page.locator('pre.stream')).toContainText('等待核查的未采纳片段。')
  await page.reload()
  await page.getByText('查看流式草稿（尚未采纳）', { exact: true }).click()
  await expect(page.locator('pre.stream')).toContainText('等待核查的未采纳片段。')
  const reconnected = await (await page.request.get('/api/tasks')).json()
  expect(reconnected.filter((row: { novel_id: string }) => row.novel_id === novel.id)).toHaveLength(1)
  await page.getByRole('button', { name: '取消生成', exact: true }).click()
  await expect
    .poll(async () => (await (await page.request.get(`/api/tasks/${task.id}`)).json()).status)
    .toBe('cancelled')
  expect((await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content).toBe(original)
  const artifacts = await (await page.request.get(`/api/artifacts?branch_id=${novel.branch_id}`)).json()
  expect(artifacts).toEqual([])
  await page.getByText('任务历史与失败产物', { exact: true }).click()
  await page.getByRole('button', { name: '查看保存的步骤与中断输出', exact: true }).click()
  await expect(page.locator('.candidates pre.stream')).toContainText('中断输出：')
  const refused = await page.request.post(`/api/tasks/${task.id}/resume`, {
    headers,
    data: { acknowledge_unknown: false },
  })
  expect(refused.status()).toBe(409)
  const resumed = await page.request.post(`/api/tasks/${task.id}/resume`, {
    headers,
    data: { acknowledge_unknown: true },
  })
  expect(resumed.ok(), await resumed.text()).toBeTruthy()
  await expect
    .poll(async () => (await (await page.request.get(`/api/tasks/${task.id}`)).json()).status, {
      timeout: 25_000,
    })
    .toBe('succeeded')
  const all = await (await page.request.get('/api/tasks')).json()
  expect(all.filter((row: { novel_id: string }) => row.novel_id === novel.id)).toHaveLength(1)
  expect((await (await page.request.get(`/api/chapters/${chapter.id}`)).json()).content).toBe(original)
  const finalArtifacts = await (await page.request.get(`/api/artifacts?branch_id=${novel.branch_id}`)).json()
  expect(finalArtifacts).toHaveLength(1)
  expect(finalArtifacts[0].status).toBe('candidate')
  expect(finalArtifacts[0].task_id).toBe(task.id)
  await expect(page.getByRole('button', { name: '采纳到正文', exact: true })).toBeVisible()
})
