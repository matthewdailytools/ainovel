import { useEffect, useState } from 'react'
import { bodyCountMethod, type OfflineRules } from './offlineRules'
import type { PortableCheckpointFact, PortableCheckpointLengthTarget } from './portableCheckpoint'

const reminder = { due: '回收已到期', open: '已进入回收窗口', review: '故事原文或关联待复检' }
const factKinds: Record<PortableCheckpointFact['kind'], string> = {
  event: '事件',
  knowledge: '认知',
  capability: '能力',
  resource: '资源',
  relationship: '关系',
  promise: '承诺',
  world: '世界规则',
}
const factTruth: Record<PortableCheckpointFact['truth'], string> = {
  fact: '事实',
  belief: '相信',
  uncertain: '待证实',
  plan: '计划',
}

function LengthTarget({
  target,
  issues,
}: {
  target: PortableCheckpointLengthTarget['targets'] | undefined
  issues?: string[]
}) {
  if (target === undefined) return null
  if (target === null) return <span> · 篇幅目标已清除</span>
  const bounds = [
    target.min_chars !== null ? `至少 ${target.min_chars} 字符` : null,
    target.max_chars !== null ? `至多 ${target.max_chars} 字符` : null,
    target.expected_chapters !== null ? `${target.expected_chapters} 章` : null,
  ].filter(Boolean)
  return (
    <span>
      {' '}
      · 冻结篇幅目标 {bounds.join('、') || '未填写数值'} · {issues?.length ? issues.join('、') : '已达标'}
    </span>
  )
}

export function OfflineRulePreview({
  read,
  refreshToken,
}: {
  read: () => Promise<OfflineRules>
  refreshToken: number
}) {
  const [open, setOpen] = useState(false)
  const [result, setResult] = useState<{ data?: OfflineRules; error?: string }>()
  useEffect(() => {
    if (!open) return
    let active = true
    setResult(undefined)
    void read().then(
      (data) => {
        if (active) setResult({ data })
      },
      (failure: unknown) => {
        if (active) setResult({ error: failure instanceof Error ? failure.message : '无法读取本机保存稿。' })
      },
    )
    return () => {
      active = false
    }
  }, [open, read, refreshToken])

  const data = result?.data
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>本机规则核算</summary>
      {!result && <p role="status">正在读取本机保存稿…</p>}
      {result?.error && <p role="alert">{result.error}</p>}
      {data && (
        <>
          <p>按本机正式保存稿核算。正式体检与交付记录在历史面板查看。</p>
          <p>
            全书 {data.book.chapters} 章 · {data.book.chars} 个有效字符 · 空稿 {data.book.empty} 章 · 待复检{' '}
            {data.book.stale} 章
            <LengthTarget target={data.book.lengthTarget} issues={data.book.lengthIssues} />
          </p>
          <details>
            <summary>章与卷的篇幅</summary>
            <p>{bodyCountMethod}</p>
            {!!data.volumes.length && (
              <ul aria-label="分卷篇幅">
                {data.volumes.map(({ id, title, chapters, chars, lengthTarget, lengthIssues }) => (
                  <li key={id}>
                    {title} · {chapters} 章 · {chars} 个有效字符
                    <LengthTarget target={lengthTarget} issues={lengthIssues} />
                  </li>
                ))}
              </ul>
            )}
            {!!data.chapters.length && (
              <ul aria-label="分章篇幅">
                {data.chapters.map(
                  ({ id, title, position, revision, chars, stale, lengthTarget, lengthIssues }) => (
                    <li key={id}>
                      {position}. {title} · 修订 {revision} · {chars} 个有效字符{stale ? ' · 待复检' : ''}
                      <LengthTarget target={lengthTarget} issues={lengthIssues} />
                    </li>
                  ),
                )}
              </ul>
            )}
          </details>
          {!!data.facts.length && (
            <details>
              <summary>正式故事事实（{data.facts.length}）</summary>
              <ul>
                {data.facts.map(
                  (
                    {
                      subject,
                      kind,
                      truth,
                      statement,
                      story_time,
                      evidence,
                      chapterTitle,
                      chapterRevision,
                      quote,
                    },
                    index,
                  ) => (
                    <li key={`${evidence.revision_id}:${evidence.start}:${index}`}>
                      <p>
                        {subject} · {factKinds[kind]} · {factTruth[truth]}
                        {story_time ? ` · ${story_time}` : ''}
                      </p>
                      <p>{statement}</p>
                      <p>
                        {chapterTitle} · 修订 {chapterRevision} · 原文位置 {evidence.start}–{evidence.end}
                      </p>
                      <blockquote>{quote}</blockquote>
                    </li>
                  ),
                )}
              </ul>
            </details>
          )}
          <h3>伏笔提醒</h3>
          {data.outlineIssue && <p role="status">{data.outlineIssue}</p>}
          {data.outlineRevision ? (
            data.promises.length ? (
              <ul>
                {data.promises.map(({ id, title, state, chapter, required }) => (
                  <li key={id}>
                    {title} · {reminder[state]}
                    {required ? ' · 必达承诺尚未兑现' : ''}
                    {chapter ? ' · 最迟第 ' + chapter.position + ' 章 ' + chapter.title : ''}
                  </li>
                ))}
              </ul>
            ) : (
              <p>当前保存进度暂无待处理提醒。</p>
            )
          ) : (
            !data.outlineIssue && <p>确认大纲后可核算回收窗口。</p>
          )}
        </>
      )}
    </details>
  )
}
