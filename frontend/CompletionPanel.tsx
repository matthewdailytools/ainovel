import { useEffect, useRef, useState } from 'react'
import { AssistantField } from './AssistantField'
import { api, ApiError, type Chapter } from './api'
import type { Notify } from './App'
import {
  closureFields,
  CompletionReviewEvidence,
  type AuthorDecisions,
  type Closure,
  type ClosureDecision,
  type ClosureKey,
  type CompletionReview,
} from './CompletionReviewEvidence'
import { storedTextOffset } from './inspectionEvidence'
import { useLeaveGuard } from './leaveGuard'

type ChapterOption = Pick<Chapter, 'id' | 'title' | 'position'>
type Source = { chapter_id: string; revision: number; start: number; end: number; quote: string }
type Check = { id: string; title: string; status: 'green' | 'pending' | 'red'; detail: string }
type Manifest = { files?: Record<string, string>; chapters?: { id: string; title: string }[] }
type Completion = {
  id: string
  current: boolean
  checks: Check[]
  manifest: Manifest
  review?: CompletionReview
}
type Current = {
  basis_sha256: string
  latest: Completion | null
  checks: Check[]
  history?: {
    id: string
    current: boolean
    created: number
    origin?: 'manuscript_package' | 'cloud'
    source_device_id?: string
  }[]
}
type Preview = { checks: Check[]; manifest: Manifest; materials: Record<string, string> | null }
type Preparation = {
  id: string
  status: string
  error: string
  artifact_id: string | null
  candidate?: {
    current: boolean
    closures: Record<ClosureKey, Closure>
    intro: string
    intro_coverage: string
    coverage: string
    spoiler_scope: 'opening' | 'whole'
    sample_chapter_ids: string[]
  }
}
type PreparedIntroduction = Pick<
  NonNullable<Preparation['candidate']>,
  'intro' | 'spoiler_scope' | 'sample_chapter_ids'
> & { id?: string }

const pendingDecisions: AuthorDecisions = {
  reading: 'pending',
  ending: 'pending',
  characters: 'pending',
  promises: 'pending',
}
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

function CheckList({ checks, label }: { checks: Check[]; label: string }) {
  return (
    <ol className="fact-grid" aria-label={label}>
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

function frozenTitles(manifest: Manifest) {
  return new Map(manifest.chapters?.map(({ id, title }) => [id, title]))
}

function CompletionHistoryReview({ route }: { route: string }) {
  const [open, setOpen] = useState(false)
  const [history, setHistory] = useState<{ review: CompletionReview; manifest: Manifest }>()
  const [error, setError] = useState('')
  useEffect(() => {
    if (!open || history) return
    let active = true
    void api<{ review: CompletionReview; manifest: Manifest }>(route).then(
      (data) => {
        if (active) {
          setHistory(data)
          setError('')
        }
      },
      (failure: unknown) => {
        if (active) setError(failure instanceof Error ? failure.message : '无法读取原裁定。')
      },
    )
    return () => {
      active = false
    }
  }, [open, route, history])
  return (
    <details
      onToggle={(event) => {
        setOpen(event.currentTarget.open)
        setError('')
      }}
    >
      <summary>查看这次原裁定</summary>
      {error && <p role="alert">{error} 可合上后重新打开。</p>}
      {open && !history && !error && <p role="status">正在读取原裁定…</p>}
      {history && (
        <CompletionReviewEvidence review={history.review} titles={frozenTitles(history.manifest)} />
      )}
    </details>
  )
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
              value={current.content.replace(/\r\n?/g, '\n')}
              onSelect={(event) => {
                const { selectionStart, selectionEnd } = event.currentTarget
                setRange({
                  start: storedTextOffset(current.content, selectionStart),
                  end: storedTextOffset(current.content, selectionEnd),
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
  account,
  modelReady,
}: {
  branchId: string
  chapters: ChapterOption[] | undefined
  notify: Notify
  account: string
  modelReady: boolean
}) {
  const endpoint = `/branches/${encodeURIComponent(branchId)}/book-completion`
  const [current, setCurrent] = useState<Current>()
  const [form, setForm] = useState(freshClosures)
  const [readNote, setReadNote] = useState('')
  const [draftDecisions, setDraftDecisions] = useState<{ basis: string; decisions: AuthorDecisions }>()
  const [intro, setIntro] = useState('')
  const [spoilerScope, setSpoilerScope] = useState<'opening' | 'whole'>('opening')
  const [openingLength, setOpeningLength] = useState(1)
  const [preview, setPreview] = useState<{ signature: string; result: Preview; reviewedChecks?: Check[] }>()
  const [reviewed, setReviewed] = useState(false)
  const [reload, setReload] = useState(0)
  const [busy, setBusy] = useState<'preview' | 'review' | 'confirm'>()
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [preparation, setPreparation] = useState<Preparation | null>(null)
  const [closurePreparationId, setClosurePreparationId] = useState<string>()
  const [preparedIntroduction, setPreparedIntroduction] = useState<PreparedIntroduction>()
  const [preparing, setPreparing] = useState(false)
  const pendingPreparation = useRef<{ key: string; requestId: string } | undefined>(undefined)
  const alive = useRef(true)
  const decisions =
    draftDecisions?.basis === current?.basis_sha256
      ? (draftDecisions?.decisions ?? pendingDecisions)
      : pendingDecisions
  useLeaveGuard(
    !!(
      readNote ||
      intro ||
      spoilerScope !== 'opening' ||
      openingLength !== 1 ||
      Object.values(decisions).some((value) => value !== 'pending') ||
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
  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout>
    async function refreshPreparation() {
      try {
        const { task: result } = await api<{ task: Preparation | null }>(`${endpoint}/preparation`)
        if (!active) return
        setPreparation(result)
        if (result && ['queued', 'running'].includes(result.status)) {
          timer = setTimeout(() => void refreshPreparation(), 1000)
        }
      } catch (failure) {
        if (active) notify(failure)
      }
    }
    void refreshPreparation()
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [endpoint, reload, notify])

  const basisReady = !!current && !!chapters
  const ordered = [...(chapters ?? [])].sort((left, right) => left.position - right.position)
  const titles = new Map(ordered.map(({ id, title }) => [id, title]))
  const sampleIds = ordered.slice(0, openingLength).map(({ id }) => id)
  const unchangedIntroduction =
    preparedIntroduction && intro.trim() === preparedIntroduction.intro.trim()
      ? preparedIntroduction
      : undefined
  const introScopeChanged = !!(
    unchangedIntroduction &&
    (spoilerScope !== unchangedIntroduction.spoiler_scope ||
      sampleIds.length !== unchangedIntroduction.sample_chapter_ids.length ||
      sampleIds.some((id, index) => id !== unchangedIntroduction.sample_chapter_ids[index]))
  )
  const linkedPreparationId = unchangedIntroduction?.id ?? closurePreparationId
  const body = {
    basis_sha256: current?.basis_sha256,
    read_note: readNote.trim(),
    author_decisions: decisions,
    ...form,
    intro: intro.trim(),
    spoiler_scope: spoilerScope,
    sample_chapter_ids: sampleIds,
    ...(linkedPreparationId ? { preparation_id: linkedPreparationId } : {}),
  }
  const signature = JSON.stringify(body)
  const matchingPreview = preview?.signature === signature ? preview : undefined
  const readyPreview = matchingPreview?.result
  const reviewedChecks = reviewed && busy !== 'review' ? matchingPreview?.reviewedChecks : undefined
  const checks = reviewedChecks ?? readyPreview?.checks ?? current?.checks ?? []
  const draftLabel = readyPreview ? '本次交付预览的六项判定' : '本次拟确认的六项完本判定'
  const history = current?.history?.filter((entry) => entry.id !== current.latest?.id) ?? []
  const canConfirm = !!(
    !introScopeChanged &&
    readyPreview?.materials &&
    reviewedChecks?.length === 6 &&
    reviewedChecks.every((check) => check.status === 'green')
  )

  function editClosure(id: ClosureKey, transform: (closure: Closure) => Closure) {
    setForm((before) => ({ ...before, [id]: transform(before[id]) }))
    changeDecisions({ [id]: 'pending' })
  }

  function changeDecisions(change: Partial<AuthorDecisions>) {
    const basis = current?.basis_sha256
    if (basis)
      setDraftDecisions((before) => ({
        basis,
        decisions: { ...(before?.basis === basis ? before.decisions : pendingDecisions), ...change },
      }))
    setReviewed(false)
  }

  async function prepare() {
    if (!current || !chapters || preparing || !modelReady) return
    setPreparing(true)
    try {
      const input = {
        basis_sha256: current.basis_sha256,
        spoiler_scope: spoilerScope,
        sample_chapters: openingLength,
        account,
      }
      const key = JSON.stringify(input)
      if (pendingPreparation.current?.key !== key)
        pendingPreparation.current = { key, requestId: crypto.randomUUID() }
      await api(`${endpoint}/preparation`, 'POST', {
        ...input,
        client_request_id: pendingPreparation.current.requestId,
      })
      pendingPreparation.current = undefined
      if (alive.current) setReload((version) => version + 1)
    } catch (failure) {
      if (alive.current) notify(failure)
    } finally {
      if (alive.current) setPreparing(false)
    }
  }

  function loadPreparation() {
    const candidate = preparation?.candidate
    if (!preparation?.artifact_id || !candidate?.current) return
    const updated = { ...form }
    let loadedClosure = false
    for (const [key] of closureFields) {
      if (!form[key].note && !form[key].sources.length && candidate.closures[key].note) {
        updated[key] = candidate.closures[key]
        changeDecisions({ [key]: 'pending' })
        loadedClosure = true
      }
    }
    if (loadedClosure) {
      setForm(updated)
      setClosurePreparationId(preparation.artifact_id)
    }
    if (!intro.trim()) {
      setIntro(candidate.intro)
      setSpoilerScope(candidate.spoiler_scope)
      setOpeningLength(candidate.sample_chapter_ids.length)
      setPreparedIntroduction({
        id: preparation.artifact_id,
        intro: candidate.intro,
        spoiler_scope: candidate.spoiler_scope,
        sample_chapter_ids: candidate.sample_chapter_ids,
      })
    }
    setReviewed(false)
    setPreview(undefined)
    setMessage('已将 AI 建议载入空白项。请按原文核对，逐项记录决定和通读。')
  }

  async function submit(action: 'preview' | 'review' | 'confirm') {
    if (!current || !chapters || busy || introScopeChanged || (action === 'confirm' && !canConfirm)) return
    setBusy(action)
    setError('')
    setMessage('')
    try {
      if (action !== 'confirm') {
        setReviewed(action === 'review')
        const result = await api<Preview>(`${endpoint}/preview`, 'POST', {
          ...body,
          materials_reviewed: action === 'review',
        })
        if (!alive.current) return
        if (action === 'review' && matchingPreview && result.materials) {
          setPreview({ ...matchingPreview, reviewedChecks: result.checks })
        } else {
          setPreview({ signature, result })
          setReviewed(false)
        }
      } else {
        const result = await api<Completion>(`${endpoint}/confirm`, 'POST', {
          ...body,
          materials_reviewed: true,
        })
        if (!alive.current) return
        setCurrent({ ...current, latest: result })
        setReload((count) => count + 1)
        setPreview(undefined)
        setReviewed(false)
        setForm(freshClosures())
        setReadNote('')
        setDraftDecisions(undefined)
        setIntro('')
        setSpoilerScope('opening')
        setOpeningLength(1)
        setClosurePreparationId(undefined)
        setPreparedIntroduction(undefined)
        setMessage('已确认完结，交付包已固定到本次稿件版本。')
      }
    } catch (failure) {
      if (!alive.current) return
      setError(failure instanceof Error ? failure.message : '完本操作失败，请重试。')
      notify(failure)
      if (action === 'review') setReviewed(false)
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
      {!basisReady && !error && <p role="status">正在读取完本依据…</p>}
      {current?.latest && (
        <section className="notice">
          <h3>{current.latest.current ? '当前版本已完结' : '历史完结记录 · 当前需重新核对'}</h3>
          <a className="button" href={`/api${endpoint}/${encodeURIComponent(current.latest.id)}/export`}>
            下载{current.latest.current ? '' : '历史'}交付包
          </a>
          {current.latest.current && <CheckList checks={current.latest.checks} label="当前六项完本判定" />}
          <details>
            <summary>完结判定与文件校验值</summary>
            {!current.latest.current && <CheckList checks={current.latest.checks} label="历史六项完本判定" />}
            {current.latest.review && (
              <CompletionReviewEvidence
                review={current.latest.review}
                titles={frozenTitles(current.latest.manifest)}
              />
            )}
            <ManifestList manifest={current.latest.manifest} />
          </details>
        </section>
      )}
      {history.length > 0 && (
        <details>
          <summary>历次交付包（{history.length}）</summary>
          <ul>
            {history.map(({ id, current: stillCurrent, created, origin, source_device_id: sourceDevice }) => (
              <li key={id}>
                <a href={`/api${endpoint}/${encodeURIComponent(id)}/export`}>
                  下载 {new Date(created * 1000).toLocaleString('zh-CN')} 的交付包
                </a>
                {' · '}
                {origin === 'cloud'
                  ? `云同步原交付${sourceDevice ? ` · 来源设备 ${sourceDevice}` : ''}`
                  : origin === 'manuscript_package'
                    ? '稿件包原交付'
                    : stillCurrent
                      ? '依据仍有效'
                      : '旧依据'}
                <CompletionHistoryReview route={`${endpoint}/history/${encodeURIComponent(id)}`} />
              </li>
            ))}
          </ul>
        </details>
      )}
      {(readyPreview || !current?.latest?.current) && !!checks.length && (
        <>
          <h3>{draftLabel}</h3>
          <CheckList checks={checks} label={draftLabel} />
        </>
      )}
      <details>
        <summary>AI 准备完本建议</summary>
        <p>所选试读范围用于起草简介。收束核对会读取当前正文或体检已有的选区。</p>
        <button
          type="button"
          disabled={
            !basisReady ||
            !modelReady ||
            preparing ||
            (!!preparation && ['queued', 'running'].includes(preparation.status))
          }
          onClick={() => void prepare()}
        >
          生成完本核对建议与简介
        </button>
        {preparation && ['queued', 'running'].includes(preparation.status) && (
          <p role="status">正在准备完本建议…</p>
        )}
        {preparation?.error && <p role="alert">{preparation.error}</p>}
        {preparation?.candidate && (
          <>
            <p>
              收束依据 · {preparation.candidate.coverage}。简介依据 · {preparation.candidate.intro_coverage}。
            </p>
            <p>{preparation.candidate.intro}</p>
            <button
              type="button"
              disabled={!basisReady || !preparation.candidate.current}
              onClick={loadPreparation}
            >
              载入 AI 建议到空白项
            </button>
            {!preparation.candidate.current && <p>依据已更新，请重新生成。</p>}
          </>
        )}
      </details>
      {closurePreparationId && (
        <button type="button" onClick={() => setClosurePreparationId(undefined)}>
          解除 AI 收束建议的版本关联
        </button>
      )}
      <fieldset disabled={!basisReady || !!busy}>
        <legend>作者通读与收束说明</legend>
        <AssistantField
          field="read_note"
          label="六项完本判定"
          value=""
          objectKey="completion-checks"
          contextKey={current?.basis_sha256}
          enabled={basisReady && !busy}
          quickLabel="解释六项判定"
          quickQuestion="请解释当前六项完本判定，并说明每个待处理项接下来应核对哪项依据。"
          action="review"
        />
        <label>
          通读记录
          <textarea
            value={readNote}
            onChange={(event) => {
              setReadNote(event.target.value)
              changeDecisions({ reading: 'pending' })
            }}
            rows={3}
            maxLength={4000}
          />
        </label>
        <AssistantField
          field="read_note"
          label="稿件通读记录"
          value={readNote}
          contextKey={current?.basis_sha256}
          enabled={basisReady && !busy}
          quickLabel="整理通读记录"
          action="review"
          onApply={(value) => {
            if (typeof value === 'string') {
              setReadNote(value)
              changeDecisions({ reading: 'pending' })
            }
          }}
        />
        <label className="check">
          <input
            type="checkbox"
            checked={decisions.reading === 'verified'}
            onChange={(event) => changeDecisions({ reading: event.target.checked ? 'verified' : 'pending' })}
          />
          已通读本次稿件
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
            <AssistantField
              field={id}
              label={`${title}说明`}
              value={form[id].note}
              contextKey={current?.basis_sha256}
              enabled={basisReady && !busy}
              quickLabel="AI 准备收束建议"
              action="review"
              onApply={(value) => {
                if (typeof value === 'string') editClosure(id, (entry) => ({ ...entry, note: value }))
              }}
            />
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
            <label>
              {title}判定
              <select
                value={decisions[id]}
                onChange={(event) => changeDecisions({ [id]: event.target.value as ClosureDecision })}
              >
                <option value="pending">待核对</option>
                <option value="verified">已核对正文与依据</option>
                <option value="author_decision">作者已裁定</option>
              </select>
            </label>
            {decisions[id] === 'verified' && !form[id].sources.length && <p>请为本项添加当前正文引文。</p>}
            {decisions[id] === 'author_decision' && <p>请在说明中记录作者确认的安排或留白。</p>}
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
      <fieldset disabled={!basisReady || !!busy}>
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
        <AssistantField
          field="intro"
          label="交付简介"
          value={intro}
          contextKey={`${current?.basis_sha256 ?? ''}/${spoilerScope}/${openingLength}`}
          request={{ spoiler_scope: spoilerScope, sample_chapters: openingLength }}
          enabled={basisReady && !busy && !introScopeChanged}
          quickLabel="AI 起草简介"
          action="suggest"
          onApply={(value) => {
            if (typeof value === 'string') {
              setIntro(value)
              setPreparedIntroduction({
                intro: value,
                spoiler_scope: spoilerScope,
                sample_chapter_ids: sampleIds,
              })
              setReviewed(false)
            }
          }}
        />
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
        {introScopeChanged && (
          <p role="status">
            已载入的 AI
            简介采用另一剧透或试读范围。请恢复原范围、改写简介，或清空简介后载入按当前范围生成的建议。
          </p>
        )}
        <button type="button" disabled={introScopeChanged} onClick={() => void submit('preview')}>
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
              onChange={(event) => {
                if (event.target.checked) void submit('review')
                else setReviewed(false)
              }}
            />
            已核对这次交付材料与剧透范围
          </label>
          {busy === 'review' && <p role="status">正在核对六项完本判定…</p>}
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
