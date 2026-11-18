import { useEffect, useRef, useState } from 'react'
import { createDraftOwner } from './drafts'
import { useLeaveGuard } from './leaveGuard'
import type { SettingsEvidence } from './settingsSyncModel'
import type { ConfirmOfflineStory, createStoryOfflineStore } from './storyOfflineStore'
import {
  captureStoryEvidence,
  describeStoryVersion,
  parseLocalStoryDraft,
  storyPromiseStages,
  type StoryChapterRevision,
  type StoryPromiseStage,
  type StoryRecord,
} from './storySyncModel'
import { useWorkbenchDraft, WorkbenchDraftRecovery } from './workbenchDrafts'

type OfflineStoryData = Awaited<ReturnType<ReturnType<typeof createStoryOfflineStore>['readLocalStory']>>
type Kind = StoryRecord['kind']
type Option = { id: string; title: string }
type Chapter = Option & { archived?: boolean | number }
type ReadChapter = (chapterId: string) => Promise<StoryChapterRevision | undefined>

const kindLabels: Record<Kind, string> = {
  character: '人物',
  event: '事件',
  state: '状态',
  relationship: '关系',
  promise: '伏笔',
}
const stageLabels: Record<StoryPromiseStage['status'], string> = {
  planned: '计划',
  planted: '已埋设',
  reinforced: '已强化',
  resolved: '已回收',
  retained: '作者保留',
  abandoned: '作者放弃',
}
const truthLabels = { planned: '计划', actual: '已发生', unknown: '未知', conflict: '待核对' } as const
const defaultStage: StoryPromiseStage = { status: 'planned', event_id: null, note: '', sources: [] }
const owner = createDraftOwner('ainovel-story-draft-window')

function blankRecord(kind: Kind): StoryRecord {
  const base = { title: '', notes: '', volume: '', depends_on: [], sources: [] as [] }
  switch (kind) {
    case 'character':
      return { ...base, kind, aliases: [], profile: '' }
    case 'event':
      return {
        ...base,
        kind,
        thread: '主线',
        story_order: null,
        story_time: '未知',
        status: 'planned',
        participants: [],
        after: [],
      }
    case 'state':
      return {
        ...base,
        kind,
        character_id: '',
        event_id: '',
        until_event_id: null,
        category: 'other',
        key: '',
        value: '',
        audience: 'author',
        truth: 'planned',
      }
    case 'relationship':
      return { ...base, kind, from_id: '', to_id: '', relation: '', event_id: null, truth: 'planned' }
    case 'promise':
      return { ...base, kind, thread: '主线', stages: [defaultStage] }
  }
}

function StorySelect({
  label,
  value,
  rows,
  onChange,
}: {
  label: string
  value: string | null
  rows: Option[]
  onChange: (value: string) => void
}) {
  return (
    <label>
      {label}
      <select value={value ?? ''} onChange={(event) => onChange(event.target.value)}>
        <option value="">请选择</option>
        {value && !rows.some(({ id }) => id === value) && <option value={value}>待复检 · {value}</option>}
        {rows.map(({ id, title }) => (
          <option key={id} value={id}>
            {title}
          </option>
        ))}
      </select>
    </label>
  )
}

function StoryMultiSelect({
  label,
  value,
  rows,
  onChange,
}: {
  label: string
  value: string[]
  rows: Option[]
  onChange: (value: string[]) => void
}) {
  const missing = value.filter((id) => !rows.some((item) => item.id === id))
  if (!rows.length && !missing.length) return null
  return (
    <details>
      <summary>{label}</summary>
      {[...rows, ...missing.map((id) => ({ id, title: `待复检 · ${id}` }))].map(({ id, title }) => (
        <label key={id}>
          <input
            type="checkbox"
            checked={value.includes(id)}
            onChange={(event) =>
              onChange(event.target.checked ? [...value, id] : value.filter((selected) => selected !== id))
            }
          />
          {title}
        </label>
      ))}
    </details>
  )
}

function TruthEditor({
  value,
  onChange,
}: {
  value: keyof typeof truthLabels
  onChange: (truth: keyof typeof truthLabels) => void
}) {
  return (
    <label>
      记录状态
      <select value={value} onChange={(event) => onChange(event.target.value as keyof typeof truthLabels)}>
        {Object.entries(truthLabels).map(([key, label]) => (
          <option key={key} value={key}>
            {label}
          </option>
        ))}
      </select>
    </label>
  )
}

function ProofEditor({
  label,
  chapters,
  readChapter,
  evidence,
  onChange,
}: {
  label: string
  chapters: Chapter[]
  readChapter: ReadChapter
  evidence: SettingsEvidence[]
  onChange: (evidence: SettingsEvidence[]) => void
}) {
  const [chapterId, setChapterId] = useState('')
  const [saved, setSaved] = useState<StoryChapterRevision>()
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function run(action: () => Promise<void>) {
    setError('')
    setBusy(true)
    try {
      await action()
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '原文依据暂未保存。')
    } finally {
      setBusy(false)
    }
  }

  return (
    <details>
      <summary>
        {label} · {evidence.length} 处
      </summary>
      {error && <p role="alert">{error}</p>}
      {evidence.map((proof, index) => (
        <div key={index} className="actions" style={{ overflowWrap: 'anywhere' }}>
          <small>
            章节 {proof.chapter_id} · 保存修订 {proof.revision_id} · 选区 {proof.start}–{proof.end}
          </small>
          <button
            type="button"
            disabled={busy}
            onClick={() => onChange(evidence.filter((_, at) => at !== index))}
          >
            移除第 {index + 1} 处证据
          </button>
        </div>
      ))}
      <fieldset disabled={busy || evidence.length >= 30}>
        <legend>附上保存稿选区</legend>
        <StorySelect
          label="原文证据章节"
          rows={chapters.filter(({ archived }) => !archived)}
          value={chapterId}
          onChange={(id) => {
            setChapterId(id)
            setSaved(undefined)
            setStart('')
            setEnd('')
          }}
        />
        <button
          type="button"
          disabled={!chapterId}
          onClick={() =>
            void run(async () => {
              const revision = await readChapter(chapterId)
              if (!revision || revision.chapter_id !== chapterId) throw new Error('找不到所选章节的保存稿。')
              setSaved(revision)
            })
          }
        >
          读取证据章保存稿
        </button>
        {saved && (
          <>
            <label>
              可选取的保存稿
              <textarea
                readOnly
                value={saved.content}
                onSelect={(event) => {
                  setStart(String(event.currentTarget.selectionStart))
                  setEnd(String(event.currentTarget.selectionEnd))
                }}
              />
            </label>
            <label>
              选区起点
              <input type="number" min={0} value={start} onChange={(event) => setStart(event.target.value)} />
            </label>
            <label>
              选区终点
              <input type="number" min={1} value={end} onChange={(event) => setEnd(event.target.value)} />
            </label>
            <button
              type="button"
              disabled={start === '' || end === '' || Number(start) >= Number(end)}
              onClick={() =>
                void run(async () => {
                  const proof = await captureStoryEvidence(saved, Number(start), Number(end))
                  if (
                    evidence.some(
                      (row) =>
                        row.revision_id === proof.revision_id &&
                        row.start === proof.start &&
                        row.end === proof.end,
                    )
                  )
                    throw new Error('已附上这处保存稿选区。')
                  onChange([...evidence, proof])
                })
              }
            >
              附上所选原文
            </button>
          </>
        )}
      </fieldset>
    </details>
  )
}

function SpecificFields({
  record,
  people,
  events,
  onChange,
}: {
  record: StoryRecord
  people: Option[]
  events: Option[]
  onChange: (record: StoryRecord) => void
}) {
  switch (record.kind) {
    case 'character':
      return (
        <>
          <label>
            人物简介
            <textarea
              maxLength={8000}
              value={record.profile}
              onChange={(event) => onChange({ ...record, profile: event.target.value })}
            />
          </label>
          <label>
            别名（每行一个）
            <textarea
              value={record.aliases.join('\n')}
              onChange={(event) => onChange({ ...record, aliases: event.target.value.split('\n') })}
            />
          </label>
        </>
      )
    case 'event':
      return (
        <>
          <TruthEditor value={record.status} onChange={(status) => onChange({ ...record, status })} />
          <label>
            故事时间
            <input
              maxLength={200}
              value={record.story_time}
              onChange={(event) => onChange({ ...record, story_time: event.target.value })}
            />
          </label>
          <label>
            故事顺序
            <input
              type="number"
              value={record.story_order ?? ''}
              onChange={(event) =>
                onChange({
                  ...record,
                  story_order: event.target.value === '' ? null : Number(event.target.value),
                })
              }
            />
          </label>
          <StoryMultiSelect
            label="参与人物"
            rows={people}
            value={record.participants}
            onChange={(participants) => onChange({ ...record, participants })}
          />
          <StoryMultiSelect
            label="先行事件"
            rows={events}
            value={record.after}
            onChange={(after) => onChange({ ...record, after })}
          />
        </>
      )
    case 'state':
      return (
        <>
          <TruthEditor value={record.truth} onChange={(truth) => onChange({ ...record, truth })} />
          <StorySelect
            label="对应人物"
            rows={people}
            value={record.character_id}
            onChange={(character_id) => onChange({ ...record, character_id })}
          />
          <StorySelect
            label="发生事件"
            rows={events}
            value={record.event_id}
            onChange={(event_id) => onChange({ ...record, event_id })}
          />
          <StorySelect
            label="结束事件（可选）"
            rows={events}
            value={record.until_event_id}
            onChange={(id) => onChange({ ...record, until_event_id: id || null })}
          />
          <label>
            状态类别
            <select
              value={record.category}
              onChange={(event) =>
                onChange({ ...record, category: event.target.value as typeof record.category })
              }
            >
              <option value="knowledge">所知</option>
              <option value="capability">能力</option>
              <option value="resource">资源</option>
              <option value="injury">伤势</option>
              <option value="other">其他</option>
            </select>
          </label>
          <label>
            可见范围
            <select
              value={record.audience}
              onChange={(event) =>
                onChange({ ...record, audience: event.target.value as typeof record.audience })
              }
            >
              <option value="author">作者</option>
              <option value="character">人物</option>
              <option value="reader">读者</option>
            </select>
          </label>
          <label>
            状态字段
            <input
              maxLength={100}
              value={record.key}
              onChange={(event) => onChange({ ...record, key: event.target.value })}
            />
          </label>
          <label>
            状态内容
            <textarea
              maxLength={4000}
              value={record.value}
              onChange={(event) => onChange({ ...record, value: event.target.value })}
            />
          </label>
        </>
      )
    case 'relationship':
      return (
        <>
          <TruthEditor value={record.truth} onChange={(truth) => onChange({ ...record, truth })} />
          <StorySelect
            label="关系起点"
            rows={people}
            value={record.from_id}
            onChange={(from_id) => onChange({ ...record, from_id })}
          />
          <StorySelect
            label="关系另一方"
            rows={people}
            value={record.to_id}
            onChange={(to_id) => onChange({ ...record, to_id })}
          />
          <StorySelect
            label="关联事件（可选）"
            rows={events}
            value={record.event_id}
            onChange={(id) => onChange({ ...record, event_id: id || null })}
          />
          <label>
            关系描述
            <textarea
              maxLength={500}
              value={record.relation}
              onChange={(event) => onChange({ ...record, relation: event.target.value })}
            />
          </label>
        </>
      )
    case 'promise':
      return null
  }
}

export function OfflineStory({
  branchId,
  read,
  readChapter,
  chapters,
  onConfirm,
  refreshToken,
  disabled,
  onDirty,
}: {
  branchId: string
  read: () => Promise<OfflineStoryData>
  readChapter: ReadChapter
  chapters: Chapter[]
  onConfirm: (draft: ConfirmOfflineStory) => Promise<unknown>
  refreshToken: number
  disabled: boolean
  onDirty: (dirty: boolean) => void
}) {
  const [data, setData] = useState<OfflineStoryData>()
  const [kind, setKind] = useState<Kind>('promise')
  const {
    draft,
    setDraft,
    recovery,
    loaded,
    error: draftError,
    status: draftStatus,
  } = useWorkbenchDraft({
    key: `offline/story-record/${branchId}`,
    label: '故事记录',
    owner,
    parse: parseLocalStoryDraft,
  })
  const [selected, setSelected] = useState('')
  const [historyId, setHistoryId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const requests = useRef(0)
  const chosen = data?.records.find(({ head }) => head.recordId === selected)
  const currentHistory = data?.versions.find(
    ({ version }) => version.id === (historyId || chosen?.version.id),
  )
  const available =
    data?.records.filter(({ version }) => !version.archived && version.record_id !== draft?.record_id) ?? []
  const options = (kind?: Kind) =>
    available
      .filter(({ version }) => !kind || version.record.kind === kind)
      .map(({ version }) => ({ id: version.record_id, title: version.record.title }))
  const currentVersion = data?.records.find(({ head }) => head.recordId === draft?.record_id)?.version.id
  const stale =
    !!draft &&
    (draft.baseStoryRevision !== data?.storyRevision || draft.baseVersionId !== (currentVersion ?? null))
  const dirty = !!draft
  const editingBlocked = disabled || busy || dirty || !loaded || recovery?.foreign === false
  useLeaveGuard(dirty, '离线故事记录')
  useEffect(() => {
    onDirty(dirty)
    return () => onDirty(false)
  }, [dirty, onDirty])

  useEffect(() => {
    const attempt = ++requests.current
    void read().then(
      (result) => {
        if (attempt === requests.current) setData(result)
      },
      (failure: Error) => {
        if (attempt === requests.current) setError(failure.message)
      },
    )
    return () => {
      requests.current++
    }
  }, [read, refreshToken])

  function start(record: StoryRecord, id: string, old?: NonNullable<typeof chosen>) {
    if (!data || editingBlocked) return
    setDraft({
      record,
      record_id: id,
      archived: old?.version.archived ?? false,
      baseStoryRevision: data.storyRevision,
      baseVersionId: old?.version.id ?? null,
      evidence: old?.version.evidence ?? {
        record: [],
        stages: record.kind === 'promise' ? record.stages.map(() => []) : [],
      },
    })
    setError('')
    setStatus('')
  }

  function edit(record: StoryRecord) {
    if (draft) setDraft({ ...draft, record })
  }

  async function confirm() {
    if (!draft || stale) return
    setBusy(true)
    setError('')
    try {
      const record =
        draft.record.kind === 'character'
          ? { ...draft.record, aliases: draft.record.aliases.map((value) => value.trim()).filter(Boolean) }
          : draft.record
      await onConfirm({ ...draft, record })
      setSelected(draft.record_id)
      setHistoryId('')
      setDraft(undefined)
      setStatus('已确认并保存故事记录。')
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '故事记录未保存，当前输入已保留。')
    } finally {
      await read().then(setData, () => undefined)
      setBusy(false)
    }
  }

  return (
    <details aria-label="离线故事记录">
      <summary>故事记录</summary>
      {error && <p role="alert">{error}</p>}
      {status && <p role="status">{status}</p>}
      {draftError && <p role="alert">{draftError}</p>}
      {draftStatus && <p role="status">{draftStatus}</p>}
      {recovery && !draft && (
        <WorkbenchDraftRecovery
          label="故事记录"
          foreign={recovery.foreign}
          disabled={disabled || busy || !data}
          onDiscard={() => setDraft(undefined)}
          onEdit={() => {
            setDraft(recovery.value)
            setSelected(recovery.value.record_id)
            setHistoryId('')
            setError('')
            setStatus('')
          }}
        />
      )}
      {!data && !error && <p role="status">正在读取本机故事记录…</p>}
      {!!data?.records.length && (
        <StorySelect
          label="查看故事记录"
          value={selected}
          rows={data.records.map(({ version }) => ({
            id: version.record_id,
            title: `${kindLabels[version.record.kind]} · ${version.record.title}${version.archived ? '（已归档）' : ''}`,
          }))}
          onChange={(id) => {
            setSelected(id)
            setHistoryId('')
          }}
        />
      )}
      {chosen && (
        <>
          {chosen.invalidReasons.map((reason) => (
            <p key={reason} className="small">
              {reason}
            </p>
          ))}
          <button
            type="button"
            disabled={editingBlocked}
            onClick={() => start(chosen.version.record, chosen.head.recordId, chosen)}
          >
            编辑所选故事记录
          </button>
          <details>
            <summary>故事记录历史</summary>
            <StorySelect
              label="历史来源修订"
              value={historyId || chosen.version.id}
              rows={
                data?.versions
                  .filter(({ version }) => version.record_id === selected)
                  .map(({ version }) => ({ id: version.id, title: version.id.slice(0, 12) })) ?? []
              }
              onChange={setHistoryId}
            />
            {currentHistory && (
              <pre style={{ overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}>
                {describeStoryVersion(currentHistory.version)}
              </pre>
            )}
          </details>
        </>
      )}
      {!!data?.planned.length && (
        <details>
          <summary>大纲中的伏笔计划</summary>
          {data.planned.map(({ id, seed }) => (
            <button key={id} type="button" disabled={editingBlocked} onClick={() => start(seed, id)}>
              记录「{seed.title}」的进展
            </button>
          ))}
        </details>
      )}
      {!draft && data && (
        <fieldset disabled={editingBlocked}>
          <legend>新建故事记录</legend>
          <label>
            记录类型
            <select value={kind} onChange={(event) => setKind(event.target.value as Kind)}>
              {Object.entries(kindLabels).map(([id, title]) => (
                <option key={id} value={id}>
                  {title}
                </option>
              ))}
            </select>
          </label>
          <button type="button" onClick={() => start(blankRecord(kind), crypto.randomUUID())}>
            添加故事记录
          </button>
        </fieldset>
      )}
      {draft && (
        <fieldset disabled={disabled || busy}>
          <legend>编辑{kindLabels[draft.record.kind]}</legend>
          {stale && <p role="alert">故事版本已变化。当前编辑保留，请与保存版比较后重新填写。</p>}
          <label>
            记录名称
            <input
              maxLength={200}
              value={draft.record.title}
              onChange={(event) => edit({ ...draft.record, title: event.target.value })}
            />
          </label>
          <label>
            记录说明
            <textarea
              maxLength={4000}
              value={draft.record.notes}
              onChange={(event) => edit({ ...draft.record, notes: event.target.value })}
            />
          </label>
          <SpecificFields
            record={draft.record}
            people={options('character')}
            events={options('event')}
            onChange={edit}
          />
          <StoryMultiSelect
            label="关联故事记录"
            rows={options()}
            value={draft.record.depends_on}
            onChange={(depends_on) => edit({ ...draft.record, depends_on })}
          />
          <ProofEditor
            label="记录原文证据"
            chapters={chapters}
            readChapter={readChapter}
            evidence={draft.evidence.record}
            onChange={(record) =>
              setDraft((current) => current && { ...current, evidence: { ...current.evidence, record } })
            }
          />
          {draft.record.kind === 'promise' && (
            <fieldset>
              <legend>伏笔阶段</legend>
              {draft.record.stages.map((stage, index) => {
                const update = (next: StoryPromiseStage) => {
                  if (draft.record.kind === 'promise')
                    edit({
                      ...draft.record,
                      stages: draft.record.stages.map((at, position) => (position === index ? next : at)),
                    })
                }
                return (
                  <fieldset key={index}>
                    <legend>阶段 {index + 1}</legend>
                    <label>
                      阶段状态
                      <select
                        value={stage.status}
                        onChange={(event) =>
                          update({ ...stage, status: event.target.value as StoryPromiseStage['status'] })
                        }
                      >
                        {storyPromiseStages.map((status) => (
                          <option key={status} value={status}>
                            {stageLabels[status]}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      阶段说明
                      <textarea
                        maxLength={2000}
                        value={stage.note}
                        onChange={(event) => update({ ...stage, note: event.target.value })}
                      />
                    </label>
                    <StorySelect
                      label="阶段事件（可选）"
                      rows={options('event')}
                      value={stage.event_id}
                      onChange={(id) => update({ ...stage, event_id: id || null })}
                    />
                    <ProofEditor
                      label="阶段原文证据"
                      chapters={chapters}
                      readChapter={readChapter}
                      evidence={draft.evidence.stages[index]}
                      onChange={(proof) =>
                        setDraft(
                          (current) =>
                            current && {
                              ...current,
                              evidence: {
                                ...current.evidence,
                                stages: current.evidence.stages.map((group, at) =>
                                  at === index ? proof : group,
                                ),
                              },
                            },
                        )
                      }
                    />
                  </fieldset>
                )
              })}
              <button
                type="button"
                disabled={draft.record.stages.length >= 100}
                onClick={() =>
                  setDraft((current) =>
                    current?.record.kind === 'promise'
                      ? {
                          ...current,
                          record: { ...current.record, stages: [...current.record.stages, defaultStage] },
                          evidence: { ...current.evidence, stages: [...current.evidence.stages, []] },
                        }
                      : current,
                  )
                }
              >
                添加伏笔阶段
              </button>
            </fieldset>
          )}
          {draft.baseVersionId && (
            <label>
              <input
                type="checkbox"
                checked={draft.archived}
                onChange={(event) => setDraft({ ...draft, archived: event.target.checked })}
              />
              归档故事记录
            </label>
          )}
          <button
            type="button"
            disabled={!!stale || !draft.record.title.trim()}
            onClick={() => void confirm()}
          >
            确认故事记录并保存到浏览器
          </button>
          <button
            type="button"
            onClick={() => {
              if (window.confirm('放弃本次未确认的故事编辑？')) {
                setDraft(undefined)
                setError('')
              }
            }}
          >
            放弃本次故事编辑
          </button>
        </fieldset>
      )}
    </details>
  )
}
