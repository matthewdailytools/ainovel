import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from 'react'
import { ApiError, label as taskLabel } from './api'
import { WorkbenchTabs } from './WorkbenchTabs'
import type { NovelAssistantDelivery, NovelAssistantReference } from './novelAssistantModel'
import {
  allowsAssistantTarget,
  assistantActions,
  assistantDraftText,
  assistantLocationKey,
  assistantTurnMatchesTarget,
  assistantTurnPending,
  canApplyAssistantSuggestion,
  cancelAssistantTask,
  createAssistantConversation,
  listAssistantConversations,
  postAssistantTurn,
  readAssistantHistory,
  recordAssistantDraftApplication,
  type AssistantAction,
  type AssistantCitation,
  type AssistantConversation,
  type AssistantDraftBinding,
  type AssistantHistory,
  type AssistantLocation,
  type AssistantRequestOptions,
  type AssistantSuggestion,
  type AssistantTurn,
  type AssistantTurnInput,
  type AssistantValue,
  type CreationBench,
} from './workbenchAssistantModel'
import './workbenchAssistant.css'

export type { AssistantAction, AssistantCitation, AssistantRequestOptions, AssistantValue, CreationBench }

const specialists: Record<CreationBench, string> = {
  M1: '立项助手',
  M2: '拆解助手',
  M3: '设定助手',
  M4: '大纲助手',
  M5: '写作助手',
  M6: '校验助手',
  M7: '完本助手',
}
const directQuestions: Record<Exclude<AssistantAction, 'discuss'>, string> = {
  suggest: '请给这个字段一条具体的改进建议。',
  refine: '请润色这个字段，保留原意并说明改动理由。',
  review: '请复核这个字段，指出依据和仍需确认的地方。',
}
const assistantTabs = [
  ['conversation', '对话'],
  ['history', '历史'],
  ['tools', '本台工具'],
] as const
type AssistantView = (typeof assistantTabs)[number][0]

type Scope = {
  scopeId: string | null
  stage?: CreationBench
  workspaceEligible?: boolean
  label: string
  account?: string
  onSource?: (source: AssistantCitation) => void
}
type TargetOptions<T extends AssistantValue> = {
  field: string
  label: string
  value: T
  onApply?: (value: T) => boolean | void
  enabled?: boolean
  scopeId?: string | null
  scopeLabel?: string
  objectKey?: string
  contextKey?: string
  quickQuestion?: string
  request?: AssistantRequestOptions
  element?: RefObject<HTMLElement | null>
}
type Target = AssistantDraftBinding &
  Omit<TargetOptions<AssistantValue>, 'onApply' | 'value'> & {
    apply?: (value: AssistantValue) => boolean
  }
type Intent = {
  question: string
  account: string
  action: AssistantAction
  target?: Target
}
type Selection = {
  host: string
  baseScope: string | null
  id: string
  location: AssistantLocation
  label: string
  action: AssistantAction
  binding: Pick<Target, 'field' | 'objectKey' | 'contextKey'>
  launch?: { id: number; intent: Intent }
}
type AssistantContextValue = {
  host: string
  open: boolean
  dockOpen: boolean
  activated: boolean
  revealRequest: number
  show: () => void
  select: () => void
  dismiss: () => void
  close: (onReturnFocus?: () => void) => void
  resetTarget: () => void
  railId: string
  stage: CreationBench
  scope: Scope | undefined
  account?: string
  location: AssistantLocation
  selection: Selection | undefined
  registerScope: (host: string, id: string, scope: Scope) => () => void
  registerTarget: (id: string, target: RefObject<Target>) => () => void
  openTarget: (id: string, action: AssistantAction) => void
  claimLaunch: (id: number) => boolean
  selectedTarget: () => Target | undefined
  findTarget: (turn: AssistantTurn) => Target | undefined
  continueInWorkspace?: () => void
  findDeliveryTarget: (delivery: NovelAssistantDelivery) => Target | undefined
}
const AssistantContext = createContext<AssistantContextValue | null>(null)
const closedDock = { open: false, select: () => undefined, dismiss: () => undefined }

export function useDeliveryTarget() {
  return useContext(AssistantContext)?.findDeliveryTarget
}

export function useAssistantDock(): { open: boolean; select: () => void; dismiss: () => void } {
  const context = useContext(AssistantContext)
  return context ? { open: context.dockOpen, select: context.select, dismiss: context.dismiss } : closedDock
}

export function useAssistantScope({ scopeId, label, stage, workspaceEligible, account, onSource }: Scope) {
  const context = useContext(AssistantContext)
  const register = context?.registerScope
  const host = context?.host
  const currentStage = context?.stage
  const id = useId()
  const source = useRef(onSource)
  source.current = onSource
  useLayoutEffect(() => {
    if (!register || !host || (stage && stage !== currentStage)) return
    return register(host, id, {
      scopeId,
      label,
      workspaceEligible,
      account,
      ...(onSource ? { onSource: (citation) => source.current?.(citation) } : {}),
    })
  }, [register, host, id, scopeId, label, stage, currentStage, workspaceEligible, account, !!onSource])
}

export function useAssistantTarget<T extends AssistantValue>(options: TargetOptions<T>) {
  const context = useContext(AssistantContext)
  const id = useId()
  const register = context?.registerTarget
  const openTarget = context?.openTarget
  const latest = useRef(options)
  const target = useRef<Target>(options)
  useLayoutEffect(() => {
    latest.current = options
    target.current = {
      ...options,
      apply: options.onApply
        ? (value) => {
            const current = latest.current
            if (!current.onApply || current.enabled === false || typeof current.value !== typeof value)
              return false
            return current.onApply(value as T) !== false
          }
        : undefined,
    }
  })
  useLayoutEffect(() => register?.(id, target), [register, id])
  return {
    targetEnabled: Boolean(
      context && options.enabled !== false && allowsAssistantTarget(context.stage, options.field),
    ),
    openAssistant: (action: AssistantAction = 'discuss') => openTarget?.(id, action),
  }
}

function targetLocation(base: AssistantLocation, target: Target): AssistantLocation {
  if (target.scopeId === undefined) return base
  const draft = target.scopeId === null && (base.stage === 'M2' || base.stage === 'M3' || base.stage === 'M4')
  return { ...base, scopeId: target.scopeId, draftKey: draft ? (target.objectKey ?? null) : null }
}

export function CreationAssistantLayout({
  stage,
  branchId,
  children,
  inline = false,
  account: defaultAccount,
  workspace,
  onContinueInWorkspace,
}: {
  stage: CreationBench
  branchId: string
  children: ReactNode
  inline?: boolean
  account?: string
  workspace?: (current: NovelAssistantReference) => ReactNode
  onContinueInWorkspace?: (current: NovelAssistantReference) => void
}) {
  const host = JSON.stringify([branchId, stage])
  const railId = useId()
  const [open, setOpen] = useState(false)
  const [openHost, setOpenHost] = useState<string>()
  const [activated, setActivated] = useState(false)
  const [revealRequest, setRevealRequest] = useState(0)
  const [registeredScope, setRegisteredScope] = useState<{ host: string; id: string; value: Scope }>()
  const [chosen, setChosen] = useState<Selection>()
  const targets = useRef(new Map<string, RefObject<Target>>())
  const origin = useRef<HTMLElement | null>(null)
  const launchId = useRef(0)
  const lastClaimed = useRef(0)
  const scope = registeredScope?.host === host ? registeredScope.value : undefined
  const account = scope?.account || defaultAccount
  const base: AssistantLocation = { branchId, stage, scopeId: scope?.scopeId ?? null, draftKey: null }
  const selection = chosen?.host === host && chosen.baseScope === base.scopeId ? chosen : undefined
  const location = selection?.location ?? base
  const locationKey = assistantLocationKey(location)
  useEffect(() => {
    setChosen((current) =>
      current?.host === host && current.baseScope === base.scopeId ? current : undefined,
    )
  }, [host, base.scopeId])

  const registerScope = useCallback((owner: string, id: string, value: Scope) => {
    setRegisteredScope({ host: owner, id, value })
    return () => {
      setRegisteredScope((current) => (current?.host === owner && current.id === id ? undefined : current))
    }
  }, [])
  const registerTarget = useCallback((id: string, binding: RefObject<Target>) => {
    targets.current.set(id, binding)
    return () => {
      if (targets.current.get(id) === binding) targets.current.delete(id)
    }
  }, [])
  function reveal() {
    const active = document.activeElement
    if (active instanceof HTMLElement && !document.getElementById(railId)?.contains(active))
      origin.current = active
    setActivated(true)
    setOpenHost(host)
    setOpen(true)
    setRevealRequest((request) => request + 1)
  }
  const select = useCallback(() => {
    origin.current = null
    setActivated(true)
    setOpenHost(host)
    setOpen(true)
  }, [host])
  const dismiss = useCallback(() => setOpen(false), [])
  function close(onReturnFocus?: () => void) {
    const previous = origin.current
    origin.current = null
    setOpen(false)
    requestAnimationFrame(() => {
      if (previous?.isConnected && previous.getClientRects().length) {
        previous.focus()
        if (document.activeElement === previous) return
      }
      if (onReturnFocus) onReturnFocus()
      else document.getElementById(railId + '-open')?.focus()
    })
  }
  function openTarget(id: string, action: AssistantAction) {
    const target = targets.current.get(id)?.current
    if (!target || target.enabled === false || !allowsAssistantTarget(stage, target.field)) return
    setChosen({
      host,
      baseScope: base.scopeId,
      id,
      location: targetLocation(base, target),
      label: target.scopeLabel || scope?.label || target.label,
      action,
      binding: { field: target.field, objectKey: target.objectKey, contextKey: target.contextKey },
      ...(action !== 'discuss' && account
        ? {
            launch: {
              id: ++launchId.current,
              intent: {
                question: target.quickQuestion || directQuestions[action],
                account,
                action,
                target: { ...target, value: Array.isArray(target.value) ? [...target.value] : target.value },
              },
            },
          }
        : {}),
    })
    reveal()
  }
  const selectionBinding = () => {
    if (!selection) return
    const target = targets.current.get(selection.id)?.current
    if (
      target &&
      target.enabled !== false &&
      target.field === selection.binding.field &&
      target.objectKey === selection.binding.objectKey &&
      target.contextKey === selection.binding.contextKey &&
      assistantLocationKey(targetLocation(base, target)) === locationKey
    )
      return target
  }
  function claimLaunch(id: number) {
    if (id <= lastClaimed.current) return false
    lastClaimed.current = id
    return true
  }
  const findTarget = (turn: AssistantTurn) =>
    [...targets.current.values()]
      .map(({ current }) => current)
      .find(
        (target) =>
          target.enabled !== false &&
          target.apply &&
          assistantLocationKey(targetLocation(base, target)) === locationKey &&
          allowsAssistantTarget(stage, target.field) &&
          assistantTurnMatchesTarget(turn, target),
      )
  const findDeliveryTarget = ({ stage: targetStage, scope_id, field }: NovelAssistantDelivery) => {
    if (targetStage !== stage) return
    const matches = [...targets.current.values()]
      .map(({ current }) => current)
      .filter((target) => {
        if (!target.apply || target.enabled === false || target.field !== field) return false
        const bound = targetLocation(base, target)
        return bound.scopeId === scope_id && !bound.draftKey
      })
    if (matches.length === 1) return matches[0]
  }
  const reference = (where: AssistantLocation, label?: string): NovelAssistantReference => ({
    stage: where.stage,
    scope_id: where.scopeId,
    scope_label: label || '',
  })
  const workbench = inline ? (
    children
  ) : (
    <div className={open ? 'wb-assistant-layout wb-assistant-layout-open' : 'wb-assistant-layout'}>
      <WorkbenchAssistantRail />
      <div className="wb-assistant-main">{children}</div>
    </div>
  )

  return (
    <AssistantContext.Provider
      value={{
        host,
        open,
        dockOpen: open && openHost === host,
        activated,
        revealRequest,
        show: reveal,
        select,
        dismiss,
        close,
        resetTarget: () => setChosen(undefined),
        railId,
        stage,
        scope,
        account,
        location,
        selection,
        registerScope,
        registerTarget,
        openTarget,
        claimLaunch,
        selectedTarget: selectionBinding,
        findTarget,
        findDeliveryTarget,
        continueInWorkspace:
          onContinueInWorkspace && !location.draftKey && scope?.workspaceEligible !== false
            ? () => {
                dismiss()
                onContinueInWorkspace(reference(location, selection?.label || scope?.label))
              }
            : undefined,
      }}
    >
      {workspace ? (
        <div className="novel-workbench-shell">
          <div className="novel-workbench-main">{workbench}</div>
          {workspace(
            scope?.workspaceEligible === false
              ? reference({ ...base, scopeId: null })
              : reference(base, scope?.label),
          )}
        </div>
      ) : (
        workbench
      )}
    </AssistantContext.Provider>
  )
}

type Attempt = {
  intent: Intent
  creationId: string
  turnId: string
  conversationId?: string
  input?: AssistantTurnInput
}

function description(error: unknown) {
  return error instanceof Error ? error.message : '本机服务暂时无法完成此操作，请稍后重试。'
}

function mergeTurns(older: AssistantTurn[], fresh: AssistantTurn[]) {
  return [...new Map([...older, ...fresh].map((turn) => [turn.id, turn])).values()].sort(
    (a, b) => a.sequence - b.sequence,
  )
}

function AssistantConversationPanel({
  context,
  onClose,
}: {
  context: AssistantContextValue
  onClose: () => void
}) {
  const { location, selection, account } = context
  const [view, setView] = useState<AssistantView>('conversation')
  const [action, setAction] = useState<AssistantAction>(selection?.action ?? 'discuss')
  const [conversations, setConversations] = useState<AssistantConversation[] | null>(null)
  const [sessionChoice, setSelectedId] = useState<string>()
  const [history, setHistory] = useState<AssistantHistory>()
  const [question, setQuestion] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [failure, setFailure] = useState('')
  const [notice, setNotice] = useState('')
  const [operation, setOperation] = useState('')
  const [retryable, setRetryable] = useState(false)
  const [applied, setApplied] = useState(new Set<string>())
  const [pendingReceipts, setPendingReceipts] = useState(new Set<string>())
  const attempt = useRef<Attempt | null>(null)
  const alive = useRef(false)
  const busy = useRef(false)
  const latestContext = useRef(context)
  latestContext.current = context
  const locationKey = assistantLocationKey(location)
  const launch = selection?.launch
  const selectedId = sessionChoice ?? ''
  const selectedHistory = history?.conversation.id === selectedId ? history : undefined
  const running = selectedHistory?.turns.some(assistantTurnPending) ?? false
  const latestTask = selectedHistory?.turns.filter(assistantTurnPending).at(-1)?.task_id
  const target = context.selectedTarget()
  const targetUnavailable = !!selection && !target
  const tabsId = useId()

  useEffect(() => {
    setView('conversation')
    setAction(selection?.action ?? 'discuss')
  }, [selection])

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  useEffect(() => {
    let active = true
    void listAssistantConversations(location)
      .then((rows) => {
        if (!active) return
        setConversations(rows)
        setSelectedId((id) => id ?? rows[0]?.id ?? '')
      })
      .catch((error) => {
        if (active) setFailure(description(error))
      })
    return () => {
      active = false
    }
  }, [locationKey, refresh])

  useEffect(() => {
    if (!selectedId) return
    let active = true
    let fetching = false
    const read = () => {
      if (fetching) return
      fetching = true
      void readAssistantHistory(location, selectedId)
        .then((next) => {
          if (!active) return
          setHistory((previous) =>
            previous?.conversation.id === selectedId
              ? { ...next, turns: mergeTurns(previous.turns, next.turns), next_before: previous.next_before }
              : next,
          )
        })
        .catch((error) => {
          if (active) setFailure(description(error))
        })
        .finally(() => {
          fetching = false
        })
    }
    read()
    const retry = latestTask ? window.setInterval(read, 1800) : undefined
    return () => {
      active = false
      window.clearInterval(retry)
    }
  }, [locationKey, selectedId, latestTask, refresh])

  async function perform(request: Attempt) {
    if (busy.current) return
    busy.current = true
    setFailure('')
    setNotice('')
    setOperation('正在提交')
    setRetryable(false)
    try {
      if (!request.conversationId) {
        const { target, question } = request.intent
        const subject = Array.from(target ? target.label + ' · ' + question.trim() : question.trim())
          .slice(0, 48)
          .join('')
        const created = await createAssistantConversation(location, subject, request.creationId)
        request.conversationId = created.id
        if (!alive.current) return
        setConversations((previous) => [created, ...(previous ?? []).filter(({ id }) => id !== created.id)])
        setSelectedId(created.id)
      }
      if (!request.input) {
        const frozen =
          selectedHistory?.conversation.id === request.conversationId
            ? selectedHistory
            : await readAssistantHistory(location, request.conversationId)
        if (!alive.current) return
        if (!frozen.basis)
          throw new ApiError('assistant_basis', '该会话的原目标已不存在，可以查阅历史。', 409)
        const { target, action, account, question } = request.intent
        request.input = {
          base_revision: frozen.conversation.revision,
          basis_sha256: frozen.basis.sha256,
          question,
          account,
          client_request_id: request.turnId,
          action,
          ...(target
            ? {
                ...target.request,
                target: target.field,
                target_key: target.objectKey,
                context_key: target.contextKey,
                draft_text: assistantDraftText(target.value),
              }
            : {}),
        }
      }
      const accepted = await postAssistantTurn(location, request.conversationId, request.input)
      if (!alive.current) return
      attempt.current = null
      setSelectedId(accepted.conversation.id)
      setQuestion((current) => (current === request.intent.question ? '' : current))
      setRefresh((version) => version + 1)
    } catch (error) {
      if (!alive.current) return
      setFailure(description(error))
      const rejected =
        error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408
      if (rejected) {
        attempt.current = null
        setQuestion((current) => current || request.intent.question)
        setRefresh((version) => version + 1)
      } else {
        setRetryable(true)
      }
    } finally {
      busy.current = false
      if (alive.current) setOperation('')
    }
  }
  function send(intent: Intent) {
    if (busy.current || attempt.current) return
    const request = {
      intent,
      creationId: crypto.randomUUID(),
      turnId: crypto.randomUUID(),
      conversationId: selectedId || undefined,
    }
    attempt.current = request
    void perform(request)
  }

  useEffect(() => {
    if (!launch || !conversations || (selectedId && !selectedHistory) || !context.claimLaunch(launch.id))
      return
    if (!context.selectedTarget()) {
      setNotice('所选目标已变化，请重新选择工作台字段。')
      return
    }
    if (running || busy.current || attempt.current) {
      setQuestion((current) => current || launch.intent.question)
      setNotice('会话已有待处理请求，请待其结束后发送。')
      return
    }
    send(launch.intent)
  }, [launch, conversations, selectedId, selectedHistory, running])

  async function loadEarlier() {
    const before = selectedHistory?.next_before
    if (busy.current || before == null) return
    busy.current = true
    setOperation('正在读取较早消息')
    setFailure('')
    try {
      const next = await readAssistantHistory(location, selectedId, before)
      if (alive.current)
        setHistory((current) =>
          current?.conversation.id === selectedId
            ? { ...current, turns: mergeTurns(next.turns, current.turns), next_before: next.next_before }
            : current,
        )
    } catch (error) {
      if (alive.current) setFailure(description(error))
    } finally {
      busy.current = false
      if (alive.current) setOperation('')
    }
  }
  async function cancel(turn: AssistantTurn) {
    if (busy.current || !turn.task_id) return
    busy.current = true
    setOperation('正在请求取消')
    setFailure('')
    try {
      await cancelAssistantTask(turn.task_id)
      if (alive.current) {
        setNotice('已提交取消请求，等待服务确认结果。')
        setRefresh((version) => version + 1)
      }
    } catch (error) {
      if (alive.current) setFailure(description(error))
    } finally {
      busy.current = false
      if (alive.current) setOperation('')
    }
  }
  async function saveReceipt(turn: AssistantTurn, field: string) {
    const key = turn.id + ':' + field
    setPendingReceipts((ids) => new Set([...ids, key]))
    try {
      await recordAssistantDraftApplication(location, selectedId, turn.id, field)
      if (!alive.current) return
      setPendingReceipts((ids) => new Set([...ids].filter((id) => id !== key)))
      setRefresh((version) => version + 1)
      setNotice('建议已写入当前草稿，请按工作台原有流程核对并保存。')
    } catch (error) {
      if (alive.current) setFailure('建议已写入草稿，采用记录未能保存。' + description(error))
    }
  }
  async function apply(turn: AssistantTurn, suggestion: AssistantSuggestion, receiptOnly = false) {
    if (busy.current) return
    busy.current = true
    setOperation('正在核对采用依据')
    setFailure('')
    try {
      if (receiptOnly) {
        await saveReceipt(turn, suggestion.field)
        return
      }
      const latest = await readAssistantHistory(location, selectedId)
      if (!alive.current) return
      const current = latestContext.current
      const currentTarget =
        current.selection?.id === context.selection?.id ? current.findTarget(turn) : undefined
      if (
        !currentTarget ||
        !canApplyAssistantSuggestion(turn, suggestion, currentTarget, latest.basis?.sha256)
      )
        throw new Error('该字段、目标或正式依据已有变化，请按当前内容重新请求建议。')
      if (!currentTarget.apply?.(suggestion.value)) throw new Error('字段或选区已改变，请重新选择后再试。')
      setApplied((ids) => new Set([...ids, turn.id + ':' + suggestion.field]))
      await saveReceipt(turn, suggestion.field)
    } catch (error) {
      if (alive.current) setFailure(description(error))
    } finally {
      busy.current = false
      if (alive.current) setOperation('')
    }
  }

  return (
    <>
      <WorkbenchTabs
        id={tabsId}
        label="助手视图"
        items={assistantTabs}
        value={view}
        onChange={setView}
        className="wb-assistant-actions wb-assistant-tabs"
      />
      {context.continueInWorkspace && view === 'conversation' && (
        <button type="button" onClick={context.continueInWorkspace}>
          用此对象继续作品对话
        </button>
      )}
      <section
        role="tabpanel"
        id={tabsId + '-panel-history'}
        aria-labelledby={tabsId + '-history'}
        hidden={view !== 'history'}
      >
        <button
          type="button"
          disabled={!!operation || retryable || !conversations}
          onClick={() => {
            setSelectedId('')
            setHistory(undefined)
            setNotice('')
            setFailure('')
            setView('conversation')
            document.getElementById(tabsId + '-conversation')?.focus()
          }}
        >
          新对话
        </button>
        {conversations?.length === 0 && <p>这个范围还没有会话。</p>}
        <ul className="wb-assistant-turns">
          {conversations?.map(({ id, subject }) => (
            <li className="wb-assistant-turn" key={id}>
              <button
                type="button"
                disabled={!!operation || retryable}
                onClick={() => {
                  setSelectedId(id)
                  if (id !== selectedId) setHistory(undefined)
                  setNotice('')
                  setFailure('')
                  setView('conversation')
                  document.getElementById(tabsId + '-conversation')?.focus()
                }}
              >
                {subject}
              </button>
            </li>
          ))}
        </ul>
      </section>
      <section
        role="tabpanel"
        id={tabsId + '-panel-tools'}
        aria-labelledby={tabsId + '-tools'}
        hidden={view !== 'tools'}
      >
        <button type="button" onClick={onClose}>
          返回本台工具
        </button>
      </section>
      <section
        role="tabpanel"
        id={tabsId + '-panel-conversation'}
        aria-labelledby={tabsId + '-conversation'}
        hidden={view !== 'conversation'}
      >
        {selection && (
          <p className="wb-assistant-muted">
            当前目标 {selection.label}
            {target && selection.label !== target.label ? ' · ' + target.label : ''}
          </p>
        )}
        {targetUnavailable && <p className="wb-assistant-muted">所选目标已变化，请重新选择工作台字段。</p>}
        {selection && (
          <button type="button" disabled={!!operation || retryable} onClick={context.resetTarget}>
            讨论本台全局
          </button>
        )}
        {selectedHistory && !selectedHistory.basis && (
          <p className="wb-assistant-muted">原目标已不存在，仍可查看会话历史。</p>
        )}
        {selectedHistory?.basis?.versions.length ? (
          <details className="wb-assistant-basis">
            <summary>本轮可用依据</summary>
            <ul>
              {selectedHistory.basis.versions.map(({ kind, id, revision, label }) => (
                <li key={kind + id + revision}>
                  {label} <code>{id}</code>
                  {revision != null && <> · {revision}</>}
                </li>
              ))}
            </ul>
          </details>
        ) : null}
        {!!selectedHistory?.basis?.checks.length && (
          <details className="wb-assistant-basis">
            <summary>{context.stage === 'M6' ? '查看本次检查项' : '查看完本判定'}</summary>
            <ul>
              {selectedHistory.basis.checks.map(({ id, title, status, detail }) => (
                <li key={id}>
                  <strong>
                    {id} {title}
                  </strong>{' '}
                  · {status}
                  {detail && <p>{detail}</p>}
                </li>
              ))}
            </ul>
          </details>
        )}
        {selectedHistory?.next_before != null && (
          <button type="button" disabled={!!operation} onClick={() => void loadEarlier()}>
            读取较早消息
          </button>
        )}
        <ol className="wb-assistant-turns" aria-label="对话记录">
          {selectedHistory?.turns.map((turn) => (
            <li className="wb-assistant-turn" key={turn.id}>
              <span className="wb-assistant-muted">{assistantActions[turn.action]}</span>
              <p className="wb-assistant-text">{turn.question}</p>
              {turn.result ? (
                <>
                  <p className="wb-assistant-text">{turn.result.answer}</p>
                  {!turn.basis_current && <p className="wb-assistant-muted">本轮使用历史依据。</p>}
                  {!!turn.result.citations.length && (
                    <details className="wb-assistant-basis">
                      <summary>查看引用依据</summary>
                      <ul>
                        {turn.result.citations.map((source) => (
                          <li key={source.kind + source.id}>
                            {context.scope?.onSource ? (
                              <button type="button" onClick={() => context.scope?.onSource?.(source)}>
                                {source.label}
                              </button>
                            ) : (
                              source.label
                            )}{' '}
                            <code>{source.id}</code>
                            {source.revision != null && <> · {source.revision}</>}
                            {source.excerpt && (
                              <blockquote className="wb-assistant-text">{source.excerpt}</blockquote>
                            )}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                  {turn.result.suggestions.map((suggestion, index) => {
                    const target = context.findTarget(turn)
                    const key = turn.id + ':' + suggestion.field
                    const written = applied.has(key) || turn.applied_fields.includes(suggestion.field)
                    const receiptPending =
                      pendingReceipts.has(key) && !turn.applied_fields.includes(suggestion.field)
                    const canApply =
                      target &&
                      canApplyAssistantSuggestion(turn, suggestion, target, selectedHistory.basis?.sha256)
                    return (
                      <details className="wb-assistant-comparison" key={index}>
                        <summary>比较建议</summary>
                        <dl>
                          <dt>请求时的草稿</dt>
                          <dd className="wb-assistant-text">{turn.draft_text || '空白'}</dd>
                          <dt>建议稿</dt>
                          <dd className="wb-assistant-text">
                            {Array.isArray(suggestion.value) ? suggestion.value.join('、') : suggestion.value}
                          </dd>
                        </dl>
                        {suggestion.reason && <p className="wb-assistant-text">{suggestion.reason}</p>}
                        <button
                          type="button"
                          disabled={!canApply || !!operation || written}
                          onClick={() => void apply(turn, suggestion)}
                        >
                          写入当前草稿
                        </button>
                        {written ? (
                          <p className="wb-assistant-muted">
                            {receiptPending ? '已写入草稿，待补记' : '已写入草稿'}
                          </p>
                        ) : (
                          !canApply && (
                            <p className="wb-assistant-muted">请选择原字段并核对当前草稿与依据。</p>
                          )
                        )}
                        {receiptPending && (
                          <button
                            type="button"
                            disabled={!!operation}
                            onClick={() => void apply(turn, suggestion, true)}
                          >
                            补记草稿采用
                          </button>
                        )}
                      </details>
                    )
                  })}
                </>
              ) : (
                <p role={assistantTurnPending(turn) ? 'status' : undefined}>
                  {turn.status === 'failed' ? '生成失败' : taskLabel(turn.status)}
                  {turn.error && ' · ' + turn.error}
                </p>
              )}
              {assistantTurnPending(turn) && turn.task_id && (
                <button type="button" disabled={!!operation} onClick={() => void cancel(turn)}>
                  取消生成
                </button>
              )}
              {['failed', 'blocked_runtime', 'cancelled'].includes(turn.status) && (
                <button
                  type="button"
                  disabled={!!operation || running || retryable}
                  onClick={() => {
                    setQuestion(turn.question)
                    setNotice('已取回问题，请核对当前目标后发送。')
                  }}
                >
                  取回问题
                </button>
              )}
            </li>
          ))}
        </ol>
        <form
          onSubmit={(event) => {
            event.preventDefault()
            const currentTarget = context.selectedTarget()
            if (
              !question.trim() ||
              !account ||
              !conversations ||
              running ||
              (selection && !currentTarget) ||
              (selectedId && !selectedHistory?.basis)
            )
              return
            send({
              question,
              account,
              action: currentTarget ? action : 'discuss',
              target: currentTarget,
            })
          }}
        >
          {target && (
            <label>
              本轮操作
              <select
                value={action}
                onChange={(event) => {
                  const selected = event.target.value
                  if (
                    selected === 'discuss' ||
                    selected === 'suggest' ||
                    selected === 'refine' ||
                    selected === 'review'
                  )
                    setAction(selected)
                }}
              >
                {Object.entries(assistantActions).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            创作问题
            <textarea
              rows={3}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              maxLength={6000}
            />
          </label>
          {!account && <p className="wb-assistant-muted">请选择已配置的模型账户。</p>}
          <div className="wb-assistant-actions">
            <button
              className="primary"
              type="submit"
              disabled={
                !account ||
                !conversations ||
                !question.trim() ||
                !!operation ||
                running ||
                retryable ||
                targetUnavailable ||
                (!!selectedId && !selectedHistory?.basis)
              }
            >
              发送
            </button>
            <button
              type="button"
              disabled={!!operation}
              onClick={() => {
                setFailure('')
                setRefresh((version) => version + 1)
              }}
            >
              刷新历史
            </button>
            {retryable && (
              <button
                type="button"
                disabled={!!operation}
                onClick={() => {
                  if (attempt.current) void perform(attempt.current)
                }}
              >
                重试原请求
              </button>
            )}
          </div>
        </form>
      </section>
      {!conversations && !failure && <p role="status">正在读取会话历史…</p>}
      {operation && <p role="status">{operation}…</p>}
      {notice && <p role="status">{notice}</p>}
      {failure && <p role="alert">{failure}</p>}
    </>
  )
}

export function WorkbenchAssistantRail({
  embedded,
  docked = false,
  onReturnFocus,
}: {
  embedded?: boolean
  docked?: boolean
  onReturnFocus?: () => void
} = {}) {
  const context = useContext(AssistantContext)
  const heading = useRef<HTMLHeadingElement>(null)
  const open = docked ? context?.dockOpen : context?.open
  const revealRequest = context?.revealRequest ?? 0
  const focusedReveal = useRef(revealRequest)
  const locationKey = context && assistantLocationKey(context.location)
  useEffect(() => {
    const explicitlyRevealed = revealRequest !== focusedReveal.current
    focusedReveal.current = revealRequest
    if (open && (!docked || explicitlyRevealed)) heading.current?.focus()
  }, [open, docked, revealRequest, locationKey])
  if (!context) return null
  const Region = (embedded ?? context.stage === 'M5') ? 'div' : 'aside'
  const title = specialists[context.stage]
  const close = () => context.close(onReturnFocus)
  return (
    <>
      {!docked && (
        <div className="wb-assistant-entry" hidden={open}>
          <button
            type="button"
            id={context.railId + '-open'}
            aria-expanded={false}
            aria-controls={context.railId}
            onClick={context.show}
          >
            {title}
          </button>
        </div>
      )}
      <Region
        className="wb-assistant-rail"
        role={Region === 'div' ? 'region' : undefined}
        hidden={!open}
        id={context.railId}
        aria-labelledby={context.railId + '-heading'}
        onKeyDown={(event) => {
          if (event.key === 'Escape' && !event.nativeEvent.isComposing) {
            event.stopPropagation()
            close()
          }
        }}
      >
        <header className="wb-assistant-header">
          <h2 id={context.railId + '-heading'} ref={heading} tabIndex={-1}>
            {title}
          </h2>
          {!docked && (
            <button type="button" onClick={close}>
              收起助手
            </button>
          )}
        </header>
        {context.activated && (
          <AssistantConversationPanel key={locationKey} context={context} onClose={close} />
        )}
      </Region>
    </>
  )
}
