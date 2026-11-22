import type { Chapter } from './api'

export type ReadingPosition = { chapter_id: string | null; cursor: number; note: string; revision: number }
type LocalPosition = {
  chapterId: string
  chapterRevision: number
  readingRevision: number
  cursor: number
  scrollTop?: number
  note: string
  pending?: ReadingPosition
}

const storageKey = (branchId: string) => `ainovel-writing-position-${branchId}`

export function readWorkspacePosition(branchId: string, chapters: Chapter[], server: ReadingPosition) {
  try {
    const value: LocalPosition | null = JSON.parse(localStorage.getItem(storageKey(branchId)) || 'null')
    if (!value || typeof value.note !== 'string' || !Number.isSafeInteger(value.cursor) || value.cursor < 0)
      return
    if (
      !chapters.some(
        (chapter) => chapter.id === value.chapterId && chapter.revision === value.chapterRevision,
      )
    )
      return
    const matchesServer =
      value.chapterId === server.chapter_id && value.cursor === server.cursor && value.note === server.note
    const pending = value.pending
    const matchesPending =
      pending &&
      pending.revision === value.readingRevision &&
      pending.revision + 1 === server.revision &&
      pending.chapter_id === server.chapter_id &&
      pending.cursor === server.cursor &&
      pending.note === server.note
    if (value.readingRevision !== server.revision && !matchesServer && !matchesPending) return
    const scrollTop = value.scrollTop
    return {
      ...value,
      scrollTop:
        typeof scrollTop === 'number' && Number.isFinite(scrollTop) && scrollTop >= 0 ? scrollTop : undefined,
    }
  } catch {
    return undefined
  }
}

export function rememberWorkspacePosition(branchId: string, position: LocalPosition) {
  try {
    localStorage.setItem(storageKey(branchId), JSON.stringify(position))
  } catch {
    // The workbench also saves the reading position through the local service.
  }
}
