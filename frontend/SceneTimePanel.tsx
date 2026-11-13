import { useEffect, useState } from 'react'
import { api, type Chapter } from './api'
import type { Notify } from './App'

type Scene = {
  event_id?: string | null
  event_revision?: number | null
  title: string
  source_revision: number
  start: number
  end: number
  story_order: number | null
  story_time: string
  audience: 'author' | 'character' | 'reader'
  character_id: string | null
  note: string
}
type Row = { id: string; revision: number; payload: Scene; archived: number; stale: boolean }
type Data = { source_revision: number; story_revision: number; scenes: Row[] }
type Character = { id: string; kind: string; archived: number; payload: { title: string } }
type Context = {
  longform: {
    blocked?: string
    coverage: string
    scene_binding?: Row
    states: { id: string; payload: { title: string; value: string } }[]
    uncertain: unknown[]
    conflicts: unknown[]
    excluded_legacy_facts?: number
  }
}
type Bundle = {
  scenes: { binding: Row; entry: Context['longform']; exit: Context['longform'] }[]
  unbound_ranges: { start: number; end: number }[]
  coverage: string
}
const fresh = (chapter: Chapter): Scene => ({
  title: '',
  source_revision: chapter.revision,
  start: 0,
  end: chapter.content.length,
  story_order: null,
  story_time: '未知',
  audience: 'author',
  character_id: null,
  note: '',
})

export function SceneTimePanel({
  branchId,
  chapter,
  cursor,
  selection,
  notify,
}: {
  branchId: string
  chapter: Chapter
  cursor: number
  selection: { start: number; end: number }
  notify: Notify
}) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<Data>()
  const [characters, setCharacters] = useState<Character[]>([])
  const [form, setForm] = useState<Scene>(() => fresh(chapter))
  const [editing, setEditing] = useState<Row>()
  const [context, setContext] = useState<Context>()
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [history, setHistory] = useState<{ revision: number; payload: Scene; archived: number }[]>([])
  const [bundle, setBundle] = useState<Bundle>()
  useEffect(() => {
    if (!open) return
    let active = true
    void Promise.all([
      api<Data>(`/branches/${branchId}/chapters/${chapter.id}/scene-times`),
      api<{ records: Character[] }>(`/branches/${branchId}/story-graph`),
    ])
      .then(([d, g]) => {
        if (active) {
          setData(d)
          setCharacters(g.records.filter((r) => r.kind === 'character' && !r.archived))
        }
      })
      .catch((e) => {
        if (active) notify(e)
      })
    return () => {
      active = false
    }
  }, [open, branchId, chapter.id, chapter.revision, notify])
  useEffect(() => {
    if (!open) return
    let active = true
    setContext(undefined)
    if (cursor > chapter.content.length) return
    const timer = setTimeout(
      () =>
        void api<Context>(`/branches/${branchId}/context?chapter_id=${chapter.id}&text_position=${cursor}`)
          .then((c) => {
            if (active) setContext(c)
          })
          .catch((e) => {
            if (active) notify(e)
          }),
      250,
    )
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [open, branchId, chapter.id, chapter.revision, cursor, data?.story_revision, notify])
  async function save(archived = false) {
    if (busy || !data) return
    setBusy(true)
    try {
      const result = await api<Data>(
        `/branches/${branchId}/chapters/${chapter.id}/scene-times${editing ? '/' + editing.id : ''}`,
        editing ? 'PUT' : 'POST',
        {
          base_story_revision: data.story_revision,
          base_revision: editing?.revision ?? null,
          scene: form,
          archived,
        },
      )
      setData(result)
      setBundle(undefined)
      const row = result.scenes.find((r) => r.id === editing?.id) || result.scenes.at(-1)
      setEditing(row)
      if (row) setForm(row.payload)
      setMessage(
        archived
          ? '已归档保存的绑定，历史保留。'
          : '场景时点已确认，不修改正文。正文后续变化时须重新复核范围。',
      )
    } catch (e) {
      notify(e)
      setMessage(e instanceof Error ? e.message : '保存失败，表单保留。')
    } finally {
      setBusy(false)
    }
  }
  return (
    <details className="notice" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>场景时点与人物当前状态</summary>
      {open && (
        <section aria-label="场景时点绑定">
          <p>
            把本章范围绑定到明确故事时点和信息视角。正文位置沿用选区；未设置故事顺序保持未知。绑定后旧式章序事实不自动成为当前人物知识。
          </p>
          <p role="status">{message}</p>
          <p>
            当前保存稿修订 {chapter.revision}；表单确认稿修订 {form.source_revision}。
            {form.source_revision !== chapter.revision ? '稿件已变化，请先核对范围。' : ''}
          </p>
          <button
            disabled={busy}
            onClick={() => {
              setForm(fresh(chapter))
              setEditing(undefined)
              setHistory([])
            }}
          >
            新建本章场景绑定
          </button>
          {data?.scenes.map((row) => (
            <article className="fact" key={row.id}>
              <button
                onClick={() => {
                  setEditing(row)
                  setForm(structuredClone(row.payload))
                  setHistory([])
                }}
              >
                {row.payload.title} · {row.archived ? '已归档' : row.stale ? '待复核' : '已确认'} ·{' '}
                {row.payload.start}—{row.payload.end}
              </button>
            </article>
          ))}
          <label>
            场景名称
            <input
              value={form.title}
              maxLength={200}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
            />
          </label>
          <div className="form-grid">
            <label>
              场景开始位置
              <input
                type="number"
                value={form.start}
                onChange={(e) => setForm({ ...form, start: Number(e.target.value) })}
              />
            </label>
            <label>
              场景结束位置
              <input
                type="number"
                value={form.end}
                onChange={(e) => setForm({ ...form, end: Number(e.target.value) })}
              />
            </label>
          </div>
          <div className="actions">
            <button
              onClick={() =>
                setForm({
                  ...form,
                  start: selection.start,
                  end: selection.end,
                  source_revision: chapter.revision,
                })
              }
            >
              用当前选区复核场景范围
            </button>
            <button
              onClick={() =>
                setForm({ ...form, start: 0, end: chapter.content.length, source_revision: chapter.revision })
              }
            >
              用当前保存稿整章范围复核
            </button>
          </div>
          <label>
            场景故事顺序（空白为未知）
            <input
              type="number"
              value={form.story_order ?? ''}
              onChange={(e) =>
                setForm({ ...form, story_order: e.target.value === '' ? null : Number(e.target.value) })
              }
            />
          </label>
          <label>
            场景故事时间说明
            <input
              value={form.story_time}
              maxLength={200}
              onChange={(e) => setForm({ ...form, story_time: e.target.value })}
            />
          </label>
          {form.event_id && (
            <div className="notice">
              <p>
                此时点关联实际事件修订{form.event_revision}
                。事件断言改变后需要重新核对；仅追加来源不会使绑定失效。
              </p>
              <button
                disabled={busy}
                onClick={() => setForm({ ...form, event_id: null, event_revision: null })}
              >
                改为作者手动确认时点（解除事件修订关联）
              </button>
            </div>
          )}
          <label>
            场景信息视角
            <select
              value={form.audience}
              onChange={(e) =>
                setForm({ ...form, audience: e.target.value as Scene['audience'], character_id: null })
              }
            >
              <option value="author">作者掌握</option>
              <option value="character">指定人物掌握</option>
              <option value="reader">读者已知</option>
            </select>
          </label>
          {form.audience === 'character' && (
            <label>
              本场景视角人物
              <select
                value={form.character_id || ''}
                onChange={(e) => setForm({ ...form, character_id: e.target.value || null })}
              >
                <option value="">请选择已有稳定人物身份</option>
                {characters.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.payload.title}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            时点确认说明
            <textarea
              rows={2}
              value={form.note}
              maxLength={2000}
              onChange={(e) => setForm({ ...form, note: e.target.value })}
            />
          </label>
          <div className="actions">
            <button disabled={busy || !data || !form.title.trim()} onClick={() => void save()}>
              确认保存场景时点
            </button>
            {editing && (
              <>
                <button disabled={busy} onClick={() => void save(true)}>
                  归档保存的场景绑定（保留历史）
                </button>
                <button
                  disabled={busy}
                  onClick={() =>
                    void api<typeof history>(
                      `/branches/${branchId}/chapters/${chapter.id}/scene-times/${editing.id}/history`,
                    )
                      .then(setHistory)
                      .catch(notify)
                  }
                >
                  查看场景绑定历史
                </button>
              </>
            )}
          </div>
          {history.map((h) => (
            <article key={h.revision}>
              <p>
                修订{h.revision} · {h.payload.title} · 故事序{h.payload.story_order ?? '未知'} ·{' '}
                {h.payload.start}—{h.payload.end}
              </p>
            </article>
          ))}
          <section aria-label="当前光标有效状态">
            <h3>当前光标位置 {cursor} 的状态</h3>
            {context ? (
              <>
                <p>
                  {context.longform.scene_binding
                    ? `场景：${context.longform.scene_binding.payload.title} · 故事序${context.longform.scene_binding.payload.story_order ?? '未知'}`
                    : '当前位置尚无有效场景绑定'}
                </p>
                <p>{context.longform.coverage}</p>
                {context.longform.blocked && <p role="alert">场景绑定待复核，模型动作暂停。</p>}
                <p>
                  有效状态 {context.longform.states.length}；冲突组 {context.longform.conflicts.length}
                  ；待判断 {context.longform.uncertain.length}
                </p>
                {context.longform.states.map((s) => (
                  <p key={s.id}>
                    {s.payload.title}：{s.payload.value}
                  </p>
                ))}
              </>
            ) : (
              <p>正在读取保存稿状态；若有未保存改动，请先保存再核对位置。</p>
            )}
          </section>
          <details>
            <summary>整章场景及进入／离开状态</summary>
            <button
              disabled={busy}
              onClick={() => {
                setBundle(undefined)
                void api<Bundle | null>(`/branches/${branchId}/chapters/${chapter.id}/scene-context`)
                  .then((value) => {
                    if (value) setBundle(value)
                    else setMessage('本章尚无场景绑定，不能据此宣称已核对故事时点。')
                  })
                  .catch(notify)
              }}
            >
              读取整章逐场景状态
            </button>
            {bundle && (
              <section aria-label="整章场景状态清单">
                <p>{bundle.coverage}</p>
                {bundle.scenes.map((s) => (
                  <article key={s.binding.id}>
                    <h4>
                      {s.binding.payload.title} · 故事序{s.binding.payload.story_order ?? '未知'}
                    </h4>
                    <p>
                      进入时有效状态 {s.entry.states.length}；离开时有效状态 {s.exit.states.length}
                    </p>
                    {s.entry.states.map((v) => (
                      <p key={v.id}>
                        进入：{v.payload.title} · {v.payload.value}
                      </p>
                    ))}
                    {s.exit.states.map((v) => (
                      <p key={v.id}>
                        离开：{v.payload.title} · {v.payload.value}
                      </p>
                    ))}
                  </article>
                ))}
                {bundle.unbound_ranges.map((r, i) => (
                  <p key={i}>
                    未绑定范围：{r.start}—{r.end}，时点未知。
                  </p>
                ))}
              </section>
            )}
          </details>
        </section>
      )}
    </details>
  )
}
