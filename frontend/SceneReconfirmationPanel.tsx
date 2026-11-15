import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from './api'
import { useLeaveGuard } from './leaveGuard'
import { readSceneOrderBasis } from './sceneOrderBasis'
import {
  canReconfirmScene,
  createReconfirmation,
  object,
  readPreview,
  readReceipt,
  receiptMatchesRequest,
  selectedEvidence,
  textareaSelection,
  type Preview,
  type Receipt,
  type ReconfirmationInput,
  type SceneRow,
  type TimeEvidence,
} from './sceneReconfirmation'

export function ReceiptDetails({ receipt }: { receipt: Receipt }) {
  return (
    <section aria-label="人工场景重核回执">
      <h4>人工核对记录已保存</h4>
      <p>
        场景标识保持，修订 {receipt.old_revision} → {receipt.revision}
        ；原覆盖审计保留。后续整书任务由你另行恢复。
      </p>
      <p>确认理由：{receipt.reason}</p>
      <p>
        时间说明：{receipt.previous_story_time} → {receipt.story_time}
      </p>
      <p>记录性质：作者确认；模型审查与文学认可仍另行判断。</p>
      {receipt.time_evidence.map((source, index) => (
        <blockquote key={index}>{source.quote}</blockquote>
      ))}
      <details>
        <summary>查看原始回执与版本依据</summary>
        <pre className="report">{JSON.stringify(receipt, null, 2)}</pre>
      </details>
    </section>
  )
}

export function SceneReconfirmation({
  branchId,
  chapterId,
  scene,
  nameFor = () => '事件名称暂不可用',
  onSaved,
  onDirtyChange,
  disabled = false,
}: {
  branchId: string
  chapterId: string
  scene: SceneRow
  nameFor?: (id: string) => string
  onSaved: (receipt: Receipt) => Promise<void>
  onDirtyChange?: (dirty: boolean) => void
  disabled?: boolean
}) {
  const owner = JSON.stringify([branchId, chapterId, scene.id])
  const currentOwner = useRef(owner)
  currentOwner.current = owner
  const sequence = useRef(0),
    mounted = useRef(true)
  const [open, setOpen] = useState(false),
    [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<Preview>(),
    [incoming, setIncoming] = useState<Preview>()
  const [reason, setReason] = useState(''),
    [editTime, setEditTime] = useState(false),
    [storyTime, setStoryTime] = useState('')
  const [quotes, setQuotes] = useState<TimeEvidence[]>([]),
    [selection, setSelection] = useState<{ start: number; end: number }>()
  const [error, setError] = useState(''),
    [stale, setStale] = useState(false),
    [uncertain, setUncertain] = useState(false)
  const [receipt, setReceipt] = useState<Receipt>(),
    [completed, setCompleted] = useState(false)
  const pending = useRef<{ signature: string; request: ReconfirmationInput } | undefined>(undefined)
  const sourceInput = useRef<HTMLTextAreaElement>(null)
  const dirty = open && !completed && Boolean(reason || editTime || quotes.length)
  useLeaveGuard(dirty, '场景人工重新核对草稿')
  useEffect(() => {
    onDirtyChange?.(dirty)
  }, [dirty, onDirtyChange])
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      sequence.current++
      onDirtyChange?.(false)
    }
  }, [owner, onDirtyChange])
  const path = `/branches/${encodeURIComponent(branchId)}/chapters/${encodeURIComponent(chapterId)}/scene-times/${encodeURIComponent(scene.id)}`
  const capable = canReconfirmScene(scene)
  const externalChange =
    !completed &&
    preview &&
    (preview.scene.revision !== scene.revision || preview.scene.payload_sha256 !== scene.payload_sha256)
  const effectiveTime = editTime ? storyTime : preview?.request_basis.story_time || ''
  const changedTime = Boolean(preview && effectiveTime !== preview.request_basis.story_time)
  const selected =
    preview && selection ? selectedEvidence(preview.source, selection.start, selection.end) : undefined
  const canSubmit = Boolean(
    preview &&
    capable &&
    !disabled &&
    !externalChange &&
    !stale &&
    !uncertain &&
    !incoming &&
    !completed &&
    reason.trim() &&
    effectiveTime.trim() &&
    (!changedTime || quotes.length),
  )
  const author = object(scene.payload.author_reconfirmed) ? scene.payload.author_reconfirmed : undefined
  const savedNonce =
    author?.version === 'scene-author-reconfirmation/1' &&
    author.authority === 'author_confirmation_not_model_review' &&
    typeof author.client_request_id === 'string'
      ? author.client_request_id
      : undefined

  async function load(mode: 'initial' | 'refresh' | 'new') {
    if (busy || uncertain || disabled) return
    const target = owner,
      request = ++sequence.current
    const matches = () => mounted.current && currentOwner.current === target && sequence.current === request
    setBusy(true)
    setError('')
    try {
      const raw = await api<unknown>(path + '/reconfirmation-preview')
      if (!matches()) return
      const next = readPreview(raw, branchId, chapterId, scene.id)
      if (!next) throw new Error('重新核对依据的目标、版本或原文范围无法核实，草稿保留。')
      if (mode === 'refresh' && preview) setIncoming(next)
      else {
        setPreview(next)
        setIncoming(undefined)
        setReason('')
        setEditTime(false)
        setStoryTime(next.request_basis.story_time)
        setQuotes([])
        setSelection(undefined)
        setStale(false)
        setCompleted(false)
        pending.current = undefined
      }
    } catch (failure) {
      if (matches()) {
        setError(failure instanceof Error ? failure.message : '读取核对依据失败。')
        setStale(true)
      }
    } finally {
      if (matches()) setBusy(false)
    }
  }
  function acceptIncoming() {
    if (!incoming || uncertain || busy) return
    const sameSource =
      preview &&
      preview.request_basis.content_sha256 === incoming.request_basis.content_sha256 &&
      JSON.stringify(preview.source) === JSON.stringify(incoming.source)
    setPreview(incoming)
    setIncoming(undefined)
    setStale(false)
    setCompleted(false)
    setError('')
    if (!sameSource) {
      setQuotes([])
      setSelection(undefined)
    }
    // Retain the author's reason and edited description; only explicit acceptance
    // replaces version inputs. Reusing old source offsets across texts is blocked.
    pending.current = undefined
  }
  async function publishResult(raw: unknown, request: ReconfirmationInput, matches: () => boolean) {
    const result = readReceipt(raw, scene.id, request.client_request_id)
    if (!result || !receiptMatchesRequest(result, request))
      throw new Error('保存回执未对应本次目标和确认内容；原请求仍保留，请查询回执核对。')
    if (!matches()) return
    setReceipt(result)
    setCompleted(true)
    setUncertain(false)
    setStale(false)
    setError('')
    try {
      await onSaved(result)
    } catch {
      if (matches()) setError('人工确认已保存，但页面刷新失败；回执保留，可重新读取场景。')
    }
  }
  async function submit(retry = false) {
    if (busy || (!retry && !canSubmit)) return
    let request: ReconfirmationInput
    try {
      if (retry) {
        if (!pending.current) return
        request = pending.current.request
      } else {
        if (!preview) return
        const values = createReconfirmation(preview, reason, effectiveTime, changedTime ? quotes : [], '')
        const signature = JSON.stringify([owner, values])
        if (pending.current?.signature !== signature)
          pending.current = {
            signature,
            request: { ...values, client_request_id: crypto.randomUUID() },
          }
        request = pending.current.request
      }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '请核对本次输入。')
      return
    }
    const target = owner,
      requestSequence = ++sequence.current
    const matches = () =>
      mounted.current && currentOwner.current === target && sequence.current === requestSequence
    setBusy(true)
    setError('')
    try {
      const result = await api<unknown>(path + '/reconfirm', 'POST', request)
      if (matches()) await publishResult(result, request, matches)
    } catch (failure) {
      if (!matches()) return
      setError(failure instanceof Error ? failure.message : '保存结果尚未核实，草稿与原请求保留。')
      if (failure instanceof ApiError && failure.status < 500) setStale(failure.status === 409)
      else setUncertain(true)
    } finally {
      if (matches()) setBusy(false)
    }
  }
  async function lookup(nonce: string, request?: ReconfirmationInput) {
    if (busy) return
    const target = owner,
      requestSequence = ++sequence.current
    const matches = () =>
      mounted.current && currentOwner.current === target && sequence.current === requestSequence
    setBusy(true)
    setError('')
    try {
      const raw = await api<unknown>(path + '/reconfirmations/' + encodeURIComponent(nonce))
      if (!matches()) return
      if (!object(raw) || raw.scene_id !== scene.id || raw.client_request_id !== nonce)
        throw new Error('回执查询目标不一致。')
      if (raw.receipt === null) {
        setError('尚未查到回执；原请求保留，可稍后查询或按原请求重试。')
        return
      }
      if (request) await publishResult(raw.receipt, request, matches)
      else {
        const result = readReceipt(raw.receipt, scene.id, nonce)
        if (!result) throw new Error('历史人工确认回执无法核对。')
        setReceipt(result)
      }
    } catch (failure) {
      if (matches()) setError(failure instanceof Error ? failure.message : '读取回执失败。')
    } finally {
      if (matches()) setBusy(false)
    }
  }
  if (!capable && !savedNonce && !open) return null
  const locked = busy || uncertain || completed || disabled
  return (
    <section className="notice" aria-label="提取场景人工重新核对" style={{ overflowWrap: 'anywhere' }}>
      {disabled && <p role="status">请先保存或处理当前场景表单改动，再重新核对保存记录。</p>}
      {capable && (
        <button
          type="button"
          disabled={busy || disabled}
          onClick={() => {
            setOpen(true)
            if (!preview) void load('initial')
          }}
        >
          重新核对提取场景
        </button>
      )}
      {savedNonce && (
        <button type="button" disabled={busy} onClick={() => void lookup(savedNonce)}>
          查看该场景已保存的人工确认回执
        </button>
      )}
      {open && (
        <>
          <h4>核对当前事件引用，保留原场景</h4>
          <p>
            默认保留时间说明，仅刷新原引用的修订。范围、故事顺序、信息视角和原覆盖审计按保存记录保留；这是作者确认，模型审查另行判断。
          </p>
          {error && <p role="status">{error}</p>}
          {(!capable || externalChange || stale) && (
            <p role="status">当前依据已失效或不再适用，草稿保留；重新读取核对依据后再决定。</p>
          )}
          {busy && <p role="status">正在核对或保存…</p>}
          {!uncertain && (
            <button type="button" disabled={busy} onClick={() => void load(preview ? 'refresh' : 'initial')}>
              重新读取核对依据（保留草稿）
            </button>
          )}
          {incoming && (
            <section aria-label="新读取的场景依据">
              <p>
                旧场景修订 {preview?.request_basis.base_revision} → 当前修订{' '}
                {incoming.request_basis.base_revision}；原文修订 {incoming.source.revision}
                。理由和已编辑时间保留，尚未覆盖当前依据。
              </p>
              <p>当前保存时间：{incoming.request_basis.story_time}</p>
              <button type="button" disabled={busy || uncertain} onClick={acceptIncoming}>
                采用这份新依据继续核对（保留草稿）
              </button>
            </section>
          )}
          {preview && (
            <>
              <p>
                场景：{preview.scene.payload.title} · 场景修订 {preview.scene.revision}；保存稿修订{' '}
                {preview.source.revision}。
              </p>
              <p>
                原范围 {preview.source.start}—{preview.source.end}；故事顺序{' '}
                {preview.scene.payload.story_order}；信息视角 {preview.scene.payload.audience}。
              </p>
              <ol aria-label="原引用与当前修订对照">
                {preview.request_basis.refs.map((ref, index) => {
                  const previous = readSceneOrderBasis(preview.scene.payload.order_basis)?.refs[index]
                  return (
                    <li key={ref.event_id}>
                      {nameFor(ref.event_id)} · {ref.ref} · 事件修订 {previous?.event_revision ?? '未知'} →{' '}
                      {ref.event_revision} · 故事顺序 {ref.story_order}
                    </li>
                  )
                })}
              </ol>
              <fieldset disabled={locked}>
                <legend>本次时间说明</legend>
                <label className="check">
                  <input
                    type="radio"
                    name={'scene-time-' + scene.id}
                    checked={!editTime}
                    onChange={() => setEditTime(false)}
                  />
                  保留原时间说明
                </label>
                <p>{preview.request_basis.story_time}</p>
                <label className="check">
                  <input
                    type="radio"
                    name={'scene-time-' + scene.id}
                    checked={editTime}
                    onChange={() => setEditTime(true)}
                  />
                  由我修改描述性时间（需要原文依据）
                </label>
                {editTime && (
                  <label>
                    本次确认的故事时间
                    <input
                      maxLength={200}
                      value={storyTime}
                      onChange={(event) => setStoryTime(event.target.value)}
                    />
                  </label>
                )}
                <label>
                  人工重新核对理由
                  <textarea
                    rows={3}
                    maxLength={2000}
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                  />
                </label>
              </fieldset>
              <label>
                原场景冻结原文
                <textarea
                  ref={sourceInput}
                  readOnly
                  rows={6}
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
              {editTime && (
                <>
                  <p>
                    从显示的原范围选一段连续原文，确认它支持本次时间说明；这里只核对出处与范围，含义仍由你判断。
                  </p>
                  <div className="form-grid">
                    <label>
                      原文选段起点（从0开始）
                      <input
                        type="number"
                        min={0}
                        max={preview.source.quote.length}
                        value={selection?.start ?? 0}
                        disabled={locked}
                        onChange={(event) =>
                          setSelection({ start: Number(event.target.value), end: selection?.end ?? 0 })
                        }
                      />
                    </label>
                    <label>
                      原文选段终点
                      <input
                        type="number"
                        min={0}
                        max={preview.source.quote.length}
                        value={selection?.end ?? 0}
                        disabled={locked}
                        onChange={(event) =>
                          setSelection({ start: selection?.start ?? 0, end: Number(event.target.value) })
                        }
                      />
                    </label>
                  </div>
                  <p>
                    也可用键盘填写原文内选段位置；所选原文在下方预览，不能超出显示范围或截断表情与扩展汉字。
                  </p>
                  {selected && <blockquote aria-label="当前连续选段预览">{selected.quote}</blockquote>}
                  <div className="actions">
                    <button
                      type="button"
                      disabled={locked || !selected}
                      onClick={() => {
                        if (selected) setQuotes([selected])
                      }}
                    >
                      使用当前连续选段作为时间依据
                    </button>
                    <button
                      type="button"
                      disabled={locked || !selectedEvidence(preview.source, 0, preview.source.quote.length)}
                      onClick={() => {
                        const whole = selectedEvidence(preview.source, 0, preview.source.quote.length)
                        if (whole) setQuotes([whole])
                      }}
                    >
                      使用显示的整段原文作为依据
                    </button>
                  </div>
                  {quotes.map((quote, index) => (
                    <blockquote key={index}>{quote.quote}</blockquote>
                  ))}
                  {!!quotes.length && (
                    <button type="button" disabled={locked} onClick={() => setQuotes([])}>
                      清除本次时间依据
                    </button>
                  )}
                  {changedTime && !quotes.length && <p role="status">请先选取原文依据，再确认更改时间。</p>}
                </>
              )}
              <button
                type="button"
                className="primary"
                disabled={busy || !canSubmit}
                onClick={() => void submit()}
              >
                确认重新核对并保存为新场景修订
              </button>
              <details>
                <summary>查看核对版本与来源信息</summary>
                <pre className="report">
                  {JSON.stringify(
                    {
                      scene_id: scene.id,
                      source: preview.source,
                      request_basis: preview.request_basis,
                      payload_hash_version: 'scene-time-public-payload/1',
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
            </>
          )}
          {uncertain && pending.current && (
            <section aria-label="保存结果待核实">
              <p role="status">保存结果尚未核实，原请求和提交标识保留；先查询回执，输入暂时锁定。</p>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void lookup(pending.current!.request.client_request_id, pending.current!.request)
                }
              >
                查询本次人工确认回执
              </button>
              <button type="button" disabled={busy} onClick={() => void submit(true)}>
                按原请求重试
              </button>
            </section>
          )}
          {completed && (
            <button type="button" disabled={busy} onClick={() => void load('new')}>
              再次核对当前保存场景
            </button>
          )}
        </>
      )}
      {receipt && <ReceiptDetails receipt={receipt} />}
      {!open && error && <p role="status">{error}</p>}
    </section>
  )
}
