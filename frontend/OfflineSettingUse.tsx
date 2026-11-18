import { useEffect, useState } from 'react'
import {
  confirmLocalSettingUse,
  readLocalOutlineSettingUse,
  readLocalSettingUse,
  withdrawLocalSettingUse,
  type LocalRevision,
} from './offlineStore'
import { captureStoryEvidence } from './storySyncModel'
import type { SettingUseRecord, SettingUseStatus } from './settingUseSyncModel'

export type SavedSelection = { revisionId: string; start: number; end: number; text: string }
const labels: Record<SettingUseStatus, string> = {
  current: '当前正文证据',
  outdated: '待按当前正文或设定重新核对',
  superseded: '已由新证据接替',
  withdrawn: '已撤回',
  missing: '原稿来源待补齐',
}

function Proof({ proof }: { proof: SettingUseRecord }) {
  const { version, source_device } = proof.entry
  return (
    <>
      <p style={{ overflowWrap: 'anywhere' }}>
        大纲 {version.outline_version_id} · 设定采用版 {version.settings_version_id} · 原稿{' '}
        {version.evidence.revision_id} · {version.evidence.start}–{version.evidence.end}
      </p>
      {source_device && <p>来源设备 {source_device}</p>}
      {proof.quote !== null && <blockquote>{proof.quote}</blockquote>}
    </>
  )
}

export function OfflineSettingUse({
  projectId,
  saved,
  selection,
  dirty,
  disabled,
  refreshToken,
  onUpdated,
}: {
  projectId: string
  saved: LocalRevision
  selection?: SavedSelection
  dirty: boolean
  disabled: boolean
  refreshToken: number
  onUpdated: () => void
}) {
  const [open, setOpen] = useState(false)
  const [view, setView] = useState<Awaited<ReturnType<typeof readLocalSettingUse>>>()
  const [itemId, setItemId] = useState('')
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const [reload, refresh] = useState(0)
  const chapterId = saved.chapter_id
  useEffect(() => {
    if (!open) return
    let active = true
    void readLocalSettingUse(projectId, chapterId).then(
      (value) => {
        if (active) setView(value)
      },
      (failure: Error) => {
        if (active) setError(failure.message)
      },
    )
    return () => {
      active = false
    }
  }, [open, projectId, chapterId, saved.id, refreshToken, reload])

  const basis = view?.basis
  const option = view?.options.find(({ id }) => id === itemId)
  const canUseSelection =
    !!selection &&
    !dirty &&
    selection.revisionId === saved.id &&
    basis?.revision.id === saved.id &&
    selection.end > selection.start &&
    !!selection.text.trim() &&
    saved.content.slice(selection.start, selection.end) === selection.text

  async function perform(action: () => Promise<unknown>, message: string) {
    setBusy(true)
    setError('')
    setStatus('')
    try {
      await action()
      setStatus(message)
      onUpdated()
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '未能保存设定使用证据。')
      refresh((value) => value + 1)
    } finally {
      setBusy(false)
    }
  }

  function confirm() {
    if (!basis || !option || !selection || !canUseSelection || busy || disabled) return
    void perform(
      async () =>
        confirmLocalSettingUse(projectId, chapterId, {
          outline_version_id: basis.outlineVersionId,
          settings_version_id: basis.settingsVersionId,
          item_id: option.id,
          parent_id: option.headId,
          chapter_sha256: basis.revision.sha256,
          evidence: await captureStoryEvidence(saved, selection.start, selection.end),
        }),
      '已确认当前正文使用该设定。',
    )
  }

  function withdraw(id: string) {
    if (busy || disabled || !window.confirm('撤回这条正文使用证据？原稿和历史证据会保留。')) return
    void perform(() => withdrawLocalSettingUse(projectId, chapterId, id), '已撤回正文使用证据。')
  }

  return (
    <details aria-label="离线设定正文证据" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>设定的正文使用证据</summary>
      {error && <p role="alert">{error}</p>}
      {status && <p role="status">{status}</p>}
      {!view && !error && open && <p role="status">正在读取本章已保存的证据…</p>}
      {!!view?.options.length && (
        <fieldset disabled={busy || disabled}>
          <legend>确认保存稿中的设定使用</legend>
          <label>
            本章引用的设定
            <select value={option?.id ?? ''} onChange={(event) => setItemId(event.target.value)}>
              <option value="">请选择设定</option>
              {view.options.map(({ id, title, category }) => (
                <option key={id} value={id}>
                  {title} · {category}
                </option>
              ))}
            </select>
          </label>
          {canUseSelection ? (
            <blockquote>{selection.text}</blockquote>
          ) : (
            <p>请先保存本章，再在正文编辑区选择原句。</p>
          )}
          <button type="button" disabled={!option || !canUseSelection} onClick={confirm}>
            确认该选区为正文证据
          </button>
        </fieldset>
      )}
      {view && !view.options.length && <p>当前章稿和大纲暂无可确认的非世界设定引用。</p>}
      {!!view?.records.length && (
        <ul>
          {view.records.map((proof) => (
            <li key={proof.entry.version.id}>
              <details>
                <summary>
                  {proof.title} · {labels[proof.status]}
                </summary>
                <Proof proof={proof} />
                {proof.canWithdraw && (
                  <button
                    type="button"
                    disabled={busy || disabled}
                    onClick={() => withdraw(proof.entry.version.id)}
                  >
                    撤回这条正文证据
                  </button>
                )}
              </details>
            </li>
          ))}
        </ul>
      )}
    </details>
  )
}

export function OfflineOutlineSettingLedger({
  projectId,
  refreshToken,
}: {
  projectId: string
  refreshToken: number
}) {
  const [open, setOpen] = useState(false)
  const [rows, setRows] = useState<Awaited<ReturnType<typeof readLocalOutlineSettingUse>>>()
  const [error, setError] = useState('')
  useEffect(() => {
    if (!open) return
    let active = true
    void readLocalOutlineSettingUse(projectId).then(
      (value) => {
        if (active) {
          setRows(value)
          setError('')
        }
      },
      (failure: Error) => {
        if (active) setError(failure.message)
      },
    )
    return () => {
      active = false
    }
  }, [open, projectId, refreshToken])
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>非世界设定使用台账</summary>
      {error && <p role="alert">{error}</p>}
      {open && !rows && !error && <p role="status">正在读取当前大纲和正文证据…</p>}
      {rows?.length === 0 && <p>当前大纲暂无正式的非世界设定引用。</p>}
      {!!rows?.length && (
        <ul>
          {rows.map(({ chapterId, chapterTitle, itemId, title, proof }) => (
            <li key={`${chapterId}/${itemId}`}>
              {proof ? (
                <details>
                  <summary>
                    {chapterTitle} · {title} · {labels[proof.status]}
                  </summary>
                  <Proof proof={proof} />
                </details>
              ) : (
                <p>
                  {chapterTitle} · {title} · 仅计划采用
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </details>
  )
}
