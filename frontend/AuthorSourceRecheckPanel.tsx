import { useEffect, useRef, useState } from 'react'
import { api, ApiError, type Task } from './api'
import { useLeaveGuard } from './leaveGuard'
import { canonical, object, sourceSignature } from './planRestart'
import {
  AUTHOR_SOURCE_RECHECK_VERSION,
  AUTHORED_SOURCE_POLICY_VERSION,
  readPendingSourceRecheck,
  readSourceRecheckChild,
  readSourceRecheckLookup,
  readSourceRecheckPreview,
  sourceRecheckPath,
  sourceRecheckPreviewPath,
  sourceRecheckUnavailableReason,
  type PendingSourceRecheck,
  type SourceRecheckPreview,
} from './authorSourceRecheck'

const failureText = (value: unknown) =>
  value instanceof Error ? value.message : '核对失败，原话与原提交保留。'
const amount = (value: number | null, currency: string) =>
  value === null ? '受服务商共享上限保护' : `${value.toFixed(6)} ${currency}`
export function AuthorSourceRecheckReceipt({ task }: { task: Task }) {
  const result = object(task.result),
    receipt = object(result?.author_source_recheck),
    request = object(receipt?.request),
    entry = object(receipt?.source_entry),
    execution = object(object(result?.authored_source_scope_receipts)?.[`chapter:${receipt?.chapter}`])
  if (
    result?.author_source_recheck_version !== AUTHOR_SOURCE_RECHECK_VERSION ||
    result.authored_source_scope_version !== AUTHORED_SOURCE_POLICY_VERSION ||
    receipt?.version !== AUTHOR_SOURCE_RECHECK_VERSION ||
    receipt.task_id !== task.id ||
    receipt.novel_id !== task.novel_id ||
    receipt.branch_id !== task.branch_id ||
    receipt.source_unchanged !== true ||
    !request ||
    typeof entry?.text !== 'string'
  )
    return null
  return (
    <section className="notice" aria-label="同原话新版核对回执">
      <h4>同原话新版核对子任务</h4>
      <p>
        承接第 {String(receipt.chapter)}{' '}
        章的原停点。没有补情节或选择人物；原话、旧来源观察、原任务与费用保留。
      </p>
      <p>
        新政策重新观察范围、解释全部情节并独立核对原义保持；仍未知或不符会继续停止。模型通过后才沿原采纳策略推进，不代表作者文学认可。
      </p>
      <pre className="report">{entry.text}</pre>
      <section aria-label="本任务新版来源核对">
        <p>
          {execution?.version === AUTHORED_SOURCE_POLICY_VERSION && execution.state === 'ready'
            ? '新版来源模型核对就绪；正文和作者审阅仍独立。'
            : '新版来源仍在核对或待核，不能据此宣称通过。'}
        </p>
        <details>
          <summary>本任务新版来源观察、全部情节解释与独立保持（只读）</summary>
          <p>
            仅依据本次原话；分类为无接收范围不等于忽略该句情节。独立保持仍核对原句、全部事项和固定条件，未知或不符不能直接继续正文。
          </p>
          <pre className="report">{JSON.stringify(execution ?? { state: '尚无来源核对记录' }, null, 2)}</pre>
        </details>
      </section>
      <details>
        <summary>原话、原停点、政策与累计费用回执（只读）</summary>
        <pre className="report">{JSON.stringify(receipt, null, 2)}</pre>
      </details>
    </section>
  )
}

export function AuthorSourceRecheckPanel({
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
    storageKey = `ainovel-author-source-recheck:${owner}`
  const mounted = useRef(true),
    liveScope = useRef(scope),
    sequence = useRef(0),
    inFlight = useRef(false),
    composing = useRef(false),
    pending = useRef<PendingSourceRecheck | undefined>(undefined),
    knownRejected = useRef(false),
    previewSignature = useRef(''),
    incomingSignature = useRef('')
  liveScope.current = scope
  const [open, setOpen] = useState(false),
    [ready, setReady] = useState(false),
    [busy, setBusy] = useState(false),
    [reason, setReason] = useState(''),
    [confirmed, setConfirmed] = useState(false),
    [preview, setPreview] = useState<SourceRecheckPreview>(),
    [incoming, setIncoming] = useState<SourceRecheckPreview>(),
    [uncertain, setUncertain] = useState(false),
    [stale, setStale] = useState(false),
    [created, setCreated] = useState<Task>(),
    [error, setError] = useState(''),
    [message, setMessage] = useState('')
  const targetMatches = task.branch_id === activeBranchId,
    unavailable = sourceRecheckUnavailableReason(task),
    sourceChanged = !!preview && previewSignature.current !== sourceSignature(task)
  useLeaveGuard(
    targetMatches && !created && !!(reason || uncertain || (busy && pending.current)),
    '同原话新版核对原因或未核实提交',
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
          const capsule = readPendingSourceRecheck(saved.pending, owner)
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
  const matches = (target: string, id: number) =>
    mounted.current && liveScope.current === target && sequence.current === id
  async function load() {
    if (inFlight.current || uncertain || disabled || !ready || !targetMatches || unavailable) return
    inFlight.current = true
    const target = scope,
      id = ++sequence.current,
      source = task,
      signature = sourceSignature(task)
    setBusy(true)
    setError('')
    setMessage('')
    setConfirmed(false)
    try {
      const raw = await api<unknown>(sourceRecheckPreviewPath(source.id))
      if (!matches(target, id)) return
      const next = readSourceRecheckPreview(raw, source)
      if (!next) throw Error('返回依据未对应完整停点、逐字原话、政策或已结费用；请刷新任务后核对。')
      if (preview) {
        setIncoming(next)
        incomingSignature.current = signature
      } else {
        setPreview(next)
        previewSignature.current = signature
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
  async function publish(raw: unknown, capsule: PendingSourceRecheck, target: string, id: number) {
    const child = readSourceRecheckChild(raw, capsule)
    if (!child) throw Error('返回子任务与逐字原话、原政策或预算不符，结果仍未核实；只能查询原标识。')
    if (!matches(target, id)) return
    setCreated(child)
    setUncertain(false)
    setStale(false)
    setError('')
    setMessage('独立子任务已建立并开始，原话与历史保留；仍须新核对，未自动认定通过。')
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
    const capsule: PendingSourceRecheck = {
      source: task,
      preview,
      request: {
        version: AUTHOR_SOURCE_RECHECK_VERSION,
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
    inFlight.current = true
    const target = scope,
      id = ++sequence.current
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const raw = await api<unknown>(sourceRecheckPath(task.id), 'POST', capsule.request)
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
        setConfirmed(false)
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
    const target = scope,
      id = ++sequence.current
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const raw = await api<unknown>(sourceRecheckPath(capsule.source.id, capsule.request.client_request_id))
      if (!matches(target, id)) return
      const child = readSourceRecheckLookup(raw, capsule)
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
      <summary>同一作者原话，按新版范围政策重新核对</summary>
      <section aria-label="同原话新版核对">
        <p>
          用于旧版把动作、目标、否定或结果误当接收范围不明的完整来源停点。无需补写情节，不选择人物或改原意；真正歧义仍需明确补充，未知派发不能用此入口重发。
        </p>
        <p>
          提交将创建并开始同工作版本子任务，产生后续模型费用。新核对分别观察接收范围、解释全部原情节、独立核对原义保持；仍未知或不符会停止，不能直接绕过为通过。
        </p>
        {!targetMatches && <p role="status">当前批次属于其他工作版本，请先选择对应版本；原请求保留。</p>}
        {unavailable && !uncertain && !created && <p className="small">{unavailable}</p>}
        <button
          type="button"
          disabled={busy || uncertain || disabled || !ready || !targetMatches || !!unavailable}
          onClick={() => void load()}
        >
          {preview ? '重新读取同原话核对依据' : '读取同原话核对依据'}
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
            <h4>第 {preview.chapter} 章 · 已完整返回的来源待核停点，正文尚未开始</h4>
            <h5>原作者情节（只读）</h5>
            <pre className="report">{preview.original_source.text}</pre>
            {preview.current_source.text !== preview.original_source.text && (
              <details>
                <summary>本次实际核对的既有来源（逐字保留）</summary>
                <pre className="report">{preview.current_source.text}</pre>
              </details>
            )}
            <details>
              <summary>旧观察与原情节解释（只读）</summary>
              <pre className="report">{JSON.stringify(preview.source_review, null, 2)}</pre>
            </details>
            <p>
              原话逐字沿用；只变更来源核对政策。完整规划、修订轮次及原稿保留，旧观察作为历史记录，不作为新政策通过依据。
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
                    old_policy: preview.old_policy,
                    new_policy: preview.new_policy,
                    baseline: preview.baseline,
                    source_steps: preview.budget.source_steps,
                    budget: preview.budget,
                    policies: preview.policies,
                  },
                  null,
                  2,
                )}
              </pre>
            </details>
            <label>
              本次同原话核对原因（必填，仅记录，不作为情节）
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
                我已核对逐字原话、后续模型费用和原采纳策略，确认开始独立子任务
              </span>
            </label>
            <div className="actions">
              <button type="button" className="primary" disabled={!canSubmit} onClick={() => void submit()}>
                创建并开始同原话新版核对子任务
              </button>
              {uncertain && (
                <button type="button" disabled={busy || disabled} onClick={() => void lookup()}>
                  只查询原同原话核对回执
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
        {created && <AuthorSourceRecheckReceipt task={created} />}
      </section>
    </details>
  )
}
