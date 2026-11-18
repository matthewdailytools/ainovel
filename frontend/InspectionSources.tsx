import { useRef, useState } from 'react'
import { api } from './api'
import type { components } from './api-schema'

type EvidencePage = components['schemas']['InspectionEvidencePage']
type EvidenceContext = components['schemas']['InspectionEvidenceContext']

function SourceOriginal({ route, source }: { route: string; source: EvidencePage['entries'][number] }) {
  const [context, setContext] = useState<EvidenceContext>()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const reading = useRef(false)

  async function load() {
    if (reading.current) return
    reading.current = true
    setBusy(true)
    setError('')
    try {
      const result = await api<EvidencePage>(`${route}?source_index=${source.index}`)
      if (!result.context) throw new Error('此处原文依据暂不可读。')
      setContext(result.context)
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '无法读取体检原文。')
    } finally {
      reading.current = false
      setBusy(false)
    }
  }

  const excerptLabel = `${source.chapter_title}原文 · 修订 ${source.revision} · 位置 ${source.start}–${source.end}`
  return (
    <details
      onToggle={(event) => {
        if (event.currentTarget.open && !context && !error) void load()
      }}
    >
      <summary>打开{excerptLabel}</summary>
      <p className="small">{source.label}</p>
      {busy && <p role="status">正在读取原文依据…</p>}
      {error && (
        <>
          <p role="alert">{error}</p>
          <button type="button" onClick={() => void load()}>
            重试读取这处原文
          </button>
        </>
      )}
      {context && (
        <section aria-label={excerptLabel}>
          <pre className="report">
            {context.before}
            <mark>{context.quote}</mark>
            {context.after}
          </pre>
        </section>
      )}
    </details>
  )
}

export function InspectionSources({ route }: { route: string }) {
  const [page, setPage] = useState<EvidencePage>()
  const [offset, setOffset] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const reading = useRef(false)

  async function load(nextOffset: number) {
    if (reading.current) return
    reading.current = true
    setBusy(true)
    setError('')
    try {
      const result = await api<EvidencePage>(`${route}?offset=${nextOffset}`)
      setPage(result)
      setOffset(nextOffset)
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '无法读取体检原文。')
    } finally {
      reading.current = false
      setBusy(false)
    }
  }

  return (
    <details
      onToggle={(event) => {
        if (event.currentTarget.open && !page && !error) void load(0)
      }}
    >
      <summary>查看体检原文</summary>
      {busy && <p role="status">正在读取原文依据…</p>}
      {error && <p role="alert">{error}</p>}
      {!page && error && (
        <button type="button" onClick={() => void load(0)}>
          重试读取原文依据
        </button>
      )}
      {page && !page.total && <p>此项没有固定的正文选区。</p>}
      {page && page.total > 0 && (
        <>
          <ul>
            {page.entries.map((source) => (
              <li key={source.index}>
                <SourceOriginal route={route} source={source} />
              </li>
            ))}
          </ul>
          {page.previous_offset != null && (
            <button type="button" disabled={busy} onClick={() => void load(page.previous_offset ?? 0)}>
              上一组原文
            </button>
          )}
          {offset + page.entries.length < page.total && (
            <button type="button" disabled={busy} onClick={() => void load(offset + page.entries.length)}>
              下一组原文
            </button>
          )}
        </>
      )}
    </details>
  )
}
