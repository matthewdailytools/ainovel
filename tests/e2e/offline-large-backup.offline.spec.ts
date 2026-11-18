import { expect, test, type Page } from '@playwright/test'
import { createHash } from 'node:crypto'
import { readFile, stat, writeFile } from 'node:fs/promises'
import type { LocalChapter, LocalProject, LocalRevision } from '../../frontend/offlineStore'

const title = '空谷铜铃 · 长篇备份验证'
const keptTitle = '仍须保留的本机作品'
const chapterCount = 36
const body = '渡口收船后，学徒沿着石阶逐条记录和铜铃有关的证词。'.repeat(12_000).slice(0, 249_940)

function chapterText(number: number) {
  return `第${number}章开场。\n${body}\n第${number}章留档。`
}

function hash(text: string) {
  return createHash('sha256').update(text, 'utf8').digest('hex')
}

function projects(page: Page) {
  return page.getByRole('combobox', { name: '离线作品', exact: true })
}

async function prepareOffline(page: Page) {
  await page.goto('/offline/')
  await expect(page.getByText('离线页面就绪', { exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
}

async function createWork(page: Page, name: string) {
  await page.getByLabel('新作品名').fill(name)
  await page.getByRole('button', { name: '本地新建作品', exact: true }).click()
  await expect(projects(page).getByRole('option', { name, exact: true })).toBeAttached()
  await expect(projects(page)).not.toHaveValue('')
  return projects(page).inputValue()
}

async function saveChapter(page: Page, name: string, text: string) {
  await page.getByLabel('新章节名').fill(name)
  await page.getByRole('button', { name: '本地新建章节', exact: true }).click()
  await expect(page.getByRole('heading', { name, exact: true })).toBeVisible()
  await page.getByLabel('章节正文').fill(text)
  await page.getByRole('button', { name: '保存正文到浏览器', exact: true }).click()
  await expect(page.getByText('本地已保存 · 修订 1', { exact: true })).toBeVisible()
}

async function manifest(page: Page, workId: string) {
  // Inspect persisted identities without seeding or editing IndexedDB outside the product's UI.
  return page.evaluate(async (id) => {
    function read<T>(request: IDBRequest<T>) {
      return new Promise<T>((resolve, reject) => {
        request.onsuccess = () => resolve(request.result)
        request.onerror = () => reject(request.error)
      })
    }
    const db = await read(indexedDB.open('ainovel-offline-workspace'))
    try {
      const tx = db.transaction(['projects', 'chapters', 'revisions'], 'readonly')
      const work = (await read(tx.objectStore('projects').get(id))) as LocalProject
      const chapters = (
        (await read(tx.objectStore('chapters').index('byBranch').getAll(work.branch.id))) as LocalChapter[]
      ).sort((a, b) => a.position - b.position)
      const versions = tx.objectStore('revisions').index('byRevision')
      const sources = await Promise.all(
        chapters.map(async (chapter) => {
          const [initial, head] = (await Promise.all([
            read(versions.get([chapter.id, 0])),
            read(versions.get([chapter.id, chapter.revision])),
          ])) as [LocalRevision, LocalRevision]
          return {
            chapterId: chapter.id,
            initial: initial.id,
            head: head.id,
            parent: head.parent_id,
            sha256: head.sha256,
            revision: head.revision,
          }
        }),
      )
      return { work: work.novel.id, branch: work.branch.id, sources }
    } finally {
      db.close()
    }
  }, workId)
}

test('36 章超限长稿断网下载独立备份，截断拒绝且完整恢复保留存稿与修订身份', async ({
  page,
  context,
  browser,
  baseURL,
}, testInfo) => {
  test.setTimeout(120_000)
  await prepareOffline(page)
  await context.setOffline(true)
  await page.reload()
  const sourceId = await createWork(page, title)
  for (let number = 1; number <= chapterCount; number++) {
    await saveChapter(page, `铜铃试稿 ${number}`, chapterText(number))
  }
  const original = await manifest(page, sourceId)
  expect(original.sources).toHaveLength(chapterCount)
  for (const [index, source] of original.sources.entries()) {
    expect(source.revision).toBe(1)
    expect(source.parent).toBe(source.initial)
    expect(source.sha256).toBe(hash(chapterText(index + 1)))
  }
  await page.getByRole('button', { name: '导出稿件交换包', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('稿件交换包超过 24 MiB')

  const transfer = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载独立作品备份', exact: true }).click()
  const saved = await transfer
  expect(saved.suggestedFilename()).toMatch(/\.ainovel-backup$/)
  const backup = testInfo.outputPath('long-work.ainovel-backup')
  await saved.saveAs(backup)
  await expect(page.getByText('已下载独立作品备份。', { exact: true })).toBeVisible()
  expect((await stat(backup)).size).toBeGreaterThan(24 * 1024 * 1024)
  const complete = await readFile(backup)
  const truncated = testInfo.outputPath('truncated.ainovel-backup')
  await writeFile(truncated, complete.subarray(0, -13))

  const isolated = await browser.newContext({ baseURL })
  try {
    const reader = await isolated.newPage()
    await prepareOffline(reader)
    await expect(projects(reader).getByRole('option')).toHaveCount(1)
    const keptId = await createWork(reader, keptTitle)
    const kept = '原来这本稿里的长街一直有灯。'
    await saveChapter(reader, '仍须保留的章稿', kept)
    const unrelated = await manifest(reader, keptId)
    await isolated.setOffline(true)
    await reader.reload()
    await projects(reader).selectOption(keptId)
    await reader.getByRole('button', { name: '1. 仍须保留的章稿', exact: true }).click()

    const restore = reader.getByLabel('从独立备份恢复作品', { exact: true })
    await restore.setInputFiles(truncated)
    await expect(reader.getByRole('alert')).toContainText('独立备份的格式、分段或校验值无效')
    await expect(projects(reader).getByRole('option')).toHaveCount(2)
    await expect(projects(reader)).toHaveValue(keptId)
    await expect(reader.getByLabel('章节正文')).toHaveValue(kept)
    expect(await manifest(reader, keptId)).toEqual(unrelated)

    await restore.setInputFiles(backup)
    await expect(reader.getByText('已校验并恢复独立作品备份。', { exact: true })).toBeVisible()
    await expect(projects(reader)).toHaveValue(sourceId)
    await expect(reader.getByRole('navigation', { name: '离线章节目录' }).getByRole('button')).toHaveCount(
      chapterCount,
    )
    expect(await manifest(reader, sourceId)).toEqual(original)
    expect(await manifest(reader, keptId)).toEqual(unrelated)
    for (const number of [1, chapterCount]) {
      await reader.getByRole('button', { name: `${number}. 铜铃试稿 ${number}`, exact: true }).click()
      await expect(reader.getByRole('heading', { name: `铜铃试稿 ${number}`, exact: true })).toBeVisible()
      expect(hash(await reader.getByLabel('章节正文').inputValue())).toBe(hash(chapterText(number)))
    }
  } finally {
    await isolated.close()
  }
  expect(await manifest(page, sourceId)).toEqual(original)
})
