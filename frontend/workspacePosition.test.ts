import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import type { Chapter } from './api'
import { readWorkspacePosition, rememberWorkspacePosition, type ReadingPosition } from './workspacePosition'

const branch = 'branch'
const chapter = {
  id: 'second',
  branch_id: branch,
  position: 2,
  title: '第二章',
  revision: 4,
  content: '合上闸门。',
  stale: 0,
} satisfies Chapter
const pending = { chapter_id: 'first', cursor: 3, note: '上一个位置', revision: 7 }
const local = {
  chapterId: chapter.id,
  chapterRevision: chapter.revision,
  readingRevision: pending.revision,
  cursor: 2,
  note: '继续写闸门',
  scrollTop: 180,
  pending,
}

beforeEach(() => {
  const values = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
  })
  rememberWorkspacePosition(branch, local)
})
afterEach(() => vi.unstubAllGlobals())

test('restores the newer local position after its own earlier request commits late', () => {
  expect(readWorkspacePosition(branch, [chapter], { ...pending, revision: 8 })).toEqual(local)
})

test('ignores a position when another window recorded a newer value', () => {
  const other: ReadingPosition = { chapter_id: 'first', cursor: 6, note: '另一窗口的记录', revision: 8 }
  expect(readWorkspacePosition(branch, [chapter], other)).toBeUndefined()
})

test('does not apply a cached cursor and scroll to changed prose or a different work version', () => {
  expect(readWorkspacePosition(branch, [{ ...chapter, revision: 5 }], pending)).toBeUndefined()
  expect(readWorkspacePosition('another-branch', [chapter], pending)).toBeUndefined()
})
