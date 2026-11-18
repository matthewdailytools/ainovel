import { useEffect, useRef, useState } from 'react'
import { api, ApiError, type Chapter } from './api'
import type { Notify } from './App'
import { useLeaveGuard } from './leaveGuard'

type ChapterOption = Pick<Chapter, 'id' | 'title' | 'position'>
type Source = { chapter_id: string; revision: number; start: number; end: number; quote: string }
type Closure = { note: string; sources: Source[] }
type Check = { id: string; title: string; status: 'green' | 'pending' | 'red'; detail: string }
type Manifest = { files?: Record<string, string> }
type Completion = { id: string; current: boolean; checks: Check[]; manifest: Manifest }
type Current = {
  basis_sha256: string
  latest: Completion | null
  checks: Check[]
  history?: { id: string; current: boolean; created: number }[]
}
type Preview = { checks: Check[]; manifest: Manifest; materials: Record<string, string> | null }

const closureFields = [
  ['ending', '主冲突与终局'],
  ['characters', '关键人物与关系'],
  ['promises', '伏笔与读者承诺'],
] as const
type ClosureKey = (typeof closureFields)[number][0]
const checksText: Record<Check['status'], string> = {
  green: '绿 · 通过',
  pending: '待核对',
  red: '红 · 阻断',
}
const freshClosures = (): Record<ClosureKey, Closure> => ({
  ending: { note: '', sources: [] },
  characters: { note: '', sources: [] },
  promises: { note: '', sources: [] },
})

function CheckList({ checks }: { checks: Check[] }) {
  return (
    <ol className="fact-grid" aria-label="六项完本判定">
      {checks.map((check) => (
        <li className="fact" key={check.id}>
          <div className="section-title">
            <strong>{check.title}</strong>
            <span className="badge">{checksText[check.status]}</span>
          </div>
          <p>{check.detail}</p>
        </li>
      ))}
    </ol>
  )
}

function ManifestList({ manifest }: { manifest: Manifest }) {
  return (
    <ul>
      {Object.entries(manifest.files ?? {}).map(([name, sha]) => (
        <li key={name}>
          {name}
          <pre className="report">{sha}</pre>
        </li>
      ))}
    </ul>
  )
}

// A textarea presents CRLF as one line break. Translate the selection back to
// offsets in the saved chapter so that quotes bind to the exact stored revision.
function originalOffset(content: string, offset: number) {
  let original = 0
  for (let normalized = 0; normalized < offset && original < content.length; normalized += 1) {
    original += content.slice(original, original + 2) === '\r\n' ? 2 : 1
  }
  return original
}

function EvidencePicker({
  chapters,
  onAdd,
}: {
  chapters: ChapterOption[]
  onAdd: (destination: ClosureKey, source: Source) => void
}) {
  const [destination, setDestination] = useState<ClosureKey>('ending')
  const [selected, setSelected] = useState('')
  const [chapter, setChapter] = useState<Chapter>()
  const [range, setRange] = useState({ start: 0, end: 0 })
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    setChapter(undefined)
    setRange({ start: 0, end: 0 })
    setError('')
    if (selected)
      void api<Chapter>(`/chapters/${encodeURIComponent(selected)}`)
        .then((row) => {
          if (active) setChapter(row)
        })
        .catch((failure: Error) => {
          if (active) setError(failure.message)
        })
    return () => {
      active = false
    }
  }, [selected])
  const current =
    chapter?.id === selected && chapters.some((row) => row.id === selected) ? chapter : undefined
  const quote = current?.content.slice(range.start, range.end) ?? ''

  return (
    <details>
      <summary>添加正文依据</summary>
      <label>
        引用用于
        <select value={destination} onChange={(event) => setDestination(event.target.value as ClosureKey)}>
          {closureFields.map(([id, name]) => (
            <option key={id} value={id}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <label>
        完本引文章节
        <select value={selected} onChange={(event) => setSelected(event.target.value)}>
          <option value="">选择章节</option>
          {chapters.map(({ id, title }) => (
            <option key={id} value={id}>
              {title}
            </option>
          ))}
        </select>
      </label>
      {error && <p role="alert">{error}</p>}
      {current && (
        <>
          <label>
            保存稿修订 {current.revision}（选择要引用的文字）
            <textarea
              readOnly
              rows={5}
              value={current.content}
              onSelect={(event) => {
                const { selectionStart, selectionEnd } = event.currentTarget
                setRange({
                  start: originalOffset(current.content, selectionStart),
                  end: originalOffset(current.content, selectionEnd),
                })
              }}
            />
          </label>
          <button
            type="button"
            disabled={!quote.trim() || Array.from(quote).length > 4000}
            onClick={() =>
              onAdd(destination, { chapter_id: current.id, revision: current.revision, ...range, quote })
            }
          >
            引用选区
          </button>
        </>
      )}
    </details>
  )
}

export function CompletionPanel({
  branchId,
  chapters,
  notify,
}: {
  branchId: string
  chapters: ChapterOption[]
  notify: Notify
}) {
  const endpoint = `/branches/${encodeURIComponent(branchId)}/book-completion`
  const [current, setCurrent] = useState<Current>()
  const [form, setForm] = useState(freshClosures)
  const [readNote, setReadNote] = useState('')
  const [intro, setIntro] = useState('')
  const [spoilerScope, setSpoilerScope] = useState<'opening' | 'whole'>('opening')
  const [openingLength, setOpeningLength] = useState(1)
  const [preview, setPreview] = useState<{ signature: string; result: Preview }>()
  const [reviewed, setReviewed] = useState(false)
  const [reload, setReload] = useState(0)
  const [busy, setBusy] = useState<'preview' | 'confirm'>()
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const alive = useRef(true)
  useLeaveGuard(
    !!(
      readNote ||
      intro ||
      spoilerScope !== 'opening' ||
      openingLength !== 1 ||
      Object.values(form).some((entry) => entry.note || entry.sources.length)
    ),
    '未确认的完本说明与交付设置',
  )
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])
  useEffect(() => {
    let active = true
    void api<Current>(endpoint)
      .then((result) => {
        if (active) setCurrent(result)
      })
      .catch((failure: Error) => {
        if (active) setError(failure.message)
      })
    return () => {
      active = false
    }
  }, [endpoint, reload])

  const ordered = [...chapters].sort((left, right) => left.position - right.position)
  const titles = new Map(ordered.map(({ id, title }) => [id, title]))
  const sampleIds = ordered.slice(0, openingLength).map(({ id }) => id)
  const body = {
    basis_sha256: current?.basis_sha256,
    read_note: readNote.trim(),
    ...form,
    intro: intro.trim(),
    spoiler_scope: spoilerScope,
    sample_chapter_ids: sampleIds,
  }
  const signature = JSON.stringify(body)
  const readyPreview = preview?.signature === signature ? preview.result : undefined
  const checks = readyPreview?.checks ?? current?.checks ?? []
  const history = current?.history?.filter((entry) => entry.id !== current.latest?.id) ?? []
  const canConfirm = !!(
    readyPreview?.materials &&
    reviewed &&
    readyPreview.checks.every((check) => check.id === 'delivery' || check.status === 'green')
  )

  function editClosure(id: ClosureKey, transform: (closure: Closure) => Closure) {
    setForm((before) => ({ ...before, [id]: transform(before[id]) }))
    setReviewed(false)
  }

  async function submit(action: 'preview' | 'confirm') {
    if (!current || busy || (action === 'confirm' && !canConfirm)) return
    setBusy(action)
    setError('')
    setMessage('')
    try {
      if (action === 'preview') {
        setReviewed(false)
        const result = await api<Preview>(`${endpoint}/preview`, 'POST', {
          ...body,
          materials_reviewed: false,
        })
        if (alive.current) setPreview({ signature, result })
      } else {
        const result = await api<Completion>(`${endpoint}/confirm`, 'POST', {
          ...body,
          materials_reviewed: true,
        })
        if (!alive.current) return
        setCurrent({ ...current, latest: result, checks: result.checks })
        setReload((count) => count + 1)
        setPreview(undefined)
        setReviewed(false)
        setForm(freshClosures())
        setReadNote('')
        setIntro('')
        setSpoilerScope('opening')
        setOpeningLength(1)
        setMessage('已确认完结，交付包已固定到本次稿件版本。')
      }
    } catch (failure) {
      if (!alive.current) return
      setError(failure instanceof Error ? failure.message : '完本操作失败，请重试。')
      notify(failure)
      if (failure instanceof ApiError && failure.status === 409) {
        setReviewed(false)
        setReload((count) => count + 1)
      }
    } finally {
      if (alive.current) setBusy(undefined)
    }
  }

  return (
    <section className="card" aria-label="完本与交付">
      <h2>完本与交付</h2>
      <p>通读当前稿件，核对六项判定后标记完结，并导出与本次判定对应的交付包。</p>
      <button
        type="button"
        disabled={!!busy}
        onClick={() => {
          setError('')
          setReload((count) => count + 1)
        }}
      >
        核对最新完本依据
      </button>
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      {!current && !error && <p role="status">正在读取完本依据…</p>}
      {current?.latest && (
        <section className="notice">
          <h3>{current.latest.current ? '当前版本已完结' : '历史完结记录 · 当前依据已变化'}</h3>
          <a className="button" href={`/api${endpoint}/${encodeURIComponent(current.latest.id)}/export`}>
            下载{current.latest.current ? '' : '历史'}交付包
          </a>
          <details>
            <summary>完结判定与文件校验值</summary>
            <CheckList checks={current.latest.checks} />
            <ManifestList manifest={current.latest.manifest} />
          </details>
        </section>
      )}
      {history.length > 0 && (
        <details>
          <summary>历次交付包（{history.length}）</summary>
          <ul>
            {history.map(({ id, current: stillCurrent, created }) => (
              <li key={id}>
                <a href={`/api${endpoint}/${encodeURIComponent(id)}/export`}>
                  下载 {new Date(created * 1000).toLocaleString('zh-CN')} 的交付包
                </a>
                {' · '}
                {stillCurrent ? '依据仍有效' : '旧依据'}
              </li>
            ))}
          </ul>
        </details>
      )}
      {!!checks.length && <CheckList checks={checks} />}
      <fieldset disabled={!current || !!busy}>
        <legend>作者通读与收束说明</legend>
        <label>
          通读记录
          <textarea
            value={readNote}
            onChange={(event) => {
              setReadNote(event.target.value)
              setReviewed(false)
            }}
            rows={3}
            maxLength={4000}
          />
        </label>
        {closureFields.map(([id, title]) => (
          <section aria-label={title} key={id}>
            <label>
              {title}说明
              <textarea
                value={form[id].note}
                maxLength={4000}
                rows={3}
                onChange={(event) => editClosure(id, (entry) => ({ ...entry, note: event.target.value }))}
              />
            </label>
            {form[id].sources.map((source, index) => (
              <blockquote key={index}>
                {source.quote}
                <footer>
                  {titles.get(source.chapter_id) || source.chapter_id} · 修订 {source.revision}
                </footer>
                <button
                  type="button"
                  onClick={() =>
                    editClosure(id, (entry) => ({
                      ...entry,
                      sources: entry.sources.filter((_, position) => position !== index),
                    }))
                  }
                >
                  移除此引文
                </button>
              </blockquote>
            ))}
          </section>
        ))}
        <EvidencePicker
          chapters={ordered}
          onAdd={(id, source) =>
            editClosure(id, (entry) => {
              const duplicate = entry.sources.some(
                (row) =>
                  row.chapter_id === source.chapter_id &&
                  row.revision === source.revision &&
                  row.start === source.start &&
                  row.end === source.end,
              )
              return duplicate || entry.sources.length >= 30
                ? entry
                : { ...entry, sources: [...entry.sources, source] }
            })
          }
        />
      </fieldset>
      <fieldset disabled={!current || !!busy}>
        <legend>交付材料</legend>
        <label>
          简介
          <textarea
            value={intro}
            onChange={(event) => {
              setIntro(event.target.value)
              setReviewed(false)
            }}
            rows={4}
            maxLength={4000}
          />
        </label>
        <label>
          简介与试读的剧透范围
          <select
            value={spoilerScope}
            onChange={(event) => {
              setSpoilerScope(event.target.value as 'opening' | 'whole')
              setReviewed(false)
            }}
          >
            <option value="opening">开篇范围</option>
            <option value="whole">允许全书剧透</option>
          </select>
        </label>
        <label>
          从首章起选入的试读章数
          <select
            value={Math.min(ordered.length, openingLength)}
            onChange={(event) => {
              setOpeningLength(Number(event.target.value))
              setReviewed(false)
            }}
          >
            {ordered.slice(0, 20).map(({ id, position, title }, index) => (
              <option key={id} value={index + 1}>
                {index + 1} 章 · 截至第 {position} 章 {title}
              </option>
            ))}
          </select>
        </label>
        <button type="button" onClick={() => void submit('preview')}>
          {busy === 'preview' ? '正在生成预览…' : '预览交付材料'}
        </button>
      </fieldset>
      {preview && !readyPreview && <p role="status">说明或依据已有修改，请重新预览交付材料。</p>}
      {readyPreview && !readyPreview.materials && <p>请填写简介并选择有保存稿的试读章，生成交付预览。</p>}
      {readyPreview?.materials && (
        <section aria-label="交付预览">
          <h3>交付预览</h3>
          {Object.entries(readyPreview.materials).map(([name, content]) => (
            <details key={name}>
              <summary>{name}</summary>
              <pre className="report">{content}</pre>
            </details>
          ))}
          <details>
            <summary>交付文件校验值</summary>
            <ManifestList manifest={readyPreview.manifest} />
          </details>
          <label className="check">
            <input
              type="checkbox"
              checked={reviewed}
              disabled={!!busy}
              onChange={(event) => setReviewed(event.target.checked)}
            />
            已核对这次交付材料与剧透范围
          </label>
          <button
            className="primary"
            type="button"
            disabled={!canConfirm || !!busy}
            onClick={() => void submit('confirm')}
          >
            {busy === 'confirm' ? '正在确认…' : '确认完结'}
          </button>
        </section>
      )}
    </section>
  )
}
