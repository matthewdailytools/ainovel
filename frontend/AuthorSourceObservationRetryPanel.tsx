import { useEffect, useRef, useState } from 'react'
import { api, ApiError, type Task } from './api'
import { useLeaveGuard } from './leaveGuard'
import { canonical, object, sourceSignature } from './planRestart'
import {
  AUTHOR_OBSERVATION_RETRY_VERSION,
  readPendingObservationRetry,
  readObservationRetryChild,
  readObservationRetryLookup,
  readObservationRetryPreview,
  observationRetryPath,
  observationRetryPreviewPath,
  observationRetryUnavailableReason,
  type PendingObservationRetry,
  type ObservationRetryPreview,
} from './authorSourceObservationRetry'

const failureText = (value: unknown) =>
  value instanceof Error ? value.message : '核对失败，原话与原提交保留。'
const amount = (value: number | null, currency: string) =>
  value === null ? '受服务商共享上限保护' : `${value.toFixed(6)} ${currency}`
export function AuthorSourceObservationRetryReceipt({ task }: { task: Task }) {
  const result = object(task.result),
    receipt = object(result?.author_source_observation_retry),
    execution = object(result?.authored_source_observation_execution_snapshot)
  if (
    !receipt ||
    result?.author_source_observation_retry_version !== AUTHOR_OBSERVATION_RETRY_VERSION ||
    receipt?.version !== AUTHOR_OBSERVATION_RETRY_VERSION ||
    (receipt.task_id !== task.id &&
      (!execution ||
        execution.task_id !== receipt.task_id ||
        execution.version !== 'authored-source-observation-execution/2')) ||
    receipt.novel_id !== task.novel_id ||
    receipt.branch_id !== task.branch_id ||
    receipt.source_unchanged !== true ||
    !object(receipt.source_entry)
  )
    return null
  return (
    <section className="notice" aria-label="来源观察引用恢复回执">
      <h4>来源观察引用恢复子批次（只读）</h4>
      <p>
        来源政策仍为/2；只更换观察执行模板，保留同一原话、完整计划、旧失败与累计费用。创建后沿原采纳方式继续写作，仍须全部情节解释和独立原意保持；不是作者文学认可。
      </p>
      <pre className="report">{String(object(receipt.source_entry)?.text ?? '')}</pre>
      <details>
        <summary>原请求、三次返回、结算依据与执行版本（只读）</summary>
        <pre className="report">{JSON.stringify(receipt, null, 2)}</pre>
      </details>
      <details>
        <summary>本任务来源观察、全部情节解释及独立保持（只读）</summary>
        <pre className="report">
          {JSON.stringify(
            object(result?.authored_source_scope_receipts)?.[`chapter:${receipt.chapter}`] ?? {
              state: '尚无新观察',
            },
            null,
            2,
          )}
        </pre>
      </details>
    </section>
  )
}

export function AuthorSourceObservationRetryPanel({
  task,
  activeBranchId,
  disabled = false,
  onLockChange,
  onCreated,
}: {
  task: Task
  activeBranchId: string
  disabled?: boolean
  onLockChange: (locked: boolean) => void
  onCreated: (task: Task) => Promise<void>
}) {
  const owner = canonical([task.novel_id, task.branch_id, task.id]),
    scope = canonical([owner, activeBranchId]),
    storageKey = `ainovel-author-source-observation-retry:${owner}`
  const mounted = useRef(true),
    liveScope = useRef(scope),
    sequence = useRef(0),
    inFlight = useRef(false),
    composing = useRef(false),
    pending = useRef<PendingObservationRetry | undefined>(undefined),
    knownRejected = useRef(false),
    previewSignature = useRef(''),
    incomingSignature = useRef('')
  const signature = sourceSignature(task)
  const liveSignature = useRef(signature),
    operationSignature = useRef(signature)
  liveSignature.current = signature
  liveScope.current = scope
  const [open, setOpen] = useState(false),
    [ready, setReady] = useState(false),
    [busy, setBusy] = useState(false),
    [reason, setReason] = useState(''),
    [confirmed, setConfirmed] = useState(false),
    [preview, setPreview] = useState<ObservationRetryPreview>(),
    [incoming, setIncoming] = useState<ObservationRetryPreview>(),
    [uncertain, setUncertain] = useState(false),
    [stale, setStale] = useState(false),
    [created, setCreated] = useState<Task>(),
    [error, setError] = useState(''),
    [message, setMessage] = useState('')
  const targetMatches = task.branch_id === activeBranchId,
    unavailable = observationRetryUnavailableReason(task),
    sourceChanged = !!preview && previewSignature.current !== sourceSignature(task)
  useLeaveGuard(
    targetMatches && !created && !!(reason || uncertain || (busy && pending.current)),
    '来源观察引用恢复原因或未核实提交',
  )
  useEffect(() => {
    onLockChange(busy || uncertain)
    return () => onLockChange(false)
  }, [busy, uncertain, onLockChange])
  function persist(nextReason: string, capsule = pending.current) {
    sessionStorage.setItem(
      storageKey,
      JSON.stringify({
        version: 1,
        reason: nextReason,
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
        const saved = object(JSON.parse(raw))
        if (
          saved?.version !== 1 ||
          typeof saved.reason !== 'string' ||
          Array.from(saved.reason).length > 4000
        )
          throw Error('无法核对草稿格式')
        setReason(saved.reason)
        if (saved.pending) {
          const capsule = readPendingObservationRetry(saved.pending, owner)
          if (!capsule) {
            setUncertain(true)
            throw Error('无法核对原提交')
          }
          pending.current = capsule
          setPreview(capsule.preview)
          previewSignature.current = sourceSignature(capsule.source)
          knownRejected.current = saved.outcome === 'known_rejected'
          if (!knownRejected.current) setReason(capsule.request.reason)
          setUncertain(!knownRejected.current)
          setStale(knownRejected.current)
          setOpen(true)
          setMessage(
            knownRejected.current
              ? '上次请求已明确拒绝，原因和旧依据保留；请显式重新读取确认。'
              : '原提交结果尚未核实，恢复原标识，只查询原提交回执。',
          )
        }
      }
      setReady(true)
    } catch {
      setError('无法完整恢复本窗口原因或原提交，原记录保留；不能新建标识。')
    }
    return () => {
      mounted.current = false
      sequence.current++
    }
  }, [owner, storageKey])
  useEffect(() => {
    if (!ready || created) return
    try {
      persist(reason)
    } catch {
      setError('无法保存本窗口原因，提交前须恢复存储。')
    }
  }, [reason, ready, created, storageKey])
  useEffect(() => {
    sequence.current++
    inFlight.current = false
    setBusy(false)
    setConfirmed(false)
    setIncoming(undefined)
    setStale(true)
    if (pending.current && !knownRejected.current) setUncertain(true)
  }, [scope, signature])
  const matches = (target: string, id: number) =>
    mounted.current &&
    liveScope.current === target &&
    sequence.current === id &&
    operationSignature.current === liveSignature.current
  async function load() {
    if (inFlight.current || uncertain || disabled || !ready || !targetMatches || unavailable) return
    inFlight.current = true
    operationSignature.current = signature
    const target = scope,
      id = ++sequence.current,
      source = task,
      readSignature = sourceSignature(task)
    setBusy(true)
    setError('')
    setMessage('')
    setConfirmed(false)
    try {
      const raw = await api<unknown>(observationRetryPreviewPath(source.id))
      if (!matches(target, id)) return
      const next = readObservationRetryPreview(raw, source)
      if (!next) throw Error('返回依据未对应完整停点、逐字原话、政策或已结费用；请刷新任务后核对。')
      if (preview) {
        setIncoming(next)
        incomingSignature.current = readSignature
      } else {
        setPreview(next)
        previewSignature.current = readSignature
        setStale(false)
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
  function acceptIncoming() {
    if (
      !incoming ||
      inFlight.current ||
      uncertain ||
      !targetMatches ||
      incomingSignature.current !== sourceSignature(task) ||
      disabled
    )
      return
    setPreview(incoming)
    previewSignature.current = incomingSignature.current
    setIncoming(undefined)
    setStale(false)
    setConfirmed(false)
    setError('')
    pending.current = undefined
    knownRejected.current = false
    try {
      persist(reason, undefined)
    } catch {
      setError('无法保存原因，未派发。')
    }
  }
  async function publish(raw: unknown, capsule: PendingObservationRetry, target: string, id: number) {
    const child = readObservationRetryChild(raw, capsule)
    if (!child) throw Error('返回子任务与逐字原话、原政策或预算不符，结果仍未核实；只能查询原标识。')
    if (!matches(target, id)) return
    setCreated(child)
    setUncertain(false)
    setStale(false)
    setError('')
    setMessage(
      '独立子任务已建立并开始，原话、旧三次返回及费用保留；按原采纳方式继续写作，仍须完整来源与独立原意保持。',
    )
    pending.current = undefined
    try {
      sessionStorage.removeItem(storageKey)
    } catch {
      setError('子任务已建立，但本窗口原标识未清理，仍可查证。')
    }
    try {
      await onCreated(child)
    } catch {
      if (matches(target, id)) setError('子任务已建立，工作台刷新失败；回执保留。')
    }
  }
  const canSubmit =
    !!preview &&
    !unavailable &&
    targetMatches &&
    ready &&
    !disabled &&
    !busy &&
    !uncertain &&
    !stale &&
    !sourceChanged &&
    !incoming &&
    !created &&
    confirmed &&
    !!reason.trim() &&
    Array.from(reason).length <= 2000 &&
    preview.account.configured &&
    preview.account.priced
  async function submit() {
    if (inFlight.current || composing.current || !canSubmit || !preview) return
    const capsule: PendingObservationRetry = {
      source: task,
      preview,
      request: {
        version: AUTHOR_OBSERVATION_RETRY_VERSION,
        client_request_id: crypto.randomUUID(),
        preview_basis_sha256: preview.request_basis.preview_basis_sha256,
        reason: reason.trim(),
      },
    }
    knownRejected.current = false
    try {
      persist(reason, capsule)
    } catch {
      setError('无法保存稳定提交标识，未创建子任务。')
      return
    }
    pending.current = capsule
    setUncertain(true)
    inFlight.current = true
    operationSignature.current = signature
    const target = scope,
      id = ++sequence.current
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const raw = await api<unknown>(observationRetryPath(task.id), 'POST', capsule.request)
      if (matches(target, id)) await publish(raw, capsule, target, id)
    } catch (failure) {
      if (!matches(target, id)) return
      setError(failureText(failure))
      if (
        failure instanceof ApiError &&
        failure.status >= 400 &&
        failure.status < 500 &&
        ![404, 408].includes(failure.status) &&
        !['invalid_response', 'unknown_result'].includes(failure.code)
      ) {
        setStale(true)
        setConfirmed(false)
        setUncertain(false)
        knownRejected.current = true
        try {
          persist(reason, capsule)
        } catch {
          /* The original request stays in memory. */
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
    operationSignature.current = signature
    const target = scope,
      id = ++sequence.current
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const raw = await api<unknown>(
        observationRetryPath(capsule.source.id, capsule.request.client_request_id),
      )
      if (!matches(target, id)) return
      const child = readObservationRetryLookup(raw, capsule)
      if (child === undefined) throw Error('回执与原目标或请求不符，原标识保留，不能另建子任务。')
      if (child === null) {
        setMessage('此刻尚未查到原提交，不代表从未创建；继续只读查询原标识。')
        return
      }
      await publish(child, capsule, target, id)
    } catch (failure) {
      if (matches(target, id)) setError(failureText(failure))
    } finally {
      if (matches(target, id)) {
        inFlight.current = false
        setBusy(false)
      }
    }
  }
  if (
    task.kind !== 'workflow' ||
    task.payload.mode !== 'assisted' ||
    task.payload.plot_contract_check !== true
  )
    return null
  const shared = preview?.shared_budget.find((item) => item.provider === preview.budget.provider)
  return (
    <details
      className="notice"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      style={{ overflowWrap: 'anywhere' }}
    >
      <summary>来源引用修正未通过，核对已返回回执</summary>
      <section aria-label="来源观察引用恢复">
        <p>
          仅核对已完整返回且结算的旧观察引用修正失败。不能凭错误文案判断可重发；未知调用、部分返回或已恢复批次不能使用。原话不变，审计原因不加入情节。
        </p>
        <p>
          提交将创建并开始同工作版本子任务，产生后续模型费用。来源政策仍为/2，只有观察执行模板升级。新批次仍解释全部原情节并独立核对原义；未知或不符会停止，不能直接绕过为通过。
        </p>
        {!targetMatches && <p role="status">当前批次属于其他工作版本，请先选择对应版本；原请求保留。</p>}
        {unavailable && !uncertain && !created && <p className="small">{unavailable}</p>}
        <button
          type="button"
          disabled={busy || uncertain || disabled || !ready || !targetMatches || !!unavailable}
          onClick={() => void load()}
        >
          {preview ? '重新读取来源引用恢复依据' : '读取来源引用恢复依据'}
        </button>
        {busy && <p role="status">正在核对本次请求…</p>}
        {error && <p role="alert">{error}</p>}
        {message && <p role="status">{message}</p>}
        {sourceChanged && !created && (
          <p role="status">原任务停点已变化，原因保留；显式重读确认前不提交，未知请求仍只能查原标识。</p>
        )}
        {incoming && (
          <div className="notice">
            <p>已读取新的停点、政策与费用依据，尚未提交。</p>
            <button type="button" disabled={busy || uncertain || disabled} onClick={acceptIncoming}>
              确认新依据，保留原因
            </button>
          </div>
        )}
        {preview && targetMatches && (
          <>
            <h4>第 {preview.chapter} 章 · 旧观察三次完整返回且结算，正文尚未开始</h4>
            <h5>原作者情节（只读）</h5>
            <pre className="report">{preview.original_source.text}</pre>
            {preview.current_source.text !== preview.original_source.text && (
              <details>
                <summary>本次实际核对的既有来源（逐字保留）</summary>
                <pre className="report">{preview.current_source.text}</pre>
              </details>
            )}
            <details>
              <summary>完整原计划与修订轮次（只读）</summary>
              <pre className="report">
                {JSON.stringify({ plan: preview.plan, repair_round: preview.repair_round }, null, 2)}
              </pre>
            </details>
            <details>
              <summary>三次完整返回、引用错误与结算证明（只读）</summary>
              <ol>
                {preview.known_returned_proof.calls.map((call) => (
                  <li key={call.id}>
                    原观察调用 {call.id}：已结算 {amount(call.charged, call.currency)}；账户 {call.account}
                    ，完整返回与缓存已核对。
                  </li>
                ))}
              </ol>
              <pre className="report">{JSON.stringify(preview.known_returned_proof, null, 2)}</pre>
            </details>
            <p>
              原话逐字沿用，来源政策不变；只更换观察执行模板。完整规划、修订轮次、原稿与旧三次失败均保留，旧错误不作为作者原话有误的结论。
            </p>
            <p>
              账户：{preview.account.id} · {preview.account.model}。原任务已付{' '}
              {amount(preview.budget.source_task_settled, preview.account.currency)}；本批名义剩余{' '}
              {amount(preview.budget.remaining_run_limit, preview.account.currency)}；扣除辅助费用后可用{' '}
              {amount(preview.budget.effective_remaining_run_limit, preview.account.currency)}。
            </p>
            {shared && (
              <p className="small">
                服务商累计已付 {amount(shared.settled, shared.currency)}，未知预留{' '}
                {amount(shared.reserved_unknown, shared.currency)}，共享剩余{' '}
                {amount(shared.remaining, shared.currency)}。
              </p>
            )}
            <p>
              原采纳策略：
              {preview.adoption === 'automatic' ? '本章检查通过后自动采纳。' : '逐章由作者确认采纳。'}
              创建子任务不是来源已通过或作者文学认可。
            </p>
            <details>
              <summary>政策、完整步骤证明与费用依据（只读）</summary>
              <pre className="report">
                {JSON.stringify(
                  {
                    source_policy: preview.source_policy,
                    old_execution_version: preview.old_execution_version,
                    new_execution_version: preview.new_execution_version,
                    baseline: preview.baseline,
                    known_returned_proof: preview.known_returned_proof,
                    budget: preview.budget,
                    policies: preview.policies,
                  },
                  null,
                  2,
                )}
              </pre>
            </details>
            <label>
              恢复审计原因（必填，不加入情节）
              <textarea
                rows={2}
                required
                value={reason}
                maxLength={4000}
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
            {Array.from(reason).length > 2000 && <p role="alert">原因最多2000字，请缩短；输入保留。</p>}
            <label className="check">
              <input
                type="checkbox"
                style={{ flexShrink: 0 }}
                checked={confirmed}
                disabled={busy || uncertain || !!created || stale || sourceChanged || !!incoming}
                onChange={(event) => setConfirmed(event.target.checked)}
              />
              <span style={{ minWidth: 0 }}>
                我已核对三次已返回结算、原话和费用；确认按原采纳方式开始独立子批次
              </span>
            </label>
            <div className="actions">
              <button type="button" className="primary" disabled={!canSubmit} onClick={() => void submit()}>
                创建并开始来源观察恢复子批次
              </button>
              {uncertain && (
                <button type="button" disabled={busy || disabled} onClick={() => void lookup()}>
                  只查询原来源引用恢复回执
                </button>
              )}
            </div>
            {pending.current && (
              <p className="small">原提交标识：{pending.current.request.client_request_id}。</p>
            )}
            {uncertain && (
              <p role="status">
                提交结果尚未核实。只查询原标识，不重新提交或生成新标识；原话与费用预留保留。
              </p>
            )}
          </>
        )}
        {created && <AuthorSourceObservationRetryReceipt task={created} />}
      </section>
    </details>
  )
}
