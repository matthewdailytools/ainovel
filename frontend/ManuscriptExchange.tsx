import { useState } from 'react'
import type { Notify } from './App'
import { api, apiFile } from './api'
import { canReadEvidence, referenceVersionLabel, useReferenceCardHistory } from './ReferenceCardHistory'
import { manuscriptBackupMediaType } from './webManuscriptBackup'

type DownloadablePackage = { reference_history?: { excerpts?: Record<string, string> } }
const jsonLimit = 24 * 1024 * 1024
const backupLimit = 128 * 1024 * 1024

function download(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function downloadPackage(data: unknown) {
  const file = new Blob([JSON.stringify(data)], { type: 'application/json' })
  if (file.size > jsonLimit) throw new Error('JSON 稿件包超过 24 MiB，请下载独立作品备份。')
  download('ainovel-manuscript.json', file)
}

function transferPath(branchId: string, suffix: 'package' | 'backup', selected: string[] = []) {
  const query = new URLSearchParams()
  for (const id of selected) query.append('include_reference_evidence_for', id)
  const path = `/branches/${encodeURIComponent(branchId)}/manuscript-${suffix}`
  return selected.length ? `${path}?${query}` : path
}

async function downloadBackup(branchId: string, selected: string[] = []) {
  const file = await apiFile(transferPath(branchId, 'backup', selected), manuscriptBackupMediaType)
  download('ainovel-独立作品备份.ainovel-backup', file)
}

function ReferenceEvidenceExport({ branchId, notify }: { branchId: string; notify: Notify }) {
  const [visible, setVisible] = useState(false)
  const [selected, setSelected] = useState<string[]>([])
  const [preview, setPreview] = useState<Record<string, string>>()
  const [busy, setBusy] = useState(false)
  const [downloaded, setDownloaded] = useState(false)
  const history = useReferenceCardHistory(branchId, visible)
  const rows = history.rows?.filter(canReadEvidence)

  function select(next: string[]) {
    setSelected(next)
    setPreview(undefined)
    setDownloaded(false)
  }

  async function run(action: () => Promise<void>) {
    setBusy(true)
    setDownloaded(false)
    try {
      await action()
    } catch (error) {
      notify(error)
    } finally {
      setBusy(false)
    }
  }

  async function prepare() {
    setPreview(undefined)
    await run(async () => {
      const chosen = await Promise.all(
        selected.map(async (id) => {
          const { excerpt } = await api<{ excerpt: string | null }>(
            `/branches/${encodeURIComponent(branchId)}/reference-cards/${encodeURIComponent(id)}/evidence`,
          )
          if (!excerpt) throw new Error('所选原句无法核对，请重新读取并选择。')
          return [id, excerpt] as const
        }),
      )
      setPreview(Object.fromEntries(chosen))
    })
  }

  return (
    <details onToggle={(event) => setVisible(event.currentTarget.open)}>
      <summary>选带指定手法卡原文</summary>
      <p>勾选要随这个本地文件导出的证据原句，可预览后更改选择。</p>
      {visible && !rows && !history.error && <p role="status">正在读取可选原句…</p>}
      {history.error && (
        <>
          <p role="alert">{history.error}</p>
          <button type="button" onClick={history.retry}>
            重试读取
          </button>
        </>
      )}
      {rows && !rows.length && <p>当前没有可以随稿件包选带的已保存原句。</p>}
      {!!rows?.length && (
        <>
          <fieldset disabled={busy}>
            <legend>要附带原句的手法卡修订</legend>
            {rows.map((row) => {
              const { id, revision, citation } = row.version
              return (
                <label className="check" key={id}>
                  <input
                    type="checkbox"
                    checked={selected.includes(id)}
                    disabled={selected.length >= 100 && !selected.includes(id)}
                    onChange={(event) =>
                      select(
                        event.target.checked ? [...selected, id] : selected.filter((value) => value !== id),
                      )
                    }
                  />
                  <span>
                    {referenceVersionLabel(row)} · 修订 {revision}
                    {citation ? ` · ${citation.title}` : ''}
                  </span>
                </label>
              )
            })}
          </fieldset>
          {selected.length >= 100 && <p role="status">每个稿件包最多选带 100 条原句。</p>}
          <button type="button" disabled={busy || !selected.length} onClick={() => void prepare()}>
            {busy ? '正在核对所选原句…' : '预览选带的原文'}
          </button>
          {!!selected.length && (
            <button type="button" disabled={busy} onClick={() => select([])}>
              清除原文选择
            </button>
          )}
        </>
      )}
      {preview && (
        <section aria-label="选带原文预览">
          <h3>将随文件导出的 {selected.length} 条原句</h3>
          {selected.map((id) => {
            const row = rows?.find((item) => item.version.id === id)
            return (
              <div className="fact" key={id}>
                <p>{row ? referenceVersionLabel(row) : id}</p>
                <blockquote>{preview[id]}</blockquote>
              </div>
            )
          })}
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                const data = await api<DownloadablePackage>(transferPath(branchId, 'package', selected))
                const chosen = data.reference_history?.excerpts
                if (
                  !chosen ||
                  Object.keys(chosen).length !== selected.length ||
                  selected.some((id) => chosen[id] !== preview[id])
                )
                  throw new Error('选带原句已变化，请重新预览。')
                downloadPackage(data)
                setDownloaded(true)
              })
            }
          >
            下载含所选原文的稿件包
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await downloadBackup(branchId, selected)
                setDownloaded(true)
              })
            }
          >
            下载含所选原文的独立备份
          </button>
        </section>
      )}
      {downloaded && <p role="status">已准备含所选原句的导出文件。</p>}
    </details>
  )
}

export function ManuscriptExchange({
  branchId,
  notify,
  onImported,
}: {
  branchId?: string
  notify: Notify
  onImported: (novelId: string) => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  async function run(action: () => Promise<void>) {
    if (busy) return
    setBusy(true)
    setMessage('')
    try {
      await action()
    } catch (error) {
      setMessage('')
      notify(error)
    } finally {
      setBusy(false)
    }
  }
  return (
    <details className="new-novel">
      <summary>离线写作与交换</summary>
      <p>
        作品文件保留章节、修订和手法卡引用地址。原句可按版本选带。JSON 稿件包上限 24 MiB，独立备份上限 128
        MiB。
      </p>
      <p>
        <a href="/offline/" target="_blank" rel="noreferrer">
          打开独立 Web 写作
        </a>
      </p>
      <button
        type="button"
        disabled={!branchId || busy}
        onClick={() =>
          void run(async () => {
            if (!branchId) return
            const data = await api(transferPath(branchId, 'package'))
            downloadPackage(data)
            setMessage('已准备稿件包。')
          })
        }
      >
        导出当前稿件包
      </button>
      <button
        type="button"
        disabled={!branchId || busy}
        onClick={() =>
          void run(async () => {
            if (!branchId) return
            setMessage('正在读取独立作品备份…')
            await downloadBackup(branchId)
            setMessage('已准备独立作品备份。')
          })
        }
      >
        下载独立作品备份
      </button>
      {branchId && <ReferenceEvidenceExport key={branchId} branchId={branchId} notify={notify} />}
      <label>
        导入稿件包
        <input
          type="file"
          accept=".json,application/json"
          disabled={busy}
          onChange={(event) => {
            const file = event.currentTarget.files?.[0]
            event.currentTarget.value = ''
            if (!file) return
            void run(async () => {
              if (file.size > jsonLimit) throw new Error('JSON 稿件包超过 24 MiB。')
              let data
              try {
                data = JSON.parse(await file.text())
              } catch {
                throw new Error('稿件包不是有效 JSON。')
              }
              const result = await api<{ id: string }>('/manuscript-packages/import', 'POST', data)
              await onImported(result.id)
              setMessage('已导入新作品。')
            })
          }}
        />
      </label>
      <label>
        导入独立作品备份
        <input
          type="file"
          accept={`.ainovel-backup,${manuscriptBackupMediaType}`}
          disabled={busy}
          onChange={(event) => {
            const file = event.currentTarget.files?.[0]
            event.currentTarget.value = ''
            if (!file) return
            void run(async () => {
              if (file.size > backupLimit) throw new Error('独立作品备份超过 128 MiB。')
              setMessage('正在核验独立作品备份…')
              const result = await api<{ id: string }>('/manuscript-backups/import', 'POST', file)
              await onImported(result.id)
              setMessage('已从独立备份导入新作品。')
            })
          }}
        />
      </label>
      {message && <p role="status">{message}</p>}
    </details>
  )
}
