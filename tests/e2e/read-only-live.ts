import { test as base, expect } from '@playwright/test'
import { writeFile } from 'node:fs/promises'

const writeMethods = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

type ReadOnlyStory = {
  novelId: string
  branchId: string
  chapterId: string
  content: string
  revision: number
}

export const test = base.extend<{ readOnlyStory: ReadOnlyStory; readOnlyApiGuard: void }>({
  readOnlyStory: async ({ request, context }, use, testInfo) => {
    const session = await (await request.get('/api/session')).json()
    const headers = { 'X-Ainovel-CSRF': session.csrf }
    const created = await request.post('/api/novels', {
      headers,
      data: { title: `后台验证 · 只读工作台 · ${testInfo.testId} · ${Date.now()}` },
    })
    expect(created.ok(), await created.text()).toBeTruthy()
    const novel = await created.json()
    const chapterResponse = await request.post(`/api/branches/${novel.branch_id}/chapters`, {
      headers,
      data: { title: '仅供只读验证的独立章节' },
    })
    expect(chapterResponse.ok(), await chapterResponse.text()).toBeTruthy()
    const chapter = await chapterResponse.json()
    const content = '只读验证样本：她记下渡船到岸的时刻，再核对手中的旧记录。😀𠮷'
    const saved = await request.put(`/api/chapters/${chapter.id}`, {
      headers,
      data: { base_revision: chapter.revision, content },
    })
    expect(saved.ok(), await saved.text()).toBeTruthy()
    const baseline = await saved.json()
    const story: ReadOnlyStory = {
      novelId: novel.id,
      branchId: novel.branch_id,
      chapterId: chapter.id,
      content,
      revision: baseline.revision,
    }
    await context.addInitScript(({ novelId, branchId }) => {
      localStorage.setItem('ainovel-novel', novelId)
      localStorage.setItem(`ainovel-branch-${novelId}`, branchId)
    }, story)
    await use(story)
  },
  readOnlyApiGuard: [
    async ({ context, page, request, readOnlyStory }, use, testInfo) => {
      const writes: { method: string; path: string }[] = []
      const positionWrites: { method: string; path: string }[] = []
      // Only the fixture's normal reading-position maintenance may reach the service.
      // Every other attempted write remains a test failure and is blocked before dispatch.
      await context.route(/\/api(?:\/|\?|$)/, async (route) => {
        const browserRequest = route.request()
        if (!writeMethods.has(browserRequest.method())) {
          await route.continue()
          return
        }
        const entry = { method: browserRequest.method(), path: new URL(browserRequest.url()).pathname }
        let fixturePosition = false
        if (
          entry.method === 'PUT' &&
          entry.path === `/api/branches/${readOnlyStory.branchId}/reading-state`
        ) {
          try {
            fixturePosition = browserRequest.postDataJSON()?.chapter_id === readOnlyStory.chapterId
          } catch {
            // Invalid JSON is an unexpected write too; record and block it below.
          }
        }
        if (fixturePosition) {
          positionWrites.push(entry)
          await route.continue()
        } else {
          writes.push(entry)
          await route.abort('blockedbyclient')
        }
      })
      try {
        await use()
      } finally {
        const screenshot = testInfo.outputPath('read-only-workspace.png')
        await page.screenshot({ path: screenshot, fullPage: true })
        const currentResponse = await request.get(`/api/chapters/${readOnlyStory.chapterId}`)
        expect(currentResponse.ok()).toBeTruthy()
        const current = await currentResponse.json()
        const tasksResponse = await request.get(`/api/tasks?branch_id=${readOnlyStory.branchId}`)
        expect(tasksResponse.ok()).toBeTruthy()
        const tasks = await tasksResponse.json()
        const summary = testInfo.outputPath('read-only-api-summary.json')
        await writeFile(
          summary,
          JSON.stringify(
            {
              scope: 'this browser context; fixture setup is separate',
              fixture: readOnlyStory,
              readingPositionWrites: positionWrites,
              otherWriteRequests: writes,
              chapterContentUnchanged: current.content === readOnlyStory.content,
              chapterRevisionUnchanged: current.revision === readOnlyStory.revision,
              fixtureModelTasks: tasks.length,
            },
            null,
            2,
          ),
          'utf8',
        )
        await testInfo.attach('read-only-api-summary', {
          path: summary,
          contentType: 'application/json',
        })
        await testInfo.attach('read-only-workspace', { path: screenshot, contentType: 'image/png' })
        expect(writes, '只允许本测试作品的位置维护；不得派发生成、改稿或其他作品写请求').toEqual([])
        expect(current.content).toBe(readOnlyStory.content)
        expect(current.revision).toBe(readOnlyStory.revision)
        expect(tasks).toEqual([])
      }
    },
    { auto: true },
  ],
})

export { expect }
