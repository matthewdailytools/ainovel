import { useEffect, useRef, useState } from 'react'
import { api, ApiError, type Artifact, type Chapter, type Task } from './api'
import { useLeaveGuard } from './leaveGuard'
import { parseRecheckBudget } from './candidateRecheck'
import { PlotAuditDetails } from './PlotAuditDetails'
import { ConditionalReviewNotice } from './ConditionalReviewNotice'
import { PreparedScopeObjectEvidence } from './PreparedScopeObjectEvidence'
import {
  canonical,
  correctionKindLabel,
  correctionOwner,
  digest,
  object,
  ownerKey,
  sourceReceiptTargetCurrent,
  capsuleMatchesCurrent,
  type Phase,
} from './sourceCorrection'
import {
  createScopeOperation,
  readScopeReport,
  readScopeBodyPreview,
  readScopeTask,
  readScopePreview,
  readScopeReceipt,
  scopeEligibility,
  scopePreviewPath,
  objectAliasesEnabled,
  SCOPE_OBJECT_VERSION,
  SCOPE_OBJECT_BODY_VERSION,
  SCOPE_KIND,
  type ScopeCapsule,
  type ScopeOperationState,
  type ScopeBodyPreview,
  type ScopePreview,
  type ScopeReceipt,
} from './preparedScopeObjectCorrection'
const errorText = (value: unknown) =>
  value instanceof Error ? value.message : '读取失败，输入与原提交记录保留。'
const scopeLabel = { only: '仅限指定接收方', unrestricted: '明确不限制', unspecified: '未声明独占' }
export function PreparedScopeReceiptView({
  receipt,
  reason,
  historical = false,
}: {
  receipt: ScopeReceipt
  reason?: string
  historical?: boolean
}) {
  return (
    <section aria-label="AI章纲范围纠偏只读回执" style={{ overflowWrap: 'anywhere' }}>
      <h4>
        AI章纲范围纠偏 · {historical ? '历史依据' : receipt.state === 'ready' ? '来源回执就绪' : '仍待核对'} ·
        只读
      </h4>
      <p>
        来源为冻结的有效AI章纲，不是作者原话或已发生事实。此回执只核对接收范围及其条件，没有检查或修改正文；来源就绪不代表正文通过。
      </p>
      {reason !== undefined && <p>操作原因（仅供审计）：{reason || '未填写'}</p>}
      <details>
        <summary>冻结AI章纲与原解释</summary>
        <pre className="stream">{receipt.snapshot.plot.plot}</pre>
      </details>
      <PreparedScopeObjectEvidence receipt={receipt} />
      {receipt.snapshot.prepared_contract.items.map((before, i) => {
        const proposal = receipt.proposal.items[i],
          check = receipt.preservation?.item_checks[i]
        const aliasCheck = check && 'object_alias_check' in check ? check.object_alias_check : undefined
        return (
          <article className="fact" key={before.id}>
            <strong>
              {before.id} · {receipt.snapshot.item_ids.includes(before.id) ? '选定范围' : '原事项保持'}
            </strong>
            <blockquote>{before.author_evidence}</blockquote>
            <p>
              接收范围：{scopeLabel[before.recipient_scope]} → {scopeLabel[proposal.recipient_scope]}；条件：
              {before.scope_condition || '未单列'} → {proposal.scope_condition || '未声明额外范围条件'}。
            </p>
            <p>
              {proposal.status === 'supported' ? '来源观察已形成提案' : '来源仍未知'}：{proposal.reason}
            </p>
            <p>
              {check
                ? `独立保持核对：${check.result === 'pass' ? '模型标保持' : check.result === 'fail' ? '模型标不符' : '仍未知'}；两字段是否足够：${check.scope_only_fix_sufficient}。${check.reason}`
                : '来源尚未核实，没有派发独立保持核对，不能继续正文复检。'}
            </p>
            {check && <blockquote>{check.evidence}</blockquote>}
            {aliasCheck !== undefined && (
              <p>
                末次独立称谓复核：
                {aliasCheck === 'pass'
                  ? '模型标同指保持'
                  : aliasCheck === 'fail'
                    ? '模型标不符'
                    : aliasCheck === 'unknown'
                      ? '仍待核对'
                      : '本项无需额外关联'}
                。
              </p>
            )}
          </article>
        )
      })}
      <details>
        <summary>原文观察、固定条件及技术依据（只读）</summary>
        <pre className="report">
          {JSON.stringify({ inventory: receipt.inventory, preservation: receipt.preservation }, null, 2)}
        </pre>
      </details>
      <p>{receipt.note}</p>
      <p className="small muted">
        版本：{receipt.version}；候选修订 {receipt.snapshot.candidate_revision}。原解释、费用及原稿保留。
      </p>
    </section>
  )
}
export function PreparedScopeTaskNotice({ task }: { task: Task }) {
  const source = task.kind === SCOPE_KIND
  return (
    <section className="notice" aria-label="AI章纲范围独立任务状态">
      <strong>
        {source ? 'AI章纲范围来源纠偏' : '独立正文复检'} · {task.status}
      </strong>
      <p>
        {source
          ? objectAliasesEnabled(task.payload)
            ? '先独立观察冻结AI章纲，必要时核对材料不同称谓，再独立核对全部原义保持；不读取候选正文。来源就绪不代表正文通过。'
            : '先核冻结AI章纲，再独立核对原义保持；不读取候选正文。来源就绪不代表正文通过。'
          : '仅按就绪来源回执复检原候选，不生成、采纳或恢复正文。'}
      </p>
      {task.error && <p role="status">{task.error}</p>}
      <p className="small muted">
        任务 {task.id} · 账户 {typeof task.payload.account === 'string' ? task.payload.account : '记录待核'}
        ；调用步骤及累计费用保留。
      </p>
    </section>
  )
}
type Prepared = { phase: 'source'; value: ScopePreview } | { phase: 'recheck'; value: ScopeBodyPreview }
export function PreparedScopeCorrectionPanel({
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
  const [verifyObjectAliases, setVerifyObjectAliases] = useState(false)
  const current = useRef({ scope, signature, unsaved, disabled, modelReady, verifyObjectAliases })
  current.current = { scope, signature, unsaved, disabled, modelReady, verifyObjectAliases }
  const mounted = useRef(true),
    sequence = useRef(0),
    composing = useRef(false)
  const engine = useRef<ReturnType<typeof createScopeOperation> | undefined>(undefined)
  const [operation, setOperation] = useState<ScopeOperationState>({
    status: 'idle',
    message: '',
  })
  const [prepared, setPrepared] = useState<Prepared>(),
    [incoming, setIncoming] = useState<Prepared>()
  const previewSignature = useRef(''),
    incomingSignature = useRef('')
  const trackedScopeCapsule = useRef<ScopeCapsule | undefined>(undefined)
  const completedTasks = useRef(new Set<string>())
  const [reason, setReason] = useState(''),
    [selected, setSelected] = useState<string[]>([]),
    [budget, setBudget] = useState('')
  const [selectedReceipt, setSelectedReceipt] = useState(''),
    [receipt, setReceipt] = useState<{
      artifact: Artifact
      data: ScopeReceipt
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
  const draftSignature = canonical([selected, reason, budget, verifyObjectAliases])
  const draftKey = `ainovel.prepared-scope-correction.draft.1:${scope}`
  const budgetValue = parseRecheckBudget(budget)
  const locked =
    loading ||
    operation.status === 'sending' ||
    operation.status === 'uncertain' ||
    operation.status === 'unavailable' ||
    task?.status === 'queued' ||
    task?.status === 'running'
  const stale =
    prepared !== undefined &&
    (previewSignature.current !== signature ||
      objectAliasesEnabled(prepared.value) !== verifyObjectAliases ||
      (prepared.phase === 'recheck' && prepared.value.receipt_id !== selectedReceipt))
  const currentReceipt = receipt?.artifact.id === selectedReceipt ? receipt : undefined
  const receiptCurrent =
    currentReceipt !== undefined &&
    currentReceipt.current &&
    currentReceipt.bindingSignature === signature &&
    objectAliasesEnabled(currentReceipt.data.snapshot) === verifyObjectAliases
  useLeaveGuard(
    operation.status === 'sending' ||
      operation.status === 'uncertain' ||
      (draftSignature !== submitted && (!!reason || selected.length > 0 || !!budget || verifyObjectAliases)),
    'AI章纲范围纠偏范围、原因或待查提交',
  )
  useEffect(() => {
    mounted.current = true
    try {
      const storage = window.localStorage
      const control = createScopeOperation(scope, api, storage, (next) => {
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
          (draft.verify_object_aliases === undefined || typeof draft.verify_object_aliases === 'boolean') &&
          draft.selected.every((id) => typeof id === 'string' && /^P(?:0[1-9]|1[0-2])$/u.test(id))
        ) {
          setReason(draft.reason)
          setBudget(draft.budget)
          setSelected(draft.selected)
          setVerifyObjectAliases(draft.verify_object_aliases === true)
          if (typeof draft.submitted === 'string') setSubmitted(draft.submitted)
        }
      }
      if (control.state.pending) {
        const capsule = control.state.pending
        setVerifyObjectAliases(
          capsule.preview.version === SCOPE_OBJECT_VERSION ||
            capsule.preview.version === SCOPE_OBJECT_BODY_VERSION,
        )
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
      localStorage.setItem(
        draftKey,
        JSON.stringify({
          reason,
          selected,
          budget,
          submitted,
          ...(verifyObjectAliases ? { verify_object_aliases: true } : {}),
        }),
      )
    } catch {
      setStorageReady(false)
      setError('范围草稿无法保存，提交前必须先恢复浏览器存储。')
    }
  }, [storageReady, draftKey, reason, selected, budget, submitted, verifyObjectAliases])
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
    trackedScopeCapsule.current = operation.pending
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
        if (!alive || !trackedScopeCapsule.current) return
        const updated = readScopeTask(raw, trackedScopeCapsule.current)
        if (!updated || updated.id !== task.id)
          throw new Error('当前任务回执无法绑定原请求，保留记录，不发布检查结论。')
        setTask(updated)
        if (updated.status === 'succeeded') {
          const artifactId = updated.result.artifact_id
          if (typeof artifactId === 'string' && updated.kind === SCOPE_KIND) setSelectedReceipt(artifactId)
          if (updated.kind !== SCOPE_KIND && !completedTasks.current.has(updated.id)) {
            completedTasks.current.add(updated.id)
            const original = trackedScopeCapsule.current
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
        const data = readScopeReceipt(raw.payload)
        if (!alive) return
        if (
          raw.kind !== SCOPE_KIND ||
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
          origin.kind !== SCOPE_KIND ||
          origin.status !== 'succeeded' ||
          !object(origin.result) ||
          origin.result.artifact_id !== raw.id ||
          !object(origin.payload) ||
          canonical(origin.result.prepared_source_scope_snapshot) !== canonical(raw.payload.snapshot)
        )
          throw new Error('回执与来源任务无法完整对应，未启用正文复检。')
        const receiptMatches =
          (await digest(data.snapshot.plot.plot)) === data.snapshot.source_text_sha256 &&
          (await sourceReceiptTargetCurrent(data, artifact, branchId))
        let bindingStatus: 'current' | 'historical' | 'unavailable' = receiptMatches
          ? 'unavailable'
          : 'historical'
        if (receiptMatches && owner) {
          try {
            const latest = readScopePreview(
              await api<unknown>(scopePreviewPath(artifact.id, objectAliasesEnabled(data.snapshot))),
              owner,
              objectAliasesEnabled(data.snapshot),
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
  const eligibility = scopeEligibility(artifact, source, chapter, branchId, unsaved)
  const available = artifacts.filter(
    (row) =>
      row.kind === SCOPE_KIND &&
      row.branch_id === branchId &&
      readScopeReceipt(row.payload)?.snapshot.artifact_id === artifact.id,
  )
  const blocked = disabled || unsaved || !storageReady || locked || !!eligibility
  const matches = (token: number, expected: string, expectedAliases?: boolean) =>
    mounted.current &&
    sequence.current === token &&
    current.current.scope === scope &&
    current.current.signature === expected &&
    (expectedAliases === undefined || current.current.verifyObjectAliases === expectedAliases)
  async function load(phase: Phase) {
    if (blocked || !owner) return
    const token = ++sequence.current,
      expected = signature,
      expectedAliases = phase === 'source' ? verifyObjectAliases : undefined
    setLoading(true)
    setError('')
    try {
      let next: Prepared
      if (phase === 'source') {
        const raw = await api<unknown>(scopePreviewPath(artifact.id, verifyObjectAliases))
        const preview = readScopePreview(raw, owner, verifyObjectAliases)
        if (!preview || (await digest(preview.source_text)) !== preview.source_text_sha256)
          throw new Error('来源预览的原文、目标或摘要无法核对，未提交。')
        next = { phase, value: preview }
      } else {
        if (!currentReceipt || !receiptCurrent || currentReceipt.data.state !== 'ready') return
        const raw = await api<unknown>(
          `/artifacts/${encodeURIComponent(artifact.id)}/recheck-source-correction-preview?receipt_id=${encodeURIComponent(currentReceipt.artifact.id)}`,
        )
        const preview = readScopeBodyPreview(raw, owner, currentReceipt.artifact.id, currentReceipt.data)
        if (!preview) throw new Error('正文复检依据与就绪回执无法对应，未提交。')
        next = { phase, value: preview }
      }
      if (!matches(token, expected, expectedAliases)) return
      if (
        phase === 'recheck' &&
        prepared?.phase === 'source' &&
        !incoming &&
        !unsaved &&
        !disabled &&
        !stale &&
        receiptCurrent &&
        operation.status === 'confirmed' &&
        operation.pending?.phase === 'source' &&
        task?.kind === SCOPE_KIND &&
        task.status === 'succeeded' &&
        task.id === operation.task?.id &&
        currentReceipt?.artifact.task_id === task.id &&
        task.result.artifact_id === currentReceipt.artifact.id &&
        currentReceipt.data.state === 'ready' &&
        prepared.value.basis_sha256 === operation.pending.request.preview_basis_sha256 &&
        prepared.value.basis_sha256 === currentReceipt.data.snapshot.basis_sha256
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
      if (matches(token, expected, expectedAliases)) {
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
      objectAliasesEnabled(incoming.value) !== verifyObjectAliases ||
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
      objectAliasesEnabled(prepared.value) !== current.current.verifyObjectAliases ||
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
    let capsule: ScopeCapsule
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
        request: {
          ...base,
          source_identity_context: false,
          item_ids: selected,
          reason: reason.trim(),
          ...(objectAliasesEnabled(prepared.value) ? { verify_object_aliases: true } : {}),
        },
      }
    } else
      capsule = {
        phase: 'recheck',
        client_signature: clientSignature,
        owner,
        preview: prepared.value,
        request: { ...base, source_identity_context: false, receipt_id: prepared.value.receipt_id },
      }
    await engine.current?.start(capsule)
  }
  return (
    <details className="notice" style={{ overflowWrap: 'anywhere' }}>
      <summary>核对AI章纲范围，再显式复检正文</summary>
      <section aria-label="AI章纲范围来源纠偏">
        <p>
          用于旧AI章纲解释把普通接收方误当唯一接收方，或把别处的许可范围错套到本次交接。来源是冻结的有效AI章纲，不是作者原话或已发生事实；仅允许核对接收范围及其条件，角色、动作、阶段、结果、原章纲和正文均不修改。
        </p>
        <p>
          来源纠偏基础流程包含两个独立模型步骤；开启称谓核对后可能增加一步。就绪后另行读取依据并提交正文复检。每步及失败均可能产生费用，继续受累计预算和原任务剩余额度保护。
        </p>
        <label className="check">
          <input
            type="checkbox"
            style={{ flexShrink: 0 }}
            checked={verifyObjectAliases}
            disabled={locked || disabled || unsaved}
            onChange={(event) => {
              sequence.current++
              setVerifyObjectAliases(event.target.checked)
              setIncoming(undefined)
              incomingSignature.current = ''
            }}
          />
          <span style={{ minWidth: 0 }}>核对材料的不同称谓</span>
        </label>
        <p className="small muted">
          默认关闭。开启后先独立观察原文，必要时增加同一次传递的材料称谓关联检查，最后仍核对全部事项与固定条件，可能产生额外费用。
          更换选项后须重新读取依据；旧回执和旧提交保持原版本，关联不新增本书事实或代表正文通过。
        </p>
        {eligibility && <p role="status">{eligibility}</p>}
        {error && <p role="alert">{error}</p>}
        {operation.message && <p role="status">{operation.message}</p>}
        {task && <PreparedScopeTaskNotice task={task} />}
        {operation.pending && (
          <p className="small">原提交标识：{operation.pending.request.client_request_id}。</p>
        )}
        {(operation.status === 'uncertain' || operation.status === 'confirmed') && (
          <button type="button" disabled={loading} onClick={() => void engine.current?.lookup()}>
            只查询原提交回执（不重发）
          </button>
        )}
        <button type="button" disabled={blocked} onClick={() => void load('source')}>
          {prepared?.phase === 'source' ? '重新读取章纲范围依据' : '读取章纲范围依据'}
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
            <h4>冻结的有效AI章纲</h4>
            <pre className="stream">{prepared.value.source_text}</pre>
            <fieldset disabled={locked}>
              <legend>选择需要核对的原P事项</legend>
              {prepared.value.items.map((item) => (
                <label className="check" key={item.id}>
                  <input
                    type="checkbox"
                    style={{ flexShrink: 0 }}
                    checked={selected.includes(item.id)}
                    onChange={(event) =>
                      setSelected((rows) =>
                        event.target.checked ? [...rows, item.id] : rows.filter((id) => id !== item.id),
                      )
                    }
                  />
                  <span style={{ minWidth: 0 }}>
                    <span style={{ display: 'block' }}>
                      {item.id} · {item.certainty === 'ambiguous' ? '存在歧义' : '原标明确'} ·{' '}
                      {item.actor || '未指定主体'} · {item.action}
                    </span>
                    <span style={{ display: 'block' }}>{item.author_evidence}</span>
                  </span>
                </label>
              ))}
            </fieldset>
            <label>
              本次AI章纲范围纠偏原因（可留空）
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
              原因仅留审计，不作为模型证明；只核对接收范围与条件，不扩展原章纲或改写其他事件字段。
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
            已有章纲范围回执
            <select
              value={selectedReceipt}
              disabled={loading || operation.status === 'sending'}
              onChange={(event) => setSelectedReceipt(event.target.value)}
            >
              <option value="">选择只读回执</option>
              {available.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.id.slice(0, 8)} ·{' '}
                  {readScopeReceipt(row.payload)?.state === 'ready' ? '回执就绪' : '仍待核对'}
                </option>
              ))}
            </select>
          </label>
        )}
        {currentReceipt && (
          <>
            {objectAliasesEnabled(currentReceipt.data.snapshot) !== verifyObjectAliases && (
              <p role="status">
                此回执的材料称谓选项与当前选择不同；回执保留只读，请显式选择相同选项后再读取正文依据。
              </p>
            )}
            {currentReceipt.bindingStatus === 'unavailable' && (
              <p role="status">当前完整来源依据尚未核实；回执保留只读，未启用正文复检。</p>
            )}
            <PreparedScopeReceiptView
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
            的独立正文复检依据；原稿和原章纲保持。下一步仍由你显式提交。
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
                ? '提交AI章纲范围纠偏（额外模型费用）'
                : '提交独立正文复检（额外模型费用）'}
            </button>
          </>
        )}
      </section>
    </details>
  )
}

export function PreparedScopeCorrectionArtifact({
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
    artifact.branch_id === branchId && artifact.kind === SCOPE_KIND
      ? readScopeReceipt(artifact.payload)
      : undefined
  const report =
    artifact.branch_id === branchId && artifact.kind !== SCOPE_KIND
      ? readScopeReport(artifact.payload)
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
        const chapter = await api<Chapter>(`/chapters/${encodeURIComponent(target.chapter_id)}`)
        const sha256 = await digest(row.payload.text)
        if (active)
          setLoaded({
            key,
            artifact: row,
            sha256,
            current:
              chapter.id === target.chapter_id &&
              chapter.revision === target.base_revision &&
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
      <section aria-label="AI章纲范围只读记录">
        <p role="status">报告格式无法核对，仅保留原始记录，不能作为有效或通过结论。</p>
        <pre className="report">{JSON.stringify(artifact.payload, null, 2)}</pre>
      </section>
    )
  return (
    <section aria-label="AI章纲范围只读记录" style={{ overflowWrap: 'anywhere' }}>
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
          <PreparedScopeReceiptView receipt={source} historical={state?.current === false} />
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
