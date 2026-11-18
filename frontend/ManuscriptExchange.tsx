import { useState } from 'react'
import type { Notify } from './App'
import { api } from './api'
import { canReadEvidence, referenceVersionLabel, useReferenceCardHistory } from './ReferenceCardHistory'

type DownloadablePackage = { reference_history?: { excerpts?: Record<string, string> } }

function downloadPackage(data: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data)], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url
  link.download = 'ainovel-manuscript.json'
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function ReferenceEvidenceExport({ branchId, notify }: { branchId: string; notify: Notify }) {
  const [visible, setVisible] = useState(false)
  const [selected, setSelected] = useState<string[]>([])
  const [preview, setPreview] = useState<DownloadablePackage>()
  const [busy, setBusy] = useState(false)
  const [downloaded, setDownloaded] = useState(false)
  const history = useReferenceCardHistory(branchId, visible)
  const rows = history.rows?.filter(canReadEvidence)
  const excerpts = preview?.reference_history?.excerpts

  function select(next: string[]) {
    setSelected(next)
    setPreview(undefined)
    setDownloaded(false)
  }

  async function prepare() {
    setBusy(true)
    setPreview(undefined)
    setDownloaded(false)
    try {
      const query = new URLSearchParams()
      for (const id of selected) query.append('include_reference_evidence_for', id)
      const data = await api<DownloadablePackage>(`/branches/${branchId}/manuscript-package?${query}`)
      const chosen = data.reference_history?.excerpts
      if (!chosen || Object.keys(chosen).length !== selected.length || selected.some((id) => !chosen[id])) {
        throw new Error('稿件包未能核对全部已选原句，请重新读取并选择。')
      }
      setPreview(data)
    } catch (error) {
      notify(error)
    } finally {
      setBusy(false)
    }
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
      {preview && excerpts && (
        <section aria-label="选带原文预览">
          <h3>将随文件导出的 {selected.length} 条原句</h3>
          {selected.map((id) => {
            const row = rows?.find((item) => item.version.id === id)
            return (
              <div className="fact" key={id}>
                <p>{row ? referenceVersionLabel(row) : id}</p>
                <blockquote>{excerpts[id]}</blockquote>
              </div>
            )
          })}
          <button
            type="button"
            onClick={() => {
              downloadPackage(preview)
              setDownloaded(true)
            }}
          >
            下载含所选原文的稿件包
          </button>
        </section>
      )}
      {downloaded && <p role="status">已准备含所选原句的稿件包。</p>}
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
      notify(error)
    } finally {
      setBusy(false)
    }
  }
  return (
    <details className="new-novel">
      <summary>离线写作与交换</summary>
      <p>稿件包保留章节、修订和已保存手法卡的引用地址，默认不包含参考原文。完整本机归档可使用备份。</p>
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
            const data = await api(`/branches/${branchId}/manuscript-package`)
            downloadPackage(data)
            setMessage('已准备稿件包。')
          })
        }
      >
        导出当前稿件包
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
              if (file.size > 24 * 1024 * 1024) throw new Error('文件超过 24 MiB。')
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
      {message && <p role="status">{message}</p>}
    </details>
  )
}
