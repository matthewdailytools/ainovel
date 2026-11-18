import { useEffect, useState } from 'react'
import { CloudAttachments } from './CloudAttachments'
import { SyncScopePreview } from './SyncScopePreview'
import type { LocalProject } from './offlineStore'
import { describeOutlineVersion } from './outlineSyncModel'
import { describeProjectVersion } from './projectSyncModel'
import { describeSettingsVersion } from './settingsSyncModel'
import { describeStoryVersion } from './storySyncModel'
import { describeReferenceVersion } from './referenceArchive'
import { readSyncState, setSyncEnabled, type SyncPayload } from './syncClient'
import {
  cloudScope,
  httpSyncTransport,
  prepareWebResolution,
  resolveWebManuscript,
  syncWebManuscript,
} from './manuscriptSync'

type State = Awaited<ReturnType<typeof readSyncState>> & {
  deferred?: { object_id: string; reason: string }[]
  localPending?: number
}
type Comparison = Awaited<ReturnType<typeof prepareWebResolution>>

function excerpt(value: SyncPayload | null, comparison: Comparison) {
  if (!value) return '无此对象或已归档'
  if (value.kind === 'project-head') {
    const source = comparison.versions.find(({ version }) => version.id === value.revision_id)
    return source ? describeProjectVersion(source.version) : '尚未取得该版本的立项来源。'
  }
  if (value.kind === 'settings-head') {
    const source = comparison.settings.versions.find(({ version }) => version.id === value.revision_id)
    return source ? describeSettingsVersion(source.version) : '尚未取得该版本的设定来源。'
  }
  if (value.kind === 'outline-head') {
    const source = comparison.outline.versions.find(({ version }) => version.id === value.revision_id)
    return source ? describeOutlineVersion(source.version) : '尚未取得该版本的大纲来源。'
  }
  if (value.kind === 'story-head') {
    const source = comparison.story.versions.find(
      ({ version }) => version.id === value.revision_id && version.record_id === value.record_id,
    )
    return source ? describeStoryVersion(source.version) : '尚未取得该版本的故事记录来源。'
  }
  if (value.kind === 'reference-card-head') {
    const source = comparison.reference?.versions.find(
      ({ id, session_id, card_id }) =>
        id === value.revision_id && session_id === value.session_id && card_id === value.card_id,
    )
    return source ? describeReferenceVersion(source) : '尚未取得该手法卡的源修订。'
  }
  const part = value.head ?? value.novel
  if (part && typeof part === 'object' && !Array.isArray(part)) {
    if (typeof part.content === 'string') return part.content
    if (typeof part.title === 'string') return part.title
  }
  return '无法预览此对象'
}

export function WebSyncPanel({
  project,
  unsaved,
  onApplied,
  onBusy,
}: {
  project: LocalProject
  unsaved: boolean
  onApplied: () => Promise<void>
  onBusy: (busy: boolean) => void
}) {
  const [service, setService] = useState('')
  const [device, setDevice] = useState('')
  const [token, setToken] = useState('')
  const [status, setStatus] = useState<State>()
  const [compared, setCompared] = useState<Comparison>()
  const [busy, setBusy] = useState(false)
  const [attachmentBusy, setAttachmentBusy] = useState(false)
  const [message, setMessage] = useState('')
  const ready = !!service.trim() && !!device.trim()
  useEffect(() => {
    let active = true
    setStatus(undefined)
    setCompared(undefined)
    if (ready) {
      try {
        void readSyncState(cloudScope(service, project.branch.id, device.trim()))
          .then((value) => {
            if (active) setStatus(value)
          })
          .catch((error) => {
            if (active) setMessage(String(error.message))
          })
      } catch (error) {
        setMessage(error instanceof Error ? error.message : '同步地址无效。')
      }
    }
    return () => {
      active = false
    }
  }, [project.branch.id, service, device, ready])

  async function run(action: 'enable' | 'disable' | 'sync' | 'compare' | 'choose', value?: string) {
    if (busy || !ready) return
    setBusy(true)
    onBusy(true)
    setMessage('')
    try {
      const scope = cloudScope(service, project.branch.id, device.trim())
      if (action === 'disable') {
        await setSyncEnabled(scope, false)
        setCompared(undefined)
        setStatus(await readSyncState(scope))
        return
      }
      if (action === 'compare' && value) {
        setCompared(await prepareWebResolution(project, scope, value))
        return
      }
      const transport = httpSyncTransport(scope, token.trim())
      if (action === 'enable') {
        await transport.pull(0)
        await setSyncEnabled(scope, true)
        setStatus(await readSyncState(scope))
        return
      }
      if (unsaved) throw new Error('请先保存当前章稿、立项或设定修改，再进行同步或合并。')
      const next =
        action === 'choose' && compared && (value === 'local' || value === 'current' || value === 'incoming')
          ? await resolveWebManuscript(project, scope, transport, compared, value)
          : await syncWebManuscript(project, scope, transport)
      setStatus(next)
      setCompared(undefined)
      await onApplied()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '同步未完成。')
      if (ready) {
        try {
          setStatus(await readSyncState(cloudScope(service, project.branch.id, device.trim())))
        } catch {
          /*保留已显示状态*/
        }
      }
    } finally {
      setBusy(false)
      onBusy(false)
    }
  }
  return (
    <details>
      <summary>作品云同步</summary>
      <SyncScopePreview title={project.novel.title} />
      <p>
        工作版本编号 <code>{project.branch.id}</code>
      </p>
      <fieldset disabled={busy || attachmentBusy}>
        <legend>设备连接</legend>
        <label>
          同步服务地址
          <input value={service} onChange={(event) => setService(event.target.value)} />
        </label>
        <label>
          设备编号
          <input value={device} onChange={(event) => setDevice(event.target.value)} />
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
          disabled={!ready || !token || status?.enabled}
          onClick={() => void run('enable')}
        >
          启用作品同步
        </button>
        <button
          type="button"
          disabled={!status?.enabled || !token || unsaved}
          onClick={() => void run('sync')}
        >
          同步当前工作版本
        </button>
        <button type="button" disabled={!status?.enabled} onClick={() => void run('disable')}>
          停用作品同步
        </button>
      </fieldset>
      {message && <p role="alert">{message}</p>}
      {status && (
        <p role="status">
          {status.enabled ? '已启用' : '本地模式'} · 游标 {status.cursor} · {status.pending.length}{' '}
          条待发送或确认
          {!!status.localPending && ` · ${status.localPending} 项正式保存仍待同步`}
        </p>
      )}
      {!!status?.deferred?.length && (
        <ul>
          {status.deferred.map(({ object_id, reason }) => (
            <li key={object_id}>
              {object_id} · {reason}
            </li>
          ))}
        </ul>
      )}
      {status?.conflicts.map((id) => (
        <button key={id} type="button" title={id} disabled={busy} onClick={() => void run('compare', id)}>
          比较冲突 {id.slice(0, 8)}
        </button>
      ))}
      {compared && (
        <section aria-label="云同步冲突比较">
          {(
            [
              ['本机当前版', compared.object],
              ['云端当前版', compared.current.payload],
              ['另一提交版', compared.incoming.payload],
              ['共同依据', compared.base?.payload ?? null],
            ] as const
          ).map(([label, side]) => (
            <details key={label}>
              <summary>{label}</summary>
              <pre>
                {label === '共同依据' && !compared.base
                  ? compared.incoming.parent_revision
                    ? '本机尚未取得共同基线修订。'
                    : '没有已记录的共同基线。'
                  : excerpt(side as SyncPayload | null, compared)}
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
              disabled={busy || !token || unsaved}
              onClick={() => {
                const kind = compared.current.payload?.kind
                const history =
                  kind === 'story-head'
                    ? '故事记录历史'
                    : kind === 'outline-head'
                      ? '大纲版本'
                      : kind === 'settings-head'
                        ? '设定版本'
                        : kind === 'project-head'
                          ? '立项版本'
                          : '原稿修订'
                if (window.confirm(`${label}并结束冲突？${history}保留。`)) void run('choose', choice)
              }}
            >
              {label}
            </button>
          ))}
        </section>
      )}
      <CloudAttachments
        key={service}
        workId={project.branch.id}
        service={service}
        token={token}
        inactive={!status?.enabled || busy}
        onBusy={setAttachmentBusy}
      />
    </details>
  )
}
