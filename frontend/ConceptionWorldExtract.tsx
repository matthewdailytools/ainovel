import { useEffect, useState } from 'react'
import { api, type Chapter } from './api'

type ChapterList = Pick<Chapter, 'id' | 'position' | 'title' | 'revision'> & { chars: number }
const maxChapters = 20
const maxChars = 20_000

export function ConceptionWorldExtract({
  branchId,
  storyRevision,
  locked,
  onExtract,
}: {
  branchId: string
  storyRevision?: number
  locked: boolean
  onExtract: (chapterIds: string[], budget?: number) => void
}) {
  const [chapters, setChapters] = useState<ChapterList[]>()
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [budget, setBudget] = useState('')
  const [loadCount, setLoadCount] = useState(0)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    setChapters(undefined)
    setError('')
    void api<ChapterList[]>(`/branches/${encodeURIComponent(branchId)}/chapters`)
      .then((data) => {
        if (active) setChapters(data)
      })
      .catch((failure: Error) => {
        if (active) setError(failure.message)
      })
    return () => {
      active = false
    }
  }, [branchId, storyRevision, loadCount])

  const available = chapters?.filter((chapter) => chapter.chars > 0) || []
  const selected = available.filter((chapter) => selectedIds.includes(chapter.id))
  const chars = selected.reduce((count, chapter) => count + chapter.chars, 0)
  const overLimit = selected.length > maxChapters || chars > maxChars
  const amount = budget ? Number(budget) : undefined
  const badBudget = amount !== undefined && (!Number.isFinite(amount) || amount <= 0)

  return (
    <section className="conception-world-extract" aria-label="从正文提取世界设定">
      <h3>从正文提取世界设定</h3>
      <p className="small">
        只发送你勾选的保存稿。AI 提取正文明确交代的规则和资源，并附原文。单批最多 {maxChapters} 章、合计{' '}
        {maxChars.toLocaleString()} 字符。
      </p>
      {error && (
        <div role="alert">
          <p>{error}</p>
          <button type="button" onClick={() => setLoadCount((count) => count + 1)}>
            重新读取本书章节
          </button>
        </div>
      )}
      {!chapters && !error && <p className="small muted">正在读取保存稿目录…</p>}
      {chapters && !available.length && <p className="small muted">这本小说还没有含正文的保存稿。</p>}
      {!!available.length && (
        <fieldset disabled={locked}>
          <legend>世界规则与资源</legend>
          {available.map((chapter) => (
            <label className="check" key={chapter.id}>
              <input
                type="checkbox"
                aria-label={`选择用于提取世界规则的章节 ${chapter.position} · ${chapter.title}`}
                checked={selectedIds.includes(chapter.id)}
                onChange={(event) =>
                  setSelectedIds((previous) =>
                    event.target.checked
                      ? [...previous, chapter.id]
                      : previous.filter((id) => id !== chapter.id),
                  )
                }
              />
              <span>
                {chapter.position} · {chapter.title} · 修订 {chapter.revision} · {chapter.chars} 字符
              </span>
            </label>
          ))}
          <p className="small" aria-live="polite">
            已选 {selected.length} 章、{chars.toLocaleString()} 字符。
            {overLimit ? '超过单批范围，请缩小选择。' : ''}
          </p>
          <label>
            提取任务预算上限（可选，使用所选账户币种）
            <input
              type="number"
              min={0.01}
              step={0.01}
              value={budget}
              onChange={(event) => setBudget(event.target.value)}
            />
          </label>
          {badBudget && <p className="small">请填写大于零的提取预算，或留空使用账户剩余预算。</p>}
          <button
            className="primary"
            type="button"
            disabled={!selected.length || overLimit || badBudget}
            onClick={() =>
              onExtract(
                selected.map((chapter) => chapter.id),
                amount,
              )
            }
          >
            从所选保存稿提取世界设定
          </button>
        </fieldset>
      )}
    </section>
  )
}
