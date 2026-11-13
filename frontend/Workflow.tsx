import { useEffect, useRef, useState } from 'react'
import {
  api,
  label,
  type Account,
  type Artifact,
  type ContinuationInput,
  type Novel,
  type Task,
  type WorkflowInput,
  type GenreProfile,
} from './api'
import type { Notify } from './App'
import { useLeaveGuard } from './leaveGuard'
import story from '../fixtures/original-story.json'
import { GenreProfileDetails, GenreProfileNotice } from './GenreProfile'
import { AuthorContractDetails, AuthorContractNotice, type AuthorContract } from './AuthorIntent'
import { StoryInheritance, type InheritanceSelection } from './StoryInheritance'
import { characterFieldRepairEnabled } from './characterFieldRepair'
import { CharacterFieldRepairOption, FrozenCharacterFieldRepairPolicy } from './CharacterRepairDetails'
import { FrozenPlotAuditPolicy, PlotAuditDetails, PlotAuditOption } from './PlotAuditDetails'
import { plotAuditFromPayload } from './plotAudit'

export function WorkflowPage({
  novel,
  accounts,
  branchId,
  account,
  modelReady,
  notify,
  onStarted,
  onGenreSettings,
}: {
  novel: Novel
  accounts: Account[]
  branchId: string
  account: string
  modelReady: boolean
  notify: Notify
  onStarted: (id: string) => Promise<void>
  onGenreSettings: () => void
}) {
  const [mode, setMode] = useState<'assisted' | 'automatic'>('assisted')
  const [completionScope, setCompletionScope] = useState<'whole_book' | 'segment'>('whole_book')
  const [brief, setBrief] = useState(novel.brief)
  const [fixed, setFixed] = useState('')
  const [plots, setPlots] = useState('')
  const [count, setCount] = useState(36)
  const [minChars, setMinChars] = useState(2500)
  const [maxChars, setMaxChars] = useState(3500)
  const [adoption, setAdoption] = useState<'manual' | 'automatic'>('manual')
  const [structuredMaintenance, setStructuredMaintenance] = useState(true)
  const [sceneMaintenance, setSceneMaintenance] = useState(true)
  const [characterFieldRepair, setCharacterFieldRepair] = useState(false)
  const [plotContractCheck, setPlotContractCheck] = useState(false)
  const [inheritance, setInheritance] = useState<InheritanceSelection>()
  const [profile, setProfile] = useState(
    '第三人称限定视角，克制白话，动作有因果，人物选择遵循当时掌握的信息与资源。',
  )
  const [runLimit, setRunLimit] = useState('')
  const [seenContract, setSeenContract] = useState<AuthorContract>()
  const [seenGenre, setSeenGenre] = useState<GenreProfile>()
  const [tasks, setTasks] = useState<Task[]>([])
  const [busy, setBusy] = useState(false)
  const pendingRequest = useRef<{ signature: string; key: string } | null>(null)
  const pendingContinuation = useRef<{ signature: string; key: string } | null>(null)
  const [selectedTask, setSelectedTask] = useState('')
  const [loadedWaiting, setWaiting] = useState<Artifact>()
  const [incomingWaiting, setIncomingWaiting] = useState<Artifact>()
  const waitingRef = useRef<Artifact | undefined>(undefined)
  const [unknown, setUnknown] = useState(false)
  const [changes, setChanges] = useState(false)
  const [regenerate, setRegenerate] = useState(false)
  const [retryReason, setRetryReason] = useState('')
  const [continueAccount, setContinueAccount] = useState('')
  const [continueLimit, setContinueLimit] = useState('')
  const [continueReason, setContinueReason] = useState('')
  const [status, setStatus] = useState('')
  const formSignature = JSON.stringify([
    mode,
    completionScope,
    brief,
    fixed,
    plots,
    count,
    minChars,
    maxChars,
    adoption,
    structuredMaintenance,
    sceneMaintenance,
    characterFieldRepair,
    plotContractCheck,
    profile,
    runLimit,
  ])
  const [submittedForm, setSubmittedForm] = useState(formSignature)
  useLeaveGuard(formSignature !== submittedForm, '整书创作设置')
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
  const visibleTasks = tasks.filter((t) => t.novel_id === novel.id)
  const task =
    visibleTasks.find((t) => t.id === selectedTask) ||
    visibleTasks.find((t) => t.branch_id === branchId) ||
    visibleTasks[0]
  const waitingId = task?.result.waiting_structure || task?.result.waiting_artifact
  const waiting = loadedWaiting?.id === waitingId ? loadedWaiting : undefined
  const structureWaiting = !!task?.result.waiting_structure
  const candidateChanged = !!waiting && incomingWaiting?.id === waiting.id
  const waitingPlotAudit = plotAuditFromPayload(waiting?.payload || {})
  useEffect(() => {
    setUnknown(false)
    setChanges(false)
    setRegenerate(false)
    setRetryReason('')
    setContinueAccount('')
    setContinueLimit('')
    setContinueReason('')
  }, [task?.id, task?.status, waitingId])
  useEffect(() => {
    let active = true
    setWaiting(undefined)
    setIncomingWaiting(undefined)
    waitingRef.current = undefined
    const id = waitingId
    const poll = () => {
      if (!id) return
      void api<Artifact>(`/artifacts/${id}`)
        .then((a) => {
          if (!active) return
          const viewed = waitingRef.current
          if (viewed?.id === a.id && a.candidate_revision < viewed.candidate_revision) return
          if (viewed?.id === a.id && a.candidate_revision !== viewed.candidate_revision) {
            setIncomingWaiting((prior) =>
              prior?.id === a.id && prior.candidate_revision > a.candidate_revision ? prior : a,
            )
            return
          }
          waitingRef.current = a
          setWaiting(a)
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
  }, [waitingId, task?.status, notify])
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
      if (seenGenre?.branch_id !== branchId) throw new Error('正在读取来源版本的题材配置，请稍后再启动。')
      if (seenContract?.branch_id !== branchId) throw new Error('正在读取本书创作约定，请稍后再启动。')
      if (inheritance?.selected_ids.length && inheritance.source_branch_id !== branchId)
        throw new Error('继承来源工作版本已切换，请重新勾选。')
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
        completion_scope: completionScope,
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
        plot_contract_check: plotContractCheck,
        structured_maintenance: structuredMaintenance,
        scene_maintenance: structuredMaintenance && sceneMaintenance,
        character_field_repair: characterFieldRepairEnabled(
          characterFieldRepair,
          adoption,
          structuredMaintenance,
        ),
        inherit_story_record_ids: inheritance?.selected_ids || [],
        inherit_source_revision: inheritance?.selected_ids.length ? inheritance.source_story_revision : null,
        writing_profile: profile,
        author_contract_basis: {
          source_artifact_id: seenContract.source_artifact_id,
          contract_version: seenContract.contract_version,
        },
        genre_profile_basis: {
          source_artifact_id: seenGenre.source_artifact_id ?? null,
          catalog_version: seenGenre.catalog_version,
        },
        budget_limit: runLimit ? Number(runLimit) : null,
      }
      const signature = JSON.stringify(payload)
      if (pendingRequest.current?.signature !== signature)
        pendingRequest.current = { signature, key: crypto.randomUUID() }
      payload.client_request_id = pendingRequest.current.key
      const row = await api<Task>('/workflows', 'POST', payload)
      setSubmittedForm(formSignature)
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
            <AuthorContractNotice
              branchId={branchId}
              onSettings={onGenreSettings}
              onResolved={setSeenContract}
            />
            <GenreProfileNotice
              branchId={branchId}
              onSettings={onGenreSettings}
              scope="新建半自动／全自动批次的规划、写作、审查与修订"
              onResolved={setSeenGenre}
            />
            <StoryInheritance
              key={branchId}
              branchId={branchId}
              busy={busy}
              notify={notify}
              onChange={setInheritance}
            />
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
              本次任务范围
              <select
                value={completionScope}
                onChange={(e) => setCompletionScope(e.target.value as 'whole_book' | 'segment')}
              >
                <option value="whole_book">整书完结：本次章数覆盖完整小说</option>
                <option value="segment">阶段批次：只完成本次选定章节</option>
              </select>
            </label>
            <p className="small">
              {completionScope === 'segment'
                ? '完成本批情节、篇幅、状态维护与范围审查后记录阶段完成。后续终局和未到回收时点的伏笔继续保留，不将本批标为全书完结。'
                : '全部章数完成后核对主冲突、作者终局与已维护承诺；存在未闭合或严重问题时，全书仍未完成。'}
            </p>
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
            {seenContract?.branch_id === branchId && seenContract.author_text && (
              <button type="button" disabled={busy} onClick={() => setBrief(seenContract.author_text)}>
                沿用当前创作约定作为简报
              </button>
            )}
            <label>
              {completionScope === 'segment' ? '本批适用固定条件（每行一条）' : '全书固定条件（每行一条）'}
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
                onChange={(e) => {
                  const value = e.target.value as 'manual' | 'automatic'
                  setAdoption(value)
                  if (value !== 'automatic') setCharacterFieldRepair(false)
                }}
              >
                <option value="manual">每章保留候选，作者采纳后推进</option>
                <option value="automatic">通过本章检查后自动采纳</option>
              </select>
            </label>
            <label>
              <span className="check">
                <input
                  type="checkbox"
                  checked={structuredMaintenance}
                  onChange={(e) => {
                    setStructuredMaintenance(e.target.checked)
                    if (!e.target.checked) setCharacterFieldRepair(false)
                  }}
                />
                每章采用后整理人物、时点、事件和伏笔
              </span>
              <span className="small">
                增加模型调用，计入同一批次及共享预算；人工政策需确认结构，旧事实纠错会停下等待核对。
              </span>
            </label>
            <label className="check">
              <input
                type="checkbox"
                disabled={!structuredMaintenance}
                checked={structuredMaintenance && sceneMaintenance}
                onChange={(e) => setSceneMaintenance(e.target.checked)}
              />
              同时维护新正文的场景范围
            </label>
            <p className="small">
              与结构使用同一次整理调用；按新正文重新定位，未知时点明确保留。作者编辑过的旧绑定需核对后替换。
            </p>
            <CharacterFieldRepairOption
              checked={characterFieldRepair}
              adoption={adoption}
              maintenance={structuredMaintenance}
              busy={busy}
              onChange={setCharacterFieldRepair}
            />
            <PlotAuditOption checked={plotContractCheck} disabled={busy} onChange={setPlotContractCheck} />
            {plotContractCheck && mode === 'automatic' && (
              <p className="small muted">全自动核对来源是AI章纲，不是作者原话或新增硬条件。</p>
            )}
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
              <p>
                自动维护仅按本章原文兑现无旧引用、断言保持一致的计划；改写已有事实、档案或带历史来源的计划会停下核对。
              </p>
            </div>
            <button
              className="primary wide"
              disabled={
                busy ||
                !modelReady ||
                seenGenre?.branch_id !== branchId ||
                seenContract?.branch_id !== branchId
              }
            >
              {busy
                ? '提交中…'
                : !modelReady
                  ? '请先配置模型账户和单价'
                  : seenGenre?.branch_id !== branchId || seenContract?.branch_id !== branchId
                    ? '正在读取来源题材配置…'
                    : '创建独立版本并启动'}
            </button>
          </form>
        </section>
        <section className="card run-monitor">
          <div className="section-title">
            <h2>任务与检查点</h2>
            <span className="badge">持久执行</span>
          </div>
          {visibleTasks.length ? (
            <>
              <label>
                本作品批次
                <select value={task?.id || ''} onChange={(e) => setSelectedTask(e.target.value)}>
                  {visibleTasks.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.payload.mode === 'assisted' ? '半自动' : '全自动'} · {t.payload.account} ·{' '}
                      {t.payload.completion_scope === 'segment' ? '阶段批次' : '整书完结'} · {label(t.status)}{' '}
                      · {new Date(t.created * 1000).toLocaleString('zh-CN')}
                    </option>
                  ))}
                </select>
              </label>
              {task && (
                <>
                  <div className="run-status">
                    <strong>{label(task.status)}</strong>
                    <span>
                      {task.result.repair_round
                        ? `整书复检第 ${task.result.repair_round} 轮 · 已推进`
                        : '初稿阶段 · 已推进'}
                      ： {Math.min((task.result.next_chapter || 1) - 1, task.payload.chapters || 36)} /{' '}
                      {task.payload.chapters} 章
                    </span>
                  </div>
                  <progress
                    max={task.payload.chapters}
                    value={Math.min((task.result.next_chapter || 1) - 1, task.payload.chapters || 36)}
                  />
                  <p role="status">{task.error || '生成、审查、事实和采纳分别留有记录。'}</p>
                  <p className="small muted">
                    {task.payload.completion_scope === 'segment'
                      ? '本次为阶段批次：范围检查通过只表示本批完成，未来终局与待回收承诺不被自动改为已闭合。'
                      : '本次为整书范围：所有已维护承诺与核心终局通过后，才记录模型生产完成。'}
                  </p>
                  <p className="small muted">
                    执行账户：{task.payload.account} · 本次预算上限：
                    {task.payload.budget_limit ?? '服务商共享上限'}{' '}
                    {accounts.find((a) => a.id === task.payload.account)?.currency}
                  </p>
                  {task.result.continued_from && (
                    <p className="small muted">已承接上次检查点；原稿、费用历史与修订轮次保留。</p>
                  )}
                  <FrozenCharacterFieldRepairPolicy enabled={task.payload.character_field_repair} />
                  <FrozenPlotAuditPolicy
                    enabled={task.payload.plot_contract_check}
                    mode={task.payload.mode}
                    version={task.result.plot_contract_version}
                  />
                  <details>
                    <summary>本批次冻结的频道与题材</summary>
                    {task.result.author_contract_snapshot && (
                      <details>
                        <summary>本次任务冻结的创作约定</summary>
                        <AuthorContractDetails contract={task.result.author_contract_snapshot} />
                      </details>
                    )}
                    {task.result.genre_profile_snapshot &&
                    !task.result.genre_profile_snapshot.legacy_unconfigured ? (
                      <GenreProfileDetails profile={task.result.genre_profile_snapshot} />
                    ) : (
                      <p className="small muted">
                        这是未冻结题材装配的历史批次，恢复时沿用原有范围。当前配置不会被自动套入旧稿；新配置用于新建批次。
                      </p>
                    )}
                  </details>
                  {task.result.continued_to && (
                    <button
                      onClick={() =>
                        void perform(async () => {
                          const row = await api<Task>(`/tasks/${task.result.continued_to}`)
                          setTasks((prior) => [row, ...prior.filter((t) => t.id !== row.id)])
                          setSelectedTask(row.id)
                        })
                      }
                    >
                      打开续跑任务
                    </button>
                  )}
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
                    前文已修改时，从最早失效章重新检查并按需修订
                  </label>
                  {task.status === 'needs_review' &&
                    (waiting?.status === 'candidate' ||
                      (structureWaiting && waiting?.status === 'rejected')) && (
                      <>
                        <label className="check">
                          <input
                            type="checkbox"
                            checked={regenerate}
                            onChange={(e) => setRegenerate(e.target.checked)}
                          />
                          {structureWaiting
                            ? '明确重做当前结构整理（保留旧候选与原因记录）'
                            : '明确重做当前待审章（拒绝旧候选，追加一次有记录的修订尝试）'}
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
                      (task.status === 'needs_review' &&
                        !!waitingId &&
                        (!waiting || candidateChanged || (structureWaiting && !regenerate))) ||
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
                  {['paused', 'blocked_runtime', 'blocked_creative', 'cancelled'].includes(task.status) &&
                    !waitingId && (
                      <details>
                        <summary>换账户，从检查点续跑</summary>
                        <p className="small">
                          保留当前稿件和修订次数。原账户的未知费用继续占用预留；后续预算按新账户币种设置，仍受该服务商共享上限限制。
                        </p>
                        <label>
                          后续执行账户
                          <select
                            value={continueAccount}
                            onChange={(e) => setContinueAccount(e.target.value)}
                          >
                            <option value="">请选择其他账户</option>
                            {accounts
                              .filter((a) => a.id !== task.payload.account)
                              .map((a) => (
                                <option key={a.id} value={a.id} disabled={!a.configured || !a.priced}>
                                  {a.id} · {a.model} · {a.currency}
                                </option>
                              ))}
                          </select>
                        </label>
                        <label>
                          续跑预算上限（
                          {accounts.find((a) => a.id === continueAccount)?.currency || '先选择账户'}）
                          <input
                            type="number"
                            min="0.01"
                            step="0.01"
                            value={continueLimit}
                            onChange={(e) => setContinueLimit(e.target.value)}
                            placeholder="留空使用该服务商共享上限"
                          />
                        </label>
                        <label>
                          换账户原因
                          <input
                            value={continueReason}
                            maxLength={2000}
                            onChange={(e) => setContinueReason(e.target.value)}
                            placeholder="如服务余额不足，保留进度换账户继续"
                          />
                        </label>
                        <button
                          disabled={
                            busy ||
                            !continueAccount ||
                            !continueReason.trim() ||
                            (['blocked_runtime', 'cancelled'].includes(task.status) && !unknown)
                          }
                          onClick={() =>
                            void perform(async () => {
                              const limit = continueLimit ? Number(continueLimit) : null
                              if (limit !== null && (!Number.isFinite(limit) || limit <= 0))
                                throw new Error('请输入正数预算，或留空使用共享上限。')
                              const values = {
                                account: continueAccount,
                                budget_limit: limit,
                                reason: continueReason,
                                acknowledge_unknown: unknown,
                              }
                              const signature = JSON.stringify({ source: task.id, ...values })
                              if (pendingContinuation.current?.signature !== signature)
                                pendingContinuation.current = { signature, key: crypto.randomUUID() }
                              const body: ContinuationInput = {
                                ...values,
                                client_request_id: pendingContinuation.current.key,
                              }
                              const row = await api<Task>(`/tasks/${task.id}/continue`, 'POST', body)
                              setTasks((prior) => [row, ...prior.filter((t) => t.id !== row.id)])
                              setSelectedTask(row.id)
                              pendingContinuation.current = null
                              setStatus('已换账户，从原检查点继续；旧记录保留。')
                            })
                          }
                        >
                          使用所选账户续跑
                        </button>
                      </details>
                    )}
                  {structureWaiting && (
                    <div className="notice" role="status">
                      <h3>正文已采用，结构整理等待核对</h3>
                      <p>
                        当前不会推进下一章。请切换到此批次的工作版本，在「故事与审阅 →
                        结构整理」核对候选并采用；完成后回到这里恢复。也可填写原因重做整理。
                      </p>
                      <p className="small">候选标识：{task.result.waiting_structure}</p>
                      <button
                        disabled={busy}
                        onClick={() =>
                          void perform(async () => {
                            await onStarted(task.branch_id)
                            onGenreSettings()
                          })
                        }
                      >
                        打开此批次的结构审阅
                      </button>
                    </div>
                  )}
                  {!!task.result.structure_history?.length && (
                    <p>
                      已完成 {task.result.structure_history.length}{' '}
                      次章节结构维护；完整采用回执保留在检查点报告中。
                    </p>
                  )}
                  {task.result.structure_inheritance && (
                    <details>
                      <summary>
                        本批继承的设定与计划 · {task.result.structure_inheritance.records.length}项
                      </summary>
                      <p>
                        来自来源故事修订{task.result.structure_inheritance.source_story_revision}
                        ；已建立本版独立身份，未复制原稿或实际经历。
                      </p>
                      <ul>
                        {task.result.structure_inheritance.records.map((r) => (
                          <li key={r.target_id}>
                            {r.title} · 来源记录修订{r.source_revision}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                  {waiting && !structureWaiting && (
                    <div className="notice">
                      <h3>待审章节候选</h3>
                      <p>候选修订 {waiting.candidate_revision}</p>
                      {candidateChanged && incomingWaiting && (
                        <div role="status">
                          <p>候选已在其他窗口更新。当前比较版本仍保留，请载入最新候选后重新决定。</p>
                          <button
                            onClick={() => {
                              waitingRef.current = incomingWaiting
                              setWaiting(incomingWaiting)
                              setIncomingWaiting(undefined)
                              setRegenerate(false)
                              setRetryReason('')
                            }}
                          >
                            载入最新待审候选
                          </button>
                        </div>
                      )}
                      <pre className="stream">{waiting.payload.text}</pre>
                      <PlotAuditDetails
                        value={waitingPlotAudit.value}
                        candidateRevision={waiting.candidate_revision}
                        candidateText={waiting.payload.text}
                        historical={waitingPlotAudit.historical}
                      />
                      <details>
                        <summary>本章检查</summary>
                        <pre>{JSON.stringify(waiting.payload.review, null, 2)}</pre>
                      </details>
                      <button
                        className="primary"
                        disabled={busy || candidateChanged || waiting.status !== 'candidate'}
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
                    <summary>
                      {task.payload.completion_scope === 'segment'
                        ? '采用规划与本批范围报告'
                        : '采用规划与整书检查报告'}
                    </summary>
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
