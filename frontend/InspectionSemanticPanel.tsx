import { useEffect, useRef, useState } from 'react'
import { api, label } from './api'

type Task = { id: string; status: string; error: string; created: number; artifact_id: string | null }
export type InspectionSuggestion = {
  check_id: string
  result: 'pass' | 'fail' | 'unknown'
  reason: string
  evidence: { chapter_id: string; revision: number; start: number; end: number; quote: string }[]
  basis_excerpts: { id: string; label: string; text: string }[]
}
type Candidate = {
  id: string
  current: boolean
  basis_sha256: string
  chapters: { id: string; title: string; revision: number }[]
  items: InspectionSuggestion[]
}
type SourceBasis = { scope: 'book' | 'volume' | 'chapter'; scope_id: string | null; basis_sha256: string }

const results = { pass: '候选相符', fail: '疑似冲突', unknown: '证据不足' }
const titles: Record<string, string> = {
  '01': '立项及作者约定',
  '02': '章纲必达与正文',
  '10': '节奏与衔接',
}

export function InspectionSemanticPanel({
  route,
  account,
  modelReady,
  basis,
  onLoad,
}: {
  route: string
  account: string
  modelReady: boolean
  basis: SourceBasis
  onLoad: (candidateId: string, item: InspectionSuggestion) => void
}) {
  const endpoint = route + '/semantic'
  const [tasks, setTasks] = useState<Task[]>([])
  const [selected, setSelected] = useState('')
  const [candidate, setCandidate] = useState<Candidate>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const submission = useRef<{ signature: string; id: string } | null>(null)
  const selectedTask = tasks.find((task) => task.id === selected)
  const artifactId = selectedTask?.artifact_id
  const running = tasks.some((task) => ['queued', 'running'].includes(task.status))
  const current = candidate?.id === artifactId ? candidate : undefined
  const canUse = current?.current && current.basis_sha256 === basis.basis_sha256
  const epoch = useRef(0)
  const params = new URLSearchParams({ scope: basis.scope })
  if (basis.scope_id) params.set('scope_id', basis.scope_id)
  const historyUrl = `${endpoint}/tasks?${params}`

  function showError(error: unknown) {
    setError(error instanceof Error ? error.message : '语义候选暂不可读。')
  }

  useEffect(() => {
    let active = true
    async function read() {
      const start = epoch.current
      try {
        const records = await api<Task[]>(historyUrl)
        if (!active || start !== epoch.current) return
        setTasks(records)
        setSelected((id) => (records.some((row) => row.id === id) ? id : (records[0]?.id ?? '')))
      } catch (error) {
        if (active) showError(error)
      }
    }
    void read()
    const timer = setInterval(() => void read(), 2500)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [historyUrl])

  useEffect(() => {
    let active = true
    if (artifactId)
      void api<Candidate>(`${endpoint}/${artifactId}`)
        .then((value) => {
          if (active) setCandidate(value)
        })
        .catch((error: unknown) => {
          if (active) showError(error)
        })
    return () => {
      active = false
    }
  }, [endpoint, artifactId, basis.basis_sha256])

  async function launch() {
    setBusy(true)
    setError('')
    const values = { ...basis, account }
    const signature = JSON.stringify(values)
    if (submission.current?.signature !== signature)
      submission.current = { signature, id: crypto.randomUUID() }
    try {
      const task = await api<Task>(endpoint, 'POST', { ...values, client_request_id: submission.current.id })
      epoch.current++
      setTasks((records) => [task, ...records.filter((row) => row.id !== task.id)])
      setSelected(task.id)
      submission.current = null
    } catch (error) {
      showError(error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <details>
      <summary>AI 语义对账候选 · 01、02、10</summary>
      <p className="small">发送本次所选范围的保存正文及对照依据。待核对的建议可载入作者表单。</p>
      <button type="button" disabled={!modelReady || busy || running} onClick={() => void launch()}>
        生成语义对账候选
      </button>
      {error && <p role="alert">{error}</p>}
      {!!tasks.length && (
        <label>
          语义候选记录
          <select value={selected} onChange={(event) => setSelected(event.target.value)}>
            {tasks.map((task) => (
              <option key={task.id} value={task.id}>
                {label(task.status)} · {new Date(task.created * 1000).toLocaleString()}
              </option>
            ))}
          </select>
        </label>
      )}
      {selectedTask?.error && <p role="status">{selectedTask.error}</p>}
      {current && (
        <section aria-label="语义候选证据">
          <p>
            {canUse ? '等待作者核对' : '送审依据已变，保留历史候选'} · 实际送审 {current.chapters.length} 章
          </p>
          <ul>
            {current.chapters.map((chapter) => (
              <li key={chapter.id}>
                {chapter.title} · 正文修订 {chapter.revision}
              </li>
            ))}
          </ul>
          {current.items.map((item) => (
            <article className="fact" key={item.check_id}>
              <h4>
                {item.check_id} {titles[item.check_id]} · {results[item.result]}
              </h4>
              <p>{item.reason}</p>
              {item.basis_excerpts.map((source) => (
                <blockquote key={source.id}>
                  {source.label} · {source.text}
                </blockquote>
              ))}
              {item.evidence.map((source) => (
                <blockquote key={`${source.chapter_id}/${source.start}`}>{source.quote}</blockquote>
              ))}
              <button type="button" disabled={!canUse} onClick={() => onLoad(current.id, item)}>
                载入 {item.check_id} 依据供作者核对
              </button>
            </article>
          ))}
        </section>
      )}
    </details>
  )
}
