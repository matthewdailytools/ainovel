import { useEffect, useRef, useState } from 'react'
import { useLeaveGuard } from './leaveGuard'
import {
  describeOutlineVersion,
  eligibleOutlineSettings,
  outlineCardKey,
  outlineChapterFields,
  outlineChapterPlans,
  outlinePromiseSeed,
  outlineWindowFields,
  prepareOutlineVersion,
  validateOutlineVersionSources,
  type OutlineArchivePending,
  type OutlineCard,
  type OutlineChapterPlan,
  type OutlineDirectoryChapter,
  type OutlineExistingPromise,
  type OutlinePromiseSeed,
  type OutlineVersion,
  type OutlineVersionRecord,
  type OutlineWindow,
} from './outlineSyncModel'
import type { SettingsVersion } from './settingsSyncModel'

export type OfflineOutlineData = {
  current?: OutlineVersionRecord
  versions: OutlineVersionRecord[]
  projectVersionId: string | null
  settings?: SettingsVersion | null
  chapters: OutlineDirectoryChapter[]
  promises?: OutlineExistingPromise[]
  cards?: OutlineCard[]
  restorePending?: OutlineArchivePending
}

type Choice = { id: string; title: string }

function Choices({
  label,
  value,
  rows,
  onChange,
}: {
  label: string
  value: string[]
  rows: Choice[]
  onChange: (ids: string[]) => void
}) {
  if (!rows.length && !value.length) return null
  const options = [
    ...rows,
    ...value
      .filter((id) => !rows.some((row) => row.id === id))
      .map((id) => ({ id, title: `待复核 · ${id}` })),
  ]
  return (
    <details>
      <summary>
        {label} · {value.length} 项
      </summary>
      {options.map(({ id, title }) => (
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

function DirectoryChoice({
  label,
  value,
  rows,
  onChange,
}: {
  label: string
  value: string
  rows: Choice[]
  onChange: (id: string) => void
}) {
  const missing = value && !rows.some(({ id }) => id === value)
  return (
    <label>
      {label}
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="" disabled>
          请选择章节
        </option>
        {missing && <option value={value}>待复核 · {value}</option>}
        {rows.map(({ id, title }) => (
          <option key={id} value={id}>
            {title}
          </option>
        ))}
      </select>
    </label>
  )
}

const newChapterPlan = (id: string): OutlineChapterPlan => ({
  chapter_id: id,
  goal: '',
  obstacle: '',
  turn: '',
  result: '',
  setting_ids: [],
})

export function OfflineOutline({
  read,
  onConfirm,
  refreshToken,
  disabled,
  onDirty,
}: {
  read: () => Promise<OfflineOutlineData>
  onConfirm: (base: string | null, version: OutlineVersion) => Promise<void>
  refreshToken: number
  disabled: boolean
  onDirty: (dirty: boolean) => void
}) {
  const [data, setData] = useState<OfflineOutlineData>()
  const [draft, setDraft] = useState<OutlineVersion>()
  const [promiseTitle, setPromiseTitle] = useState('')
  const [promiseNotes, setPromiseNotes] = useState('')
  const [nextPromise, setNextPromise] = useState('')
  const [selectedRevision, setSelectedRevision] = useState('')
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const request = useRef(0)
  const current = data?.current?.version
  const activeSettings = data?.settings?.project_version_id === data?.projectVersionId ? data?.settings : null
  const stale =
    !!draft &&
    (draft.parent_id !== (current?.id ?? null) ||
      draft.project_version_id !== data?.projectVersionId ||
      (draft.settings_version_id !== null && draft.settings_version_id !== activeSettings?.id))
  const dirty = !!draft
  const chapters =
    data?.chapters.filter((chapter) => !chapter.archived).sort((a, b) => a.position - b.position) ?? []
  const planned = draft ? outlineChapterPlans(draft.plan).map(({ chapter_id }) => chapter_id) : []
  const windowChapters = chapters.filter(({ id }) => planned.includes(id))
  const history = data?.versions ?? []
  const record =
    history.find((row) => row.version.id === selectedRevision) ??
    data?.current ??
    history.find((row) => row.version.id === data?.restorePending?.revisionId)
  const parent = history.find((row) => row.version.id === record?.version.parent_id)
  const settings = eligibleOutlineSettings(activeSettings)
  const cardIndex = new Map(
    [...(data?.cards ?? []), ...(draft?.reference_cards ?? [])].map((card) => [outlineCardKey(card), card]),
  )
  const cardOptions = Array.from(cardIndex, ([id, card]) => ({
    id,
    title: `${card.technique} · 修订 ${card.revision}`,
  }))
  const promises = data?.promises?.filter((promise) => !promise.archived) ?? []
  const pendingPromises = promises.filter(
    ({ id }) => !draft?.plan.promises.some((window) => window.promise_id === id),
  )
  useLeaveGuard(dirty, '离线大纲')
  useEffect(() => {
    onDirty(dirty)
    return () => onDirty(false)
  }, [dirty, onDirty])

  useEffect(() => {
    const attempt = ++request.current
    void read().then(
      (next) => {
        if (attempt === request.current) setData(next)
      },
      (failure: Error) => {
        if (attempt === request.current) setError(failure.message)
      },
    )
    return () => {
      request.current++
    }
  }, [read, refreshToken])

  function start() {
    if (!data || !chapters.length) return
    setDraft({
      id: crypto.randomUUID(),
      parent_id: current?.id ?? null,
      project_version_id: data.projectVersionId,
      settings_version_id: activeSettings?.id ?? null,
      plan: current?.plan ?? { summary: '', volumes: [], promises: [] },
      reference_cards: current?.reference_cards ?? [],
      promise_sources: current?.promise_sources ?? {},
    })
    setError('')
    setStatus('')
  }

  function editPlan(transform: (plan: OutlineVersion['plan']) => OutlineVersion['plan']) {
    setDraft((before) => before && { ...before, plan: transform(before.plan) })
    setError('')
  }

  function openWindow(id: string, seed?: OutlinePromiseSeed) {
    if (!draft || !windowChapters.length || draft.plan.promises.some((window) => window.promise_id === id))
      return
    const first = windowChapters[0].id
    setDraft({
      ...draft,
      plan: {
        ...draft.plan,
        promises: [
          ...draft.plan.promises,
          {
            promise_id: id,
            plant_chapter_id: first,
            opens_chapter_id: first,
            closes_chapter_id: first,
          },
        ],
      },
      promise_sources: seed ? { ...draft.promise_sources, [id]: seed } : draft.promise_sources,
    })
    setNextPromise('')
    setPromiseTitle('')
    setPromiseNotes('')
    setError('')
  }

  async function confirm() {
    if (!draft || !data || stale) return
    setBusy(true)
    setError('')
    try {
      const references = new Set(
        outlineChapterPlans(draft.plan).flatMap((chapter) =>
          (chapter.reference_cards ?? []).map(outlineCardKey),
        ),
      )
      const windows = new Set(draft.plan.promises.map(({ promise_id }) => promise_id))
      const version = prepareOutlineVersion({
        ...draft,
        reference_cards: [...cardIndex.values()].filter((card) => references.has(outlineCardKey(card))),
        promise_sources: Object.fromEntries(
          Object.entries(draft.promise_sources).filter(([id]) => windows.has(id)),
        ),
      })
      await validateOutlineVersionSources(version, {
        project: (id) => id === data.projectVersionId,
        outline: (id) => id === current?.id,
        settings: (id) => (id === activeSettings?.id ? activeSettings : null),
        chapter: (id) => chapters.find((chapter) => chapter.id === id),
        ...(data.promises
          ? { promise: (id: string) => data.promises?.find((entry) => entry.id === id) }
          : {}),
      })
      await onConfirm(draft.parent_id, version)
      setDraft(undefined)
      setStatus('已确认并保存大纲修订。')
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '大纲未保存，当前输入已保留。')
    } finally {
      await read().then(setData, () => undefined)
      setBusy(false)
    }
  }

  return (
    <details aria-label="离线大纲台">
      <summary>大纲台</summary>
      {error && <p role="alert">{error}</p>}
      {status && <p role="status">{status}</p>}
      {!data && !error && <p role="status">正在读取本机大纲…</p>}
      {data?.restorePending && !current && (
        <p className="small">
          已保留稿件包的大纲修订 {data.restorePending.revisionId}。{data.restorePending.reason}
        </p>
      )}
      {data && !chapters.length && <p>先在同一工作版本建立章节目录。</p>}
      {current && (
        <p style={{ overflowWrap: 'anywhere' }}>
          已确认修订 {current.id}
          {(current.project_version_id !== data?.projectVersionId ||
            (current.settings_version_id && current.settings_version_id !== activeSettings?.id)) &&
            ' · 采用依据已有变化'}
        </p>
      )}
      {data && !draft && (
        <button type="button" disabled={disabled || busy || !chapters.length} onClick={start}>
          {current ? '编辑当前大纲' : '新建大纲'}
        </button>
      )}
      {draft && (
        <fieldset disabled={disabled || busy}>
          <legend>四级大纲与伏笔计划</legend>
          {stale && <p role="alert">大纲或上游依据已更新。请先核对当前版本，未确认输入仍保留。</p>}
          <p className="small" style={{ overflowWrap: 'anywhere' }}>
            立项 {draft.project_version_id ?? '未采用'} · 设定 {draft.settings_version_id ?? '未采用'}
          </p>
          <label>
            总纲
            <textarea
              maxLength={4000}
              value={draft.plan.summary}
              onChange={(event) => editPlan((plan) => ({ ...plan, summary: event.target.value }))}
            />
          </label>
          {draft.plan.volumes.map((volume) => {
            const updateVolume = (transform: (value: typeof volume) => typeof volume) =>
              editPlan((plan) => ({
                ...plan,
                volumes: plan.volumes.map((row) => (row.id === volume.id ? transform(row) : row)),
              }))
            return (
              <fieldset key={volume.id}>
                <legend>{volume.title || '新卷'}</legend>
                <label>
                  卷名称
                  <input
                    maxLength={200}
                    value={volume.title}
                    onChange={(event) => updateVolume((row) => ({ ...row, title: event.target.value }))}
                  />
                </label>
                <label>
                  卷纲
                  <textarea
                    maxLength={4000}
                    value={volume.summary}
                    onChange={(event) => updateVolume((row) => ({ ...row, summary: event.target.value }))}
                  />
                </label>
                {volume.units.map((unit) => {
                  const updateUnit = (transform: (value: typeof unit) => typeof unit) =>
                    updateVolume((row) => ({
                      ...row,
                      units: row.units.map((value) => (value.id === unit.id ? transform(value) : value)),
                    }))
                  return (
                    <fieldset key={unit.id}>
                      <legend>{unit.title || '新单元'}</legend>
                      <label>
                        单元名称
                        <input
                          maxLength={200}
                          value={unit.title}
                          onChange={(event) => updateUnit((row) => ({ ...row, title: event.target.value }))}
                        />
                      </label>
                      <label>
                        单元纲
                        <textarea
                          maxLength={4000}
                          value={unit.summary}
                          onChange={(event) => updateUnit((row) => ({ ...row, summary: event.target.value }))}
                        />
                      </label>
                      {unit.chapters.map((chapter) => {
                        const replace = (next: OutlineChapterPlan) =>
                          updateUnit((row) => ({
                            ...row,
                            chapters: row.chapters.map((entry) =>
                              entry.chapter_id === chapter.chapter_id ? next : entry,
                            ),
                          }))
                        return (
                          <fieldset key={chapter.chapter_id}>
                            <legend>章纲</legend>
                            <DirectoryChoice
                              label="对应章节"
                              value={chapter.chapter_id}
                              rows={chapters.filter(
                                ({ id }) => id === chapter.chapter_id || !planned.includes(id),
                              )}
                              onChange={(id) => replace({ ...chapter, chapter_id: id })}
                            />
                            {outlineChapterFields.map(([key, label]) => (
                              <label key={key}>
                                {label}
                                <textarea
                                  maxLength={4000}
                                  value={chapter[key]}
                                  onChange={(event) => replace({ ...chapter, [key]: event.target.value })}
                                />
                              </label>
                            ))}
                            <Choices
                              label="引用设定"
                              value={chapter.setting_ids}
                              rows={settings}
                              onChange={(setting_ids) => replace({ ...chapter, setting_ids })}
                            />
                            <Choices
                              label="手法卡摘要"
                              value={(chapter.reference_cards ?? []).map(outlineCardKey)}
                              rows={cardOptions}
                              onChange={(keys) =>
                                replace({
                                  ...chapter,
                                  reference_cards: keys.flatMap((key) => {
                                    const card = cardIndex.get(key)
                                    return card
                                      ? [
                                          {
                                            session_id: card.session_id,
                                            card_id: card.card_id,
                                            revision: card.revision,
                                          },
                                        ]
                                      : []
                                  }),
                                })
                              }
                            />
                            <button
                              type="button"
                              onClick={() =>
                                updateUnit((row) => ({
                                  ...row,
                                  chapters: row.chapters.filter(
                                    (entry) => entry.chapter_id !== chapter.chapter_id,
                                  ),
                                }))
                              }
                            >
                              移除此章纲
                            </button>
                          </fieldset>
                        )
                      })}
                      <button
                        type="button"
                        disabled={
                          unit.chapters.length >= 100 || !chapters.some(({ id }) => !planned.includes(id))
                        }
                        onClick={() => {
                          const next = chapters.find(({ id }) => !planned.includes(id))
                          if (next)
                            updateUnit((row) => ({
                              ...row,
                              chapters: [...row.chapters, newChapterPlan(next.id)],
                            }))
                        }}
                      >
                        添加章纲
                      </button>
                      <button
                        type="button"
                        onClick={() =>
                          updateVolume((row) => ({
                            ...row,
                            units: row.units.filter(({ id }) => id !== unit.id),
                          }))
                        }
                      >
                        移除此单元
                      </button>
                    </fieldset>
                  )
                })}
                <button
                  type="button"
                  disabled={volume.units.length >= 100}
                  onClick={() =>
                    updateVolume((row) => ({
                      ...row,
                      units: [
                        ...row.units,
                        { id: crypto.randomUUID(), title: '', summary: '', chapters: [] },
                      ],
                    }))
                  }
                >
                  添加单元
                </button>
                <button
                  type="button"
                  onClick={() =>
                    editPlan((plan) => ({
                      ...plan,
                      volumes: plan.volumes.filter(({ id }) => id !== volume.id),
                    }))
                  }
                >
                  移除此卷
                </button>
              </fieldset>
            )
          })}
          <button
            type="button"
            disabled={draft.plan.volumes.length >= 100}
            onClick={() =>
              editPlan((plan) => ({
                ...plan,
                volumes: [...plan.volumes, { id: crypto.randomUUID(), title: '', summary: '', units: [] }],
              }))
            }
          >
            添加卷
          </button>

          <fieldset>
            <legend>伏笔窗口</legend>
            <p className="small">此处安排计划。正文中的埋设、推进和回收由故事记录保存。</p>
            {draft.plan.promises.map((window) => {
              const title =
                draft.promise_sources[window.promise_id]?.title ??
                promises.find(({ id }) => id === window.promise_id)?.title ??
                window.promise_id
              const update = (next: OutlineWindow) =>
                editPlan((plan) => ({
                  ...plan,
                  promises: plan.promises.map((row) => (row.promise_id === window.promise_id ? next : row)),
                }))
              return (
                <fieldset key={window.promise_id}>
                  <legend>{title}</legend>
                  {outlineWindowFields.map(([key, label]) => (
                    <DirectoryChoice
                      key={key}
                      label={label}
                      value={window[key]}
                      rows={windowChapters}
                      onChange={(id) => update({ ...window, [key]: id })}
                    />
                  ))}
                  <button
                    type="button"
                    onClick={() =>
                      editPlan((plan) => ({
                        ...plan,
                        promises: plan.promises.filter(({ promise_id }) => promise_id !== window.promise_id),
                      }))
                    }
                  >
                    移除此计划窗口
                  </button>
                </fieldset>
              )
            })}
            {!!pendingPromises.length && (
              <>
                <label>
                  已有伏笔
                  <select value={nextPromise} onChange={(event) => setNextPromise(event.target.value)}>
                    <option value="">选择已有伏笔</option>
                    {pendingPromises.map(({ id, title }) => (
                      <option key={id} value={id}>
                        {title}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  disabled={!nextPromise || !windowChapters.length || draft.plan.promises.length >= 250}
                  onClick={() => {
                    const existing = promises.find(({ id }) => id === nextPromise)
                    if (existing) openWindow(existing.id, existing.seed)
                  }}
                >
                  安排已有伏笔
                </button>
              </>
            )}
            <label>
              新伏笔名称
              <input
                maxLength={200}
                value={promiseTitle}
                onChange={(event) => setPromiseTitle(event.target.value)}
              />
            </label>
            <label>
              计划说明
              <textarea
                maxLength={4000}
                value={promiseNotes}
                onChange={(event) => setPromiseNotes(event.target.value)}
              />
            </label>
            <button
              type="button"
              disabled={!promiseTitle.trim() || !windowChapters.length || draft.plan.promises.length >= 250}
              onClick={() =>
                openWindow(crypto.randomUUID(), outlinePromiseSeed(promiseTitle.trim(), promiseNotes.trim()))
              }
            >
              添加新伏笔计划
            </button>
          </fieldset>
          <button type="button" disabled={stale} onClick={() => void confirm()}>
            确认大纲并保存到浏览器
          </button>
          <button
            type="button"
            onClick={() => {
              if (window.confirm('放弃本次未确认的大纲修改？')) {
                setDraft(undefined)
                setError('')
              }
            }}
          >
            放弃本次大纲修改
          </button>
        </fieldset>
      )}
      {!!history.length && (
        <details>
          <summary>大纲版本历史</summary>
          <label>
            浏览正式大纲修订
            <select
              value={record?.version.id ?? ''}
              onChange={(event) => setSelectedRevision(event.target.value)}
            >
              {!record && <option value="">请选择修订</option>}
              {history.map(({ version }) => (
                <option key={version.id} value={version.id}>
                  {version.id}
                </option>
              ))}
            </select>
          </label>
          {parent && (
            <button type="button" onClick={() => setSelectedRevision(parent.version.id)}>
              查看上一大纲修订
            </button>
          )}
          {record?.source && <p>同步来源设备 {record.source.device_id}</p>}
          {!record?.source && record?.recordedDeviceId && (
            <p>稿件包记载的来源设备 {record.recordedDeviceId}</p>
          )}
          {record && (
            <pre style={{ overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}>
              {describeOutlineVersion(record.version)}
            </pre>
          )}
        </details>
      )}
    </details>
  )
}
