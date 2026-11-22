import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { components } from './api-schema'
import { useLeaveGuard } from './leaveGuard'

const gateLabels = {
  manuscript: '稿件齐全与通读',
  ending: '主冲突与终局',
  characters: '关键人物与关系',
  promises: '伏笔与读者承诺',
  consistency: '一致性与复检',
  materials: '交付材料核验',
} as const

type Schema = components['schemas']
type Gate = Schema['Gate']['id']
type SourceGate = keyof Schema['ReviewSources']
type Anchor = Schema['QuoteInput']
type Chapter = Schema['ChapterManifest']
type Materials = {
  synopsis: string
  spoiler_scope: string
  sample_start_chapter_id: string | null
  sample_end_chapter_id: string | null
  notes: Partial<Record<Gate, string>>
  sources: Partial<Record<SourceGate, Anchor[]>>
}
type Package = Schema['Package']
type Delivery = Schema['DeliveryState']
type Draft = { base: string; input: Materials; edited: boolean }

const gateStatus = { pass: '已通过', pending: '待核对', blocked: '未通过' }
const packageStatus = { draft: '待核对', ready: '六项已通过', finished: '已完结', outdated: '历史快照' }
const evidenceOrigin: Record<Schema['Evidence']['origin'], string> = {
  author: '作者引用',
  ledger: '台账证据',
  chapter_check: '章节校验',
  review: '审阅意见',
}
const guides: Record<Gate, string> = {
  manuscript: '通读保存稿，核对目录、章节是否齐全及已采用的篇幅要求。',
  ending: '说明主冲突如何闭合、固定结果如何兑现，并引用终局原文。',
  characters: '说明关键人物、选择的后果和关系走向，并引用保存稿。',
  promises: '核对重要承诺的回收或有依据的作者处理。若没有重要伏笔或读者承诺，请说明核对结果。',
  consistency: '核对一致性检查和受改稿影响的章节，可逐章补充当前保存稿证据。',
  materials: '审阅四件材料，并核对简介和试读的剧透范围。',
}
const sourceGates: readonly Gate[] = ['ending', 'characters', 'promises', 'consistency']
const newAnchor = { chapter_id: '', quote: '' }

function initialInput(previous: Package | undefined, basis: string): Materials {
  if (!previous)
    return {
      synopsis: '',
      spoiler_scope: '',
      sample_start_chapter_id: null,
      sample_end_chapter_id: null,
      notes: {},
      sources: {},
    }
  return {
    ...previous.input,
    sample_start_chapter_id: previous.input.sample_start_chapter_id ?? null,
    sample_end_chapter_id: previous.input.sample_end_chapter_id ?? null,
    notes: previous.basis_sha256 === basis ? (previous.input.notes ?? {}) : {},
    sources: previous.basis_sha256 === basis ? (previous.input.sources ?? {}) : {},
  }
}

function ChapterOptions({ chapters }: { chapters: Chapter[] }) {
  return (
    <>
      <option value="">选择章节</option>
      {chapters.map((chapter) => (
        <option key={chapter.id} value={chapter.id}>
          第 {chapter.position} 章 {chapter.title}
        </option>
      ))}
    </>
  )
}

function CitationEditor({
  label,
  anchor,
  chapters,
  onChange,
}: {
  label: string
  anchor: Anchor
  chapters: Chapter[]
  onChange: (value: Anchor) => void
}) {
  const [source, setSource] = useState<{ id: string; revision: number; content?: string; error?: string }>()
  const [loading, setLoading] = useState(false)
  const selected = chapters.find((chapter) => chapter.id === anchor.chapter_id)
  const current = source?.id === selected?.id && source?.revision === selected?.revision ? source : undefined
  async function read() {
    if (!selected) return
    setLoading(true)
    try {
      const row = await api<{ content: string; revision: number }>(`/chapters/${selected.id}`)
      if (row.revision !== selected.revision) throw new Error('保存稿已有更新，请刷新交付依据。')
      setSource({ id: selected.id, revision: selected.revision, content: row.content })
    } catch (error) {
      setSource({
        id: selected.id,
        revision: selected.revision,
        error: error instanceof Error ? error.message : '原文读取失败。',
      })
    } finally {
      setLoading(false)
    }
  }
  return (
    <>
      <label>
        {label}引用章节
        <select
          value={anchor.chapter_id}
          onChange={(event) => onChange({ chapter_id: event.target.value, quote: '' })}
        >
          <ChapterOptions chapters={chapters} />
        </select>
      </label>
      <label>
        {label}逐字引文
        <textarea
          value={anchor.quote}
          rows={2}
          maxLength={4000}
          onChange={(event) => onChange({ ...anchor, quote: event.target.value })}
        />
      </label>
      {selected && (
        <details
          onToggle={(event) => {
            if (event.currentTarget.open && !current && !loading) void read()
          }}
        >
          <summary>查看所选章保存稿 · 修订 {selected.revision}</summary>
          {loading && <p role="status">正在读取保存稿…</p>}
          {current?.error && <p role="alert">{current.error}</p>}
          {current?.content !== undefined && <pre className="delivery-text">{current.content}</pre>}
        </details>
      )}
    </>
  )
}

function ReviewInputs({
  input,
  chapters,
  update,
}: {
  input: Materials
  chapters: Chapter[]
  update: (patch: Partial<Materials>) => void
}) {
  return (
    <details>
      <summary>填写六项核对依据</summary>
      {(Object.entries(gateLabels) as [Gate, string][]).map(([gate, label]) => {
        const sourceGate = sourceGates.includes(gate) ? (gate as SourceGate) : undefined
        const saved = sourceGate ? input.sources[sourceGate] : undefined
        const anchors = sourceGate ? (saved?.length ? saved : [newAnchor]) : []
        const setAnchors = (patched: Anchor[]) => {
          if (sourceGate) update({ sources: { ...input.sources, [sourceGate]: patched } })
        }
        return (
          <details className="fact" key={gate}>
            <summary>填写{label}依据</summary>
            <p className="small muted">{guides[gate]}</p>
            <label>
              {label}核对说明
              <textarea
                value={input.notes[gate] ?? ''}
                rows={3}
                maxLength={4000}
                onChange={(event) => update({ notes: { ...input.notes, [gate]: event.target.value } })}
              />
            </label>
            {anchors.map((anchor, index) => (
              <div key={index}>
                <CitationEditor
                  label={anchors.length === 1 ? label : `${label}第 ${index + 1} 条`}
                  anchor={anchor}
                  chapters={chapters}
                  onChange={(value) => setAnchors(anchors.map((row, at) => (at === index ? value : row)))}
                />
                {anchors.length > 1 && (
                  <button type="button" onClick={() => setAnchors(anchors.filter((_, at) => at !== index))}>
                    移除第 {index + 1} 条引文
                  </button>
                )}
              </div>
            ))}
            {sourceGate && anchors.length < 100 && (
              <button type="button" onClick={() => setAnchors([...anchors, newAnchor])}>
                再加一条原文
              </button>
            )}
          </details>
        )
      })}
    </details>
  )
}

function PackageEvidence({ value }: { value: Package }) {
  return (
    <section aria-label="六项验收证据">
      <h3>六项验收证据</h3>
      {value.gates.map((gate) => (
        <article className="fact delivery-gate" key={gate.id} aria-label={gate.label}>
          <details>
            <summary>
              {gate.label} · {gateStatus[gate.status]}
            </summary>
            <p>{gate.reason}</p>
            {gate.note && (
              <p>
                <strong>作者核对</strong> · {gate.note}
              </p>
            )}
            {gate.sources.map((source, index) => {
              return (
                <figure key={index}>
                  <figcaption className="small muted">
                    {evidenceOrigin[source.origin]} · 第 {source.position} 章 {source.title} · 修订{' '}
                    {source.revision} · 原文位置 {source.start}–{source.end}
                  </figcaption>
                  <blockquote>{source.quote}</blockquote>
                </figure>
              )
            })}
            {gate.evidence.length > 0 && (
              <ul>
                {gate.evidence.map((entry, index) => (
                  <li key={index}>{entry}</li>
                ))}
              </ul>
            )}
          </details>
        </article>
      ))}
    </section>
  )
}

function PackageMaterials({ value, path }: { value: Package; path: string }) {
  return (
    <details>
      <summary>预览交付材料</summary>
      {value.files.map((file) => (
        <details key={file.key} className="fact">
          <summary>{file.label}</summary>
          <p className="small muted" title={file.sha256}>
            {file.name} · {file.bytes} 字节 · SHA-256 {file.sha256.slice(0, 12)}
          </p>
          <pre className="delivery-text">{file.preview || '此材料尚无可预览内容。'}</pre>
          {new TextEncoder().encode(file.preview).length < file.bytes && (
            <p className="small muted">此处显示开头片段，可下载完整材料。</p>
          )}
          <a className="button" href={`/api${path}/packages/${value.id}/files/${file.key}`} download>
            下载{file.label}
          </a>
        </details>
      ))}
    </details>
  )
}

export function CompletionDelivery({
  branchId,
  refreshVersion,
}: {
  branchId: string
  refreshVersion?: number
}) {
  const path = `/branches/${branchId}/delivery`
  const [data, setData] = useState<Delivery>()
  const [selected, setSelected] = useState('')
  const [loadedPackage, setLoadedPackage] = useState<Package>()
  const [draft, setDraft] = useState<Draft>()
  const [reload, setReload] = useState(0)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const inFlight = useRef(false)
  const alive = useRef(true)
  const pending = useRef<{ content: string; key: string } | undefined>(undefined)
  const value = loadedPackage?.id === selected ? loadedPackage : undefined
  const status = value && data && value.basis_sha256 !== data.basis_sha256 ? 'outdated' : value?.status
  const draftChanged = !!draft && draft.base !== data?.basis_sha256
  useLeaveGuard(!!draft?.edited, '未生成快照的交付材料及核对依据')

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])
  useEffect(() => {
    const refresh = () => setReload((value) => value + 1)
    window.addEventListener('focus', refresh)
    return () => window.removeEventListener('focus', refresh)
  }, [])

  useEffect(() => {
    let active = true
    setLoading(true)
    void api<Delivery>(path)
      .then((value) => {
        if (!active) return
        setData(value)
        setSelected((old) => old || value.packages[0]?.id || '')
      })
      .catch((error: unknown) => {
        if (active) setError(error instanceof Error ? error.message : '交付依据读取失败。')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [path, refreshVersion, reload])

  useEffect(() => {
    if (!selected) return
    let active = true
    void api<Package>(`${path}/packages/${selected}`)
      .then((value) => {
        if (active) setLoadedPackage(value)
      })
      .catch((error: unknown) => {
        if (active) setError(error instanceof Error ? error.message : '交付快照读取失败。')
      })
    return () => {
      active = false
    }
  }, [path, selected, reload])

  async function write(endpoint: string, body: Record<string, unknown>) {
    if (inFlight.current) return undefined
    setBusy(true)
    setError('')
    setNotice('')
    inFlight.current = true
    const content = JSON.stringify([endpoint, body])
    if (pending.current?.content !== content) pending.current = { content, key: crypto.randomUUID() }
    try {
      const next = await api<Package>(path + endpoint, 'POST', {
        ...body,
        idempotency_key: pending.current.key,
      })
      if (!alive.current) return undefined
      pending.current = undefined
      setLoadedPackage(next)
      setSelected(next.id)
      setReload((value) => value + 1)
      return next
    } catch (error) {
      if (alive.current) setError(error instanceof Error ? error.message : '操作失败，输入已保留。')
      return undefined
    } finally {
      inFlight.current = false
      if (alive.current) setBusy(false)
    }
  }

  function openForm() {
    if (!data) return
    const input = initialInput(value ?? data.packages[0], data.basis_sha256)
    const ids = new Set(data.chapters.map((chapter) => chapter.id))
    if (!input.sample_start_chapter_id || !ids.has(input.sample_start_chapter_id))
      input.sample_start_chapter_id = null
    if (!input.sample_end_chapter_id || !ids.has(input.sample_end_chapter_id))
      input.sample_end_chapter_id = null
    setDraft({ base: data.basis_sha256, input, edited: false })
  }

  function update(patch: Partial<Materials>) {
    setDraft((draft) => {
      if (!draft) return draft
      const input = { ...draft.input, ...patch }
      if (
        input.synopsis !== draft.input.synopsis ||
        input.spoiler_scope !== draft.input.spoiler_scope ||
        input.sample_start_chapter_id !== draft.input.sample_start_chapter_id ||
        input.sample_end_chapter_id !== draft.input.sample_end_chapter_id
      )
        input.notes = { ...input.notes, materials: '' }
      return { ...draft, edited: true, input }
    })
  }

  async function create(event: React.FormEvent) {
    event.preventDefault()
    if (!draft || draftChanged || loading) return
    const sources = Object.fromEntries(
      Object.entries(draft.input.sources).map(([key, rows]) => [
        key,
        rows.filter((row) => row.chapter_id || row.quote.trim()),
      ]),
    )
    if (
      Object.values(sources)
        .flat()
        .some((row) => !row.chapter_id || !row.quote.trim())
    ) {
      setError('已填写的引文须同时指定章节与逐字原文。')
      return
    }
    const next = await write('/packages', { ...draft.input, sources, base_sha256: draft.base })
    if (next) {
      setDraft(undefined)
      setNotice('交付快照已保存，可逐项核对和预览材料。')
    }
  }

  async function finalize() {
    if (
      !value ||
      !data ||
      status !== 'ready' ||
      value.basis_sha256 !== data.basis_sha256 ||
      loading ||
      draft?.edited
    )
      return
    if (await write(`/packages/${value.id}/finalize`, { base_sha256: data.basis_sha256 })) {
      setNotice('已根据六项证据确认完本。')
    }
  }

  return (
    <section className="card completion-delivery" aria-label="完本验收与交付">
      <div className="section-title">
        <h2>完本验收与交付</h2>
      </div>
      <div className="writing-workbench-actions">
        <a className="button" href={`/api/branches/${branchId}/export?format=txt`} download>
          导出原稿
        </a>
        <button
          type="button"
          onClick={() => {
            setError('')
            setReload((value) => value + 1)
          }}
          disabled={loading || busy}
        >
          刷新交付依据
        </button>
        {!draft && data && (
          <button type="button" onClick={openForm} disabled={loading || busy}>
            {data.packages.length ? '新建交付快照' : '准备交付材料'}
          </button>
        )}
      </div>
      {loading && <p role="status">正在读取交付依据…</p>}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {draft && data && (
        <form onSubmit={(event) => void create(event)}>
          <fieldset className="delivery-form" disabled={busy}>
            <legend>准备交付快照</legend>
            <label>
              简介
              <textarea
                value={draft.input.synopsis}
                rows={4}
                maxLength={6000}
                onChange={(event) => update({ synopsis: event.target.value })}
              />
            </label>
            <label>
              剧透范围
              <textarea
                value={draft.input.spoiler_scope}
                rows={2}
                maxLength={2000}
                onChange={(event) => update({ spoiler_scope: event.target.value })}
              />
            </label>
            <div className="delivery-range">
              <label>
                试读起章
                <select
                  value={draft.input.sample_start_chapter_id ?? ''}
                  onChange={(event) => update({ sample_start_chapter_id: event.target.value || null })}
                >
                  <ChapterOptions chapters={data.chapters} />
                </select>
              </label>
              <label>
                试读止章
                <select
                  value={draft.input.sample_end_chapter_id ?? ''}
                  onChange={(event) => update({ sample_end_chapter_id: event.target.value || null })}
                >
                  <ChapterOptions chapters={data.chapters} />
                </select>
              </label>
            </div>
            <ReviewInputs input={draft.input} chapters={data.chapters} update={update} />
            {draftChanged && <p role="alert">作品依据已变化。保留材料草稿，重新载入当前依据后逐项核对。</p>}
            <div className="writing-workbench-actions">
              <button className="primary" type="submit" disabled={loading || draftChanged}>
                创建交付快照
              </button>
              {draftChanged && (
                <button
                  type="button"
                  disabled={loading}
                  onClick={() =>
                    setDraft({
                      ...draft,
                      base: data.basis_sha256,
                      input: { ...draft.input, notes: {}, sources: {} },
                    })
                  }
                >
                  基于当前保存稿重新核对
                </button>
              )}
              <button type="button" onClick={() => setDraft(undefined)}>
                关闭准备表单
              </button>
            </div>
          </fieldset>
        </form>
      )}
      {!!data?.packages.length && (
        <label>
          历史交付包
          <select value={selected} disabled={busy} onChange={(event) => setSelected(event.target.value)}>
            {data.packages.map((row) => (
              <option value={row.id} key={row.id}>
                {new Date(row.created * 1000).toLocaleString('zh-CN')} · {row.id.slice(0, 8)} ·{' '}
                {packageStatus[row.status]}
              </option>
            ))}
          </select>
        </label>
      )}
      {value && status && (
        <section aria-label="所选交付包" key={value.id}>
          <h3>{packageStatus[status]}</h3>
          <p className="small muted" title={value.basis_sha256}>
            四件材料共用快照 {value.id.slice(0, 8)} · 依据 {value.basis_sha256.slice(0, 12)}
            {status === 'outdated' && value.finished && ' · 曾确认完本'}
            {status === 'outdated' && ' · 当前稿已更新，可另建快照复核'}
          </p>
          <PackageEvidence value={value} />
          <PackageMaterials value={value} path={path} />
          <div className="writing-workbench-actions">
            <button
              type="button"
              className="primary"
              disabled={busy || loading || status !== 'ready' || !!draft?.edited}
              onClick={() => void finalize()}
            >
              确认完本
            </button>
            {value.finished && (
              <a className="button" href={`/api${path}/packages/${value.id}/download`} download>
                下载完整交付包
              </a>
            )}
          </div>
        </section>
      )}
    </section>
  )
}
