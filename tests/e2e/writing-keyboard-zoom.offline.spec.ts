import { chromium, expect, test, type APIResponse, type Locator, type Page } from '@playwright/test'

async function verified(response: APIResponse) {
  expect(response.ok(), await response.text()).toBe(true)
  return response.json()
}

async function focusWithTab(page: Page, target: Locator, key = 'Tab') {
  for (let attempt = 0; attempt < 60; attempt++) {
    if (await target.evaluate((element) => element === document.activeElement)) return
    await page.keyboard.press(key)
  }
  await expect(target).toBeFocused()
}

async function expectVisibleFocus(target: Locator, editor = false) {
  expect(
    await target.evaluate((element, codeEditor) => {
      const outlined = codeEditor ? element.closest('.cm-editor')! : element
      const outline = getComputedStyle(outlined)
      const bounds = element.getBoundingClientRect()
      const visible = document.elementFromPoint(
        Math.max(0, Math.min(innerWidth - 1, (bounds.left + bounds.right) / 2)),
        Math.max(0, Math.min(innerHeight - 1, (bounds.top + bounds.bottom) / 2)),
      )
      return (
        element.contains(visible) &&
        outline.outlineStyle !== 'none' &&
        Number.parseFloat(outline.outlineWidth) > 0
      )
    }, editor),
  ).toBe(true)
}

test('M5 正常写作可用键盘切章、编辑及保存，浏览器网页缩放 200% 后仍可操作', async ({
  baseURL,
  browserName,
}, testInfo) => {
  test.skip(browserName !== 'chromium', '使用 Chromium 自己的网页缩放设置。')
  const context = await chromium.launchPersistentContext(testInfo.outputPath('zoom-profile'), {
    channel: process.env.AINOVEL_BROWSER_CHANNEL || 'chromium',
    headless: true,
    viewport: null,
    args: ['--window-size=1440,1000'],
    baseURL,
  })
  try {
    const page = context.pages()[0]
    await page.goto('/')
    const { csrf } = await verified(await page.request.get('/api/session'))
    const headers = { 'X-Ainovel-CSRF': csrf }
    const { id, branch_id } = await verified(
      await page.request.post('/api/novels', { headers, data: { title: '隔离写作 · 键盘缩放' } }),
    )
    const branch = `/api/branches/${branch_id}`
    async function createChapter(title: string) {
      return (await verified(await page.request.post(branch + '/chapters', { headers, data: { title } }))).id
    }
    const first = await createChapter('渡船靠岸')
    const second = await createChapter('月下开启石门')
    await verified(
      await page.request.put(`/api/chapters/${first}`, {
        headers,
        data: { base_revision: 0, content: '一艘渡船靠到了堤岸。' },
      }),
    )
    const outline = branch + '/outline-workspace'
    const plan = (chapter_id: string, goal: string) => ({
      chapter_id,
      goal,
      obstacle: '打不开锁',
      turn: '发现新刻痕',
      result: '找到开门理由',
    })
    const savedOutline = await verified(
      await page.request.put(outline, {
        headers,
        data: {
          base_revision: 0,
          confirmation_id: null,
          plan: {
            summary: '找到旧石门的钥匙。',
            volumes: [
              {
                id: 'volume',
                title: '铜钥卷',
                summary: '先靠岸寻访，再开石门。',
                units: [
                  {
                    id: 'unit',
                    title: '寻找钥匙',
                    summary: '根据痕迹查找。',
                    chapters: [plan(first, '找到靠岸的路线'), plan(second, '查明石门的来历')],
                  },
                ],
              },
            ],
          },
        },
      }),
    )
    await verified(
      await page.request.post(outline + '/confirm', {
        headers,
        data: { base_revision: savedOutline.draft.revision },
      }),
    )
    await page.evaluate((novelId) => localStorage.setItem('ainovel-novel', novelId), id)
    await page.reload()
    const chapterTitle = page.getByRole('textbox', { name: '章节标题', exact: true })
    const editor = page.getByRole('textbox', { name: '章节正文', exact: true })
    const status = page.getByRole('region', { name: '正文写作' })
    const save = status.getByRole('button', { name: '保存', exact: true })
    const directory = page.getByRole('navigation', { name: '全书目录' })
    await expect(chapterTitle).toHaveValue('渡船靠岸')

    const dimensions = () =>
      page.evaluate(() => ({
        width: innerWidth,
        dpr: devicePixelRatio,
        pageScale: visualViewport?.scale,
        scrollWidth: document.documentElement.scrollWidth,
      }))
    const baseline = await dimensions()
    const next = page.getByRole('button', { name: '继续写作 · 月下开启石门', exact: true })
    await focusWithTab(page, next)
    await expectVisibleFocus(next)
    await page.keyboard.press('Enter')
    await expect(chapterTitle).toHaveValue('月下开启石门')
    await expect(status.getByText('查明石门的来历', { exact: true })).toBeVisible()
    await focusWithTab(page, editor)
    await expectVisibleFocus(editor, true)
    const body = '她按住石门，月光照见钥匙上的旧痕。'
    await page.keyboard.insertText(body)
    await focusWithTab(page, save, 'Shift+Tab')
    await expectVisibleFocus(save)
    await page.keyboard.press('Enter')
    const chapterPath = `/api/chapters/${second}`
    const stored = async () => (await (await page.request.get(chapterPath)).json()).content
    await expect.poll(stored).toBe(body)

    const settings = await context.newPage()
    await settings.goto('chrome://settings/appearance')
    const browserZoom = settings.locator('settings-appearance-page #zoomLevel')
    await expect(browserZoom).toHaveValue('1')
    await browserZoom.selectOption({ label: '200%' })
    await expect(browserZoom).toHaveValue('2')
    await settings.close()
    await page.bringToFront()
    await page.reload()
    await expect(chapterTitle).toBeVisible()
    await expect.poll(async () => (await dimensions()).dpr).toBeCloseTo(baseline.dpr * 2)
    const zoomed = await dimensions()
    expect(zoomed.width).toBeCloseTo(baseline.width / 2, -1)
    expect(zoomed.pageScale).toBe(1)
    const overflowing = await page.evaluate(() =>
      Array.from(document.querySelectorAll('body *')).flatMap((element) => {
        const { right, width } = element.getBoundingClientRect()
        return right > innerWidth + 1 && width > 0
          ? [
              {
                tag: element.tagName,
                label: element.closest('label')?.innerText.split('\n')[0],
                parent: element.parentElement?.parentElement?.className,
                right: Math.round(right),
              },
            ]
          : []
      }),
    )
    await testInfo.attach('browser-page-zoom', {
      body: JSON.stringify({ baseline, zoomed, overflowing }),
      contentType: 'application/json',
    })

    const account = page.getByRole('combobox', { name: '模型账户', exact: true })
    await focusWithTab(page, account)
    await expectVisibleFocus(account)
    for (const title of ['渡船靠岸', '月下开启石门']) {
      const chapter = directory.getByRole('button', { name: new RegExp(title) })
      await focusWithTab(page, chapter)
      await expectVisibleFocus(chapter)
      await page.keyboard.press('Enter')
      await expect(chapterTitle).toHaveValue(title)
    }
    const history = status.getByRole('button', { name: '版本历史', exact: true })
    await focusWithTab(page, history)
    await expectVisibleFocus(history)
    await page.keyboard.press('Enter')
    await expect(status.getByRole('heading', { name: '恢复会生成新修订，当前稿保留在历史中' })).toBeVisible()
    await focusWithTab(page, history, 'Shift+Tab')
    await page.keyboard.press('Enter')
    await expect(status.getByRole('heading', { name: '恢复会生成新修订，当前稿保留在历史中' })).toHaveCount(0)
    await focusWithTab(page, editor)
    await expectVisibleFocus(editor, true)
    await page.keyboard.press('Tab')
    await expect(status.getByRole('button', { name: '下载当前草稿', exact: true })).toBeFocused()
    await page.keyboard.press('Shift+Tab')
    await expect(editor).toBeFocused()
    await page.keyboard.press('Control+End')
    const added = '她抬起钥匙。'
    await page.keyboard.insertText(added)
    await focusWithTab(page, save, 'Shift+Tab')
    await page.keyboard.press('Enter')
    await expect.poll(stored).toBe(body + added)
    expect(zoomed.scrollWidth, JSON.stringify(overflowing)).toBeLessThanOrEqual(zoomed.width + 1)
    const assistant = page.getByRole('combobox', { name: '这次要做什么', exact: true })
    await focusWithTab(page, assistant)
    await expectVisibleFocus(assistant)
  } finally {
    await context.close()
  }
})
