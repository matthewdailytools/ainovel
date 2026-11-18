import { useEffect, useImperativeHandle, useRef, useState, type Ref } from 'react'
import { settingAdoptionLabels, type ConceptionItem } from './conception'
import { ConceptionItemEditor } from './ConceptionItems'
import { useLeaveGuard } from './leaveGuard'
import {
  blockingSettingsFindings,
  copyConflictSetting,
  createSettingsDraft,
  inspectSettingsDraft,
  prepareSettingsVersion,
  type SettingsConflictMerge,
  type SettingsFinding,
  type SettingsItem,
  type SettingsVersion,
  type SettingsVersionRecord,
} from './settingsSyncModel'

export type OfflineSettingsData = {
  current?: SettingsVersionRecord
  versions: SettingsVersionRecord[]
  projectVersionId: string | null
}

export type OfflineSettingsHandle = {
  beginConflictMerge: (context: SettingsConflictMerge) => void
}

const findingLabels: Record<SettingsFinding['kind'], string> = {
  same_name: '同名条目的内容或约束不同',
  missing_link: '关联目标不存在',
  self_link: '项目关联自身',
  duplicate_link: '同一目标重复关联',
  skipped_link: '关联目标所在类别已暂缓',
  empty_link: '关联目标尚无设定内容',
  policy_link: '关联规则的适用范围或例外文字不同',
}

type Review = {
  result: Awaited<ReturnType<typeof inspectSettingsDraft>>
  findings: string[]
  projectItems: string[]
}

function selectId(ids: string[], id: string, checked: boolean) {
  return checked ? [...ids, id] : ids.filter((value) => value !== id)
}

function SettingsRestorePreview({
  current,
  historical,
  onPick,
}: {
  current?: SettingsVersion
  historical: SettingsVersion
  onPick?: (itemId: string, categoryId: string) => void
}) {
  function entries(version?: SettingsVersion) {
    return new Map(
      (version?.categories ?? []).flatMap(({ id, title, items, skipped }) =>
        items.map(
          (item) =>
            [item.id, { category: id, title, skipped, item, origin: version?.origins[item.id] }] as const,
        ),
      ),
    )
  }
  const active = entries(current)
  const selected = entries(historical)
  const verbs = onPick
    ? { change: '修改', add: '新增', remove: '移除' }
    : { change: '还原', add: '带回', remove: '移出' }
  const changes = [...new Set([...active.keys(), ...selected.keys()])].flatMap((id) => {
    const before = active.get(id)
    const after = selected.get(id)
    return JSON.stringify(before) === JSON.stringify(after) ? [] : [{ id, before, after }]
  })
  return (
    <details style={{ overflowWrap: 'anywhere' }}>
      <summary>
        {onPick ? '与本机底稿的差异' : '恢复差异'}（{changes.length} 项）
      </summary>
      {!!changes.length && (
        <ul>
          {changes.map(({ id, before, after }) => (
            <li key={id}>
              {before && after ? verbs.change : after ? verbs.add : verbs.remove} ·{' '}
              {after?.item.title ?? before?.item.title} · {id}
              {before && (
                <p>
                  当前 · {before.title} · {before.item.detail || '未填写'}
                  {before.item.adoption_state && ` · ${settingAdoptionLabels[before.item.adoption_state]}`}
                </p>
              )}
              {after && (
                <>
                  <p>
                    所选 · {after.title} · {after.item.detail || '未填写'}
                    {after.item.adoption_state && ` · ${settingAdoptionLabels[after.item.adoption_state]}`}
                    {!!after.item.links.length && ` · 关联 ${after.item.links.join('、')}`}
                  </p>
                  {onPick && after.item.setting_policy && (
                    <p>
                      {after.item.setting_policy.strength === 'hard' ? '硬规定' : '可调整设定'} · 适用范围{' '}
                      {after.item.setting_policy.scope || '未填写'} · 例外{' '}
                      {after.item.setting_policy.exceptions || '未填写'}
                    </p>
                  )}
                  {onPick && (
                    <button type="button" onClick={() => onPick(id, after.category)}>
                      取用条目 {id}
                    </button>
                  )}
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      {!onPick && <p className="small">恢复稿需按现行立项体检。确认后，采用旧设定修订的大纲需复核。</p>}
    </details>
  )
}

function SettingsVersionPreview({ record }: { record: SettingsVersionRecord }) {
  const version = record.version
  return (
    <section aria-label="选中设定修订" style={{ overflowWrap: 'anywhere' }}>
      <p>
        修订 {version.id}
        {version.parent_id && ` · 上一修订 ${version.parent_id}`}
      </p>
      <p>立项依据 {version.project_version_id ?? '未采用立项'}</p>
      {record.source && <p>同步来源设备 {record.source.device_id}</p>}
      {record.fromPackage && (
        <p>来自稿件交换包{record.recordedDevice && ` · 原来源设备 ${record.recordedDevice}`}</p>
      )}
      {version.note && <p>作者确认说明 {version.note}</p>}
      {version.categories
        .filter((category) => category.items.length)
        .map((category) => (
          <details key={category.id}>
            <summary>
              {category.title}
              {category.skipped && ' · 暂缓'}
            </summary>
            <ul>
              {category.items.map((item) => {
                const origin = version.origins[item.id]
                const evidence = origin.evidence
                return (
                  <li key={item.id}>
                    <p>
                      {item.title} · 条目编号 {item.id} ·{' '}
                      {settingAdoptionLabels[item.adoption_state ?? 'adopted']}
                    </p>
                    <p>{item.detail}</p>
                    {item.setting_policy && (
                      <p className="small">
                        {item.setting_policy.strength === 'hard' ? '硬规定' : '可调整设定'} · 适用范围{' '}
                        {item.setting_policy.scope || '未填写'} · 例外{' '}
                        {item.setting_policy.exceptions || '未填写'}
                      </p>
                    )}
                    {!!item.links.length && <p className="small">关联 {item.links.join('、')}</p>}
                    <p className="small">
                      {origin.origin === 'author' ? '作者设定' : 'AI 草案'}
                      {origin.edited && ' · 经作者编辑'}
                      {origin.project_version_id && ` · 立项依据 ${origin.project_version_id}`}
                      {origin.artifact_id && ` · 草案编号 ${origin.artifact_id}`}
                      {origin.device_id && ` · 来源设备 ${origin.device_id}`}
                    </p>
                    {evidence && (
                      <p className="small">
                        原文依据 {evidence.chapter_id} · 修订 {evidence.revision_id} · 选区 {evidence.start}–
                        {evidence.end} · SHA-256 {evidence.quote_sha256}
                      </p>
                    )}
                  </li>
                )
              })}
            </ul>
          </details>
        ))}
    </section>
  )
}

export function OfflineSettings({
  read,
  onConfirm,
  refreshToken,
  disabled,
  onDirty,
  ref,
}: {
  read: () => Promise<OfflineSettingsData>
  onConfirm: (base: string | null, version: SettingsVersion) => Promise<void>
  refreshToken: number
  disabled: boolean
  onDirty: (dirty: boolean) => void
  ref?: Ref<OfflineSettingsHandle>
}) {
  const [data, setData] = useState<OfflineSettingsData>()
  const [draft, setDraft] = useState<SettingsVersion>()
  const [restoring, setRestoring] = useState<{ id: string; projectId: string | null }>()
  const [merging, setMerging] = useState<{ context: SettingsConflictMerge; base: SettingsVersion }>()
  const [review, setReview] = useState<Review>()
  const [categoryId, setCategoryId] = useState('world')
  const [selectedRevision, setSelectedRevision] = useState('')
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const recoveryFocus = useRef<HTMLSelectElement>(null)
  const panel = useRef<HTMLDetailsElement>(null)
  const request = useRef(0)
  const current = data?.current?.version
  const projectChanged = current && current.project_version_id !== data?.projectVersionId
  const stale =
    !!draft &&
    (draft.parent_id !== (current?.id ?? null) || draft.project_version_id !== data?.projectVersionId)
  const dirty = !!draft
  const categories = draft?.categories ?? []
  const category = categories.find((row) => row.id === categoryId)
  const choices = categories.flatMap((row) => row.items)
  const history = data?.versions ?? []
  const record = history.find((entry) => entry.version.id === selectedRevision) ?? data?.current
  const parent = history.find((entry) => entry.version.id === record?.version.parent_id)
  const restoreSource = record && record.version.id !== current?.id ? record : undefined
  const restoreNote = restoring ? `从设定修订 ${restoring.id} 恢复。` : ''
  const sourceNote = merging
    ? `合写设定冲突 ${merging.context.conflictId}。本机底稿 ${merging.base.id}，云端当前版 ${merging.context.current.version.id}，另一提交版 ${merging.context.incoming.version.id}。`
    : restoreNote
  useLeaveGuard(dirty, '离线设定')
  useEffect(() => {
    onDirty(dirty)
    return () => onDirty(false)
  }, [dirty, onDirty])
  useEffect(() => {
    if (!disabled && (restoring || merging)) recoveryFocus.current?.focus()
  }, [restoring, merging, disabled])

  useImperativeHandle(ref, () => ({
    beginConflictMerge(context) {
      if (!data?.current || !current || draft || busy) throw new Error('请先确认或放弃当前设定草稿。')
      if (current.id !== context.localRevisionId || current.project_version_id !== data.projectVersionId)
        throw new Error('本机设定或立项已变化，请重新读取并比较。')
      startDraft(data.current)
      setMerging({ context, base: current })
      if (panel.current) panel.current.open = true
    },
  }))

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

  function edit(next: SettingsVersion | undefined) {
    setDraft(next)
    if (!next) setRestoring(undefined)
    if (!next) setMerging(undefined)
    setReview(undefined)
    setError('')
    setStatus('')
  }

  function editItem(next: ConceptionItem) {
    if (!draft || !category) return
    const item: SettingsItem = {
      id: next.id,
      title: next.title,
      detail: next.detail ?? '',
      links: next.links ?? [],
      ...(next.adoption_state ? { adoption_state: next.adoption_state } : {}),
      ...(next.setting_policy
        ? {
            setting_policy: {
              strength: next.setting_policy.strength,
              scope: next.setting_policy.scope ?? '',
              exceptions: next.setting_policy.exceptions ?? '',
            },
          }
        : {}),
    }
    edit({
      ...draft,
      categories: categories.map((row) =>
        row.id === category.id
          ? { ...row, items: row.items.map((prior) => (prior.id === item.id ? item : prior)) }
          : row,
      ),
      origins: { ...draft.origins, [item.id]: { ...draft.origins[item.id], edited: true } },
    })
  }

  function addItem() {
    if (!draft || !category) return
    const id = crypto.randomUUID()
    edit({
      ...draft,
      categories: categories.map((row) =>
        row.id === category.id
          ? { ...row, items: [...row.items, { id, title: '', detail: '', links: [] }] }
          : row,
      ),
      origins: {
        ...draft.origins,
        [id]: {
          origin: 'author',
          edited: false,
          artifact_id: null,
          device_id: null,
          project_version_id: draft.project_version_id,
          evidence: null,
        },
      },
    })
  }

  function removeItem(id: string) {
    if (!draft) return
    const origins = { ...draft.origins }
    delete origins[id]
    edit({
      ...draft,
      origins,
      categories: categories.map((row) => ({ ...row, items: row.items.filter((item) => item.id !== id) })),
    })
  }

  function startDraft(source?: SettingsVersionRecord, restore = false) {
    if (!data) return
    edit(createSettingsDraft(crypto.randomUUID(), current?.id ?? null, data.projectVersionId, source))
    const version = source?.version
    setRestoring(restore && version ? { id: version.id, projectId: version.project_version_id } : undefined)
  }

  async function run(operation: () => Promise<void>) {
    setBusy(true)
    setError('')
    try {
      await operation()
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '设定操作未完成，当前输入已保留。')
    } finally {
      setBusy(false)
    }
  }

  async function confirm() {
    if (!draft || !review || stale || !canConfirm) return
    const note = [sourceNote, draft.note.trim()].filter(Boolean).join('\n')
    const version = await prepareSettingsVersion({
      ...draft,
      note,
      reviewed_setting_findings: review.findings,
      reconciled_project_items: review.projectItems,
    })
    try {
      await onConfirm(draft.parent_id, version)
    } catch (failure) {
      await read().then(setData, () => undefined)
      throw failure
    }
    edit(undefined)
    setStatus(
      merging
        ? '已保存合写设定。同步后重新打开冲突比较，可采用本机版。'
        : restoring
          ? '已确认历史内容为新的设定修订。'
          : '已确认并保存新的设定修订。',
    )
    setData(await read())
  }

  const canConfirm =
    !!review &&
    !review.result.findings.some(({ kind }) => blockingSettingsFindings.has(kind)) &&
    review.findings.length === review.result.findings.length &&
    review.projectItems.length === review.result.projectImpacts.length

  return (
    <details aria-label="离线设定台" ref={panel}>
      <summary>设定台</summary>
      {error && <p role="alert">{error}</p>}
      {status && <p role="status">{status}</p>}
      {!data && !error && <p role="status">正在读取本机设定…</p>}
      {data && !current && !draft && (
        <>
          <p>本机还没有已确认设定。</p>
          <button type="button" disabled={disabled || busy} onClick={() => startDraft()}>
            开始建立设定
          </button>
        </>
      )}
      {current && data?.current && (
        <>
          <p style={{ overflowWrap: 'anywhere' }}>
            {projectChanged ? '最近确认修订' : '当前确认修订'} {current.id}
          </p>
          {projectChanged && <p className="small">采用的立项已变化。编辑并核对后可确认新的设定修订。</p>}
          {!draft && (
            <button type="button" disabled={disabled || busy} onClick={() => startDraft(data.current)}>
              编辑当前设定
            </button>
          )}
        </>
      )}
      {draft && (
        <fieldset disabled={disabled || busy}>
          <legend>本地设定编辑</legend>
          {restoring && (
            <p className="small" style={{ overflowWrap: 'anywhere' }}>
              {restoreNote}
              {restoring.projectId !== draft.project_version_id &&
                ` 原立项 ${restoring.projectId ?? '未采用'} · 现行立项 ${draft.project_version_id ?? '未采用'}。`}
            </p>
          )}
          {merging && (
            <section aria-label="设定冲突合写依据">
              <p className="small">以本机版为底稿。取用对照条目或手动编辑后，按现行立项重新体检并确认。</p>
              {(
                [
                  ['云端当前版设定', merging.context.current],
                  ['另一提交版设定', merging.context.incoming],
                ] as const
              ).map(([label, source]) => (
                <details key={label}>
                  <summary>{label}</summary>
                  <SettingsRestorePreview
                    current={merging.base}
                    historical={source.version}
                    onPick={(itemId, nextCategory) => {
                      try {
                        edit(copyConflictSetting(draft, source, itemId))
                        setCategoryId(nextCategory)
                      } catch (failure) {
                        setError(failure instanceof Error ? failure.message : '暂时无法取用所选条目。')
                      }
                    }}
                  />
                </details>
              ))}
            </section>
          )}
          {stale && <p role="alert">当前设定或立项已更新。请先核对当前版本，未确认输入仍保留。</p>}
          <label>
            设定类别
            <select
              ref={recoveryFocus}
              value={categoryId}
              onChange={(event) => setCategoryId(event.target.value)}
            >
              {categories.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.title}
                </option>
              ))}
            </select>
          </label>
          {category && (
            <button
              type="button"
              disabled={category.items.length >= 40 || choices.length >= 240}
              onClick={addItem}
            >
              添加设定条目
            </button>
          )}
          {category?.items.map((item) => (
            <section className="conception-item" key={item.id} aria-label={item.title || '未命名设定'}>
              <p className="small" style={{ overflowWrap: 'anywhere' }}>
                条目编号 {item.id}
              </p>
              <ConceptionItemEditor
                item={item}
                choices={choices}
                worldSetting={category.id === 'world' || !!item.setting_policy}
                onChange={editItem}
              />
              <button
                type="button"
                disabled={choices.some((other) => other.links.includes(item.id))}
                onClick={() => {
                  if (!item.title && !item.detail) removeItem(item.id)
                  else if (window.confirm(`移除「${item.title || '未命名设定'}」？旧版本仍可查看。`))
                    removeItem(item.id)
                }}
              >
                移除条目
              </button>
              {choices.some((other) => other.links.includes(item.id)) && (
                <p className="small">请先解除其他条目对本条的关联。</p>
              )}
            </section>
          ))}
          <label>
            确认说明
            <textarea
              value={draft.note}
              maxLength={4000 - (sourceNote ? sourceNote.length + 1 : 0)}
              onChange={(event) => setDraft({ ...draft, note: event.target.value })}
            />
          </label>
          <button
            type="button"
            disabled={stale}
            onClick={() =>
              void run(async () =>
                setReview({
                  result: await inspectSettingsDraft(
                    draft.categories,
                    draft.origins,
                    draft.project_version_id,
                  ),
                  findings: [],
                  projectItems: [],
                }),
              )
            }
          >
            体检本地设定草稿
          </button>
          {review && (
            <>
              {!review.result.findings.length && !review.result.projectImpacts.length && (
                <p>当前无待核对项。</p>
              )}
              {review.result.projectImpacts.map((id) => (
                <label key={id}>
                  <input
                    type="checkbox"
                    checked={review.projectItems.includes(id)}
                    onChange={(event) =>
                      setReview({
                        ...review,
                        projectItems: selectId(review.projectItems, id, event.target.checked),
                      })
                    }
                  />
                  已按现行立项核对「{choices.find((item) => item.id === id)?.title}」 · {id}
                  {' · 原立项 '} {review.result.origins[id].project_version_id}
                </label>
              ))}
              {review.result.findings.map(({ id, kind, locations }) => (
                <details key={id} style={{ overflowWrap: 'anywhere' }}>
                  <summary>{findingLabels[kind]}</summary>
                  <ul>
                    {locations.map((location, index) => {
                      const item = choices.find((entry) => entry.id === location.item_id)
                      return (
                        <li key={index}>
                          {location.category_title
                            ? `${location.category_title} · 第 ${location.item_number} 项`
                            : '位置未找到'}
                          {location.link_number ? ` · 第 ${location.link_number} 个关联` : ''}
                          {` · ${location.item_id}`}
                          {item && (
                            <p>
                              {item.title} · {item.detail || '设定尚空'}
                            </p>
                          )}
                          {item?.setting_policy && (
                            <p>
                              适用范围 {item.setting_policy.scope || '未填写'} · 例外{' '}
                              {item.setting_policy.exceptions || '未填写'}
                            </p>
                          )}
                        </li>
                      )
                    })}
                  </ul>
                  {blockingSettingsFindings.has(kind) ? (
                    <p>请修复关联后重新体检。</p>
                  ) : (
                    <label>
                      <input
                        type="checkbox"
                        checked={review.findings.includes(id)}
                        onChange={(event) =>
                          setReview({
                            ...review,
                            findings: selectId(review.findings, id, event.target.checked),
                          })
                        }
                      />
                      已核对此项，按当前内容保留
                    </label>
                  )}
                </details>
              ))}
              <button type="button" disabled={stale || !canConfirm} onClick={() => void run(confirm)}>
                确认设定并保存到浏览器
              </button>
            </>
          )}
          <button
            type="button"
            onClick={() => {
              if (window.confirm('放弃本次未确认的设定修改？')) edit(undefined)
            }}
          >
            放弃本次修改
          </button>
        </fieldset>
      )}
      {!!history.length && (
        <details>
          <summary>设定版本历史</summary>
          <label>
            浏览正式设定修订
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
              查看上一修订
            </button>
          )}
          {record && <SettingsVersionPreview key={record.version.id} record={record} />}
          {restoreSource && (
            <>
              <SettingsRestorePreview current={current} historical={restoreSource.version} />
              <button
                type="button"
                disabled={disabled || busy || dirty}
                onClick={() => startDraft(restoreSource, true)}
              >
                恢复所选版本为设定草稿
              </button>
              {dirty && <p className="small">确认或放弃当前草稿后可选择其他恢复来源。</p>}
            </>
          )}
        </details>
      )}
    </details>
  )
}
