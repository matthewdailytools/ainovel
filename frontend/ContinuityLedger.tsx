import { useEffect, useRef, useState } from 'react'
import { api, type Chapter } from './api'
import type { components } from './api-schema'
import type { Selection } from './Editor'
import { useLeaveGuard } from './leaveGuard'
import type { WritingSetting } from './WritingContext'

const stages = {
  effective: '生效',
  planted: '埋设',
  reinforced: '推进',
  resolved: '回收',
} as const
type Schema = components['schemas']
type StageView = Schema['LedgerStage']
type Entry = Schema['LedgerEntry']
type Stage = StageView['stage']
type Kind = Entry['kind']
type Window = Schema['Window']
type LedgerChapter = Schema['LedgerChapter']
type Source = Schema['Source']
type Plan = Required<Omit<Schema['PlanCreate'], 'client_request_id' | 'windows'>> & {
  windows: Partial<Record<Stage, Window | null>>
}
export type LedgerTarget = { chapter_id: string; entry_id: string; stage?: Stage; source?: Source }
type Ledger = Schema['ContinuityLedger']

const windowLabels = {
  none: '未设窗口',
  future: '尚未进入窗口',
  open: '窗口已开启',
  due: '本章到期',
  overdue: '已过窗口',
  invalid: '窗口需要调整',
}
const reminderLabels = {
  recheck: '待复检',
  window_open: '窗口已开启',
  window_due: '到期',
  window_overdue: '已过窗口',
}

export function useContinuityLedger(branchId: string, chapter?: Chapter) {
  const path = `/branches/${branchId}/continuity-ledger`
  const [refresh, setRefresh] = useState(0)
  const [state, setState] = useState<{ key: string; data?: Ledger; error?: string }>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const writing = useRef(false)
  const key = `${chapter?.id ?? ''}:${chapter?.revision ?? ''}:${refresh}`

  useEffect(() => {
    let active = true
    void api<Ledger>(path)
      .then((data) => {
        if (active) setState({ key, data })
      })
      .catch((error: unknown) => {
        if (active)
          setState((prior) => ({
            key,
            data: prior?.data,
            error: error instanceof Error ? error.message : '台账读取失败。',
          }))
      })
    return () => {
      active = false
    }
  }, [key, path])

  const reload = () => setRefresh((value) => value + 1)
  useEffect(() => {
    window.addEventListener('focus', reload)
    return () => window.removeEventListener('focus', reload)
  }, [])

  async function update(
    endpoint: string,
    method: 'POST' | 'PUT',
    body: Schema['PlanCreate'] | Schema['PlanUpdate'] | Schema['EvidenceInput'],
  ) {
    if (writing.current) return undefined
    writing.current = true
    setBusy(true)
    setError('')
    try {
      const entry = await api<Entry>(path + endpoint, method, body)
      reload()
      return entry
    } catch (error) {
      setError(error instanceof Error ? error.message : '台账保存失败，已保留本次输入。')
      reload()
      return undefined
    } finally {
      writing.current = false
      setBusy(false)
    }
  }

  return {
    path,
    data: state?.data,
    loading: state?.key !== key,
    ready: state?.key === key && !state.error,
    error: error || state?.error,
    busy,
    reload,
    update,
  }
}
type LedgerState = ReturnType<typeof useContinuityLedger>

function chapterName(chapters: LedgerChapter[], id: string) {
  const chapter = chapters.find((chapter) => chapter.id === id)
  return chapter ? `第 ${chapter.position} 章 ${chapter.title}` : '已不可用的章节'
}

function planOf(entry: Entry): Plan {
  return {
    kind: entry.kind,
    title: entry.title,
    detail: entry.detail,
    setting_id: entry.setting_id,
    setting_chapter_id: entry.setting_chapter_id,
    affected_chapter_ids: entry.affected_chapter_ids,
    windows: Object.fromEntries(entry.stages.map((stage) => [stage.stage, stage.window])),
  }
}

function WindowFields({
  value,
  onChange,
  chapters,
}: {
  value: Window
  onChange: (value: Window) => void
  chapters: LedgerChapter[]
}) {
  return (
    <div className="ledger-window-fields">
      <label>
        窗口起章
        <select
          value={value.start_chapter_id}
          onChange={(event) => onChange({ ...value, start_chapter_id: event.target.value })}
        >
          <option value="">未指定</option>
          {value.start_chapter_id && !chapters.some((chapter) => chapter.id === value.start_chapter_id) && (
            <option value={value.start_chapter_id}>原起章已不可用</option>
          )}
          {chapters.map((chapter) => (
            <option key={chapter.id} value={chapter.id}>
              {chapterName(chapters, chapter.id)}
            </option>
          ))}
        </select>
      </label>
      <label>
        窗口止章
        <select
          value={value.end_chapter_id}
          onChange={(event) => onChange({ ...value, end_chapter_id: event.target.value })}
        >
          <option value="">未指定</option>
          {value.end_chapter_id && !chapters.some((chapter) => chapter.id === value.end_chapter_id) && (
            <option value={value.end_chapter_id}>原止章已不可用</option>
          )}
          {chapters.map((chapter) => (
            <option key={chapter.id} value={chapter.id}>
              {chapterName(chapters, chapter.id)}
            </option>
          ))}
        </select>
      </label>
    </div>
  )
}

function emptyPlan(kind: Kind): Plan {
  return {
    kind,
    title: '',
    detail: '',
    setting_id: null,
    setting_chapter_id: null,
    affected_chapter_ids: [],
    windows: {},
  }
}

function selectedSource(chapter: Chapter, selection: Selection): Source | undefined {
  if (!selection.text.trim() || selection.end <= selection.start) return undefined
  const offset = (position: number) => {
    for (const newline of chapter.content.matchAll(/\r\n/g)) {
      if (newline.index >= position) break
      position += 1
    }
    return position
  }
  const start = offset(selection.start)
  const end = offset(selection.end)
  const quote = chapter.content.slice(start, end)
  if (quote.length > 4000 || quote.replace(/\r\n?/g, '\n') !== selection.text) return undefined
  return { chapter_id: chapter.id, revision: chapter.revision, start, end, quote }
}

export function TodayBar({
  ledger,
  disabled,
  visit,
}: {
  ledger: LedgerState
  disabled: boolean
  visit: (target: LedgerTarget) => void
}) {
  const actions = ledger.ready ? (ledger.data?.reminders ?? []) : []
  if (!actions.length) return null
  return (
    <section className="ledger-today" aria-label="今日条">
      <h2>今日条</h2>
      <ul>
        {actions.map((target) => (
          <li key={`${target.entry_id}:${target.stage}:${target.kind}`}>
            <button type="button" disabled={disabled} onClick={() => visit(target)}>
              {target.title} · {stages[target.stage]}
              {reminderLabels[target.kind]} · {chapterName(ledger.data?.chapters ?? [], target.chapter_id)}
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}

function InitialPlan({
  kind,
  chapter,
  settings,
  busy,
  submit,
  cancel,
}: {
  kind: Kind
  chapter: Chapter
  settings: WritingSetting[]
  busy: boolean
  submit: (plan: Plan) => Promise<boolean>
  cancel: () => void
}) {
  const [plan, setPlan] = useState(() => emptyPlan(kind))
  const options = settings.filter((setting) => setting.source_current)
  const setting = options.find((item) => item.id === plan.setting_id)
  useLeaveGuard(!!(plan.title || plan.detail || plan.setting_id), '未保存的台账计划')

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault()
        if (plan.title.trim() && (kind === 'foreshadow' || setting))
          void submit({ ...plan, title: plan.title.trim(), detail: plan.detail.trim() })
      }}
    >
      <h4>{kind === 'setting' ? '新建设定计划' : '新建伏笔计划'}</h4>
      {kind === 'setting' && (
        <label>
          采用的设定
          <select
            required
            value={plan.setting_id ?? ''}
            onChange={(event) => {
              const picked = options.find((item) => item.id === event.target.value)
              setPlan({
                ...plan,
                title: plan.title || picked?.title || '',
                setting_id: picked?.id ?? null,
                setting_chapter_id: picked ? chapter.id : null,
              })
            }}
          >
            <option value="">选择本章可查的设定</option>
            {options.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title}
              </option>
            ))}
          </select>
          {!options.length && <small>当前章没有可用的已采用设定。可切到有对应依据的章节。</small>}
        </label>
      )}
      <label>
        计划名称
        <input
          value={plan.title}
          onChange={(event) => setPlan({ ...plan, title: event.target.value })}
          maxLength={200}
          required
        />
      </label>
      <label>
        计划说明（可选）
        <textarea
          value={plan.detail}
          onChange={(event) => setPlan({ ...plan, detail: event.target.value })}
          rows={2}
          maxLength={4000}
        />
      </label>
      <div className="writing-workbench-actions">
        <button type="submit" disabled={busy || !plan.title.trim() || (kind === 'setting' && !setting)}>
          保存计划
        </button>
        <button type="button" onClick={cancel} disabled={busy}>
          取消
        </button>
      </div>
    </form>
  )
}

function RelatedChapters({
  entry,
  chapters,
  disabled,
  onSave,
  visit,
}: {
  entry: Entry
  chapters: LedgerChapter[]
  disabled: boolean
  onSave: (plan: Plan, revision: number) => Promise<boolean>
  visit: (target: LedgerTarget) => void
}) {
  const [draft, setDraft] = useState<{ base: Entry; ids: string[] }>()
  useLeaveGuard(!!draft, '未保存的台账关联章节')
  const related = entry.affected_chapters
  const impacted = entry.impacted_chapters
  return (
    <div className="ledger-related">
      {related.length > 0 && (
        <p className="small">
          已登记关联章节 · {related.map((chapter) => chapterName(chapters, chapter.id)).join('、')}
        </p>
      )}
      {impacted.length > 0 && (
        <div>
          <h5>受影响的章节</h5>
          <ul>
            {impacted.map((chapter) => (
              <li key={chapter.id}>
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => visit({ chapter_id: chapter.id, entry_id: entry.id })}
                >
                  查看{chapterName(chapters, chapter.id)}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {draft ? (
        <form
          onSubmit={(event) => {
            event.preventDefault()
            void onSave({ ...planOf(draft.base), affected_chapter_ids: draft.ids }, draft.base.revision).then(
              (saved) => {
                if (saved) setDraft(undefined)
              },
            )
          }}
        >
          <fieldset>
            <legend>依赖此计划或证据的章节</legend>
            {chapters.map((chapter) => (
              <label key={chapter.id} className="check">
                <input
                  type="checkbox"
                  checked={draft.ids.includes(chapter.id)}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      ids: event.target.checked
                        ? [...draft.ids, chapter.id]
                        : draft.ids.filter((id) => id !== chapter.id),
                    })
                  }
                />
                {chapterName(chapters, chapter.id)}
              </label>
            ))}
          </fieldset>
          <div className="writing-workbench-actions">
            <button type="submit" disabled={disabled}>
              保存关联章节
            </button>
            <button type="button" disabled={disabled} onClick={() => setDraft(undefined)}>
              取消
            </button>
          </div>
        </form>
      ) : (
        <button
          type="button"
          disabled={disabled}
          onClick={() =>
            setDraft({
              base: entry,
              ids: entry.affected_chapter_ids.filter((id) => chapters.some((chapter) => chapter.id === id)),
            })
          }
        >
          编辑关联章节
        </button>
      )}
    </div>
  )
}

function EvidenceQuote({
  source,
  current,
  chapters,
}: {
  source: Source
  current?: boolean
  chapters: LedgerChapter[]
}) {
  return (
    <div>
      <p className="small muted">
        {chapterName(chapters, source.chapter_id)} · 修订 {source.revision}
        {current === false && ' · 历史原文'}
      </p>
      <blockquote>{source.quote}</blockquote>
    </div>
  )
}

function ReviewBasis({ stage, chapters }: { stage: StageView; chapters: LedgerChapter[] }) {
  if (!stage.source) return null
  return (
    <>
      <div role="group" aria-label="正文证据">
        <EvidenceQuote source={stage.source} current={stage.source_current} chapters={chapters} />
        {stage.additional_sources.map((source, index) => (
          <EvidenceQuote key={index} source={source} current={source.source_current} chapters={chapters} />
        ))}
      </div>
      {stage.setting_basis && (
        <details>
          <summary>核对时使用的设定</summary>
          <blockquote>{stage.setting_basis.text}</blockquote>
          {stage.setting_basis.source && (
            <EvidenceQuote source={stage.setting_basis.source} chapters={chapters} />
          )}
        </details>
      )}
      {stage.note && <p>{stage.note}</p>}
    </>
  )
}

function StageCard({
  stage,
  entry,
  ledger,
  chapter,
  selected,
  saved,
  disabled,
  target,
  onPlan,
  onVisit,
}: {
  stage: StageView
  entry: Entry
  ledger: LedgerState
  chapter: Chapter
  selected?: Source
  saved: boolean
  disabled: boolean
  target?: LedgerTarget
  onPlan: (plan: Plan, revision: number) => Promise<boolean>
  onVisit: (target: LedgerTarget) => void
}) {
  const [draft, setDraft] = useState<{ base: Entry; window: Window }>()
  const heading = useRef<HTMLHeadingElement>(null)
  const chapters = ledger.data?.chapters ?? []
  const state = stage.review_status
  const matchingTarget = target?.entry_id === entry.id && target.stage === stage.stage ? target : undefined
  const source = stage.source
  const sameSource =
    saved &&
    stage.source_current &&
    stage.source?.chapter_id === chapter.id &&
    stage.source.revision === chapter.revision
      ? stage.source
      : undefined
  const validWindow = !draft || !!draft.window.start_chapter_id === !!draft.window.end_chapter_id
  useLeaveGuard(!!draft, '未保存的台账窗口')

  useEffect(() => {
    if (!matchingTarget || matchingTarget.source) return
    const node = heading.current
    if (!node) return
    const details = node.closest('details')
    if (details) details.open = true
    node.focus({ preventScroll: true })
    node.scrollIntoView({ block: 'nearest' })
  }, [matchingTarget])

  async function record(source?: Source) {
    if (!source || disabled || stage.repair_blocked) return
    await ledger.update(`/entries/${entry.id}/evidence`, 'PUT', {
      base_revision: entry.revision,
      stage: stage.stage,
      source,
      note: stage.note,
      expected_setting: entry.setting_basis,
    })
  }

  return (
    <section className="ledger-stage" aria-label={stages[stage.stage]}>
      <h5 ref={heading} tabIndex={-1}>
        {stages[stage.stage]}
      </h5>
      <p className="small">
        {stage.window
          ? `计划窗口 · ${chapterName(chapters, stage.window.start_chapter_id)} 至 ${chapterName(chapters, stage.window.end_chapter_id)} · ${stage.window_status === 'due' && stage.window.end_chapter_id !== chapter.id ? '进度已到计划止章' : windowLabels[stage.window_status]}`
          : '未设计划窗口'}
      </p>
      <p className="small">
        {state === 'stale'
          ? '待复检'
          : state === 'outside_window'
            ? '证据在计划窗口外'
            : state === 'current'
              ? '证据已核对'
              : '未登记正文证据'}
        {stage.review_reason && ` · ${stage.review_reason}`}
      </p>
      <ReviewBasis stage={stage} chapters={chapters} />
      {stage.additional_sources.length > 0 && (
        <p className="small">使用选区复检将更新本阶段证据，原有引文仍可在台账历史查看。</p>
      )}
      <div className="writing-workbench-actions">
        {stage.source_current && source && chapters.some((row) => row.id === source.chapter_id) && (
          <button
            type="button"
            disabled={disabled}
            onClick={() =>
              onVisit({ chapter_id: source.chapter_id, entry_id: entry.id, stage: stage.stage, source })
            }
          >
            定位正文原文
          </button>
        )}
        <button
          type="button"
          onClick={() => void record(selected)}
          disabled={disabled || !selected || stage.repair_blocked}
        >
          {stage.source ? '用当前选区复检' : '记录正文选区'}
        </button>
        {state === 'stale' && sameSource && !stage.repair_blocked && stage.window_status !== 'invalid' && (
          <button type="button" onClick={() => void record(sameSource)} disabled={disabled}>
            用原引文复检
          </button>
        )}
      </div>
      {draft ? (
        <form
          onSubmit={(event) => {
            event.preventDefault()
            if (!validWindow || disabled) return
            const plan = planOf(draft.base)
            plan.windows[stage.stage] = draft.window.start_chapter_id ? draft.window : null
            void onPlan(plan, draft.base.revision).then((saved) => {
              if (saved) setDraft(undefined)
            })
          }}
        >
          <WindowFields
            value={draft.window}
            onChange={(window) => setDraft({ ...draft, window })}
            chapters={chapters}
          />
          {!validWindow && <p className="small">请同时选择起章和止章，或都留空。</p>}
          <div className="writing-workbench-actions">
            <button type="submit" disabled={disabled || !validWindow}>
              保存计划窗口
            </button>
            <button type="button" disabled={disabled} onClick={() => setDraft(undefined)}>
              取消
            </button>
          </div>
        </form>
      ) : (
        <button
          type="button"
          className="ledger-adjust"
          disabled={disabled}
          onClick={() =>
            setDraft({ base: entry, window: stage.window ?? { start_chapter_id: '', end_chapter_id: '' } })
          }
        >
          调整计划窗口
        </button>
      )}
    </section>
  )
}

function LedgerHistory({ entry, ledger }: { entry: Entry; ledger: LedgerState }) {
  const [versions, setVersions] = useState<Entry[]>()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  async function load() {
    setLoading(true)
    setError('')
    try {
      setVersions(await api<Entry[]>(`${ledger.path}/entries/${entry.id}/history`))
    } catch (error) {
      setError(error instanceof Error ? error.message : '台账历史读取失败。')
    } finally {
      setLoading(false)
    }
  }
  const chapters = ledger.data?.chapters ?? []
  return (
    <>
      <button type="button" onClick={() => void load()} disabled={loading}>
        {loading ? '正在读取台账历史…' : '查看台账历史'}
      </button>
      {error && <p role="alert">{error}</p>}
      {versions && (
        <section aria-label="台账历史">
          {versions
            .filter((version) => version.revision !== entry.revision)
            .map((version) => (
              <details key={version.revision}>
                <summary>台账修订 {version.revision}</summary>
                {version.stages.map((stage) => (
                  <div key={stage.stage}>
                    <strong>{stages[stage.stage]}</strong>
                    <p className="small">
                      {stage.window
                        ? `原窗口 · ${chapterName(chapters, stage.window.start_chapter_id)} 至 ${chapterName(chapters, stage.window.end_chapter_id)}`
                        : '当时未设窗口'}
                      {stage.review_reason && ` · ${stage.review_reason}`}
                    </p>
                    <ReviewBasis stage={stage} chapters={chapters} />
                  </div>
                ))}
              </details>
            ))}
          {!versions.some((version) => version.revision !== entry.revision) && (
            <p className="small">还没有更早的台账修订。</p>
          )}
        </section>
      )}
    </>
  )
}

function EntryCard({
  entry,
  ledger,
  chapter,
  selected,
  saved,
  disabled,
  target,
  opened,
  visit,
}: {
  entry: Entry
  ledger: LedgerState
  chapter: Chapter
  selected?: Source
  saved: boolean
  disabled: boolean
  target?: LedgerTarget
  opened: boolean
  visit: (target: LedgerTarget) => void
}) {
  const summary = useRef<HTMLElement>(null)
  const matchingTarget = target?.entry_id === entry.id && !target.stage ? target : undefined
  useEffect(() => {
    if (!matchingTarget || !summary.current) return
    const card = summary.current.closest('details')
    if (card) card.open = true
    summary.current.focus({ preventScroll: true })
    summary.current.scrollIntoView({ block: 'nearest' })
  }, [matchingTarget])

  const savePlan = async (plan: Plan, revision: number) =>
    !!(await ledger.update(`/entries/${entry.id}/plan`, 'PUT', { ...plan, base_revision: revision }))

  return (
    <article className="fact" aria-label={entry.title}>
      <details open={opened || undefined}>
        <summary ref={summary}>
          {entry.title} · {entry.kind === 'setting' ? '设定' : '伏笔'}
        </summary>
        {entry.detail && <p>{entry.detail}</p>}
        {entry.setting_basis && (
          <details>
            <summary>当前关联设定</summary>
            <blockquote>{entry.setting_basis.text}</blockquote>
            {entry.setting_basis.source && (
              <EvidenceQuote source={entry.setting_basis.source} chapters={ledger.data?.chapters ?? []} />
            )}
          </details>
        )}
        {entry.graph_warning && <p className="small">{entry.graph_warning}</p>}
        {entry.closed && <p className="small">已处理</p>}
        {entry.stages.map((stage) => (
          <StageCard
            key={stage.stage}
            {...{ stage, entry, ledger, chapter, selected, saved, disabled, target }}
            onPlan={savePlan}
            onVisit={visit}
          />
        ))}
        <RelatedChapters
          entry={entry}
          chapters={ledger.data?.chapters ?? []}
          disabled={disabled}
          onSave={savePlan}
          visit={visit}
        />
        <LedgerHistory entry={entry} ledger={ledger} />
      </details>
    </article>
  )
}

export function ContinuityLedger({
  ledger,
  chapter,
  selection,
  saved,
  disabled,
  settings,
  target,
  visit,
}: {
  ledger: LedgerState
  chapter: Chapter
  selection: Selection
  saved: boolean
  disabled: boolean
  settings?: WritingSetting[]
  target?: LedgerTarget
  visit: (target: LedgerTarget) => void
}) {
  const [creating, setCreating] = useState<Kind>()
  const [opened, setOpened] = useState('')
  const pendingCreate = useRef<{ signature: string; id: string } | undefined>(undefined)
  const selected = saved ? selectedSource(chapter, selection) : undefined
  const busy = disabled || ledger.busy || !ledger.ready
  return (
    <section className="notice writing-workbench-section" aria-label="设定与伏笔台账">
      <div className="section-title">
        <h3>设定与伏笔台账</h3>
        <button type="button" onClick={ledger.reload} disabled={ledger.busy}>
          刷新台账
        </button>
      </div>
      {ledger.error && <p role="alert">{ledger.error}</p>}
      {ledger.loading && (
        <p role="status" className="small">
          正在读取台账…
        </p>
      )}
      {ledger.busy && (
        <p role="status" className="small">
          正在保存台账…
        </p>
      )}
      {ledger.data && (
        <>
          <p className="small muted">
            {saved
              ? '在已保存正文选中文字，即可登记原文或复检相应阶段。'
              : '请先保存正文，再登记原文或复检。'}
          </p>
          <div className="writing-workbench-actions">
            <button type="button" disabled={busy || !!creating} onClick={() => setCreating('setting')}>
              新建设定计划
            </button>
            <button type="button" disabled={busy || !!creating} onClick={() => setCreating('foreshadow')}>
              新建伏笔计划
            </button>
          </div>
          {creating && (
            <InitialPlan
              key={creating}
              kind={creating}
              chapter={chapter}
              settings={settings ?? []}
              busy={busy}
              cancel={() => {
                pendingCreate.current = undefined
                setCreating(undefined)
              }}
              submit={async (plan) => {
                const signature = JSON.stringify(plan)
                if (pendingCreate.current?.signature !== signature)
                  pendingCreate.current = { signature, id: crypto.randomUUID() }
                const entry = await ledger.update('/entries', 'POST', {
                  ...plan,
                  client_request_id: pendingCreate.current.id,
                })
                if (!entry) return false
                pendingCreate.current = undefined
                setOpened(entry.id)
                setCreating(undefined)
                return true
              }}
            />
          )}
          {ledger.data.entries.map((entry) => (
            <EntryCard
              key={entry.id}
              {...{ entry, ledger, chapter, selected, saved, target, visit }}
              disabled={busy}
              opened={opened === entry.id}
            />
          ))}
        </>
      )}
    </section>
  )
}
