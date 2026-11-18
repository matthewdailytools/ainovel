import { useCallback, useEffect, useRef, useState } from 'react'
import { api, label, type Task } from './api'
import type { Notify } from './App'
import type { ConceptionState } from './conception'

type StartFrom = 'idea' | 'project' | 'settings' | 'outline' | 'writing' | 'inspection'
type ReviewSources = { writing_id: string | null; inspection_id: string | null }
type OutlineState = {
  confirmed: { id: string; revision: number } | null
  draft: unknown
  stale: string[]
  chapters: { id: string }[]
}
type ReferenceOption = {
  id: string
  revision: number
  payload: { goal: string; source: { title: string; quote: string } }
}
type Stage =
  'project' | 'reference' | 'settings' | 'outline' | 'writing' | 'inspection' | 'completion' | 'done'
type Run = Task & {
  result: {
    seven_stage_run: {
      stage: Stage
      child_id?: string
      child_recovery?: { state: string; dispatch_event_ids: number[] }
      completed_chapters: { id: string; revision: number; existing?: boolean }[]
      outline_id?: string
      inspection_id?: string
      completion_id?: string
      reference_analysis_id?: string
      reference_basis?: { revision: number; goal: string; source: { title: string } }
      reference_references?: { card_id: string; revision: number }[]
    }
  }
}
const stageLabels: Record<Stage, string> = {
  project: '立项',
  reference: '拆解',
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
  const [reviewSources, setReviewSources] = useState<ReviewSources>()
  const [references, setReferences] = useState<ReferenceOption[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  const [runId, setRunId] = useState('')
  const [from, setFrom] = useState<StartFrom>()
  const [idea, setIdea] = useState('')
  const [chapters, setChapters] = useState('2')
  const [budget, setBudget] = useState('')
  const [autoConfirm, setAutoConfirm] = useState(false)
  const [autoAdopt, setAutoAdopt] = useState(false)
  const [referenceChoice, setReferenceChoice] = useState('')
  const [autoReference, setAutoReference] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [pollError, setPollError] = useState('')
  const alive = useRef(true)
  const pending = useRef<{ signature: string; key: string } | undefined>(undefined)

  const refreshSources = useCallback(async () => {
    const [concept, plan, savedReferences, sources] = await Promise.all([
      api<ConceptionState>(`${prefix}/conception`),
      api<OutlineState>(`${prefix}/outline-workspace`),
      api<ReferenceOption[]>(`/reference-sessions?branch_id=${encodeURIComponent(branchId)}`),
      api<ReviewSources>(`${endpoint}/sources`),
    ])
    if (!alive.current) return
    setSettings(concept)
    setOutline(plan)
    setReviewSources(sources)
    setReferences(
      savedReferences.filter((entry) => entry.payload.goal.trim() && entry.payload.source.quote.trim()),
    )
  }, [branchId, endpoint, prefix])

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

  const validProject = settings?.project
  const validSettings = settings?.confirmation?.current ? settings.confirmation : undefined
  const validOutline = outline && !outline.stale.length ? outline.confirmed : undefined
  const blank =
    settings?.revision === 0 && !settings.project && outline && !outline.chapters.length && !outline.draft
  const origin =
    from ?? (validOutline ? 'outline' : validSettings ? 'settings' : validProject ? 'project' : 'idea')
  const sources = {
    project: validProject?.id,
    settings: validSettings?.id,
    outline: validOutline?.id,
    writing: reviewSources?.writing_id,
    inspection: reviewSources?.inspection_id,
  }
  const plansRemaining = origin === 'idea' || origin === 'project' || origin === 'settings'
  const writingRemaining = plansRemaining || origin === 'outline'
  const chapterCount = plansRemaining && !outline?.chapters.length
  const run = runs.find((entry) => entry.id === runId) ?? runs[0]
  const reference = plansRemaining
    ? references.find((entry) => `${entry.id}/${entry.revision}` === referenceChoice)
    : undefined
  const referenceChanged = plansRemaining && !!referenceChoice && !reference
  const activeRun = runs.some((entry) => unresolved.includes(entry.status))
  const status = run?.result.seven_stage_run
  const permitted = origin === 'idea' ? !!blank && !!idea.trim() : !!sources[origin]
  const ready =
    permitted &&
    modelReady &&
    !referenceChanged &&
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
      ...(origin === 'idea' ? { idea: idea.trim() } : { source_id: sources[origin] }),
      ...(chapterCount ? { chapter_count: Number(chapters) } : {}),
      account,
      budget_limit: Number(budget),
      auto_confirm_planning: plansRemaining && autoConfirm,
      auto_adopt_body: writingRemaining && autoAdopt,
      ...(reference && {
        reference_session_id: reference.id,
        reference_revision: reference.revision,
        auto_adopt_reference: autoReference,
      }),
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
      <p>
        从创意或采用版本启动。可先拆解明确选中的资料。遇到需作者判定的体检或完本事项时，运行会在该处等待。
      </p>
      <p className="small">
        按阶段把选定资料和保存稿发送给当前账户。开篇简介只参考首章，最终完本由作者确认。
      </p>
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
            <option value="project" disabled={!validProject}>
              从已确认立项接力{validProject && ` · 修订 ${validProject.revision}`}
            </option>
            <option value="settings" disabled={!validSettings}>
              从已确认设定接力{validSettings && ` · 修订 ${validSettings.revision}`}
            </option>
            <option value="outline" disabled={!validOutline}>
              从已确认大纲接力{validOutline && ` · 修订 ${validOutline.revision}`}
            </option>
            <option value="writing" disabled={!sources.writing}>
              从已保存的完整正文接力
            </option>
            <option value="inspection" disabled={!sources.inspection}>
              从已通过的全书体检接力
            </option>
          </select>
        </label>
        {!blank && !Object.values(sources).some(Boolean) && (
          <p>请先确认一份立项，或保存符合当前创作范围的正式版本。</p>
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
        {plansRemaining && (
          <div>
            <label>
              自动拆解的保存选段（可选）
              <select
                value={referenceChoice}
                onChange={(event) => {
                  setReferenceChoice(event.target.value)
                  setAutoReference(false)
                }}
              >
                <option value="">跳过拆解</option>
                {referenceChanged && <option value={referenceChoice}>所选拆解修订已更新，请重新选择</option>}
                {references.map(({ id, revision, payload }) => (
                  <option key={id} value={`${id}/${revision}`}>
                    {payload.source.title} · {payload.goal} · 修订 {revision}
                  </option>
                ))}
              </select>
            </label>
            {reference && (
              <>
                <details>
                  <summary>查看本次发送的原文选段</summary>
                  <blockquote>{reference.payload.source.quote}</blockquote>
                </details>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={autoReference}
                    onChange={(event) => setAutoReference(event.target.checked)}
                  />
                  授权保存并选用写法、适用条件和须替换内容齐全的手法卡
                </label>
              </>
            )}
          </div>
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
        {plansRemaining && (
          <label className="check">
            <input
              type="checkbox"
              checked={autoConfirm}
              onChange={(event) => setAutoConfirm(event.target.checked)}
            />
            授权确认本次生成的规划产物
          </label>
        )}
        {writingRemaining && (
          <label className="check">
            <input
              type="checkbox"
              checked={autoAdopt}
              onChange={(event) => setAutoAdopt(event.target.checked)}
            />
            授权采纳通过检查的空章正文候选
          </label>
        )}
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
          {status.reference_basis && (
            <p>
              拆解依据 {status.reference_basis.source.title} · 修订 {status.reference_basis.revision} ·{' '}
              {status.reference_basis.goal}
              {status.reference_references && ` · 已采用 ${status.reference_references.length} 张手法卡`}
            </p>
          )}
          {!!status.completed_chapters.length && (
            <p>本次范围已核对 {status.completed_chapters.length} 章，原有正文保留。</p>
          )}
          {status.child_id && (
            <p className="small">子任务 {status.child_id.slice(0, 8)}，可在对应工作台查阅产物。</p>
          )}
          {status.child_id && status.child_recovery?.state === 'unknown' && (
            <p className="notice">
              暂缺派发回执 {status.child_recovery.dispatch_event_ids.join('、')}。请核对账本与
              <a
                href={`/api/tasks/${encodeURIComponent(status.child_id)}/transcript`}
                target="_blank"
                rel="noreferrer"
              >
                已完成步骤及保留片段
              </a>
              。
            </p>
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
