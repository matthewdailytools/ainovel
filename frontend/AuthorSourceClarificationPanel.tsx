import { useEffect, useRef, useState } from 'react'
import { api, ApiError, type Task } from './api'
import { useLeaveGuard } from './leaveGuard'
import { canonical, object, sourceSignature } from './planRestart'
import {
  AUTHOR_CLARIFICATION_VERSION,
  clarificationUnavailableReason,
  readClarificationChild,
  readClarificationLookup,
  readClarificationPreview,
  readPendingClarification,
  type ClarificationPreview,
  type PendingClarification,
} from './authorSourceClarification'

const failureText = (value: unknown) =>
  value instanceof Error ? value.message : '读取失败，补充与原提交保留。'
const amount = (value: number | null, currency: string) =>
  value === null ? '受服务商共享上限保护' : `${value.toFixed(6)} ${currency}`

export function AuthorSourceClarificationReceipt({ task }: { task: Task }) {
  const result = object(task.result),
    receipt = object(result?.author_source_clarification),
    request = object(receipt?.request)
  if (
    result?.author_source_clarification_version !== AUTHOR_CLARIFICATION_VERSION ||
    receipt?.version !== AUTHOR_CLARIFICATION_VERSION ||
    receipt.branch_id !== task.branch_id ||
    receipt.novel_id !== task.novel_id ||
    !request ||
    !['author', 'authorized_operator'].includes(String(request.actor))
  )
    return null
  return (
    <section className="notice" aria-label="作者来源补充回执">
      <h4>本任务保留显式来源补充</h4>
      <p>
        第 {String(receipt.chapter)} 章 · 来源：{request.actor === 'author' ? '作者' : '授权操作者'}
        。原情节、待核报告和累计费用保留。
      </p>
      <p>
        新任务会先独立核对补充与原意及创作约定是否兼容；通过后按原采纳策略继续正文和后续流程。补充不代表模型核对通过或文学认可。
      </p>
      <pre className="report">{String(request.clarification)}</pre>
      <details>
        <summary>查看原来源、补充与费用回执</summary>
        <pre className="report">{JSON.stringify(receipt, null, 2)}</pre>
      </details>
    </section>
  )
}

export function AuthorSourceClarificationPanel({
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
    storageKey = `ainovel-author-source-clarification:${owner}`
  const liveScope = useRef(scope),
    mounted = useRef(true),
    sequence = useRef(0),
    inFlight = useRef(false),
    composing = useRef(false),
    pending = useRef<PendingClarification | undefined>(undefined),
    knownRejected = useRef(false),
    previewSource = useRef(''),
    incomingSource = useRef('')
  liveScope.current = scope
  const [open, setOpen] = useState(false),
    [busy, setBusy] = useState(false),
    [ready, setReady] = useState(false),
    [clarification, setClarification] = useState(''),
    [reason, setReason] = useState(''),
    [preview, setPreview] = useState<ClarificationPreview>(),
    [incoming, setIncoming] = useState<ClarificationPreview>(),
    [uncertain, setUncertain] = useState(false),
    [stale, setStale] = useState(false),
    [created, setCreated] = useState<Task>(),
    [error, setError] = useState(''),
    [message, setMessage] = useState('')
  const targetMatches = task.branch_id === activeBranchId,
    unavailable = clarificationUnavailableReason(task),
    sourceChanged = !!preview && previewSource.current !== sourceSignature(task)
  useLeaveGuard(
    targetMatches && !created && !!(clarification || reason || uncertain || (busy && pending.current)),
    '作者来源补充或未核实提交',
  )
  useEffect(() => {
    onLockChange(busy || uncertain)
    return () => onLockChange(false)
  }, [busy, uncertain, onLockChange])
  function persist(nextClarification: string, nextReason: string, capsule = pending.current) {
    sessionStorage.setItem(
      storageKey,
      JSON.stringify({
        version: 1,
        clarification: nextClarification,
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
        if (saved?.version !== 1) throw new Error('旧草稿格式无法核对')
        if (typeof saved.clarification === 'string') setClarification(saved.clarification)
        if (typeof saved.reason === 'string') setReason(saved.reason)
        if (saved.pending) {
          const capsule = readPendingClarification(saved.pending, owner)
          if (!capsule) {
            setUncertain(true)
            throw new Error('原提交无法完整恢复')
          }
          pending.current = capsule
          setPreview(capsule.preview)
          previewSource.current = sourceSignature(capsule.source)
          knownRejected.current = saved.outcome === 'known_rejected'
          if (!knownRejected.current) {
            setClarification(capsule.request.clarification)
            setReason(capsule.request.reason)
          }
          setUncertain(!knownRejected.current)
          setStale(knownRejected.current)
          setOpen(true)
          setMessage(
            knownRejected.current
              ? '上次请求已明确拒绝，补充与旧依据保留；请显式重新读取依据。'
              : '原提交结果尚未核实，已恢复原标识；只查询原提交回执。',
          )
        }
      }
      setReady(true)
    } catch {
      setError('无法完整恢复本窗口草稿或提交标识，原记录保留；不能创建新标识。')
    }
    return () => {
      mounted.current = false
      sequence.current++
    }
  }, [owner, storageKey])
  useEffect(() => {
    if (!ready || created) return
    try {
      persist(clarification, reason)
    } catch {
      setError('无法保留本窗口草稿；提交前会再次核对存储。')
    }
  }, [clarification, reason, ready, created, storageKey])
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
    try {
      const raw = await api<unknown>(
        `/tasks/${encodeURIComponent(source.id)}/author-source-clarification-preview`,
      )
      if (!matches(target, id)) return
      const next = readClarificationPreview(raw, source)
      if (!next) throw new Error('返回依据未对应原情节、待核停点或费用，补充保留；请刷新批次后核对。')
      if (preview) {
        setIncoming(next)
        incomingSource.current = signature
      } else {
        setPreview(next)
        previewSource.current = signature
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
    if (!incoming || inFlight.current || uncertain || !targetMatches) return
    setPreview(incoming)
    previewSource.current = incomingSource.current
    setIncoming(undefined)
    setStale(false)
    setError('')
    pending.current = undefined
    knownRejected.current = false
    try {
      persist(clarification, reason, undefined)
    } catch {
      setError('无法保留补充草稿，未提交。')
    }
  }
  async function publish(raw: unknown, capsule: PendingClarification, target: string, id: number) {
    const child = readClarificationChild(raw, capsule)
    if (!child) throw new Error('返回任务未对应原来源、补充或预算，结果仍未核实；请查询原标识。')
    if (!matches(target, id)) return
    setCreated(child)
    setUncertain(false)
    setStale(false)
    setError('')
    setMessage('独立子任务已建立，原情节和历史保留；后续模型工作仍须核对。')
    pending.current = undefined
    try {
      sessionStorage.removeItem(storageKey)
    } catch {
      setError('子任务已建立，但本窗口草稿未能清理；原标识仍可查询。')
    }
    try {
      await onCreated(child)
    } catch {
      if (matches(target, id)) setError('子任务已建立，但工作台刷新失败，已保存回执保留。')
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
    !!clarification.trim() &&
    Array.from(clarification).length <= 8000 &&
    Array.from(reason).length <= 2000 &&
    preview.account.configured &&
    preview.account.priced
  async function submit() {
    if (inFlight.current || composing.current || !canSubmit || !preview) return
    const capsule: PendingClarification = {
      source: task,
      preview,
      request: {
        version: AUTHOR_CLARIFICATION_VERSION,
        client_request_id: crypto.randomUUID(),
        preview_basis_sha256: preview.request_basis.preview_basis_sha256,
        clarification,
        actor: 'author',
        reason,
      },
    }
    knownRejected.current = false
    try {
      persist(clarification, reason, capsule)
    } catch {
      setError('无法保留稳定提交标识，未创建任务。')
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
        `/tasks/${encodeURIComponent(task.id)}/author-source-clarifications`,
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
          persist(clarification, reason, capsule)
        } catch {
          /* Frozen original request remains in memory. */
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
        `/tasks/${encodeURIComponent(capsule.source.id)}/author-source-clarifications?${new URLSearchParams({ client_request_id: capsule.request.client_request_id })}`,
      )
      if (!matches(target, id)) return
      const child = readClarificationLookup(raw, capsule)
      if (child === undefined) throw new Error('回执与原目标或补充不符，原请求保留；不能创建另一任务。')
      if (child === null) {
        setMessage('此刻未查到原提交，不代表永远未创建；请稍后继续只读查询。')
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
    <details className="notice" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>补充作者情节来源，保留原话与费用</summary>
      <section aria-label="作者来源补充">
        <p>
          来源仍待核对不代表作者写错。可补充原话中角色称谓、回指或范围的说明；无需为范围明确的角色新增姓名。真正改变剧情方向请另建批次。
        </p>
        <p>
          提交会创建并开始独立子任务，产生后续模型调用。先独立核对补充与原意及创作约定；通过后按原策略继续正文、审查与维护。原任务、情节、待核报告和费用保留。
        </p>
        {!targetMatches && (
          <p role="status">批次属于其他工作版本，请先在顶栏选择对应版本；草稿与原提交保留。</p>
        )}
        {unavailable && !uncertain && !created && <p className="small">{unavailable}</p>}
        <button
          type="button"
          disabled={busy || uncertain || disabled || !ready || !targetMatches || !!unavailable}
          onClick={() => void load()}
        >
          {preview ? '重新读取来源补充依据' : '读取来源补充依据'}
        </button>
        {busy && <p role="status">正在核对本次请求…</p>}
        {error && <p role="alert">{error}</p>}
        {message && <p role="status">{message}</p>}
        {sourceChanged && !created && (
          <p role="status">任务检查点已更新，补充保留；请显式重新读取确认。未核实原提交仍只能查询。</p>
        )}
        {incoming && (
          <div className="notice">
            <p>已读取新的来源与费用依据，确认后保留补充使用新依据，尚未提交。</p>
            <button type="button" disabled={busy || uncertain} onClick={acceptIncoming}>
              确认使用新依据，保留补充
            </button>
          </div>
        )}
        {preview && targetMatches && (
          <>
            <h4>第 {preview.chapter} 章 · 来源仍待核对，尚无正文候选</h4>
            <h5>原作者情节</h5>
            <pre className="report">{preview.original_source.text}</pre>
            {preview.current_source.text !== preview.original_source.text && (
              <details>
                <summary>此前已保存的来源补充</summary>
                <pre className="report">{preview.current_source.text}</pre>
              </details>
            )}
            <details>
              <summary>原来源待核报告与技术依据</summary>
              <pre className="report">
                {JSON.stringify(
                  {
                    source_review: preview.source_review,
                    baseline: preview.baseline,
                    policies: preview.policies,
                    budget: preview.budget,
                  },
                  null,
                  2,
                )}
              </pre>
            </details>
            <p>
              沿用账户：{preview.account.id} · {preview.account.model}。原任务已付：
              {amount(preview.budget.source_task_settled, preview.account.currency)}； 名义剩余额度：
              {amount(preview.budget.remaining_run_limit, preview.account.currency)}； 扣除辅助费用后可用：
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
              补充由作者明确提交，不代表文学认可。
            </p>
            <label>
              本章补充说明（作者）
              <textarea
                rows={5}
                value={clarification}
                maxLength={16000}
                disabled={busy || uncertain || !!created}
                onChange={(event) => setClarification(event.target.value)}
                onCompositionStart={() => {
                  composing.current = true
                }}
                onCompositionEnd={() => {
                  composing.current = false
                }}
              />
            </label>
            <p className="small">{Array.from(clarification).length} / 8000 字；补充与原话分开保存。</p>
            {Array.from(clarification).length > 8000 && (
              <p role="alert">本章补充最多 8000 字，请缩短；当前输入保留。</p>
            )}
            <label>
              本次补充原因（可空，仅留作记录）
              <textarea
                rows={2}
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
            {Array.from(reason).length > 2000 && (
              <p role="alert">补充原因最多 2000 字，请缩短；当前输入保留。</p>
            )}
            <div className="actions">
              <button type="button" className="primary" disabled={!canSubmit} onClick={() => void submit()}>
                创建并开始来源澄清子任务
              </button>
              {uncertain && (
                <button type="button" disabled={busy || disabled} onClick={() => void lookup()}>
                  查询原来源补充回执
                </button>
              )}
            </div>
            {uncertain && (
              <p role="status">
                提交结果尚未核实，原标识、来源和补充已保留。不会重复提交或生成新标识；只查询原回执。
              </p>
            )}
          </>
        )}
        {created && <AuthorSourceClarificationReceipt task={created} />}
      </section>
    </details>
  )
}
