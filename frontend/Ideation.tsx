import { useEffect, useRef, useState } from 'react'
import {
  api,
  label,
  type Artifact,
  type AuthorContract,
  type Branch,
  type GenreProfile,
  type ModuleInput,
  type Task,
} from './api'
import { confirmLeave, useLeaveGuard } from './leaveGuard'

const profile =
  '小说构思工具/2.0.0。一个结构完整、因果连贯的小说方案，保留作者约定；补充细节与建议结局可由作者修改，不生成正文。'
type Draft = {
  idea: string
  text: string
  sourceId: string | null
  revision: number
  dirty: boolean
  pendingTaskId?: string
}
const empty: Draft = { idea: '', text: '', sourceId: null, revision: 0, dirty: false }
type Snapshot = { contract: AuthorContract; genre: GenreProfile; storyRevision: number }

function readDraft(key: string): Draft {
  const value: unknown = JSON.parse(sessionStorage.getItem(key) || 'null')
  if (value === null) return empty
  if (typeof value === 'object') {
    const d = value as Record<string, unknown>
    if (
      typeof d.idea === 'string' &&
      typeof d.text === 'string' &&
      (d.sourceId === null || typeof d.sourceId === 'string') &&
      typeof d.revision === 'number' &&
      Number.isSafeInteger(d.revision) &&
      d.revision >= 0 &&
      typeof d.dirty === 'boolean' &&
      (d.pendingTaskId === undefined || typeof d.pendingTaskId === 'string')
    )
      return d as Draft
  }
  throw new Error('浏览器构思草稿无法读取，请保留本窗口内容。')
}

export function Ideation({
  branch,
  account,
  modelReady,
  onWriting,
}: {
  branch: Branch
  account: string
  modelReady: boolean
  onWriting: () => void
}) {
  const storageKey = 'ainovel-ideation-' + branch.id
  const [draft, setDraft] = useState<Draft>(empty)
  const draftRef = useRef(draft)
  draftRef.current = draft
  const [initialized, setInitialized] = useState(false)
  const [snapshot, setSnapshot] = useState<Snapshot>()
  const [tasks, setTasks] = useState<Task[]>([])
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [task, setTask] = useState<Task>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [storageError, setStorageError] = useState('')
  const [status, setStatus] = useState('')
  const [adopted, setAdopted] = useState(false)
  const [retained, setRetained] = useState('')
  const mounted = useRef(false)
  const storageWritable = useRef(true)
  const pending = useRef<{ signature: string; key: string } | null>(null)
  const pendingAdoption = useRef<{ signature: string; key: string } | null>(null)
  const inFlight = useRef(false)
  const activeTask = task && ['queued', 'running'].includes(task.status)
  useLeaveGuard(draft.dirty, '尚未采用的小说构思编辑')

  async function readSnapshot(): Promise<Snapshot> {
    const [contract, genre] = await Promise.all([
      api<AuthorContract>(`/branches/${branch.id}/author-contract`),
      api<GenreProfile>(`/branches/${branch.id}/genre-profile`),
    ])
    if (
      contract.source_story_revision === null ||
      contract.source_story_revision !== genre.source_story_revision
    )
      throw new Error('本书设定正在变化，请稍后重新读取。')
    return { contract, genre, storyRevision: contract.source_story_revision }
  }
  async function readHistory() {
    const [rows, candidates] = await Promise.all([
      api<Task[]>(`/tasks?branch_id=${branch.id}`),
      api<Artifact[]>(`/artifacts?branch_id=${branch.id}`),
    ])
    const ideas = rows.filter(
      (row) =>
        (row.payload.action === 'ideation.structure' || row.payload.action === 'ideation.explore') &&
        row.payload.writing_profile?.startsWith('小说构思工具/'),
    )
    const saved = candidates.filter((a) => ideas.some((t) => t.id === a.task_id))
    const latestId = ideas[0]?.result.artifact_id
    if (latestId && !saved.some((a) => a.id === latestId))
      saved.unshift(await api<Artifact>(`/artifacts/${latestId}`))
    return { ideas, candidates: saved }
  }
  useEffect(() => {
    mounted.current = true
    let active = true
    let restored = empty
    try {
      restored = readDraft(storageKey)
    } catch (e) {
      storageWritable.current = false
      setStorageError(e instanceof Error ? e.message : '浏览器草稿不可用，请在离开前采用构思。')
    }
    setDraft(restored)
    setInitialized(true)
    void Promise.all([readSnapshot(), readHistory()])
      .then(([value, history]) => {
        if (!active) return
        setSnapshot(value)
        setTasks(history.ideas)
        setArtifacts(history.candidates)
        setTask(history.ideas[0])
        const adoptedText = value.contract.intent_contract?.direction || ''
        const provenance =
          value.contract.provenance && typeof value.contract.provenance === 'object'
            ? (value.contract.provenance as Record<string, unknown>)
            : {}
        if (adoptedText && !draftRef.current.dirty && !restored.dirty && restored.text === adoptedText)
          setAdopted(true)
        if (
          !draftRef.current.dirty &&
          !restored.idea &&
          !restored.text &&
          adoptedText &&
          provenance.origin === 'author_adopted_idea'
        ) {
          setDraft({
            idea:
              typeof provenance.author_idea === 'string'
                ? provenance.author_idea
                : value.contract.author_text,
            text: adoptedText,
            sourceId:
              typeof provenance.source_candidate_id === 'string' ? provenance.source_candidate_id : null,
            revision:
              typeof provenance.source_candidate_revision === 'number'
                ? provenance.source_candidate_revision
                : 0,
            dirty: false,
          })
          setAdopted(true)
        } else if ((!restored.idea && !restored.text) || (!restored.dirty && restored.pendingTaskId)) {
          const latest = history.candidates.find(
            (a) =>
              a.status !== 'rejected' && (!restored.pendingTaskId || a.task_id === restored.pendingTaskId),
          )
          const source = history.ideas.find((t) => t.id === latest?.task_id)
          if (latest && source)
            setDraft((current) =>
              current.dirty
                ? current
                : {
                    idea: source.payload.instruction || '',
                    text: latest.payload.text || '',
                    sourceId: latest.id,
                    revision: latest.candidate_revision,
                    dirty: false,
                  },
            )
        }
      })
      .catch((e: unknown) => {
        if (active) setError(e instanceof Error ? e.message : '读取失败，请重试。')
      })
    return () => {
      active = false
      mounted.current = false
    }
  }, [branch.id])
  useEffect(() => {
    if (!initialized || !storageWritable.current) return
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(draft))
    } catch {
      setStorageError('浏览器草稿保存失败，输入仍在当前页面；请在离开前采用或复制。')
    }
  }, [draft, initialized, storageKey])
  useEffect(() => {
    if (!task || !activeTask) return
    let active = true
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const row = await api<Task>(`/tasks/${task.id}`)
        if (!active) return
        if (row.status === 'succeeded') {
          const history = await readHistory()
          if (!active) return
          setTasks(history.ideas)
          setArtifacts(history.candidates)
          const result = history.candidates.find((a) => a.id === row.result.artifact_id)
          if (result) setAdopted(false)
          if (result)
            setDraft((current) =>
              current.dirty
                ? current
                : {
                    idea: row.payload.instruction || '',
                    text: result.payload.text || '',
                    sourceId: result.id,
                    revision: result.candidate_revision,
                    dirty: false,
                  },
            )
          setStatus('构思已生成并保存。可以修改后采用，也可以再想一个。')
        } else if (!['queued', 'running'].includes(row.status)) {
          setError(row.error || '构思任务已停止，想法和已有方案保留，可以重试。')
        }
        setTask(row)
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : '读取进度失败，正在重连。')
      } finally {
        if (active) timer = setTimeout(() => void poll(), 1500)
      }
    }
    void poll()
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [task?.id, activeTask])

  async function perform(operation: () => Promise<void>) {
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true)
    setError('')
    try {
      await operation()
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : '操作失败，当前输入保留。')
    } finally {
      inFlight.current = false
      if (mounted.current) setBusy(false)
    }
  }
  async function generate() {
    if (!snapshot || !draft.idea.trim() || activeTask) return
    if (
      draft.text &&
      draft.dirty &&
      !window.confirm('生成新方案会替换当前尚未采用的编辑，继续吗？原始 AI 候选仍保存在历史构思中。')
    )
      return
    const payload: ModuleInput = {
      action: 'ideation.structure',
      novel_id: branch.novel_id,
      branch_id: branch.id,
      instruction: draft.idea.trim(),
      writing_profile: profile,
      account,
      max_output_tokens: 8192,
      include_scene_ranges: false,
      context_position: 0,
      author_contract_basis: {
        source_artifact_id: snapshot.contract.source_artifact_id,
        contract_version: snapshot.contract.contract_version,
      },
      genre_profile_basis: {
        source_artifact_id: snapshot.genre.source_artifact_id || null,
        catalog_version: snapshot.genre.catalog_version,
      },
    }
    const signature = JSON.stringify(payload)
    if (pending.current?.signature !== signature) pending.current = { signature, key: crypto.randomUUID() }
    const row = await api<Task>('/modules', 'POST', { ...payload, client_request_id: pending.current.key })
    if (!mounted.current) return
    pending.current = null
    setTask(row)
    setRetained('')
    setDraft((current) => ({ ...current, dirty: false, pendingTaskId: row.id }))
    setAdopted(false)
    setStatus('正在展开你的想法，完成后会保存为候选。')
  }
  async function adopt(manual = false) {
    if (!snapshot) return
    const body = {
      text: manual ? draft.idea.trim() : draft.text.trim(),
      author_idea: draft.idea.trim(),
      source_candidate_id: manual ? null : draft.sourceId,
      candidate_revision: manual ? 0 : draft.revision,
      base_story_revision: snapshot.storyRevision,
    }
    const signature = JSON.stringify(body)
    if (pendingAdoption.current?.signature !== signature)
      pendingAdoption.current = { signature, key: crypto.randomUUID() }
    await api(`/branches/${branch.id}/ideas/adopt`, 'POST', {
      ...body,
      idempotency_key: pendingAdoption.current.key,
    })
    if (!mounted.current) return
    setDraft((current) => ({
      ...current,
      text: body.text,
      sourceId: body.source_candidate_id,
      dirty: false,
      pendingTaskId: undefined,
    }))
    setAdopted(true)
    setStatus('已采用为本书故事方向。后续新任务会使用此构思，可以开始写作了。')
    const value = await readSnapshot()
    if (mounted.current) setSnapshot(value)
  }
  const locked = !!busy || !!activeTask || !initialized
  return (
    <div className="page-pad ideation-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">从一句想法开始</span>
          <h1>小说构思</h1>
          <p>写下一句想法，展开从起因、发展到高潮与结局的完整故事。</p>
        </div>
      </div>
      <section className="card">
        <form
          onSubmit={(event) => {
            event.preventDefault()
            void perform(generate)
          }}
        >
          <label>
            你的想法
            <textarea
              rows={4}
              maxLength={6000}
              value={draft.idea}
              disabled={locked}
              placeholder="例如：一个只能修补旧物的学徒，发现自己修好的东西会带回失主的一段记忆。"
              onChange={(e) => {
                setDraft((d) => ({ ...d, idea: e.target.value, dirty: true }))
                setAdopted(false)
              }}
            />
          </label>
          <div className="actions">
            <button className="primary" disabled={locked || !snapshot || !modelReady || !draft.idea.trim()}>
              {activeTask ? '正在构思…' : draft.text ? '再想一个' : '帮我展开'}
            </button>
            {!draft.text && (
              <button
                type="button"
                disabled={locked || !snapshot || !draft.idea.trim()}
                onClick={() => void perform(() => adopt(true))}
              >
                直接采用这段想法
              </button>
            )}
            {activeTask && (
              <button
                type="button"
                disabled={busy || !!task.cancel}
                onClick={() =>
                  void perform(async () => {
                    await api(`/tasks/${task.id}/cancel`, 'POST')
                    if (mounted.current) {
                      setTask((row) => row && { ...row, cancel: 1 })
                      setStatus('已请求取消，等待任务停止；已产生的费用仍按实际结算。')
                    }
                  })
                }
              >
                {task.cancel ? '正在取消…' : '取消生成'}
              </button>
            )}
          </div>
          <p className="small muted">
            AI方案包含人物动机、核心矛盾、发展转折、高潮结局与伏笔回收。直接采用只记录你自己的想法。
          </p>
          <p className="small muted">
            {modelReady
              ? `生成使用 ${account}，计入现有共享预算。`
              : '模型账户尚未配置，可以直接采用自己的想法。'}{' '}
            发送当前想法和本工作版本的已采用设定、约定及相关故事上下文；不发送书库资料。
          </p>
        </form>
      </section>
      {storageError && (
        <p className="notice" role="alert">
          {storageError}
        </p>
      )}
      {error && (
        <div className="notice" role="alert">
          <p>{error}</p>
          <button
            disabled={locked}
            onClick={() =>
              void perform(async () => {
                const value = await readSnapshot()
                if (mounted.current) {
                  setSnapshot(value)
                  setStatus('已重新读取当前约定，请核对后再生成或采用。')
                }
              })
            }
          >
            重新读取当前约定
          </button>
        </div>
      )}
      <p role="status">{activeTask ? `${label(task.status)} · ${status}` : status}</p>
      {task && !activeTask && task.status !== 'succeeded' && (
        <details className="notice">
          <summary>中断的构思</summary>
          <p>{task.error || label(task.status)}。原想法和已有方案继续保留。</p>
          <button
            disabled={busy}
            onClick={() =>
              void perform(async () => {
                const saved = await api<{ partials: { payload: { text?: string } }[] }>(
                  `/tasks/${task.id}/transcript`,
                )
                if (!mounted.current) return
                setRetained(
                  saved.partials
                    .map((row) => row.payload.text || '')
                    .filter(Boolean)
                    .join('\n\n'),
                )
                setStatus(
                  saved.partials.length
                    ? '已读取中断片段，仅供参考，尚未采用。'
                    : '该任务未留下生成片段，原想法保留，可重新生成。',
                )
              })
            }
          >
            查看保留的生成片段
          </button>
          {retained && <pre className="stream">{retained}</pre>}
        </details>
      )}
      {draft.text && (
        <section className="card">
          <h2>{adopted ? '已采用的构思' : '构思方案'}</h2>
          <p className="small muted">
            核对主角每一步为何行动、结果怎样引出后续，以及主冲突和主要伏笔如何收束。编辑后请重新核对这些衔接。
          </p>
          <label>
            修改构思方案
            <textarea
              rows={14}
              maxLength={6000}
              value={draft.text}
              disabled={locked}
              onChange={(e) => {
                setDraft((d) => ({ ...d, text: e.target.value, dirty: true }))
                setAdopted(false)
              }}
            />
          </label>
          {draft.text.length > 6000 && (
            <p role="alert">方案超过6000字，请精简后再采用；完整候选仍保留在历史构思中。</p>
          )}
          <p className="small muted">
            采用后更新本书的故事方向，已有锁定条件、禁止项和其他设定继续保留。正文由你在写作页继续创作。
          </p>
          <div className="actions">
            <button
              className="primary"
              disabled={locked || !snapshot || !draft.text.trim() || draft.text.length > 6000 || adopted}
              onClick={() => void perform(() => adopt())}
            >
              {adopted ? '已采用' : '采用构思'}
            </button>
            {adopted && <button onClick={onWriting}>去写作 →</button>}
          </div>
        </section>
      )}
      {snapshot?.contract.configured && (
        <details className="notice">
          <summary>当前创作约定</summary>
          {snapshot.contract.intent_contract && (
            <>
              <p>
                <strong>故事方向：</strong>
                {snapshot.contract.intent_contract.direction || '未设置'}
              </p>
              <p>
                <strong>锁定结果：</strong>
                {snapshot.contract.intent_contract.fixed_results.join('；') || '未设置'}
              </p>
              <p>
                <strong>禁止改变：</strong>
                {snapshot.contract.intent_contract.prohibitions.join('；') || '未设置'}
              </p>
            </>
          )}
          <p className="small muted">采用新构思只更新故事方向。其他约定可在「故事与审阅」调整。</p>
        </details>
      )}
      {artifacts.length > 0 && (
        <details className="notice">
          <summary>历史构思（{artifacts.length}）</summary>
          <label>
            载入历史构思
            <select
              disabled={locked}
              value={draft.sourceId || ''}
              onChange={(e) => {
                const a = artifacts.find((row) => row.id === e.target.value)
                const t = tasks.find((row) => row.id === a?.task_id)
                if (!a || !t || !confirmLeave()) return
                setDraft({
                  idea: t.payload.instruction || '',
                  text: a.payload.text || '',
                  sourceId: a.id,
                  revision: a.candidate_revision,
                  dirty: false,
                })
                setAdopted(false)
                setTask(t)
                setRetained('')
                setStatus('已载入历史构思，采用前请核对当前约定。')
              }}
            >
              <option value="">选择一个构思</option>
              {artifacts
                .filter((a) => a.status !== 'rejected')
                .map((a) => (
                  <option key={a.id} value={a.id}>
                    {tasks.find((t) => t.id === a.task_id)?.payload.instruction?.slice(0, 40) || '构思'} ·{' '}
                    {a.id.slice(0, 6)}
                  </option>
                ))}
            </select>
          </label>
        </details>
      )}
      <p className="small muted">
        想法和编辑暂存在本窗口，采用后保存到本机工作台。AI 候选会自动保留，可随时回来查看。
      </p>
    </div>
  )
}
