import { useEffect, useState } from 'react'
import { api, label, type Artifact, type GenreCatalog, type Task } from './api'
import type { components } from './api-schema'
import { useLeaveGuard } from './leaveGuard'

type ProjectBrief = components['schemas']['ProjectBrief']
type ConfirmedProject = components['schemas']['ConfirmedProject']
export type ProjectDraft = { base_revision: number; idea: string; brief: ProjectBrief; candidate_id?: string }

const fields = [
  ['position', '核心定位'],
  ['track', '赛道'],
  ['audience', '读者定位'],
  ['distinction', '差异化'],
  ['length', '篇幅目标'],
  ['cadence', '更新计划'],
  ['ending', '终局'],
  ['final_image', '最后一镜'],
] as const

const blankBrief: ProjectBrief = {
  position: '',
  track: '',
  genre_ids: [],
  audience: '',
  distinction: '',
  length: '',
  cadence: '',
  ending: '',
  final_image: '',
}

export function ProjectBriefPanel({
  project,
  storyRevision,
  task,
  busy,
  modelReady,
  onGenerate,
  onConfirm,
  onWorld,
}: {
  project?: ConfirmedProject | null
  storyRevision: number
  task?: Task
  busy: boolean
  modelReady: boolean
  onGenerate: (idea: string) => void
  onConfirm: (draft: ProjectDraft) => Promise<boolean>
  onWorld: () => void
}) {
  const [catalog, setCatalog] = useState<GenreCatalog>()
  const [draft, setDraft] = useState<ProjectDraft>()
  const [candidate, setCandidate] = useState<Artifact>()
  const [catalogError, setCatalogError] = useState('')
  const [candidateError, setCandidateError] = useState('')
  const [open, setOpen] = useState(false)
  const current = {
    base_revision: project?.revision ?? 0,
    idea: project?.idea ?? '',
    brief: project?.brief ?? blankBrief,
  }
  const value = draft ?? current
  const artifactId = task?.result.artifact_id
  const stale = value.base_revision !== current.base_revision
  const generating = task?.status === 'queued' || task?.status === 'running'
  const candidateCurrent =
    !!candidate &&
    candidate.basis.story_revision === storyRevision &&
    candidate.basis.project_revision === current.base_revision
  const valid =
    !!value.idea.trim() &&
    !!value.brief.genre_ids.length &&
    fields.every(([key]) => key === 'cadence' || key === 'final_image' || !!value.brief[key]?.trim())
  useLeaveGuard(!!draft, '未确认的立项修改')

  useEffect(() => {
    if (!open || catalog) return
    let active = true
    void api<GenreCatalog>('/genre-catalog')
      .then((result) => {
        if (active) {
          setCatalog(result)
          setCatalogError('')
        }
      })
      .catch((failure: Error) => {
        if (active) setCatalogError(failure.message)
      })
    return () => {
      active = false
    }
  }, [open, catalog])

  useEffect(() => {
    let active = true
    setCandidate(undefined)
    setCandidateError('')
    if (artifactId) {
      void api<Artifact>(`/artifacts/${encodeURIComponent(artifactId)}`)
        .then((result) => {
          if (active) setCandidate(result)
        })
        .catch((failure: Error) => {
          if (active) setCandidateError(failure.message)
        })
    }
    return () => {
      active = false
    }
  }, [artifactId])

  function editBrief(brief: ProjectBrief) {
    setDraft({ ...value, brief })
  }

  function selectGenre(position: number, identifier: string) {
    const identifiers = [...value.brief.genre_ids]
    if (identifier) identifiers[position] = identifier
    else identifiers.splice(position, 1)
    editBrief({ ...value.brief, genre_ids: identifiers })
  }

  return (
    <details className="card" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>立项台{project ? ` · 已确认修订 ${project.revision}` : ''}</summary>
      <p className="small">确认后的定位、题材和终局作为生成设定的依据。修改立项后，请复核已有设定和草案。</p>
      {stale && <p role="alert">立项已在别处确认。当前编辑仍保留，请核对最新修订后重新录入。</p>}
      <fieldset disabled={busy || generating}>
        <legend>创作思路与立项卡</legend>
        <label>
          创作思路
          <textarea
            rows={3}
            maxLength={6000}
            value={value.idea}
            onChange={(event) => setDraft({ ...value, idea: event.target.value, candidate_id: undefined })}
          />
        </label>
        <div className="actions">
          <button
            type="button"
            disabled={!modelReady || !value.idea.trim() || stale}
            onClick={() => onGenerate(value.idea)}
          >
            AI 分析立项
          </button>
          {!!draft && (
            <button
              type="button"
              onClick={() => {
                if (window.confirm('放弃本次未确认的立项修改？')) setDraft(undefined)
              }}
            >
              载入已确认立项
            </button>
          )}
        </div>
        {task && (
          <p role="status">
            立项分析 · {label(task.status)}
            {task.error ? ` · ${task.error}` : ''}
          </p>
        )}
        {candidateError && <p role="alert">{candidateError}</p>}
        {candidate && (
          <div className="notice">
            <p className="small">
              {candidateCurrent
                ? 'AI 建议已可查看。载入后可以逐项修改。'
                : '该 AI 建议的立项或故事依据已过期。'}
            </p>
            <button
              type="button"
              disabled={!candidateCurrent || stale}
              onClick={() => {
                setDraft({
                  base_revision: current.base_revision,
                  idea: String(candidate.payload.idea),
                  brief: candidate.payload.brief as ProjectBrief,
                  candidate_id: candidate.id,
                })
              }}
            >
              载入 AI 立项建议
            </button>
          </div>
        )}
        {catalogError && <p role="alert">{catalogError}</p>}
        {catalog &&
          [0, 1, 2].map((index) => (
            <label key={index}>
              {index === 0 ? '主类型' : `辅类型 ${index}（可选）`}
              <select
                value={value.brief.genre_ids[index] || ''}
                disabled={index > value.brief.genre_ids.length}
                onChange={(event) => selectGenre(index, event.target.value)}
              >
                <option value="">{index === 0 ? '选择主类型' : '无'}</option>
                {catalog.genres
                  .filter(
                    (genre) =>
                      !value.brief.genre_ids.some((id, position) => id === genre.id && position !== index),
                  )
                  .map((genre) => (
                    <option key={genre.id} value={genre.id}>
                      {genre.name}
                    </option>
                  ))}
              </select>
            </label>
          ))}
        {fields.map(([key, title]) => (
          <label key={key}>
            {title}
            {key === 'cadence' || key === 'final_image' ? '（可选）' : ''}
            <textarea
              value={value.brief[key] ?? ''}
              maxLength={700}
              rows={2}
              onChange={(event) => editBrief({ ...value.brief, [key]: event.target.value })}
            />
          </label>
        ))}
        <div className="actions">
          <button
            className="primary"
            type="button"
            disabled={!draft || stale || !valid}
            onClick={() => {
              void onConfirm(value).then((confirmed) => {
                if (confirmed) setDraft(undefined)
              })
            }}
          >
            确认立项
          </button>
          {project && (
            <button type="button" onClick={onWorld}>
              去完善世界设定
            </button>
          )}
        </div>
      </fieldset>
    </details>
  )
}
