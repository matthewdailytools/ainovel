import { useEffect, useRef, useState } from 'react'
import { api, type Chapter } from './api'
import { confirmLeave, useLeaveGuard } from './leaveGuard'

type Scope = 'book' | 'volume' | 'chapter'
type Mode = 'in_place' | 'new_branch'
type Snapshot = {
  id: string
  label: string
  scope: Scope
  scope_id: string | null
  purpose: string
  created: number
  story_revision: number
}
type Preview = {
  snapshot: Snapshot
  basis_sha256: string
  changes: {
    chapter_id: string
    title: string
    action: 'restore' | 'archive' | 'keep'
    snapshot_revision: number | null
    current_revision: number | null
  }[]
  in_place_blockers: string[]
  shared_changes: { definitions: string[]; conception: boolean; outline: boolean }
  reports: Record<string, string>
  notes: string[]
}
type ChapterDiff = {
  title: string
  snapshot: { revision: number; content: string }
  current: { revision: number; content: string; archived: boolean } | null
}
type RestoreReceipt = { id: string; branch_id: string; recovery_snapshot_id: string; snapshot_id: string }
type Outline = { confirmed: { plan: { volumes: { id: string; title: string }[] } } | null }

const scopes = { book: '全书', volume: '卷', chapter: '章' }
const chapterActions = { restore: '恢复快照稿', archive: '移到归档', keep: '保留' }
const objectNames: Record<string, string> = {
  intent: '作者约定',
  reader: '题材设定',
  world: '世界规则',
  characters: '人物设定',
  outline: '旧版规划',
  project_brief: '立项',
  workflow_inspection: '体检单',
  book_completion: '完本记录',
  author_finalization: '作者定稿',
}

export function WorkSnapshotPanel({
  branchId,
  chapters,
  onRestored,
}: {
  branchId: string
  chapters: Pick<Chapter, 'id' | 'title'>[]
  onRestored?: (branchId: string) => void | Promise<void>
}) {
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [volumes, setVolumes] = useState<{ id: string; title: string }[]>([])
  const [label, setLabel] = useState('')
  const [scope, setScope] = useState<Scope>('book')
  const [scopeId, setScopeId] = useState('')
  const [snapshotId, setSnapshotId] = useState('')
  const [preview, setPreview] = useState<Preview>()
  const [diff, setDiff] = useState<ChapterDiff>()
  const [mode, setMode] = useState<Mode>('in_place')
  const [branchName, setBranchName] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const pending = useRef<{ signature: string; key: string } | null>(null)
  const route = `/branches/${branchId}/work-snapshots`
  useLeaveGuard(!!label.trim() || (!!preview && (!!branchName.trim() || confirmed)), '未提交的作品快照操作')

  useEffect(() => {
    let active = true
    void Promise.all([api<Snapshot[]>(route), api<Outline>(`/branches/${branchId}/outline-workspace`)])
      .then(([rows, outline]) => {
        if (!active) return
        setSnapshots(rows)
        setVolumes(outline.confirmed?.plan.volumes ?? [])
      })
      .catch((error: unknown) => {
        if (active) setError(error instanceof Error ? error.message : '无法读取作品快照。')
      })
    return () => {
      active = false
    }
  }, [route, branchId])

  function resetPreview() {
    setPreview(undefined)
    setDiff(undefined)
    setConfirmed(false)
    setBranchName('')
    pending.current = null
  }

  async function perform(operation: () => Promise<void>) {
    if (busy) return
    setBusy(true)
    setMessage('')
    setError('')
    try {
      await operation()
    } catch (error) {
      setError(error instanceof Error ? error.message : '快照操作失败。')
    } finally {
      setBusy(false)
    }
  }

  async function create() {
    const snapshot = await api<Snapshot>(route, 'POST', { label, scope, scope_id: scopeId || null })
    setSnapshots((rows) => [snapshot, ...rows])
    setSnapshotId(snapshot.id)
    setLabel('')
    resetPreview()
    setMessage('已保存作品快照。')
  }

  async function compare() {
    const selected = await api<Preview>(`${route}/${snapshotId}/preview`)
    resetPreview()
    setMode(selected.in_place_blockers.length ? 'new_branch' : 'in_place')
    setPreview(selected)
  }

  async function restore() {
    if (!preview || !confirmed) return
    const request = {
      basis_sha256: preview.basis_sha256,
      mode,
      branch_name: mode === 'new_branch' ? branchName : null,
    }
    const signature = JSON.stringify([snapshotId, request])
    if (pending.current?.signature !== signature) pending.current = { signature, key: crypto.randomUUID() }
    const receipt = await api<RestoreReceipt>(`${route}/${snapshotId}/restore`, 'POST', {
      ...request,
      idempotency_key: pending.current.key,
    })
    resetPreview()
    setMessage(
      mode === 'new_branch'
        ? `已创建恢复分支「${branchName}」。原工作版本下可查看恢复前检查点。`
        : '已恢复为新的当前修订。恢复前检查点可在快照列表查看。',
    )
    try {
      setSnapshots(await api<Snapshot[]>(route))
      if (onRestored) await onRestored(receipt.branch_id)
    } catch (error) {
      setError(
        error instanceof Error
          ? `恢复已提交，刷新失败。${error.message}`
          : '恢复已提交，请重新打开工作版本。',
      )
    }
  }

  const choices = scope === 'volume' ? volumes : chapters
  const definitionChanges = preview?.shared_changes.definitions.map((kind) => objectNames[kind] || kind) ?? []
  return (
    <section className="card" aria-label="作品快照与回滚" style={{ overflowWrap: 'anywhere' }}>
      <h2>作品快照与回滚</h2>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          void perform(create)
        }}
      >
        <fieldset disabled={busy}>
          <legend>保存检查点</legend>
          <label>
            快照名称
            <input value={label} onChange={(event) => setLabel(event.target.value)} maxLength={200} />
          </label>
          <label>
            快照范围
            <select
              value={scope}
              onChange={(event) => {
                setScope(event.target.value as Scope)
                setScopeId('')
              }}
            >
              <option value="book">全书</option>
              <option value="volume">卷</option>
              <option value="chapter">章</option>
            </select>
          </label>
          {scope !== 'book' && (
            <label>
              {scope === 'volume' ? '快照所用卷' : '快照所用章节'}
              <select value={scopeId} onChange={(event) => setScopeId(event.target.value)}>
                <option value="">请选择</option>
                {choices.map(({ id, title }) => (
                  <option key={id} value={id}>
                    {title}
                  </option>
                ))}
              </select>
            </label>
          )}
          <button type="submit" disabled={!label.trim() || (scope !== 'book' && !scopeId)}>
            保存快照
          </button>
        </fieldset>
      </form>
      <div className="actions">
        <label>
          已保存快照
          <select
            value={snapshotId}
            disabled={busy}
            onChange={(event) => {
              if (preview && !confirmLeave()) return
              setSnapshotId(event.target.value)
              resetPreview()
            }}
          >
            <option value="">选择快照</option>
            {snapshots.map((snapshot) => (
              <option key={snapshot.id} value={snapshot.id}>
                {snapshot.label} · {scopes[snapshot.scope]} · 故事版本 {snapshot.story_revision}
              </option>
            ))}
          </select>
        </label>
        <button type="button" disabled={busy || !snapshotId} onClick={() => void perform(compare)}>
          预览恢复差异
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      {preview && (
        <section aria-label="恢复差异预览">
          <h3>
            {preview.snapshot.label} · {scopes[preview.snapshot.scope]}
          </h3>
          <ul>
            {preview.changes.map((change) => (
              <li key={change.chapter_id}>
                {change.title} · {chapterActions[change.action]} · 当前修订 {change.current_revision ?? '无'}{' '}
                / 快照修订 {change.snapshot_revision ?? '无'}
                {change.snapshot_revision !== null && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void perform(async () => {
                        setDiff(
                          await api<ChapterDiff>(`${route}/${snapshotId}/chapters/${change.chapter_id}`),
                        )
                      })
                    }
                  >
                    比较{change.title}正文
                  </button>
                )}
              </li>
            ))}
          </ul>
          {diff && (
            <section aria-label="章稿对照">
              <h4>{diff.title}</h4>
              <h5>当前修订 {diff.current?.revision ?? '无'}</h5>
              <pre className="report">{diff.current?.content ?? '当前目录无对应稿件'}</pre>
              <h5>快照修订 {diff.snapshot.revision}</h5>
              <pre className="report">{diff.snapshot.content}</pre>
            </section>
          )}
          {(definitionChanges.length > 0 ||
            preview.shared_changes.conception ||
            preview.shared_changes.outline) && (
            <p>
              共用依据有变化 ·{' '}
              {[
                ...definitionChanges,
                ...(preview.shared_changes.conception ? ['分类构思与设定'] : []),
                ...(preview.shared_changes.outline ? ['四级大纲'] : []),
              ].join('、')}
            </p>
          )}
          {Object.keys(preview.reports).length > 0 && (
            <p>
              恢复后需复检{' '}
              {Object.keys(preview.reports)
                .map((kind) => objectNames[kind] || kind)
                .join('、')}
              。
            </p>
          )}
          {preview.in_place_blockers.map((note) => (
            <p className="notice" key={note}>
              {note}
            </p>
          ))}
          <details>
            <summary>恢复范围说明</summary>
            <ul>
              {preview.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </details>
          <form
            onSubmit={(event) => {
              event.preventDefault()
              void perform(restore)
            }}
          >
            <fieldset disabled={busy}>
              <legend>提交恢复</legend>
              <label>
                恢复方式
                <select
                  value={mode}
                  onChange={(event) => {
                    setMode(event.target.value as Mode)
                    setConfirmed(false)
                  }}
                >
                  <option value="in_place" disabled={preview.in_place_blockers.length > 0}>
                    原位恢复为新修订
                  </option>
                  <option value="new_branch">建立新分支</option>
                </select>
              </label>
              {mode === 'new_branch' && (
                <label>
                  新分支名称
                  <input
                    value={branchName}
                    maxLength={200}
                    onChange={(event) => {
                      setBranchName(event.target.value)
                      setConfirmed(false)
                    }}
                  />
                </label>
              )}
              <label className="check">
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                />
                我已核对恢复影响
              </label>
              <button
                className="primary"
                type="submit"
                disabled={
                  !confirmed ||
                  (mode === 'new_branch' ? !branchName.trim() : preview.in_place_blockers.length > 0)
                }
              >
                提交恢复
              </button>
            </fieldset>
          </form>
        </section>
      )}
    </section>
  )
}
