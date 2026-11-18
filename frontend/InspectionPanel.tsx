import { useEffect, useState } from 'react'
import { api, type Chapter } from './api'
import { confirmLeave, useLeaveGuard } from './leaveGuard'

type Scope = 'book' | 'volume' | 'chapter'
type Status = 'green' | 'yellow' | 'red' | 'pending' | 'outdated' | 'not_applicable'
type Conclusion = Exclude<Status, 'pending' | 'outdated'>
type Evidence = {
  chapter_id: string
  revision: number
  start: number
  end: number
  quote: string
}
type Review = {
  check_id: string
  status: Conclusion | ''
  basis_note: string
  reason: string
  evidence: Evidence[]
}
type Item = {
  check_id: string
  title: string
  status: Status
  current_status?: Status
  summary: string
  basis: Record<string, unknown>
  author_review?: Review
}
type Preview = {
  scope: Scope
  scope_id: string | null
  basis_sha256: string
  chapters: { id: string; title: string; revision: number; chars: number }[]
  items: Item[]
}
type Report = Preview & { id: string; current: boolean; created: number }
type Collection = { preview: Preview; latest: Report | null; history: { id: string; created: number }[] }
type Outline = { confirmed: { plan: { volumes: { id: string; title: string }[] } } | null }

const labels: Record<Status, string> = {
  green: '通过',
  yellow: '待处理',
  red: '阻断',
  pending: '未核查',
  outdated: '过期',
  not_applicable: '不适用',
}
const shades: Partial<Record<Status, [string, string]>> = {
  green: ['#1e493a', '#dcefe2'],
  yellow: ['#604308', '#fff0c2'],
  red: ['#721f24', '#fbdfe2'],
}
const decisions: Conclusion[] = ['green', 'yellow', 'red', 'not_applicable']
const newReview = (id: string): Review => ({
  check_id: id,
  status: '',
  basis_note: '',
  reason: '',
  evidence: [],
})

function StatusBadge({ status }: { status: Status }) {
  const shade = shades[status]
  return (
    <span className="badge" style={shade && { color: shade[0], backgroundColor: shade[1] }}>
      {labels[status]}
    </span>
  )
}

function CheckList({ items, onChoose }: { items: Item[]; onChoose?: (item: Item) => void }) {
  return (
    <ol className="fact-grid">
      {items.map((item) => (
        <li key={item.check_id} className="fact">
          <div className="section-title">
            <strong>
              {item.check_id} {item.title}
            </strong>
            <StatusBadge status={item.current_status ?? item.status} />
          </div>
          <p>{item.summary}</p>
          {item.current_status && item.current_status !== item.status && <p>原判定 {labels[item.status]}</p>}
          {item.author_review && (
            <div>
              <p>
                作者核对 · {labels[item.author_review.status as Conclusion]} · {item.author_review.reason}
              </p>
              <p>对照依据 {item.author_review.basis_note}</p>
              {item.author_review.evidence.map((source, index) => (
                <blockquote key={index}>
                  {source.quote}
                  <footer>
                    章稿修订 {source.revision} · 位置 {source.start}–{source.end}
                  </footer>
                </blockquote>
              ))}
            </div>
          )}
          <details>
            <summary>对照依据</summary>
            <pre className="report">{JSON.stringify(item.basis, null, 2)}</pre>
          </details>
          {onChoose && (
            <button type="button" onClick={() => onChoose(item)}>
              核对 {item.check_id}
            </button>
          )}
        </li>
      ))}
    </ol>
  )
}

function EvidenceField({
  chapters,
  onAdd,
}: {
  chapters: Preview['chapters']
  onAdd: (source: Evidence) => void
}) {
  const [chapterId, setChapterId] = useState('')
  const [chapter, setChapter] = useState<Chapter>()
  const [range, setRange] = useState({ start: 0, end: 0 })
  const [error, setError] = useState('')
  const expected = chapters.find((row) => row.id === chapterId)
  useEffect(() => {
    let active = true
    setChapter(undefined)
    setRange({ start: 0, end: 0 })
    setError('')
    if (!chapterId) return
    void api<Chapter>(`/chapters/${chapterId}`)
      .then((current) => {
        if (!active) return
        if (current.revision !== expected?.revision) {
          setError('正文已变化，请重新读取体检依据。')
          return
        }
        setChapter(current)
      })
      .catch((error: unknown) => {
        if (active) setError(error instanceof Error ? error.message : '章稿读取失败。')
      })
    return () => {
      active = false
    }
  }, [chapterId, expected?.revision])
  const quote = chapter?.content.slice(range.start, range.end) ?? ''
  return (
    <div>
      <label>
        引用章节
        <select value={chapterId} onChange={(event) => setChapterId(event.target.value)}>
          <option value="">选择本次范围内的章节</option>
          {chapters.map(({ id, title }) => (
            <option key={id} value={id}>
              {title}
            </option>
          ))}
        </select>
      </label>
      {error && <p role="alert">{error}</p>}
      {chapter && (
        <>
          <label>
            保存稿（选择要引用的文字）
            <textarea
              readOnly
              rows={5}
              value={chapter.content}
              onSelect={(event) =>
                setRange({
                  start: event.currentTarget.selectionStart,
                  end: event.currentTarget.selectionEnd,
                })
              }
            />
          </label>
          <button
            type="button"
            disabled={!quote.trim() || Array.from(quote).length > 4000}
            onClick={() => onAdd({ chapter_id: chapter.id, revision: chapter.revision, ...range, quote })}
          >
            引用选区
          </button>
        </>
      )}
    </div>
  )
}

export function InspectionPanel({
  branchId,
  chapters,
}: {
  branchId: string
  chapters: Pick<Chapter, 'id' | 'title'>[]
}) {
  const [scope, setScope] = useState<Scope>('book')
  const [scopeId, setScopeId] = useState('')
  const [volumes, setVolumes] = useState<{ id: string; title: string }[]>([])
  const [data, setData] = useState<Collection>()
  const [selected, setSelected] = useState('')
  const [reviews, setReviews] = useState<Record<string, Review>>({})
  const [minimum, setMinimum] = useState('')
  const [maximum, setMaximum] = useState('')
  const [count, setCount] = useState('')
  const [reload, setReload] = useState(0)
  const [historyId, setHistoryId] = useState('')
  const [historical, setHistorical] = useState<Report>()
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const route = `/branches/${branchId}/workflow-inspections`
  const dirty =
    Object.values(reviews).some(
      (review) => review.status || review.reason || review.basis_note || review.evidence.length,
    ) || !!(minimum || maximum || count)
  useLeaveGuard(dirty, '尚未保存的体检核对')

  useEffect(() => {
    let active = true
    void api<Outline>(`/branches/${branchId}/outline-workspace`)
      .then((outline) => {
        if (active) setVolumes(outline.confirmed?.plan.volumes ?? [])
      })
      .catch((error: unknown) => {
        if (active) setError(error instanceof Error ? error.message : '卷目录读取失败。')
      })
    return () => {
      active = false
    }
  }, [branchId, reload])

  useEffect(() => {
    let active = true
    setData(undefined)
    setLoading(true)
    if (scope !== 'book' && !scopeId) {
      setLoading(false)
      return
    }
    const search = new URLSearchParams({ scope })
    if (scopeId) search.set('scope_id', scopeId)
    void api<Collection>(`${route}?${search}`)
      .then((next) => {
        if (active) setData(next)
      })
      .catch((error: unknown) => {
        if (active) setError(error instanceof Error ? error.message : '体检依据读取失败。')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [route, scope, scopeId, reload])

  useEffect(() => {
    let active = true
    setHistorical(undefined)
    if (historyId) {
      void api<Report>(`${route}/${historyId}`)
        .then((report) => {
          if (active) setHistorical(report)
        })
        .catch((error: unknown) => {
          if (active) setError(error instanceof Error ? error.message : '历史体检单读取失败。')
        })
    }
    return () => {
      active = false
    }
  }, [route, historyId])

  function resetDraft() {
    setSelected('')
    setReviews({})
    setMinimum('')
    setMaximum('')
    setCount('')
    setHistoryId('')
    setError('')
    setMessage('')
  }
  const choices = scope === 'volume' ? volumes : chapters
  const review = reviews[selected] ?? newReview(selected)
  const change = (values: Partial<Review>) =>
    setReviews((current) => ({
      ...current,
      [selected]: { ...(current[selected] ?? newReview(selected)), ...values },
    }))
  const visible = historyId ? historical : data?.latest
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!data || saving) return
    const validReviews = Object.values(reviews).filter(
      (review) => review.status || review.reason || review.basis_note || review.evidence.length,
    )
    if (
      validReviews.some(
        (review) =>
          !review.status ||
          !review.reason.trim() ||
          !review.basis_note.trim() ||
          (['green', 'red'].includes(review.status) && !review.evidence.length),
      )
    ) {
      setError('请完成已编辑项目的判定和说明。确认通过或阻断时请引用正文。')
      return
    }
    setSaving(true)
    setError('')
    setMessage('')
    try {
      const result = await api<Report>(route, 'POST', {
        scope,
        scope_id: scopeId || null,
        basis_sha256: data.preview.basis_sha256,
        author_reviews: validReviews,
        ...(minimum || maximum || count
          ? {
              length_targets: {
                min_chars: minimum === '' ? null : Number(minimum),
                max_chars: maximum === '' ? null : Number(maximum),
                expected_chapters: count === '' ? null : Number(count),
              },
            }
          : {}),
      })
      setData({
        ...data,
        latest: result,
        history: [{ id: result.id, created: result.created }, ...data.history],
      })
      resetDraft()
      setMessage('已保存本次体检单与作者核对。')
    } catch (error) {
      setError(error instanceof Error ? error.message : '体检单保存失败。')
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="card" aria-label="十处对账体检" style={{ overflowWrap: 'anywhere' }}>
      <h2>十处对账体检</h2>
      <div className="actions">
        <label>
          检查范围
          <select
            value={scope}
            disabled={saving}
            onChange={(event) => {
              if (!confirmLeave()) return
              resetDraft()
              setScope(event.target.value as Scope)
              setScopeId('')
            }}
          >
            <option value="book">全书</option>
            <option value="volume">卷</option>
            <option value="chapter">章</option>
          </select>
        </label>
        {scope !== 'book' && (
          <label>
            {scope === 'volume' ? '选择已确认卷' : '选择章节'}
            <select
              value={scopeId}
              disabled={saving}
              onChange={(event) => {
                if (!confirmLeave()) return
                resetDraft()
                setScopeId(event.target.value)
              }}
            >
              <option value="">请选择</option>
              {choices.map(({ id, title }) => (
                <option key={id} value={id}>
                  {title}
                </option>
              ))}
            </select>
          </label>
        )}
        <button
          type="button"
          disabled={loading || saving}
          onClick={() => {
            if (!confirmLeave()) return
            resetDraft()
            setReload((current) => current + 1)
          }}
        >
          重新读取依据
        </button>
      </div>
      {loading && <p role="status">正在读取当前依据…</p>}
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      {data && (
        <>
          <p>
            {data.preview.chapters.length} 章 ·{' '}
            {data.preview.chapters.reduce((total, chapter) => total + chapter.chars, 0)} 个有效字符
          </p>
          <details>
            <summary>本次依据与未核查项</summary>
            <CheckList items={data.preview.items} onChoose={(item) => setSelected(item.check_id)} />
          </details>
          <form onSubmit={(event) => void submit(event)}>
            <fieldset disabled={saving}>
              <legend>作者核对</legend>
              <label>
                核对项目
                <select value={selected} onChange={(event) => setSelected(event.target.value)}>
                  <option value="">选择要核对的项目</option>
                  {data.preview.items.map((item) => (
                    <option key={item.check_id} value={item.check_id}>
                      {item.check_id} {item.title}
                    </option>
                  ))}
                </select>
              </label>
              {selected && (
                <div key={selected}>
                  <label>
                    作者判定
                    <select
                      value={review.status}
                      onChange={(event) => change({ status: event.target.value as Review['status'] })}
                    >
                      <option value="">待填</option>
                      {decisions.map((status) => (
                        <option value={status} key={status}>
                          {labels[status]}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    对照的章纲、约定或规则
                    <textarea
                      rows={2}
                      maxLength={2000}
                      value={review.basis_note}
                      onChange={(event) => change({ basis_note: event.target.value })}
                    />
                  </label>
                  <label>
                    判定理由
                    <textarea
                      rows={2}
                      maxLength={2000}
                      value={review.reason}
                      onChange={(event) => change({ reason: event.target.value })}
                    />
                  </label>
                  <EvidenceField
                    chapters={data.preview.chapters}
                    onAdd={(source) => {
                      if (
                        review.evidence.length < 30 &&
                        !review.evidence.some(
                          (old) =>
                            old.chapter_id === source.chapter_id &&
                            old.start === source.start &&
                            old.end === source.end,
                        )
                      )
                        change({ evidence: [...review.evidence, source] })
                    }}
                  />
                  {review.evidence.map((source, index) => (
                    <div key={`${source.chapter_id}/${source.start}/${source.end}`}>
                      <blockquote>{source.quote}</blockquote>
                      <button
                        type="button"
                        onClick={() => change({ evidence: review.evidence.filter((_, i) => i !== index) })}
                      >
                        移除引文 {index + 1}
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </fieldset>
            <fieldset disabled={saving}>
              <legend>本次篇幅目标</legend>
              <label>
                章数
                <input
                  type="number"
                  min={1}
                  max={1000}
                  value={count}
                  onChange={(event) => setCount(event.target.value)}
                />
              </label>
              <label>
                有效字符下限
                <input
                  type="number"
                  min={0}
                  value={minimum}
                  onChange={(event) => setMinimum(event.target.value)}
                />
              </label>
              <label>
                有效字符上限
                <input
                  type="number"
                  min={1}
                  value={maximum}
                  onChange={(event) => setMaximum(event.target.value)}
                />
              </label>
            </fieldset>
            <button className="primary" type="submit" disabled={saving}>
              {saving ? '正在保存…' : '生成体检单'}
            </button>
          </form>
          {data.latest && (
            <details open>
              <summary>已保存体检单</summary>
              <label>
                选择体检单
                <select value={historyId} onChange={(event) => setHistoryId(event.target.value)}>
                  <option value="">最近一次</option>
                  {data.history
                    .filter((row) => row.id !== data.latest?.id)
                    .map(({ id, created }) => (
                      <option key={id} value={id}>
                        {new Date(created * 1000).toLocaleString()}
                      </option>
                    ))}
                </select>
              </label>
              {visible && (
                <section aria-label="体检单结果">
                  <p>{visible.current ? '依据仍为当前版本' : '部分依据已更新，过期项待复检'}</p>
                  <CheckList items={visible.items} />
                </section>
              )}
            </details>
          )}
        </>
      )}
    </section>
  )
}
