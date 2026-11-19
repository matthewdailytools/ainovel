import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from './api'
import { useLeaveGuard } from './leaveGuard'

type ForkPreview = {
  basis_sha256: string
  chapter_count: number
  story_record_count: number
  scene_spans_for_review: number
  outline: 'confirmed' | 'draft' | 'none'
}

const outlineLabels = { confirmed: '正式大纲', draft: '待核大纲', none: '无大纲' }

export function PortableCheckpointFork({
  checkpointUrl,
  label,
  onRestored,
}: {
  checkpointUrl: string
  label: string
  onRestored?: (branchId: string) => void | Promise<void>
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
      setPreview(await api<ForkPreview>(`${checkpointUrl}/fork-preview`))
    } catch (caught) {
      setPreview(undefined)
      setError(caught instanceof Error ? caught.message : '无法核验恢复点的历史来源。')
    } finally {
      setBusy(false)
    }
  }

  async function fork() {
    if (!preview || !confirmed || !name.trim() || busy) return
    const request = { basis_sha256: preview.basis_sha256, branch_name: name.trim() }
    const signature = JSON.stringify(request)
    if (pending.current?.signature !== signature) pending.current = { signature, key: crypto.randomUUID() }
    setBusy(true)
    setError('')
    try {
      const receipt = await api<{ branch_id: string }>(`${checkpointUrl}/fork`, 'POST', {
        ...request,
        idempotency_key: pending.current.key,
      })
      if (!active.current) return
      setPreview(undefined)
      setConfirmed(false)
      setName('')
      setResult(`已从归档建立恢复分支「${request.branch_name}」。`)
      try {
        await onRestored?.(receipt.branch_id)
      } catch (caught) {
        setError(
          caught instanceof Error
            ? `分支已建立，刷新失败。${caught.message}`
            : '分支已建立，请从工作版本打开。',
        )
      }
    } catch (caught) {
      if (
        caught instanceof ApiError &&
        (caught.code === 'snapshot_changed' || caught.code === 'snapshot_source')
      ) {
        setConfirmed(false)
        setPreview(undefined)
      }
      setError(caught instanceof Error ? caught.message : '建立恢复分支失败。')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <button type="button" disabled={busy} onClick={() => void loadPreview()}>
        {preview ? '重新预览归档另存' : '预览归档另存'}
      </button>
      {busy && <p role="status">正在核对或保存恢复分支…</p>}
      {error && <p role="alert">{error}</p>}
      {result && <p role="status">{result}</p>}
      {preview && (
        <section aria-label="归档另存预览">
          <h4 tabIndex={-1} ref={previewHeading}>
            从「{label}」另存工作分支
          </h4>
          <p>新分支保留当时的全书上下文。归档和本机当前稿保留在来源工作版本。</p>
          <p>
            {preview.chapter_count} 章 · {preview.story_record_count} 条故事记录 ·{' '}
            {outlineLabels[preview.outline]}
          </p>
          {preview.scene_spans_for_review > 0 && (
            <p>{preview.scene_spans_for_review} 处场景绑定需在新分支重新核对。</p>
          )}
          <form
            onSubmit={(event) => {
              event.preventDefault()
              void fork()
            }}
          >
            <fieldset disabled={busy}>
              <legend>确认建立恢复分支</legend>
              <label>
                恢复分支名称
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
                建立恢复分支
              </button>
            </fieldset>
          </form>
        </section>
      )}
    </div>
  )
}
