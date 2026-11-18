import { useEffect, useState } from 'react'
import { readLocalReferenceHistory } from './offlineStore'
import type { ReferenceHistory } from './referenceArchive'

const cardLabels = { ready: '可引用', incomplete: '未完成', archived: '已归档' }
const cardFields = [
  ['observation', '观察'],
  ['interpretation', '效果解释'],
  ['technique', '可迁移手法'],
  ['conditions', '适用条件'],
  ['exceptions', '须替换处'],
  ['evidence', '原文证据'],
] as const

export function OfflineReferenceHistory({
  projectId,
  includedExcerpts,
  setIncludedExcerpts,
}: {
  projectId: string
  includedExcerpts: readonly string[]
  setIncludedExcerpts: (ids: string[]) => void
}) {
  const [archive, setArchive] = useState<ReferenceHistory>()
  const [selected, setSelected] = useState('')
  const [error, setError] = useState('')
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
  }, [projectId])
  if (error) return <p role="alert">{error}</p>
  if (!archive) return null
  const adopted = new Set(archive.adopted_heads.map(({ revision_id }) => revision_id))
  const versions = [...archive.versions].reverse()
  const version =
    versions.find(({ id }) => id === selected) ?? versions.find(({ id }) => adopted.has(id)) ?? versions[0]
  const quote = archive.excerpts?.[version.id]
  return (
    <details aria-label="离线手法卡历史">
      <summary>手法卡历史</summary>
      <label>
        手法卡修订
        <select value={version.id} onChange={(event) => setSelected(event.target.value)}>
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
      {version.card && version.citation && (
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
