import { useEffect, useRef, useState } from 'react'
import { api, type Artifact, type Chapter, type Task } from './api'
import type { Notify } from './App'

type Finding = {
  chapter: number
  severity: 'blocker' | 'suggestion'
  quote: string
  reason: string
  repair: string
}

function object(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

function string(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function finding(value: unknown): value is Finding {
  return (
    object(value) &&
    typeof value.chapter === 'number' &&
    (value.severity === 'blocker' || value.severity === 'suggestion') &&
    typeof value.quote === 'string' &&
    typeof value.reason === 'string' &&
    typeof value.repair === 'string'
  )
}

function Resolution({ value }: { value: unknown }) {
  if (!object(value)) return null
  const result = string(value.result)
  const index = typeof value.finding_index === 'number' ? value.finding_index + 1 : '?'
  const labels: Record<string, string> = { pass: '已回应', fail: '仍未解决', unknown: '尚不能确定' }
  return (
    <article className="excerpt">
      <strong>
        原问题 {index} · {labels[result] || result}
      </strong>
      <p>{string(value.reason)}</p>
      {!!value.evidence && <blockquote>{string(value.evidence)}</blockquote>}
    </article>
  )
}

function RevisionTrace({ trace }: { trace: Record<string, unknown> }) {
  const selected = Array.isArray(trace.selected_findings) ? trace.selected_findings : []
  const rounds = Array.isArray(trace.rounds) ? trace.rounds : []
  const resolutions = Array.isArray(trace.original_problem_recheck) ? trace.original_problem_recheck : []
  return (
    <section className="notice" aria-label="定向修订与原问题复检">
      <h4>定向修订与原问题复检</h4>
      <p className="small">
        来源报告 {string(trace.source_review_id)} · 基线修订 {String(trace.base_revision ?? '?')} · 已用{' '}
        {String(trace.rounds_used ?? 0)} / 2 轮局部修订。生成时只保存候选，没有自动改动正文。
      </p>
      <details>
        <summary>本次选中的原问题</summary>
        {selected.map((row, index) =>
          object(row) ? (
            <article className="excerpt" key={index}>
              <strong>
                问题 {typeof row.index === 'number' ? row.index + 1 : index + 1} ·{' '}
                {row.severity === 'suggestion' ? '作者选定的偏好建议' : '原报告标记的阻断问题'}
              </strong>
              <blockquote>{string(row.quote)}</blockquote>
              <p>{string(row.reason)}</p>
              <p>原建议：{string(row.repair)}</p>
            </article>
          ) : null,
        )}
      </details>
      {rounds.map((round, index) =>
        object(round) ? (
          <details key={index}>
            <summary>第 {index + 1} 轮局部修法与逐项复检</summary>
            {Array.isArray(round.changes) &&
              round.changes.map((change, position) =>
                object(change) ? (
                  <article className="excerpt" key={position}>
                    <p>原段落：</p>
                    <blockquote>{string(change.original) || '（在此处插入）'}</blockquote>
                    <p>修订段落：</p>
                    <blockquote>{string(change.replacement) || '（删除此段）'}</blockquote>
                  </article>
                ) : null,
              )}
            {Array.isArray(round.original_problem_recheck) &&
              round.original_problem_recheck.map((row, position) => (
                <Resolution key={position} value={row} />
              ))}
          </details>
        ) : null,
      )}
      <h4>原问题最终复检</h4>
      {resolutions.map((row, index) => (
        <Resolution key={index} value={row} />
      ))}
      <p className="small muted">
        原问题已回应也可能仍有其他审稿项未通过；偏好是否更好由作者判断，不代表整书验收通过。
      </p>
      {trace.stop_reason === 'repair_scope_unavailable' && (
        <p role="status">
          复检未能提供唯一的局部修订位置，已保留当前候选和记录并停止自动返修。请先重审取得更小的原文定位。
        </p>
      )}
    </section>
  )
}

export function RevisionTraceDetails({ trace }: { trace: unknown }) {
  return object(trace) ? <RevisionTrace trace={trace} /> : null
}

export function RevisionPanel({
  review,
  chapter,
  novelId,
  account,
  modelReady,
  beforeRun,
  onStarted,
  notify,
  disabled = false,
}: {
  review: Artifact
  chapter?: Chapter
  novelId: string
  account: string
  modelReady: boolean
  beforeRun: () => Promise<Chapter | undefined>
  onStarted: (task: Task) => void | Promise<void>
  notify: Notify
  disabled?: boolean
}) {
  const [selected, setSelected] = useState<number[]>([])
  const [instruction, setInstruction] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const pending = useRef<{ signature: string; key: string } | null>(null)
  useEffect(() => {
    setSelected([])
    setInstruction('')
    setMessage('')
    pending.current = null
  }, [review.id, chapter?.id, chapter?.revision])
  const raw = Array.isArray(review.payload.findings) ? review.payload.findings : []
  const findings = raw.flatMap((value, index) => (finding(value) ? [{ value, index }] : []))
  const checks = Array.isArray(review.payload.checks) ? review.payload.checks.filter(object) : []
  const stale = !chapter || review.chapter_id !== chapter.id || review.base_revision !== chapter.revision
  if (object(review.payload.revision_trace))
    return <RevisionTraceDetails trace={review.payload.revision_trace} />
  if (review.kind !== 'review') {
    return review.payload.review ? (
      <p className="small muted">此检查针对未采纳候选。定向修订请先对已保存正文生成独立审稿报告。</p>
    ) : null
  }
  async function generate() {
    if (busy || disabled || stale || !selected.length || !chapter) return
    setBusy(true)
    setMessage('')
    try {
      const saved = await beforeRun()
      if (!saved || saved.id !== review.chapter_id || saved.revision !== review.base_revision)
        throw new Error('刚保存的正文已改变，来源审稿报告已过期。请先重新审稿，原稿已保留。')
      const fixed = Array.isArray(review.payload.fixed)
        ? review.payload.fixed.filter((v): v is string => typeof v === 'string')
        : []
      const values = {
        action: 'revision.targeted',
        novel_id: novelId,
        branch_id: saved.branch_id,
        chapter_id: saved.id,
        base_revision: saved.revision,
        source_review_id: review.id,
        finding_indices: [...selected].sort((a, b) => a - b),
        instruction: instruction.trim() || '仅对选中审稿问题作必要的局部修订，保留原定情节与结果。',
        fixed,
        account,
      }
      const signature = JSON.stringify(values)
      if (pending.current?.signature !== signature) pending.current = { signature, key: crypto.randomUUID() }
      const task = await api<Task>('/modules', 'POST', { ...values, client_request_id: pending.current.key })
      pending.current = null
      await onStarted(task)
      setMessage('已创建定向修订任务；候选生成及复检均单独保存，正文尚未改变。')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '定向修订未启动，请保留当前输入后重试。')
      notify(error)
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="notice" aria-label="选择审稿问题并定向修订">
      <h4>选择问题，生成最小修订候选</h4>
      <p className="small muted">
        仅处理你选择的问题，最多两轮局部修订并逐项复检。审美建议可以不选；原稿及本报告保留。
      </p>
      {!!checks.length && (
        <details>
          <summary>报告中的条件检查 · {checks.length} 项</summary>
          {checks.map((check, index) => (
            <article className="excerpt" key={index}>
              <strong>
                {string(check.condition)} ·{' '}
                {check.result === 'pass' ? '通过' : check.result === 'fail' ? '未通过' : '尚不能确定'}
              </strong>
              {!!check.evidence && <blockquote>{string(check.evidence)}</blockquote>}
            </article>
          ))}
        </details>
      )}
      {stale && <p role="status">此报告不对应当前保存稿，请先对当前正文重新审稿。</p>}
      {review.status === 'rejected' && <p role="status">此报告已拒绝，请重新审稿后选择。</p>}
      {!findings.length && (
        <p>此报告没有可选择的定位问题。若条件仍未通过，请先重新审稿，要求给出具体位置与修法。</p>
      )}
      {findings.map(({ value, index }) => {
        const unique =
          !!chapter &&
          !!value.quote &&
          chapter.content.indexOf(value.quote) >= 0 &&
          chapter.content.indexOf(value.quote) === chapter.content.lastIndexOf(value.quote)
        const localRange =
          unique &&
          [...value.quote].length <= 800 &&
          !!value.quote.trim() &&
          value.quote.trim() !== chapter?.content.trim()
        return (
          <article className="excerpt" key={index}>
            <label className="check">
              <input
                type="checkbox"
                checked={selected.includes(index)}
                disabled={
                  busy ||
                  disabled ||
                  stale ||
                  !localRange ||
                  (!selected.includes(index) && selected.length >= 20)
                }
                onChange={(event) =>
                  setSelected((rows) =>
                    event.target.checked ? [...rows, index] : rows.filter((row) => row !== index),
                  )
                }
              />
              问题 {index + 1} · {value.severity === 'blocker' ? '报告标记为阻断' : '可选偏好建议'}
            </label>
            <blockquote>{value.quote}</blockquote>
            <p>{value.reason}</p>
            <p>建议修法：{value.repair || '可在下方补充本次修订要求。'}</p>
            {!unique && <p className="small">引文未唯一定位到当前正文，请重新审稿取得明确位置。</p>}
            {unique && !localRange && (
              <p className="small">引文覆盖整章或超过800字，请先重审取得更小的原文定位。</p>
            )}
          </article>
        )
      })}
      <label>
        本次修订补充要求（可选）
        <textarea
          rows={3}
          value={instruction}
          onChange={(event) => setInstruction(event.target.value)}
          maxLength={10_000}
        />
      </label>
      <p className="small">
        已选择 {selected.length} / 20 项 · 执行账户：{account}
      </p>
      <button
        type="button"
        disabled={
          busy || disabled || !modelReady || stale || review.status === 'rejected' || !selected.length
        }
        onClick={() => void generate()}
      >
        {busy ? '正在保存并校验来源…' : '生成选中问题的最小修订候选'}
      </button>
      {!modelReady && <p className="small">请先配置可用模型账户与单价。</p>}
      {message && <p role="status">{message}</p>}
    </section>
  )
}
