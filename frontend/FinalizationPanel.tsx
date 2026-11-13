import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { Notify } from './App'

type Checklist = {
  manuscript_read: boolean
  main_conflict_closed: boolean
  character_outcomes_reviewed: boolean
  promises_addressed: boolean
}
type Decision = '' | 'not_an_error' | 'intentional_choice'
type Disposition = { decision: Decision; reason: string }
type FinalizationIssue = {
  id: string
  position: number
  title: string
  revision: number
  quote: string
  reason: string
  repair: string
  sources: { kind: string; artifact_id?: string; task_id?: string }[]
}
type FinalizationStatus = {
  branch_id: string
  base_story_revision: number
  manifest_sha256: string
  author_finalized: boolean
  status: 'finalized' | 'outdated' | 'blocked' | 'unconfirmed'
  chapter_count: number
  expected_chapters: number
  deterministic_blockers: { id: string; message: string }[]
  known_issues: FinalizationIssue[]
  historical_reports_not_current: number
  record: {
    id: string
    story_revision: number
    note: string
    created: number
    checklist: Checklist
    dispositions: { issue_id: string; decision: Exclude<Decision, ''>; reason: string }[]
  } | null
  note: string
}

const emptyChecklist: Checklist = {
  manuscript_read: false,
  main_conflict_closed: false,
  character_outcomes_reviewed: false,
  promises_addressed: false,
}
const confirmations: [keyof Checklist, string][] = [
  ['manuscript_read', '我已通读当前版本正文，核对缺章、衔接和内部占位说明。'],
  ['main_conflict_closed', '本书主冲突已按选定方向闭合，开放部分有明确保留理由。'],
  ['character_outcomes_reviewed', '主要人物去向与重要关系的处理已经核对。'],
  ['promises_addressed', '重要承诺与伏笔已处理，或在定稿说明中写明保留理由。'],
]
const statusLabels = {
  finalized: '当前版本已由作者确认定稿',
  outdated: '旧定稿记录已过期，请重新审阅当前版本',
  blocked: '存在待修复问题或待作者裁定的意见',
  unconfirmed: '当前版本尚未由作者确认定稿',
}

export function FinalizationPanel({
  branchId,
  notify,
  onFinalized,
  refreshVersion,
}: {
  branchId: string
  notify: Notify
  onFinalized?: () => Promise<void>
  refreshVersion?: number
}) {
  const [loaded, setLoaded] = useState<FinalizationStatus>()
  const [checklist, setChecklist] = useState<Checklist>(emptyChecklist)
  const [dispositions, setDispositions] = useState<Record<string, Disposition>>({})
  const [note, setNote] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [reload, setReload] = useState(0)
  const alive = useRef(true)
  const currentBranch = useRef(branchId)
  const branchGeneration = useRef(0)
  const lastLoaded = useRef<FinalizationStatus | undefined>(undefined)
  const pendingRequest = useRef<{ signature: string; key: string } | null>(null)
  if (currentBranch.current !== branchId) {
    currentBranch.current = branchId
    branchGeneration.current += 1
  }
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])
  useEffect(() => {
    let active = true
    if (lastLoaded.current?.branch_id !== branchId) {
      setBusy(false)
      pendingRequest.current = null
    }
    setLoading(true)
    setError('')
    void api<FinalizationStatus>(`/branches/${branchId}/finalization`)
      .then((row) => {
        if (!active) return
        const previous = lastLoaded.current
        if (previous?.branch_id !== row.branch_id) {
          setChecklist(emptyChecklist)
          setDispositions({})
          setNote('')
          setStatus('')
        } else if (
          previous.base_story_revision !== row.base_story_revision ||
          previous.manifest_sha256 !== row.manifest_sha256
        ) {
          setChecklist(emptyChecklist)
          setDispositions({})
          setStatus('正文或设定版本已变化，说明草稿保留；请重新通读并勾选确认。')
        }
        lastLoaded.current = row
        setLoaded(row)
      })
      .catch((e: unknown) => {
        if (active) setError(e instanceof Error ? e.message : '定稿状态读取失败，请重试。')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [branchId, reload, refreshVersion])
  const data = loaded?.branch_id === branchId ? loaded : undefined
  const adjudicated =
    data?.known_issues.every((issue) => {
      const answer = dispositions[issue.id]
      return answer?.decision && [...answer.reason.trim()].length >= 10
    }) ?? false
  const ready =
    data &&
    !data.author_finalized &&
    !data.deterministic_blockers.length &&
    adjudicated &&
    Object.values(checklist).every(Boolean) &&
    [...note.trim()].length >= 10

  async function confirm(event: React.FormEvent) {
    event.preventDefault()
    if (!ready || busy || loading || !data) return
    setBusy(true)
    setError('')
    const target = branchId
    const generation = branchGeneration.current
    try {
      const values = {
        base_story_revision: data.base_story_revision,
        base_manifest_sha256: data.manifest_sha256,
        checklist,
        note: note.trim(),
        dispositions: data.known_issues.map((issue) => {
          const answer = dispositions[issue.id]
          if (!answer || !answer.decision) throw new Error('请逐项完成当前严重意见的作者裁定。')
          return { issue_id: issue.id, decision: answer.decision, reason: answer.reason.trim() }
        }),
      }
      const signature = JSON.stringify(values)
      if (pendingRequest.current?.signature !== signature)
        pendingRequest.current = { signature, key: crypto.randomUUID() }
      const row = await api<FinalizationStatus>(`/branches/${target}/finalization`, 'POST', {
        ...values,
        idempotency_key: pendingRequest.current.key,
      })
      if (!alive.current || currentBranch.current !== target || branchGeneration.current !== generation)
        return
      lastLoaded.current = row
      setLoaded(row)
      setStatus('已记录当前版本的作者定稿；自动生产与三层验收判定保持独立。')
      pendingRequest.current = null
      try {
        await onFinalized?.()
      } catch (e) {
        notify(e)
      }
    } catch (e) {
      if (alive.current && currentBranch.current === target && branchGeneration.current === generation) {
        setError(e instanceof Error ? e.message : '定稿确认失败，输入已保留。')
        notify(e)
      }
    } finally {
      if (alive.current && currentBranch.current === target && branchGeneration.current === generation)
        setBusy(false)
    }
  }

  return (
    <section className="card author-finalization">
      <div className="section-title">
        <h2>作者定稿确认</h2>
        <button type="button" disabled={loading || busy} onClick={() => setReload((n) => n + 1)}>
          刷新定稿检查
        </button>
      </div>
      <p>
        亲自写作或导入的作品也可由作者确认定稿。这里记录对当前版本的人工判断，独立于模型自动生产完成及工程、创作质量、使用收益三层验收。
      </p>
      {loading && <p role="status">正在读取当前版本、章目录与已知问题…</p>}
      {error && (
        <p className="notice" role="alert">
          {error} 说明与裁定草稿保留；版本变化后请刷新核对。
        </p>
      )}
      {!loading && data && (
        <>
          <p role="status">
            <strong>{statusLabels[data.status]}</strong>
          </p>
          <p className="small muted">
            故事修订 {data.base_story_revision} · 当前目录 {data.chapter_count}/{data.expected_chapters} 章。
            改稿、调整设定或目录后，旧定稿记录不再作为新版本确认。
          </p>
          {data.deterministic_blockers.length > 0 && (
            <div className="notice">
              <strong>请先修复以下确定性问题</strong>
              <ul>
                {data.deterministic_blockers.map((b) => (
                  <li key={b.id}>{b.message}</li>
                ))}
              </ul>
              <p>这些检查不能通过填写裁定理由跳过。</p>
            </div>
          )}
          {data.record && (
            <details>
              <summary>查看作者定稿记录 · 故事修订 {data.record.story_revision}</summary>
              <p>{data.record.note}</p>
              <p className="small muted">记录时间：{new Date(data.record.created * 1000).toLocaleString()}</p>
              {data.record.dispositions.map((d) => (
                <p key={d.issue_id}>
                  意见 {d.issue_id.slice(0, 8)} ·{' '}
                  {d.decision === 'not_an_error' ? '裁定不是错误' : '有意叙事安排'}：{d.reason}
                </p>
              ))}
            </details>
          )}
          {data.historical_reports_not_current > 0 && (
            <p className="small muted">
              另有 {data.historical_reports_not_current}{' '}
              份涉及旧版本或未采纳替代稿的严重意见，保留在历史中，未冒充当前稿件问题。
            </p>
          )}
          {!data.author_finalized && (
            <form onSubmit={(event) => void confirm(event)}>
              <fieldset disabled={busy || loading || !!data.deterministic_blockers.length}>
                <legend>确认当前版本</legend>
                {data.known_issues.length > 0 && (
                  <div>
                    <h3>当前版本的严重意见 · {data.known_issues.length} 项</h3>
                    <p>
                      确为错误时请先修稿并复检。误判或有意安排需要逐项说明依据；未解决的错误不能选择忽略后定稿。
                    </p>
                    {data.known_issues.map((issue, index) => {
                      const answer = dispositions[issue.id] || { decision: '', reason: '' }
                      return (
                        <article className="fact" key={issue.id}>
                          <strong>
                            意见 {index + 1} · 第 {issue.position} 章《{issue.title}》／修订 {issue.revision}
                          </strong>
                          <blockquote>{issue.quote}</blockquote>
                          <p>{issue.reason}</p>
                          {issue.repair && <p>原修订建议：{issue.repair}</p>}
                          <label>
                            意见 {index + 1} 的作者裁定
                            <select
                              value={answer.decision}
                              onChange={(e) =>
                                setDispositions((rows) => ({
                                  ...rows,
                                  [issue.id]: { ...answer, decision: e.target.value as Decision },
                                }))
                              }
                              required
                            >
                              <option value="">需要修复或尚未裁定</option>
                              <option value="not_an_error">结合原文裁定：不是错误</option>
                              <option value="intentional_choice">有意叙事安排，已核对其依据</option>
                            </select>
                          </label>
                          <label>
                            意见 {index + 1} 的裁定理由与原文依据
                            <textarea
                              rows={3}
                              value={answer.reason}
                              onChange={(e) =>
                                setDispositions((rows) => ({
                                  ...rows,
                                  [issue.id]: { ...answer, reason: e.target.value },
                                }))
                              }
                              minLength={10}
                              maxLength={2000}
                              required
                            />
                          </label>
                        </article>
                      )
                    })}
                  </div>
                )}
                {confirmations.map(([key, label]) => (
                  <label className="check" key={key}>
                    <input
                      type="checkbox"
                      checked={checklist[key]}
                      onChange={(e) => setChecklist((c) => ({ ...c, [key]: e.target.checked }))}
                    />
                    <span>{label}</span>
                  </label>
                ))}
                <label>
                  定稿说明、结局与保留事项
                  <textarea
                    rows={4}
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    minLength={10}
                    maxLength={10_000}
                    required
                    placeholder="写明主冲突和人物结局；未回收事项为何有意保留。至少10字。"
                  />
                </label>
                <p className="small muted">
                  逐项确认、完成必要裁定并填写说明后可提交。不需要配置模型，本操作不发起生成调用。
                </p>
                <button type="submit" className="primary" disabled={!ready || busy || loading}>
                  {busy ? '正在记录作者定稿…' : '确认此版本为作者定稿'}
                </button>
              </fieldset>
            </form>
          )}
          <p className="small muted">{data.note}</p>
        </>
      )}
      <p role="status">{status}</p>
    </section>
  )
}
