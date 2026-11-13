import { useEffect, useState } from 'react'
import { api, type Artifact, type Chapter, type Fact } from './api'

export function ChapterContext({
  branchId,
  chapter,
  artifacts,
  onExtract,
}: {
  branchId: string
  chapter?: Chapter
  artifacts: Artifact[]
  onExtract: () => void
}) {
  const [facts, setFacts] = useState<Fact[]>([])
  const [context, setContext] = useState<{ facts: Fact[]; coverage: string }>()
  const [error, setError] = useState('')
  const receipt = artifacts
    .filter((a) => a.chapter_id === chapter?.id && a.status === 'accepted' && a.kind === 'memory')
    .map((a) => `${a.id}:${a.candidate_revision}`)
    .join(',')
  useEffect(() => {
    let active = true
    setContext(undefined)
    setFacts([])
    setError('')
    if (!chapter) return
    void Promise.all([
      api<Fact[]>(`/branches/${branchId}/facts`),
      api<{ facts: Fact[]; coverage: string }>(`/branches/${branchId}/context?chapter_id=${chapter.id}`),
    ])
      .then(([rows, current]) => {
        if (active) {
          setFacts(rows)
          setContext(current)
        }
      })
      .catch((e: unknown) => {
        if (active) setError(e instanceof Error ? e.message : '故事记录读取失败')
      })
    return () => {
      active = false
    }
  }, [branchId, chapter?.id, chapter?.revision, receipt])
  if (!chapter) return null
  const own = facts.filter((f) => f.chapter_id === chapter.id)
  const current = own.filter((f) => f.source_revision === chapter.revision && !f.stale)
  const stale = own.filter((f) => f.stale)
  const reviewed = artifacts.some(
    (a) =>
      a.chapter_id === chapter.id &&
      a.status === 'accepted' &&
      a.kind === 'memory' &&
      a.base_revision === chapter.revision,
  )
  const pending = chapter.content.trim() && !current.length && !reviewed
  return (
    <details className="notice">
      <summary>
        写到这里的故事记录 ·{' '}
        {error
          ? '读取失败'
          : !context
            ? '正在读取'
            : pending
              ? '本章待按需整理'
              : `${current.length} 条当前章记录`}
      </summary>
      {error && <p role="alert">{error}</p>}
      {context && (
        <>
          <p className="small">
            正文保存与故事记录分别维护。
            {pending
              ? '未见本修订的有效记录；可提取候选或手动录入。未整理不表示正文有误。'
              : '本修订的已采用记录可回到原文。'}
            {stale.length ? ` 本章另有 ${stale.length} 条旧记录已失效。` : ''}
          </p>
          <p className="small muted">
            {context.coverage} 此处旧式记录按章序查阅；场景时点以单独绑定的结构化状态为准。
          </p>
          <button type="button" onClick={onExtract} disabled={!chapter.content.trim()}>
            准备提取本章故事记录
          </button>
          {[...current, ...context.facts.slice(-8)].map((fact) => (
            <article className="fact" key={fact.id}>
              <strong>
                {fact.subject} · {fact.kind} · {fact.truth}
              </strong>
              <p>{fact.statement}</p>
              <details>
                <summary>
                  原文依据 · 第 {fact.position} 章／修订 {fact.source_revision}
                </summary>
                <blockquote>{fact.evidence}</blockquote>
                <small>
                  故事时点：{fact.story_time} · UTF-16 {fact.evidence_start}—{fact.evidence_end}
                </small>
              </details>
            </article>
          ))}
          {!current.length && !context.facts.length && (
            <p className="empty-note">
              暂无有效故事记录，仍可继续手写。未绑定时点时可参考近期正文；绑定场景以相应时点的有效结构化状态为准。
            </p>
          )}
        </>
      )}
    </details>
  )
}
