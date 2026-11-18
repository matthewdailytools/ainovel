import { useEffect, useState } from 'react'
import { api } from './api'
import type { Notify } from './App'
import { CloudAttachments } from './CloudAttachments'
import { SyncScopePreview } from './SyncScopePreview'
import { describeProjectVersion, type ProjectVersion } from './projectSyncModel'
import type { ArchivedReferenceCard } from './referenceArchive'
import { referenceAssessmentLabel, referenceFieldLabels } from './ReferenceCardHistory'

type Conflict = { id: string; object_id: string }
type FormalSource = { id: string; device_id: string; change_id: string; sequence: number }
type SyncStatus = {
  enabled: boolean
  work_id: string
  cursor: number
  pending: number
  project_source?: FormalSource | null
  settings_source?: FormalSource | null
  outline_source?: FormalSource | null
  conflicts: Conflict[]
  deferred: { object_id: string; reason: string }[]
}
type Side = {
  kind?: string
  revision_id?: string
  head?: { content: string }
  novel?: { title: string; brief: string }
} | null
type StoryEvidence = { chapter_id: string; revision_id: string; start: number; end: number }
type StoryVersion = {
  record_id: string
  archived: boolean
  record: {
    kind: string
    title: string
    notes: string
    status?: string
    truth?: string
    stages?: { status: string; note: string }[]
  }
  evidence: { record: StoryEvidence[]; stages: StoryEvidence[][] }
  dependencies: Record<string, string>
}
type Comparison = {
  local_sha256: string
  local: Side
  project_versions?: Record<string, ProjectVersion>
  settings_versions?: Record<
    string,
    {
      categories: { title: string; skipped: boolean; items: { title: string; detail: string }[] }[]
      note: string
      project_version_id: string | null
    }
  >
  outline_versions?: Record<
    string,
    {
      plan: {
        summary: string
        volumes: {
          title: string
          summary: string
          units: { title: string; chapters: { goal: string; result: string }[] }[]
        }[]
      }
      settings_version_id: string | null
    }
  >
  story_versions?: Record<string, StoryVersion>
  reference_versions?: Record<string, ArchivedReferenceCard>
  remote: {
    head_revision: string
    parent_revision: string | null
    current: { payload: Side }
    incoming: { payload: Side }
    base?: { payload: Side } | null
  }
}

const storyKinds: Record<string, string> = {
  character: '人物',
  event: '事件',
  state: '状态',
  relationship: '关系',
  promise: '伏笔',
}
const storyStates: Record<string, string> = {
  planned: '计划',
  actual: '实际',
  unknown: '待核',
  conflict: '冲突',
  planted: '已埋设',
  reinforced: '已强化',
  resolved: '已回收',
  retained: '已保留',
  abandoned: '已放弃',
}

function readable(side: Side, comparison: Comparison) {
  if (!side) return '无此对象或已归档'
  if (side.kind === 'project-head' && side.revision_id) {
    const version = comparison.project_versions?.[side.revision_id]
    return version ? describeProjectVersion(version) : '尚未取得该版本的立项来源。'
  }
  if (side.kind === 'settings-head' && side.revision_id) {
    const version = comparison.settings_versions?.[side.revision_id]
    if (!version) return '尚未取得该版本的设定来源。'
    return [
      `立项来源 ${version.project_version_id ?? '无'}`,
      version.note,
      ...version.categories.flatMap((category) => [
        `${category.title}${category.skipped ? ' · 暂缓' : ''}`,
        ...category.items.map((item) => `${item.title} · ${item.detail}`),
      ]),
    ]
      .filter(Boolean)
      .join('\n')
  }
  if (side.kind === 'reference-card-head' && side.revision_id) {
    const version = comparison.reference_versions?.[side.revision_id]
    if (!version) return '尚未取得该版本的手法卡来源。'
    const { card, citation } = version
    return [
      `修订 ${version.revision} · ${referenceAssessmentLabel(version.assessment)}`,
      `版本 SHA-256 ${version.id}`,
      version.goal && `学习目标 ${version.goal}`,
      ...(card
        ? (['observation', 'interpretation', 'technique', 'conditions', 'exceptions'] as const).map(
            (field) => `${referenceFieldLabels[field]} · ${card[field]}`,
          )
        : []),
      version.missing_fields.length &&
        `待补 ${version.missing_fields.map((field) => referenceFieldLabels[field]).join('、')}`,
      citation && `资料《${citation.title}》· 单元 ${citation.unit_id}`,
      citation && `选段 UTF-16 ${citation.selection_start}–${citation.selection_end}`,
      citation &&
        (citation.evidence_start === null
          ? '原句在选段中有多个匹配位置'
          : `证据 UTF-16 ${citation.evidence_start}–${citation.evidence_end}`),
      citation && `证据 SHA-256 ${citation.evidence_sha256}`,
    ]
      .filter(Boolean)
      .join('\n')
  }
  if (side.kind === 'outline-head' && side.revision_id) {
    const version = comparison.outline_versions?.[side.revision_id]
    if (!version) return '尚未取得该版本的大纲来源。'
    return [
      `设定来源 ${version.settings_version_id ?? '无'}`,
      version.plan.summary,
      ...version.plan.volumes.flatMap((volume) => [
        `${volume.title} · ${volume.summary}`,
        ...volume.units.flatMap((unit) => [
          unit.title,
          ...unit.chapters.map((chapter) => `${chapter.goal} → ${chapter.result}`),
        ]),
      ]),
    ].join('\n')
  }
  if (side.kind === 'story-head' && side.revision_id) {
    const version = comparison.story_versions?.[side.revision_id]
    if (!version) return '尚未取得该版本的故事记录来源。'
    const { record, evidence } = version
    const status = record.status ?? record.truth
    const proof = ({ chapter_id, revision_id, start, end }: StoryEvidence) =>
      `章 ${chapter_id} · 原稿 ${revision_id} · 选区 ${start}–${end}`
    return [
      `${storyKinds[record.kind] ?? record.kind} · ${record.title}${version.archived ? ' · 已归档' : ''}`,
      `故事记录编号 ${version.record_id}`,
      status ? (storyStates[status] ?? status) : '',
      record.notes,
      ...evidence.record.map(proof),
      ...(record.stages ?? []).flatMap((stage, index) => [
        [storyStates[stage.status] ?? stage.status, stage.note].filter(Boolean).join(' · '),
        ...(evidence.stages[index] ?? []).map(proof),
      ]),
      ...Object.entries(version.dependencies).map(
        ([recordId, revisionId]) => `关联记录 ${recordId} · 版本 ${revisionId}`,
      ),
    ]
      .filter(Boolean)
      .join('\n')
  }
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
      <SyncScopePreview />
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
      {[
        { label: '立项', source: status?.project_source },
        { label: '设定', source: status?.settings_source },
        { label: '大纲', source: status?.outline_source },
      ].map(
        ({ label, source }) =>
          source && (
            <details key={label}>
              <summary>当前{label}的云端来源</summary>
              <p>
                版本 <code>{source.id}</code>
              </p>
              <p>
                设备 <code>{source.device_id}</code> · 回执 <code>{source.change_id}</code>
              </p>
            </details>
          ),
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
                  : readable(side, comparison.value)}
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
                  if (!window.confirm(`${label}并保存为解决后的版本？历史修订会保留。`)) return
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
