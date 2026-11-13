import { useEffect, useState } from 'react'
import { api, type Chapter } from './api'
import type { components } from './api-schema'
import type { Notify } from './App'
import { StoryExtraction } from './StoryExtraction'

type Schemas = components['schemas']
type Payload =
  Schemas['Character'] | Schemas['Event'] | Schemas['State'] | Schemas['Promise'] | Schemas['Relationship']
type Row = {
  id: string
  kind: Payload['kind']
  revision: number
  payload: Payload
  archived: number
  invalid_reasons: string[]
}
type Graph = { story_revision: number; records: Row[]; chapters: Chapter[]; coverage: string }
type Source = Schemas['Source']
type Choice = { id: string; payload: { title: string }; invalid_reasons?: string[] }
const names = {
  character: '人物身份',
  event: '事件',
  state: '时点状态',
  promise: '伏笔与承诺',
  relationship: '人物关系',
}
const statusNames: Record<string, string> = {
  planned: '计划',
  actual: '实际',
  unknown: '未知',
  conflict: '冲突',
  planted: '埋设',
  reinforced: '强化',
  resolved: '回收',
  retained: '保留',
  abandoned: '放弃',
}
function fresh(kind: Payload['kind']): Payload {
  const common = { title: '', notes: '', volume: '', sources: [], depends_on: [] }
  if (kind === 'character') return { ...common, kind, aliases: [], profile: '' }
  if (kind === 'event')
    return {
      ...common,
      kind,
      thread: '主线',
      story_order: null,
      story_time: '未知',
      status: 'planned',
      participants: [],
      after: [],
    }
  if (kind === 'state')
    return {
      ...common,
      kind,
      character_id: '',
      event_id: '',
      category: 'knowledge',
      key: '',
      value: '',
      audience: 'character',
      truth: 'unknown',
      until_event_id: null,
    }
  if (kind === 'relationship')
    return { ...common, kind, from_id: '', to_id: '', relation: '', event_id: null, truth: 'planned' }
  return {
    ...common,
    kind,
    thread: '主线',
    stages: [{ status: 'planned', event_id: null, sources: [], note: '' }],
  }
}

export function StoryGraph({
  branchId,
  notify,
  onSaved,
  account,
  modelReady,
}: {
  branchId: string
  notify: Notify
  onSaved: () => Promise<void>
  account: string
  modelReady: boolean
}) {
  const [graph, setGraph] = useState<Graph>()
  const [draft, setDraft] = useState<Payload>(fresh('character'))
  const [editing, setEditing] = useState<Row>()
  const [base, setBase] = useState(0)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [filter, setFilter] = useState('')
  const [volume, setVolume] = useState('')
  const [view, setView] = useState('lanes')
  const [chapterId, setChapterId] = useState('')
  const [sourceChapter, setSourceChapter] = useState<Chapter & { content: string }>()
  const [selection, setSelection] = useState<Source>()
  const [history, setHistory] = useState<{ revision: number; payload: Payload }[]>([])
  const [order, setOrder] = useState('')
  const [stageStatus, setStageStatus] = useState<Schemas['PromiseStage']['status']>('planted')
  const [stageEvent, setStageEvent] = useState('')
  const [stageNote, setStageNote] = useState('')
  const [audience, setAudience] = useState<'author' | 'character' | 'reader'>('character')
  const [queriedCharacter, setQueriedCharacter] = useState('')
  const [sourceView, setSourceView] = useState<Source>()
  const [timeResult, setTimeResult] = useState<{
    states: Row[]
    uncertain: Row[]
    conflicts: Row[][]
    coverage: string
  }>()
  const dirty = JSON.stringify(draft) !== JSON.stringify(editing?.payload || fresh(draft.kind))
  useEffect(() => {
    if (!dirty) return
    const guard = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', guard)
    return () => window.removeEventListener('beforeunload', guard)
  }, [dirty])
  useEffect(() => {
    let active = true
    void api<Graph>(`/branches/${branchId}/story-graph`)
      .then((g) => {
        if (active) {
          setGraph(g)
          setBase(g.story_revision)
        }
      })
      .catch(notify)
    return () => {
      active = false
    }
  }, [branchId, notify])
  useEffect(() => {
    let active = true
    setSourceChapter(undefined)
    setSelection(undefined)
    if (chapterId)
      void api<Chapter & { content: string }>(`/chapters/${chapterId}`)
        .then((c) => {
          if (active) setSourceChapter(c)
        })
        .catch(notify)
    return () => {
      active = false
    }
  }, [chapterId, notify])
  async function perform(fn: () => Promise<void>) {
    if (busy) return
    setBusy(true)
    try {
      await fn()
    } catch (e) {
      notify(e)
      setMessage(e instanceof Error ? e.message : '操作失败，表单保留')
    } finally {
      setBusy(false)
    }
  }
  function select(row: Row) {
    if (dirty && !window.confirm('当前长篇记录有未保存修改。放弃这些修改并载入所选记录？')) return
    setEditing(row)
    setDraft(structuredClone(row.payload))
    setBase(graph!.story_revision)
    setHistory([])
    setMessage('正在编辑保存版本；保存前不改变正式记录。')
  }
  function create(kind: Payload['kind']) {
    if (dirty && !window.confirm('当前长篇记录有未保存修改。放弃这些修改并新建记录？')) return
    setEditing(undefined)
    setDraft(fresh(kind))
    setBase(graph!.story_revision)
    setHistory([])
  }
  async function save(archived = false) {
    const next = await api<Graph>(
      `/branches/${branchId}/story-records${editing ? '/' + editing.id : ''}`,
      editing ? 'PUT' : 'POST',
      {
        base_story_revision: base,
        base_revision: editing?.revision ?? null,
        record: draft,
        archived,
      },
    )
    setGraph(next)
    setBase(next.story_revision)
    if (editing) {
      const updated = next.records.find((r) => r.id === editing.id)
      setEditing(updated)
    } else {
      setDraft(fresh(draft.kind))
    }
    setMessage(archived ? '已归档；关联记录会显示失效，历史保留。' : '已保存；关联旧版本的记录需重新核对。')
    await onSaved()
  }
  if (!graph)
    return (
      <section className="card">
        <p role="status">正在载入长篇工作台…</p>
      </section>
    )
  const active = graph.records.filter((r) => !r.archived)
  const characters = active.filter((r) => r.kind === 'character')
  const events = active.filter((r) => r.kind === 'event')
  const filtered = active.filter(
    (r) =>
      (!filter ||
        `${r.payload.title} ${r.payload.notes} ${'thread' in r.payload ? r.payload.thread : ''}`.includes(
          filter,
        )) &&
      (!volume || r.payload.volume === volume),
  )
  const options = (rows: Choice[]) =>
    rows.map((r) => (
      <option key={r.id} value={r.id}>
        {r.payload.title}
        {r.invalid_reasons?.length ? '（失效）' : ''}
      </option>
    ))
  const picker = (title: string, value: string, rows: Choice[], change: (id: string) => void) => (
    <label>
      {title}
      <select value={value} onChange={(e) => change(e.target.value)}>
        <option value="">请选择／未知</option>
        {options(rows)}
      </select>
    </label>
  )
  const multi = (title: string, values: string[], rows: Row[], change: (ids: string[]) => void) => (
    <fieldset>
      <legend>{title}</legend>
      {rows
        .filter((r) => r.id !== editing?.id)
        .map((r) => (
          <label className="check" key={r.id}>
            <input
              type="checkbox"
              checked={values.includes(r.id)}
              onChange={(e) =>
                change(e.target.checked ? [...values, r.id] : values.filter((id) => id !== r.id))
              }
            />
            {r.payload.title}
          </label>
        ))}
    </fieldset>
  )
  const graphNodes =
    view === 'relations'
      ? filtered.filter((r) => r.kind === 'character')
      : filtered.filter((r) => r.kind === 'event')
  const nodePositions = new Map(
    graphNodes.map((r, i) => [r.id, { x: 130 + (i % 3) * 250, y: 65 + Math.floor(i / 3) * 115 }]),
  )
  const edges =
    view === 'relations'
      ? filtered
          .filter((r) => r.payload.kind === 'relationship')
          .map((r) => ({
            from: (r.payload as Schemas['Relationship']).from_id,
            to: (r.payload as Schemas['Relationship']).to_id,
            label: r.payload.title,
            id: r.id,
          }))
      : graphNodes.flatMap((r) =>
          r.payload.kind === 'event'
            ? (r.payload.after || []).map((id) => ({ from: id, to: r.id, label: '先于', id: r.id }))
            : [],
        )
  return (
    <section className="card" aria-label="长篇人物时点与多线工作台">
      <h2>长篇人物、时点与多线</h2>
      <StoryExtraction
        branchId={branchId}
        storyRevision={graph.story_revision}
        chapters={graph.chapters}
        recordNames={Object.fromEntries(graph.records.map((r) => [r.id, r.payload.title]))}
        account={account}
        modelReady={modelReady}
        notify={notify}
        onAdopted={async () => {
          const next = await api<Graph>(`/branches/${branchId}/story-graph`)
          setGraph(next)
          if (!dirty) setBase(next.story_revision)
          await onSaved()
        }}
      />
      <p>{graph.coverage}</p>
      <p role="status">{message}</p>
      <button
        disabled={busy}
        onClick={() =>
          void perform(async () => {
            const current = await api<Graph>(`/branches/${branchId}/story-graph`)
            setGraph(current)
            setBase(current.story_revision)
            setMessage(
              '已读取当前故事版本，表单保留。请重新核对来源和关联；同一记录若在别处修改，需从记录表重新载入比较。',
            )
          })
        }
      >
        读取当前故事版本并保留表单
      </button>
      <div className="actions">
        {(Object.keys(names) as Payload['kind'][]).map((kind) => (
          <button key={kind} onClick={() => create(kind)}>
            新建{names[kind]}
          </button>
        ))}
      </div>
      <div className="form-grid">
        <label>
          筛选人物、事件或线别
          <input value={filter} onChange={(e) => setFilter(e.target.value)} />
        </label>
        <label>
          卷别
          <select value={volume} onChange={(e) => setVolume(e.target.value)}>
            <option value="">全部卷</option>
            {[...new Set(active.map((r) => r.payload.volume).filter(Boolean))].map((v) => (
              <option key={v}>{v}</option>
            ))}
          </select>
        </label>
        <label>
          查看方式
          <select value={view} onChange={(e) => setView(e.target.value)}>
            <option value="lanes">多线泳道</option>
            <option value="dependencies">事件依赖图</option>
            <option value="relations">人物关系图</option>
            <option value="table">全部记录表</option>
          </select>
        </label>
      </div>
      {view === 'lanes' && (
        <div className="story-lanes" aria-label="事件多线泳道">
          {[
            ...new Set(
              filtered
                .filter((r) => r.payload.kind === 'event')
                .map((r) => (r.payload as Schemas['Event']).thread || '主线'),
            ),
          ].map((thread) => (
            <section key={thread}>
              <h3>{thread}</h3>
              <div className="actions">
                {filtered
                  .filter((r) => r.payload.kind === 'event' && r.payload.thread === thread)
                  .sort(
                    (a, b) =>
                      ((a.payload as Schemas['Event']).story_order ?? Infinity) -
                      ((b.payload as Schemas['Event']).story_order ?? Infinity),
                  )
                  .map((r) => (
                    <button key={r.id} onClick={() => select(r)}>
                      {r.payload.title} · 故事序{(r.payload as Schemas['Event']).story_order ?? '未知'} ·{' '}
                      {statusNames[(r.payload as Schemas['Event']).status || 'unknown']}
                      {r.invalid_reasons.length ? ' · 已失效' : ''}
                    </button>
                  ))}
              </div>
            </section>
          ))}
          {!events.length && <p>尚无事件，可先新建人物或事件。故事序由作者明确，叙述位置取自原文。</p>}
        </div>
      )}
      {(view === 'dependencies' || view === 'relations') && (
        <div className="story-diagram">
          <svg
            viewBox={`0 0 800 ${Math.max(180, Math.ceil(graphNodes.length / 3) * 115 + 40)}`}
            role="group"
            aria-label={view === 'relations' ? '人物关系图' : '事件依赖图'}
          >
            <defs>
              <marker
                id="story-arrow"
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="6"
                markerHeight="6"
                orient="auto"
              >
                <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" />
              </marker>
            </defs>
            {edges.map((e, i) => {
              const a = nodePositions.get(e.from),
                b = nodePositions.get(e.to)
              return a && b ? (
                <g key={i}>
                  <path
                    d={`M${a.x},${a.y} L${b.x},${b.y}`}
                    fill="none"
                    stroke="currentColor"
                    markerEnd="url(#story-arrow)"
                  />
                  <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 8}>
                    {e.label.slice(0, 12)}
                  </text>
                </g>
              ) : null
            })}
            {graphNodes.map((r) => {
              const p = nodePositions.get(r.id)!
              return (
                <g
                  key={r.id}
                  role="button"
                  tabIndex={0}
                  aria-label={`编辑${r.payload.title}`}
                  onClick={() => select(r)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      select(r)
                    }
                  }}
                >
                  <rect
                    x={p.x - 90}
                    y={p.y - 23}
                    width="180"
                    height="46"
                    rx="6"
                    fill="var(--paper, #fffefa)"
                    stroke="currentColor"
                  />
                  <text x={p.x} y={p.y + 5} textAnchor="middle">
                    {r.payload.title.slice(0, 12)}
                    {r.invalid_reasons.length ? ' ⚠' : ''}
                  </text>
                  <title>{r.payload.title}</title>
                </g>
              )
            })}
          </svg>
          <p>箭头表示先后或有向关系。可聚焦节点按回车编辑；全部信息也可用记录表查看。</p>
        </div>
      )}
      <details open={view === 'table'}>
        <summary>记录表与原文定位（键盘可操作）</summary>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>类型</th>
                <th>名称</th>
                <th>卷别／状态</th>
                <th>来源</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r) => (
                <tr key={r.id}>
                  <td>{names[r.kind]}</td>
                  <td>{r.payload.title}</td>
                  <td>
                    {r.payload.volume || '未分卷'}
                    {r.invalid_reasons.length ? ' · ' + r.invalid_reasons.join('；') : ''}
                  </td>
                  <td>
                    {(r.payload.sources || []).map((s, i) => (
                      <button key={i} onClick={() => setSourceView(s)}>
                        原文 {i + 1}
                      </button>
                    ))}
                  </td>
                  <td>
                    <button onClick={() => select(r)}>编辑{r.payload.title}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      {sourceView && (
        <aside className="notice">
          <h3>固定来源 · 修订{sourceView.revision}</h3>
          <blockquote>{sourceView.quote}</blockquote>
          <p>
            正文选区 {sourceView.start}—{sourceView.end}；章节{' '}
            {graph.chapters.find((c) => c.id === sourceView.chapter_id)?.title || '历史章节'}
          </p>
          <button onClick={() => setSourceView(undefined)}>关闭来源</button>
        </aside>
      )}
      <details open>
        <summary>
          {editing ? '编辑' : '新建'}
          {names[draft.kind]} · {draft.title || '未命名'}
        </summary>
        <label>
          记录名称
          <input
            value={draft.title}
            maxLength={200}
            onChange={(e) => setDraft({ ...draft, title: e.target.value })}
          />
        </label>
        <label>
          所在卷（可跨卷编辑）
          <input
            value={draft.volume || ''}
            maxLength={100}
            onChange={(e) => setDraft({ ...draft, volume: e.target.value })}
          />
        </label>
        <label>
          补充说明
          <textarea
            value={draft.notes || ''}
            onChange={(e) => setDraft({ ...draft, notes: e.target.value })}
          />
        </label>
        {draft.kind === 'character' && (
          <>
            <label>
              别名（每行一个）
              <textarea
                value={(draft.aliases || []).join('\n')}
                onChange={(e) => setDraft({ ...draft, aliases: e.target.value.split('\n').filter(Boolean) })}
              />
            </label>
            <label>
              人物档案
              <textarea
                value={draft.profile || ''}
                onChange={(e) => setDraft({ ...draft, profile: e.target.value })}
              />
            </label>
          </>
        )}
        {draft.kind === 'event' && (
          <>
            <label>
              事件线别
              <input
                value={draft.thread || ''}
                onChange={(e) => setDraft({ ...draft, thread: e.target.value })}
              />
            </label>
            <label>
              故事顺序（空白为未知）
              <input
                type="number"
                value={draft.story_order ?? ''}
                onChange={(e) =>
                  setDraft({ ...draft, story_order: e.target.value === '' ? null : Number(e.target.value) })
                }
              />
            </label>
            <label>
              故事时间说明
              <input
                value={draft.story_time || ''}
                onChange={(e) => setDraft({ ...draft, story_time: e.target.value })}
              />
            </label>
            <label>
              发生状态
              <select
                value={draft.status}
                onChange={(e) => setDraft({ ...draft, status: e.target.value as Schemas['Event']['status'] })}
              >
                {['planned', 'actual', 'unknown', 'conflict'].map((s) => (
                  <option key={s} value={s}>
                    {statusNames[s]}
                  </option>
                ))}
              </select>
            </label>
            {multi('参与人物', draft.participants || [], characters, (ids) =>
              setDraft({ ...draft, participants: ids }),
            )}
            {multi('发生在这些事件之后', draft.after || [], events, (ids) =>
              setDraft({ ...draft, after: ids }),
            )}
          </>
        )}
        {draft.kind === 'state' && (
          <>
            {picker('所属人物', draft.character_id, characters, (id) =>
              setDraft({ ...draft, character_id: id }),
            )}
            {picker('获得或变化事件', draft.event_id, events, (id) => setDraft({ ...draft, event_id: id }))}
            {picker('失效事件（可选）', draft.until_event_id || '', events, (id) =>
              setDraft({ ...draft, until_event_id: id || null }),
            )}
            <label>
              状态类别
              <select
                value={draft.category}
                onChange={(e) =>
                  setDraft({ ...draft, category: e.target.value as Schemas['State']['category'] })
                }
              >
                {Object.entries({
                  knowledge: '认知',
                  capability: '能力',
                  resource: '资源',
                  injury: '伤势',
                  other: '其他',
                }).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
            <label>
              状态事项
              <input value={draft.key} onChange={(e) => setDraft({ ...draft, key: e.target.value })} />
            </label>
            <label>
              该时点的状态值
              <textarea value={draft.value} onChange={(e) => setDraft({ ...draft, value: e.target.value })} />
            </label>
            <label>
              信息视角
              <select
                value={draft.audience}
                onChange={(e) =>
                  setDraft({ ...draft, audience: e.target.value as Schemas['State']['audience'] })
                }
              >
                <option value="author">作者掌握</option>
                <option value="character">该人物掌握</option>
                <option value="reader">读者已知</option>
              </select>
            </label>
            <label>
              状态性质
              <select
                value={draft.truth}
                onChange={(e) => setDraft({ ...draft, truth: e.target.value as Schemas['State']['truth'] })}
              >
                {['actual', 'planned', 'unknown', 'conflict'].map((s) => (
                  <option key={s} value={s}>
                    {statusNames[s]}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}
        {draft.kind === 'relationship' && (
          <>
            {picker('关系起点人物', draft.from_id, characters, (id) => setDraft({ ...draft, from_id: id }))}
            {picker('关系终点人物', draft.to_id, characters, (id) => setDraft({ ...draft, to_id: id }))}
            {picker('关系形成事件（可选）', draft.event_id || '', events, (id) =>
              setDraft({ ...draft, event_id: id || null }),
            )}
            <label>
              关系描述
              <input
                value={draft.relation}
                onChange={(e) => setDraft({ ...draft, relation: e.target.value })}
              />
            </label>
            <label>
              关系性质
              <select
                value={draft.truth}
                onChange={(e) =>
                  setDraft({ ...draft, truth: e.target.value as Schemas['Relationship']['truth'] })
                }
              >
                {['actual', 'planned', 'unknown', 'conflict'].map((s) => (
                  <option key={s} value={s}>
                    {statusNames[s]}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}
        <details>
          <summary>从保存稿选区关联原文</summary>
          {picker(
            '来源章节',
            chapterId,
            graph.chapters.map((c) => ({ id: c.id, payload: { title: c.title } })),
            setChapterId,
          )}
          {sourceChapter && (
            <label>
              保存稿原文（选择要引用的范围）
              <textarea
                readOnly
                rows={6}
                value={sourceChapter.content}
                onSelect={(e) => {
                  const t = e.currentTarget
                  setSelection(
                    t.selectionEnd > t.selectionStart
                      ? {
                          chapter_id: sourceChapter.id,
                          revision: sourceChapter.revision,
                          start: t.selectionStart,
                          end: t.selectionEnd,
                          quote: t.value.slice(t.selectionStart, t.selectionEnd),
                        }
                      : undefined,
                  )
                }}
              />
            </label>
          )}
          <button
            disabled={!selection}
            onClick={() =>
              selection && setDraft({ ...draft, sources: [...(draft.sources || []), selection] })
            }
          >
            关联所选原文
          </button>
        </details>
        {(draft.sources || []).map((s, i) => (
          <div key={i}>
            <blockquote>{s.quote}</blockquote>
            <button
              onClick={() => setDraft({ ...draft, sources: (draft.sources || []).filter((_, j) => i !== j) })}
            >
              移除来源 {i + 1}
            </button>
          </div>
        ))}
        {draft.kind === 'promise' && (
          <>
            <label>
              承诺线别
              <input
                value={draft.thread || ''}
                onChange={(e) => setDraft({ ...draft, thread: e.target.value })}
              />
            </label>
            {draft.stages.map((s, i) => (
              <article className="fact" key={i}>
                <strong>{statusNames[s.status]}</strong>
                <p>{s.note}</p>
                {s.sources?.map((source, j) => (
                  <button key={j} onClick={() => setSourceView(source)}>
                    查看阶段来源 {j + 1}
                  </button>
                ))}
                <button
                  disabled={draft.stages.length === 1}
                  onClick={() => setDraft({ ...draft, stages: draft.stages.filter((_, j) => i !== j) })}
                >
                  移除此阶段（保存后历史保留）
                </button>
              </article>
            ))}
            <label>
              新增阶段
              <select
                value={stageStatus}
                onChange={(e) => setStageStatus(e.target.value as Schemas['PromiseStage']['status'])}
              >
                {['planned', 'planted', 'reinforced', 'resolved', 'retained', 'abandoned'].map((s) => (
                  <option key={s} value={s}>
                    {statusNames[s]}
                  </option>
                ))}
              </select>
            </label>
            {picker('阶段关联事件', stageEvent, events, setStageEvent)}
            <label>
              阶段说明／保留或放弃理由
              <textarea value={stageNote} onChange={(e) => setStageNote(e.target.value)} />
            </label>
            <button
              onClick={() =>
                setDraft({
                  ...draft,
                  stages: [
                    ...draft.stages,
                    {
                      status: stageStatus,
                      event_id: stageEvent || null,
                      sources: selection ? [selection] : [],
                      note: stageNote,
                    },
                  ],
                })
              }
            >
              加入阶段（使用当前原文选区）
            </button>
          </>
        )}
        <div className="actions">
          <button
            className="primary"
            disabled={busy || !draft.title.trim()}
            onClick={() => void perform(() => save())}
          >
            保存长篇记录
          </button>
          {editing && (
            <>
              <button
                disabled={busy}
                onClick={() =>
                  void perform(async () => {
                    setHistory(await api(`/branches/${branchId}/story-records/${editing.id}/history`))
                  })
                }
              >
                查看记录修订历史
              </button>
              <button disabled={busy} onClick={() => void perform(() => save(!editing.archived))}>
                {editing.archived ? '恢复记录' : '归档记录（保留历史）'}
              </button>
            </>
          )}
        </div>
        {history.map((h) => (
          <article className="fact" key={h.revision}>
            <strong>
              修订 {h.revision} · {h.payload.title}
            </strong>
            <p>{h.payload.notes}</p>
            <button
              onClick={() => {
                setDraft(structuredClone(h.payload))
                setMessage('已载入历史内容，保存会创建新修订。')
              }}
            >
              载入此历史内容供核对
            </button>
          </article>
        ))}
      </details>
      <details>
        <summary>查询人物在故事时点知道什么、具有什么</summary>
        <div className="form-grid">
          {picker('查询人物', queriedCharacter, characters, setQueriedCharacter)}
          <label>
            查询故事顺序
            <input type="number" value={order} onChange={(e) => setOrder(e.target.value)} />
          </label>
          <label>
            查询信息视角
            <select value={audience} onChange={(e) => setAudience(e.target.value as typeof audience)}>
              <option value="character">人物</option>
              <option value="author">作者</option>
              <option value="reader">读者</option>
            </select>
          </label>
        </div>
        <button
          disabled={busy}
          onClick={() =>
            void perform(async () => {
              const q = new URLSearchParams({ audience })
              if (order !== '') q.set('story_order', order)
              if (queriedCharacter) q.set('character_id', queriedCharacter)
              setTimeResult(await api(`/branches/${branchId}/story-context?${q}`))
            })
          }
        >
          查询有效状态
        </button>
        {timeResult && (
          <section>
            <p>{timeResult.coverage}</p>
            <p>
              有效状态 {timeResult.states.length}；冲突组 {timeResult.conflicts.length}；未知或待判断{' '}
              {timeResult.uncertain.length}
            </p>
            {[...timeResult.states, ...timeResult.uncertain].map((r) => (
              <article key={r.id}>
                <button onClick={() => select(r)}>
                  {r.payload.title} · {r.payload.kind === 'state' ? r.payload.value : ''}
                </button>
              </article>
            ))}
          </section>
        )}
      </details>
    </section>
  )
}
