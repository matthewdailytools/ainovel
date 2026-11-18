import { useCallback, useEffect, useRef, useState } from 'react'
import type { Notify } from './App'
import { api, ApiError } from './api'
import type { components } from './api-schema'
import { useLeaveGuard } from './leaveGuard'
import { OutlineGenerationPanel } from './OutlineGenerationPanel'
import { OutlinePromiseProgress, type OutlinePromise } from './OutlinePromiseProgress'
import { OutlineImpactDetails, type OutlineConfirmationImpact } from './OutlineConfirmationImpact'

type OutlinePlan = components['schemas']['OutlinePlan']
type ChapterPlan = components['schemas']['ChapterPlan']
type NewPromisePlan = components['schemas']['NewPromisePlan']
type OutlineCard = components['schemas']['OutlineCard']
type OutlineCardReference = components['schemas']['OutlineCardReference']
export type FrozenCard = Omit<OutlineCard, 'source_title'> & { source_title?: string }
type SavedReferenceCard = components['schemas']['SavedReferenceCard']
type ReferenceCardEvidence = components['schemas']['ReferenceCardEvidence']
type Setting = { id: string; title: string; detail: string; category_id?: string; category_title?: string }
export type ConfirmedSettingBasis = { confirmation_id: string | null; revision?: number; items: Setting[] }
type OutlineRevision = {
  id: string
  revision: number
  confirmed: boolean
  plan: OutlinePlan
  setting_basis: ConfirmedSettingBasis & { reference_cards?: OutlineCard[] }
}
export type OutlineView = {
  draft: OutlineRevision | null
  confirmed: OutlineRevision | null
  requires_promise_confirmation?: boolean
  stale: string[]
  chapters: { id: string; title: string; position: number }[]
  promise_options: { id: string; title: string }[]
  settings: {
    chapter_id: string
    setting: Setting
    evidence: { report_id: string; source: { text: string } } | null
  }[]
  promises: OutlinePromise[]
}

function readWorkspace(root: string) {
  return Promise.all([
    api<OutlineView>(root),
    api<ConfirmedSettingBasis>(root + '/setting-options'),
    api<OutlineCard[]>(root + '/reference-options'),
  ])
}

const windowFields = [
  ['plant_chapter_id', '埋设章'],
  ['opens_chapter_id', '开始回收'],
  ['closes_chapter_id', '最迟回收'],
] as const
type WindowField = (typeof windowFields)[number][0]

export const outlineFields = {
  goal: '目标',
  obstacle: '阻碍',
  turn: '转折',
  result: '结果',
} as const

export function findChapterPlan(outline: OutlineRevision | null | undefined, chapterId: string | undefined) {
  return outline?.plan.volumes
    ?.flatMap((volume) =>
      (volume.units ?? []).flatMap((unit) =>
        (unit.chapters ?? []).map((chapter) => ({ volume, unit, chapter })),
      ),
    )
    .find(({ chapter }) => chapter.chapter_id === chapterId)
}

function cardKey(card: OutlineCardReference) {
  return `${card.session_id}/${card.card_id}/${card.revision}/${card.version_id ?? ''}`
}

export function OutlineCardSource({
  card,
  branchId,
  notify,
}: {
  card: FrozenCard
  branchId?: string
  notify: Notify
}) {
  const [source, setSource] = useState<SavedReferenceCard>()
  const [evidence, setEvidence] = useState<ReferenceCardEvidence>()
  const [loading, setLoading] = useState(false)
  async function readSource() {
    if (loading) return
    setLoading(true)
    try {
      if (card.version_id && branchId) {
        setEvidence(
          await api<ReferenceCardEvidence>(
            `/branches/${encodeURIComponent(branchId)}/reference-cards/${encodeURIComponent(card.version_id)}/evidence`,
          ),
        )
        return
      }
      const session = encodeURIComponent(card.session_id)
      const identifier = encodeURIComponent(card.card_id)
      setSource(
        await api<SavedReferenceCard>(
          `/reference-sessions/${session}/cards/${identifier}/revisions/${card.revision}`,
        ),
      )
    } catch (error) {
      notify(error)
    } finally {
      setLoading(false)
    }
  }
  return (
    <details>
      <summary>
        {card.technique} · 手法卡修订 {card.revision}
      </summary>
      <p>适用条件 · {card.conditions}</p>
      <p>须替换 · {card.exceptions}</p>
      {card.source_title && <p>来源 · {card.source_title}</p>}
      {card.version_id && (
        <p className="small" style={{ overflowWrap: 'anywhere' }}>
          来源修订编号 {card.version_id}
        </p>
      )}
      {evidence ? (
        evidence.excerpt ? (
          <blockquote>{evidence.excerpt}</blockquote>
        ) : (
          <p>该手法卡保留来源地址。此工作版本未包含原文。</p>
        )
      ) : source ? (
        <>
          <p>卡片所引 · {source.card.evidence}</p>
          {typeof source.source.quote === 'string' && <blockquote>{source.source.quote}</blockquote>}
        </>
      ) : (
        <button
          type="button"
          disabled={loading || (!!card.version_id && !branchId)}
          onClick={() => void readSource()}
        >
          {loading ? '正在读取手法卡原文…' : '查看手法卡原文依据'}
        </button>
      )}
    </details>
  )
}

export function chapterCards(outline: OutlineRevision | null | undefined, chapter: ChapterPlan) {
  const selected = new Set((chapter.reference_cards ?? []).map(cardKey))
  return (outline?.setting_basis.reference_cards ?? []).filter((card) => selected.has(cardKey(card)))
}

export function OutlineWorkspace({
  branchId,
  account,
  modelReady,
  notify,
  onWriting,
}: {
  branchId: string
  account: string
  modelReady: boolean
  notify: Notify
  onWriting: (chapterId: string, reportId?: string) => void
}) {
  const root = `/branches/${branchId}/outline-workspace`
  const [view, setView] = useState<OutlineView>()
  const [settings, setSettings] = useState<ConfirmedSettingBasis>()
  const [cards, setCards] = useState<OutlineCard[]>([])
  const [editing, setEditing] = useState<OutlinePlan>()
  const [settingId, setSettingId] = useState<string | null>(null)
  const [generationId, setGenerationId] = useState<string>()
  const [agreedPromiseRevision, setAgreedPromiseRevision] = useState<number>()
  const [cut, setCut] = useState<number>()
  const [previous, setPrevious] = useState<OutlineRevision>()
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [reload, setReload] = useState(0)
  const [impactReply, setImpactReply] = useState<{
    key: string
    value?: OutlineConfirmationImpact
    error?: string
  }>()
  const impactRetry = useRef<HTMLButtonElement>(null)
  const focusImpactRetry = useRef(false)
  const baseline = view?.draft?.plan ?? { summary: '', volumes: [], promises: [] }
  const dirty =
    !!editing &&
    (!!generationId ||
      JSON.stringify(baseline) !== JSON.stringify(editing) ||
      settingId !== (view?.draft?.setting_basis.confirmation_id ?? null))
  const draftForImpact = !dirty && view?.draft && !view.draft.confirmed ? view.draft : undefined
  const impactRevision = draftForImpact?.revision
  const impactKey = `${root}/${draftForImpact?.id ?? ''}/${reload}`
  const checkedImpact = impactReply?.key === impactKey ? impactReply : undefined
  useLeaveGuard(dirty, '大纲草稿')
  const loadWorkspace = useCallback(
    ([data, available, referenceCards]: Awaited<ReturnType<typeof readWorkspace>>) => {
      setView(data)
      setAgreedPromiseRevision(undefined)
      setSettings(available)
      setCards(referenceCards)
      setEditing(data.draft?.plan ?? { summary: '', volumes: [], promises: [] })
      setSettingId(data.draft?.setting_basis.confirmation_id ?? null)
      setGenerationId(undefined)
    },
    [],
  )
  useEffect(() => {
    let active = true
    void readWorkspace(root)
      .then((data) => {
        if (active) loadWorkspace(data)
      })
      .catch((error) => {
        if (active) notify(error)
      })
    return () => {
      active = false
    }
  }, [root, notify, loadWorkspace])
  useEffect(() => {
    if (impactRevision === undefined) return
    let active = true
    void api<OutlineConfirmationImpact>(`${root}/confirmation-impact?base_revision=${impactRevision}`)
      .then((value) => {
        if (active) setImpactReply({ key: impactKey, value })
      })
      .catch((error: unknown) => {
        if (active)
          setImpactReply({
            key: impactKey,
            error: error instanceof Error ? error.message : '暂无法读取大纲确认影响。',
          })
      })
    return () => {
      active = false
    }
  }, [root, impactKey, impactRevision])
  useEffect(() => {
    if (focusImpactRetry.current && checkedImpact?.error && !busy) {
      focusImpactRetry.current = false
      impactRetry.current?.focus()
    }
  }, [checkedImpact?.error, busy])
  if (!view || !editing || !settings) return <p role="status">正在读取大纲与台账…</p>
  const chapterName = (id: string) =>
    view.chapters.find((chapter) => chapter.id === id)?.title ?? '已移出目录'
  const chapters = [...view.chapters].sort((left, right) => left.position - right.position)
  const usedChapters = new Set(
    (editing.volumes ?? []).flatMap((volume) =>
      (volume.units ?? []).flatMap((unit) => (unit.chapters ?? []).map((chapter) => chapter.chapter_id)),
    ),
  )
  const frozen =
    settingId && settingId === settings.confirmation_id
      ? settings
      : view.draft?.setting_basis.confirmation_id === settingId
        ? view.draft.setting_basis
        : undefined
  const availableCards = new Map(
    [...(view.draft?.setting_basis.reference_cards ?? []), ...cards].map((card) => [cardKey(card), card]),
  )
  async function run(action: () => Promise<void>) {
    if (busy) return
    setBusy(true)
    setNotice('')
    try {
      await action()
    } catch (error) {
      notify(error)
    } finally {
      setBusy(false)
    }
  }
  function edit(change: (plan: OutlinePlan) => void) {
    setEditing((current) => {
      if (!current) return current
      const next = structuredClone(current)
      change(next)
      return next
    })
  }

  function windowChapters(
    window: Pick<NewPromisePlan, WindowField>,
    change: (field: WindowField, id: string) => void,
  ) {
    return windowFields.map(([field, name]) => (
      <label key={field}>
        {name}
        <select required value={window[field]} onChange={(event) => change(field, event.target.value)}>
          <option value="">选择章纲</option>
          {chapters
            .filter((chapter) => usedChapters.has(chapter.id))
            .map((chapter) => (
              <option key={chapter.id} value={chapter.id}>
                {chapter.title}
              </option>
            ))}
        </select>
      </label>
    ))
  }
  const fullWindows = (editing.promises?.length ?? 0) + (editing.new_promises?.length ?? 0) >= 250
  const agreesToPromises = agreedPromiseRevision === view.draft?.revision

  return (
    <div className="records" id="bench-outline" tabIndex={-1}>
      <section className="card">
        <h2>大纲与台账</h2>
        <p>{view.confirmed ? `已确认大纲修订 ${view.confirmed.revision}` : '尚无确认大纲'}</p>
        {!!view.stale.length && <p role="alert">{view.stale.join('，')}。请核对后重新确认。</p>}
        {notice && <p role="status">{notice}</p>}
        <OutlineGenerationPanel
          branchId={branchId}
          account={account}
          modelReady={modelReady}
          baseRevision={view.draft?.revision ?? 0}
          confirmationId={settingId ?? settings.confirmation_id}
          hasChapters={!!view.chapters.length}
          dirty={dirty || busy}
          notify={notify}
          onLoad={(plan, confirmationId, candidateId) => {
            setEditing(plan)
            setSettingId(confirmationId)
            setGenerationId(candidateId)
          }}
        />
        <form
          onSubmit={(event) => {
            event.preventDefault()
            void run(async () => {
              const updated = await api<OutlineView>(root, 'PUT', {
                base_revision: view.draft?.revision ?? 0,
                confirmation_id: settingId,
                source_generation_id: generationId,
                plan: editing,
              })
              setView(updated)
              setEditing(updated.draft?.plan)
              setGenerationId(undefined)
              setNotice(`已保存大纲草稿修订 ${updated.draft?.revision}。`)
            })
          }}
        >
          <fieldset disabled={busy}>
            <label>
              总纲
              <textarea
                required
                maxLength={4000}
                value={editing.summary}
                onChange={(event) => {
                  const value = event.target.value
                  edit((plan) => {
                    plan.summary = value
                  })
                }}
              />
            </label>
            <label>
              采用设定
              <select
                value={settingId ?? ''}
                onChange={(event) => {
                  const value = event.target.value || null
                  setSettingId(value)
                  setGenerationId(undefined)
                  const allowed = value ? new Set(settings.items.map((item) => item.id)) : new Set<string>()
                  edit((plan) => {
                    for (const volume of plan.volumes ?? [])
                      for (const unit of volume.units ?? [])
                        for (const chapter of unit.chapters ?? [])
                          chapter.setting_ids = (chapter.setting_ids ?? []).filter((id) => allowed.has(id))
                  })
                }}
              >
                <option value="">暂不挂载</option>
                {settingId && settingId !== settings.confirmation_id && (
                  <option value={settingId}>旧设定修订 {frozen?.revision}</option>
                )}
                {settings.confirmation_id && (
                  <option value={settings.confirmation_id}>已确认设定修订 {settings.revision}</option>
                )}
              </select>
            </label>
            {(editing.volumes ?? []).map((volume, volumeIndex) => (
              <fieldset key={volume.id}>
                <legend>第 {volumeIndex + 1} 卷</legend>
                <label>
                  卷名
                  <input
                    required
                    maxLength={200}
                    value={volume.title}
                    onChange={(event) => {
                      const value = event.target.value
                      edit((plan) => {
                        plan.volumes![volumeIndex].title = value
                      })
                    }}
                  />
                </label>
                <label>
                  卷纲
                  <textarea
                    required
                    maxLength={4000}
                    value={volume.summary}
                    onChange={(event) => {
                      const value = event.target.value
                      edit((plan) => {
                        plan.volumes![volumeIndex].summary = value
                      })
                    }}
                  />
                </label>
                {(volume.units ?? []).map((unit, unitIndex) => {
                  const update = (change: (entry: typeof unit) => void) =>
                    edit((plan) => change(plan.volumes![volumeIndex].units![unitIndex]))
                  return (
                    <fieldset key={unit.id}>
                      <legend>单元 {unitIndex + 1}</legend>
                      <label>
                        单元名
                        <input
                          required
                          maxLength={200}
                          value={unit.title}
                          onChange={(event) => {
                            const value = event.target.value
                            update((entry) => {
                              entry.title = value
                            })
                          }}
                        />
                      </label>
                      <label>
                        单元纲
                        <textarea
                          required
                          maxLength={4000}
                          value={unit.summary}
                          onChange={(event) => {
                            const value = event.target.value
                            update((entry) => {
                              entry.summary = value
                            })
                          }}
                        />
                      </label>
                      {(unit.chapters ?? []).map((chapter, chapterIndex) => {
                        const changeChapter = (change: (row: ChapterPlan) => void) =>
                          update((entry) => change(entry.chapters![chapterIndex]))
                        return (
                          <fieldset key={chapter.chapter_id}>
                            <legend>{chapterName(chapter.chapter_id)} · 章纲</legend>
                            {Object.entries(outlineFields).map(([name, label]) => {
                              const field = name as keyof typeof outlineFields
                              return (
                                <label key={field}>
                                  {label}
                                  <textarea
                                    required
                                    maxLength={4000}
                                    value={chapter[field]}
                                    onChange={(event) => {
                                      const value = event.target.value
                                      changeChapter((row) => {
                                        row[field] = value
                                      })
                                    }}
                                  />
                                </label>
                              )
                            })}
                            {!!frozen?.items.length && (
                              <fieldset>
                                <legend>计划采用的设定</legend>
                                {frozen.items.map((item) => (
                                  <label className="check" key={item.id}>
                                    <input
                                      type="checkbox"
                                      checked={chapter.setting_ids?.includes(item.id) ?? false}
                                      onChange={(event) => {
                                        const checked = event.target.checked
                                        changeChapter((row) => {
                                          row.setting_ids = checked
                                            ? [...(row.setting_ids ?? []), item.id]
                                            : row.setting_ids?.filter((id) => id !== item.id)
                                        })
                                      }}
                                    />
                                    {item.title}
                                    {item.category_title && ` · ${item.category_title}`}
                                  </label>
                                ))}
                              </fieldset>
                            )}
                            {(availableCards.size > 0 || !!chapter.reference_cards?.length) && (
                              <fieldset>
                                <legend>计划引用的手法卡</legend>
                                {(chapter.reference_cards ?? []).map((ref) => {
                                  const key = cardKey(ref)
                                  const card = availableCards.get(key)
                                  return (
                                    <div key={key}>
                                      {card ? (
                                        <OutlineCardSource card={card} branchId={branchId} notify={notify} />
                                      ) : (
                                        <p>手法卡 {ref.card_id}</p>
                                      )}
                                      <button
                                        type="button"
                                        onClick={() =>
                                          changeChapter((row) => {
                                            row.reference_cards = row.reference_cards?.filter(
                                              (item) => cardKey(item) !== key,
                                            )
                                          })
                                        }
                                      >
                                        移除这张手法卡
                                      </button>
                                    </div>
                                  )
                                })}
                                <label>
                                  加入手法卡
                                  <select
                                    value=""
                                    onChange={(event) => {
                                      const card = availableCards.get(event.target.value)
                                      if (!card) return
                                      const { session_id, card_id, revision, version_id } = card
                                      changeChapter((row) => {
                                        row.reference_cards = [
                                          ...(row.reference_cards ?? []).filter(
                                            (ref) => ref.session_id !== session_id || ref.card_id !== card_id,
                                          ),
                                          {
                                            session_id,
                                            card_id,
                                            revision,
                                            ...(version_id ? { version_id } : {}),
                                          },
                                        ]
                                      })
                                    }}
                                  >
                                    <option value="">选择已保存的手法卡</option>
                                    {Array.from(availableCards, ([key, card]) => (
                                      <option
                                        key={key}
                                        value={key}
                                        disabled={chapter.reference_cards?.some(
                                          (ref) => cardKey(ref) === key,
                                        )}
                                      >
                                        {card.technique} · {card.source_title} · 修订 {card.revision}
                                      </option>
                                    ))}
                                  </select>
                                </label>
                              </fieldset>
                            )}
                            <button
                              type="button"
                              onClick={() => update((entry) => entry.chapters?.splice(chapterIndex, 1))}
                            >
                              移除章纲
                            </button>
                          </fieldset>
                        )
                      })}
                      <label>
                        加入已有章节
                        <select
                          value=""
                          onChange={(event) => {
                            const id = event.target.value
                            if (!id) return
                            update((entry) => {
                              entry.chapters = [
                                ...(entry.chapters ?? []),
                                {
                                  chapter_id: id,
                                  goal: '',
                                  obstacle: '',
                                  turn: '',
                                  result: '',
                                  setting_ids: [],
                                },
                              ]
                            })
                          }}
                        >
                          <option value="">选择章节</option>
                          {chapters
                            .filter((chapter) => !usedChapters.has(chapter.id))
                            .map((chapter) => (
                              <option key={chapter.id} value={chapter.id}>
                                {chapter.position}. {chapter.title}
                              </option>
                            ))}
                        </select>
                      </label>
                      <button
                        type="button"
                        onClick={() => edit((plan) => plan.volumes![volumeIndex].units?.splice(unitIndex, 1))}
                      >
                        移除单元
                      </button>
                    </fieldset>
                  )
                })}
                <button
                  type="button"
                  onClick={() =>
                    edit((plan) => {
                      const row = plan.volumes![volumeIndex]
                      row.units = [
                        ...(row.units ?? []),
                        { id: crypto.randomUUID(), title: '', summary: '', chapters: [] },
                      ]
                    })
                  }
                >
                  新增单元
                </button>
                <button type="button" onClick={() => edit((plan) => plan.volumes?.splice(volumeIndex, 1))}>
                  移除卷
                </button>
              </fieldset>
            ))}
            <button
              type="button"
              onClick={() =>
                edit((plan) => {
                  plan.volumes = [
                    ...(plan.volumes ?? []),
                    { id: crypto.randomUUID(), title: '', summary: '', units: [] },
                  ]
                })
              }
            >
              新增卷
            </button>
            <h3>伏笔窗口</h3>
            {(editing.promises ?? []).map((window, index) => (
              <fieldset key={index}>
                <legend>窗口 {index + 1}</legend>
                <label>
                  伏笔
                  <select
                    required
                    value={window.promise_id}
                    onChange={(event) => {
                      const id = event.target.value
                      edit((plan) => {
                        plan.promises![index].promise_id = id
                      })
                    }}
                  >
                    <option value="">选择故事记录中的伏笔</option>
                    {view.promise_options.map((item) => (
                      <option value={item.id} key={item.id}>
                        {item.title}
                      </option>
                    ))}
                  </select>
                </label>
                {windowChapters(window, (field, id) =>
                  edit((plan) => {
                    plan.promises![index][field] = id
                  }),
                )}
                <label>
                  <input
                    type="checkbox"
                    checked={window.fulfillment === 'author_required'}
                    onChange={(event) => {
                      const required = event.target.checked
                      edit((plan) => {
                        const window = plan.promises![index]
                        if (required) window.fulfillment = 'author_required'
                        else delete window.fulfillment
                      })
                    }}
                  />
                  必须在回收窗口内兑现
                </label>
                <button type="button" onClick={() => edit((plan) => plan.promises?.splice(index, 1))}>
                  移除窗口
                </button>
              </fieldset>
            ))}
            <button
              type="button"
              disabled={!view.promise_options.length || !usedChapters.size || fullWindows}
              onClick={() =>
                edit((plan) => {
                  plan.promises = [
                    ...(plan.promises ?? []),
                    { promise_id: '', plant_chapter_id: '', opens_chapter_id: '', closes_chapter_id: '' },
                  ]
                })
              }
            >
              新增伏笔窗口
            </button>
            {(editing.new_promises ?? []).map((window, index) => (
              <fieldset key={index}>
                <legend>新伏笔计划 {index + 1}</legend>
                <label>
                  伏笔标题
                  <input
                    required
                    maxLength={200}
                    value={window.title}
                    onChange={(event) => {
                      const title = event.target.value
                      edit((plan) => {
                        plan.new_promises![index].title = title
                      })
                    }}
                  />
                </label>
                <label>
                  计划说明
                  <textarea
                    maxLength={4000}
                    value={window.notes ?? ''}
                    onChange={(event) => {
                      const notes = event.target.value
                      edit((plan) => {
                        plan.new_promises![index].notes = notes
                      })
                    }}
                  />
                </label>
                {windowChapters(window, (field, id) =>
                  edit((plan) => {
                    plan.new_promises![index][field] = id
                  }),
                )}
                <label>
                  <input
                    type="checkbox"
                    checked={window.fulfillment === 'author_required'}
                    onChange={(event) => {
                      const required = event.target.checked
                      edit((plan) => {
                        const window = plan.new_promises![index]
                        if (required) window.fulfillment = 'author_required'
                        else delete window.fulfillment
                      })
                    }}
                  />
                  必须在回收窗口内兑现
                </label>
                <button
                  type="button"
                  onClick={() =>
                    edit((plan) => {
                      plan.new_promises?.splice(index, 1)
                      if (!plan.new_promises?.length) delete plan.new_promises
                    })
                  }
                >
                  移除新伏笔
                </button>
              </fieldset>
            ))}
            <button
              type="button"
              disabled={!usedChapters.size || fullWindows}
              onClick={() =>
                edit((plan) => {
                  plan.new_promises = [
                    ...(plan.new_promises ?? []),
                    {
                      title: '',
                      notes: '',
                      plant_chapter_id: '',
                      opens_chapter_id: '',
                      closes_chapter_id: '',
                    },
                  ]
                })
              }
            >
              新建伏笔计划
            </button>
            {!usedChapters.size && <p className="small">添加章纲后可安排伏笔窗口。</p>}
            {!!editing.new_promises?.length && (
              <p className="small">确认大纲会把新伏笔加入台账，初始状态为「计划」。</p>
            )}
            {draftForImpact && (
              <section aria-label="大纲确认影响">
                <h3>大纲确认影响</h3>
                {!checkedImpact && <p role="status">正在读取确认影响…</p>}
                {checkedImpact?.error && (
                  <>
                    <p role="alert">{checkedImpact.error}</p>
                    <button
                      ref={impactRetry}
                      type="button"
                      onClick={() =>
                        void run(async () => {
                          loadWorkspace(await readWorkspace(root))
                          setReload((current) => current + 1)
                        })
                      }
                    >
                      重新读取大纲与影响
                    </button>
                  </>
                )}
                {checkedImpact?.value && <OutlineImpactDetails impact={checkedImpact.value} />}
              </section>
            )}
            {view.requires_promise_confirmation && !dirty && (
              <label>
                <input
                  type="checkbox"
                  checked={agreesToPromises}
                  onChange={(event) =>
                    setAgreedPromiseRevision(event.target.checked ? view.draft?.revision : undefined)
                  }
                />
                我已核对必达承诺的新增、调整或撤销
              </label>
            )}
            <p>
              <button type="submit" className="primary" disabled={!dirty}>
                保存大纲草稿
              </button>
              {dirty && (
                <button
                  type="button"
                  onClick={() => {
                    if (!window.confirm('放弃本次未保存的大纲编辑？')) return
                    setEditing(baseline)
                    setSettingId(view.draft?.setting_basis.confirmation_id ?? null)
                    setGenerationId(undefined)
                  }}
                >
                  重置未保存大纲
                </button>
              )}
              <button
                type="button"
                disabled={
                  dirty ||
                  !view.draft ||
                  view.draft.confirmed ||
                  !checkedImpact?.value ||
                  (view.requires_promise_confirmation && !agreesToPromises)
                }
                onClick={() => {
                  const impact = checkedImpact?.value
                  if (!impact || impact.draft_id !== view.draft?.id) return
                  void run(async () => {
                    let updated: OutlineView
                    try {
                      updated = await api<OutlineView>(root + '/confirm', 'POST', {
                        base_revision: impact.base_revision,
                        impact_sha256: impact.basis_sha256,
                        ...(view.requires_promise_confirmation
                          ? { acknowledge_required_promises: agreesToPromises }
                          : {}),
                      })
                    } catch (error) {
                      if (error instanceof ApiError && error.status === 409) {
                        focusImpactRetry.current = true
                        setAgreedPromiseRevision(undefined)
                        setImpactReply({ key: impactKey, error: error.message })
                        return
                      }
                      throw error
                    }
                    setView(updated)
                    setAgreedPromiseRevision(undefined)
                    setEditing(updated.draft?.plan)
                    setNotice(`已确认大纲修订 ${updated.confirmed?.revision}。`)
                  })
                }}
              >
                确认已保存大纲
              </button>
            </p>
          </fieldset>
        </form>
        {view.draft && view.draft.revision > 1 && (
          <details>
            <summary>查看历史大纲</summary>
            <label>
              修订编号
              <input
                type="number"
                min={1}
                max={view.draft.revision}
                value={cut ?? ''}
                onChange={(event) => setCut(event.target.valueAsNumber || undefined)}
              />
            </label>
            <button
              type="button"
              disabled={busy || !cut}
              onClick={() =>
                void run(async () => {
                  setPrevious(await api<OutlineRevision>(root + '/revisions/' + cut))
                })
              }
            >
              读取历史
            </button>
            {previous && (
              <>
                <h3>
                  修订 {previous.revision} · {previous.confirmed ? '已确认' : '草稿'}
                </h3>
                <p>{previous.plan.summary}</p>
                {previous.plan.promises
                  ?.filter((window) => window.fulfillment === 'author_required')
                  .map((window) => (
                    <p key={window.promise_id}>
                      必达承诺 {window.promise_id} · {chapterName(window.opens_chapter_id)} 至{' '}
                      {chapterName(window.closes_chapter_id)}
                    </p>
                  ))}
                {previous.plan.new_promises?.map((window) => (
                  <p key={window.title}>
                    {window.title} · {chapterName(window.plant_chapter_id)} 至{' '}
                    {chapterName(window.closes_chapter_id)}
                    {window.fulfillment === 'author_required' ? ' · 必达承诺' : ''}
                  </p>
                ))}
                {(previous.plan.volumes ?? []).map((volume) => (
                  <details key={volume.id}>
                    <summary>{volume.title}</summary>
                    <p>{volume.summary}</p>
                    {volume.units?.map((unit) => (
                      <div key={unit.id}>
                        <h4>{unit.title}</h4>
                        <p>{unit.summary}</p>
                        {unit.chapters?.map((chapter) => (
                          <div key={chapter.chapter_id}>
                            <p>
                              {chapterName(chapter.chapter_id)} · {chapter.goal} · {chapter.obstacle} ·{' '}
                              {chapter.turn} · {chapter.result}
                            </p>
                            {previous.setting_basis.items
                              .filter((item) => chapter.setting_ids?.includes(item.id))
                              .map((item) => (
                                <details key={item.id}>
                                  <summary>
                                    {item.title}
                                    {item.category_title && ` · ${item.category_title}`}
                                  </summary>
                                  <p>{item.detail}</p>
                                </details>
                              ))}
                            {chapterCards(previous, chapter).map((card) => (
                              <OutlineCardSource
                                key={cardKey(card)}
                                card={card}
                                branchId={branchId}
                                notify={notify}
                              />
                            ))}
                          </div>
                        ))}
                      </div>
                    ))}
                  </details>
                ))}
              </>
            )}
          </details>
        )}
      </section>
      {view.confirmed && (
        <section className="card">
          <h3>设定台账</h3>
          {!view.settings.length && <p>当前大纲尚未安排设定引用。</p>}
          <ul>
            {view.settings.map(({ chapter_id, setting, evidence }) => (
              <li key={`${chapter_id}/${setting.id}`}>
                <button type="button" onClick={() => onWriting(chapter_id, evidence?.report_id)}>
                  {chapterName(chapter_id)}
                </button>{' '}
                {setting.title}
                {setting.category_title && ` · ${setting.category_title}`} ·{' '}
                {evidence ? '已有正文对账证据' : '计划'}
                {evidence && <blockquote>{evidence.source.text}</blockquote>}
              </li>
            ))}
          </ul>
          <h3>伏笔台账</h3>
          {!view.promises.length && <p>当前大纲尚未安排伏笔窗口。</p>}
          <ul>
            {view.promises.map((entry) => (
              <li key={entry.promise_id}>
                <OutlinePromiseProgress promise={entry} chapterName={chapterName} onChapter={onWriting} />
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
