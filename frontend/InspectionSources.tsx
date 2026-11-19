import { useRef, useState, type ReactNode } from 'react'
import { api } from './api'
import type { components } from './api-schema'
import type { InspectionExcerpt } from './inspectionEvidence'
import { inspectionStateConflicts } from './workflowArchive'

type EvidencePage = components['schemas']['InspectionEvidencePage']
type EvidenceContext = components['schemas']['InspectionEvidenceContext']

const stateAudience = { author: '作者所知', character: '人物所知', reader: '读者所知' }
const storyTruth = { actual: '已记录', planned: '计划', unknown: '待核实', conflict: '有冲突' }

function fields(value: unknown) {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined
}

function storyRows(value: unknown) {
  return Array.isArray(value) ? value.map(fields).filter((row) => row !== undefined) : []
}

function storyTime(row: Record<string, unknown>) {
  const order = typeof row.story_order === 'number' ? `故事顺序 ${row.story_order}` : '故事顺序未定'
  return typeof row.story_time === 'string' && row.story_time.trim() && row.story_time !== '未知'
    ? `${order} · ${row.story_time}`
    : order
}

function storyLabel(labels: Record<string, string>, value: unknown, fallback: string) {
  return typeof value === 'string' && Object.hasOwn(labels, value) ? labels[value] : fallback
}

export function InspectionDescendantLengths({ basis }: { basis: Record<string, unknown> }) {
  const rows = storyRows(basis.descendant_length_targets)
  if (!rows.length) return null
  return (
    <details open={rows.some(({ status }) => status === 'red')}>
      <summary>分章、分卷篇幅目标</summary>
      <ul>
        {rows.map((row) => {
          const target = fields(row.length_target)
          const bounds = fields(target?.targets)
          const labels = [
            [bounds?.expected_chapters, '章数'],
            [bounds?.min_chars, '有效字符下限'],
            [bounds?.max_chars, '有效字符上限'],
          ].flatMap(([value, label]) => (typeof value === 'number' ? [`${label} ${value}`] : []))
          return (
            <li key={`${row.scope}/${row.scope_id}`}>
              <strong>{`${row.scope === 'volume' ? '卷' : '章'} · ${row.title}`}</strong>
              <p>
                {`目标修订 ${target?.revision}`}
                {labels.length > 0 && ` · ${labels.join('、')}`}
              </p>
              <p>{String(row.summary)}</p>
            </li>
          )
        })}
      </ul>
    </details>
  )
}

export function InspectionNarrativeEvidence({
  basis,
  chapters,
  kind,
}: {
  basis: Record<string, unknown>
  chapters: { id: string; title: string }[]
  kind: 'time' | 'viewpoint'
}) {
  const isTime = kind === 'time'
  const records = storyRows(isTime ? basis.events : basis.knowledge)
  const scenes = storyRows(basis.scenes)
  if (!records.length && !scenes.length) return null
  const names = fields(basis.viewpoint_names)
  const anchors = new Map(storyRows(basis.knowledge_events).map((row) => [row.id, row]))
  const chapterNames = new Map(chapters.map(({ id, title }) => [id, title]))
  const name = (id: unknown) => {
    if (typeof id !== 'string' || !id) return undefined
    const title = names?.[id]
    return typeof title === 'string' ? title : id
  }
  return (
    <details style={{ overflowWrap: 'anywhere' }}>
      <summary>{isTime ? '事件顺序与场景时点' : '场景视角与知情依据'}</summary>
      {!!records.length && (
        <ul aria-label={isTime ? '冻结事件' : '冻结知情记录'}>
          {records.map((record, index) => {
            const anchor = anchors.get(record.event_id)
            const until = anchors.get(record.until_event_id)
            const subject = name(record.character_id)
            return (
              <li key={String(record.id ?? index)}>
                <strong>{String(record.title ?? '未注明名称')}</strong>
                {isTime ? (
                  <p>
                    {storyTime(record)} · {storyLabel(storyTruth, record.status, '待核实')}
                  </p>
                ) : (
                  <>
                    <p>
                      {storyLabel(stateAudience, record.audience, '视域未定')}
                      {subject && ` · ${subject}`} · {storyLabel(storyTruth, record.truth, '待核实')}
                    </p>
                    {typeof record.value === 'string' && record.value && <p>{record.value}</p>}
                    <p>
                      获知依据{' '}
                      {anchor
                        ? `${String(anchor.title)} · ${storyTime(anchor)}${anchor.stale ? ' · 依据待复检' : ''}`
                        : '待核对'}
                    </p>
                    {until && (
                      <p>
                        失效依据 {String(until.title)} · {storyTime(until)}
                        {until.stale === true && ' · 依据待复检'}
                      </p>
                    )}
                  </>
                )}
                {Array.isArray(record.invalid_reasons) && !!record.invalid_reasons.length && (
                  <p>记录依据待复检。</p>
                )}
              </li>
            )
          })}
        </ul>
      )}
      {!!scenes.length && (
        <ul aria-label="冻结场景范围">
          {scenes.map((scene, index) => {
            const payload = fields(scene.payload)
            if (!payload || typeof scene.chapter_id !== 'string') return null
            const subject = name(payload.character_id)
            return (
              <li key={String(scene.id ?? index)}>
                <strong>{String(payload.title ?? '未命名场景')}</strong>
                <p>
                  {storyTime(payload)} · {storyLabel(stateAudience, payload.audience, '视域未定')}
                  {subject && ` · ${subject}`}
                </p>
                <p>
                  {chapterNames.get(scene.chapter_id) ?? '来源章节'} · 章稿修订{' '}
                  {String(payload.source_revision)} · 位置 {String(payload.start)}–{String(payload.end)}
                  {scene.stale === true && ' · 依据待复检'}
                </p>
              </li>
            )
          })}
        </ul>
      )}
    </details>
  )
}

function pacingEntry(value: unknown) {
  if (!value || typeof value !== 'object') return
  const { id, title, summary, units } = value as Record<string, unknown>
  if (typeof id === 'string' && typeof title === 'string' && typeof summary === 'string')
    return { id, title, summary, units: Array.isArray(units) ? units : [] }
}

export function InspectionPacingEvidence({ basis }: { basis: Record<string, unknown> }) {
  if (!Array.isArray(basis.pacing_plan)) return null
  const volumes = basis.pacing_plan.map(pacingEntry).filter((entry) => entry !== undefined)
  if (!volumes.length) return null
  return (
    <details style={{ overflowWrap: 'anywhere' }}>
      <summary>卷纲与单元节奏安排</summary>
      {volumes.map((volume) => (
        <section key={volume.id} aria-label={volume.title}>
          <strong>{volume.title}</strong>
          <p style={{ whiteSpace: 'pre-wrap' }}>{volume.summary}</p>
          <dl>
            {volume.units
              .map(pacingEntry)
              .filter((entry) => entry !== undefined)
              .map((unit) => (
                <div key={unit.id}>
                  <dt>{unit.title}</dt>
                  <dd style={{ whiteSpace: 'pre-wrap' }}>{unit.summary}</dd>
                </div>
              ))}
          </dl>
        </section>
      ))}
    </details>
  )
}

export function InspectionStateConflicts({
  basis,
  openOriginal,
}: {
  basis: Record<string, unknown>
  openOriginal?: (evidence: InspectionExcerpt, description: string) => ReactNode
}) {
  const conflicts = inspectionStateConflicts(basis)
  if (!conflicts.length) return null
  return (
    <ul aria-label="已确定的人物状态冲突">
      {conflicts.map(({ sides, key, audience, story_order }) => (
        <li key={sides[0].record_id} style={{ overflowWrap: 'anywhere' }}>
          <details>
            <summary>
              {key} · {stateAudience[audience]} ·{' '}
              {story_order === null ? '同一事件' : `故事时点 ${story_order}`}
            </summary>
            <ul>
              {sides.map((side) => (
                <li key={side.record_id}>
                  <p>
                    {side.title} · 记录值「{side.value}」
                  </p>
                  <p className="small">
                    记录 {side.record_id} · 修订 {side.revision}
                  </p>
                  {side.sources.map((source, index) => (
                    <blockquote key={`${source.chapter_id}/${source.start}/${index}`}>
                      {source.quote}
                      {openOriginal?.(source, `${side.title} · 原文 ${index + 1}`)}
                    </blockquote>
                  ))}
                </li>
              ))}
            </ul>
          </details>
        </li>
      ))}
    </ul>
  )
}

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
