import { useEffect, useRef, useState } from 'react'
import { api, type Artifact } from './api'
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

function originalSettingsVersion(artifact: Artifact, branchId: string, confirmationId: string) {
  if (
    artifact.id !== confirmationId ||
    artifact.branch_id !== branchId ||
    artifact.kind !== 'conception_completion' ||
    artifact.status !== 'accepted'
  )
    throw new Error('找不到本工作版本的原设定确认。')
  const source = artifact.basis.sync_settings_source ?? artifact.basis.manuscript_settings_source
  if (!source) return artifact.id
  if (typeof source !== 'object' || !('revision_id' in source) || typeof source.revision_id !== 'string')
    throw new Error('无法核验原设定确认的同步来源。')
  return source.revision_id
}

function SettingUseProof({
  proof: { version, status, quote },
  title,
  busy = false,
  onWithdraw,
}: {
  proof: Proof
  title: string
  busy?: boolean
  onWithdraw?: (id: string) => void
}) {
  return (
    <details>
      <summary>
        {title} · {proofLabels[status]}
      </summary>
      <p>
        设定采用版 {version.settings_version_id} · 原稿 {version.evidence.revision_id} ·{' '}
        {version.evidence.start}–{version.evidence.end}
      </p>
      {quote !== null && <blockquote>{quote}</blockquote>}
      {status === 'current' && onWithdraw && (
        <button type="button" disabled={busy} onClick={() => onWithdraw(version.id)}>
          撤回这条正文证据
        </button>
      )}
    </details>
  )
}

type SettingReference = {
  branchId: string
  chapterId: string
  confirmationId: string
  itemId: string
  title: string
}

export function FrozenSettingReference(reference: SettingReference) {
  const [open, setOpen] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  return (
    <>
      <button type="button" ref={trigger} aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        查看原条目与正文证据
      </button>
      {open && (
        <SettingReferencePreview
          {...reference}
          onClose={() => {
            setOpen(false)
            trigger.current?.focus()
          }}
        />
      )}
    </>
  )
}

function SettingReferencePreview({
  branchId,
  chapterId,
  confirmationId,
  itemId,
  title,
  onClose,
}: SettingReference & { onClose: () => void }) {
  const endpoint = `/chapters/${encodeURIComponent(chapterId)}/setting-uses`
  const [basis, setBasis] = useState('')
  const [nextPage, setNextPage] = useState<string | null>(null)
  const [records, setRecords] = useState<Proof[]>([])
  const [read, setRead] = useState({ page: '', attempt: 0 })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => {
    heading.current?.focus()
  }, [])
  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    const path = read.page ? `${endpoint}?before_id=${encodeURIComponent(read.page)}` : endpoint
    void Promise.all([api<Artifact>(`/artifacts/${encodeURIComponent(confirmationId)}`), api<View>(path)])
      .then(([artifact, result]) => {
        if (!active) return
        if (result.chapter_id !== chapterId) throw new Error('正文证据与本章不一致。')
        const settingVersion = originalSettingsVersion(artifact, branchId, confirmationId)
        const exact = result.records.filter(
          ({ version }) =>
            version.evidence.chapter_id === chapterId &&
            version.item_id === itemId &&
            version.settings_version_id === settingVersion,
        )
        setBasis(settingVersion)
        setNextPage(result.next_before_id)
        setRecords((previous) => (read.page ? [...previous, ...exact] : exact))
      })
      .catch((failure: unknown) => {
        if (active) setError(failure instanceof Error ? failure.message : '无法读取此设定的正文证据。')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [branchId, chapterId, confirmationId, endpoint, itemId, read])

  return (
    <section aria-label="冻结设定的正文证据" style={{ overflowWrap: 'anywhere' }}>
      <h4 tabIndex={-1} ref={heading}>
        「{title}」的采用来源与正文证据
      </h4>
      <p>
        条目编号 <code>{itemId}</code> · 确认编号 <code>{confirmationId}</code>
      </p>
      {basis && basis !== confirmationId && (
        <p>
          交换来源版本 <code>{basis}</code>
        </p>
      )}
      {loading && <p role="status">正在核对原设定及本章证据…</p>}
      {error && <p role="alert">{error}</p>}
      {!loading && !error && !records.length && (
        <p>{nextPage ? '已读取范围内暂无这份确认的正文证据。' : '本章暂无这份确认的正文证据。'}</p>
      )}
      {!!records.length && (
        <ul>
          {records.map((proof) => (
            <li key={proof.version.id}>
              <SettingUseProof proof={proof} title={title} />
            </li>
          ))}
        </ul>
      )}
      {error && (
        <button
          type="button"
          onClick={() => setRead((current) => ({ ...current, attempt: current.attempt + 1 }))}
        >
          重新读取原设定与证据
        </button>
      )}
      {nextPage && (
        <button
          type="button"
          disabled={loading || !!error}
          onClick={() => setRead({ page: nextPage, attempt: 0 })}
        >
          查找更早的正文证据
        </button>
      )}
      <button type="button" onClick={onClose}>
        返回本章设定引用
      </button>
    </section>
  )
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
              {view.records.map((proof) => (
                <li key={proof.version.id} style={{ overflowWrap: 'anywhere' }}>
                  <SettingUseProof
                    proof={proof}
                    title={
                      view.options.find(({ item_id }) => item_id === proof.version.item_id)?.title ??
                      proof.version.item_id
                    }
                    busy={busy}
                    onWithdraw={withdraw}
                  />
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
