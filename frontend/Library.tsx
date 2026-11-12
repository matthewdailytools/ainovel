import { useEffect, useRef, useState } from 'react'
import { api, label, type Book, type Excerpt, type Unit } from './api'
import type { Notify } from './App'

type Hit = {
  id: string
  book_id: string
  book_title: string
  title: string
  start: number
  end: number
  snippet: string
  file_hash: string
}
type Search = { hits: Hit[]; next_offset: number | null; matching: string; coverage: Book[] }
type Job = { id: string; title: string; status: string; error: string; book_id: string }
export function LibraryPage({ novelId, notify }: { novelId: string; notify: Notify }) {
  const [books, setBooks] = useState<Book[]>([])
  const [bookId, setBookId] = useState('')
  const [units, setUnits] = useState<Unit[]>([])
  const [unit, setUnit] = useState<Unit>()
  const [query, setQuery] = useState('')
  const [scope, setScope] = useState('all')
  const [search, setSearch] = useState<Search>()
  const [catalog, setCatalog] = useState<{ title: string; url: string; coverage: string; rights: string }[]>(
    [],
  )
  const [jobs, setJobs] = useState<Job[]>([])
  const [excerpts, setExcerpts] = useState<Excerpt[]>([])
  const [busy, setBusy] = useState(false)
  const [encoding, setEncoding] = useState('utf-8-sig')
  const [downloadFormat, setDownloadFormat] = useState('epub')
  const [note, setNote] = useState('')
  const [selection, setSelection] = useState({ start: 0, end: 0, text: '' })
  const [status, setStatus] = useState('')
  const reader = useRef<HTMLTextAreaElement>(null)
  const progress = useRef({ revision: 0 })
  async function refresh() {
    const [b, j, e] = await Promise.all([
      api<Book[]>('/library/books'),
      api<Job[]>('/library/jobs'),
      api<Excerpt[]>(`/library/excerpts${novelId ? '?novel_id=' + novelId : ''}`),
    ])
    setBooks(b)
    setJobs(j)
    setExcerpts(e)
  }
  useEffect(() => {
    let active = true
    const poll = async () => {
      try {
        const [b, j, e] = await Promise.all([
          api<Book[]>('/library/books'),
          api<Job[]>('/library/jobs'),
          api<Excerpt[]>(`/library/excerpts${novelId ? '?novel_id=' + novelId : ''}`),
        ])
        if (active) {
          setBooks(b)
          setJobs(j)
          setExcerpts(e)
        }
      } catch (e) {
        if (active) notify(e)
      }
    }
    void poll()
    const timer = setInterval(() => void poll(), 4000)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [novelId, notify])
  async function perform(fn: () => Promise<unknown>) {
    if (busy) return
    setBusy(true)
    try {
      await fn()
    } catch (e) {
      notify(e)
    } finally {
      setBusy(false)
    }
  }
  async function openUnit(id: string, start = 0, end = start) {
    const row = await api<Unit>(`/library/units/${id}`)
    if (row.book_id !== bookId) {
      setBookId(row.book_id)
      setUnits(await api(`/library/books/${row.book_id}/units`))
    }
    const state = await api<{ revision: number }>(`/library/books/${row.book_id}/progress`)
    progress.current.revision = state.revision
    setUnit(row)
    setSelection({ start, end, text: row.content.slice(start, end) })
    requestAnimationFrame(() => {
      if (reader.current) {
        reader.current.focus()
        reader.current.setSelectionRange(start, end)
        const lines = row.content.slice(0, start).split('\n').length
        reader.current.scrollTop = Math.max(0, (lines - 4) * 30)
      }
    })
    const next = await api<{ revision: number }>(
      `/library/books/${row.book_id}/progress?unit_id=${id}&offset=${start}&base_revision=${progress.current.revision}`,
      'PUT',
    )
    progress.current.revision = next.revision
  }
  async function openBook(id: string) {
    const [rows, state] = await Promise.all([
      api<Unit[]>(`/library/books/${id}/units`),
      api<{ unit_id: string | null; offset: number; revision: number }>(`/library/books/${id}/progress`),
    ])
    setBookId(id)
    setUnits(rows)
    setUnit(undefined)
    if (rows.length) await openUnit(rows.find((r) => r.id === state.unit_id)?.id || rows[0].id, state.offset)
  }
  async function runSearch(offset = 0) {
    setSearch(
      await api(
        `/library/search?q=${encodeURIComponent(query)}${scope === 'book' && bookId ? '&book_id=' + bookId : scope === 'novel' && novelId ? '&novel_id=' + novelId : ''}&offset=${offset}`,
      ),
    )
  }
  const book = books.find((b) => b.id === bookId)
  return (
    <div className="library-page page-pad">
      <div className="page-heading">
        <div>
          <span className="eyebrow">资料，回到原文</span>
          <h1>资料书库</h1>
          <p>找书、阅读、搜索，再把有出处的片段带回创作。</p>
        </div>
        <details className="card import-card">
          <summary>＋ 导入本地资料</summary>
          <label>
            文字编码
            <select value={encoding} onChange={(e) => setEncoding(e.target.value)}>
              <option value="utf-8-sig">UTF-8（默认）</option>
              <option value="gb18030">GB18030</option>
            </select>
          </label>
          <label className="file-control">
            选择 TXT／Markdown／EPUB
            <input
              type="file"
              accept=".txt,.md,.epub"
              onChange={(event) => {
                const file = event.target.files?.[0]
                if (!file) return
                void perform(async () => {
                  const form = new FormData()
                  form.append('file', file)
                  form.append('encoding', encoding)
                  const row = await api<Book>('/library/import', 'POST', form)
                  await refresh()
                  await openBook(row.id)
                  setStatus('资料已入库：' + label(row.status))
                })
                event.target.value = ''
              }}
            />
          </label>
          <p className="small muted">上限 20 MiB；原件保留，EPUB 以纯文本阅读。PDF／OCR 尚未实现。</p>
        </details>
      </div>
      <form
        className="search-bar"
        onSubmit={(event) => {
          event.preventDefault()
          void perform(() => runSearch())
        }}
      >
        <label className="grow">
          书名、主题或正文词语
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="例如：渡船、潮汐、契约"
            required
            maxLength={200}
          />
        </label>
        <label>
          正文范围
          <select value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="all">已入库资料</option>
            <option value="book" disabled={!bookId}>
              当前书籍
            </option>
            <option value="novel" disabled={!novelId}>
              本作品资料包来源
            </option>
          </select>
        </label>
        <button className="primary" disabled={busy}>
          搜索正文
        </button>
        <button
          type="button"
          disabled={busy || !query}
          onClick={() =>
            void perform(async () => {
              setCatalog(await api(`/library/catalog?q=${encodeURIComponent(query)}`))
              setStatus('找到的是书目，下载解析后才可搜索正文。')
            })
          }
        >
          查找维基文库书目
        </button>
      </form>
      <p className="small muted">
        短词采用精确子串检索；三字及以上先检索可用 trigram 索引，再校验连续原文。每个正文单元显示首个命中；TXT
        单元相邻重叠 200 字符，边界命中可能重复。
      </p>
      <p role="status">{busy ? '处理中…' : status}</p>
      {!!catalog.length && (
        <section className="card">
          <h2>外部书目</h2>
          {catalog.map((c) => (
            <div className="catalog-row" key={c.title}>
              <div>
                <a href={c.url} target="_blank" rel="noreferrer">
                  {c.title}
                </a>
                <small>
                  {c.coverage}；{c.rights}
                </small>
              </div>
              <button
                disabled={busy}
                onClick={() =>
                  void perform(async () => {
                    await api('/library/download', 'POST', { title: c.title })
                    await refresh()
                    setStatus('已提交官方导出通道下载任务。')
                  })
                }
              >
                获取 EPUB
              </button>
            </div>
          ))}
        </section>
      )}
      <details>
        <summary>按维基文库页面标题获取／查看下载任务</summary>
        <p className="small">
          使用官方 WS Export 或 MediaWiki
          API；网络或来源失败会保留失败状态，不声称已取得正文。单页面获取适合页面内已有全文的短书，多页面书籍需
          EPUB 或分别获取。
        </p>
        <label>
          获取范围
          <select value={downloadFormat} onChange={(e) => setDownloadFormat(e.target.value)}>
            <option value="epub">整书 EPUB 导出</option>
            <option value="page_text">当前单页面 TXT（不跟随子页面）</option>
          </select>
        </label>
        <button
          disabled={!query || busy}
          onClick={() =>
            void perform(async () => {
              await api('/library/download', 'POST', { title: query, format: downloadFormat })
              await refresh()
            })
          }
        >
          按当前输入的页面标题获取
        </button>
        {jobs.map((j) => (
          <div className="catalog-row" key={j.id}>
            <span>
              {j.title} · {label(j.status)}
              {j.error && <small>{j.error}</small>}
            </span>
            {['queued', 'downloading'].includes(j.status) ? (
              <button
                onClick={() =>
                  void perform(async () => {
                    await api(`/library/jobs/${j.id}/cancel`, 'POST')
                    await refresh()
                  })
                }
              >
                取消下载
              </button>
            ) : j.book_id ? (
              <button onClick={() => void perform(() => openBook(j.book_id))}>阅读</button>
            ) : null}
          </div>
        ))}
      </details>
      {search && (
        <section className="search-results">
          <div className="section-title">
            <h2>正文命中 · 本页 {search.hits.length} 项</h2>
            <span>{search.matching}</span>
          </div>
          {!search.hits.length && <p>已解析内容中未命中。解析失败与未导入内容不在本次检索范围内。</p>}
          <div className="result-grid">
            {search.hits.map((hit) => (
              <button
                className="hit"
                key={hit.id}
                onClick={() => void perform(() => openUnit(hit.id, hit.start, hit.end))}
              >
                <strong>{hit.book_title}</strong>
                <small>
                  {hit.title} · UTF-16 {hit.start}—{hit.end}
                </small>
                <span>{hit.snippet}</span>
                <small>查看来源原文 →</small>
              </button>
            ))}
          </div>
          {search.next_offset !== null && (
            <button onClick={() => void perform(() => runSearch(search.next_offset!))}>下一页</button>
          )}
          <details>
            <summary>实际检索覆盖范围</summary>
            {search.coverage.map((c) => (
              <p key={c.id}>
                {c.title}：{label(c.status)} · {c.coverage}
              </p>
            ))}
          </details>
        </section>
      )}
      <div className="reader-layout">
        <aside className="card">
          <h2>已入库 · {books.length}</h2>
          {books.length ? (
            books.map((b) => (
              <button
                className={'book-row ' + (b.id === bookId ? 'selected' : '')}
                key={b.id}
                onClick={() => void perform(() => openBook(b.id))}
              >
                <strong>{b.title}</strong>
                <small>{label(b.status)}</small>
              </button>
            ))
          ) : (
            <p className="muted">导入资料，或从维基文库获取可用书籍。</p>
          )}
          {!!units.length && (
            <label>
              阅读单元
              <select value={unit?.id || ''} onChange={(e) => void perform(() => openUnit(e.target.value))}>
                {units.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.position} · {u.title}
                  </option>
                ))}
              </select>
            </label>
          )}
        </aside>
        <section className="card reader">
          <div className="section-title">
            <h2>{book?.title || '打开一本资料书'}</h2>
            {unit && <span className="badge">原文 · 只读</span>}
          </div>
          {book && (
            <details>
              <summary>来源与处理范围 · {label(book.status)}</summary>
              <p>{book.source}</p>
              <p className="small">SHA-256：{book.file_hash}</p>
              <pre>{book.coverage}</pre>
            </details>
          )}
          {unit ? (
            <>
              <label>
                <span className="sr-only">资料原文</span>
                <textarea
                  ref={reader}
                  className="source-text"
                  value={unit.content}
                  readOnly
                  onSelect={(event) => {
                    const input = event.currentTarget
                    setSelection({
                      start: input.selectionStart,
                      end: input.selectionEnd,
                      text: input.value.slice(input.selectionStart, input.selectionEnd),
                    })
                  }}
                />
              </label>
              <div className="excerpt-form">
                <label>
                  摘录笔记
                  <textarea
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    rows={2}
                    placeholder="选中原文后，记录它对当前创作的用途"
                  />
                </label>
                <p className="small">
                  已选 {selection.text.length} 个 UTF-16 单元；资料不会自动变成小说事实。
                </p>
                <button
                  className="primary"
                  disabled={!selection.text || busy}
                  onClick={() =>
                    void perform(async () => {
                      await api('/library/excerpts', 'POST', {
                        unit_id: unit.id,
                        ...selection,
                        quote: selection.text,
                        text: undefined,
                        note,
                        novel_id: novelId || null,
                      })
                      await refresh()
                      setNote('')
                      setStatus('摘录已保存，可在写作页主动选入资料包。')
                    })
                  }
                >
                  保存摘录与出处
                </button>
                <button
                  disabled={busy}
                  onClick={() =>
                    void perform(async () => {
                      const result = await api<{ revision: number }>(
                        `/library/books/${unit.book_id}/progress?unit_id=${unit.id}&offset=${selection.end}&base_revision=${progress.current.revision}`,
                        'PUT',
                      )
                      progress.current.revision = result.revision
                      setStatus('阅读位置已保存。')
                    })
                  }
                >
                  记住阅读位置
                </button>
              </div>
            </>
          ) : (
            <p className="empty-note">
              资料原件与创作稿分别保存。部分解析失败的书籍仍可阅读已经成功解析的单元。
            </p>
          )}
        </section>
        <aside className="card">
          <h2>当前资料包</h2>
          {excerpts.length ? (
            excerpts.map((e) => (
              <div className="excerpt" key={e.id}>
                <button className="text-button" onClick={() => void perform(() => openUnit(e.unit_id))}>
                  {e.title} ↗ 原文
                </button>
                <blockquote>{e.quote}</blockquote>
                <p>{e.note}</p>
              </div>
            ))
          ) : (
            <p className="muted">选中原文、记录用途并保存。发送给模型前可逐项选择。</p>
          )}
        </aside>
      </div>
    </div>
  )
}
