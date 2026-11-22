import { useEffect, useState } from 'react'
import { api, type Chapter } from './api'
import type { components } from './api-schema'

type Schema = components['schemas']
type CardSource = Schema['CardSource']
type Pin = Schema['TechniqueReference']
type Passage = Schema['Passage']

const writingActions = new Set(['writing.advise', 'writing.rewrite_selection', 'writing.expand_plot'])
export const supportsTechniqueReferences = (action: string) => writingActions.has(action)

function referencesPath(branchId: string, chapterId: string) {
  return `/branches/${branchId}/chapters/${chapterId}/technique-references`
}

function useSource(path: string) {
  const [state, setState] = useState<{ path: string; value?: CardSource; error?: string }>()
  const [loading, setLoading] = useState(false)
  const current = state?.path === path ? state : undefined
  async function locate() {
    setLoading(true)
    setState(undefined)
    try {
      setState({ path, value: await api<CardSource>(path) })
    } catch (error) {
      setState({ path, error: error instanceof Error ? error.message : '原文暂不可用。' })
    } finally {
      setLoading(false)
    }
  }
  return { current, loading, locate }
}

function OriginalPassage({ passage }: { passage: Passage }) {
  return (
    <blockquote className="technique-source" aria-label="定位的卡片原文">
      {passage.before}
      <mark>{passage.quote}</mark>
      {passage.after}
    </blockquote>
  )
}

function SourceLabel({ unit }: { unit: Schema['SourceUnit'] }) {
  return (
    <p className="small muted" title={unit.file_hash}>
      {unit.title} · {unit.unit_title} · 导入版本 {unit.file_hash.slice(0, 12)}
    </p>
  )
}

export function TechniqueCardPin({
  sessionId,
  revision,
  cardId,
  branchId,
  branchLabel,
  chapters,
  ready,
  replacement,
}: {
  sessionId: string
  revision: number
  cardId: string
  branchId?: string
  branchLabel?: string
  chapters: Chapter[]
  ready: boolean
  replacement: string
}) {
  const path = `/reference-sessions/${sessionId}/cards/${cardId}/source?revision=${revision}`
  const source = useSource(path)
  const [start, setStart] = useState<number>()
  const [chapterId, setChapterId] = useState('')
  const [applicationNote, setApplicationNote] = useState('')
  const [pending, setPending] = useState(false)
  const [receipt, setReceipt] = useState<Pin>()
  const [error, setError] = useState('')
  const value = source.current?.value
  const choice = value?.passages.length === 1 ? value.passages[0].start : start
  async function pin(event: React.FormEvent) {
    event.preventDefault()
    if (!branchId || !chapterId || !ready || pending || choice === undefined) return
    setPending(true)
    setReceipt(undefined)
    setError('')
    try {
      setReceipt(
        await api<Pin>(referencesPath(branchId, chapterId), 'POST', {
          session_id: sessionId,
          session_revision: revision,
          card_id: cardId,
          evidence_start: choice,
          application_note: applicationNote.trim(),
        }),
      )
    } catch (error) {
      setError(error instanceof Error ? error.message : '引用尚未保存，可重新提交。')
    } finally {
      setPending(false)
    }
  }
  return (
    <div className="technique-pin">
      <button type="button" onClick={() => void source.locate()} disabled={!ready || source.loading}>
        {source.loading ? '正在定位原文…' : '定位卡片原文'}
      </button>
      {source.current?.error && <p role="alert">{source.current.error}</p>}
      {value && (
        <>
          <SourceLabel unit={value.unit} />
          {value.passages.length === 1 ? (
            <OriginalPassage passage={value.passages[0]} />
          ) : (
            <fieldset>
              <legend>引文重复，请选定原文位置</legend>
              {value.passages.map((passage) => (
                <div key={passage.start}>
                  <label className="check">
                    <input
                      type="radio"
                      name={`technique-source-${cardId}`}
                      checked={start === passage.start}
                      onChange={() => setStart(passage.start)}
                    />
                    选用原文位置 {passage.start}–{passage.end}
                  </label>
                  <OriginalPassage passage={passage} />
                </div>
              ))}
              {value.truncated && <p className="small">还有重复位置，可在卡片中补长引文。</p>}
            </fieldset>
          )}
          <form onSubmit={(event) => void pin(event)}>
            {branchLabel && <p className="small muted">引用工作版本 · {branchLabel}</p>}
            <label>
              选择引用章节
              <select value={chapterId} onChange={(event) => setChapterId(event.target.value)} required>
                <option value="">选择当前工作版本的章节</option>
                {chapters.map((chapter) => (
                  <option value={chapter.id} key={chapter.id}>
                    {chapter.position} · {chapter.title}
                  </option>
                ))}
              </select>
            </label>
            <label>
              本章怎么用（可选）
              <textarea
                value={applicationNote}
                onChange={(event) => setApplicationNote(event.target.value)}
                rows={2}
                maxLength={1000}
              />
            </label>
            {!replacement.trim() && (
              <p className="small">保存须替换的内容后即可引用。确无替换项时，请写明理由。</p>
            )}
            {!chapters.length && <p className="small">请先在当前作品新建或导入目标章节。</p>}
            <button
              type="submit"
              disabled={!ready || pending || !chapterId || choice === undefined || !replacement.trim()}
            >
              {pending ? '正在保存章节引用…' : '引用到所选章节'}
            </button>
          </form>
        </>
      )}
      {!ready && <p className="small muted">保存这版手法卡后可定位并指定引用章节。</p>}
      {error && <p role="alert">{error}</p>}
      {receipt && (
        <p role="status">
          已固定手法卡修订 {receipt.session_revision} ·{' '}
          {chapters.find((chapter) => chapter.id === receipt.chapter_id)?.title}
        </p>
      )}
    </div>
  )
}

function PinnedSource({ path, pin }: { path: string; pin: Pin }) {
  const source = useSource(`${path}/${pin.id}/source`)
  return (
    <>
      <SourceLabel unit={pin.unit} />
      <details>
        <summary>固定版本的原文引文</summary>
        <blockquote>{pin.card.evidence}</blockquote>
      </details>
      <button type="button" onClick={() => void source.locate()} disabled={source.loading}>
        {source.loading ? '正在定位原文…' : '定位卡片原文'}
      </button>
      {source.current?.error && <p role="alert">{source.current.error}</p>}
      {source.current?.value?.passages.map((passage) => (
        <OriginalPassage key={passage.start} passage={passage} />
      ))}
    </>
  )
}

export function ChapterTechniqueReferences({
  branchId,
  chapterId,
  selected,
  enabled,
  disabled,
  onChange,
}: {
  branchId: string
  chapterId: string
  selected: string[]
  enabled: boolean
  disabled: boolean
  onChange: (ids: string[]) => void
}) {
  const path = referencesPath(branchId, chapterId)
  const [result, setResult] = useState<{ items?: Pin[]; error?: string }>()
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    let active = true
    void api<Pin[]>(path)
      .then((items) => {
        if (active) setResult({ items })
      })
      .catch((error) => {
        if (active)
          setResult((old) => ({
            ...old,
            error: error instanceof Error ? error.message : '手法引用读取失败。',
          }))
      })
    return () => {
      active = false
    }
  }, [path, refresh])
  useEffect(() => {
    const reload = () => setRefresh((value) => value + 1)
    window.addEventListener('focus', reload)
    return () => window.removeEventListener('focus', reload)
  }, [])
  if (!result?.items?.length && !result?.error) return null

  return (
    <section className="notice writing-workbench-section" aria-label="本章引用手法">
      <h3>本章引用手法</h3>
      <p className="small muted">
        {enabled
          ? '勾选后，本次写作辅助只额外发送写法、适用条件与例外、替换要求和本章用法。完整引文留在本机供查阅。'
          : '可查阅固定版本，写作建议、选区改写和按情节补写时可逐项选用。'}
      </p>
      {result?.error && <p role="alert">{result.error}</p>}
      {result?.items?.map((pin) => (
        <article className="fact" aria-label={pin.card.technique} key={pin.id}>
          <strong>{pin.card.technique}</strong>
          <p className="small muted">
            固定手法卡修订 {pin.session_revision}
            {pin.current_session_revision !== pin.session_revision && ' · 历史引用'}
          </p>
          <label className="check">
            <input
              type="checkbox"
              checked={enabled && selected.includes(pin.id)}
              disabled={disabled || !enabled || (!selected.includes(pin.id) && selected.length >= 8)}
              onChange={(event) =>
                onChange(
                  event.target.checked ? [...selected, pin.id] : selected.filter((id) => id !== pin.id),
                )
              }
            />
            本次写作使用此卡
          </label>
          <details>
            <summary>适用条件与须替换的内容</summary>
            <p>{pin.card.conditions}</p>
            {pin.card.exceptions && <p>{pin.card.exceptions}</p>}
            <p>{pin.card.replacement}</p>
            {pin.application_note && <p>本章用法 · {pin.application_note}</p>}
          </details>
          <PinnedSource path={path} pin={pin} />
        </article>
      ))}
    </section>
  )
}
