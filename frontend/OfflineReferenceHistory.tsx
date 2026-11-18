import { useEffect, useState } from 'react'
import { confirmLocalReferenceCard, readLocalReferenceHistory } from './offlineStore'
import { createDraftOwner } from './drafts'
import { useLeaveGuard } from './leaveGuard'
import { parseLocalReferenceDraft, type ReferenceHistory } from './referenceArchive'
import { useWorkbenchDraft, WorkbenchDraftRecovery } from './workbenchDrafts'

const cardLabels = { ready: '可引用', incomplete: '未完成', archived: '已归档' }
const cardFields = [
  ['observation', '观察'],
  ['interpretation', '效果解释'],
  ['technique', '可迁移手法'],
  ['conditions', '适用条件'],
  ['exceptions', '须替换处'],
  ['evidence', '原文证据'],
] as const
const owner = createDraftOwner('ainovel-reference-draft-window')

export function OfflineReferenceHistory({
  branchId,
  projectId,
  refreshToken,
  disabled,
  onDirty,
  onConfirmed,
  includedExcerpts,
  setIncludedExcerpts,
}: {
  branchId: string
  projectId: string
  refreshToken: number
  disabled: boolean
  onDirty: (dirty: boolean) => void
  onConfirmed: () => void
  includedExcerpts: readonly string[]
  setIncludedExcerpts: (ids: string[]) => void
}) {
  const [archive, setArchive] = useState<ReferenceHistory>()
  const [selected, setSelected] = useState('')
  const [error, setError] = useState('')
  const {
    draft,
    setDraft,
    recovery,
    loaded,
    error: draftError,
    status: draftStatus,
  } = useWorkbenchDraft({
    key: `offline/reference-card/${branchId}`,
    label: '手法卡',
    owner,
    parse: parseLocalReferenceDraft,
  })
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState('')
  const dirty = draft !== undefined
  useLeaveGuard(dirty, '离线手法卡')
  useEffect(() => {
    onDirty(dirty)
    return () => onDirty(false)
  }, [dirty, onDirty])

  useEffect(() => {
    let active = true
    void readLocalReferenceHistory(projectId).then(
      (history) => {
        if (active) setArchive(history)
      },
      (error: unknown) => {
        if (active) setError(error instanceof Error ? error.message : '无法读取手法卡档案。')
      },
    )
    return () => {
      active = false
    }
  }, [projectId, refreshToken])
  if (!archive && !draft && !recovery && !draftError) return error ? <p role="alert">{error}</p> : null
  const adopted = new Set(archive?.adopted_heads.map(({ revision_id }) => revision_id))
  const versions = [...(archive?.versions ?? [])].reverse()
  const version = draft
    ? versions.find(({ id }) => id === draft.base)
    : (versions.find(({ id }) => id === selected) ??
      versions.find(({ id }) => adopted.has(id)) ??
      versions[0])
  const quote = version && archive?.excerpts?.[version.id]
  const stale =
    !!draft &&
    (!version ||
      draft.session_id !== version.session_id ||
      draft.card_id !== version.card_id ||
      !adopted.has(draft.base))

  async function save() {
    if (!draft || stale || saving || disabled) return
    setSaving(true)
    setError('')
    try {
      const next = await confirmLocalReferenceCard(projectId, draft.base, draft.card)
      setSelected(next.id)
      setDraft(undefined)
      setNotice('已保存手法卡修订，原出处和历史仍可查。')
      onConfirmed()
    } catch (error) {
      setError(error instanceof Error ? error.message : '手法卡修订未保存。')
      setArchive(await readLocalReferenceHistory(projectId).catch(() => archive))
    } finally {
      setSaving(false)
    }
  }
  return (
    <details aria-label="离线手法卡历史">
      <summary>手法卡历史</summary>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {draftError && <p role="alert">{draftError}</p>}
      {draftStatus && <p role="status">{draftStatus}</p>}
      {recovery && !draft && (
        <WorkbenchDraftRecovery
          label="手法卡"
          foreign={recovery.foreign}
          disabled={disabled || saving}
          onDiscard={() => setDraft(undefined)}
          onEdit={() => {
            setError('')
            setNotice('')
            setDraft(recovery.value)
          }}
        />
      )}
      {version && (
        <>
          <label>
            手法卡修订
            <select disabled={dirty} value={version.id} onChange={(event) => setSelected(event.target.value)}>
              {versions.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.card?.technique.slice(0, 60) ?? entry.card_id} · 修订 {entry.revision} ·{' '}
                  {cardLabels[entry.assessment]}
                </option>
              ))}
            </select>
          </label>
          <p className="small">
            {adopted.has(version.id) ? '采用版本' : '历史版本'} · {cardLabels[version.assessment]} ·{' '}
            {version.id.slice(0, 8)}
          </p>
          {version.missing_fields.length > 0 && (
            <p>
              缺少
              {cardFields
                .filter(([key]) => version.missing_fields.includes(key))
                .map(([, label]) => label)
                .join('、')}
            </p>
          )}
        </>
      )}
      {version?.assessment === 'ready' && adopted.has(version.id) && version.card && !draft && (
        <button
          type="button"
          disabled={disabled || !loaded || recovery?.foreign === false}
          onClick={() => {
            if (!version.card) return
            setError('')
            setNotice('')
            setDraft({
              session_id: version.session_id,
              card_id: version.card_id,
              base: version.id,
              card: { ...version.card },
            })
          }}
        >
          编辑手法卡摘要
        </button>
      )}
      {draft && (
        <fieldset disabled={disabled || saving}>
          <legend>编辑手法卡摘要</legend>
          {stale && <p role="alert">采用版已在别处更新。当前输入保留，请先比较修订。</p>}
          {cardFields.map(([key, label]) =>
            key === 'evidence' ? null : (
              <label key={key}>
                {label}
                <textarea
                  rows={2}
                  maxLength={2000}
                  value={draft.card[key]}
                  onChange={(event) =>
                    setDraft({ ...draft, card: { ...draft.card, [key]: event.target.value } })
                  }
                />
              </label>
            ),
          )}
          <button
            type="button"
            disabled={!!stale || Object.values(draft.card).some((value) => !value.trim())}
            onClick={() => void save()}
          >
            确认手法卡修订
          </button>
          <button
            type="button"
            onClick={() => {
              if (window.confirm('放弃这次手法卡摘要修改？')) setDraft(undefined)
            }}
          >
            放弃本次修改
          </button>
        </fieldset>
      )}
      {version?.card && version.citation && (
        <>
          <dl>
            <dt>拆解目标</dt>
            <dd>{version.goal}</dd>
            {cardFields.flatMap(([key, label]) =>
              version.card && key !== 'evidence'
                ? [<dt key={`${key}-label`}>{label}</dt>, <dd key={key}>{version.card[key]}</dd>]
                : [],
            )}
          </dl>
          <p className="small">
            来源 {version.citation.title} · 片段 {version.citation.unit_id} · 选区{' '}
            {version.citation.selection_start}–{version.citation.selection_end}
          </p>
          {quote ? (
            <>
              <blockquote>{quote}</blockquote>
              <label>
                <input
                  type="checkbox"
                  checked={includedExcerpts.includes(version.id)}
                  onChange={(event) =>
                    setIncludedExcerpts(
                      event.target.checked
                        ? [...includedExcerpts, version.id]
                        : includedExcerpts.filter((id) => id !== version.id),
                    )
                  }
                />
                随下一次稿件包导出带上这条原句
              </label>
            </>
          ) : (
            <p className="small">档案保留了引用地址。这里没有附带原文。</p>
          )}
        </>
      )}
    </details>
  )
}
