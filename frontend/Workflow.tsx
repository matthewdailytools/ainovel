import { useEffect, useRef, useState } from 'react'
import { api, label, type Artifact, type Novel, type Task, type WorkflowInput } from './api'
import type { Notify } from './App'
import story from '../fixtures/original-story.json'

export function WorkflowPage({
  novel,
  branchId,
  account,
  modelReady,
  notify,
  onStarted,
}: {
  novel: Novel
  branchId: string
  account: string
  modelReady: boolean
  notify: Notify
  onStarted: (id: string) => Promise<void>
}) {
  const [mode, setMode] = useState<'assisted' | 'automatic'>('assisted')
  const [brief, setBrief] = useState(novel.brief)
  const [fixed, setFixed] = useState('')
  const [plots, setPlots] = useState('')
  const [count, setCount] = useState(36)
  const [minChars, setMinChars] = useState(2500)
  const [maxChars, setMaxChars] = useState(3500)
  const [adoption, setAdoption] = useState<'manual' | 'automatic'>('manual')
  const [profile, setProfile] = useState(
    '第三人称限定视角，克制白话，动作有因果，人物选择遵循当时掌握的信息与资源。',
  )
  const [runLimit, setRunLimit] = useState('')
  const [tasks, setTasks] = useState<Task[]>([])
  const [busy, setBusy] = useState(false)
  const pendingRequest = useRef<{ signature: string; key: string } | null>(null)
  const [selectedTask, setSelectedTask] = useState('')
  const [waiting, setWaiting] = useState<Artifact>()
  const [unknown, setUnknown] = useState(false)
  const [changes, setChanges] = useState(false)
  const [regenerate, setRegenerate] = useState(false)
  const [retryReason, setRetryReason] = useState('')
  const [status, setStatus] = useState('')
  async function refresh() {
    const rows = (await api<Task[]>('/tasks')).filter((t) => t.kind === 'workflow' && t.novel_id === novel.id)
    setTasks(rows)
  }
  useEffect(() => {
    let active = true
    const poll = async () => {
      try {
        const rows = (await api<Task[]>('/tasks')).filter(
          (t) => t.kind === 'workflow' && t.novel_id === novel.id,
        )
        if (active) setTasks(rows)
      } catch (e) {
        if (active) notify(e)
      }
    }
    void poll()
    const timer = setInterval(() => void poll(), 2500)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [novel.id, notify])
  const task =
    tasks.find((t) => t.id === selectedTask) || tasks.find((t) => t.branch_id === branchId) || tasks[0]
  useEffect(() => {
    let active = true
    setWaiting(undefined)
    const id = task?.result.waiting_artifact
    const poll = () => {
      if (!id) return
      void api<Artifact>(`/artifacts/${id}`)
        .then((a) => {
          if (active) setWaiting(a)
        })
        .catch((e) => {
          if (active) notify(e)
        })
    }
    poll()
    const timer = id ? setInterval(poll, 2500) : undefined
    return () => {
      active = false
      if (timer) clearInterval(timer)
    }
  }, [task?.result.waiting_artifact, task?.status, notify])
  async function perform(fn: () => Promise<unknown>) {
    if (busy) return
    setBusy(true)
    try {
      await fn()
    } catch (e) {
      notify(e)
    } finally {
      setBusy(false)
    }
  }
  async function start(event: React.FormEvent) {
    event.preventDefault()
    await perform(async () => {
      const parsed =
        mode === 'assisted'
          ? plots
              .split('\n')
              .filter((line) => line.trim())
              .map((line, i) => {
                const [title, plot, fixed = ''] = line.split('|')
                if (!title?.trim() || !plot?.trim())
                  throw new Error(`第 ${i + 1} 行需要「标题 | 情节 | 本章固定条件」。`)
                return {
                  number: i + 1,
                  title: title.trim(),
                  plot: plot.trim(),
                  fixed: fixed
                    .split('；')
                    .map((v) => v.trim())
                    .filter(Boolean),
                }
              })
          : []
      const payload: WorkflowInput = {
        novel_id: novel.id,
        source_branch_id: branchId || null,
        mode,
        brief,
        fixed: fixed
          .split('\n')
          .map((v) => v.trim())
          .filter(Boolean),
        plots: parsed,
        chapters: count,
        min_chars: minChars,
        max_chars: maxChars,
        account,
        adoption,
        writing_profile: profile,
        budget_limit: runLimit ? Number(runLimit) : null,
      }
      const signature = JSON.stringify(payload)
      if (pendingRequest.current?.signature !== signature)
        pendingRequest.current = { signature, key: crypto.randomUUID() }
      payload.client_request_id = pendingRequest.current.key
      const row = await api<Task>('/workflows', 'POST', payload)
      setTasks((tasks) => [row, ...tasks.filter((task) => task.id !== row.id)])
      await onStarted(row.branch_id)
      setSelectedTask(row.id)
      await refresh()
      pendingRequest.current = null
      setStatus('已创建独立工作版本。执行记录、费用和干预不会与另一模式混淆。')
    })
  }
  return (
    <div className="page-pad workflow-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">从情节，到一部小说</span>
          <h1>整书创作</h1>
          <p>创作分工、采纳方式和任务范围分别设置。每次启动都建立独立工作版本。</p>
        </div>
        <button
          onClick={() => {
            setBrief(story.brief)
            setFixed(story.fixed.join('\n'))
            setCount(36)
            setPlots(story.chapters.map((p) => `${p.title} | ${p.plot} | ${p.fixed.join('；')}`).join('\n'))
            setStatus('已载入原创验收简报《雾港潮尺》。尚未提交，不会产生费用。')
          }}
        >
          载入原创验收简报
        </button>
      </div>
      <p role="status">{status}</p>
      <div className="workflow-grid">
        <section className="card">
          <form onSubmit={start}>
            <fieldset>
              <legend>创作分工</legend>
              <label className="radio-card">
                <input type="radio" checked={mode === 'assisted'} onChange={() => setMode('assisted')} />
                <span>
                  <strong>半自动</strong>
                  <small>你定章级或批量情节，AI 补写、检查并维护状态。</small>
                </span>
              </label>
              <label className="radio-card">
                <input type="radio" checked={mode === 'automatic'} onChange={() => setMode('automatic')} />
                <span>
                  <strong>全自动</strong>
                  <small>你给设定与粗纲，系统细化章纲，无需逐章填写情节。</small>
                </span>
              </label>
            </fieldset>
            <label>
              作品设定、粗纲与结局方向
              <textarea
                rows={7}
                value={brief}
                onChange={(e) => setBrief(e.target.value)}
                required
                maxLength={30_000}
              />
            </label>
            <label>
              全书固定条件（每行一条）
              <textarea rows={3} value={fixed} onChange={(e) => setFixed(e.target.value)} />
            </label>
            {mode === 'assisted' && (
              <label>
                作者批量情节（每行一章）
                <textarea
                  className="plot-input"
                  rows={10}
                  value={plots}
                  onChange={(e) => setPlots(e.target.value)}
                  required
                  placeholder="标题 | 本章情节与结果 | 本章固定条件；另一条条件"
                />
                <small>
                  行数需等于章数，按行编号。固定结局条件写在相应章节，避免要求每一章提前完成全书结局。
                </small>
              </label>
            )}
            <div className="form-grid">
              <label>
                计划章数
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={count}
                  onChange={(e) => setCount(Number(e.target.value))}
                  required
                />
              </label>
              <label>
                每章字数下限
                <input
                  type="number"
                  min={200}
                  max={10000}
                  value={minChars}
                  onChange={(e) => setMinChars(Number(e.target.value))}
                  required
                />
              </label>
              <label>
                每章字数上限
                <input
                  type="number"
                  min={minChars}
                  max={15000}
                  value={maxChars}
                  onChange={(e) => setMaxChars(Number(e.target.value))}
                  required
                />
              </label>
            </div>
            <label>
              写作设置
              <textarea rows={3} value={profile} onChange={(e) => setProfile(e.target.value)} />
            </label>
            <label>
              采纳政策
              <select
                value={adoption}
                onChange={(e) => setAdoption(e.target.value as 'manual' | 'automatic')}
              >
                <option value="manual">每章保留候选，作者采纳后推进</option>
                <option value="automatic">通过本章检查后自动采纳</option>
              </select>
            </label>
            <label>
              本批次预算（可低于共享总上限）
              <input
                type="number"
                min="0.01"
                step="0.01"
                value={runLimit}
                onChange={(e) => setRunLimit(e.target.value)}
                placeholder="留空则仍受服务商共享总上限保护"
              />
            </label>
            <div className="notice">
              <p>
                当前账户：{account}。OpenAI 两账户共用 20 美元，DeepSeek 共用 80 元。并发为
                1，每项最多自动修订 2 轮；未知超时保留费用预留并停止。字数为正文非空白字符数，含标点。
              </p>
              <p>自动采纳会写入本次新建版本。模型审查结果需经作者最终审阅；写满章数不会自动通过整书验收。</p>
            </div>
            <button className="primary wide" disabled={busy || !modelReady}>
              {busy ? '提交中…' : modelReady ? '创建独立版本并启动' : '请先配置模型账户和单价'}
            </button>
          </form>
        </section>
        <section className="card run-monitor">
          <div className="section-title">
            <h2>任务与检查点</h2>
            <span className="badge">持久执行</span>
          </div>
          {tasks.length ? (
            <>
              <label>
                本作品批次
                <select value={task?.id || ''} onChange={(e) => setSelectedTask(e.target.value)}>
                  {tasks.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.payload.mode === 'assisted' ? '半自动' : '全自动'} · {t.payload.account} ·{' '}
                      {label(t.status)} · {new Date(t.created * 1000).toLocaleString('zh-CN')}
                    </option>
                  ))}
                </select>
              </label>
              {task && (
                <>
                  <div className="run-status">
                    <strong>{label(task.status)}</strong>
                    <span>
                      当前进度：{Math.min((task.result.next_chapter || 1) - 1, task.payload.chapters || 36)} /{' '}
                      {task.payload.chapters} 章
                    </span>
                  </div>
                  <progress
                    max={task.payload.chapters}
                    value={Math.min((task.result.next_chapter || 1) - 1, task.payload.chapters || 36)}
                  />
                  <p role="status">{task.error || '生成、审查、事实和采纳分别留有记录。'}</p>
                  <div className="actions">
                    <button
                      disabled={!['queued', 'running'].includes(task.status)}
                      onClick={() =>
                        void perform(async () => {
                          await api(`/tasks/${task.id}/pause`, 'POST')
                          await refresh()
                        })
                      }
                    >
                      章间暂停
                    </button>
                    <button
                      disabled={!['queued', 'running'].includes(task.status) || !!task.cancel}
                      onClick={() =>
                        void perform(async () => {
                          await api(`/tasks/${task.id}/cancel`, 'POST')
                          await refresh()
                        })
                      }
                    >
                      {task.cancel ? '正在取消…' : '取消当前执行'}
                    </button>
                  </div>
                  {['blocked_runtime', 'cancelled'].includes(task.status) && (
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={unknown}
                        onChange={(e) => setUnknown(e.target.checked)}
                      />
                      已查看费用记录，明确同意恢复时可能产生新调用
                    </label>
                  )}
                  <label className="check">
                    <input type="checkbox" checked={changes} onChange={(e) => setChanges(e.target.checked)} />
                    前文已修改时，从最早失效章重新生成
                  </label>
                  {task.status === 'needs_review' && waiting?.status === 'candidate' && (
                    <>
                      <label className="check">
                        <input
                          type="checkbox"
                          checked={regenerate}
                          onChange={(e) => setRegenerate(e.target.checked)}
                        />
                        明确重做当前待审章（拒绝旧候选，追加一次有记录的修订尝试）
                      </label>
                      {regenerate && (
                        <label>
                          重做原因
                          <input
                            value={retryReason}
                            onChange={(e) => setRetryReason(e.target.value)}
                            placeholder="说明本次调整或重新尝试的原因"
                            maxLength={2000}
                          />
                        </label>
                      )}
                    </>
                  )}
                  <button
                    disabled={
                      busy ||
                      ![
                        'paused',
                        'blocked_runtime',
                        'cancelled',
                        'needs_review',
                        'blocked_creative',
                      ].includes(task.status)
                    }
                    onClick={() =>
                      void perform(async () => {
                        await api(`/tasks/${task.id}/resume`, 'POST', {
                          acknowledge_unknown: unknown,
                          acknowledge_changes: changes,
                          regenerate_pending: regenerate,
                          reason: retryReason,
                          candidate_revision: waiting?.candidate_revision ?? 0,
                        })
                        await refresh()
                      })
                    }
                  >
                    从保存的检查点恢复
                  </button>
                  {waiting && (
                    <div className="notice">
                      <h3>待审章节候选</h3>
                      <pre className="stream">{waiting.payload.text}</pre>
                      <details>
                        <summary>本章检查</summary>
                        <pre>{JSON.stringify(waiting.payload.review, null, 2)}</pre>
                      </details>
                      <button
                        className="primary"
                        disabled={busy || waiting.status !== 'candidate'}
                        onClick={() =>
                          void perform(async () => {
                            await api(`/artifacts/${waiting.id}/accept`, 'POST', {
                              idempotency_key: 'manual-' + waiting.id,
                              candidate_revision: waiting.candidate_revision,
                            })
                            setWaiting(await api(`/artifacts/${waiting.id}`))
                            setStatus('候选已采纳。点击恢复继续下一章。')
                          })
                        }
                      >
                        作者采纳此章
                      </button>
                      <p className="small">
                        若要编辑、拒绝或比较，请切换到该工作版本的写作页。手动采纳未通过检查的稿件，不代表整书通过。
                      </p>
                    </div>
                  )}
                  <details>
                    <summary>采用规划与整书检查报告</summary>
                    <pre>{JSON.stringify(task.result, null, 2)}</pre>
                  </details>
                  <a className="button" href={`/api/branches/${task.branch_id}/export?format=md`} download>
                    导出该批次当前稿
                  </a>
                  <p className="small muted">
                    整书验收记录在「故事与审阅」页填写，绑定当前故事修订。后续改稿会使既有验收记录失效。
                  </p>
                </>
              )}
            </>
          ) : (
            <div className="empty-note">
              启动后可在这里查看进度、暂停与恢复。浏览器关闭后，只要本机服务和 Worker 保持运行，任务会继续。
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
