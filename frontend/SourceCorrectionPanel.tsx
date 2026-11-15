import { useEffect, useRef, useState } from 'react'
import { api, ApiError, type Artifact, type Chapter, type Task } from './api'
import { useLeaveGuard } from './leaveGuard'
import { parseRecheckBudget } from './candidateRecheck'
import { PlotAuditDetails } from './PlotAuditDetails'
import { ConditionalReviewNotice } from './ConditionalReviewNotice'
import {
  canonical,
  correctionKindLabel,
  correctionOwner,
  canAdvanceCorrectionPhase,
  createCorrectionOperation,
  digest,
  object,
  ownerKey,
  readCorrectedReport,
  readRecheckPreview,
  readCorrectionTask,
  sourceReceiptTargetCurrent,
  capsuleMatchesCurrent,
  readSourcePreview,
  readSourceReceipt,
  sourceCorrectionEligibility,
  SOURCE_CORRECTION_KIND,
  type Capsule,
  type OperationState,
  type Phase,
  type RecheckPreview,
  type SourcePreview,
  type SourceReceipt,
} from './sourceCorrection'

const errorText = (value: unknown) =>
  value instanceof Error ? value.message : '读取失败，输入与原提交记录保留。'
const resultLabel = {
  pass: '模型标保持',
  fail: '模型标不符',
  unknown: '仍未确认',
}
export function SourceReceiptView({
  receipt,
  reason,
  historical = false,
}: {
  receipt: SourceReceipt
  reason?: string
  historical?: boolean
}) {
  return (
    <section aria-label="来源解释纠偏只读回执" style={{ overflowWrap: 'anywhere' }}>
      <h4>
        来源解释纠偏 ·{' '}
        {historical
          ? '历史回执，当前依据已失效'
          : receipt.state === 'ready'
            ? '回执就绪（当时记录）'
            : '仍待核对'}{' '}
        · 只读
      </h4>
      <p>
        仅核对原情节的主体、指令性质和确定性。模型解释不是作者新增条件；本回执没有检查、生成或采用正文。就绪只表示这份来源回执可供显式复检，当前依据仍须重新读取。
      </p>
      {reason !== undefined && <p>操作原因（仅供审计）：{reason || '未填写'}</p>}
      <p>
        选定纠偏范围：{receipt.snapshot.item_ids.join('、')}
        。未选事项及全部事件、数值、否定、方向、时点和结果保持。
      </p>
      <details>
        <summary>本章原情节与原解释</summary>
        <pre className="stream">{receipt.snapshot.plot.plot}</pre>
      </details>
      {receipt.snapshot.prepared_contract.items.map((before, i) => {
        const proposal = receipt.proposal.items[i],
          check = receipt.preservation.item_checks[i]
        return (
          <article className="fact" key={before.id}>
            <strong>
              {before.id} · {receipt.snapshot.item_ids.includes(before.id) ? '选定事项' : '原事项保持'}
            </strong>
            <blockquote>{before.author_evidence}</blockquote>
            <p>
              主体：{before.actor || '原记录未指定'} →{' '}
              {proposal.actor ||
                (proposal.statement_kind === 'narrative_requirement'
                  ? '不锁定故事内讲述者'
                  : '未指定主体（原记录保持）')}
              ；确定性：
              {before.certainty === 'explicit' ? '明确' : '存在歧义'} →{' '}
              {proposal.certainty === 'explicit' ? '明确' : '仍有歧义'}。
            </p>
            <p>
              性质：原解释未单列 → {proposal.statement_kind === 'story_event' ? '故事行动' : '叙述交代要求'}。
            </p>
            <p>{proposal.interpretation_reason}</p>
            <blockquote>{proposal.evidence}</blockquote>
            <p>
              全部事项保持核对：{resultLabel[check.result]}。{check.reason}
            </p>
            <blockquote>{check.evidence}</blockquote>
          </article>
        )
      })}
      <details>
        <summary>全部固定条件保持（不是正文已兑现）</summary>
        {receipt.preservation.fixed_checks.length === 0 && <p>本次来源没有固定条件清单。</p>}
        {receipt.preservation.fixed_checks.map((check) => (
          <article className="fact" key={check.condition_id}>
            <strong>
              {check.condition_id} · {resultLabel[check.result]}
            </strong>
            <blockquote>{check.evidence}</blockquote>
            <p>{check.reason}</p>
          </article>
        ))}
      </details>
      <p>{receipt.preservation.summary}</p>
      <p>{receipt.note}</p>
      <p className="small muted">
        绑定候选：{receipt.snapshot.artifact_id} · 候选修订
        {receipt.snapshot.candidate_revision}；来源版本：{receipt.version}
        。原解释和原稿均保留。
      </p>
    </section>
  )
}
export function SourceCorrectionTaskNotice({ task }: { task: Task }) {
  const label = correctionKindLabel(task.kind)
  if (!label) return null
  if (
    !object(task.payload) ||
    !object(task.result) ||
    typeof task.id !== 'string' ||
    typeof task.status !== 'string' ||
    typeof task.error !== 'string' ||
    typeof task.payload.account !== 'string'
  )
    return <p role="status">来源解释任务记录无法完整读取，请查看原始任务，不能作为成功结论。</p>
  const result: Record<string, unknown> = task.result
  return (
    <section className="notice" aria-label="来源解释独立任务状态">
      <strong>
        {label} · {task.status}
      </strong>
      <p>
        {task.kind === SOURCE_CORRECTION_KIND
          ? object(result.source_correction_proposal)
            ? '来源提案已保存，独立保持核对仍按任务记录执行；没有读取候选正文。'
            : '两个独立来源步骤仅核对原情节和解释，未生成或修改正文。'
          : '按独立来源回执复检原候选；不生成、采纳或恢复正文。'}
      </p>
      {task.error && <p role="status">{task.error}</p>}
      <p className="small muted">
        任务 {task.id} · 账户 {task.payload.account}；步骤与累计费用保留。
      </p>
    </section>
  )
}

type Prepared = { phase: 'source'; value: SourcePreview } | { phase: 'recheck'; value: RecheckPreview }
export function SourceCorrectionPanel({
  artifact,
  chapter,
  novelId,
  branchId,
  account,
  modelReady,
  disabled,
  unsaved,
  artifacts,
  onTask,
  onRefresh,
}: {
  artifact: Artifact
  chapter?: Chapter
  novelId: string
  branchId: string
  account: string
  modelReady: boolean
  disabled: boolean
  unsaved: boolean
  artifacts: Artifact[]
  onTask: (task: Task) => void
  onRefresh: () => Promise<void>
}) {
  const owner = correctionOwner(artifact, novelId, branchId)
  const scope = owner ? ownerKey(owner) : canonical([novelId, branchId, artifact.id])
  const signature = canonical([
    artifact.candidate_revision,
    artifact.base_revision,
    artifact.payload,
    artifact.basis,
    chapter?.id,
    chapter?.revision,
  ])
  const current = useRef({ scope, signature, unsaved, disabled, modelReady })
  current.current = { scope, signature, unsaved, disabled, modelReady }
  const mounted = useRef(true),
    sequence = useRef(0),
    composing = useRef(false)
  const engine = useRef<ReturnType<typeof createCorrectionOperation> | undefined>(undefined)
  const [operation, setOperation] = useState<OperationState>({
    status: 'idle',
    message: '',
  })
  const [prepared, setPrepared] = useState<Prepared>(),
    [incoming, setIncoming] = useState<Prepared>()
  const previewSignature = useRef(''),
    incomingSignature = useRef('')
  const trackedCapsule = useRef<Capsule | undefined>(undefined)
  const completedTasks = useRef(new Set<string>())
  const [reason, setReason] = useState(''),
    [selected, setSelected] = useState<string[]>([]),
    [budget, setBudget] = useState('')
  const [selectedReceipt, setSelectedReceipt] = useState(''),
    [receipt, setReceipt] = useState<{
      artifact: Artifact
      data: SourceReceipt
      reason?: string
      bindingSignature: string
      current: boolean
      bindingStatus: 'current' | 'historical' | 'unavailable'
    }>()
  const [task, setTask] = useState<Task>(),
    [error, setError] = useState(''),
    [loading, setLoading] = useState(false),
    [source, setSource] = useState<unknown>()
  const [storageReady, setStorageReady] = useState(false),
    [submitted, setSubmitted] = useState('')
  const draftSignature = canonical([selected, reason, budget])
  const draftKey = `ainovel.source-correction.draft.1:${scope}`
  const budgetValue = parseRecheckBudget(budget)
  const locked =
    loading ||
    operation.status === 'sending' ||
    operation.status === 'uncertain' ||
    operation.status === 'unavailable'
  const stale =
    prepared !== undefined &&
    (previewSignature.current !== signature ||
      (prepared.phase === 'recheck' && prepared.value.receipt_id !== selectedReceipt))
  const currentReceipt = receipt?.artifact.id === selectedReceipt ? receipt : undefined
  const receiptCurrent =
    currentReceipt !== undefined && currentReceipt.current && currentReceipt.bindingSignature === signature
  useLeaveGuard(
    operation.status === 'sending' ||
      operation.status === 'uncertain' ||
      (draftSignature !== submitted && (!!reason || selected.length > 0 || !!budget)),
    '来源解释纠偏范围、原因或待查提交',
  )
  useEffect(() => {
    mounted.current = true
    try {
      const storage = window.localStorage
      const control = createCorrectionOperation(scope, api, storage, (next) => {
        if (mounted.current && current.current.scope === scope) setOperation(next)
      })
      engine.current = control
      setOperation(control.state)
      const raw = storage.getItem(draftKey)
      if (raw) {
        const draft: unknown = JSON.parse(raw)
        if (
          object(draft) &&
          typeof draft.reason === 'string' &&
          [...draft.reason].length <= 4000 &&
          typeof draft.budget === 'string' &&
          draft.budget.length <= 100 &&
          Array.isArray(draft.selected) &&
          draft.selected.length <= 12 &&
          draft.selected.every((id) => typeof id === 'string' && /^P(?:0[1-9]|1[0-2])$/u.test(id))
        ) {
          setReason(draft.reason)
          setBudget(draft.budget)
          setSelected(draft.selected)
          if (typeof draft.submitted === 'string') setSubmitted(draft.submitted)
        }
      }
      if (control.state.pending) {
        const capsule = control.state.pending
        if (capsule.phase === 'source' && 'item_ids' in capsule.request) {
          setReason(capsule.request.reason)
          setSelected(capsule.request.item_ids)
          setBudget(capsule.request.budget_limit === undefined ? '' : `${capsule.request.budget_limit}`)
        }
      }
      setStorageReady(true)
    } catch {
      setOperation({
        status: 'unavailable',
        message: '浏览器无法持久保存提交标识，未派发模型请求。',
      })
    }
    return () => {
      mounted.current = false
      sequence.current++
      engine.current?.dispose()
    }
  }, [scope, draftKey])
  useEffect(() => {
    if (!storageReady) return
    try {
      localStorage.setItem(draftKey, JSON.stringify({ reason, selected, budget, submitted }))
    } catch {
      setStorageReady(false)
      setError('范围草稿无法保存，提交前必须先恢复浏览器存储。')
    }
  }, [storageReady, draftKey, reason, selected, budget, submitted])
  useEffect(() => {
    let alive = true
    void api<unknown>(`/tasks/${encodeURIComponent(artifact.task_id || '')}`)
      .then((row) => {
        if (alive) setSource(row)
      })
      .catch((failure) => {
        if (alive) setError(errorText(failure))
      })
    return () => {
      alive = false
    }
  }, [artifact.task_id, scope])
  useEffect(() => {
    if (operation.status !== 'confirmed' || !operation.task) return
    trackedCapsule.current = operation.pending
    setTask(operation.task)
    onTask(operation.task)
    if (operation.pending?.phase === 'source') setSubmitted(draftSignature)
  }, [operation.status, operation.task?.id])
  useEffect(() => {
    if (!task) return
    let alive = true,
      flight = false
    const poll = async () => {
      if (flight) return
      flight = true
      try {
        const raw = await api<unknown>(`/tasks/${encodeURIComponent(task.id)}`)
        if (!alive || !trackedCapsule.current) return
        const updated = readCorrectionTask(raw, trackedCapsule.current)
        if (!updated || updated.id !== task.id)
          throw new Error('当前任务回执无法绑定原请求，保留记录，不发布检查结论。')
        setTask(updated)
        if (updated.status === 'succeeded') {
          const artifactId = updated.result.artifact_id
          if (typeof artifactId === 'string' && updated.kind === SOURCE_CORRECTION_KIND)
            setSelectedReceipt(artifactId)
          if (updated.kind !== SOURCE_CORRECTION_KIND && !completedTasks.current.has(updated.id)) {
            completedTasks.current.add(updated.id)
            const original = trackedCapsule.current
            const now = current.current.signature
            if (
              original &&
              (await capsuleMatchesCurrent(original, now)) &&
              alive &&
              current.current.scope === scope &&
              current.current.signature === now
            )
              await onRefresh()
          }
        }
      } catch (failure) {
        if (alive) setError(errorText(failure))
      } finally {
        flight = false
      }
    }
    void poll()
    const timer = setInterval(() => {
      if (task.status === 'queued' || task.status === 'running') void poll()
    }, 2000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [task?.id, task?.status, branchId])
  useEffect(() => {
    if (!selectedReceipt) {
      setReceipt(undefined)
      return
    }
    let alive = true
    setReceipt(undefined)
    void (async () => {
      try {
        const raw = await api<Artifact>(`/artifacts/${encodeURIComponent(selectedReceipt)}`)
        const data = readSourceReceipt(raw.payload)
        if (!alive) return
        if (
          raw.kind !== SOURCE_CORRECTION_KIND ||
          raw.branch_id !== branchId ||
          !data ||
          data.snapshot.artifact_id !== artifact.id ||
          !raw.task_id
        )
          throw new Error('回执目标或完整事项无法核对，仍保留原始记录。')
        const origin = await api<unknown>(`/tasks/${encodeURIComponent(raw.task_id)}`)
        if (!alive) return
        if (
          !object(origin) ||
          origin.kind !== SOURCE_CORRECTION_KIND ||
          origin.status !== 'succeeded' ||
          !object(origin.result) ||
          origin.result.artifact_id !== raw.id ||
          !object(origin.payload) ||
          canonical(origin.result.source_correction_snapshot) !== canonical(raw.payload.snapshot)
        )
          throw new Error('回执与来源任务无法完整对应，未启用正文复检。')
        const receiptMatches = await sourceReceiptTargetCurrent(data, artifact, branchId)
        let bindingStatus: 'current' | 'historical' | 'unavailable' = receiptMatches
          ? 'unavailable'
          : 'historical'
        if (receiptMatches && owner) {
          try {
            const latest = readSourcePreview(
              await api<unknown>(
                `/artifacts/${encodeURIComponent(artifact.id)}/source-interpretation-correction-preview`,
              ),
              owner,
            )
            if (latest)
              bindingStatus = latest.basis_sha256 === data.snapshot.basis_sha256 ? 'current' : 'historical'
          } catch (failure) {
            if (failure instanceof ApiError && failure.status === 409) bindingStatus = 'historical'
            if (alive) setError('回执已保留，但当前完整依据尚无法核对：' + errorText(failure))
          }
        }
        if (!alive) return
        setReceipt({
          artifact: raw,
          data,
          bindingSignature: signature,
          current: bindingStatus === 'current',
          bindingStatus,
          reason: typeof origin.payload.reason === 'string' ? origin.payload.reason : undefined,
        })
      } catch (failure) {
        if (alive) {
          setReceipt(undefined)
          setError(errorText(failure))
        }
      }
    })()
    return () => {
      alive = false
    }
  }, [selectedReceipt, branchId, artifact.id, signature])
  if (!owner) return null
  const eligibility = sourceCorrectionEligibility(artifact, source, chapter, branchId, unsaved)
  const available = artifacts.filter(
    (row) =>
      row.kind === SOURCE_CORRECTION_KIND &&
      row.branch_id === branchId &&
      readSourceReceipt(row.payload)?.snapshot.artifact_id === artifact.id,
  )
  const blocked = disabled || unsaved || !storageReady || locked || !!eligibility
  const matches = (token: number, expected: string) =>
    mounted.current &&
    sequence.current === token &&
    current.current.scope === scope &&
    current.current.signature === expected
  async function load(phase: Phase) {
    if (blocked || !owner) return
    const token = ++sequence.current,
      expected = signature
    setLoading(true)
    setError('')
    try {
      let next: Prepared
      if (phase === 'source') {
        const raw = await api<unknown>(
          `/artifacts/${encodeURIComponent(artifact.id)}/source-interpretation-correction-preview`,
        )
        const preview = readSourcePreview(raw, owner)
        if (!preview || (await digest(preview.source_text)) !== preview.source_text_sha256)
          throw new Error('来源预览的原文、目标或摘要无法核对，未提交。')
        next = { phase, value: preview }
      } else {
        if (!currentReceipt || !receiptCurrent || currentReceipt.data.state !== 'ready') return
        const raw = await api<unknown>(
          `/artifacts/${encodeURIComponent(artifact.id)}/recheck-source-correction-preview?receipt_id=${encodeURIComponent(currentReceipt.artifact.id)}`,
        )
        const preview = readRecheckPreview(raw, owner, currentReceipt.artifact.id, currentReceipt.data)
        if (!preview) throw new Error('正文复检依据与就绪回执无法对应，未提交。')
        next = { phase, value: preview }
      }
      if (!matches(token, expected)) return
      if (
        canAdvanceCorrectionPhase({
          nextPhase: phase,
          currentPhase: prepared?.phase,
          currentBasis: prepared?.value.basis_sha256,
          hasIncoming: incoming !== undefined,
          inputAvailable: !current.current.unsaved && !current.current.disabled,
          stale,
          operation,
          task,
          receipt: currentReceipt,
          receiptCurrent,
        })
      ) {
        if (!engine.current?.releaseKnown()) return
        setPrepared(next)
        setIncoming(undefined)
        incomingSignature.current = ''
        previewSignature.current = expected
      } else if (prepared || operation.status === 'confirmed' || operation.status === 'rejected') {
        setIncoming(next)
        incomingSignature.current = expected
      } else {
        setPrepared(next)
        previewSignature.current = expected
      }
    } catch (failure) {
      if (matches(token, expected)) {
        setError(errorText(failure))
        previewSignature.current = ''
      }
    } finally {
      if (mounted.current && current.current.scope === scope && sequence.current === token) setLoading(false)
    }
  }
  function acceptIncoming() {
    if (
      !incoming ||
      incomingSignature.current !== signature ||
      unsaved ||
      !!eligibility ||
      locked ||
      !engine.current?.releaseKnown()
    )
      return
    setPrepared(incoming)
    setIncoming(undefined)
    previewSignature.current = signature
    setError('')
  }
  async function submit() {
    if (
      !prepared ||
      !owner ||
      blocked ||
      composing.current ||
      stale ||
      incoming ||
      !modelReady ||
      budgetValue.error ||
      engine.current?.state.status !== 'idle'
    )
      return
    const expectedSignature = signature
    const clientSignature = await digest(expectedSignature)
    if (
      !mounted.current ||
      current.current.scope !== scope ||
      current.current.signature !== expectedSignature ||
      current.current.unsaved ||
      current.current.disabled
    )
      return
    const base = {
      candidate_revision: owner.candidateRevision,
      preview_basis_sha256: prepared.value.basis_sha256,
      account,
      client_request_id: crypto.randomUUID(),
      ...(budgetValue.value === undefined ? {} : { budget_limit: budgetValue.value }),
    }
    let capsule: Capsule
    if (prepared.phase === 'source') {
      if (
        [...reason.trim()].length > 2000 ||
        !selected.length ||
        selected.some((id) => !prepared.value.items.some((item) => item.id === id))
      )
        return
      capsule = {
        phase: 'source',
        client_signature: clientSignature,
        owner,
        preview: prepared.value,
        request: { ...base, item_ids: selected, reason: reason.trim() },
      }
    } else
      capsule = {
        phase: 'recheck',
        client_signature: clientSignature,
        owner,
        preview: prepared.value,
        request: { ...base, receipt_id: prepared.value.receipt_id },
      }
    await engine.current?.start(capsule)
  }
  return (
    <details className="notice" style={{ overflowWrap: 'anywhere' }}>
      <summary>核对原情节解释，再显式复检正文</summary>
      <section aria-label="原情节来源解释纠偏">
        <p>
          用于原解释误认主体、遗漏明确回指或把叙述要求当故事行动。只核对选定原P事项，真正未定的角色选择仍保留未知；原事件、固定条件和原稿保持。
        </p>
        <p>
          来源纠偏包含两个独立模型步骤；就绪后另行读取依据并提交正文复检。每步及失败均可能产生费用，继续受累计预算和原任务剩余额度保护。
        </p>
        {eligibility && <p role="status">{eligibility}</p>}
        {error && <p role="alert">{error}</p>}
        {operation.message && <p role="status">{operation.message}</p>}
        {task && <SourceCorrectionTaskNotice task={task} />}
        {operation.pending && (
          <p className="small">原提交标识：{operation.pending.request.client_request_id}。</p>
        )}
        {(operation.status === 'uncertain' || operation.status === 'confirmed') && (
          <button type="button" disabled={loading} onClick={() => void engine.current?.lookup()}>
            只查询原提交回执（不重发）
          </button>
        )}
        <button type="button" disabled={blocked} onClick={() => void load('source')}>
          {prepared?.phase === 'source' ? '重新读取来源解释依据' : '读取来源解释依据'}
        </button>
        {stale && <p role="status">候选或当前依据已变化，范围和原因保留；请显式重新读取并确认依据。</p>}
        {incoming && (
          <div className="notice">
            <p>已读到新的服务端依据，尚未提交。确认后保留范围和原因并使用新依据。</p>
            <button
              type="button"
              disabled={locked || unsaved || incomingSignature.current !== signature}
              onClick={acceptIncoming}
            >
              确认使用新依据，保留输入
            </button>
          </div>
        )}
        {prepared?.phase === 'source' && (
          <>
            <h4>本章原情节</h4>
            <pre className="stream">{prepared.value.source_text}</pre>
            <fieldset disabled={locked}>
              <legend>选择需要核对的原P事项</legend>
              {prepared.value.items.map((item) => (
                <label className="check" key={item.id}>
                  <input
                    type="checkbox"
                    checked={selected.includes(item.id)}
                    onChange={(event) =>
                      setSelected((rows) =>
                        event.target.checked ? [...rows, item.id] : rows.filter((id) => id !== item.id),
                      )
                    }
                  />
                  {item.id} · {item.certainty === 'ambiguous' ? '存在歧义' : '原标明确'} ·{' '}
                  {item.actor || '未指定主体'} · {item.action}
                  <span>{item.author_evidence}</span>
                </label>
              ))}
            </fieldset>
            <label>
              本次来源解释纠偏原因（可留空）
              <textarea
                rows={3}
                maxLength={4000}
                disabled={locked}
                value={reason}
                onCompositionStart={() => {
                  composing.current = true
                }}
                onCompositionEnd={() => {
                  composing.current = false
                }}
                onChange={(event) => setReason(event.target.value)}
              />
            </label>
            {[...reason.trim()].length > 2000 && (
              <p role="status">原因超过2000个字符，请缩短后提交；当前输入已保留。</p>
            )}
            <p className="small muted">
              原因仅留审计，不发送给模型证明剧情；纠偏不扩展原情节或选择原话没有的人物。
            </p>
          </>
        )}
        <label>
          本次独立任务预算上限（账户币种，可留空）
          <input
            type="number"
            min="0"
            step="any"
            value={budget}
            disabled={locked}
            onChange={(event) => setBudget(event.target.value)}
          />
        </label>
        {budgetValue.error && <p role="status">{budgetValue.error}</p>}
        <p className="small muted">
          留空仍受账户累计预算保护。账户：{account}
          。未知提交仅查原标识，不重发模型调用。
        </p>
        {available.length > 0 && (
          <label>
            已有来源解释回执
            <select
              value={selectedReceipt}
              disabled={loading || operation.status === 'sending'}
              onChange={(event) => setSelectedReceipt(event.target.value)}
            >
              <option value="">选择只读回执</option>
              {available.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.id.slice(0, 8)} ·{' '}
                  {readSourceReceipt(row.payload)?.state === 'ready' ? '回执就绪' : '仍待核对'}
                </option>
              ))}
            </select>
          </label>
        )}
        {currentReceipt && (
          <>
            {currentReceipt.bindingStatus === 'unavailable' && (
              <p role="status">当前完整来源依据尚未核实；回执保留只读，未启用正文复检。</p>
            )}
            <SourceReceiptView
              receipt={currentReceipt.data}
              reason={currentReceipt.reason}
              historical={
                currentReceipt.bindingStatus === 'historical' || currentReceipt.bindingSignature !== signature
              }
            />
            <button
              type="button"
              disabled={
                blocked ||
                !receiptCurrent ||
                currentReceipt.data.state !== 'ready' ||
                currentReceipt.data.snapshot.candidate_revision !== owner.candidateRevision
              }
              onClick={() => void load('recheck')}
            >
              读取就绪回执对应的正文复检依据
            </button>
          </>
        )}
        {prepared?.phase === 'recheck' && (
          <p role="status">
            已读取候选修订{prepared.value.candidate_revision}与回执
            {prepared.value.receipt_id}
            的独立正文复检依据；原文和情节保持。下一步仍由你显式提交。
          </p>
        )}
        {prepared && (
          <>
            <details>
              <summary>本次预览版本与依据</summary>
              <p>
                版本：{prepared.value.version}；候选修订
                {prepared.value.candidate_revision}；故事修订
                {prepared.value.story_revision}。
              </p>
              <p>依据摘要：{prepared.value.basis_sha256}</p>
              <p>{prepared.value.notice}</p>
            </details>
            <button
              type="button"
              disabled={
                blocked ||
                stale ||
                !!incoming ||
                !modelReady ||
                !!budgetValue.error ||
                operation.status !== 'idle' ||
                (prepared.phase === 'source' &&
                  ([...reason.trim()].length > 2000 ||
                    !selected.length ||
                    selected.some((id) => !prepared.value.items.some((item) => item.id === id))))
              }
              onClick={() => void submit().catch((failure) => setError(errorText(failure)))}
            >
              {prepared.phase === 'source'
                ? '提交来源解释纠偏（额外模型费用）'
                : '提交独立正文复检（额外模型费用）'}
            </button>
          </>
        )}
      </section>
    </details>
  )
}

export function SourceCorrectionArtifact({
  artifact,
  branchId,
  onTarget,
  disabled = false,
}: {
  artifact: Artifact
  branchId: string
  onTarget: (artifact: Artifact) => void
  disabled?: boolean
}) {
  const source =
    artifact.branch_id === branchId && artifact.kind === SOURCE_CORRECTION_KIND
      ? readSourceReceipt(artifact.payload)
      : undefined
  const report =
    artifact.branch_id === branchId && artifact.kind !== SOURCE_CORRECTION_KIND
      ? readCorrectedReport(artifact.payload)
      : undefined
  const target = source
    ? {
        artifact_id: source.snapshot.artifact_id,
        revision: source.snapshot.candidate_revision,
        chapter_id: source.snapshot.chapter_id,
        base_revision: source.snapshot.chapter_revision,
        content_sha256: source.snapshot.content_sha256,
      }
    : report?.target
  const [loaded, setLoaded] = useState<{
    key: string
    artifact?: Artifact
    sha256?: string
    current: boolean
    error?: string
  }>()
  const key = canonical([artifact.id, branchId, target])
  useEffect(() => {
    if (!target) return
    let active = true
    void (async () => {
      try {
        const row = await api<Artifact>(`/artifacts/${encodeURIComponent(target.artifact_id)}`)
        if (
          !row ||
          row.id !== target.artifact_id ||
          row.branch_id !== branchId ||
          row.kind !== 'chapter_draft' ||
          row.chapter_id !== target.chapter_id ||
          !object(row.payload) ||
          typeof row.payload.text !== 'string'
        )
          throw new Error('原候选身份或正文无法读取。')
        const sha256 = await digest(row.payload.text)
        if (active)
          setLoaded({
            key,
            artifact: row,
            sha256,
            current:
              row.candidate_revision === target.revision &&
              row.base_revision === target.base_revision &&
              sha256 === target.content_sha256 &&
              row.status !== 'rejected',
          })
      } catch (failure) {
        if (active) setLoaded({ key, current: false, error: errorText(failure) })
      }
    })()
    return () => {
      active = false
    }
  }, [key])
  const state = loaded?.key === key ? loaded : undefined
  if (!source && !report)
    return (
      <section aria-label="来源解释只读记录">
        <p role="status">报告格式无法核对，仅保留原始记录，不能作为有效或通过结论。</p>
        <pre className="report">{JSON.stringify(artifact.payload, null, 2)}</pre>
      </section>
    )
  return (
    <section aria-label="来源解释只读记录" style={{ overflowWrap: 'anywhere' }}>
      <h3>{correctionKindLabel(artifact.kind)} · 只读</h3>
      <p role="status">
        {!state
          ? '正在核对目标候选…'
          : state.current
            ? '目标候选正文与修订吻合；仍须作者审阅，不代表已采纳。'
            : state.error || '目标候选已变化，以下仅为历史记录。'}
      </p>
      {source && (
        <>
          <p>
            这是来源阶段的只读版本记录；是否仍可用于新复检，须回到原候选读取当前依据。后续正文复检报告按其自己的目标版本核对。
          </p>
          <SourceReceiptView receipt={source} historical={state?.current === false} />
        </>
      )}
      {report && (
        <>
          <p>
            {state?.current
              ? report.review_passed
                ? '模型标本轮复检通过；文学质量仍待作者判断。'
                : '本轮正文复检仍有未通过或未能核实的项目。'
              : '当前目标尚未核实，不能显示当前正文通过。'}
          </p>
          <p>{report.note}</p>
          <p>关联来源回执：{report.receipt_id}。本报告没有采纳或恢复任务。</p>
          <ConditionalReviewNotice value={report.review} />
          <PlotAuditDetails
            value={report.plot_audit}
            candidateRevision={state?.artifact?.candidate_revision}
            candidateSha256={state?.sha256}
            historical={state?.current === false}
            targetStatus={
              !state ? 'loading' : state.artifact ? (state.current ? 'ready' : 'historical') : 'unavailable'
            }
          />
          <details>
            <summary>本轮普通审稿与前次候选（只读）</summary>
            <pre className="report">
              {JSON.stringify(
                {
                  review: report.review,
                  previous_candidate: report.previous_candidate,
                },
                null,
                2,
              )}
            </pre>
          </details>
        </>
      )}
      <button
        type="button"
        disabled={disabled || !state?.artifact}
        onClick={() => {
          if (state?.artifact) onTarget(state.artifact)
        }}
      >
        查看原候选
      </button>
      <details>
        <summary>查看完整独立回执（只读）</summary>
        <pre className="report">{JSON.stringify(artifact.payload, null, 2)}</pre>
      </details>
    </section>
  )
}
