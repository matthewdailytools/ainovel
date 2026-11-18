import { useEffect, useState } from 'react'
import { api } from './api'
import type { Notify } from './App'
import { CloudAttachments } from './CloudAttachments'

type Conflict = { id: string; object_id: string }
type SyncStatus = {
  enabled: boolean
  work_id: string
  cursor: number
  pending: number
  conflicts: Conflict[]
  deferred: { object_id: string; reason: string }[]
}
type Side = { head?: { content: string }; novel?: { title: string; brief: string } } | null
type Comparison = {
  local_sha256: string
  local: Side
  remote: {
    head_revision: string
    parent_revision: string | null
    current: { payload: Side }
    incoming: { payload: Side }
    base?: { payload: Side } | null
  }
}

function readable(side: Side) {
  if (!side) return '无此对象或已归档'
  return side.head?.content ?? [side.novel?.title, side.novel?.brief].filter(Boolean).join('\n')
}

export function DesktopSyncPanel({
  branchId,
  notify,
  refresh,
}: {
  branchId: string
  notify: Notify
  refresh: () => Promise<unknown>
}) {
  const path = `/branches/${branchId}/desktop-sync`
  const [origin, setOrigin] = useState('')
  const [token, setToken] = useState('')
  const [status, setStatus] = useState<SyncStatus>()
  const [comparison, setComparison] = useState<{ id: string; value: Comparison; key: string }>()
  const [busy, setBusy] = useState(false)
  const [attachmentBusy, setAttachmentBusy] = useState(false)
  const credential = { origin: origin.trim(), token: token.trim() }
  const ready = !!credential.origin && !!credential.token
  useEffect(() => {
    let active = true
    api<SyncStatus>(path)
      .then((value) => {
        if (active) setStatus(value)
      })
      .catch((error) => {
        if (active) notify(error)
      })
    return () => {
      active = false
    }
  }, [path, notify])
  async function run(operation: () => Promise<void>) {
    if (busy) return
    setBusy(true)
    try {
      await operation()
    } catch (error) {
      notify(error)
      const latest = await api<SyncStatus>(path).catch(() => undefined)
      if (latest) setStatus(latest)
    } finally {
      setBusy(false)
    }
  }
  async function synchronize(action: 'enable' | 'disable' | 'run') {
    setComparison(undefined)
    setStatus(
      await api<SyncStatus>(path + '/' + action, 'POST', action === 'disable' ? undefined : credential),
    )
    if (action === 'run') await refresh()
  }
  return (
    <section className="card" aria-label="作品云同步">
      <h2>作品云同步</h2>
      <p>同步当前工作版本的目录、章稿、基本信息和随稿依据。启用前请确认云服务及作品范围。</p>
      <p>
        工作版本编号 <code>{branchId}</code>
      </p>
      <fieldset disabled={busy || attachmentBusy}>
        <legend>设备连接</legend>
        <label>
          同步服务地址
          <input value={origin} onChange={(event) => setOrigin(event.target.value)} />
        </label>
        <label>
          设备凭据
          <input
            type="password"
            autoComplete="off"
            value={token}
            onChange={(event) => setToken(event.target.value)}
          />
        </label>
        <button
          type="button"
          disabled={!status || !ready || status.enabled}
          onClick={() => void run(() => synchronize('enable'))}
        >
          启用作品同步
        </button>
        <button
          type="button"
          disabled={!status?.enabled || !ready}
          onClick={() => void run(() => synchronize('run'))}
        >
          同步当前工作版本
        </button>
        <button
          type="button"
          disabled={!status?.enabled}
          onClick={() => void run(() => synchronize('disable'))}
        >
          停用作品同步
        </button>
      </fieldset>
      {status && (
        <p role="status">
          {status.enabled ? '已启用' : '本地模式'} · 游标 {status.cursor} · {status.pending} 条待发送或确认
        </p>
      )}
      {!!status?.deferred.length && (
        <ul>
          {status.deferred.map((row) => (
            <li key={row.object_id}>
              {row.object_id} · {row.reason}
            </li>
          ))}
        </ul>
      )}
      {!!status?.conflicts.length && (
        <>
          <h3>待处理冲突</h3>
          {status.conflicts.map((row) => (
            <button
              key={row.id}
              type="button"
              disabled={busy || !ready || !status.enabled}
              onClick={() =>
                void run(async () => {
                  const value = await api<Comparison>(
                    `${path}/conflicts/${row.id}/compare`,
                    'POST',
                    credential,
                  )
                  setComparison({ id: row.id, value, key: crypto.randomUUID() })
                })
              }
            >
              比较 {row.object_id}
            </button>
          ))}
        </>
      )}
      <CloudAttachments
        key={credential.origin}
        workId={branchId}
        service={credential.origin}
        token={credential.token}
        inactive={!status?.enabled || busy}
        onBusy={setAttachmentBusy}
      />
      {comparison && (
        <section aria-label="云同步冲突比较">
          <h3>比较候选版本</h3>
          {(
            [
              ['本机当前版', comparison.value.local],
              ['云端当前版', comparison.value.remote.current.payload],
              ['另一提交版', comparison.value.remote.incoming.payload],
              ['共同依据', comparison.value.remote.base?.payload ?? null],
            ] as const
          ).map(([label, side]) => (
            <details key={label}>
              <summary>{label}</summary>
              <pre className="report">
                {label === '共同依据' && !comparison.value.remote.base
                  ? comparison.value.remote.parent_revision
                    ? '尚未取得共同基线修订。'
                    : '没有已记录的共同基线。'
                  : readable(side)}
              </pre>
            </details>
          ))}
          {(
            [
              ['local', '采用本机版'],
              ['current', '采用云端当前版'],
              ['incoming', '采用另一提交版'],
            ] as const
          ).map(([choice, label]) => (
            <button
              key={choice}
              type="button"
              disabled={busy || !ready}
              onClick={() =>
                void run(async () => {
                  if (!window.confirm(`${label}并保存为解决后的版本？原章稿修订保留。`)) return
                  setStatus(
                    await api<SyncStatus>(`${path}/conflicts/${comparison.id}/resolve`, 'POST', {
                      ...credential,
                      choice,
                      parent_revision: comparison.value.remote.head_revision,
                      local_sha256: comparison.value.local_sha256,
                      idempotency_key: comparison.key,
                    }),
                  )
                  setComparison(undefined)
                  await refresh()
                })
              }
            >
              {label}
            </button>
          ))}
        </section>
      )}
    </section>
  )
}
