import { useCallback, useEffect, useRef, useState } from 'react'
import { api, label, type Artifact, type Chapter, type Task } from './api'
import type { components } from './api-schema'
import type { Notify } from './App'
import { Difference } from './Editor'
import {
  characterReviewState,
  createExtractionRequestGuard,
  extractionSourcesStale,
  type CharacterReviewResult,
} from './characterSemanticReview'
import { characterRepairAccess, currentRepairTasks } from './characterFieldRepair'
import { CharacterFieldRepair } from './CharacterRepairDetails'

type Schemas = components['schemas']
type RecordPayload =
  Schemas['Character'] | Schemas['Event'] | Schemas['State'] | Schemas['Promise'] | Schemas['Relationship']
type Proposal = {
  id: string
  local_id: string
  record: RecordPayload
  reasoning: string
  target_id?: string | null
  target_revision?: number | null
  update_fields?: string[]
  previous_record?: { revision: number; record: RecordPayload }
}
type ExtractionPayload = {
  scenes?: { id: string; chapter_id: string; scene: Schemas['SceneTime']; quote?: string }[]
  scene_required?: boolean
  scene_replacements?: { id: string; revision: number; chapter_id: string; payload: Schemas['SceneTime'] }[]
  records: Proposal[]
  unknowns: string[]
  coverage: string
  source_story_revision: number
  source_manifest: { id: string; title: string; revision: number }[]
}
type ExtractionArtifact = Artifact & { payload: Artifact['payload'] & ExtractionPayload }
type History = {
  tasks: { id: string; status: string; error: string; created: number }[]
  artifacts: { id: string; status: string; created: number }[]
}
const kinds = { character: '人物', event: '事件', state: '时点状态', promise: '伏笔', relationship: '关系' }
const fieldNames: Record<string, string> = {
  title: '名称',
  aliases: '别名',
  profile: '档案',
  notes: '说明',
  volume: '卷别',
  sources: '原文来源',
  depends_on: '依赖',
  thread: '线别',
  story_order: '故事顺序',
  story_time: '故事时间',
  status: '发生状态',
  participants: '参与人物',
  after: '先行事件',
  character_id: '所属人物',
  event_id: '变化事件',
  category: '状态类别',
  key: '状态事项',
  value: '状态值',
  audience: '信息视角',
  truth: '事实性质',
  until_event_id: '失效事件',
  stages: '伏笔阶段',
  from_id: '关系起点',
  to_id: '关系终点',
  relation: '关系描述',
}
function describe(value: unknown, nameFor: (id: string) => string): string {
  if (value === null || value === undefined) return '未指定'
  if (Array.isArray(value)) return value.map((v) => describe(v, nameFor)).join('\n')
  if (typeof value === 'object') {
    if ('quote' in value)
      return `原文：${String(value.quote)}${'chapter_id' in value ? ` · 章节${String(value.chapter_id)}` : ''}${'revision' in value ? ` · 修订${String(value.revision)}` : ''}`
    if ('status' in value)
      return `阶段：${String(value.status)}${'note' in value ? ` · ${String(value.note)}` : ''}${'event_id' in value && value.event_id ? ` · 事件${nameFor(String(value.event_id))}` : ''}`
    return '请结合下方关联与原文核对'
  }
  return typeof value === 'string' ? nameFor(value) : String(value)
}

const reviewResults: Record<CharacterReviewResult, string> = {
  supported: '来源支持',
  unknown: '依据不足，待核对',
  contradicted: '与来源矛盾，待核对',
}

export function CharacterSemanticReview({
  value,
  candidateRevision,
  sourceStale,
  nameFor,
  chapterNameFor,
}: {
  value: unknown
  candidateRevision: number
  sourceStale: boolean
  nameFor: (id: string) => string
  chapterNameFor: (id: string) => string
}) {
  const { report, state } = characterReviewState(value, candidateRevision, sourceStale)
  const states = {
    missing: '尚无人物字段复核报告，不能视为来源支持；人工整理请直接核对原文，自动批次完成复核后可刷新查看。',
    invalid: '人物字段复核报告格式无法核对，请刷新重试；不能视为来源支持。',
    legacy: '旧版历史报告：复核规则已变化，仅供查看，不作为当前人物字段复核的有效支持，需重新核对。',
    historical: '历史报告已失效：报告与当前候选修订不符，需重新核对。',
    stale: '来源正文或故事已变化或待复检，需重新核对；下方仅为历史报告。',
    needs_review: '人物字段待核对：仍有依据不足、矛盾或报告待核对项。',
    supported: '模型复核：本报告的人物字段均有来源支持。',
    not_required: '本报告没有需复核的人物说明或档案字段。',
  }
  return (
    <section aria-label="人物字段来源复核" style={{ overflowWrap: 'anywhere' }}>
      <h3>人物字段来源复核</h3>
      <p role="status">{states[state]}</p>
      <p>模型辅助核对新增或实际变化的人物说明、档案，不等于作者认可；作者仍可明确选择采用。</p>
      {sourceStale && state !== 'stale' && <p>来源正文或故事已变化或待复检，需重新核对。</p>}
      {report && (
        <details>
          <summary>查看字段结论、理由与来源（{report.results.length}项）</summary>
          <p>
            报告候选修订{report.candidate_revision}；当前候选修订{candidateRevision}。
          </p>
          <p>
            模型：{report.reviewer.provider || '未记录服务商'} / {report.reviewer.model || '未记录模型'}；
            账户：{report.reviewer.account || '未记录'}；版本：{report.version}；
            {new Date(report.created * 1000).toLocaleString()}。
          </p>
          {report.results.map((item, index) => (
            <article className="fact" key={`${item.record_id}/${item.field}/${index}`}>
              <h4>
                {nameFor(item.record_id)} · {fieldNames[item.field]}：{reviewResults[item.result]}
              </h4>
              <p>{item.reason || '报告未提供理由，请结合原文核对。'}</p>
              {!item.evidence.length && <p>未提供原文证据。</p>}
              {item.evidence.map((source, i) => (
                <div key={i}>
                  <p>
                    {chapterNameFor(source.chapter_id)} · 保存稿修订{source.revision} · 位置{source.start}—
                    {source.end}
                  </p>
                  <blockquote>{source.quote}</blockquote>
                </div>
              ))}
            </article>
          ))}
        </details>
      )}
    </section>
  )
}

export function StoryExtraction({
  branchId,
  storyRevision,
  chapters,
  account,
  modelReady,
  notify,
  onAdopted,
  recordNames,
}: {
  branchId: string
  storyRevision: number
  chapters: Chapter[]
  account: string
  modelReady: boolean
  notify: Notify
  onAdopted: () => Promise<void>
  recordNames: Record<string, string>
}) {
  const [chosenChapters, setChosenChapters] = useState<string[]>([])
  const [history, setHistory] = useState<History>({ tasks: [], artifacts: [] })
  const [artifactSelection, setArtifactSelection] = useState({ branchId, id: '' })
  const artifactId = artifactSelection.branchId === branchId ? artifactSelection.id : ''
  const [loadedArtifact, setLoadedArtifact] = useState<{ branchId: string; value: ExtractionArtifact }>()
  const [selected, setSelected] = useState<string[]>([])
  const [includeScenes, setIncludeScenes] = useState(false)
  const [selectedScenes, setSelectedScenes] = useState<string[]>([])
  const [confirmReplacement, setConfirmReplacement] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [loadingArtifact, setLoadingArtifact] = useState(false)
  const [artifactError, setArtifactError] = useState('')
  const [repairContext, setRepairContext] = useState<{
    branchId: string
    artifactId: string
    tasks: Task[]
    loaded: boolean
    error: string
  }>()
  const pending = useRef<{ signature: string; key: string } | null>(null)
  const alive = useRef(true)
  const artifactRequests = useRef(createExtractionRequestGuard())
  const repairRequests = useRef(createExtractionRequestGuard())
  const currentTarget = useRef({ branchId, artifactId })
  currentTarget.current = { branchId, artifactId }
  const artifact =
    loadedArtifact?.branchId === branchId && loadedArtifact.value.id === artifactId
      ? loadedArtifact.value
      : undefined
  const refreshRepairContext = useCallback(
    async (candidate: ExtractionArtifact) => {
      const isLatest = repairRequests.current.begin()
      try {
        const tasks = await currentRepairTasks(
          candidate,
          branchId,
          await api<Task[]>(`/tasks?branch_id=${encodeURIComponent(branchId)}`),
          (id) => api<Task>(`/tasks/${encodeURIComponent(id)}`),
        )
        if (
          alive.current &&
          isLatest() &&
          currentTarget.current.branchId === branchId &&
          currentTarget.current.artifactId === candidate.id
        )
          setRepairContext({ branchId, artifactId: candidate.id, tasks, loaded: true, error: '' })
        return true
      } catch (e) {
        if (
          alive.current &&
          isLatest() &&
          currentTarget.current.branchId === branchId &&
          currentTarget.current.artifactId === candidate.id
        )
          setRepairContext({
            branchId,
            artifactId: candidate.id,
            tasks: [],
            loaded: false,
            error: e instanceof Error ? e.message : '修订链读取失败，请刷新核对。',
          })
        return false
      }
    },
    [branchId],
  )
  useEffect(() => {
    alive.current = true
    let active = true
    const load = async () => {
      try {
        const h = await api<History>(`/branches/${branchId}/story-extractions`)
        if (active) {
          setHistory(h)
          setArtifactSelection((selection) => ({
            branchId,
            id: (selection.branchId === branchId && selection.id) || h.artifacts[0]?.id || '',
          }))
        }
      } catch (e) {
        if (active) notify(e)
      }
    }
    void load()
    const timer = setInterval(() => void load(), 2000)
    return () => {
      active = false
      alive.current = false
      clearInterval(timer)
    }
  }, [branchId, notify])
  useEffect(() => {
    let active = true
    const isLatest = artifactRequests.current.begin()
    setLoadedArtifact(undefined)
    setSelected([])
    setSelectedScenes([])
    setConfirmReplacement(false)
    setArtifactError('')
    setLoadingArtifact(!!artifactId)
    const isCurrent = () =>
      active &&
      isLatest() &&
      currentTarget.current.branchId === branchId &&
      currentTarget.current.artifactId === artifactId
    if (artifactId)
      void api<ExtractionArtifact>(`/artifacts/${encodeURIComponent(artifactId)}`)
        .then((a) => {
          if (a.branch_id !== branchId) throw new Error('候选不属于当前工作版本。')
          if (isCurrent()) setLoadedArtifact({ branchId, value: a })
        })
        .catch((e: unknown) => {
          if (isCurrent()) {
            setArtifactError('候选读取失败，请刷新重试。')
            notify(e)
          }
        })
        .finally(() => {
          if (isCurrent()) setLoadingArtifact(false)
        })
    return () => {
      active = false
      artifactRequests.current.invalidate()
    }
  }, [artifactId, branchId, notify])
  useEffect(() => {
    if (!artifact) return
    void refreshRepairContext(artifact)
    const timer = setInterval(() => void refreshRepairContext(artifact), 2000)
    return () => {
      clearInterval(timer)
      repairRequests.current.invalidate()
    }
  }, [artifact, refreshRepairContext])
  async function refreshArtifact() {
    const isLatest = artifactRequests.current.begin()
    const isCurrent = () =>
      alive.current &&
      isLatest() &&
      currentTarget.current.branchId === branchId &&
      currentTarget.current.artifactId === artifactId
    setLoadingArtifact(true)
    setArtifactError('')
    try {
      const next = await api<ExtractionArtifact>(`/artifacts/${encodeURIComponent(artifactId)}`)
      if (next.branch_id !== branchId) throw new Error('候选不属于当前工作版本。')
      if (isCurrent()) {
        setLoadedArtifact({ branchId, value: next })
        const repaired = await refreshRepairContext(next)
        if (isCurrent())
          setMessage(
            repaired
              ? '候选与复核报告已刷新，当前选择和输入保留，请核对最新内容。'
              : '候选已读取，修订链尚未核对；选择和输入保留，请刷新重试。',
          )
      }
    } catch (e) {
      if (isCurrent()) {
        setArtifactError('刷新失败，已保留候选、选择和输入，请重试。')
        notify(e)
      }
    } finally {
      if (isCurrent()) setLoadingArtifact(false)
    }
  }
  async function perform(fn: () => Promise<void>) {
    if (busy) return
    setBusy(true)
    try {
      await fn()
    } catch (e) {
      notify(e)
      if (alive.current) setMessage(e instanceof Error ? e.message : '操作失败，选择保留。')
    } finally {
      if (alive.current) setBusy(false)
    }
  }
  const running = history.tasks.filter((t) => ['queued', 'running'].includes(t.status))
  const currentRepairContext =
    repairContext?.branchId === branchId && repairContext.artifactId === artifactId
      ? repairContext
      : undefined
  const repairAccess = artifact
    ? characterRepairAccess(
        artifact,
        branchId,
        currentRepairContext?.tasks || [],
        !!currentRepairContext?.loaded,
        Object.fromEntries(history.artifacts.map((a) => [a.id, a.status])),
      )
    : undefined
  const repairReadOnly = !repairAccess || repairAccess.readOnly
  const stale = artifact && artifact.payload.source_story_revision !== storyRevision
  const reviewSourceStale =
    !!stale || !!(artifact && extractionSourcesStale(artifact.payload.source_manifest, chapters))
  const nameFor = (id: string | null | undefined) =>
    id
      ? artifact?.payload.records.find((r) => r.id === id)?.record.title || recordNames[id] || id
      : '未知／未指定'
  const translated: Record<string, string> = {
    actual: '实际',
    planned: '计划',
    unknown: '未知',
    conflict: '冲突',
    character: '人物掌握',
    author: '作者掌握',
    reader: '读者已知',
    knowledge: '认知',
    capability: '能力',
    resource: '资源',
    injury: '伤势',
    other: '其他',
    planted: '埋设',
    reinforced: '强化',
    resolved: '回收',
    retained: '保留',
    abandoned: '放弃',
  }
  return (
    <details>
      <summary>按保存稿整理人物、事件、状态和伏笔候选</summary>
      <section aria-label="结构化整理与确认">
        <p>
          只发送本批所选保存稿及当前记录索引；模型推导保留为候选。请选择原文范围，完成后逐项核对来源与未知，再明确采用。单批最多20章、5万字符，超过时分批整理。
        </p>
        <p role="status">{message}</p>
        <fieldset>
          <legend>本次整理的保存稿</legend>
          {chapters.map((c) => (
            <label className="check" key={c.id}>
              <input
                type="checkbox"
                disabled={busy}
                checked={chosenChapters.includes(c.id)}
                onChange={(e) =>
                  setChosenChapters((old) =>
                    e.target.checked ? [...old, c.id] : old.filter((id) => id !== c.id),
                  )
                }
              />
              {c.position} · {c.title}
              {c.stale ? '（待复检）' : ''}
            </label>
          ))}
        </fieldset>
        <label className="check">
          <input
            type="checkbox"
            checked={includeScenes}
            disabled={busy}
            onChange={(e) => setIncludeScenes(e.target.checked)}
          />
          同时提议新正文的场景范围
        </label>
        <button
          disabled={
            busy || !modelReady || !chosenChapters.length || chosenChapters.length > 20 || running.length > 0
          }
          onClick={() =>
            void perform(async () => {
              const body = {
                base_story_revision: storyRevision,
                chapter_ids: chosenChapters,
                account,
                include_scene_ranges: includeScenes,
              }
              const signature = JSON.stringify(body)
              if (pending.current?.signature !== signature)
                pending.current = { signature, key: crypto.randomUUID() }
              await api(`/branches/${branchId}/story-extractions`, 'POST', {
                ...body,
                client_request_id: pending.current.key,
              })
              pending.current = null
              setHistory(await api(`/branches/${branchId}/story-extractions`))
              setMessage('整理任务已提交，正文和正式故事记录尚未改变。')
            })
          }
        >
          从所选保存稿生成结构候选
        </button>
        {!modelReady && <p>未配置可用模型时仍可手工维护长篇记录。</p>}
        {running.map((t) => (
          <div className="notice" key={t.id}>
            <p>{label(t.status)} · 关闭页面不取消任务，重新打开可查看。</p>
            <button
              disabled={busy}
              onClick={() =>
                void perform(async () => {
                  await api(`/tasks/${t.id}/cancel`, 'POST')
                  setHistory(await api(`/branches/${branchId}/story-extractions`))
                })
              }
            >
              取消本次结构整理
            </button>
          </div>
        ))}
        <label>
          保存的结构候选
          <select
            value={artifactId}
            disabled={busy}
            onChange={(e) => setArtifactSelection({ branchId, id: e.target.value })}
          >
            <option value="">请选择</option>
            {artifactId && !history.artifacts.some((a) => a.id === artifactId) && (
              <option value={artifactId}>修订链中的候选 · {artifactId.slice(0, 8)}</option>
            )}
            {history.artifacts.map((a) => (
              <option key={a.id} value={a.id}>
                {new Date(a.created * 1000).toLocaleString()} · {label(a.status)} · {a.id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>
        <button
          disabled={busy || loadingArtifact || !artifactId}
          onClick={() => void perform(refreshArtifact)}
        >
          {loadingArtifact ? '正在读取候选与复核报告…' : '刷新候选与复核报告'}
        </button>
        <p role="status">{artifactError}</p>
        {artifact && (
          <>
            <p>
              {artifact.payload.coverage} · {label(artifact.status)}
            </p>
            {(currentRepairContext?.error ||
              (!artifact.basis.character_field_repair && !repairAccess?.rootId && repairAccess?.reason)) && (
              <p role="status">{currentRepairContext?.error || repairAccess?.reason}</p>
            )}
            {repairAccess && (
              <CharacterFieldRepair
                value={artifact.basis.character_field_repair}
                access={repairAccess}
                currentId={artifact.id}
                busy={busy || loadingArtifact}
                nameFor={nameFor}
                onSelect={(id) => setArtifactSelection({ branchId, id })}
              />
            )}
            <p>
              来源故事修订{artifact.payload.source_story_revision}；当前{storyRevision}。
              {stale ? '源稿或故事已变更，不能直接采用旧候选。' : ''}
            </p>
            <details>
              <summary>来源章节与修订</summary>
              {artifact.payload.source_manifest.map((c) => (
                <p key={c.id}>
                  {c.title} · 修订{c.revision}
                </p>
              ))}
            </details>
            <CharacterSemanticReview
              value={artifact.payload.character_semantic_review}
              candidateRevision={artifact.candidate_revision}
              sourceStale={reviewSourceStale}
              nameFor={nameFor}
              chapterNameFor={(id) =>
                artifact.payload.source_manifest.find((chapter) => chapter.id === id)?.title || id
              }
            />
            {artifact.payload.records.map((p) => (
              <article className="fact" key={p.id}>
                <label className="check">
                  <input
                    type="checkbox"
                    disabled={busy || repairReadOnly || artifact.status !== 'candidate' || !!stale}
                    checked={selected.includes(p.id)}
                    onChange={(e) =>
                      setSelected((old) =>
                        e.target.checked ? [...old, p.id] : old.filter((id) => id !== p.id),
                      )
                    }
                  />
                  {p.target_id ? '更新' : '采用'}
                  {kinds[p.record.kind]}：{p.record.title}
                </label>
                <p>模型推导：{p.reasoning}</p>
                {p.previous_record && (
                  <details>
                    <summary>对照正式记录修订{p.previous_record.revision}与更新提案</summary>
                    <p>
                      仅明确列出的字段会变化，其他字段保留。原文默认追加新证据；明确列出“原文来源”时为替换。
                    </p>
                    {[...new Set([...(p.update_fields || []), 'sources'])].map((field) => (
                      <section key={field}>
                        <h4>
                          {fieldNames[field] || field}
                          {field === 'sources' && !p.update_fields?.includes('sources') ? '（追加证据）' : ''}
                        </h4>
                        <Difference
                          before={describe(
                            Object.entries(p.previous_record!.record).find(([key]) => key === field)?.[1],
                            nameFor,
                          )}
                          after={describe(
                            Object.entries(p.record).find(([key]) => key === field)?.[1],
                            nameFor,
                          )}
                          beforeLabel="正式旧记录"
                        />
                      </section>
                    ))}
                  </details>
                )}
                <details>
                  <summary>核对具体内容、关联和原文</summary>
                  <p>{p.record.notes}</p>
                  {p.record.kind === 'character' && (
                    <p>
                      别名：{p.record.aliases?.join('、')}；档案：{p.record.profile}
                    </p>
                  )}
                  {p.record.kind === 'event' && (
                    <p>
                      故事序：{p.record.story_order ?? '未知'}；时间：{p.record.story_time}；性质：
                      {translated[p.record.status || 'unknown']}；人物：
                      {p.record.participants?.map(nameFor).join('、')}
                    </p>
                  )}
                  {p.record.kind === 'state' && (
                    <p>
                      人物：{nameFor(p.record.character_id)}；变化事件：{nameFor(p.record.event_id)}； 事项：
                      {p.record.key}；值：{p.record.value}；类别：{translated[p.record.category]}；信息视角：
                      {translated[p.record.audience]}；性质：{translated[p.record.truth || 'unknown']}
                      ；失效事件：{nameFor(p.record.until_event_id)}
                    </p>
                  )}
                  {p.record.kind === 'relationship' && (
                    <p>
                      {nameFor(p.record.from_id)} → {nameFor(p.record.to_id)}：{p.record.relation}；
                      {translated[p.record.truth || 'unknown']}；形成事件：{nameFor(p.record.event_id)}
                    </p>
                  )}
                  {p.record.kind === 'promise' &&
                    p.record.stages.map((s, i) => (
                      <div key={i}>
                        <p>
                          阶段：{translated[s.status]}；事件：{nameFor(s.event_id)}；{s.note}
                        </p>
                        {s.sources?.map((source, j) => (
                          <blockquote key={j}>{source.quote}</blockquote>
                        ))}
                      </div>
                    ))}
                  {p.record.sources?.map((source, i) => (
                    <div key={i}>
                      <blockquote>{source.quote}</blockquote>
                      <small>
                        修订{source.revision} · 位置{source.start}—{source.end}
                      </small>
                    </div>
                  ))}
                </details>
              </article>
            ))}
            <details>
              <summary>未知与未覆盖问题</summary>
              {artifact.payload.unknowns.map((u, i) => (
                <p key={i}>{u}</p>
              ))}
            </details>
            {!!artifact.payload.scenes?.length && (
              <section aria-label="场景范围候选">
                <h3>按新正文定位场景</h3>
                <p>
                  原文位置由本次保存稿计算。时点未知不等于当前角色已知；需要新人物时，请一并选择上方人物候选。批次继续前须确认完整场景清单。
                </p>
                {artifact.payload.scenes.map((s) => (
                  <article className="fact" key={s.id}>
                    <label className="check">
                      <input
                        type="checkbox"
                        disabled={busy || repairReadOnly || !!stale || artifact.status !== 'candidate'}
                        checked={selectedScenes.includes(s.id)}
                        onChange={(e) =>
                          setSelectedScenes((old) =>
                            e.target.checked ? [...old, s.id] : old.filter((id) => id !== s.id),
                          )
                        }
                      />
                      采用场景：{s.scene.title}
                    </label>
                    <p>
                      {artifact.payload.source_manifest.find((c) => c.id === s.chapter_id)?.title || '本章'} ·
                      保存稿修订{s.scene.source_revision} · 位置{s.scene.start}—{s.scene.end} · 故事时点
                      {s.scene.story_order ?? '未知'} · {s.scene.story_time} ·{' '}
                      {s.scene.audience === 'character'
                        ? '人物视角'
                        : s.scene.audience === 'reader'
                          ? '读者视角'
                          : '作者视角'}
                    </p>
                    {s.scene.note && <p>{s.scene.note}</p>}
                    {s.quote !== undefined && (
                      <details>
                        <summary>核对场景原文</summary>
                        <blockquote>{s.quote}</blockquote>
                      </details>
                    )}
                  </article>
                ))}
                {!!artifact.payload.scene_replacements?.length && (
                  <>
                    <details>
                      <summary>对照将归档的旧场景绑定</summary>
                      {artifact.payload.scene_replacements.map((s) => (
                        <p key={s.id}>
                          {s.payload.title} · 旧稿修订{s.payload.source_revision} · 位置{s.payload.start}—
                          {s.payload.end} · 绑定修订{s.revision}
                        </p>
                      ))}
                    </details>
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={confirmReplacement}
                        onChange={(e) => setConfirmReplacement(e.target.checked)}
                        disabled={busy || repairReadOnly || artifact.status !== 'candidate'}
                      />
                      确认归档旧绑定并采用所选新范围（历史保留）
                    </label>
                  </>
                )}
              </section>
            )}
            <button
              className="primary"
              disabled={
                busy ||
                repairReadOnly ||
                !!stale ||
                artifact.status !== 'candidate' ||
                (!selected.length &&
                  !selectedScenes.length &&
                  (!!artifact.payload.records.length || !!artifact.payload.scenes?.length))
              }
              onClick={() =>
                void perform(async () => {
                  if (repairReadOnly)
                    throw new Error('历史或未核对的修订候选不能采用，请刷新并选择当前候选。')
                  const body = {
                    base_story_revision: storyRevision,
                    candidate_revision: artifact.candidate_revision,
                    selected_ids: selected,
                    selected_scene_ids: selectedScenes,
                    confirm_scene_replacement: confirmReplacement,
                  }
                  const signature = JSON.stringify({ artifactId, ...body })
                  if (pending.current?.signature !== signature)
                    pending.current = { signature, key: crypto.randomUUID() }
                  await api(`/branches/${branchId}/story-extractions/${artifact.id}/adopt`, 'POST', {
                    ...body,
                    idempotency_key: pending.current.key,
                  })
                  pending.current = null
                  setLoadedArtifact({ branchId, value: { ...artifact, status: 'accepted' } })
                  setMessage(
                    selectedScenes.length
                      ? '所选结构与场景已采用，旧绑定历史保留，正文未改变。'
                      : '所选结构已采用，未选项保留在原产物，正文未改变。',
                  )
                  await onAdopted()
                })
              }
            >
              确认采用所选结构（不修改正文）
            </button>
            <button
              disabled={busy || repairReadOnly || artifact.status !== 'candidate'}
              onClick={() =>
                void perform(async () => {
                  if (repairReadOnly)
                    throw new Error('历史或未核对的修订候选不能拒绝，请刷新并选择当前候选。')
                  await api(`/artifacts/${artifact.id}/reject`, 'POST', {
                    candidate_revision: artifact.candidate_revision,
                  })
                  setLoadedArtifact({ branchId, value: { ...artifact, status: 'rejected' } })
                  setMessage('已拒绝整批候选，正式记录和原稿保持。')
                })
              }
            >
              拒绝整批结构候选
            </button>
          </>
        )}
        <details>
          <summary>阶段任务历史</summary>
          {history.tasks.map((t) => (
            <p key={t.id}>
              {label(t.status)} · {t.error || t.id}
            </p>
          ))}
        </details>
      </section>
    </details>
  )
}
