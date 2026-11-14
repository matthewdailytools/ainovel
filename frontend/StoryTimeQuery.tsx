import { useEffect, useRef, useState } from 'react'
import { api, type Chapter } from './api'
import type { Notify } from './App'
import type { StoryRow } from './storyGraphTypes'

type TimeRow = StoryRow & {
  time_reason?: string
  time_basis?: { start_path: string[]; until_path: string[] }
}
type TimeResult = {
  source_story_revision: number
  states: TimeRow[]
  uncertain: TimeRow[]
  conflicts: TimeRow[][]
  coverage: string
  anchor?: {
    title: string
    event_id: string
    past_event_ids: string[]
    unusable_predecessors: number
    source_scope_excluded: number
  }
}

export function StoryTimeQuery({
  branchId,
  storyRevision,
  records,
  chapters,
  onSelect,
  notify,
  disabled = false,
}: {
  branchId: string
  storyRevision: number
  records: StoryRow[]
  chapters: Pick<Chapter, 'id' | 'title' | 'position' | 'revision'>[]
  onSelect: (row: StoryRow) => void
  notify: Notify
  disabled?: boolean
}) {
  const [mode, setMode] = useState('number')
  const [order, setOrder] = useState('')
  const [anchor, setAnchor] = useState('')
  const [character, setCharacter] = useState('')
  const [audience, setAudience] = useState('character')
  const [chapter, setChapter] = useState('')
  const [position, setPosition] = useState('0')
  const [positionRevision, setPositionRevision] = useState<number>()
  const [boundarySource, setBoundarySource] = useState<Chapter>()
  const [picked, setPicked] = useState<{ chapter: string; revision: number; position: number }>()
  const [result, setResult] = useState<{ generation: number; data: TimeResult }>()
  const [pending, setPending] = useState<number>()
  const [message, setMessage] = useState<{ generation: number; text: string }>()
  const current = useRef({ key: '', generation: 0 })
  const alive = useRef(true)
  const inFlight = useRef<number | undefined>(undefined)
  const boundary = chapters.find((row) => row.id === chapter)
  const source =
    boundarySource?.id === chapter && boundarySource.revision === boundary?.revision
      ? boundarySource
      : undefined
  const selectedPoint =
    picked?.chapter === source?.id && picked?.revision === source?.revision ? picked?.position : undefined
  const key = JSON.stringify({
    branchId,
    storyRevision,
    mode,
    order,
    anchor,
    character,
    audience,
    chapter,
    position,
    positionRevision,
    boundaryRevision: boundary?.revision,
  })
  if (key !== current.current.key) current.current = { key, generation: current.current.generation + 1 }
  const generation = current.current.generation
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])
  const active = records.filter((row) => !row.archived)
  const characters = active.filter((row) => row.kind === 'character')
  const events = active.filter(
    (row) => row.payload.kind === 'event' && row.payload.status === 'actual' && !row.invalid_reasons.length,
  )
  const indexed = new Map(active.map((row) => [row.id, row]))
  const data = result?.generation === generation ? result.data : undefined
  const working = pending === generation
  async function perform(action: () => Promise<void>) {
    if (working || disabled || inFlight.current === generation) return
    const token = generation
    inFlight.current = token
    setPending(token)
    setMessage(undefined)
    try {
      await action()
    } catch (error) {
      if (alive.current && current.current.generation === token) {
        setMessage({
          generation: token,
          text: error instanceof Error ? error.message : '查询失败，条件保留。',
        })
        notify(error)
      }
    } finally {
      if (inFlight.current === token) inFlight.current = undefined
      if (alive.current) setPending((value) => (value === token ? undefined : value))
    }
  }
  async function query() {
    const token = generation
    const q = new URLSearchParams({ audience, base_story_revision: String(storyRevision) })
    if (mode === 'event') q.set('at_event_id', anchor)
    else if (order !== '') {
      if (!Number.isSafeInteger(Number(order))) throw new Error('故事顺序请填写整数或留空。')
      q.set('story_order', order)
    }
    if (character) q.set('character_id', character)
    if (chapter) {
      if (!boundary || positionRevision !== boundary.revision)
        throw new Error('原文边界版本已变化，请重新读至章末或选择原文；查询条件保留。')
      if (position === '' || !Number.isSafeInteger(Number(position)) || Number(position) < 0)
        throw new Error('原文位置请填写非负整数；也可读取保存稿章末位置。')
      q.set('chapter_id', chapter)
      q.set('text_position', position)
      q.set('chapter_revision', String(positionRevision))
    }
    const next = await api<TimeResult>(`/branches/${branchId}/story-context?${q}`)
    if (!alive.current || current.current.generation !== token) return
    if (next.source_story_revision !== storyRevision)
      throw new Error('查询版本与当前故事记录不符，请重新读取记录后再查询，条件保留。')
    setResult({ generation: token, data: next })
  }
  async function readBoundary(toEnd: boolean) {
    const token = generation
    const saved = await api<Chapter>(`/chapters/${chapter}`)
    if (!alive.current || current.current.generation !== token) return
    if (saved.id !== chapter || saved.branch_id !== branchId || saved.revision !== boundary?.revision)
      throw new Error('边界章节已变化，请重新读取长篇记录后再定位；查询条件保留。')
    setBoundarySource(saved)
    setPicked(undefined)
    if (toEnd) {
      setPosition(String(saved.content.length))
      setPositionRevision(saved.revision)
    }
  }
  const path = (ids: string[]) => (
    <span>
      {ids.map((id, i) => (
        <span key={id}>
          {i > 0 && ' → '}
          <button
            className="text-button"
            type="button"
            disabled={disabled}
            onClick={() => {
              const row = indexed.get(id)
              if (row) onSelect(row)
            }}
          >
            {indexed.get(id)?.payload.title || '关联事件已不在当前列表'}
          </button>
        </span>
      ))}
    </span>
  )
  return (
    <details>
      <summary>查询人物在故事时点知道什么、具有什么</summary>
      <div className="form-grid">
        <label>
          查询人物
          <select value={character} onChange={(e) => setCharacter(e.target.value)}>
            <option value="">请选择／未知</option>
            {characters.map((row) => (
              <option key={row.id} value={row.id}>
                {row.payload.title}
                {row.invalid_reasons.length ? '（失效）' : ''}
              </option>
            ))}
          </select>
        </label>
        <label>
          查询时间依据
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="number">故事顺序编号</option>
            <option value="event">所选实际事件（相对先后）</option>
          </select>
        </label>
        {mode === 'number' ? (
          <label>
            查询故事顺序
            <input type="number" step="1" value={order} onChange={(e) => setOrder(e.target.value)} />
          </label>
        ) : (
          <label>
            查询实际事件
            <select value={anchor} onChange={(e) => setAnchor(e.target.value)}>
              <option value="">请选择实际事件</option>
              {events.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.payload.title}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          查询信息视角
          <select value={audience} onChange={(e) => setAudience(e.target.value)}>
            <option value="character">人物</option>
            <option value="author">作者</option>
            <option value="reader">读者</option>
          </select>
        </label>
        <label>
          查询原文边界
          <select
            value={chapter}
            onChange={(e) => {
              setChapter(e.target.value)
              setPosition('0')
              setPositionRevision(chapters.find((row) => row.id === e.target.value)?.revision)
              setBoundarySource(undefined)
              setPicked(undefined)
            }}
          >
            <option value="">全部已保存原文</option>
            {chapters.map((row) => (
              <option key={row.id} value={row.id}>
                {row.position} · {row.title}
              </option>
            ))}
          </select>
        </label>
      </div>
      {mode === 'event' && (
        <p className="small">
          按已维护的有效实际事件先后依赖查询，包含所选事件本身；无需补造整数编号。并行、缺证及失效边界继续待判断，不按章节顺序猜测。
        </p>
      )}
      {chapter && (
        <>
          <p className="small">
            原文边界：
            {position === '0'
              ? '本章开头'
              : source && Number(position) === source.content.length
                ? '本章末尾'
                : '本章选定位置'}
            。
          </p>
          <button
            type="button"
            disabled={working || disabled}
            onClick={() => void perform(() => readBoundary(true))}
          >
            读至本章末尾
          </button>
          <details>
            <summary>精确选择章内原文（可选）</summary>
            <button
              type="button"
              disabled={working || disabled}
              onClick={() => void perform(() => readBoundary(false))}
            >
              读取边界原文（只读）
            </button>
            {source && (
              <>
                <label>
                  查询边界原文
                  <textarea
                    readOnly
                    rows={8}
                    value={source.content}
                    onSelect={(event) =>
                      setPicked({
                        chapter: source.id,
                        revision: source.revision,
                        position: event.currentTarget.selectionEnd,
                      })
                    }
                  />
                </label>
                <button
                  type="button"
                  disabled={working || disabled || selectedPoint === undefined}
                  onClick={() => {
                    if (source && selectedPoint !== undefined) {
                      setPosition(String(selectedPoint))
                      setPositionRevision(source.revision)
                    }
                  }}
                >
                  读至选区末尾
                </button>
              </>
            )}
            <label>
              原文偏移（UTF-16，供核对）
              <input
                type="number"
                min="0"
                step="1"
                value={position}
                onChange={(e) => {
                  setPosition(e.target.value)
                  setPositionRevision(boundary?.revision)
                }}
              />
            </label>
          </details>
        </>
      )}
      <button
        type="button"
        disabled={working || disabled || (mode === 'event' && !anchor)}
        onClick={() => void perform(query)}
      >
        {working ? '正在读取状态…' : '查询有效状态'}
      </button>
      {message?.generation === generation && <p role="alert">{message.text}</p>}
      {data && (
        <section aria-label="人物时点查询结果">
          <p>{data.coverage}</p>
          {data.anchor && (
            <p>
              所选实际事件：{data.anchor.title}；可确认事件 {data.anchor.past_event_ids.length}
              {data.anchor.unusable_predecessors > 0 &&
                `；不可用前置关联 ${data.anchor.unusable_predecessors}，未借其推断先后`}
              {data.anchor.source_scope_excluded > 0 &&
                `；部分来源超出原文边界 ${data.anchor.source_scope_excluded} 项，相关记录不用于当前推断`}
            </p>
          )}
          <p>
            有效状态 {data.states.length}；冲突组 {data.conflicts.length}；未知或待判断{' '}
            {data.uncertain.length}
          </p>
          {[...data.states, ...data.uncertain].map((row) => (
            <article key={row.id}>
              <button type="button" disabled={disabled} onClick={() => onSelect(row)}>
                {row.payload.title} · {row.payload.kind === 'state' ? row.payload.value : ''}
              </button>
              {row.time_reason && <p className="small">{row.time_reason}</p>}
              {row.time_basis && (
                <div className="small">
                  <p>获得／变化事件至查询事件：{path(row.time_basis.start_path)}</p>
                  {!!row.time_basis.until_path.length && (
                    <p>查询事件至失效事件：{path(row.time_basis.until_path)}</p>
                  )}
                </div>
              )}
            </article>
          ))}
        </section>
      )}
    </details>
  )
}
