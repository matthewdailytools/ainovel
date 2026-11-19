import { useEffect, useRef, useState } from 'react'
import { useLeaveGuard } from './leaveGuard'
import type { CheckpointForkPreview } from './offlineCheckpointFork'

type ForkPreview = CheckpointForkPreview & { basis: string }

const outlineLabels = { confirmed: '正式大纲', archived: '待核大纲', missing: '无大纲' }

export function PortableCheckpointFork<T>({
  label,
  mode = 'branch',
  disabled = false,
  previewFork,
  submitFork,
  onSaved,
}: {
  label: string
  mode?: 'branch' | 'copy'
  disabled?: boolean
  previewFork: () => Promise<ForkPreview>
  submitFork: (basis: string, name: string, requestId: string) => Promise<T>
  onSaved?: (receipt: T) => void | Promise<void>
}) {
  const [preview, setPreview] = useState<ForkPreview>()
  const [name, setName] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState('')
  const pending = useRef<{ signature: string; key: string }>(null)
  const previewHeading = useRef<HTMLHeadingElement>(null)
  const active = useRef(false)
  const copy = mode === 'copy'
  const destination = copy ? '本机作品副本' : '恢复分支'
  useLeaveGuard(!!name.trim() && !result, '未提交的归档另存操作')

  useEffect(() => {
    active.current = true
    return () => {
      active.current = false
    }
  }, [])
  useEffect(() => {
    if (preview) previewHeading.current?.focus()
  }, [preview])

  async function loadPreview() {
    setBusy(true)
    setError('')
    setResult('')
    setConfirmed(false)
    try {
      const next = await previewFork()
      if (active.current) setPreview(next)
    } catch (caught) {
      if (!active.current) return
      setPreview(undefined)
      setError(caught instanceof Error ? caught.message : '无法核验恢复点的历史来源。')
    } finally {
      if (active.current) setBusy(false)
    }
  }

  async function fork() {
    const title = name.trim()
    if (!preview || preview.blockers.length || !confirmed || !title || busy || disabled) return
    const signature = JSON.stringify([preview.basis, title])
    if (pending.current?.signature !== signature) pending.current = { signature, key: crypto.randomUUID() }
    setBusy(true)
    setError('')
    try {
      const receipt = await submitFork(preview.basis, title, pending.current.key)
      if (!active.current) return
      setPreview(undefined)
      setConfirmed(false)
      setName('')
      setResult(`已从归档建立${destination}「${title}」。`)
      try {
        await onSaved?.(receipt)
      } catch (caught) {
        if (active.current) setError(`${destination}已建立，请重新打开作品列表。`)
      }
    } catch (caught) {
      if (!active.current) return
      if (
        caught instanceof Error &&
        'code' in caught &&
        (caught.code === 'snapshot_changed' ||
          caught.code === 'snapshot_source' ||
          caught.code === 'conflict')
      ) {
        setConfirmed(false)
        setPreview(undefined)
      }
      setError(caught instanceof Error ? caught.message : `建立${destination}失败。`)
    } finally {
      if (active.current) setBusy(false)
    }
  }

  return (
    <div>
      <button type="button" disabled={busy || disabled} onClick={() => void loadPreview()}>
        {preview ? '重新预览归档另存' : '预览归档另存'}
      </button>
      {busy && <p role="status">正在核对或保存{destination}…</p>}
      {error && <p role="alert">{error}</p>}
      {result && <p role="status">{result}</p>}
      {preview && (
        <section aria-label="归档另存预览" style={{ overflowWrap: 'anywhere' }}>
          <h4 tabIndex={-1} ref={previewHeading}>
            从「{label}」另存{copy ? '本机作品副本' : '工作分支'}
          </h4>
          <p>
            {copy
              ? '本机作品副本保留检查点当时的全书上下文，可从离线作品列表打开。'
              : '新分支保留当时的全书上下文。归档和本机当前稿保留在来源工作版本。'}
          </p>
          <p>
            {preview.chapterCount} 章 · {preview.storyRecordCount} 条故事记录 ·{' '}
            {outlineLabels[preview.outline]}
          </p>
          {preview.sceneSpansForReview > 0 && (
            <p>
              {preview.sceneSpansForReview} 处场景绑定需在{destination}重新核对。
            </p>
          )}
          {preview.blockers.length > 0 ? (
            <ul aria-label="无法另存的原因">
              {preview.blockers.map((blocker) => (
                <li key={blocker}>{blocker}</li>
              ))}
            </ul>
          ) : (
            <form
              onSubmit={(event) => {
                event.preventDefault()
                void fork()
              }}
            >
              <fieldset disabled={busy || disabled}>
                <legend>确认建立{destination}</legend>
                <label>
                  {copy ? '本机作品副本名称' : '恢复分支名称'}
                  <input
                    maxLength={200}
                    value={name}
                    onChange={(event) => {
                      setName(event.target.value)
                      setConfirmed(false)
                    }}
                  />
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={confirmed}
                    onChange={(event) => setConfirmed(event.target.checked)}
                  />
                  我已核对另存范围
                </label>
                <button className="primary" type="submit" disabled={!confirmed || !name.trim()}>
                  建立{destination}
                </button>
              </fieldset>
            </form>
          )}
        </section>
      )}
    </div>
  )
}
