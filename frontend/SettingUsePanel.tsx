import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { components } from './api-schema'
import type { Selection } from './Editor'
import { digest } from './sourceCorrection'

type View = components['schemas']['SettingUseView']
type Proof = components['schemas']['SettingUseRecord']

const proofLabels: Record<Proof['status'], string> = {
  current: '当前正文证据',
  outdated: '待按当前正文或设定重新核对',
  superseded: '已由新证据接替',
  withdrawn: '已撤回',
  missing: '原稿来源待补齐',
}

export function SettingUsePanel({
  chapterId,
  revision,
  content,
  selection,
  saved,
}: {
  chapterId: string
  revision: number
  content: string
  selection: Selection
  saved: boolean
}) {
  const endpoint = `/chapters/${encodeURIComponent(chapterId)}/setting-uses`
  const [open, setOpen] = useState(false)
  const [view, setView] = useState<View>()
  const [itemId, setItemId] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const effect = useRef(0)
  const pending = useRef<{ input: string; id: string } | undefined>(undefined)
  const scope = useRef({ endpoint, revision })
  scope.current = { endpoint, revision }
  useEffect(() => {
    const attempt = ++effect.current
    if (!open) return
    void api<View>(endpoint).then(
      (result) => {
        if (attempt === effect.current) setView(result)
      },
      (failure: unknown) => {
        if (attempt === effect.current)
          setError(failure instanceof Error ? failure.message : '无法读取设定的正文证据。')
      },
    )
    return () => {
      effect.current++
    }
  }, [endpoint, open, reload, revision])

  const option = view?.options.find(({ item_id }) => item_id === itemId)
  const basis = view?.basis
  const currentFile = !!basis && basis.chapter_revision === revision
  const validSelection =
    saved &&
    !!selection.text.trim() &&
    selection.end > selection.start &&
    content.slice(selection.start, selection.end) === selection.text

  function requestKey(input: unknown) {
    const signature = JSON.stringify(input)
    if (signature !== pending.current?.input) pending.current = { input: signature, id: crypto.randomUUID() }
    return pending.current.id
  }

  async function perform(action: () => Promise<void>, success: string) {
    const target = scope.current
    const current = () =>
      target.endpoint === scope.current.endpoint && target.revision === scope.current.revision
    setBusy(true)
    setMessage('')
    setError('')
    try {
      await action()
      if (current()) {
        pending.current = undefined
        setMessage(success)
        setReload((value) => value + 1)
      }
    } catch (failure) {
      if (current()) setError(failure instanceof Error ? failure.message : '未能保存证据，请重试。')
    } finally {
      setBusy(false)
    }
  }

  function confirmSelection() {
    if (!basis || !option || !currentFile || !validSelection || busy) return
    void perform(async () => {
      const input = {
        outline_version_id: basis.outline_version_id,
        settings_version_id: basis.settings_version_id,
        item_id: option.item_id,
        parent_id: option.head_id,
        chapter_sha256: basis.chapter_sha256,
        evidence: {
          chapter_id: chapterId,
          revision_id: basis.chapter_revision_id,
          start: selection.start,
          end: selection.end,
          quote_sha256: await digest(selection.text),
        },
      }
      await api(endpoint, 'POST', { ...input, idempotency_key: requestKey(input) })
    }, '已确认当前正文使用该设定。')
  }

  function withdraw(id: string) {
    if (busy || !window.confirm('撤回这条正文使用证据？原稿和历史证据会保留。')) return
    void perform(async () => {
      await api(`${endpoint}/${encodeURIComponent(id)}/withdraw`, 'POST', {
        idempotency_key: requestKey(['withdraw', id]),
      })
    }, '已撤回正文使用证据。')
  }

  async function readMore() {
    const before = view?.next_before_id
    if (!before || busy) return
    setBusy(true)
    setError('')
    const attempt = effect.current
    try {
      const next = await api<View>(`${endpoint}?before_id=${encodeURIComponent(before)}`)
      if (attempt === effect.current)
        setView(
          (previous) =>
            previous && {
              ...previous,
              records: [...previous.records, ...next.records],
              next_before_id: next.next_before_id,
            },
        )
    } catch (failure) {
      if (attempt === effect.current)
        setError(failure instanceof Error ? failure.message : '无法读取更早证据。')
    } finally {
      setBusy(false)
    }
  }

  return (
    <details className="notice" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>设定的正文使用证据</summary>
      {error && (
        <div>
          <p role="alert">{error}</p>
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              setError('')
              setReload((value) => value + 1)
            }}
          >
            重新读取正文证据
          </button>
        </div>
      )}
      {message && <p role="status">{message}</p>}
      {!view && !error && <p role="status">正在读取本章的证据…</p>}
      {view && (
        <>
          {!!view.options.length && (
            <fieldset disabled={busy}>
              <legend>确认保存稿中的设定使用</legend>
              <label>
                本章引用的设定
                <select value={option?.item_id ?? ''} onChange={(event) => setItemId(event.target.value)}>
                  <option value="">请选择设定</option>
                  {view.options.map(({ item_id, title, category_title }) => (
                    <option value={item_id} key={item_id}>
                      {title} · {category_title}
                    </option>
                  ))}
                </select>
              </label>
              {!currentFile ? (
                <p>请重新打开本章，核对当前章稿和大纲。</p>
              ) : !validSelection ? (
                <p>先保存本章，再在编辑区选取对应原文。</p>
              ) : (
                <blockquote>{selection.text}</blockquote>
              )}
              <button
                type="button"
                disabled={!option || !currentFile || !validSelection}
                onClick={confirmSelection}
              >
                确认该选区为正文证据
              </button>
            </fieldset>
          )}
          {!!view.records.length ? (
            <ul>
              {view.records.map(({ version, status, quote }) => (
                <li key={version.id} style={{ overflowWrap: 'anywhere' }}>
                  <details>
                    <summary>
                      {view.options.find(({ item_id }) => item_id === version.item_id)?.title ??
                        version.item_id}{' '}
                      · {proofLabels[status]}
                    </summary>
                    <p>
                      设定采用版 {version.settings_version_id} · 原稿 {version.evidence.revision_id} ·{' '}
                      {version.evidence.start}–{version.evidence.end}
                    </p>
                    {quote !== null && <blockquote>{quote}</blockquote>}
                    {status === 'current' && (
                      <button type="button" disabled={busy} onClick={() => withdraw(version.id)}>
                        撤回这条正文证据
                      </button>
                    )}
                  </details>
                </li>
              ))}
            </ul>
          ) : (
            <p>本章暂无已确认的正文使用证据。</p>
          )}
          {view.next_before_id && (
            <button type="button" disabled={busy} onClick={() => void readMore()}>
              读取更早证据
            </button>
          )}
        </>
      )}
    </details>
  )
}
