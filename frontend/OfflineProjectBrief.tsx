import { useEffect, useRef, useState } from 'react'
import {
  blankProjectBrief,
  briefFields,
  describeProjectVersion,
  projectGenres,
  validateProjectDraft,
  type ProjectDraft,
  type ProjectVersionRecord,
} from './projectSyncModel'
import { useLeaveGuard } from './leaveGuard'
import { confirmLocalProjectBrief, readLocalProjectBrief } from './offlineStore'
import { discardProjectDraft, readProjectDraft, writeProjectDraft } from './projectDrafts'

export function LocalProjectBrief({
  projectId,
  branchId,
  refreshToken,
  onConfirmed,
  ...controls
}: {
  projectId: string
  branchId: string
  refreshToken: number
  disabled: boolean
  onDirty: (value: boolean) => void
  onConfirmed?: () => void
}) {
  const [data, setData] = useState<Awaited<ReturnType<typeof readLocalProjectBrief>>>()
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    void readLocalProjectBrief(projectId).then(
      (next) => {
        if (active) {
          setData(next)
          setError('')
        }
      },
      (reason) => {
        if (active) setError(reason instanceof Error ? reason.message : '暂时无法读取本机立项。')
      },
    )
    return () => {
      active = false
    }
  }, [projectId, refreshToken])
  if (error) return <p role="alert">{error}</p>
  if (!data) return <p role="status">正在读取本机立项…</p>
  return (
    <OfflineProjectBrief
      {...controls}
      {...data}
      branchId={branchId}
      onConfirm={async ({ id, base, ...draft }) => {
        try {
          const version = await confirmLocalProjectBrief(projectId, base, draft, id)
          const current = { branchId, version }
          setData((previous) => ({ current, versions: [...(previous?.versions ?? []), current] }))
        } catch (failure) {
          void readLocalProjectBrief(projectId).then(setData, () => undefined)
          throw failure
        }
        onConfirmed?.()
      }}
    />
  )
}

function OfflineProjectBrief({
  branchId,
  current,
  versions,
  disabled,
  onDirty,
  onConfirm,
}: {
  branchId: string
  current: ProjectVersionRecord | undefined
  versions: ProjectVersionRecord[]
  disabled: boolean
  onDirty: (dirty: boolean) => void
  onConfirm: (draft: ProjectDraft) => Promise<void>
}) {
  const version = current?.version
  const [draft, setDraft] = useState<ProjectDraft>()
  const [savedDraft, setSavedDraft] = useState<Awaited<ReturnType<typeof readProjectDraft>>>()
  const [draftLoaded, setDraftLoaded] = useState(false)
  const [draftStatus, setDraftStatus] = useState('')
  const [draftError, setDraftError] = useState('')
  const draftWrite = useRef(0)
  const ideaField = useRef<HTMLTextAreaElement>(null)
  const [preview, setPreview] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const fresh = {
    base: version?.id ?? null,
    idea: version?.idea ?? '',
    brief: version?.brief ?? blankProjectBrief,
  }
  const value = draft ?? fresh
  const stale = !!draft && value.base !== fresh.base
  const recovering =
    savedDraft && !versions.some(({ version }) => version.id === savedDraft.draft.id) ? savedDraft : undefined
  const hasOwnRecovery = !!recovering && !recovering.foreign
  const record = versions.find((entry) => entry.version.id === preview)
  const dirty = !!draft
  useLeaveGuard(dirty, '离线立项卡')
  useEffect(() => {
    onDirty(dirty)
    return () => onDirty(false)
  }, [dirty, onDirty])

  useEffect(() => {
    let active = true
    void readProjectDraft(branchId)
      .then((saved) => {
        if (active) setSavedDraft(saved)
      })
      .catch(() => {
        if (active) setDraftError('无法读取本机立项草稿，可继续查看正式立项。')
      })
      .finally(() => {
        if (active) setDraftLoaded(true)
      })
    return () => {
      active = false
      draftWrite.current++
    }
  }, [branchId])

  function edit(next: ProjectDraft | undefined) {
    setDraft(next)
    setSavedDraft(undefined)
    setError('')
    const attempt = ++draftWrite.current
    setDraftStatus(next ? '正在暂存立项草稿…' : '')
    setDraftError('')
    void (next ? writeProjectDraft(branchId, next) : discardProjectDraft(branchId)).then(
      () => {
        if (attempt === draftWrite.current && next) setDraftStatus('立项草稿已暂存到本机。')
      },
      () => {
        if (attempt !== draftWrite.current) return
        setDraftStatus('')
        setDraftError(next ? '无法暂存立项草稿，当前输入仍在本页。' : '未能清除本机立项草稿，请重开核对。')
      },
    )
  }

  function update(next: Omit<ProjectDraft, 'id'>) {
    edit({ ...next, id: draft?.id ?? crypto.randomUUID() })
  }

  async function save() {
    if (!draft || stale) return
    setSaving(true)
    setError('')
    try {
      const copy = validateProjectDraft(draft.idea, draft.brief)
      await onConfirm({ ...draft, ...copy })
      edit(undefined)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '立项未保存。')
    } finally {
      setSaving(false)
    }
  }

  return (
    <details aria-label="离线立项台">
      <summary>立项台</summary>
      <p>{version ? `当前立项修订 ${version.id.slice(0, 8)}` : '还没有已确认立项。'}</p>
      {current?.source && (
        <p>
          已核验同步来源设备 <code>{current.source.device_id}</code>
        </p>
      )}
      {current?.fromPackage && !current.source && <p>立项来自导入的稿件交换包。</p>}
      {draftError && <p role="alert">{draftError}</p>}
      {draftStatus && <p role="status">{draftStatus}</p>}
      {error && <p role="alert">{error}</p>}
      {recovering && !draft && (
        <section aria-label="本机立项草稿恢复">
          <p>{recovering.foreign ? '另一窗口有未确认的立项草稿。' : '有未确认的本机立项草稿。'}</p>
          <button
            type="button"
            disabled={disabled || saving}
            onClick={() => {
              edit(recovering.foreign ? { ...recovering.draft, id: crypto.randomUUID() } : recovering.draft)
              // The form may be disabled until the recovery state is cleared.
              requestAnimationFrame(() => ideaField.current?.focus())
            }}
          >
            {recovering.foreign ? '取用另一窗口的立项草稿副本' : '继续编辑本机立项草稿'}
          </button>
          {hasOwnRecovery && (
            <button
              type="button"
              disabled={disabled || saving}
              onClick={() => {
                if (window.confirm('放弃这份未确认的本机立项草稿？')) edit(undefined)
              }}
            >
              放弃本机立项草稿
            </button>
          )}
        </section>
      )}
      {stale && draft && (
        <section aria-label="立项草稿基线已变化">
          <p role="alert">立项在别处已更新。请核对现行修订，当前输入仍保留。</p>
          {version && (
            <details>
              <summary>对照现行立项</summary>
              <pre>{describeProjectVersion(version)}</pre>
            </details>
          )}
          <button
            type="button"
            disabled={disabled || saving}
            onClick={() => {
              if (window.confirm('以现行立项作为基线继续核对这份草稿？'))
                edit({ ...draft, base: fresh.base, id: crypto.randomUUID() })
            }}
          >
            保留输入，改用现行立项基线
          </button>
        </section>
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault()
          if (!stale && draft) void save()
        }}
      >
        <fieldset disabled={disabled || saving || !draftLoaded || (!draft && hasOwnRecovery)}>
          <legend>创作思路与立项卡</legend>
          <label>
            创作思路
            <textarea
              ref={ideaField}
              required
              maxLength={6000}
              value={value.idea}
              onChange={(event) => update({ ...value, idea: event.target.value })}
            />
          </label>
          {[0, 1, 2].map((index) => (
            <label key={index}>
              {index === 0 ? '主类型' : `辅类型 ${index}（可选）`}
              <select
                value={value.brief.genre_ids[index] ?? ''}
                required={index === 0}
                disabled={index > value.brief.genre_ids.length}
                onChange={(event) => {
                  const genre_ids = [...value.brief.genre_ids]
                  if (event.target.value) genre_ids[index] = event.target.value
                  else genre_ids.splice(index, 1)
                  update({ ...value, brief: { ...value.brief, genre_ids } })
                }}
              >
                <option value="">{index === 0 ? '请选择主类型' : '无'}</option>
                {projectGenres
                  .filter((genre) => !value.brief.genre_ids.some((id, at) => at !== index && id === genre.id))
                  .map(({ id, name }) => (
                    <option key={id} value={id}>
                      {name}
                    </option>
                  ))}
              </select>
            </label>
          ))}
          {briefFields.map(([key, name, required]) => (
            <label key={key}>
              {name}
              {!required && '（可选）'}
              <textarea
                required={required}
                maxLength={700}
                value={value.brief[key]}
                onChange={(event) =>
                  update({ ...value, brief: { ...value.brief, [key]: event.target.value } })
                }
              />
            </label>
          ))}
          <button type="submit" disabled={!draft || stale}>
            确认立项并保存到浏览器
          </button>
          {draft && (
            <button
              type="button"
              onClick={() => {
                if (window.confirm('放弃本次未确认的立项修改？')) {
                  edit(undefined)
                }
              }}
            >
              载入当前立项
            </button>
          )}
        </fieldset>
      </form>
      {!!versions.length && (
        <details>
          <summary>立项版本历史</summary>
          <label>
            历史立项
            <select value={preview} onChange={(event) => setPreview(event.target.value)}>
              <option value="">选择立项修订</option>
              {versions.map(({ version }) => (
                <option key={version.id} value={version.id}>
                  {version.id.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
          {record && <pre>{describeProjectVersion(record.version)}</pre>}
        </details>
      )}
    </details>
  )
}
