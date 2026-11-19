import { useEffect, useRef, useState } from 'react'
import {
  hasLocalSnapshotHistory,
  manuscriptHash,
  readLocalCheckpointRestore,
  readLocalSnapshotHistory,
  readLocalSourceRevision,
  type LocalRevision,
} from './offlineStore'
import type { PortableCheckpoint } from './portableCheckpoint'
import { CheckpointRestoreAction } from './CheckpointRestoreAction'
import { snapshotSharedChanges } from './workSnapshotView'

type CheckpointChapter = PortableCheckpoint['chapters'][number]
type RestoreControls = {
  disabled: boolean
  writeBlocked?: string
  submitRestore: (
    checkpointId: string,
    basis: string,
    requestId: string,
  ) => Promise<{ recoveryCheckpointId: string }>
}

export function OfflineCheckpointHistory({
  projectId,
  refreshToken,
  ...restore
}: {
  projectId: string
  refreshToken: number
} & RestoreControls) {
  const [available, setAvailable] = useState(false)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    let active = true
    void hasLocalSnapshotHistory(projectId).then(
      (found) => {
        if (active) setAvailable(found)
      },
      () => {
        if (active) setAvailable(true)
      },
    )
    return () => {
      active = false
    }
  }, [projectId, refreshToken])

  if (!available) return null
  return (
    <details
      aria-label="已导入的卷书恢复点"
      style={{ overflowWrap: 'anywhere' }}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>已导入的卷书恢复点</summary>
      {open && <CheckpointContents projectId={projectId} {...restore} />}
    </details>
  )
}

function CheckpointContents({
  projectId,
  disabled,
  writeBlocked,
  submitRestore,
}: { projectId: string } & RestoreControls) {
  const [checkpoints, setCheckpoints] = useState<PortableCheckpoint[]>()
  const [selected, setSelected] = useState('')
  const [failure, setFailure] = useState('')
  const [chapter, setChapter] = useState<{ binding: CheckpointChapter; version: LocalRevision }>()
  const [reading, setReading] = useState(false)
  const [readError, setReadError] = useState('')
  const [archiveRevision, setArchiveRevision] = useState(0)
  const latestRequest = useRef(0)

  useEffect(() => {
    let active = true
    void readLocalSnapshotHistory(projectId).then(
      (records) => {
        if (active) {
          setCheckpoints(records)
          setFailure('')
        }
      },
      (error: unknown) => {
        if (active) setFailure(error instanceof Error ? error.message : '无法读取卷书恢复点。')
      },
    )
    return () => {
      active = false
      latestRequest.current++
    }
  }, [projectId, archiveRevision])

  async function openChapter(binding: CheckpointChapter) {
    const request = ++latestRequest.current
    setChapter(undefined)
    setReadError('')
    setReading(true)
    try {
      const version = await readLocalSourceRevision(projectId, binding.revision_id)
      if (
        !version ||
        version.chapter_id !== binding.id ||
        version.revision !== binding.revision ||
        version.sha256 !== binding.sha256 ||
        (await manuscriptHash(version.content)) !== binding.sha256
      )
        throw new Error('找不到与恢复点对应的原章稿修订，或原稿摘要无法核验。')
      if (request === latestRequest.current) setChapter({ binding, version })
    } catch (error: unknown) {
      if (request === latestRequest.current)
        setReadError(error instanceof Error ? error.message : '无法读取原稿。')
    } finally {
      if (request === latestRequest.current) setReading(false)
    }
  }

  if (failure) return <p role="alert">{failure}</p>
  if (!checkpoints) return <p role="status">正在读取卷书恢复点…</p>
  const checkpoint = checkpoints.find(({ id }) => id === selected) ?? checkpoints[0]
  if (!checkpoint) return <p>此作品没有归档恢复点。</p>
  const byId = new Map(checkpoint.chapters.map((row) => [row.id, row]))
  return (
    <>
      <p>可查阅作品备份保留的原始章稿与版本引用。</p>
      <label>
        选择已归档恢复点
        <select
          value={checkpoint.id}
          disabled={disabled}
          onChange={(event) => {
            latestRequest.current++
            setSelected(event.target.value)
            setChapter(undefined)
            setReading(false)
            setReadError('')
          }}
        >
          {checkpoints.map(({ id, label, scope, created }) => (
            <option key={id} value={id}>
              {label} · {scope === 'book' ? '整书' : '分卷'} ·{' '}
              {new Date(created * 1000).toLocaleString('zh-CN')}
            </option>
          ))}
        </select>
      </label>
      <p>
        {checkpoint.scope === 'book' ? '整书范围' : `分卷编号 ${checkpoint.scope_id}`} ·{' '}
        {checkpoint.scoped_chapter_ids.length} 章
      </p>
      {checkpoint.restore_sources && (
        <CheckpointRestoreAction
          key={checkpoint.id}
          label={checkpoint.label}
          disabled={disabled}
          writeBlocked={writeBlocked}
          previewRestore={async () => {
            const preview = await readLocalCheckpointRestore(projectId, checkpoint.id)
            return {
              basis: preview.basis,
              changes: preview.changes,
              blockers: preview.blockers,
              shared: snapshotSharedChanges({
                definitions: preview.projectChanged ? ['project_brief'] : [],
                conception: preview.settingsChanged,
                outline: preview.outlineChanged,
              }),
              affectedReports: preview.affectedReports,
              affectedSettingUses: preview.affectedSettingUses,
              notes: [
                ...(preview.sceneSpansForReview
                  ? [`${preview.sceneSpansForReview} 处场景绑定需重新核对。`]
                  : []),
                '正式体检、完本结果和原文证明可从历史查阅。',
              ],
            }
          }}
          submitRestore={async (basis, requestId) => {
            const receipt = await submitRestore(checkpoint.id, basis, requestId)
            return { recoveryId: receipt.recoveryCheckpointId }
          }}
          onRestored={() => {
            setReading(false)
            setArchiveRevision((revision) => revision + 1)
          }}
        />
      )}
      <ul aria-label="恢复点章节">
        {checkpoint.scoped_chapter_ids.map((id) => {
          const binding = byId.get(id)
          return binding ? (
            <li key={id}>
              <button type="button" onClick={() => void openChapter(binding)}>
                查看「{binding.title}」原稿
              </button>
            </li>
          ) : null
        })}
      </ul>
      <details>
        <summary>原快照及版本引用</summary>
        <dl>
          <div>
            <dt>原快照编号</dt>
            <dd>{checkpoint.id}</dd>
          </div>
          <div>
            <dt>原快照清单 SHA-256</dt>
            <dd>{checkpoint.source_manifest_sha256}</dd>
          </div>
          {checkpoint.shared.project_id && (
            <div>
              <dt>立项依据</dt>
              <dd>{checkpoint.shared.project_id}</dd>
            </div>
          )}
          {checkpoint.shared.settings_id && (
            <div>
              <dt>设定依据</dt>
              <dd>{checkpoint.shared.settings_id}</dd>
            </div>
          )}
          {checkpoint.shared.outline_id && (
            <div>
              <dt>大纲依据</dt>
              <dd>{checkpoint.shared.outline_id}</dd>
            </div>
          )}
          {checkpoint.reports.workflow_inspection && (
            <div>
              <dt>原体检</dt>
              <dd>{checkpoint.reports.workflow_inspection}</dd>
            </div>
          )}
          {checkpoint.reports.book_completion && (
            <div>
              <dt>原完本</dt>
              <dd>{checkpoint.reports.book_completion}</dd>
            </div>
          )}
        </dl>
      </details>
      {reading && <p role="status">正在读取原章稿…</p>}
      {readError && <p role="alert">{readError}</p>}
      {chapter && (
        <section aria-label="归档恢复点原稿">
          <h3>{chapter.binding.title}</h3>
          <p>
            修订 {chapter.version.revision} · {chapter.version.id}
          </p>
          <p>SHA-256 {chapter.version.sha256}</p>
          <pre className="report">{chapter.version.content || '空稿'}</pre>
        </section>
      )}
    </>
  )
}
