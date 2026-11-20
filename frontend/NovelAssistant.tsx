import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { ApiError, label } from './api'
import { createDraftOwner, discardOwnDraft, readDraft, writeDraft } from './drafts'
import { cancelAssistantTask, type CreationBench } from './workbenchAssistantModel'
import {
  isNovelAssistantStage,
  novelAssistantScopeLabel,
  novelAssistantStages,
  novelAssistantValueText,
  previewNovelAssistantDelivery,
  readNovelAssistant,
  readNovelAssistantDeliveries,
  readNovelAssistantDestinations,
  sameNovelAssistantScope,
  saveNovelAssistantDelivery,
  saveNovelAssistantDraft,
  sendNovelAssistantTurn,
  type NovelAssistantCard,
  type NovelAssistantDelivery,
  type NovelAssistantDeliveryInput,
  type NovelAssistantDestination,
  type NovelAssistantDraft,
  type NovelAssistantHistory,
  type NovelAssistantPreview,
  type NovelAssistantReference,
  type NovelAssistantTurn,
  type NovelAssistantTurnInput,
} from './novelAssistantModel'
import './novelAssistant.css'

const draftOwner = createDraftOwner('ainovel-novel-assistant-window')
const maxQuestion = 6000

function errorText(error: unknown) {
  return error instanceof Error ? error.message : '请求失败，请稍后重试。'
}
function pending(turn: NovelAssistantTurn) {
  return turn.status === 'queued' || turn.status === 'running'
}
function mergeTurns(previous: NovelAssistantTurn[], fresh: NovelAssistantTurn[]) {
  return [...new Map([...previous, ...fresh].map((turn) => [turn.id, turn])).values()].sort(
    (a, b) => a.sequence - b.sequence,
  )
}

function useChatDraft(branchId: string) {
  const draftId = 'novel-assistant:' + branchId
  const [text, setText] = useState('')
  const [base, setBase] = useState<NovelAssistantDraft>()
  const [conflict, setConflict] = useState<NovelAssistantDraft>()
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const state = useRef({ text, base, conflict })
  const io = useRef<Promise<boolean> | undefined>(undefined)
  const browser = useRef(Promise.resolve())
  const alive = useRef(false)
  const initialized = useRef(false)
  const draftEdited = useRef(false)

  useEffect(() => {
    alive.current = true
    void draftOwner()
    return () => {
      alive.current = false
    }
  }, [])

  const updateBase = useCallback((next: NovelAssistantDraft) => {
    state.current.base = next
    if (alive.current) setBase(next)
  }, [])

  const localCopy = useCallback(
    (text: string, base: NovelAssistantDraft) => {
      browser.current = browser.current
        .then(async () => {
          const owner = await draftOwner()
          if (text === base.text) await discardOwnDraft(draftId, owner)
          else await writeDraft({ id: draftId, base: base.revision, text, updated: Date.now() }, owner)
        })
        .catch(() => {
          if (alive.current) setError('此浏览器无法暂存输入，请等本机保存成功后再关闭。')
        })
    },
    [draftId],
  )

  const edit = useCallback(
    (next: string) => {
      state.current.text = next
      if (alive.current) setText(next)
      if (state.current.base) localCopy(next, state.current.base)
    },
    [localCopy],
  )

  const receive = useCallback(
    async (saved: NovelAssistantDraft) => {
      if (initialized.current) {
        const current = state.current
        if (io.current || !current.base || saved.revision <= current.base.revision) return
        if (current.text === saved.text || current.text === current.base.text) {
          current.conflict = undefined
          setConflict(undefined)
          updateBase(saved)
          edit(saved.text)
        } else {
          current.conflict = saved
          setConflict(saved)
        }
        return
      }
      initialized.current = true
      const own = await readDraft(draftId, await draftOwner()).catch(() => undefined)
      if (!alive.current) {
        initialized.current = false
        return
      }
      updateBase(saved)
      if (own && !own.fromOtherWindow && own.text !== saved.text) {
        const stale = own.base !== saved.revision
        if (stale) {
          state.current.conflict = saved
          setConflict(saved)
        }
        state.current.text = own.text
        setText(own.text)
        draftEdited.current = true
      } else {
        edit(saved.text)
      }
    },
    [draftId, edit, updateBase],
  )

  const save = useCallback(async (): Promise<boolean> => {
    if (io.current) return io.current
    const { text, base, conflict } = state.current
    if (!base || conflict) return false
    if (text === base.text) return true
    if (alive.current) {
      setSaving(true)
      setError('')
    }
    io.current = (async () => {
      try {
        const saved = await saveNovelAssistantDraft(branchId, text, base.revision)
        updateBase(saved)
        localCopy(state.current.text, saved)
        return true
      } catch (error) {
        if (error instanceof ApiError && error.code === 'novel_assistant_draft_changed') {
          try {
            const remote = (await readNovelAssistant(branchId)).draft
            if (remote.text === text) {
              updateBase(remote)
              localCopy(state.current.text, remote)
              return true
            }
            state.current.conflict = remote
            if (alive.current) setConflict(remote)
            return false
          } catch (refreshError) {
            if (alive.current) setError(errorText(refreshError))
            return false
          }
        }
        if (alive.current) setError('输入尚未保存。' + errorText(error))
        return false
      } finally {
        io.current = undefined
        if (alive.current) setSaving(false)
      }
    })()
    return io.current
  }, [branchId, localCopy, updateBase])

  useEffect(() => {
    if (!base || base.text === text || conflict || saving || error || !draftEdited.current) return
    const timer = window.setTimeout(() => void save(), 350)
    return () => window.clearTimeout(timer)
  }, [base, conflict, error, save, saving, text])

  return {
    text,
    ready: !!base,
    dirty: !!base && base.text !== text,
    saving,
    conflict,
    error,
    receive,
    save,
    async flush() {
      while (state.current.base && state.current.base.text !== state.current.text) {
        if (!(await save())) return false
      }
      return !!state.current.base && !state.current.conflict
    },
    change(next: string) {
      if (!state.current.conflict) setError('')
      draftEdited.current = true
      edit(next)
    },
    resolve(useMine: boolean) {
      const remote = state.current.conflict
      if (!remote) return
      state.current.conflict = undefined
      setConflict(undefined)
      setError('')
      updateBase(remote)
      edit(useMine ? state.current.text : remote.text)
      if (useMine) void save()
    },
    accepted(question: string) {
      const current = state.current
      if (current.text !== question || !current.base) return
      // A 202 confirms this text was sent, even if the follow-up read fails.
      updateBase({ ...current.base, text: '' })
      edit('')
    },
  }
}

function writeAttempt(branchId: string, input?: NovelAssistantTurnInput) {
  try {
    const key = 'ainovel-novel-assistant-request:' + branchId
    if (input) sessionStorage.setItem(key, JSON.stringify(input))
    else sessionStorage.removeItem(key)
  } catch {
    // The mounted component still retains an identical retry request.
  }
}
function readAttempt(branchId: string): NovelAssistantTurnInput | undefined {
  try {
    const raw = sessionStorage.getItem('ainovel-novel-assistant-request:' + branchId)
    if (raw) {
      const input = JSON.parse(raw) as NovelAssistantTurnInput
      if (typeof input.client_request_id === 'string' && isNovelAssistantStage(input.stage)) return input
    }
  } catch {
    // Unreadable recovery state never causes a request to be sent.
  }
}

type NovelAssistantProps = {
  branchId: string
  stage: CreationBench
  scopeId: string | null
  scopeLabel?: string
  novelTitle?: string
  branchLabel?: string
  account: string
  modelReady?: boolean
  open?: boolean
  onOpenChange?: (open: boolean) => void
  focusTurnId?: string
  onSourceFocus?: (turnId: string) => void
  onViewDelivery?: (delivery: NovelAssistantDelivery) => void
  onDeliverySaved?: (delivery: NovelAssistantDelivery) => void
}

export function NovelAssistant(props: NovelAssistantProps) {
  return <NovelAssistantPanel key={props.branchId} {...props} />
}

function NovelAssistantPanel({
  branchId,
  stage,
  scopeId,
  scopeLabel,
  novelTitle,
  branchLabel,
  account,
  modelReady,
  open: controlledOpen,
  onOpenChange,
  focusTurnId,
  onSourceFocus,
  onViewDelivery,
  onDeliverySaved,
}: NovelAssistantProps) {
  const [localOpen, setLocalOpen] = useState(false)
  const open = controlledOpen ?? localOpen
  const [activated, setActivated] = useState(open)
  const [history, setHistory] = useState<NovelAssistantHistory>()
  const [deliveries, setDeliveries] = useState<NovelAssistantDelivery[]>([])
  const [extraReferences, setExtraReferences] = useState<NovelAssistantReference[]>([])
  const [useCurrent, setUseCurrent] = useState(true)
  const [failure, setFailure] = useState('')
  const [sending, setSending] = useState(false)
  const [paging, setPaging] = useState(false)
  const [attempt, setAttempt] = useState(() => readAttempt(branchId))
  const [notice, setNotice] = useState('')
  const draft = useChatDraft(branchId)
  const panelId = useId()
  const heading = useRef<HTMLHeadingElement>(null)
  const opener = useRef<HTMLButtonElement>(null)
  const transcript = useRef<HTMLDivElement>(null)
  const alive = useRef(false)
  const mutation = useRef(false)
  const latestPull = useRef(0)
  const lastTurn = history?.turns.at(-1)
  const hasHistory = history !== undefined
  const olderCursor = history?.next_before
  const focusCursor = history?.turns.find(({ id }) => id === focusTurnId)?.sequence
  const requestPending = history?.turns.some(pending) ?? false
  const current: NovelAssistantReference = { stage, scope_id: scopeId, scope_label: scopeLabel ?? '' }
  const references = extraReferences.filter((row) => !useCurrent || !sameNovelAssistantScope(row, current))

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  useEffect(() => {
    if (open) {
      setActivated(true)
      heading.current?.focus()
    }
  }, [open])

  function changeOpen(next: boolean, restoreFocus = true) {
    setLocalOpen(next)
    onOpenChange?.(next)
    if (!next && restoreFocus) requestAnimationFrame(() => opener.current?.focus())
  }

  const refresh = useCallback(async () => {
    const request = ++latestPull.current
    try {
      const next = await readNovelAssistant(branchId)
      if (!alive.current || request !== latestPull.current) return
      setHistory((previous) =>
        previous && previous.revision > next.revision
          ? previous
          : {
              ...next,
              turns: mergeTurns(previous?.turns ?? [], next.turns),
              next_before: previous ? previous.next_before : next.next_before,
            },
      )
      await draft.receive(next.draft)
    } catch (error) {
      if (alive.current && request === latestPull.current) setFailure(errorText(error))
    }
  }, [branchId, draft.receive])

  const refreshDeliveries = useCallback(async () => {
    try {
      const rows = await readNovelAssistantDeliveries(branchId)
      if (alive.current)
        setDeliveries((previous) => [...new Map([...previous, ...rows].map((row) => [row.id, row])).values()])
    } catch (error) {
      if (alive.current) setFailure(errorText(error))
    }
  }, [branchId])

  useEffect(() => {
    if (!open) return
    void refresh()
    void refreshDeliveries()
  }, [open, refresh, refreshDeliveries])

  useEffect(() => {
    if (!activated || !requestPending) return
    const timer = window.setInterval(() => void refresh(), 1600)
    return () => window.clearInterval(timer)
  }, [activated, refresh, requestPending])

  async function older() {
    if (paging || history?.next_before == null) return
    setPaging(true)
    setFailure('')
    try {
      const page = await readNovelAssistant(branchId, history.next_before)
      if (!alive.current) return
      setHistory(
        (previous) =>
          previous && {
            ...previous,
            next_before: page.next_before,
            turns: mergeTurns(page.turns, previous.turns),
          },
      )
    } catch (error) {
      if (alive.current) setFailure(errorText(error))
    } finally {
      if (alive.current) setPaging(false)
    }
  }

  useEffect(() => {
    if (!open || focusTurnId || !lastTurn || !transcript.current) return
    const list = transcript.current
    if (list.scrollTop === 0 || list.scrollHeight - list.clientHeight - list.scrollTop < 160)
      list.scrollTop = list.scrollHeight
  }, [lastTurn?.id, open])

  useEffect(() => {
    if (!open || !focusTurnId || !hasHistory) return
    const target = document.getElementById(panelId + '-turn-' + focusTurnId)
    if (target) {
      target.focus()
      onSourceFocus?.(focusTurnId)
      return
    }
    if (olderCursor == null) {
      setNotice('未找到这条来源轮次，请刷新对话后重试。')
      return
    }
    let active = true
    const seek = async () => {
      const turns: NovelAssistantTurn[] = []
      let cursor: number | null = olderCursor
      while (active && cursor !== null) {
        const page = await readNovelAssistant(branchId, cursor)
        turns.push(...page.turns)
        cursor = page.next_before
        if (turns.some(({ id }) => id === focusTurnId)) break
      }
      if (!active) return
      setHistory(
        (previous) =>
          previous && { ...previous, turns: mergeTurns(turns, previous.turns), next_before: cursor },
      )
    }
    void seek().catch((error) => active && setFailure(errorText(error)))
    return () => {
      active = false
    }
  }, [branchId, focusCursor, focusTurnId, hasHistory, olderCursor, open, onSourceFocus, panelId])

  async function send(event?: FormEvent) {
    event?.preventDefault()
    if (
      mutation.current ||
      !history ||
      (!attempt &&
        (!account || modelReady === false || !draft.text.trim() || draft.conflict || requestPending))
    )
      return
    mutation.current = true
    setSending(true)
    setFailure('')
    setNotice('')
    let request = attempt
    try {
      if (!request) {
        if (!(await draft.flush()) || !alive.current) return
        request = {
          base_revision: history.revision,
          stage,
          scope_id: scopeId,
          question: draft.text,
          account,
          client_request_id: crypto.randomUUID(),
          include_current_scope: useCurrent,
          references: references.map(({ stage, scope_id }) => ({ stage, scope_id })),
        }
        setAttempt(request)
        writeAttempt(branchId, request)
      }
      const accepted = await sendNovelAssistantTurn(branchId, request)
      writeAttempt(branchId)
      if (!alive.current) return
      setAttempt(undefined)
      draft.accepted(request.question)
      setHistory(
        (previous) =>
          previous && {
            ...previous,
            revision: Math.max(previous.revision, accepted.revision),
            turns: mergeTurns(previous.turns, [accepted.turn]),
          },
      )
      setNotice('已提交对话。')
      await refresh()
    } catch (error) {
      if (!alive.current) return
      setFailure(errorText(error))
      if (error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408) {
        setAttempt(undefined)
        writeAttempt(branchId)
        void refresh()
      }
    } finally {
      mutation.current = false
      if (alive.current) setSending(false)
    }
  }

  async function cancel(turn: NovelAssistantTurn) {
    if (mutation.current || !turn.task_id) return
    mutation.current = true
    setSending(true)
    setFailure('')
    try {
      await cancelAssistantTask(turn.task_id)
      await refresh()
    } catch (error) {
      if (alive.current) setFailure(errorText(error))
    } finally {
      mutation.current = false
      if (alive.current) setSending(false)
    }
  }

  function pinCurrent() {
    setExtraReferences((prior) => [...prior, current])
    setNotice('已固定此引用。切换工作台后仍可在这里移除。')
  }

  return (
    <aside
      className="novel-assistant"
      data-open={open}
      aria-label="作品 AI 对话"
      onBlur={(event) => {
        if (
          open &&
          event.relatedTarget &&
          !event.currentTarget.contains(event.relatedTarget) &&
          window.matchMedia('(max-width: 1050px)').matches
        )
          changeOpen(false, false)
      }}
      onKeyDown={(event) => {
        if (open && event.key === 'Escape' && !event.nativeEvent.isComposing && !event.defaultPrevented) {
          event.preventDefault()
          event.stopPropagation()
          changeOpen(false)
        }
      }}
    >
      <button
        className="novel-assistant-toggle"
        ref={opener}
        type="button"
        hidden={open}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => changeOpen(true)}
      >
        AI 对话
      </button>
      <section
        className="novel-assistant-panel"
        id={panelId}
        hidden={!open}
        aria-labelledby={panelId + '-title'}
      >
        <header className="novel-assistant-header">
          <h2 ref={heading} id={panelId + '-title'} tabIndex={-1}>
            AI 对话
          </h2>
          <button type="button" onClick={() => changeOpen(false)}>
            收起对话
          </button>
          {(novelTitle || branchLabel) && (
            <p className="novel-assistant-work-title" tabIndex={0}>
              {[novelTitle, branchLabel].filter(Boolean).join(' · ')}
            </p>
          )}
        </header>

        <div ref={transcript} className="novel-assistant-transcript" aria-label="作品对话记录">
          {history?.next_before != null && (
            <button type="button" onClick={() => void older()} disabled={paging}>
              查看更早的对话
            </button>
          )}
          {!history && !failure && <p role="status">正在读取对话…</p>}
          {history?.turns.length === 0 && <p className="novel-assistant-muted">输入你想讨论的创作问题。</p>}
          {history?.turns.map((turn) => (
            <article
              key={turn.id}
              id={panelId + '-turn-' + turn.id}
              tabIndex={-1}
              className="novel-assistant-turn"
            >
              <h3>第 {turn.sequence} 轮</h3>
              <p className="novel-assistant-question">{turn.question}</p>
              <p className="novel-assistant-muted">
                {turn.include_current_scope ? novelAssistantScopeLabel(turn) : '未引用当前对象'}
                {turn.references
                  .map(novelAssistantScopeLabel)
                  .map((text) => ' · ' + text)
                  .join('')}
              </p>
              {turn.result ? (
                <>
                  <p className="novel-assistant-answer">{turn.result.answer}</p>
                  {turn.result.cards.map((card) => (
                    <DeliveryCard
                      branchId={branchId}
                      key={card.id}
                      turnId={turn.id}
                      card={card}
                      saved={deliveries.filter((row) => row.turn_id === turn.id && row.card_id === card.id)}
                      onViewDelivery={onViewDelivery}
                      onSaved={(saved) => {
                        setDeliveries((rows) => [saved, ...rows.filter(({ id }) => id !== saved.id)])
                        onDeliverySaved?.(saved)
                      }}
                    />
                  ))}
                </>
              ) : (
                <>
                  <p role="status">
                    {label(turn.status)}
                    {turn.error ? ' · ' + turn.error : ''}
                  </p>
                  {pending(turn) ? (
                    <button type="button" onClick={() => void cancel(turn)} disabled={sending}>
                      取消本轮
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => {
                        draft.change(turn.question)
                        setNotice('原问题已放回编辑框，请核对引用后发送。')
                      }}
                      disabled={!!draft.text || !draft.ready || sending}
                    >
                      复用问题
                    </button>
                  )}
                </>
              )}
            </article>
          ))}
          {failure && (
            <div className="novel-assistant-error" role="alert">
              <p>{failure}</p>
              <button
                type="button"
                onClick={() => {
                  setFailure('')
                  void refresh()
                  void refreshDeliveries()
                }}
              >
                刷新对话
              </button>
            </div>
          )}
        </div>

        <form className="novel-assistant-composer" onSubmit={(event) => void send(event)}>
          <details>
            <summary>
              本轮引用 · {useCurrent ? novelAssistantScopeLabel(current) : '当前对象未加入'}
              {references.length > 0 && `，另有 ${references.length} 项`}
            </summary>
            <label className="novel-assistant-check">
              <input
                type="checkbox"
                checked={useCurrent}
                onChange={(event) => setUseCurrent(event.target.checked)}
                disabled={sending || !!attempt}
              />
              引用当前对象
            </label>
            {extraReferences.length > 0 && (
              <ul>
                {extraReferences.map((row) => (
                  <li key={row.stage + ':' + row.scope_id}>
                    {novelAssistantScopeLabel(row)}{' '}
                    <button
                      type="button"
                      disabled={sending || !!attempt}
                      onClick={() =>
                        setExtraReferences((prior) =>
                          prior.filter((item) => !sameNovelAssistantScope(item, row)),
                        )
                      }
                      aria-label={'移除引用 · ' + novelAssistantScopeLabel(row)}
                    >
                      移除
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <button
              type="button"
              disabled={
                sending ||
                !!attempt ||
                extraReferences.length >= 5 ||
                extraReferences.some((item) => sameNovelAssistantScope(item, current))
              }
              onClick={pinCurrent}
            >
              固定当前引用
            </button>
          </details>
          {draft.conflict && (
            <div className="novel-assistant-error" role="alert">
              <p>另一窗口已保存新的对话草稿，请选择保留哪一份输入。</p>
              <details>
                <summary>查看另一窗口的草稿</summary>
                <p>{draft.conflict.text || '（空草稿）'}</p>
              </details>
              <div className="novel-assistant-actions">
                <button type="button" onClick={() => draft.resolve(true)}>
                  保留我的输入
                </button>
                <button type="button" onClick={() => draft.resolve(false)}>
                  使用已保存输入
                </button>
              </div>
            </div>
          )}
          <label>
            对话输入
            <textarea
              rows={3}
              maxLength={maxQuestion}
              value={draft.text}
              onChange={(event) => draft.change(event.target.value)}
              disabled={!draft.ready || sending || !!attempt}
            />
          </label>
          <div className="novel-assistant-actions">
            <button
              className="primary"
              type="submit"
              disabled={
                !draft.ready ||
                sending ||
                (attempt
                  ? false
                  : !account ||
                    modelReady === false ||
                    !draft.text.trim() ||
                    !!draft.conflict ||
                    requestPending)
              }
            >
              {attempt ? '重试同一轮提交' : sending ? '正在提交…' : '发送'}
            </button>
            {draft.error && !draft.conflict && (
              <button type="button" disabled={draft.saving || sending} onClick={() => void draft.save()}>
                重试保存输入
              </button>
            )}
            <span className="novel-assistant-muted" role="status">
              {draft.error ||
                (draft.saving
                  ? '正在保存输入…'
                  : draft.dirty
                    ? '输入待保存'
                    : draft.text
                      ? '输入已保存'
                      : '')}
            </span>
          </div>
          {notice && (
            <p className="novel-assistant-muted" role="status">
              {notice}
            </p>
          )}
          {(!account || modelReady === false) && (
            <p className="novel-assistant-muted">发起新对话需要在「账户与预算」配置模型。</p>
          )}
        </form>
      </section>
    </aside>
  )
}

function DeliveryCard({
  branchId,
  card,
  turnId,
  saved,
  onSaved,
  onViewDelivery,
}: {
  branchId: string
  card: NovelAssistantCard
  turnId: string
  saved: NovelAssistantDelivery[]
  onSaved: (delivery: NovelAssistantDelivery) => void
  onViewDelivery?: (delivery: NovelAssistantDelivery) => void
}) {
  const [editing, setEditing] = useState(false)
  const [stage, setStage] = useState(card.stage)
  const [destinations, setDestinations] = useState<NovelAssistantDestination[]>()
  const [scopeId, setScopeId] = useState<string | null>()
  const [field, setField] = useState('')
  const [text, setText] = useState(novelAssistantValueText(card.value))
  const [preview, setPreview] = useState<NovelAssistantPreview>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const alive = useRef(false)
  const mutation = useRef(false)
  const target = destinations?.find((item) => item.scope_id === scopeId)
  const fieldLabel = target?.fields.find(({ id }) => id === field)?.label

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  useEffect(() => {
    if (!editing) return
    let active = true
    void readNovelAssistantDestinations(branchId, stage)
      .then((options) => {
        if (!active) return
        setDestinations(options)
        if (stage === card.stage) {
          const suggested = options.find(({ scope_id }) => scope_id === card.scope_id)
          if (suggested) {
            setScopeId(suggested.scope_id)
            if (suggested.fields.some(({ id }) => id === card.field)) setField(card.field)
          }
        }
      })
      .catch((error) => {
        if (active) setError(errorText(error))
      })
    return () => {
      active = false
    }
  }, [attempt, branchId, card.field, card.scope_id, card.stage, editing, stage])

  function editStage(next: string) {
    if (!isNovelAssistantStage(next)) return
    setStage(next)
    setDestinations(undefined)
    setScopeId(undefined)
    setField('')
    setPreview(undefined)
    setError('')
  }

  function input(): NovelAssistantDeliveryInput | undefined {
    if (scopeId === undefined || !fieldLabel || !text.trim()) return
    return {
      turn_id: turnId,
      card_id: card.id,
      stage,
      scope_id: scopeId,
      field,
      value:
        field === 'genre_ids'
          ? text
              .split(/[、,，]/)
              .map((item) => item.trim())
              .filter(Boolean)
          : text,
    }
  }

  async function persist(confirm: boolean) {
    const payload = input()
    if (!payload || mutation.current || (confirm && !preview)) return
    mutation.current = true
    setBusy(true)
    setError('')
    try {
      if (confirm && preview) {
        const delivery = await saveNovelAssistantDelivery(branchId, {
          ...payload,
          value: preview.value,
          basis_sha256: preview.basis_sha256,
        })
        if (!alive.current) return
        onSaved(delivery)
        setPreview(undefined)
        setEditing(false)
      } else {
        const compared = await previewNovelAssistantDelivery(branchId, payload)
        if (!sameNovelAssistantScope(compared, payload) || compared.field !== payload.field)
          throw new Error('返回的目标与当前选择不一致，请重新比较。')
        if (alive.current) setPreview(compared)
      }
    } catch (error) {
      if (alive.current) {
        if (error instanceof ApiError && error.status === 409) setPreview(undefined)
        setError(errorText(error))
      }
    } finally {
      mutation.current = false
      if (alive.current) setBusy(false)
    }
  }

  return (
    <section className="novel-assistant-card" aria-label={card.title}>
      <h4>{card.title}</h4>
      <p className="novel-assistant-value">{novelAssistantValueText(card.value)}</p>
      {card.reason && <p className="novel-assistant-muted">{card.reason}</p>}
      {saved.map((delivery) => (
        <div className="novel-assistant-saved" key={delivery.id}>
          <p>
            已送达 · {novelAssistantStages[delivery.stage]} · {delivery.target_label} · {delivery.field_label}
            {!delivery.basis_current && '（目标有新修订，载入前请重新核对）'}
          </p>
          {onViewDelivery && (
            <button type="button" onClick={() => onViewDelivery(delivery)}>
              查看去向
            </button>
          )}
        </div>
      ))}
      {!editing ? (
        <button
          type="button"
          onClick={() => {
            setEditing(true)
            setError('')
          }}
        >
          {saved.length ? '另选去向' : '选择保存去向'}
        </button>
      ) : (
        <div className="novel-assistant-destination">
          <label>
            保存到工作台
            <select value={stage} onChange={(event) => editStage(event.target.value)} disabled={busy}>
              {Object.entries(novelAssistantStages).map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label>
            目标对象
            <select
              value={scopeId === null ? 'aggregate' : scopeId === undefined ? '' : 'scope:' + scopeId}
              disabled={!destinations?.length || busy}
              onChange={(event) => {
                const key = event.target.value
                setScopeId(key === '' ? undefined : key === 'aggregate' ? null : key.slice(6))
                setField('')
                setPreview(undefined)
              }}
            >
              <option value="">{destinations ? '选择对象' : '正在读取对象…'}</option>
              {destinations?.map((row) => (
                <option
                  key={row.scope_id ?? 'aggregate'}
                  value={row.scope_id === null ? 'aggregate' : 'scope:' + row.scope_id}
                >
                  {row.label}
                </option>
              ))}
            </select>
          </label>
          {destinations?.length === 0 && (
            <p className="novel-assistant-muted">当前工作台没有可保存的对象。</p>
          )}
          <label>
            目标字段
            <select
              value={field}
              disabled={!target || busy}
              onChange={(event) => {
                setField(event.target.value)
                setPreview(undefined)
              }}
            >
              <option value="">选择字段</option>
              {target?.fields.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            候选内容
            <textarea
              rows={3}
              value={text}
              disabled={busy}
              onChange={(event) => {
                setText(event.target.value)
                setPreview(undefined)
              }}
            />
          </label>
          {preview && (
            <div className="novel-assistant-comparison">
              <h5>
                {preview.target_label} · {preview.field_label}
              </h5>
              <dl>
                <dt>当前内容</dt>
                <dd>{novelAssistantValueText(preview.before) || '（空）'}</dd>
                <dt>待保存内容</dt>
                <dd>{novelAssistantValueText(preview.value)}</dd>
              </dl>
            </div>
          )}
          {error && (
            <p className="novel-assistant-error" role="alert">
              {error}
            </p>
          )}
          <div className="novel-assistant-actions">
            {preview ? (
              <button className="primary" type="button" disabled={busy} onClick={() => void persist(true)}>
                确认保存到{novelAssistantStages[stage]}
              </button>
            ) : (
              <button type="button" disabled={!input() || busy} onClick={() => void persist(false)}>
                预览变更
              </button>
            )}
            {!destinations && error && (
              <button
                type="button"
                onClick={() => {
                  setError('')
                  setAttempt((number) => number + 1)
                }}
              >
                重新读取目标
              </button>
            )}
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setEditing(false)
                setPreview(undefined)
              }}
            >
              取消选择
            </button>
            {busy && <span role="status">正在处理…</span>}
          </div>
        </div>
      )}
    </section>
  )
}
