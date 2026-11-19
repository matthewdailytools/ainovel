import { useEffect, useState } from 'react'
import { CloudAttachments } from './CloudAttachments'
import { SyncScopePreview } from './SyncScopePreview'
import { useLeaveGuard } from './leaveGuard'
import type { LocalProject } from './offlineStore'
import { describeOutlineVersion } from './outlineSyncModel'
import { describeProjectVersion } from './projectSyncModel'
import { describeSettingsVersion, type SettingsConflictMerge } from './settingsSyncModel'
import { describeStoryVersion } from './storySyncModel'
import { describeReferenceVersion } from './referenceArchive'
import { describeFactSet, describeLengthTarget } from './restoreStateConflictText'
import { setSyncEnabled, type SyncPayload } from './syncClient'
import {
  cloudScope,
  forkWebConflict,
  httpSyncTransport,
  prepareWebResolution,
  prepareWebSettingsMerge,
  readWebManuscriptSyncState,
  resolveWebManuscript,
  syncWebManuscript,
  webForkChoices,
  type WebForkChoice,
} from './manuscriptSync'

type State = Awaited<ReturnType<typeof readWebManuscriptSyncState>> & {
  deferred?: { object_id: string; reason: string }[]
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
  if (value.kind === 'setting-use-head') {
    const source = comparison.settingUse?.versions.find(({ version }) => version.id === value.revision_id)
    if (!source) return '尚未取得该证据的原始修订。'
    const { version } = source
    return [
      version.action === 'withdrawn' ? '撤回正文使用证据' : '确认正文使用证据',
      `设定 ${version.item_id} · 采用版 ${version.settings_version_id}`,
      `正文 ${version.evidence.revision_id} · ${version.evidence.start}–${version.evidence.end}`,
    ].join('\n')
  }
  if (value.kind === 'fact-set-head' || value.kind === 'length-target-head') {
    const source = comparison.restoreStateSync.versions.find(
      ({ version }) => version.id === value.revision_id,
    )
    if (source?.kind === 'fact-set-version') return describeFactSet(source.version)
    if (source?.kind === 'length-target-version') return describeLengthTarget(source.version)
    return '等待所选事实或篇幅目标的准确修订。'
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
  onConflictDraftDirty,
  onForkSaved,
  onSettingsMerge,
}: {
  project: LocalProject
  unsaved: boolean
  onApplied: () => Promise<void>
  onBusy: (busy: boolean) => void
  onConflictDraftDirty: (dirty: boolean) => void
  onForkSaved: (project: LocalProject) => void
  onSettingsMerge: (merge: SettingsConflictMerge) => void
}) {
  const [service, setService] = useState('')
  const [device, setDevice] = useState('')
  const [token, setToken] = useState('')
  const [status, setStatus] = useState<State>()
  const [compared, setCompared] = useState<Comparison>()
  const [mergeText, setMergeText] = useState<string>()
  const [fork, setFork] = useState<{ choice: WebForkChoice; name: string }>()
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [attachmentBusy, setAttachmentBusy] = useState(false)
  const [message, setMessage] = useState('')
  const ready = !!service.trim() && !!device.trim()
  const hasConflictDraft = mergeText !== undefined || fork !== undefined
  useLeaveGuard(hasConflictDraft, '云同步冲突编辑')
  useEffect(() => {
    onConflictDraftDirty(hasConflictDraft)
    return () => onConflictDraftDirty(false)
  }, [hasConflictDraft, onConflictDraftDirty])
  useEffect(() => {
    let active = true
    setStatus(undefined)
    setCompared(undefined)
    if (ready) {
      try {
        void readWebManuscriptSyncState(cloudScope(service, project.branch.id, device.trim()))
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

  async function run(
    action: 'enable' | 'disable' | 'revoke' | 'sync' | 'compare' | 'choose' | 'merge' | 'fork' | 'settings',
    value?: string,
  ) {
    if (busy || !ready) return
    setBusy(true)
    onBusy(true)
    setMessage('')
    setNotice('')
    try {
      const scope = cloudScope(service, project.branch.id, device.trim())
      if (action === 'disable') {
        await setSyncEnabled(scope, false)
        setCompared(undefined)
        setStatus(await readWebManuscriptSyncState(scope))
        return
      }
      if (action === 'compare' && value) {
        setCompared(await prepareWebResolution(project, scope, value))
        return
      }
      if (action === 'settings' && compared) {
        if (unsaved) throw new Error('请先处理当前未确认的稿件或设定修改。')
        onSettingsMerge(await prepareWebSettingsMerge(project, scope, compared))
        return
      }
      if (action === 'fork' && compared && fork) {
        if (unsaved) throw new Error('请先保存正在编辑的稿件，再另存冲突版本。')
        const saved = await forkWebConflict(project, scope, compared, fork.choice, fork.name)
        setFork(undefined)
        onForkSaved(saved)
        setNotice(`已另存本机工作版本「${saved.novel.title}」，可从离线作品中打开。`)
        return
      }
      const transport = httpSyncTransport(scope, token.trim())
      if (action === 'revoke') {
        await transport.revokeDevice()
        await setSyncEnabled(scope, false)
        setToken('')
        setCompared(undefined)
        setStatus(await readWebManuscriptSyncState(scope))
        setNotice('已撤销此设备在云端的凭据。本机作品与待同步记录仍可使用。')
        return
      }
      if (action === 'enable') {
        await transport.enable()
        setStatus(await readWebManuscriptSyncState(scope))
        return
      }
      if (unsaved) throw new Error('请先保存当前章稿、立项或设定修改，再进行同步或合并。')
      const next =
        action === 'merge' && compared && mergeText !== undefined
          ? await resolveWebManuscript(project, scope, transport, compared, {
              choice: 'merge_prose',
              content: mergeText,
            })
          : action === 'choose' &&
              compared &&
              (value === 'local' || value === 'current' || value === 'incoming')
            ? await resolveWebManuscript(project, scope, transport, compared, value)
            : await syncWebManuscript(project, scope, transport)
      setStatus(next)
      if (
        compared?.incoming.conflict_id &&
        action === 'merge' &&
        next.conflicts.includes(compared.incoming.conflict_id)
      ) {
        setMessage('云端冲突仍待处理，合并输入已保留。')
        return
      }
      setCompared(undefined)
      setMergeText(undefined)
      await onApplied()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '同步未完成。')
      if (ready) {
        try {
          setStatus(await readWebManuscriptSyncState(cloudScope(service, project.branch.id, device.trim())))
        } catch {
          /*保留已显示状态*/
        }
      }
    } finally {
      setBusy(false)
      onBusy(false)
    }
  }
  const canMerge =
    compared?.object?.kind === 'chapter' &&
    compared.current.payload?.kind === 'chapter' &&
    compared.incoming.payload?.kind === 'chapter'
  const canMergeSettings =
    compared?.object?.kind === 'settings-head' &&
    compared.current.payload?.kind === 'settings-head' &&
    compared.incoming.payload?.kind === 'settings-head'
  const forkChoices = compared ? webForkChoices(compared) : []
  return (
    <details>
      <summary>作品云同步</summary>
      <SyncScopePreview title={project.novel.title} />
      <p>
        工作版本编号 <code>{project.branch.id}</code>
      </p>
      <fieldset disabled={busy || attachmentBusy || hasConflictDraft}>
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
        <button
          type="button"
          disabled={!ready || !token}
          onClick={() => {
            if (window.confirm('撤销此设备的云端凭据？再次同步需要新凭据。本机作品与待同步记录会保留。'))
              void run('revoke')
          }}
        >
          撤销此设备的云端凭据
        </button>
      </fieldset>
      {message && <p role="alert">{message}</p>}
      {notice && <p role="status">{notice}</p>}
      {status && (
        <p role="status">
          {status.enabled ? '已启用' : '本地模式'} · 游标 {status.cursor} · {status.pending.length}{' '}
          条待发送或确认
          {!!status.localPending && status.enabled && ` · ${status.localPending} 项正式保存仍待同步`}
        </p>
      )}
      {!!status?.receivedUnapplied && (
        <p role="status">已收到云端更新，{status.receivedUnapplied} 项待应用到本机。当前正式稿可继续查看。</p>
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
        <button
          key={id}
          type="button"
          title={id}
          disabled={busy || hasConflictDraft}
          onClick={() => void run('compare', id)}
        >
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
          {canMerge && !hasConflictDraft && (
            <button
              type="button"
              disabled={busy || !token || unsaved}
              onClick={() => {
                if (compared.object?.kind === 'chapter') setMergeText(compared.object.head.content)
              }}
            >
              手动合并正文
            </button>
          )}
          {canMergeSettings && !hasConflictDraft && (
            <button type="button" disabled={busy || unsaved} onClick={() => void run('settings')}>
              打开设定合写草稿
            </button>
          )}
          {!!forkChoices.length && !hasConflictDraft && (
            <button
              type="button"
              disabled={busy || unsaved}
              onClick={() => setFork({ choice: forkChoices[0].choice, name: '章稿冲突副本' })}
            >
              另存为本地工作版本
            </button>
          )}
          {fork && (
            <fieldset disabled={busy || unsaved}>
              <legend>另存冲突工作版本</legend>
              <p className="small">在此设备保存所选正文和源作品依据。原工作版本与云端冲突保留。</p>
              <label>
                另存章稿来源
                <select
                  value={fork.choice}
                  onChange={(event) => {
                    const entry = forkChoices.find(({ choice }) => choice === event.target.value)
                    if (entry) setFork({ ...fork, choice: entry.choice })
                  }}
                >
                  {forkChoices.map(({ choice, label }) => (
                    <option key={choice} value={choice}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                新工作版本名称
                <input
                  maxLength={400}
                  value={fork.name}
                  onChange={(event) => setFork({ ...fork, name: event.target.value })}
                />
              </label>
              <button
                type="button"
                disabled={!fork.name.trim() || Array.from(fork.name).length > 200}
                onClick={() => void run('fork')}
              >
                保存工作版本副本
              </button>
              <button type="button" onClick={() => setFork(undefined)}>
                取消另存
              </button>
            </fieldset>
          )}
          {canMerge && mergeText !== undefined && (
            <fieldset disabled={busy || !token || unsaved}>
              <legend>手动合并冲突章节</legend>
              <label>
                合并正文
                <textarea
                  rows={12}
                  maxLength={500_000}
                  value={mergeText}
                  onChange={(event) => setMergeText(event.target.value)}
                />
              </label>
              <button
                type="button"
                disabled={Array.from(mergeText).length > 250_000}
                onClick={() => {
                  if (window.confirm('提交合并后的正文为新修订？双方原稿均保留。')) void run('merge')
                }}
              >
                提交合并章稿
              </button>
              <button
                type="button"
                onClick={() => {
                  if (window.confirm('放弃当前合并输入？已保存稿仍可打开。')) setMergeText(undefined)
                }}
              >
                放弃合并输入
              </button>
            </fieldset>
          )}
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
              disabled={busy || !token || unsaved || hasConflictDraft}
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
                          : kind === 'setting-use-head'
                            ? '设定使用证据历史'
                            : kind === 'fact-set-head'
                              ? '正式事实历史'
                              : kind === 'length-target-head'
                                ? '篇幅目标历史'
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
        ledgerId={status?.ledgerId}
        inactive={!status?.enabled || busy || hasConflictDraft}
        onBusy={setAttachmentBusy}
      />
    </details>
  )
}
