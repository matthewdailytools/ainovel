import { useState } from 'react'
import type { Notify } from './App'
import { api } from './api'

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
      <p>稿件包保留章节、修订和正文校验值。完整本机归档可使用备份。</p>
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
            const url = URL.createObjectURL(new Blob([JSON.stringify(data)], { type: 'application/json' }))
            const link = document.createElement('a')
            link.href = url
            link.download = 'ainovel-manuscript.json'
            link.click()
            setTimeout(() => URL.revokeObjectURL(url), 1000)
            setMessage('已准备稿件包。')
          })
        }
      >
        导出当前稿件包
      </button>
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
