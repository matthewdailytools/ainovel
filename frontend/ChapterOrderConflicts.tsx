import { useId, useState } from 'react'

export type ChapterOrderConflict = {
  position: number
  chapters: { chapter_id: string; title: string; head_revision: string; scheduled: boolean }[]
}

export type ChapterOrderResolution = {
  chapter_id: string
  position: number
  heads: Record<string, string>
}

type Actions = {
  busy: boolean
  blocked?: boolean
  onResolve: (selection: ChapterOrderResolution) => void | Promise<void>
}

function ConflictRow({ conflict, busy, blocked, onResolve }: Actions & { conflict: ChapterOrderConflict }) {
  const [selected, setSelected] = useState('')
  const selectId = useId()
  const movable = conflict.chapters.filter((chapter) => !chapter.scheduled)
  const titles = new Map<string, number>()
  for (const { title } of conflict.chapters) titles.set(title, (titles.get(title) ?? 0) + 1)
  function chapterLabel({ title, chapter_id }: ChapterOrderConflict['chapters'][number]) {
    return (titles.get(title) ?? 0) > 1 ? `${title} · 编号 ${chapter_id}` : title
  }
  return (
    <section aria-label={`章位 ${conflict.position}`}>
      <p>以下章节占用同一章位（{conflict.position}）。</p>
      <ul>
        {conflict.chapters.map((chapter) => (
          <li key={chapter.chapter_id}>
            {chapterLabel(chapter)} · {chapter.scheduled ? '已编入大纲' : '未编入大纲'}
          </li>
        ))}
      </ul>
      {movable.length > 0 ? (
        <>
          <label htmlFor={selectId}>选择调整章位的章节</label>
          <select
            id={selectId}
            value={selected}
            disabled={busy || blocked}
            onChange={(event) => setSelected(event.target.value)}
          >
            <option value="">请选择章节</option>
            {movable.map((chapter) => (
              <option value={chapter.chapter_id} key={chapter.chapter_id}>
                {chapterLabel(chapter)}
              </option>
            ))}
          </select>
          <button
            type="button"
            disabled={busy || blocked || !selected}
            onClick={() =>
              void onResolve({
                chapter_id: selected,
                position: conflict.position,
                heads: Object.fromEntries(
                  conflict.chapters.map((chapter) => [chapter.chapter_id, chapter.head_revision]),
                ),
              })
            }
          >
            移至目录末尾
          </button>
        </>
      ) : (
        <p>这些章节均已编入大纲。请在大纲台核对章位安排。</p>
      )}
    </section>
  )
}

export function ChapterOrderConflicts({
  conflicts,
  ...actions
}: Actions & { conflicts: ChapterOrderConflict[] }) {
  if (conflicts.length === 0) return null
  return (
    <section aria-label="章位冲突">
      <h3>章位冲突</h3>
      {conflicts.map((conflict) => (
        <ConflictRow
          key={JSON.stringify([
            conflict.position,
            conflict.chapters.map((chapter) => [
              chapter.chapter_id,
              chapter.head_revision,
              chapter.scheduled,
            ]),
          ])}
          conflict={conflict}
          {...actions}
        />
      ))}
    </section>
  )
}
