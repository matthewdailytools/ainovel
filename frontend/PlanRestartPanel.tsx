import { useEffect, useRef, useState } from 'react'
import { api, ApiError, type Task } from './api'
import { useLeaveGuard } from './leaveGuard'
import {
  canPreviewRestart,
  canonical,
  emptyChapterMatches,
  object,
  OPEN_DETAILS_VERSION,
  readPendingRestart,
  readRestartChild,
  readRestartLookup,
  readRestartPreview,
  RESTART_VERSION,
  SOURCE_ROLES_VERSION,
  sourceRolesAnchorsMatch,
  sourceSignature,
  type PendingRestart,
  type RestartPreview,
} from './planRestart'

const failureText = (error: unknown) =>
  error instanceof Error ? error.message : '读取失败，当前原因与原请求保留。'
const amount = (value: number | null, currency: string) =>
  value === null ? '继续受服务商共享上限保护' : `${value.toFixed(6)} ${currency}`

export function PlanRestartReceipt({ task }: { task: Task }) {
  const receipt = object(object(task.result)?.plan_preparation_restart)
  const request = object(receipt?.request),
    budget = object(receipt?.budget)
  const sourceRolesVersion = receipt?.workflow_plan_source_roles_version
  if (
    receipt?.version !== RESTART_VERSION ||
    object(task.result)?.workflow_plan_open_details_version !== OPEN_DETAILS_VERSION ||
    (sourceRolesVersion !== undefined && sourceRolesVersion !== SOURCE_ROLES_VERSION) ||
    object(task.result)?.workflow_plan_source_roles_version !== sourceRolesVersion ||
    object(task.payload)?.workflow_plan_source_roles_version !== sourceRolesVersion ||
    request?.workflow_plan_source_roles_version !== sourceRolesVersion ||
    !sourceRolesAnchorsMatch(task) ||
    !request ||
    typeof receipt.source_task_id !== 'string'
  )
    return null
  return (
    <section className="notice" aria-label="写前重核新任务回执">
      <h4>写前重核新任务已建立</h4>
      <p>
        本任务承接原第 {String(request.chapter)}{' '}
        章检查点，原章纲、旧失败与累计费用保留；尚不表示正文或文学质量通过。
      </p>
      <p>重核原因：{typeof request.reason === 'string' ? request.reason : '请查看原始回执。'}</p>
      {sourceRolesVersion === SOURCE_ROLES_VERSION && (
        <>
          <p>本任务重新核对原章纲的角色与限制范围；许可不代表已经交付，原事件、阶段和接收限制仍须保持。</p>
          <p>
            新任务会按原策略自动开始，写前核对通过后继续正文与后续流程；
            {task.payload.adoption === 'automatic' ? '本章检查通过后自动采纳。' : '逐章由作者确认采纳。'}
          </p>
        </>
      )}
      <p className="small">
        原任务已付：{String(budget?.source_task_settled ?? '待核对')}；新任务沿用剩余额度：
        {String(budget?.remaining_run_limit ?? '服务商共享上限')}，不更换账户或重置费用。
      </p>
      <details>
        <summary>查看写前重核来源与原提交回执</summary>
        <pre className="report">{JSON.stringify(receipt, null, 2)}</pre>
      </details>
    </section>
  )
}

export function PlanRestartPanel({
  task,
  activeBranchId,
  currency,
  disabled = false,
  onCreated,
  onLockChange,
}: {
  task: Task
  activeBranchId: string
  currency: string
  disabled?: boolean
  onCreated: (task: Task) => Promise<void>
  onLockChange: (locked: boolean) => void
}) {
  const owner = canonical([task.novel_id, task.branch_id, task.id])
  const scope = canonical([owner, activeBranchId])
  const liveScope = useRef(scope)
  liveScope.current = scope
  const mounted = useRef(true),
    sequence = useRef(0),
    inFlight = useRef(false),
    composing = useRef(false)
  const [open, setOpen] = useState(false),
    [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<RestartPreview>(),
    [incoming, setIncoming] = useState<RestartPreview>()
  const [reason, setReason] = useState(''),
    [error, setError] = useState(''),
    [message, setMessage] = useState('')
  const [stale, setStale] = useState(false),
    [uncertain, setUncertain] = useState(false),
    [completed, setCompleted] = useState(false)
  const [storageReady, setStorageReady] = useState(false),
    [created, setCreated] = useState<Task>()
  const pending = useRef<PendingRestart | undefined>(undefined),
    previewSource = useRef(''),
    incomingSource = useRef('')
  const knownRejected = useRef(false)
  const storageKey = `ainovel-plan-restart:${owner}`
  const targetMatches = task.branch_id === activeBranchId
  const sourceChanged = !!preview && previewSource.current !== sourceSignature(task)
  const capable = canPreviewRestart(task)
  const sourceRolesMode =
    object(task.result)?.workflow_plan_open_details_version === OPEN_DETAILS_VERSION ||
    preview?.workflow_plan_source_roles_version === SOURCE_ROLES_VERSION
  const hasRestart =
    task.kind === 'workflow' &&
    task.payload.mode === 'automatic' &&
    task.payload.plot_contract_check === true &&
    task.result.workflow_plan_preparation_version === 'workflow-plan-preparation/1'
  const dirty = targetMatches && !completed && Boolean(reason || uncertain || (busy && pending.current))
  useLeaveGuard(dirty, '写前重核原因或未核实提交')
  useEffect(() => {
    onLockChange(busy || uncertain)
    return () => onLockChange(false)
  }, [busy, uncertain, onLockChange])

  function persist(nextReason: string, request = pending.current) {
    sessionStorage.setItem(
      storageKey,
      JSON.stringify({
        version: 1,
        reason: nextReason,
        pending: request,
        outcome: knownRejected.current ? 'known_rejected' : 'unverified',
      }),
    )
  }
  useEffect(() => {
    mounted.current = true
    try {
      const raw = sessionStorage.getItem(storageKey)
      if (raw) {
        const stored = object(JSON.parse(raw))
        if (stored?.version === 1) {
          if (typeof stored.reason === 'string' && stored.reason.length <= 2000) setReason(stored.reason)
          const prior = readPendingRestart(stored.pending, owner)
          if (stored.pending && !prior) {
            setUncertain(true)
            throw new Error('旧提交依据无法完整读取。')
          }
          if (prior) {
            pending.current = prior
            setPreview(prior.preview)
            previewSource.current = sourceSignature(prior.source)
            setReason(prior.request.reason)
            knownRejected.current = stored.outcome === 'known_rejected'
            setUncertain(!knownRejected.current)
            setStale(knownRejected.current)
            setOpen(true)
            setMessage(
              knownRejected.current
                ? '上次请求已被明确拒绝，原因与旧依据已恢复；请显式重新读取依据。'
                : '上次提交结果尚未核实，已恢复原标识；请只查询原提交回执。',
            )
          }
        }
      }
      setStorageReady(true)
    } catch {
      setError('本窗口无法完整读取提交草稿；原记录保留，不能生成新标识。请先核对浏览器中的原提交记录。')
    }
    return () => {
      mounted.current = false
      sequence.current++
    }
  }, [storageKey, owner])
  useEffect(() => {
    if (!storageReady || completed) return
    try {
      persist(reason)
    } catch {
      setError('无法在本窗口保留原因草稿；提交前会再次核对本地存储。')
    }
  }, [reason, storageReady, completed, storageKey])

  const matches = (target: string, id: number) =>
    mounted.current && liveScope.current === target && sequence.current === id
  async function load(refresh: boolean) {
    if (inFlight.current || uncertain || disabled || !storageReady || !targetMatches || !capable) return
    inFlight.current = true
    const target = scope,
      id = ++sequence.current,
      source = task,
      signature = sourceSignature(task)
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const raw = await api<unknown>(`/tasks/${encodeURIComponent(source.id)}/reprepare-plan-preview`)
      if (!matches(target, id)) return
      const next = readRestartPreview(raw, source)
      if (!next)
        throw new Error(
          '服务端依据的目标、原章纲或版本无法对应当前批次；原因草稿保留，请刷新批次后重新读取。',
        )
      const chapter = await api<unknown>(`/chapters/${encodeURIComponent(next.chapter_id)}`)
      if (!matches(target, id)) return
      if (!emptyChapterMatches(chapter, next))
        throw new Error('本章已有正文、版本或目标变化；不能在写作后重选工作细节。')
      if (refresh && preview) {
        setIncoming(next)
        incomingSource.current = signature
      } else {
        setPreview(next)
        previewSource.current = signature
        setIncoming(undefined)
        setStale(false)
        setCompleted(false)
        pending.current = undefined
        knownRejected.current = false
        persist(reason, undefined)
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
    setCompleted(false)
    pending.current = undefined
    knownRejected.current = false
    try {
      persist(reason, undefined)
    } catch {
      setError('无法保留草稿，未提交。')
    }
  }
  async function publish(raw: unknown, capsule: PendingRestart, target: string, id: number) {
    const child = readRestartChild(raw, capsule)
    if (!child) throw new Error('返回任务未对应原提交、来源或剩余预算，结果仍未核实；请查询原标识。')
    if (!matches(target, id)) return
    setCreated(child)
    setCompleted(true)
    setUncertain(false)
    setStale(false)
    setError('')
    setMessage('新任务已建立；原章纲和旧调用保留，后续输出仍须核对。')
    pending.current = undefined
    try {
      sessionStorage.removeItem(storageKey)
    } catch {
      setError('新任务已建立，但本窗口草稿标记未能清理；原标识仍可只读查询。')
    }
    try {
      await onCreated(child)
    } catch {
      if (matches(target, id)) setError('新任务已建立，但工作台刷新失败；已保存的回执保留。')
    }
  }
  const canSubmit =
    preview &&
    capable &&
    targetMatches &&
    storageReady &&
    !disabled &&
    !busy &&
    !stale &&
    !sourceChanged &&
    !incoming &&
    !uncertain &&
    !completed &&
    !!reason.trim()
  async function submit() {
    if (inFlight.current || composing.current || !canSubmit || !preview) return
    const values = { ...preview.request_basis, reason: reason.trim() }
    const previous = pending.current
    const capsule: PendingRestart =
      previous &&
      canonical({ ...previous.request, client_request_id: '' }) ===
        canonical({ ...values, client_request_id: '' })
        ? previous
        : { source: task, preview, request: { ...values, client_request_id: crypto.randomUUID() } }
    knownRejected.current = false
    try {
      persist(reason, capsule)
    } catch {
      setError('无法在本窗口保留稳定提交标识，未派发任务。请先解决浏览器存储问题。')
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
        `/tasks/${encodeURIComponent(capsule.source.id)}/reprepare-plan`,
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
          persist(reason, capsule)
        } catch {
          /* Original intent remains unverified after reload. */
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
        `/tasks/${encodeURIComponent(capsule.source.id)}/plan-restarts/${encodeURIComponent(capsule.request.client_request_id)}`,
      )
      if (!matches(target, id)) return
      const child = readRestartLookup(raw, capsule)
      if (child === undefined) throw new Error('回执目标或内容不一致，原请求保留；不能据此创建另一任务。')
      if (child === null) {
        setMessage('此刻尚未查到原提交回执，不代表任务永远未创建；请稍后继续只读查询。')
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

  if (!hasRestart) return null
  return (
    <details
      className="notice plan-restart"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        {sourceRolesMode ? '重做写前核对，保留原规划与费用' : '补齐AI粗纲未明确的工作细节并重新核对'}
      </summary>
      <section aria-label="AI粗纲写前重核">
        {sourceRolesMode ? (
          <p>
            为尚未开始正文的本章初次解释新建任务，核对原章纲中的角色与限制范围；完整原规划、明确事件、阶段、固定条件、旧失败及费用保留。通过后按原策略继续正文与后续流程。许可不等于资料已经交付，也不能清空原有接收限制。
          </p>
        ) : (
          <p>
            只为本章原AI工作规划尚未明确的执行细节新建任务；保留全部原事项、固定条件与旧失败。选择是工作计划，不是已发生事实，文学质量与整书完成仍另行验证。
          </p>
        )}
        {!targetMatches && (
          <p role="status">
            所选批次属于其他工作版本，请先在顶栏选择对应版本再使用写前重核。原原因与未核实提交仍保留。
          </p>
        )}
        {!capable && !uncertain && !completed && (
          <p className="small">
            {sourceRolesMode
              ? '仅全自动尚未开始正文的初次解释失败可使用。服务端会核对三份完整已结调用；已有选择或保持核对不能借此清空轮次。'
              : '仅旧政策、全自动且尚未开始正文的已知写前解释失败可使用；服务端会核对三份完整已结调用与空稿来源。'}
          </p>
        )}
        <button
          type="button"
          disabled={busy || uncertain || disabled || !storageReady || !targetMatches || !capable}
          onClick={() => void load(!!preview)}
        >
          {preview ? '重新读取写前重核依据' : '读取写前重核依据'}
        </button>
        {busy && <p role="status">正在核对本次请求…</p>}
        {error && <p role="alert">{error}</p>}
        {message && <p role="status">{message}</p>}
        {sourceChanged && !completed && (
          <p role="status">
            任务检查点已更新，原因草稿保留；请显式重新读取并确认依据。原未核实请求仍只能查询。
          </p>
        )}
        {incoming && (
          <div className="notice">
            <p>已读到新的服务端依据。确认后保留原因，使用新版本；尚未提交。</p>
            <button type="button" disabled={busy || uncertain} onClick={acceptIncoming}>
              确认使用新依据，保留原因
            </button>
          </div>
        )}
        {preview && targetMatches && (
          <>
            <h4>第 {preview.request_basis.chapter} 章 · 写前解释尚未形成有效结果</h4>
            <p>已核对本章正式修订 0、正文为空；三份旧解释完整且已结算，没有将旧失败当成已通过。</p>
            <h4>原AI章纲</h4>
            <pre className="stream">{preview.original_plot}</pre>
            <p>
              原账户：{task.payload.account}；原任务累计已付{' '}
              {amount(preview.budget.source_task_settled, currency)}。新任务仅沿用剩余额度{' '}
              {amount(preview.budget.remaining_run_limit, currency)}，共享预算和历史费用不重置。
            </p>
            <p className="small">
              {preview.workflow_plan_source_roles_version === SOURCE_ROLES_VERSION
                ? '创建后会自动开始并产生后续模型调用，先重新解释原章纲，再按原政策处理开放细节与严格保持核对，最多两轮选择；通过后继续正文与后续流程，原章纲和已有正文保留。'
                : '重核会产生后续模型调用；仅追加开放细节选择和严格逐项保持核对，最多两轮选择。不改写原章纲或当前稿件，不把缺失参与者猜成已确定。'}
            </p>
            {preview.workflow_plan_source_roles_version === SOURCE_ROLES_VERSION && (
              <p className="small">
                沿用原采纳策略：
                {task.payload.adoption === 'automatic' ? '本章检查通过后自动采纳。' : '逐章由作者确认采纳。'}
              </p>
            )}
            <details>
              <summary>三次已结失败调用与来源</summary>
              <ul>
                {preview.known_invalid_steps.call_ids.map((call) => (
                  <li key={call}>{call} · 已结，原结果保留</li>
                ))}
              </ul>
              <pre className="report">
                {JSON.stringify(
                  {
                    original_plot_sha256: preview.original_plot_sha256,
                    old_preparation_sha256: preview.old_preparation_sha256,
                    request_basis: preview.request_basis,
                    known_invalid_steps: preview.known_invalid_steps,
                    budget: preview.budget,
                    ...(preview.workflow_plan_source_roles_version
                      ? { workflow_plan_source_roles_version: preview.workflow_plan_source_roles_version }
                      : {}),
                  },
                  null,
                  2,
                )}
              </pre>
            </details>
            <label>
              本次写前重核原因
              <textarea
                rows={3}
                maxLength={2000}
                value={reason}
                disabled={busy || uncertain || completed}
                onChange={(event) => setReason(event.target.value)}
                onCompositionStart={() => {
                  composing.current = true
                }}
                onCompositionEnd={() => {
                  composing.current = false
                }}
              />
            </label>
            <div className="actions">
              <button className="primary" type="button" disabled={!canSubmit} onClick={() => void submit()}>
                {sourceRolesMode ? '创建并开始新任务' : '创建新任务重新核对'}
              </button>
              {uncertain && (
                <button type="button" disabled={busy || disabled} onClick={() => void lookup()}>
                  查询原提交回执
                </button>
              )}
            </div>
            {uncertain && (
              <p role="status">
                提交结果尚未核实，原标识与正文目标已保留。不会重复提交或生成新标识；请查询原提交回执。
              </p>
            )}
          </>
        )}
        {created && <PlanRestartReceipt task={created} />}
      </section>
    </details>
  )
}
