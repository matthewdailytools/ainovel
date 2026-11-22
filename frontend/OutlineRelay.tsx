import { useRef, useState } from 'react'
import { api, type Task } from './api'
import { useLeaveGuard } from './leaveGuard'

export type RelayChapter = {
  id: string
  title: string
  position: number
  outline: { source: 'author' | 'frozen_ai'; text: string; version: string } | null
  confirmed: boolean
}
export type RelayClarification = {
  checkpoint_id: string
  clarification: string
  client_request_id: string
}

export function RelayRange({
  chapters,
  first,
  last,
  selectFirst,
  selectLast,
  selected,
  reload,
}: {
  chapters: RelayChapter[]
  first: string
  last: string
  selectFirst: (value: string) => void
  selectLast: (value: string) => void
  selected: RelayChapter[]
  reload: () => void
}) {
  return (
    <section className="notice" aria-label="接力章纲范围">
      <p>
        在写作页保存作者章纲，再从这里选择连续章节。旧批次章纲请先核对原约定，将仍适用的固定条件写进章纲后保存。
      </p>
      <div className="form-grid">
        <label>
          起始章
          <select value={first} onChange={(event) => selectFirst(event.target.value)}>
            <option value="">选择起始章</option>
            {chapters.map((chapter) => (
              <option key={chapter.id} value={chapter.id} disabled={!chapter.confirmed}>
                {chapter.position} · {chapter.title}
                {!chapter.confirmed && (chapter.outline ? ' · 待重新确认章纲' : ' · 尚无作者章纲')}
              </option>
            ))}
          </select>
        </label>
        <label>
          结束章
          <select value={last} onChange={(event) => selectLast(event.target.value)}>
            <option value="">选择结束章</option>
            {chapters.map((chapter) => (
              <option key={chapter.id} value={chapter.id} disabled={!chapter.confirmed}>
                {chapter.position} · {chapter.title}
                {!chapter.confirmed && (chapter.outline ? ' · 待重新确认章纲' : ' · 尚无作者章纲')}
              </option>
            ))}
          </select>
        </label>
      </div>
      <button type="button" onClick={reload}>
        重新读取章纲
      </button>
      {selected.length > 0 && (
        <details>
          <summary>核对接力范围（{selected.length} 章）</summary>
          {selected.map((chapter) => (
            <details key={chapter.id}>
              <summary>{chapter.title}</summary>
              <blockquote>{chapter.outline?.text}</blockquote>
            </details>
          ))}
        </details>
      )}
    </section>
  )
}

export function RelayDecision({
  task,
  checkpoint,
  disabled,
  clarify,
}: {
  task: Task
  checkpoint: NonNullable<Task['result']['outline_relay_waiting']>
  disabled: boolean
  clarify: (body: RelayClarification) => Promise<void>
}) {
  const [clarification, setClarification] = useState('')
  const pending = useRef<{ text: string; id: string } | null>(null)
  useLeaveGuard(!!clarification.trim(), '章纲接力待提交的作者决定')
  return (
    <form
      className="notice"
      aria-label="章纲接力待作者决定"
      onSubmit={(event) => {
        event.preventDefault()
        const text = clarification.trim()
        if (!text || disabled) return
        if (pending.current?.text !== text) pending.current = { text, id: crypto.randomUUID() }
        void clarify({
          checkpoint_id: checkpoint.id,
          clarification: text,
          client_request_id: pending.current.id,
        })
      }}
    >
      <h3>第 {checkpoint.number} 章待作者决定</h3>
      <p>{task.error}</p>
      {checkpoint.contract.note && <p>{checkpoint.contract.note}</p>}
      <ul>
        {checkpoint.contract.items
          .filter((item) => item.certainty === 'ambiguous')
          .map((item, index) => (
            <li key={index}>{item.author_evidence}</li>
          ))}
      </ul>
      <label>
        作者明确补充
        <textarea
          value={clarification}
          onChange={(event) => setClarification(event.target.value)}
          rows={3}
          maxLength={4000}
        />
      </label>
      <p className="small">补充会与原章纲一同核对，继续使用本批次预算。</p>
      <button type="submit" className="primary" disabled={disabled || !clarification.trim()}>
        采用补充并继续接力
      </button>
    </form>
  )
}

export function submitRelayDecision(task: Task, data: RelayClarification) {
  return api<Task>(`/tasks/${task.id}/outline-relay/clarify`, 'POST', data)
}
