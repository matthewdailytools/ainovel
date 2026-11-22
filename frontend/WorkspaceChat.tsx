import { useEffect, useRef, useState } from 'react'
import { api, label, type Branch, type Chapter } from './api'
import type { components } from './api-schema'
import type { Notify } from './App'
import { useLeaveGuard } from './leaveGuard'
import type { SettingKind } from './workspaceSettingsKinds'

type Turn = components['schemas']['WorkspaceChatTurn']
type Conversation = components['schemas']['WorkspaceChatConversation']

type Props = {
  branch: Branch
  chapter?: Chapter
  setting?: SettingKind
  account: string
  modelReady: boolean
  notify: Notify
  draftDirty?: boolean
  refreshKey?: number
}

const active = (turn: Turn) => turn.status === 'queued' || turn.status === 'running'

export function WorkspaceChat(props: Props) {
  const target = props.setting || props.chapter?.id
  if (!target) return null
  return <ChatConversation key={props.branch.id + ':' + target} {...props} />
}

function ChatConversation({
  branch,
  chapter,
  setting,
  account,
  modelReady,
  notify,
  draftDirty,
  refreshKey,
}: Props) {
  const path = `/branches/${branch.id}/workspace-chat`
  const target: Record<string, string> = setting
    ? { setting_kind: setting }
    : { chapter_id: chapter?.id || '' }
  const url = `${path}?${new URLSearchParams(target)}`
  const [data, setData] = useState<Conversation>()
  const [draft, setDraft] = useState('')
  const [length, setLength] = useState<'concise' | 'standard'>('standard')
  const [requesting, setRequesting] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const submitted = useRef<{ signature: string; key: string } | null>(null)
  const transcript = useRef<HTMLOListElement>(null)
  const followLatest = useRef(true)
  const busy = useRef(false)
  const pending = data?.turns.find(active)
  const lastTurn = data?.turns.at(-1)
  const changed = !setting && data?.source.chapter_revision !== chapter?.revision
  useLeaveGuard(!!draft.trim(), '尚未发送的 AI 对话', 'workspace-chat')

  useEffect(() => {
    let current = true
    let timer: ReturnType<typeof setTimeout> | undefined
    setLoading(true)
    async function read() {
      try {
        const state = await api<Conversation>(url)
        if (current) {
          setData(state)
          if (state.turns.some(active)) timer = setTimeout(() => void read(), 1200)
        }
      } catch (failure) {
        if (current) setError(failure instanceof Error ? failure.message : '对话读取失败。')
      } finally {
        if (current) setLoading(false)
      }
    }
    void read()
    return () => {
      current = false
      clearTimeout(timer)
    }
  }, [url, chapter?.revision, chapter?.title, branch.story_revision, refreshKey, reload])

  useEffect(() => {
    const list = transcript.current
    if (list && followLatest.current) list.scrollTop = list.scrollHeight
  }, [lastTurn?.id, lastTurn?.status])

  async function send(event: React.FormEvent) {
    event.preventDefault()
    const question = draft.trim()
    if (!data || !question || busy.current || pending || changed || draftDirty || loading || !modelReady)
      return
    const basis = setting
      ? { base_artifact_id: data.source.artifact_id }
      : { base_revision: chapter?.revision }
    const body = { ...target, ...basis, account, question, reply_length: length }
    const signature = JSON.stringify(body)
    if (submitted.current?.signature !== signature)
      submitted.current = { signature, key: crypto.randomUUID() }
    busy.current = true
    followLatest.current = true
    setRequesting(true)
    setError('')
    try {
      const turn = await api<Turn>(path, 'POST', { ...body, client_request_id: submitted.current.key })
      setData({ ...data, turns: [...data.turns.filter((row) => row.id !== turn.id), turn] })
      setDraft('')
      submitted.current = null
      setReload((value) => value + 1)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '提交失败，问题已保留。')
    } finally {
      busy.current = false
      setRequesting(false)
    }
  }

  async function cancel(turn: Turn) {
    if (busy.current) return
    busy.current = true
    setRequesting(true)
    try {
      await api(`/tasks/${turn.id}/cancel`, 'POST')
      setReload((value) => value + 1)
    } catch (failure) {
      notify(failure)
    } finally {
      busy.current = false
      setRequesting(false)
    }
  }

  return (
    <section aria-label="AI 对话" className="workspace-chat">
      <h2>AI 对话</h2>
      {data && <p className="small muted">{data.source.title}</p>}
      {loading && <p role="status">正在读取对话…</p>}
      {error && (
        <div className="notice">
          <p role="alert">{error}</p>
          <button
            type="button"
            disabled={loading}
            onClick={() => {
              setError('')
              setReload((value) => value + 1)
            }}
          >
            重新读取对话
          </button>
        </div>
      )}
      {!!data?.turns.length && (
        <ol
          className="workspace-chat-turns"
          aria-label="最近对话"
          ref={transcript}
          onScroll={({ currentTarget: list }) => {
            followLatest.current = list.scrollHeight - list.scrollTop - list.clientHeight <= 24
          }}
        >
          {data.turns.map((turn) => (
            <li className="fact" key={turn.id}>
              <strong>你</strong>
              <div className="workspace-chat-text">{turn.question}</div>
              {turn.source.sha256 !== data.source.sha256 ||
              turn.source.title !== data.source.title ||
              turn.source.artifact_id !== data.source.artifact_id ||
              turn.source.chapter_revision !== data.source.chapter_revision ? (
                <p className="small muted">基于旧保存版本</p>
              ) : null}
              {turn.reply ? (
                <div>
                  <strong>AI</strong>
                  <div className="workspace-chat-text">{turn.reply}</div>
                </div>
              ) : (
                <p role="status">
                  {label(turn.status)}
                  {turn.error && ` · ${turn.error}`}
                </p>
              )}
              {active(turn) && (
                <button type="button" disabled={requesting} onClick={() => void cancel(turn)}>
                  取消本轮
                </button>
              )}
            </li>
          ))}
        </ol>
      )}
      <form onSubmit={(event) => void send(event)}>
        <label>
          对话问题
          <textarea
            rows={3}
            value={draft}
            maxLength={4000}
            onChange={(event) => setDraft(event.target.value)}
            disabled={requesting}
          />
        </label>
        {!modelReady && <p className="small muted">请先配置模型账户及单价。</p>}
        {draftDirty && <p className="small muted">请先保存当前正文，再用新修订发起对话。</p>}
        <button
          className="primary"
          type="submit"
          disabled={
            !modelReady ||
            !draft.trim() ||
            !!pending ||
            draftDirty ||
            changed ||
            loading ||
            requesting ||
            !data
          }
        >
          {requesting ? '处理中…' : '发送问题'}
        </button>
        <details>
          <summary>对话设置与发送范围</summary>
          <label>
            回复长度
            <select
              value={length}
              onChange={(event) => setLength(event.target.value === 'concise' ? 'concise' : 'standard')}
              disabled={requesting}
            >
              <option value="standard">标准</option>
              <option value="concise">简短</option>
            </select>
          </label>
          <p className="small muted">模型账户 {account} · 使用共享预算。</p>
          {data && (
            <p className="small muted">
              {setting && !data.source.artifact_id ? (
                '当前类别尚无已采用的设定。本轮发送本次问题'
              ) : (
                <>
                  发送本次问题、
                  {setting ? '当前类别已采用的设定' : `当前章保存稿（修订 ${data.source.chapter_revision}）`}
                  {data.source.sent_chars < data.source.chars &&
                    `的首尾节录（共 ${data.source.sent_chars} 字符）`}
                </>
              )}
              ，以及同一保存版本最近四轮对话的节录。
            </p>
          )}
        </details>
      </form>
    </section>
  )
}
