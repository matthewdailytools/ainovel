import { expect, test, vi } from 'vitest'
import { indexedDB } from 'fake-indexeddb'

test('each window preserves its own draft and marks other-window recovery for review', async () => {
  vi.stubGlobal('indexedDB', indexedDB)
  const values = new Map<string, string>()
  vi.stubGlobal('sessionStorage', {
    getItem: (key: string) => values.get(key) || null,
    setItem: (key: string, value: string) => values.set(key, value),
  })
  vi.resetModules()
  const first = await import('./drafts')
  await first.writeDraft({ id: 'chapter', base: 2, text: '甲😀𠮷\n原稿', updated: 1 })
  values.clear()
  vi.resetModules()
  const second = await import('./drafts')
  expect(await second.readDraft('chapter')).toMatchObject({ text: '甲😀𠮷\n原稿', fromOtherWindow: true })
  await second.writeDraft({ id: 'chapter', base: 2, text: '另一窗口的未保存稿', updated: 2 })
  expect(await first.readDraft('chapter')).toMatchObject({ text: '甲😀𠮷\n原稿', fromOtherWindow: false })
  expect(await second.readDraft('chapter')).toMatchObject({
    text: '另一窗口的未保存稿',
    fromOtherWindow: false,
  })
  vi.unstubAllGlobals()
})
