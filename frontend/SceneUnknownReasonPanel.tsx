import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from './api'
import { useLeaveGuard } from './leaveGuard'
import { object, textareaSelection, type SceneRow } from './sceneReconfirmation'
import { readSceneOrderBasis } from './sceneOrderBasis'
import {
  canonical,
  canCorrectUnknownScene,
  correctionEvidence,
  correctionReceiptMatches,
  createCorrection,
  readCorrectionPreview,
  readCorrectionReceipt,
  readEvidence,
  readPendingCorrection,
  UNKNOWN_REASON_VERSION,
  type Evidence,
  type Pending,
  type Preview,
  type Receipt,
} from './sceneUnknownReasonCorrection'

const errorText = (value: unknown) =>
  value instanceof Error ? value.message : '操作结果尚未核实，原草稿与提交标识保留。'
function ReceiptDetails({ receipt, current }: { receipt: Receipt; current: SceneRow }) {
  const isCurrent =
    !current.archived &&
    !current.stale &&
    current.revision === receipt.scene_revision &&
    current.payload_sha256 === receipt.payload_sha256 &&
    current.payload.source_revision === receipt.source_revision
  const historical = current.revision >= receipt.scene_revision && !isCurrent
  return (
    <section aria-label="未知原因更正回执">
      <h4>未知原因更正已保存，时点仍未知</h4>
      <p role="status">
        {isCurrent
          ? '此回执对应当前保存场景，仅说明未知理由已更正。'
          : historical
            ? '这是历史更正回执，不能代表当前场景版本或当前未知理由已核实。'
            : '此更正已保存，当前场景版本仍需重新读取核实。'}
      </p>
      <p>
        记录来源：{receipt.actor === 'author' ? '作者明确更正' : '获授权操作者明确更正'}
        。模型审查与文学审阅均未执行；整书任务保持原状态。
      </p>
      <p>更正后的未知原因：{receipt.reason}</p>
      <p>上一版理由：{receipt.previous_reason}</p>
      <p>最初保留的模型理由：{receipt.original_reason}</p>
      <p>
        场景修订 {receipt.previous_scene_revision} → {receipt.scene_revision}
        。正文、范围、故事顺序、视角和原覆盖记录保持。
      </p>
      <details>
        <summary>查看更正依据与完整回执</summary>
        {receipt.evidence.map((row, index) => (
          <blockquote key={index}>{row.quote}</blockquote>
        ))}
        <pre className="report">{JSON.stringify(receipt, null, 2)}</pre>
      </details>
    </section>
  )
}

export function SceneUnknownReasonCorrection({
  branchId,
  chapterId,
  scene,
  disabled = false,
  onSaved,
  onDirtyChange,
  onLockChange,
}: {
  branchId: string
  chapterId: string
  scene: SceneRow
  disabled?: boolean
  onSaved: (receipt: Receipt) => Promise<void>
  onDirtyChange?: (dirty: boolean) => void
  onLockChange?: (locked: boolean) => void
}) {
  const owner = JSON.stringify([branchId, chapterId, scene.id]),
    storageKey = 'ainovel-unknown-reason-correction:' + owner
  const liveOwner = useRef(owner)
  liveOwner.current = owner
  const mounted = useRef(true),
    sequence = useRef(0),
    inFlight = useRef(false),
    composing = useRef(false),
    knownRejected = useRef(false)
  const pending = useRef<Pending | undefined>(undefined),
    sourceInput = useRef<HTMLTextAreaElement>(null)
  const [open, setOpen] = useState(false),
    [busy, setBusy] = useState(false),
    [storageReady, setStorageReady] = useState(false)
  const [preview, setPreview] = useState<Preview>(),
    [incoming, setIncoming] = useState<Preview>()
  const [reason, setReason] = useState(''),
    [quotes, setQuotes] = useState<Evidence[]>([])
  const [selection, setSelection] = useState<{ start: number; end: number }>()
  const [error, setError] = useState(''),
    [message, setMessage] = useState(''),
    [stale, setStale] = useState(false)
  const [uncertain, setUncertain] = useState(false),
    [completed, setCompleted] = useState(false),
    [receipt, setReceipt] = useState<Receipt>()
  const dirty = !completed && Boolean(reason || quotes.length || uncertain || (busy && pending.current))
  useLeaveGuard(dirty, '未知原因更正草稿或未核实提交')
  useEffect(() => {
    onDirtyChange?.(dirty)
  }, [dirty, onDirtyChange])
  useEffect(() => {
    onLockChange?.(busy || uncertain)
    return () => onLockChange?.(false)
  }, [busy, uncertain, onLockChange])
  const path = `/branches/${encodeURIComponent(branchId)}/chapters/${encodeURIComponent(chapterId)}/scene-times/${encodeURIComponent(scene.id)}`
  const capable = canCorrectUnknownScene(scene)
  const externalChange =
    !!preview &&
    !completed &&
    (scene.revision !== preview.scene.revision || scene.payload_sha256 !== preview.scene.payload_sha256)
  const selected =
    preview && selection ? correctionEvidence(preview.source, selection.start, selection.end) : undefined
  const canSubmit =
    capable &&
    storageReady &&
    !!preview &&
    !!reason.trim() &&
    quotes.length > 0 &&
    !disabled &&
    !busy &&
    !uncertain &&
    !completed &&
    !stale &&
    !incoming &&
    !externalChange
  const payload: unknown = scene.payload
  const saved =
    object(payload) && object(payload.unknown_reason_corrected) ? payload.unknown_reason_corrected : undefined
  const savedNonce =
    saved?.version === UNKNOWN_REASON_VERSION && typeof saved.client_request_id === 'string'
      ? saved.client_request_id
      : undefined
  function persist(
    nextReason = reason,
    nextPreview = preview,
    nextQuotes = quotes,
    nextPending = pending.current,
    nextReceipt = receipt,
  ) {
    sessionStorage.setItem(
      storageKey,
      JSON.stringify({
        version: 1,
        reason: nextReason,
        preview: nextPreview,
        evidence: nextQuotes,
        pending: nextPending,
        receipt: nextReceipt,
        outcome: knownRejected.current ? 'known_rejected' : nextReceipt ? 'confirmed' : 'unverified',
      }),
    )
  }
  useEffect(() => {
    mounted.current = true
    try {
      const raw = sessionStorage.getItem(storageKey)
      if (raw) {
        const stored: unknown = JSON.parse(raw)
        if (!object(stored) || stored.version !== 1) throw new Error('旧草稿版本无法读取。')
        const prior = stored.pending
          ? readPendingCorrection(stored.pending, branchId, chapterId, scene.id)
          : undefined
        if (stored.pending && !prior) {
          setUncertain(true)
          throw new Error('原提交依据无法完整核对。')
        }
        if (prior) {
          pending.current = prior
          setPreview(prior.preview)
          setReason(prior.request.unknown_reason_correction.reason)
          setQuotes(prior.request.unknown_reason_correction.evidence)
          setOpen(true)
          const found = readCorrectionReceipt(
            stored.receipt,
            scene.id,
            prior.request.unknown_reason_correction.client_request_id,
          )
          if (found && correctionReceiptMatches(found, prior)) {
            setReceipt(found)
            setCompleted(true)
          } else {
            setUncertain(true)
            setMessage('已恢复原提交标识；请只查询原回执，不能换标识重发。')
          }
        } else {
          if (stored.outcome === 'known_rejected') {
            knownRejected.current = true
            setStale(true)
            setMessage('上次提交已明确拒绝，原因与选段保留；请重新读取并确认新依据。')
          }
          if (typeof stored.reason === 'string' && stored.reason.length <= 2000) setReason(stored.reason)
          const old = readCorrectionPreview(stored.preview, branchId, chapterId, scene.id)
          if (old) {
            setPreview(old)
            if (
              Array.isArray(stored.evidence) &&
              stored.evidence.length <= 30 &&
              stored.evidence.every((row) => {
                const quote = readEvidence(row)
                return (
                  !!quote &&
                  canonical(
                    correctionEvidence(
                      old.source,
                      quote.start - old.source.start,
                      quote.end - old.source.start,
                    ),
                  ) === canonical(quote)
                )
              })
            )
              setQuotes(stored.evidence as Evidence[])
          }
        }
      }
      setStorageReady(true)
    } catch {
      setError('无法完整读取本窗口的原草稿；原记录保留，不能创建新提交。')
    }
    return () => {
      mounted.current = false
      sequence.current++
      onDirtyChange?.(false)
    }
  }, [storageKey, owner, branchId, chapterId, scene.id, onDirtyChange])
  useEffect(() => {
    if (storageReady) {
      try {
        persist()
      } catch {
        setError('无法保留本窗口草稿；提交前须能保存原标识。')
      }
    }
  }, [reason, preview, quotes, receipt, storageReady, storageKey])
  const matches = (target: string, id: number) =>
    mounted.current && liveOwner.current === target && sequence.current === id
  async function load(mode: 'initial' | 'refresh' | 'new') {
    if (inFlight.current || uncertain || disabled || !storageReady || !capable) return
    inFlight.current = true
    const target = owner,
      id = ++sequence.current
    setBusy(true)
    setOpen(true)
    setError('')
    try {
      const raw = await api<unknown>(path + '/unknown-reason-correction-preview')
      if (!matches(target, id)) return
      const next = readCorrectionPreview(raw, branchId, chapterId, scene.id)
      if (!next) throw new Error('服务端依据的目标、全文版本或原场景无法对应，草稿保留。')
      if (mode === 'refresh' && preview) setIncoming(next)
      else {
        setPreview(next)
        setIncoming(undefined)
        setStale(false)
        setCompleted(false)
        setReceipt(undefined)
        pending.current = undefined
        knownRejected.current = false
        if (mode === 'new') {
          setReason('')
          setQuotes([])
        } else if (!preview || canonical(preview.source) !== canonical(next.source)) setQuotes([])
        setSelection(undefined)
      }
    } catch (failure) {
      if (matches(target, id)) {
        setError(errorText(failure))
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
    if (!incoming || busy || uncertain) return
    if (!preview || canonical(preview.source) !== canonical(incoming.source)) {
      setQuotes([])
      setSelection(undefined)
    }
    setPreview(incoming)
    setIncoming(undefined)
    pending.current = undefined
    knownRejected.current = false
    setStale(false)
    setCompleted(false)
    setReceipt(undefined)
    setError('')
  }
  async function publish(
    raw: unknown,
    capsule: Pending | undefined,
    nonce: string,
    target: string,
    id: number,
  ) {
    if (!object(raw) || raw.scene_id !== scene.id || raw.client_request_id !== nonce)
      throw new Error('回执查询目标不一致，原请求保留。')
    if (raw.receipt === null) {
      setMessage('此刻未查到原回执，不代表从未保存；请继续只读查询原标识。')
      return
    }
    const result = readCorrectionReceipt(raw.receipt, scene.id, nonce)
    if (!result || (capsule && !correctionReceiptMatches(result, capsule)))
      throw new Error('回执内容或原始依据不一致，不能视为本次更正成功。')
    if (!matches(target, id)) return
    setReceipt(result)
    setError('')
    if (!capsule) return
    knownRejected.current = false
    persist(
      capsule.request.unknown_reason_correction.reason,
      capsule.preview,
      capsule.request.unknown_reason_correction.evidence,
      capsule,
      result,
    )
    setCompleted(true)
    setUncertain(false)
    setStale(false)
    setMessage('更正已保存，仍保持时点未知；未调用模型或恢复任务。')
    try {
      await onSaved(result)
    } catch {
      if (matches(target, id)) setError('更正回执已核实，但场景刷新失败；请重新读取保存记录。')
    }
  }
  async function submit() {
    if (inFlight.current || !canSubmit || !preview || composing.current) return
    let capsule: Pending
    try {
      capsule = { preview, request: createCorrection(preview, reason, quotes, crypto.randomUUID(), 'author') }
      pending.current = capsule
      knownRejected.current = false
      persist(reason, preview, quotes, capsule, undefined)
    } catch (failure) {
      pending.current = undefined
      setError(errorText(failure))
      return
    }
    inFlight.current = true
    const target = owner,
      id = ++sequence.current,
      nonce = capsule.request.unknown_reason_correction.client_request_id
    let writeCompleted = false
    setBusy(true)
    setError('')
    setMessage('')
    try {
      await api<unknown>(path, 'PUT', capsule.request)
      writeCompleted = true
      if (!matches(target, id)) return
      setUncertain(true)
      const raw = await api<unknown>(path + '/unknown-reason-corrections/' + encodeURIComponent(nonce))
      if (matches(target, id)) await publish(raw, capsule, nonce, target, id)
    } catch (failure) {
      if (!matches(target, id)) return
      setError(errorText(failure))
      const rejected =
        !writeCompleted &&
        failure instanceof ApiError &&
        failure.status >= 400 &&
        failure.status < 500 &&
        failure.code !== 'invalid_response'
      if (rejected) {
        setStale(true)
        knownRejected.current = true
        pending.current = undefined
        persist(reason, preview, quotes, undefined, undefined)
      } else setUncertain(true)
    } finally {
      if (matches(target, id)) {
        inFlight.current = false
        setBusy(false)
      }
    }
  }
  async function lookup(nonce: string, capsule?: Pending) {
    if (inFlight.current) return
    inFlight.current = true
    const target = owner,
      id = ++sequence.current
    setBusy(true)
    setError('')
    setOpen(true)
    try {
      const raw = await api<unknown>(path + '/unknown-reason-corrections/' + encodeURIComponent(nonce))
      if (matches(target, id)) await publish(raw, capsule, nonce, target, id)
    } catch (failure) {
      if (matches(target, id)) setError(errorText(failure))
    } finally {
      if (matches(target, id)) {
        inFlight.current = false
        setBusy(false)
      }
    }
  }
  if (!capable && !savedNonce && !pending.current) return null
  const locked = busy || uncertain || completed || disabled
  return (
    <details
      className="notice"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      style={{ overflowWrap: 'anywhere' }}
    >
      <summary>更正未知原因，仍保持时点未知</summary>
      <section aria-label="未知原因更正">
        <p>
          只更正无依据或不准确的未知理由。故事顺序仍为空，正文、原模型记录、事件引用、范围和视角保留；这是作者的明确更正，不代表模型或文学审阅。
        </p>
        <p>当前场景保存理由：{readSceneOrderBasis(scene.payload.order_basis)?.reason}</p>
        {disabled && <p role="status">请先处理当前场景表单改动，再更正已保存的未知理由。</p>}
        <button
          type="button"
          disabled={busy || uncertain || disabled || !storageReady || !capable}
          onClick={() => void load(completed ? 'new' : preview ? 'refresh' : 'initial')}
        >
          {completed
            ? '开始另一条未知原因更正'
            : preview
              ? '重新读取更正依据（保留草稿）'
              : '读取未知原因更正依据'}
        </button>
        {savedNonce && (
          <button type="button" disabled={busy} onClick={() => void lookup(savedNonce)}>
            查看已保存的未知原因更正回执
          </button>
        )}
        {busy && <p role="status">正在读取或保存未知原因更正…</p>}
        {error && <p role="alert">{error}</p>}
        {message && <p role="status">{message}</p>}
        {(externalChange || stale) && (
          <p role="status">保存依据已变化或需重新核对；原因草稿保留，请显式读取并确认新依据。</p>
        )}
        {incoming && (
          <section aria-label="新读取的未知原因依据">
            <p>已读到新依据。确认后保留原因；原文变化时需重新选段。</p>
            <button type="button" disabled={busy || uncertain} onClick={acceptIncoming}>
              确认使用新依据，保留更正原因
            </button>
          </section>
        )}
        {preview && (
          <>
            <p>本次更正冻结的原理由：{readSceneOrderBasis(preview.scene.payload.order_basis)?.reason}</p>
            <p>记录来源：作者明确更正。保存后场景时点仍未知，整书批次不会自动继续。</p>
            <label>
              作为更正依据的原场景选段
              <textarea
                ref={sourceInput}
                rows={6}
                readOnly
                value={preview.source.quote}
                onSelect={(event) =>
                  setSelection(
                    textareaSelection(
                      preview.source,
                      event.currentTarget.value,
                      event.currentTarget.selectionStart,
                      event.currentTarget.selectionEnd,
                    ),
                  )
                }
              />
            </label>
            <button
              type="button"
              disabled={locked || !selected || quotes.length >= 30}
              onClick={() => {
                if (selected && !quotes.some((quote) => canonical(quote) === canonical(selected)))
                  setQuotes([...quotes, selected])
              }}
            >
              加入所选原文依据
            </button>
            <p>已选择 {quotes.length} 条原文依据，至少需要1条；选段使用保存稿与全文校验。</p>
            {quotes.map((quote, index) => (
              <div key={index}>
                <blockquote>{quote.quote}</blockquote>
                <button
                  type="button"
                  disabled={locked}
                  onClick={() => setQuotes(quotes.filter((_, i) => i !== index))}
                >
                  移除第{index + 1}条依据
                </button>
              </div>
            ))}
            <label>
              更正后的未知原因
              <textarea
                rows={3}
                maxLength={2000}
                value={reason}
                disabled={locked}
                onChange={(event) => setReason(event.target.value)}
                onCompositionStart={() => {
                  composing.current = true
                }}
                onCompositionEnd={() => {
                  composing.current = false
                }}
              />
            </label>
            <button type="button" className="primary" disabled={!canSubmit} onClick={() => void submit()}>
              保存更正，仍保持时点未知
            </button>
            {uncertain && pending.current && (
              <>
                <p role="status">保存结果尚未完整核实，原标识与草稿保留；只查询原回执，不重复保存。</p>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    void lookup(
                      pending.current!.request.unknown_reason_correction.client_request_id,
                      pending.current,
                    )
                  }
                >
                  查询原更正回执
                </button>
              </>
            )}
            <details>
              <summary>查看冻结范围与版本依据</summary>
              <pre className="report">
                {JSON.stringify(
                  {
                    source: preview.source,
                    request_basis: preview.request_basis,
                    scene_revision: preview.scene.revision,
                  },
                  null,
                  2,
                )}
              </pre>
            </details>
          </>
        )}
        {receipt && <ReceiptDetails receipt={receipt} current={scene} />}
      </section>
    </details>
  )
}
