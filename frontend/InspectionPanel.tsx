import { useEffect, useState } from 'react'
import { api, ApiError, type Chapter } from './api'
import type { components } from './api-schema'
import { confirmLeave, useLeaveGuard } from './leaveGuard'
import { InspectionSemanticPanel, type InspectionSuggestion } from './InspectionSemanticPanel'
import { InspectionSources } from './InspectionSources'
import {
  coversInspectionExcerpt,
  maxAuthorEvidence,
  maxSoftDeviations,
  softDeviations,
  type InspectionExcerpt,
  type SoftDeviation,
} from './inspectionEvidence'

type Scope = 'book' | 'volume' | 'chapter'
type Status = 'green' | 'yellow' | 'red' | 'pending' | 'outdated' | 'not_applicable'
type Conclusion = Exclude<Status, 'pending' | 'outdated'>
type Evidence = InspectionExcerpt
type Review = {
  check_id: string
  status: Conclusion | ''
  basis_note: string
  reason: string
  evidence: Evidence[]
  source_candidate_id?: string
  soft_deviation_report_ids?: string[]
  author_overrides_candidate?: boolean
  candidate_result?: InspectionSuggestion['result']
}
type Item = {
  check_id: string
  title: string
  status: Status
  current_status?: Status
  summary: string
  basis: Record<string, unknown>
  can_mark_not_applicable: boolean
  author_review?: Review
}
type LengthTarget = {
  id: string
  revision: number
  targets: { min_chars: number | null; max_chars: number | null; expected_chapters: number | null }
}
type Preview = {
  scope: Scope
  scope_id: string | null
  basis_sha256: string
  chapters: { id: string; title: string; revision: number; chars: number }[]
  items: Item[]
  length_target: LengthTarget | null
}
type Report = Preview & {
  id: string
  current: boolean
  created: number
  origin?: 'manuscript_package' | 'cloud'
  source_device_id?: string
  revalidation_request?: { basis_sha256: string; author_reviews: Review[] } | null
}
type Collection = {
  preview: Preview
  latest: Report | null
  history: { id: string; created: number; scope?: Scope; origin?: Report['origin'] }[]
}
type Outline = { confirmed: { plan: { volumes: { id: string; title: string }[] } } | null }
type Citation = Pick<components['schemas']['CardCitation'], 'title' | 'evidence_start' | 'evidence_end'>
type CardReference = {
  chapter_id: string
  session_id: string
  card_id: string
  revision: number
  version_id?: string
  source?: Citation | null
  chapter_revision?: number
  artifact_id?: string
}
type CardEvidence = components['schemas']['ReferenceCardEvidence']

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

function canOpenSources(item: Item) {
  return (
    !!item.author_review?.evidence.length ||
    ['records', 'events', 'knowledge', 'reports', 'scenes', 'review_blockers', 'author_setting_uses'].some(
      (key) => Array.isArray(item.basis[key]) && (item.basis[key] as unknown[]).length > 0,
    )
  )
}

function ScopedInspectionSources({
  item,
  chapters,
  branchId,
}: {
  item: Item
  chapters: Preview['chapters']
  branchId: string
}) {
  const rows: unknown = item.basis.scoped_findings
  if (!Array.isArray(rows)) return null
  const findings = rows.filter(
    (row): row is { scope: 'chapter' | 'volume'; scope_id: string; report_id: string } =>
      !!row &&
      (row.scope === 'chapter' || row.scope === 'volume') &&
      typeof row.scope_id === 'string' &&
      typeof row.report_id === 'string',
  )
  if (!findings.length) return null
  return (
    <ul aria-label="章卷红项来源">
      {findings.map((finding) => (
        <li key={finding.report_id}>
          <p className="small" style={{ overflowWrap: 'anywhere' }}>
            {finding.scope === 'chapter'
              ? (chapters.find((chapter) => chapter.id === finding.scope_id)?.title ?? '本章')
              : '分卷'}{' '}
            · 体检报告 <code>{finding.report_id}</code>
          </p>
          <InspectionSources
            route={`/branches/${encodeURIComponent(branchId)}/workflow-inspections/${encodeURIComponent(finding.report_id)}/source-context/${item.check_id}`}
          />
        </li>
      ))}
    </ul>
  )
}

function StatusBadge({ status }: { status: Status }) {
  const shade = shades[status]
  return (
    <span className="badge" style={shade && { color: shade[0], backgroundColor: shade[1] }}>
      {labels[status]}
    </span>
  )
}

function referenceRows(value: unknown): CardReference[] {
  if (!Array.isArray(value)) return []
  return value.filter(
    (row): row is CardReference =>
      !!row &&
      typeof row.chapter_id === 'string' &&
      typeof row.session_id === 'string' &&
      typeof row.card_id === 'string' &&
      Number.isInteger(row.revision),
  )
}

function CardOriginal({ branchId, versionId }: { branchId: string; versionId: string }) {
  const [result, setResult] = useState<'loading' | 'unavailable' | 'failed' | { excerpt: string }>()
  async function read() {
    if (result === 'loading' || typeof result === 'object') return
    setResult('loading')
    try {
      const proof = await api<CardEvidence>(
        `/branches/${encodeURIComponent(branchId)}/reference-cards/${encodeURIComponent(versionId)}/evidence`,
      )
      setResult(
        proof.version_id === versionId &&
          (proof.source_state === 'local' || proof.source_state === 'included_excerpt') &&
          typeof proof.excerpt === 'string' &&
          proof.excerpt.trim()
          ? { excerpt: proof.excerpt }
          : 'unavailable',
      )
    } catch (error) {
      setResult(error instanceof ApiError && error.status === 404 ? 'unavailable' : 'failed')
    }
  }
  return (
    <details onToggle={(event) => event.currentTarget.open && void read()}>
      <summary>查看手法卡原文</summary>
      <div role="status">
        {result === 'loading' && <p>正在读取本机原文…</p>}
        {result === 'unavailable' && <p>本机没有这一手法卡修订的可核验原文。</p>}
      </div>
      {result === 'failed' && (
        <div>
          <p role="alert">读取手法卡原文失败。</p>
          <button type="button" onClick={() => void read()}>
            重试读取原文
          </button>
        </div>
      )}
      {typeof result === 'object' && <blockquote>{result.excerpt}</blockquote>}
    </details>
  )
}

function CheckCardReferences({
  basis,
  chapters,
  branchId,
}: {
  basis: Item['basis']
  chapters: Preview['chapters']
  branchId: string
}) {
  const groups = [
    { label: '章纲计划手法卡', rows: referenceRows(basis.planned_card_references), adopted: false },
    { label: '已采纳稿的手法卡依据', rows: referenceRows(basis.card_references), adopted: true },
  ]
  if (groups.every(({ rows }) => !rows.length)) return null
  return groups.map(({ label, rows, adopted }) => (
    <section key={label} aria-label={label} style={{ overflowWrap: 'anywhere' }}>
      <p>
        <strong>{label}</strong>
      </p>
      {rows.length ? (
        <ul>
          {rows.map((row) => {
            const chapter = chapters.find(({ id }) => id === row.chapter_id)
            const versionId =
              typeof row.version_id === 'string' && /^[a-f0-9]{64}$/.test(row.version_id)
                ? row.version_id
                : undefined
            const citation = typeof row.source?.title === 'string' ? row.source : undefined
            return (
              <li
                key={JSON.stringify([
                  branchId,
                  row.chapter_id,
                  row.session_id,
                  row.card_id,
                  row.revision,
                  versionId,
                  row.artifact_id,
                ])}
              >
                <p>
                  {chapter?.title || '章节'} · 手法卡 {row.card_id} · 修订 {row.revision}
                </p>
                <p>章节编号 {row.chapter_id}</p>
                {adopted && (
                  <p>
                    {typeof row.chapter_revision === 'number'
                      ? `章稿修订 ${row.chapter_revision}`
                      : '章稿修订未记录'}
                  </p>
                )}
                {versionId && <p>手法卡版本 {versionId}</p>}
                {citation && (
                  <p>
                    来源 {citation.title}
                    {typeof citation.evidence_start === 'number' &&
                      typeof citation.evidence_end === 'number' && (
                        <>
                          {' '}
                          · 原文位置 {citation.evidence_start}–{citation.evidence_end}
                        </>
                      )}
                  </p>
                )}
                {versionId && citation ? (
                  <CardOriginal branchId={branchId} versionId={versionId} />
                ) : (
                  <p>缺少可核验的手法卡版本或来源。</p>
                )}
              </li>
            )
          })}
        </ul>
      ) : (
        <p>本范围无记录。</p>
      )}
    </section>
  ))
}

function CheckList({
  items,
  chapters,
  branchId,
  reportId,
  onChoose,
}: {
  items: Item[]
  chapters: Preview['chapters']
  branchId: string
  reportId?: string
  onChoose?: (item: Item) => void
}) {
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
              {!!item.author_review.soft_deviation_report_ids?.length && (
                <p>已签认 {item.author_review.soft_deviation_report_ids.length} 条软设定偏离。</p>
              )}
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
          {item.check_id === '08' && (
            <CheckCardReferences basis={item.basis} chapters={chapters} branchId={branchId} />
          )}
          {reportId && canOpenSources(item) && (
            <InspectionSources
              route={`/branches/${encodeURIComponent(branchId)}/workflow-inspections/${encodeURIComponent(reportId)}/source-context/${item.check_id}`}
            />
          )}
          <ScopedInspectionSources item={item} chapters={chapters} branchId={branchId} />
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
  account = '',
  modelReady = false,
}: {
  branchId: string
  chapters: Pick<Chapter, 'id' | 'title'>[]
  account?: string
  modelReady?: boolean
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
  const target = data?.preview.length_target
  const targets = {
    min_chars: minimum === '' ? null : Number(minimum),
    max_chars: maximum === '' ? null : Number(maximum),
    expected_chapters: count === '' ? null : Number(count),
  }
  const targetDirty =
    targets.min_chars !== (target?.targets.min_chars ?? null) ||
    targets.max_chars !== (target?.targets.max_chars ?? null) ||
    targets.expected_chapters !== (target?.targets.expected_chapters ?? null)
  const dirty =
    Object.values(reviews).some(
      (review) => review.status || review.reason || review.basis_note || review.evidence.length,
    ) || targetDirty
  useLeaveGuard(dirty, '尚未保存的体检核对')

  function loadTargets(value?: LengthTarget | null) {
    setMinimum(value?.targets.min_chars?.toString() ?? '')
    setMaximum(value?.targets.max_chars?.toString() ?? '')
    setCount(value?.targets.expected_chapters?.toString() ?? '')
  }

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
        if (active) {
          setData(next)
          loadTargets(next.preview.length_target)
        }
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
    loadTargets()
    setHistoryId('')
    setError('')
    setMessage('')
  }
  const choices = scope === 'volume' ? volumes : chapters
  const review = reviews[selected] ?? newReview(selected)
  const selectedCheck = data?.preview.items.find((item) => item.check_id === selected)
  const deviations = selectedCheck?.check_id === '05' ? (softDeviations(selectedCheck.basis) ?? []) : []
  const requiresCandidateResolution =
    !!review.source_candidate_id && review.candidate_result !== 'pass' && review.status === 'green'
  const change = (values: Partial<Review>) =>
    setReviews((current) => ({
      ...current,
      [selected]: { ...(current[selected] ?? newReview(selected)), ...values },
    }))
  function markSoftDeviation(deviation: SoftDeviation, checked: boolean) {
    const previous = review.soft_deviation_report_ids ?? []
    if (checked && previous.length >= maxSoftDeviations) {
      setError('本次签认可包含至多 1000 条软偏离。请先在对应章卷处理部分偏离。')
      return
    }
    setError('')
    change({
      soft_deviation_report_ids: checked
        ? [...previous, deviation.id]
        : previous.filter((id) => id !== deviation.id),
    })
  }
  const visible = historyId ? historical : data?.latest
  function loadSuggestion(candidateId: string, suggestion: InspectionSuggestion) {
    const id = suggestion.check_id
    setSelected(id)
    setReviews((current) => {
      const draft = current[id] ?? newReview(id)
      const evidence = [
        ...draft.evidence,
        ...suggestion.evidence.filter(
          (item) =>
            !draft.evidence.some(
              (old) => old.chapter_id === item.chapter_id && old.start === item.start && old.end === item.end,
            ),
        ),
      ].slice(0, maxAuthorEvidence)
      return {
        ...current,
        [id]: {
          ...draft,
          evidence,
          basis_note:
            draft.basis_note ||
            suggestion.basis_excerpts
              .map((source) => source.label)
              .join('、')
              .slice(0, 2000),
          reason: draft.reason || (suggestion.result === 'pass' ? suggestion.reason : ''),
          source_candidate_id: candidateId,
          candidate_result: suggestion.result,
          author_overrides_candidate: false,
        },
      }
    })
    setMessage(`已载入 ${id} 候选引用，请在作者核对中选择本次判定。`)
  }
  async function saveTargets() {
    if (!data || saving) return
    setSaving(true)
    setError('')
    setMessage('')
    try {
      const updated = await api<Pick<Collection, 'preview' | 'latest'>>(`${route}/length-targets`, 'PUT', {
        scope,
        scope_id: scopeId || null,
        base_target_id: target?.id ?? null,
        targets,
      })
      setData({ ...data, ...updated })
      loadTargets(updated.preview.length_target)
      setMessage(`已采用篇幅目标修订 ${updated.preview.length_target?.revision}，请核对当前篇幅结论。`)
    } catch (error) {
      setError(error instanceof Error ? error.message : '篇幅目标保存失败。')
    } finally {
      setSaving(false)
    }
  }
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!data || saving || targetDirty) return
    const validReviews = Object.values(reviews).filter(
      (review) => review.status || review.reason || review.basis_note || review.evidence.length,
    )
    const settingReview = validReviews.find(({ check_id }) => check_id === '05')
    const settingDeviations =
      softDeviations(data.preview.items.find(({ check_id }) => check_id === '05')?.basis ?? {}) ?? []
    const selectedSoftDeviations = new Set(settingReview?.soft_deviation_report_ids)
    if (
      settingReview?.status === 'green' &&
      (selectedSoftDeviations.size !== settingDeviations.length ||
        settingDeviations.some(({ id }) => !selectedSoftDeviations.has(id)))
    ) {
      setError('请逐条核对当前软设定偏离并保留本次取舍依据。')
      return
    }
    const authorReviews = validReviews.map(({ candidate_result: _, ...review }) => {
      if (review.check_id !== '05' || review.status !== 'green' || !settingDeviations.length) return review
      const evidence = [...review.evidence]
      for (const { source } of settingDeviations) {
        if (!evidence.some((excerpt) => coversInspectionExcerpt(excerpt, source))) evidence.push(source)
      }
      return { ...review, evidence }
    })
    if (
      authorReviews.some(
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
        author_reviews: authorReviews,
      })
      setData({
        ...data,
        latest: result,
        history: [{ id: result.id, created: result.created }, ...data.history],
      })
      resetDraft()
      loadTargets(data.preview.length_target)
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
            <CheckList
              items={data.preview.items}
              chapters={data.preview.chapters}
              branchId={branchId}
              onChoose={(item) => setSelected(item.check_id)}
            />
          </details>
          <InspectionSemanticPanel
            key={`${scope}/${scopeId}`}
            route={route}
            account={account}
            modelReady={modelReady && !saving}
            basis={{ scope, scope_id: scopeId || null, basis_sha256: data.preview.basis_sha256 }}
            onLoad={loadSuggestion}
          />
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
                      onChange={(event) =>
                        change({
                          status: event.target.value as Review['status'],
                          author_overrides_candidate: false,
                          soft_deviation_report_ids:
                            event.target.value === 'green' ? review.soft_deviation_report_ids : undefined,
                        })
                      }
                    >
                      <option value="">待填</option>
                      {decisions
                        .filter(
                          (status) => status !== 'not_applicable' || selectedCheck?.can_mark_not_applicable,
                        )
                        .map((status) => (
                          <option value={status} key={status}>
                            {labels[status]}
                          </option>
                        ))}
                    </select>
                  </label>
                  {review.status === 'green' && deviations.length > 0 && (
                    <fieldset>
                      <legend>已发现的软设定偏离</legend>
                      {deviations.map((deviation) => {
                        const { chapter_id, revision, quote } = deviation.source
                        const chapter = data.preview.chapters.find(({ id }) => id === chapter_id)
                        const description = `soft-deviation-${deviation.id}`
                        return (
                          <div key={deviation.id}>
                            <label className="check">
                              <input
                                type="checkbox"
                                aria-describedby={description}
                                checked={review.soft_deviation_report_ids?.includes(deviation.id) ?? false}
                                onChange={(event) => markSoftDeviation(deviation, event.target.checked)}
                              />
                              核对并引用「{deviation.ruleTitle}」的偏离
                            </label>
                            <div id={description}>
                              <span className="small">
                                {chapter?.title ?? chapter_id} · 正文修订 {revision}
                              </span>
                              <blockquote>{quote}</blockquote>
                            </div>
                          </div>
                        )
                      })}
                    </fieldset>
                  )}
                  {requiresCandidateResolution && (
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={!!review.author_overrides_candidate}
                        onChange={(event) => change({ author_overrides_candidate: event.target.checked })}
                      />
                      已核对候选的冲突或证据缺口，并在理由中说明本次作者裁定
                    </label>
                  )}
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
                        review.evidence.length < maxAuthorEvidence &&
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
              <legend>此范围的篇幅目标</legend>
              <p>{target ? `当前采用修订 ${target.revision}` : '尚未采用数值目标'}</p>
              <p>{data.preview.items.find((item) => item.check_id === '09')?.summary}</p>
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
              <button
                type="button"
                disabled={!targetDirty || (!minimum && !maximum && !count)}
                onClick={() => void saveTargets()}
              >
                保存篇幅目标
              </button>
              {targetDirty && (
                <p className="small">保存新目标后会重算篇幅结论。若继续沿用当前目标，请恢复原值。</p>
              )}
            </fieldset>
            <button className="primary" type="submit" disabled={saving || targetDirty}>
              {saving ? '正在保存…' : '生成体检单'}
            </button>
          </form>
          {(data.latest || data.history.length > 0) && (
            <details open>
              <summary>已保存体检单</summary>
              <label>
                选择体检单
                <select value={historyId} onChange={(event) => setHistoryId(event.target.value)}>
                  <option value="" disabled={!data.latest}>
                    {data.latest ? '最近一次' : '选择历史体检单'}
                  </option>
                  {data.history
                    .filter((row) => row.id !== data.latest?.id)
                    .map(({ id, created, origin, scope: historyScope }) => (
                      <option key={id} value={id}>
                        {origin &&
                          `${origin === 'cloud' ? '云同步' : '稿件包'} · ${historyScope === 'chapter' ? '章' : historyScope === 'volume' ? '卷' : '全书'} · `}
                        {new Date(created * 1000).toLocaleString()}
                      </option>
                    ))}
                </select>
              </label>
              {visible && (
                <section aria-label="体检单结果">
                  <p>
                    {visible.origin
                      ? `${visible.origin === 'cloud' ? '云同步' : '稿件包'}原判定 · 当前待复检`
                      : visible.current
                        ? '依据仍为当前版本'
                        : '部分依据已更新，过期项待复检'}
                  </p>
                  {visible.source_device_id && <p>来源设备 {visible.source_device_id}</p>}
                  {visible.revalidation_request?.basis_sha256 === data.preview.basis_sha256 && (
                    <button
                      type="button"
                      onClick={() => {
                        if (dirty && !window.confirm('用历史核对内容替换本轮尚未保存的核对？')) return
                        const archived = visible.revalidation_request!.author_reviews
                        const ruleCheck = visible.items.find(({ check_id }) => check_id === '05')
                        const historicalDeviations = softDeviations(ruleCheck?.basis ?? {}) ?? []
                        setReviews(
                          Object.fromEntries(
                            archived.map((row) => [
                              row.check_id,
                              row.check_id !== '05' || !row.soft_deviation_report_ids?.length
                                ? row
                                : {
                                    ...row,
                                    evidence: row.evidence.filter(
                                      (excerpt) =>
                                        !historicalDeviations.some(
                                          ({ source }) =>
                                            excerpt.start === source.start &&
                                            excerpt.end === source.end &&
                                            coversInspectionExcerpt(excerpt, source),
                                        ),
                                    ),
                                  },
                            ]),
                          ),
                        )
                        setSelected(archived[0]?.check_id ?? '')
                        setMessage('已载入历史作者核对。请按当前依据复核后生成新体检单。')
                      }}
                    >
                      载入历史作者核对
                    </button>
                  )}
                  <CheckList
                    key={visible.id}
                    items={visible.items}
                    chapters={visible.chapters}
                    branchId={branchId}
                    reportId={visible.id}
                  />
                </section>
              )}
            </details>
          )}
        </>
      )}
    </section>
  )
}
