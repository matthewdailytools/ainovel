import { useEffect, useRef, useState } from 'react'
import { api, ApiError, type Task } from './api'
import { useLeaveGuard } from './leaveGuard'
import { object } from './planRestart'
import {
  canonical,
  FLOW_VERSION,
  FLOW_LEGACY_VERSION,
  isFlowVersion,
  FLOW_CONTINUATION_VERSION,
  flowUnavailableReason,
  readFlowPreview,
  readFlowContext,
  readFlowChild,
  readPendingFlow,
  sourceSignature,
  type FlowContext,
  type PendingFlow,
} from './flowContinuation'

const failureText = (error: unknown) =>
  error instanceof Error ? error.message : '读取失败，当前输入与原请求保留。'
const amount = (value: number | null, currency: string) =>
  value === null ? '受服务商共享上限保护' : `${value.toFixed(6)} ${currency}`

export function FlowContinuationReceipt({ task }: { task: Task }) {
  const receipt = object(object(task.result)?.flow_policy_continuation),
    request = object(receipt?.request)
  if (
    receipt?.version !== FLOW_CONTINUATION_VERSION ||
    !isFlowVersion(task.result.flow_source_entailment_version) ||
    !request ||
    request.flow_source_entailment_version !== task.result.flow_source_entailment_version ||
    typeof receipt.source_task_id !== 'string'
  )
    return null
  return (
    <section className="notice" aria-label="新版流向政策子任务回执">
      <h4>新版流向政策子任务已建立</h4>
      <p>
        原任务：{receipt.source_task_id}，已标记转入续跑并追加子任务链接；仅本子任务后续新审查冻结{' '}
        {task.result.flow_source_entailment_version}
        。原任务载荷、旧稿、旧报告和原政策保持，不追认旧检查通过。
      </p>
      <p>决定原因：{String(request.reason)}</p>
      <details>
        <summary>查看新版政策原提交与费用回执</summary>
        <pre className="report">{JSON.stringify(receipt, null, 2)}</pre>
      </details>
    </section>
  )
}

export function FlowContinuationPanel({
  task,
  activeBranchId,
  disabled = false,
  onLockChange,
  onCreated,
  onOpen,
}: {
  task: Task
  activeBranchId: string
  disabled?: boolean
  onLockChange: (locked: boolean) => void
  onCreated: (task: Task) => Promise<void>
  onOpen: (task: Task) => Promise<void>
}) {
  const owner = canonical([task.novel_id, task.branch_id, task.id]),
    scope = canonical([owner, activeBranchId])
  const liveScope = useRef(scope)
  liveScope.current = scope
  const mounted = useRef(true),
    sequence = useRef(0),
    inFlight = useRef(false),
    composing = useRef(false)
  const [open, setOpen] = useState(false),
    [busy, setBusy] = useState(false),
    [ready, setReady] = useState(false)
  const [context, setContext] = useState<FlowContext>(),
    [incoming, setIncoming] = useState<FlowContext>()
  const [reason, setReason] = useState(''),
    [selectedPolicy, setSelectedPolicy] = useState(false)
  const [error, setError] = useState(''),
    [message, setMessage] = useState(''),
    [stale, setStale] = useState(false)
  const [uncertain, setUncertain] = useState(false),
    [created, setCreated] = useState<Task>()
  const pending = useRef<PendingFlow | undefined>(undefined),
    source = useRef(''),
    incomingSource = useRef(''),
    knownRejected = useRef(false)
  const storageKey = `ainovel-flow-continuation:${owner}`,
    targetMatches = task.branch_id === activeBranchId
  const unavailable = flowUnavailableReason(task),
    changed = !!context && source.current !== sourceSignature(task)
  useLeaveGuard(
    targetMatches && !created && !!(reason || uncertain || (busy && pending.current)),
    '新版流向政策决定或未核实提交',
  )
  useEffect(() => {
    onLockChange(busy || uncertain)
    return () => onLockChange(false)
  }, [busy, uncertain, onLockChange])
  function persist(nextReason: string, nextPolicy: boolean, capsule = pending.current) {
    sessionStorage.setItem(
      storageKey,
      JSON.stringify({
        version: 1,
        reason: nextReason,
        selectedPolicy: nextPolicy,
        pending: capsule,
        outcome: knownRejected.current ? 'known_rejected' : 'unverified',
      }),
    )
  }
  useEffect(() => {
    mounted.current = true
    try {
      const raw = sessionStorage.getItem(storageKey)
      if (raw) {
        const value = object(JSON.parse(raw))
        if (value?.version !== 1) throw new Error('旧草稿格式无法核对')
        if (typeof value.reason === 'string' && value.reason.length <= 2000) setReason(value.reason)
        setSelectedPolicy(value.selectedPolicy === true)
        const prior = readPendingFlow(value.pending, owner)
        if (value.pending && !prior) throw new Error('旧提交范围无法核对')
        if (prior) {
          pending.current = prior
          setContext(prior.context)
          source.current = sourceSignature(prior.source)
          setReason(prior.request.reason)
          setSelectedPolicy(true)
          knownRejected.current = value.outcome === 'known_rejected'
          setUncertain(!knownRejected.current)
          setStale(knownRejected.current)
          setOpen(true)
          setMessage(
            knownRejected.current
              ? '旧提交已明确拒绝，输入保留；请显式重新读取依据。'
              : '已恢复原提交标识；结果未知，只能查询原提交回执。',
          )
        }
      }
      setReady(true)
    } catch {
      setUncertain(true)
      setError('无法完整读取本窗口原提交记录，不能生成新标识。请先核对原浏览器记录。')
    }
    return () => {
      mounted.current = false
      sequence.current++
    }
  }, [storageKey, owner])
  useEffect(() => {
    if (!ready || created) return
    try {
      persist(reason, selectedPolicy)
    } catch {
      setError('无法保留本窗口草稿；提交前必须成功保存稳定标识。')
    }
  }, [reason, selectedPolicy, ready, created, storageKey])
  const matches = (target: string, id: number) =>
    mounted.current && liveScope.current === target && sequence.current === id
  async function load() {
    if (inFlight.current || uncertain || disabled || !ready || !targetMatches || unavailable) return
    inFlight.current = true
    const target = scope,
      id = ++sequence.current,
      signature = sourceSignature(task),
      frozenSource = task
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const raw = await api<unknown>(`/tasks/${encodeURIComponent(task.id)}/continue-flow-policy-preview`)
      if (!matches(target, id)) return
      const preview = readFlowPreview(raw, frozenSource)
      if (!preview) throw new Error('预览的来源、政策、检查点或费用字段无法对应当前任务；未创建任务。')
      const config = await api<unknown>('/config')
      if (!matches(target, id)) return
      const next = readFlowContext(preview, config)
      if (!next) throw new Error('当前账户、模型或共享余额无法核对；原输入保留。')
      if (context) {
        setIncoming(next)
        incomingSource.current = signature
      } else {
        setContext(next)
        source.current = signature
        setStale(false)
        pending.current = undefined
        knownRejected.current = false
        persist(reason, selectedPolicy, undefined)
      }
    } catch (failure) {
      if (matches(target, id)) {
        setError(failureText(failure))
        setStale(true)
      }
    } finally {
      if (matches(target, id)) {
        inFlight.current = false
        setBusy(false)
      }
    }
  }
  function confirmIncoming() {
    if (!incoming || inFlight.current || uncertain || !targetMatches) return
    setContext(incoming)
    source.current = incomingSource.current
    setIncoming(undefined)
    setStale(false)
    setError('')
    pending.current = undefined
    knownRejected.current = false
    try {
      persist(reason, selectedPolicy, undefined)
    } catch {
      setError('无法保留原输入，未提交。')
      setStale(true)
    }
  }
  async function publish(raw: unknown, capsule: PendingFlow, target: string, id: number) {
    const child = readFlowChild(raw, capsule)
    if (!child) throw new Error('新任务回执与原提交或冻结范围不符，结果仍未知；请只查询原标识。')
    if (!matches(target, id)) return
    setCreated(child)
    setUncertain(false)
    setStale(false)
    setError('')
    pending.current = undefined
    setMessage('新子任务已创建并排队，Worker可开始后续章节；父任务转入续跑，旧稿与旧政策保留。')
    try {
      sessionStorage.removeItem(storageKey)
    } catch {
      setError('子任务已建立，但本窗口记录未清理；原回执仍保留。')
    }
    try {
      await onCreated(child)
    } catch {
      if (matches(target, id)) setError('子任务已建立，列表刷新失败；可用下方按钮定位已保存任务。')
    }
  }
  const canSubmit =
    !!context &&
    !unavailable &&
    targetMatches &&
    ready &&
    !disabled &&
    !busy &&
    !stale &&
    !changed &&
    !incoming &&
    !uncertain &&
    !created &&
    !!reason.trim() &&
    selectedPolicy &&
    context.account.configured &&
    context.account.priced &&
    context.shared.remaining > 0
  async function submit() {
    if (!canSubmit || !context || inFlight.current || composing.current) return
    const capsule: PendingFlow = {
      source: task,
      context,
      request: {
        client_request_id: crypto.randomUUID(),
        preview_basis_sha256: context.preview.preview_basis_sha256,
        flow_source_entailment_version: FLOW_VERSION,
        reason,
      },
    }
    knownRejected.current = false
    try {
      persist(reason, selectedPolicy, capsule)
    } catch {
      setError('无法保存稳定提交标识，未派发请求。')
      return
    }
    pending.current = capsule
    inFlight.current = true
    const target = scope,
      id = ++sequence.current
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const raw = await api<unknown>(
        `/tasks/${encodeURIComponent(task.id)}/continue-flow-policy`,
        'POST',
        capsule.request,
      )
      if (matches(target, id)) await publish(raw, capsule, target, id)
    } catch (failure) {
      if (!matches(target, id)) return
      setError(failureText(failure))
      if (
        failure instanceof ApiError &&
        failure.status >= 400 &&
        failure.status < 500 &&
        failure.code !== 'invalid_response'
      ) {
        setStale(true)
        knownRejected.current = true
        try {
          persist(reason, selectedPolicy, capsule)
        } catch {
          /* Original intent stays unverified after reload. */
        }
      } else setUncertain(true)
    } finally {
      if (matches(target, id)) {
        inFlight.current = false
        setBusy(false)
      }
    }
  }
  async function lookup() {
    const capsule = pending.current
    if (!capsule || inFlight.current || disabled || !targetMatches) return
    inFlight.current = true
    const target = scope,
      id = ++sequence.current
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const raw = await api<unknown>(
        `/tasks/${encodeURIComponent(capsule.source.id)}/flow-policy-continuations/${encodeURIComponent(capsule.request.client_request_id)}`,
      )
      if (matches(target, id)) await publish(raw, capsule, target, id)
    } catch (failure) {
      if (matches(target, id))
        setError(
          failure instanceof ApiError && failure.status === 404
            ? '尚未查到原提交回执，不代表永远未创建；保留原标识继续只读查询。'
            : failureText(failure),
        )
    } finally {
      if (matches(target, id)) {
        inFlight.current = false
        setBusy(false)
      }
    }
  }
  if (
    task.kind !== 'workflow' ||
    task.payload.plot_contract_check !== true ||
    (task.result.flow_source_entailment_version !== undefined &&
      task.result.flow_source_entailment_version !== FLOW_LEGACY_VERSION &&
      !pending.current &&
      !created)
  )
    return null
  return (
    <details className="notice" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>按新版流向来源政策继续后续章节</summary>
      <section aria-label="新版流向政策续跑">
        <p>
          在服务端确认已采纳并完成维护的安全暂停检查点后，创建并开始独立子批次；后续新审查使用新版流向出处核对，包括全书返修的新审查，额外步骤及失败仍收费。父任务标记转入续跑并追加子任务链接，原载荷、旧政策、旧稿和旧报告保留。
        </p>
        <p className="small">
          全书条件政策：
          {task.result.conditional_book_policy_version || '原冻结政策'}
          ；流向来源政策：
          {task.result.flow_source_entailment_version || '旧政策，未启用新版'}
          。两项分别表达，不因一项已启用就推定另一项已启用。
        </p>
        {!targetMatches && (
          <p role="status">所选任务属于其他工作版本，请先切到对应版本；原输入和未核实提交保留。</p>
        )}
        {unavailable && !uncertain && !created && <p role="status">{unavailable}</p>}
        <button
          type="button"
          disabled={busy || uncertain || disabled || !ready || !targetMatches || !!unavailable || !!created}
          onClick={() => void load()}
        >
          {context ? '重新读取新版政策依据' : '读取新版政策续跑依据'}
        </button>
        {busy && <p role="status">正在核对本次请求…</p>}
        {error && <p role="alert">{error}</p>}
        {message && <p role="status">{message}</p>}
        {changed && !created && (
          <p role="status">任务检查点已变化，输入保留；请显式重新读取并确认依据。未知提交仍只能查询。</p>
        )}
        {incoming && (
          <div className="notice">
            <p>已读到新依据，原原因与政策选择保留；确认前不提交。</p>
            <button type="button" disabled={busy || uncertain} onClick={confirmIncoming}>
              确认使用新版政策依据，保留输入
            </button>
          </div>
        )}
        {context && targetMatches && (
          <>
            <h4>从第 {context.preview.next_chapter} 章检查点开始新子任务</h4>
            <p>
              服务端预览已通过当前检查点门禁；下一章游标不是正文已派发的证明，提交时仍重新核对所有后续章节的正文派发范围及冻结目标。
            </p>
            <p>
              创建后子任务立即排队，Worker可从第 {context.preview.next_chapter} 章继续；沿用原采纳策略：
              {task.payload.adoption === 'automatic' ? '自动采纳' : '由作者采纳'}
              ，原修订轮次和完整规划保留。这会开始后续模型工作。
            </p>
            <p>
              执行配置：{context.account.id} · 模型 {context.account.model} · {context.account.currency}
              。本入口沿用当前模型；换账户使用现有“换账户，从检查点续跑”，再为新的安全检查点读取政策依据。
            </p>
            <p>
              本任务名义剩余 {amount(context.preview.budget.remaining_run_limit, context.account.currency)}
              ；同家族辅助费用 {amount(context.preview.budget.auxiliary_settled, context.account.currency)}
              ；实际可用{' '}
              {amount(context.preview.budget.effective_remaining_run_limit, context.account.currency)}
              。子任务限额不再重复扣辅助费用。
            </p>
            <p>
              当前服务商共享剩余 {amount(context.shared.remaining, context.account.currency)}
              ，共享上限 {context.shared.limit} {context.account.currency}
              ；已有已结 {amount(context.shared.settled, context.account.currency)}
              、未知预留 {amount(context.shared.reserved_unknown, context.account.currency)}
              。本决定不重置费用或额度。
            </p>
            <p className="small">
              历史外币未决 {context.preview.budget.foreign_unknown_calls.length}{' '}
              笔继续占用原币种预留，不重发、不扣本币种限额；当前任务及当前服务商未决仍停止。
            </p>
            <details>
              <summary>查看冻结依据与逐币历史费用</summary>
              <pre className="report">{JSON.stringify(context.preview, null, 2)}</pre>
            </details>
            <label>
              本次新版流向政策续跑原因
              <textarea
                rows={3}
                maxLength={2000}
                value={reason}
                disabled={busy || uncertain || !!created}
                onChange={(event) => setReason(event.target.value)}
                onCompositionStart={() => {
                  composing.current = true
                }}
                onCompositionEnd={() => {
                  composing.current = false
                }}
              />
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={selectedPolicy}
                disabled={busy || uncertain || !!created}
                onChange={(event) => setSelectedPolicy(event.target.checked)}
              />
              明确为新子任务选择 {FLOW_VERSION}，接受后续新增核对及费用
            </label>
            <div className="actions">
              <button type="button" className="primary" disabled={!canSubmit} onClick={() => void submit()}>
                创建并开始新版流向政策子批次
              </button>
              {uncertain && (
                <button type="button" disabled={busy || disabled} onClick={() => void lookup()}>
                  查询原政策提交回执
                </button>
              )}
            </div>
            {uncertain && (
              <p role="status">提交结果未知，原标识已保留；只查询原回执，不重复POST，也不创建新标识。</p>
            )}
          </>
        )}
        {created && (
          <>
            <FlowContinuationReceipt task={created} />
            <button
              type="button"
              disabled={busy || disabled}
              onClick={() => void onOpen(created).catch((failure) => setError(failureText(failure)))}
            >
              打开新版政策子任务
            </button>
          </>
        )}
      </section>
    </details>
  )
}
