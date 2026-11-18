import { useEffect, useState } from 'react'
import {
  blankProjectBrief,
  briefFields,
  describeProjectVersion,
  projectGenres,
  validateProjectDraft,
  type ProjectBrief,
  type ProjectVersionRecord,
} from './projectSyncModel'
import { useLeaveGuard } from './leaveGuard'
import { confirmLocalProjectBrief, readLocalProjectBrief } from './offlineStore'

type Draft = { base: string | null; idea: string; brief: ProjectBrief }

export function LocalProjectBrief({
  projectId,
  refreshToken,
  onConfirmed,
  ...controls
}: {
  projectId: string
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
      onConfirm={async ({ base, ...draft }) => {
        try {
          await confirmLocalProjectBrief(projectId, base, draft)
        } catch (failure) {
          void readLocalProjectBrief(projectId).then(setData, () => undefined)
          throw failure
        }
        setData(await readLocalProjectBrief(projectId))
        onConfirmed?.()
      }}
    />
  )
}

function OfflineProjectBrief({
  current,
  versions,
  disabled,
  onDirty,
  onConfirm,
}: {
  current: ProjectVersionRecord | undefined
  versions: ProjectVersionRecord[]
  disabled: boolean
  onDirty: (dirty: boolean) => void
  onConfirm: (draft: Draft) => Promise<void>
}) {
  const version = current?.version
  const [draft, setDraft] = useState<Draft>()
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
  const record = versions.find((entry) => entry.version.id === preview)
  const dirty = !!draft
  useLeaveGuard(dirty, '离线立项卡')
  useEffect(() => {
    onDirty(dirty)
    return () => onDirty(false)
  }, [dirty, onDirty])

  async function save() {
    setSaving(true)
    setError('')
    try {
      const copy = validateProjectDraft(value.idea, value.brief)
      await onConfirm({ ...copy, base: value.base })
      setDraft(undefined)
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
      {stale && <p role="alert">立项在别处已更新。请核对现行修订，当前输入仍保留。</p>}
      {error && <p role="alert">{error}</p>}
      <form
        onSubmit={(event) => {
          event.preventDefault()
          if (!stale && draft) void save()
        }}
      >
        <fieldset disabled={disabled || saving}>
          <legend>创作思路与立项卡</legend>
          <label>
            创作思路
            <textarea
              required
              maxLength={6000}
              value={value.idea}
              onChange={(event) => setDraft({ ...value, idea: event.target.value })}
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
                  setDraft({ ...value, brief: { ...value.brief, genre_ids } })
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
                  setDraft({ ...value, brief: { ...value.brief, [key]: event.target.value } })
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
                  setDraft(undefined)
                  setError('')
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
