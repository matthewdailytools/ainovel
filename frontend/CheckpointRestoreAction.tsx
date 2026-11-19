import { useEffect, useRef, useState } from 'react'
import { RestoreReportList, type RestoreAffectedReport } from './ChapterRestorePreview'
import { useLeaveGuard } from './leaveGuard'

export type CheckpointRestorePreview = {
  basis: string
  changes: readonly {
    id: string
    title: string
    action: 'restore' | 'archive' | 'keep'
    currentRevision: number | null
    targetRevision: number | null
  }[]
  blockers: readonly string[]
  shared: readonly string[]
  affectedReports: readonly RestoreAffectedReport[]
  affectedSettingUses?: readonly { chapterTitle: string; title: string; versionId: string }[]
  notes: readonly string[]
}

type RestoreResult = { recoveryId: string }

const chapterActions = { restore: '恢复快照稿', archive: '移到归档', keep: '保留' }

function requiresPreview(error: unknown) {
  if (!(error instanceof Error) || !('code' in error)) return false
  return ['conflict', 'snapshot_changed', 'snapshot_restore_scope', 'snapshot_source'].includes(
    String(error.code),
  )
}

export function CheckpointRestoreAction({
  label,
  previewRestore,
  submitRestore,
  onRestored,
  disabled,
  writeBlocked,
}: {
  label: string
  previewRestore: () => Promise<CheckpointRestorePreview>
  submitRestore: (basis: string, requestId: string) => Promise<RestoreResult>
  onRestored?: () => void | Promise<void>
  disabled?: boolean
  writeBlocked?: string
}) {
  const [preview, setPreview] = useState<CheckpointRestorePreview>()
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState('')
  const [result, setResult] = useState<RestoreResult>()
  const pending = useRef<{ basis: string; requestId: string }>(null)
  const previewHeading = useRef<HTMLHeadingElement>(null)
  const previewButton = useRef<HTMLButtonElement>(null)
  const active = useRef(false)
  const running = useRef(false)
  const needsFocus = useRef(false)
  useLeaveGuard(confirmed && !busy, '未提交的归档原位恢复')

  useEffect(() => {
    active.current = true
    return () => {
      active.current = false
    }
  }, [])
  useEffect(() => {
    if (preview) previewHeading.current?.focus()
  }, [preview])
  useEffect(() => {
    if (!busy && !disabled && needsFocus.current) {
      needsFocus.current = false
      previewButton.current?.focus()
    }
  }, [busy, disabled])

  async function perform(operation: () => Promise<void>) {
    if (running.current || disabled) return
    running.current = true
    setBusy(true)
    setFailure('')
    try {
      await operation()
    } catch (error) {
      if (active.current) setFailure(error instanceof Error ? error.message : '无法从归档恢复。')
    } finally {
      running.current = false
      if (active.current) setBusy(false)
    }
  }

  async function load() {
    setConfirmed(false)
    setResult(undefined)
    setPreview(undefined)
    const next = await previewRestore()
    if (active.current) setPreview(next)
  }

  async function submit() {
    if (!preview || !confirmed || writeBlocked || preview.blockers.length) return
    if (pending.current?.basis !== preview.basis)
      pending.current = { basis: preview.basis, requestId: crypto.randomUUID() }
    let restored: RestoreResult
    try {
      restored = await submitRestore(preview.basis, pending.current.requestId)
    } catch (error) {
      if (active.current && requiresPreview(error)) {
        needsFocus.current = true
        setConfirmed(false)
        setPreview(undefined)
      }
      throw error
    }
    if (!active.current) return
    pending.current = null
    needsFocus.current = true
    setResult(restored)
    setConfirmed(false)
    setPreview(undefined)
    try {
      await onRestored?.()
    } catch (error) {
      throw new Error(
        error instanceof Error
          ? `恢复已提交，刷新失败。${error.message}`
          : '恢复已提交，请重新打开工作版本。',
      )
    }
  }

  return (
    <div>
      <button
        ref={previewButton}
        type="button"
        disabled={busy || disabled}
        onClick={() => void perform(load)}
      >
        {preview ? '重新预览归档原位恢复' : '预览归档原位恢复'}
      </button>
      {busy && <p role="status">正在核对或提交归档恢复…</p>}
      {failure && <p role="alert">{failure}</p>}
      {result && (
        <p role="status">
          已恢复为新的当前修订。恢复前检查点 <code>{result.recoveryId}</code>。
        </p>
      )}
      {preview && (
        <section aria-label="归档原位恢复预览" style={{ overflowWrap: 'anywhere' }}>
          <h4 ref={previewHeading} tabIndex={-1}>
            原位恢复「{label}」
          </h4>
          <p>恢复写入当前工作版本的新修订，同时保存恢复前检查点。</p>
          {preview.changes.length > 0 && (
            <ul aria-label="目录与章稿变化">
              {preview.changes.map((change) => (
                <li key={change.id}>
                  {change.title} · {chapterActions[change.action]} · 当前修订 {change.currentRevision ?? '无'}{' '}
                  / 归档修订 {change.targetRevision ?? '无'}
                </li>
              ))}
            </ul>
          )}
          {preview.shared.length > 0 && <p>共用依据有变化 · {preview.shared.join('、')}</p>}
          <RestoreReportList reports={preview.affectedReports} />
          {!!preview.affectedSettingUses?.length && (
            <section aria-label="原位恢复后待重核的设定正文使用">
              <h4>原位恢复后待重核的设定正文使用</h4>
              <ul>
                {preview.affectedSettingUses.map((proof) => (
                  <li key={proof.versionId}>
                    {proof.chapterTitle} · {proof.title} · 证明 <code>{proof.versionId}</code>
                  </li>
                ))}
              </ul>
            </section>
          )}
          {preview.blockers.map((blocker) => (
            <p className="notice" key={blocker}>
              {blocker}
            </p>
          ))}
          {writeBlocked && <p>{writeBlocked}</p>}
          {preview.notes.length > 0 && (
            <details>
              <summary>恢复范围说明</summary>
              <ul>
                {preview.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </details>
          )}
          <form
            onSubmit={(event) => {
              event.preventDefault()
              void perform(submit)
            }}
          >
            <fieldset disabled={busy || disabled || !!writeBlocked || preview.blockers.length > 0}>
              <legend>提交归档恢复</legend>
              <label className="check">
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                />
                我已核对恢复影响
              </label>
              <button className="primary" type="submit" disabled={!confirmed}>
                原位恢复为新修订
              </button>
            </fieldset>
          </form>
        </section>
      )}
    </div>
  )
}
