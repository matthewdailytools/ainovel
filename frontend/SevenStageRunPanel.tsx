import { useCallback, useEffect, useRef, useState } from 'react'
import { api, label, type Task } from './api'
import type { Notify } from './App'
import type { ConceptionState } from './conception'

type StartFrom = 'idea' | 'settings' | 'outline'
type OutlineState = {
  confirmed: { id: string; revision: number } | null
  draft: unknown
  stale: string[]
  chapters: { id: string }[]
}
type Stage = 'project' | 'settings' | 'outline' | 'writing' | 'inspection' | 'completion' | 'done'
type Run = Task & {
  result: {
    seven_stage_run: {
      stage: Stage
      child_id?: string
      completed_chapters: { id: string; revision: number; existing?: boolean }[]
      outline_id?: string
      inspection_id?: string
      completion_id?: string
    }
  }
}
const stageLabels: Record<Stage, string> = {
  project: '立项',
  settings: '设定',
  outline: '大纲',
  writing: '写作',
  inspection: '校验',
  completion: '完本',
  done: '已核对完本',
}
const runStatus: Record<string, string> = {
  waiting_child: '子任务运行或排队中',
  needs_review: '等待处理检查点',
  succeeded: '已完成接力',
}
const live = ['queued', 'running', 'waiting_child']
const unresolved = [...live, 'paused', 'needs_review']

export function SevenStageRunPanel({
  branchId,
  account,
  modelReady,
  notify,
}: {
  branchId: string
  account: string
  modelReady: boolean
  notify: Notify
}) {
  const prefix = `/branches/${encodeURIComponent(branchId)}`
  const endpoint = `${prefix}/seven-stage-runs`
  const [outline, setOutline] = useState<OutlineState>()
  const [settings, setSettings] = useState<ConceptionState>()
  const [runs, setRuns] = useState<Run[]>([])
  const [runId, setRunId] = useState('')
  const [from, setFrom] = useState<StartFrom>()
  const [idea, setIdea] = useState('')
  const [chapters, setChapters] = useState('2')
  const [budget, setBudget] = useState('')
  const [autoConfirm, setAutoConfirm] = useState(false)
  const [autoAdopt, setAutoAdopt] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [pollError, setPollError] = useState('')
  const alive = useRef(true)
  const pending = useRef<{ signature: string; key: string } | undefined>(undefined)

  const refreshSources = useCallback(async () => {
    const [concept, plan] = await Promise.all([
      api<ConceptionState>(`${prefix}/conception`),
      api<OutlineState>(`${prefix}/outline-workspace`),
    ])
    if (!alive.current) return
    setSettings(concept)
    setOutline(plan)
  }, [prefix])

  useEffect(() => {
    alive.current = true
    void refreshSources().catch((failure: Error) => {
      if (alive.current) setError(failure.message)
    })
    return () => {
      alive.current = false
    }
  }, [refreshSources])
  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const results = await api<Run[]>(endpoint)
        if (active) {
          setRuns(results)
          setPollError('')
        }
      } catch (failure) {
        if (active) setPollError(failure instanceof Error ? failure.message : '接力记录读取失败。')
      } finally {
        if (active) timer = setTimeout(() => void poll(), 1700)
      }
    }
    void poll()
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [endpoint])

  const validSettings = settings?.confirmation?.current ? settings.confirmation : undefined
  const validOutline = outline && !outline.stale.length ? outline.confirmed : undefined
  const blank =
    settings?.revision === 0 && !settings.project && outline && !outline.chapters.length && !outline.draft
  const origin = from ?? (validOutline ? 'outline' : validSettings ? 'settings' : 'idea')
  const chapterCount = origin === 'idea' || (origin === 'settings' && !outline?.chapters.length)
  const run = runs.find((entry) => entry.id === runId) ?? runs[0]
  const activeRun = runs.some((entry) => unresolved.includes(entry.status))
  const status = run?.result.seven_stage_run
  const permitted =
    origin === 'idea' ? !!blank && !!idea.trim() : origin === 'settings' ? !!validSettings : !!validOutline
  const ready =
    permitted &&
    modelReady &&
    Number.isFinite(Number(budget)) &&
    Number(budget) > 0 &&
    (!chapterCount ||
      (Number.isInteger(Number(chapters)) && Number(chapters) >= 1 && Number(chapters) <= 100))

  function setEntry(entry: Run) {
    setRuns((rows) => [entry, ...rows.filter((row) => row.id !== entry.id)])
    setRunId(entry.id)
  }

  async function execute(operation: () => Promise<void>) {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await operation()
    } catch (failure) {
      if (!alive.current) return
      setError(failure instanceof Error ? failure.message : '接力操作未完成，请重试。')
      notify(failure)
    } finally {
      if (alive.current) setBusy(false)
    }
  }

  async function start() {
    if (!ready || activeRun) return
    const body = {
      start_from: origin,
      ...(origin === 'idea'
        ? { idea: idea.trim() }
        : {
            source_id: origin === 'settings' ? validSettings?.id : validOutline?.id,
          }),
      ...(chapterCount ? { chapter_count: Number(chapters) } : {}),
      account,
      budget_limit: Number(budget),
      auto_confirm_planning: autoConfirm,
      auto_adopt_body: autoAdopt,
    }
    const signature = JSON.stringify(body)
    if (pending.current?.signature !== signature) pending.current = { signature, key: crypto.randomUUID() }
    const entry = await api<Run>(endpoint, 'POST', { ...body, client_request_id: pending.current.key })
    if (alive.current) {
      pending.current = undefined
      setEntry(entry)
    }
  }

  async function control(action: 'pause' | 'resume' | 'cancel') {
    if (!run) return
    const entry = await api<Run>(`${endpoint}/${encodeURIComponent(run.id)}/${action}`, 'POST')
    if (alive.current) setEntry(entry)
  }

  return (
    <section className="card" aria-label="七台自动接力">
      <h2>七台自动接力</h2>
      <p>从创意或采用版本启动。拆解仍可按需单独使用。遇到需作者判定的体检或完本事项时，运行会在该处等待。</p>
      <div className="actions">
        <button type="button" disabled={busy} onClick={() => void execute(refreshSources)}>
          核对接力起点
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
      {pollError && <p role="alert">{pollError}</p>}
      {!modelReady && <p className="small">配置可用模型账户与单价后即可启动。</p>}
      <fieldset disabled={!settings || !outline || busy || activeRun}>
        <legend>启动范围与授权</legend>
        <label>
          接力起点
          <select value={origin} onChange={(event) => setFrom(event.target.value as StartFrom)}>
            <option value="idea" disabled={!blank}>
              从创意开始
            </option>
            <option value="settings" disabled={!validSettings}>
              从已确认设定接力{validSettings && ` · 修订 ${validSettings.revision}`}
            </option>
            <option value="outline" disabled={!validOutline}>
              从已确认大纲接力{validOutline && ` · 修订 ${validOutline.revision}`}
            </option>
          </select>
        </label>
        {!blank && !validSettings && !validOutline && (
          <p>请先确认设定或大纲。只有新建空白作品可从创意启动。</p>
        )}
        {origin === 'idea' && (
          <label>
            自动立项的创作思路
            <textarea
              rows={3}
              value={idea}
              maxLength={6000}
              onChange={(event) => setIdea(event.target.value)}
            />
          </label>
        )}
        {chapterCount && (
          <label>
            新建章节数
            <input
              type="number"
              value={chapters}
              min={1}
              max={100}
              onChange={(event) => setChapters(event.target.value)}
            />
          </label>
        )}
        <label>
          本次接力总预算（所选账户币种）
          <input
            type="number"
            min={0.000001}
            step="any"
            value={budget}
            onChange={(event) => setBudget(event.target.value)}
          />
        </label>
        {origin !== 'outline' && (
          <label className="check">
            <input
              type="checkbox"
              checked={autoConfirm}
              onChange={(event) => setAutoConfirm(event.target.checked)}
            />
            授权确认本次生成的规划产物
          </label>
        )}
        <label className="check">
          <input
            type="checkbox"
            checked={autoAdopt}
            onChange={(event) => setAutoAdopt(event.target.checked)}
          />
          授权采纳通过检查的空章正文候选
        </label>
        <button className="primary" type="button" disabled={!ready} onClick={() => void execute(start)}>
          启动七台接力
        </button>
      </fieldset>
      {run && status && (
        <section aria-label="接力检查点">
          <h3>接力检查点</h3>
          {runs.length > 1 && (
            <label>
              接力记录
              <select value={run.id} onChange={(event) => setRunId(event.target.value)}>
                {runs.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {new Date(entry.created * 1000).toLocaleString('zh-CN')} ·{' '}
                    {runStatus[entry.status] ?? label(entry.status)}
                  </option>
                ))}
              </select>
            </label>
          )}
          <p role="status">
            {stageLabels[status.stage]} · {runStatus[run.status] ?? label(run.status)}
          </p>
          {run.error && <p>{run.error}</p>}
          {!!status.completed_chapters.length && (
            <p>本次范围已核对 {status.completed_chapters.length} 章，原有正文保留。</p>
          )}
          {status.child_id && (
            <p className="small">子任务 {status.child_id.slice(0, 8)}，可在对应工作台查阅产物。</p>
          )}
          <div className="actions">
            {live.includes(run.status) && (
              <button type="button" disabled={busy} onClick={() => void execute(() => control('pause'))}>
                在检查点暂停
              </button>
            )}
            {['paused', 'needs_review', 'blocked_runtime'].includes(run.status) && (
              <button type="button" disabled={busy} onClick={() => void execute(() => control('resume'))}>
                继续接力
              </button>
            )}
            {run.status !== 'succeeded' && run.status !== 'cancelled' && (
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  if (window.confirm('停止这次接力及当前子任务？已保存的产物和费用记录将保留。'))
                    void execute(() => control('cancel'))
                }}
              >
                停止本次接力
              </button>
            )}
          </div>
        </section>
      )}
    </section>
  )
}
