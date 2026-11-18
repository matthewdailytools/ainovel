import { useEffect, useState } from 'react'
import { api } from './api'
import { storedTextOffset } from './inspectionEvidence'

type ReadingSelection = { start: number; end: number; quote: string }
type ExcerptRole = 'opening' | 'ending'
type Excerpts = Partial<Record<ExcerptRole, ReadingSelection>>
export type PublishedChapter = {
  chapter_id: string
  published_at: string
  opens_at?: string | null
  title_at_import?: string | null
  release_position?: number | null
  published_text?:
    ({ status: 'verified' | 'unavailable'; revision: number; revision_id: string } & Excerpts) | null
}
export type ReadingPublicationBinding = Excerpts & { chapter_id: string; revision_id: string; sha256: string }

type Revision = { id: string; revision: number; content: string; sha256: string }
type RevisionChoice = Pick<Revision, 'id' | 'revision'> & { cause: string; created: number }
const pageLength = 50
const excerptTitles = { opening: '开篇', ending: '章末' }

export function ReadingPublishedExcerpt({
  chapter,
  title,
  role,
}: {
  chapter: PublishedChapter | undefined
  title: string
  role: ExcerptRole
}) {
  const source = chapter?.published_text
  const label = excerptTitles[role]
  const excerpt = source?.[role]
  const missing =
    source?.status === 'unavailable'
      ? '原保存稿无法核实。'
      : source
        ? '该发布稿尚无此处选区。'
        : '此版本尚未绑定发布章稿。'

  return excerpt && source ? (
    <details>
      <summary>
        查看{title}
        {label}
        {source.status === 'verified' ? '原文' : '保存的选区'}
      </summary>
      <p className="muted">
        {source.status === 'verified' ? '作者绑定的发布稿' : '原保存稿无法核实，以下为绑定时封存的选区'}
        {' · '}修订 {source.revision} · 选区 {excerpt.start}–{excerpt.end}
      </p>
      <blockquote style={{ whiteSpace: 'pre-wrap' }}>{excerpt.quote}</blockquote>
    </details>
  ) : (
    <p>
      {title}
      {label} · {missing}
    </p>
  )
}

function chosenText(content: string, start: number, end: number) {
  const originalStart = storedTextOffset(content, start)
  const originalEnd = storedTextOffset(content, end)
  const quote = content.slice(originalStart, originalEnd)
  return quote.trim() && !/\p{Surrogate}/u.test(quote) && Array.from(quote).length <= 4000
    ? { start: originalStart, end: originalEnd, quote }
    : undefined
}

function RevisionBindingForm({
  chapter,
  route,
  busy,
  onSave,
}: {
  chapter: PublishedChapter
  route: string
  busy: boolean
  onSave: (binding: ReadingPublicationBinding) => Promise<void>
}) {
  const chapterRoute = `${route}/chapters/${encodeURIComponent(chapter.chapter_id)}/revisions`
  const [choices, setChoices] = useState<RevisionChoice[]>([])
  const [before, setBefore] = useState<number>()
  const [more, setMore] = useState(false)
  const [revisionId, setRevisionId] = useState('')
  const [revision, setRevision] = useState<Revision>()
  const [listing, setListing] = useState(true)
  const [listRetry, setListRetry] = useState(0)
  const [reading, setReading] = useState(false)
  const [readRetry, setReadRetry] = useState(0)
  const [listError, setListError] = useState('')
  const [readError, setReadError] = useState('')
  const [saveError, setSaveError] = useState('')
  const [selection, setSelection] = useState<ReadingSelection>()
  const [excerpts, setExcerpts] = useState<Excerpts>({})
  const [confirmed, setConfirmed] = useState(false)

  useEffect(() => {
    let active = true
    setListing(true)
    setListError('')
    void api<RevisionChoice[]>(`${chapterRoute}${before === undefined ? '' : `?before_revision=${before}`}`)
      .then((rows) => {
        if (!active) return
        setMore(rows.length === pageLength && (rows.at(-1)?.revision ?? 0) > 0)
        setChoices((previous) =>
          before === undefined ? rows : [...previous.filter((row) => row.revision >= before), ...rows],
        )
      })
      .catch((error: unknown) => {
        if (active) setListError(error instanceof Error ? error.message : '无法读取章稿历史。')
      })
      .finally(() => {
        if (active) setListing(false)
      })
    return () => {
      active = false
    }
  }, [chapterRoute, before, listRetry])

  useEffect(() => {
    if (!revisionId) return
    let active = true
    setReading(true)
    setReadError('')
    void api<Revision>(`${chapterRoute}/${encodeURIComponent(revisionId)}`)
      .then((data) => {
        if (active) setRevision(data)
      })
      .catch((error: unknown) => {
        if (active) setReadError(error instanceof Error ? error.message : '无法读取所选修订。')
      })
      .finally(() => {
        if (active) setReading(false)
      })
    return () => {
      active = false
    }
  }, [chapterRoute, revisionId, readRetry])

  const activeRevision = revision?.id === revisionId ? revision : undefined
  function markSelection(role: ExcerptRole) {
    if (!selection) return
    setExcerpts({ ...excerpts, [role]: selection })
    setConfirmed(false)
    setSaveError('')
  }
  async function save() {
    if (busy || !activeRevision || !confirmed || !(excerpts.opening || excerpts.ending)) return
    setSaveError('')
    try {
      await onSave({
        chapter_id: chapter.chapter_id,
        revision_id: activeRevision.id,
        sha256: activeRevision.sha256,
        ...excerpts,
      })
    } catch (error: unknown) {
      setSaveError(error instanceof Error ? error.message : '发布章稿绑定失败。')
    }
  }

  return (
    <fieldset disabled={busy}>
      <legend>选择章稿修订和发布选区</legend>
      <label>
        保存稿修订
        <select
          value={revisionId}
          onChange={(event) => {
            setRevisionId(event.target.value)
            setReadError('')
            setSaveError('')
            setSelection(undefined)
            setExcerpts({})
            setConfirmed(false)
          }}
        >
          <option value="">选择已发布的修订</option>
          {choices.map(({ id, revision, created, cause }) => (
            <option key={id} value={id}>
              修订 {revision} · {cause} · {new Date(created * 1000).toLocaleString('zh-CN')}
            </option>
          ))}
        </select>
      </label>
      {listing && <p role="status">正在读取修订目录…</p>}
      {listError && (
        <p role="alert">
          {listError}{' '}
          <button type="button" onClick={() => setListRetry(listRetry + 1)}>
            重读修订目录
          </button>
        </p>
      )}
      {more && !listError && (
        <button type="button" disabled={listing} onClick={() => setBefore(choices.at(-1)?.revision)}>
          读取更早修订
        </button>
      )}
      {!listing && !listError && !choices.length && <p>本章没有可用的保存稿修订。</p>}
      {reading && revisionId && <p role="status">正在读取保存稿…</p>}
      {readError && (
        <p role="alert">
          {readError}{' '}
          <button type="button" onClick={() => setReadRetry(readRetry + 1)}>
            重读保存稿
          </button>
        </p>
      )}
      {activeRevision && (
        <>
          <label>
            选择发布稿原文
            <textarea
              readOnly
              rows={7}
              value={activeRevision.content.replace(/\r\n?/g, '\n')}
              onSelect={(event) => {
                const { selectionStart, selectionEnd } = event.currentTarget
                setSelection(chosenText(activeRevision.content, selectionStart, selectionEnd))
              }}
            />
          </label>
          <p>可选开篇、章末或两处原文。每处最多 4,000 字符。</p>
          <div className="actions">
            <button type="button" disabled={!selection} onClick={() => markSelection('opening')}>
              设为开篇选区
            </button>
            <button type="button" disabled={!selection} onClick={() => markSelection('ending')}>
              设为章末选区
            </button>
          </div>
          <dl>
            {(['opening', 'ending'] as const).map(
              (role) =>
                excerpts[role] && (
                  <div key={role}>
                    <dt>{excerptTitles[role]}选区</dt>
                    <dd style={{ whiteSpace: 'pre-wrap' }}>{excerpts[role]?.quote}</dd>
                  </div>
                ),
            )}
          </dl>
          {chapter.published_text && chapter.published_text.revision_id !== activeRevision.id && (
            <p>选用新修订会生成新的报告版本。旧选区仍可在原报告中查看。</p>
          )}
          <label className="check">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(event) => setConfirmed(event.target.checked)}
            />
            我确认此章稿与报告所列发布版本一致
          </label>
          <button
            type="button"
            disabled={!confirmed || !(excerpts.opening || excerpts.ending)}
            onClick={() => void save()}
          >
            {busy ? '正在保存…' : '保存发布出处'}
          </button>
          {saveError && <p role="alert">{saveError}</p>}
        </>
      )}
    </fieldset>
  )
}

export function ReadingPublicationEditor({
  chapters,
  title,
  route,
  busy,
  onSave,
}: {
  chapters: PublishedChapter[]
  title: (id: string) => string
  route: string
  busy: boolean
  onSave: (binding: ReadingPublicationBinding) => Promise<void>
}) {
  const [chapterId, setChapterId] = useState('')
  const chapter = chapters.find((row) => row.chapter_id === chapterId)
  return (
    <details>
      <summary>绑定发布章稿</summary>
      <p>从本作品的保存稿中选择能对应本报告的发布修订。统计指标沿用原数据。</p>
      <label>
        发布清单中的章节
        <select value={chapterId} disabled={busy} onChange={(event) => setChapterId(event.target.value)}>
          <option value="">选择章节</option>
          {chapters.map((row, position) => (
            <option key={row.chapter_id} value={row.chapter_id}>
              {row.release_position ?? position + 1}. {title(row.chapter_id)}
            </option>
          ))}
        </select>
      </label>
      {chapter && (
        <>
          <section aria-label="所选章的已保存出处">
            <ReadingPublishedExcerpt chapter={chapter} title={title(chapterId)} role="opening" />
            <ReadingPublishedExcerpt chapter={chapter} title={title(chapterId)} role="ending" />
          </section>
          <RevisionBindingForm key={chapterId} chapter={chapter} route={route} busy={busy} onSave={onSave} />
        </>
      )}
    </details>
  )
}
