import type { Excerpt, Task } from './api'
import type { Selection } from './Editor'

export type RecoveryTarget = {
  novelId: string
  branchId: string
  chapterId: string | null
  revision: number | null
  savedContent: string
  draftContent: string
}
export type RecoveryReference = {
  id: string
  status: 'available' | 'missing' | 'unavailable'
  excerpt?: Excerpt
  basis: string
}
export type TaskInputRecovery = {
  task: Task
  target: RecoveryTarget
  basis: string
  references: RecoveryReference[]
  selection: Selection | null
  selectionNotice: string
}
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('任务或资料格式不可核对，请重新读取。')
  return value as Record<string, unknown>
}
function boundary(text: string, position: number) {
  if (!Number.isSafeInteger(position) || position < 0 || position > text.length) return false
  const before = text.charCodeAt(position - 1)
  const after = text.charCodeAt(position)
  return !(before >= 0xd800 && before <= 0xdbff && after >= 0xdc00 && after <= 0xdfff)
}
export function prepareTaskInputRecovery(
  task: Task,
  target: RecoveryTarget,
  currentReferences: unknown,
): TaskInputRecovery {
  if (task.novel_id !== target.novelId || task.branch_id !== target.branchId)
    throw new Error('原任务不属于当前作品和工作版本，未恢复任何输入或资料。')
  const payload = object(task.payload)
  if (
    payload.novel_id !== target.novelId ||
    payload.branch_id !== target.branchId ||
    typeof payload.instruction !== 'string' ||
    !payload.instruction.trim()
  )
    throw new Error('原任务的作品、版本或输入依据不完整，未恢复。')
  if (
    typeof payload.action !== 'string' ||
    (payload.fixed !== undefined &&
      (!Array.isArray(payload.fixed) || payload.fixed.some((value) => typeof value !== 'string'))) ||
    (payload.writing_profile !== undefined && typeof payload.writing_profile !== 'string') ||
    ['plot_contract_check', 'source_identity_context'].some(
      (key) => payload[key] !== undefined && typeof payload[key] !== 'boolean',
    )
  )
    throw new Error('原任务设置格式不可核对，未恢复。')
  const ids = payload.reference_ids ?? []
  if (
    !Array.isArray(ids) ||
    ids.length > 20 ||
    ids.some((id) => typeof id !== 'string' || !id.trim()) ||
    new Set(ids).size !== ids.length ||
    !Array.isArray(currentReferences)
  )
    throw new Error('原引用清单或当前资料不可核对，请重新读取。')
  const rows = currentReferences.map(object)
  const references: RecoveryReference[] = ids.map((id: string) => {
    const matches = rows.filter((row) => row.id === id)
    if (!matches.length) return { id, status: 'missing', basis: 'missing' }
    const row = matches[0]
    const valid =
      matches.length === 1 &&
      (row.novel_id === null || row.novel_id === target.novelId) &&
      ['unit_id', 'book_id', 'quote', 'note', 'title', 'source'].every(
        (key) => typeof row[key] === 'string',
      ) &&
      typeof row.quote === 'string' &&
      !!row.quote &&
      typeof row.start === 'number' &&
      Number.isSafeInteger(row.start) &&
      row.start >= 0 &&
      typeof row.end === 'number' &&
      Number.isSafeInteger(row.end) &&
      row.end - row.start === row.quote.length
    if (!valid) return { id, status: 'unavailable', basis: 'unavailable' }
    const excerpt: Excerpt = {
      id,
      unit_id: String(row.unit_id),
      book_id: String(row.book_id),
      start: Number(row.start),
      end: Number(row.end),
      quote: String(row.quote),
      note: String(row.note),
      title: String(row.title),
      source: String(row.source),
    }
    return { id, status: 'available', excerpt, basis: JSON.stringify([row.novel_id, excerpt]) }
  })
  let selection: Selection | null = null
  let selectionNotice = '原任务没有选区；确认恢复后请按需重新选择正文。'
  if (payload.selection_start != null || payload.selection_end != null || payload.selection_text != null) {
    const start = payload.selection_start
    const end = payload.selection_end
    const quote = payload.selection_text
    if (
      payload.chapter_id === target.chapterId &&
      payload.base_revision === target.revision &&
      target.draftContent === target.savedContent &&
      typeof start === 'number' &&
      typeof end === 'number' &&
      boundary(target.savedContent, start) &&
      boundary(target.savedContent, end) &&
      start < end &&
      typeof quote === 'string' &&
      quote.length === end - start &&
      target.savedContent.slice(start, end) === quote
    ) {
      selection = { start, end, text: quote }
      selectionNotice = `原选区 ${start}—${end}（UTF-16）与同章保存修订及原文一致，可恢复。`
    } else {
      selectionNotice = '原选区的章节、修订或原文依据不再一致；不会使用旧坐标，确认后请重新选择。'
    }
  }
  return {
    task,
    target: { ...target },
    basis: JSON.stringify([
      task.id,
      payload,
      references.map((row) => [row.id, row.status, row.basis]),
      target,
    ]),
    references,
    selection,
    selectionNotice,
  }
}

export function checkedRecoveryReferences(preview: TaskInputRecovery, ids: string[]): string[] {
  if (
    new Set(ids).size !== ids.length ||
    ids.some((id) => !preview.references.some((row) => row.id === id && row.status === 'available'))
  )
    throw new Error('只能恢复本次已核对且当前可用的原引用。')
  return preview.references.filter((row) => ids.includes(row.id)).map((row) => row.id)
}
