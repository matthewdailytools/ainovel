import { useCallback, useEffect, useRef, useState } from 'react'
import { api, label, type Artifact, type Branch, type Task } from './api'
import type { Notify } from './App'
import { AuthorIntentPanel } from './AuthorIntent'
import { ConceptionCandidateReview, ConceptionItemEditor, ConceptionQuote } from './ConceptionItems'
import { ConceptionWorldExtract } from './ConceptionWorldExtract'
import { GenreSettings } from './GenreProfile'
import { ProjectBriefPanel, type ProjectDraft } from './ProjectBrief'
import { StoryGraph } from './StoryGraph'
import { useLeaveGuard } from './leaveGuard'
import {
  baseRevision,
  candidateCategories,
  candidateIssues,
  candidateModelReview,
  conceptionHints,
  conceptionSourceEvidence,
  draftFromState,
  readConceptionDraft,
  removeConceptionLinks,
  type CandidateCategory,
  type ConceptionCategory,
  type ConceptionDestination,
  type ConceptionDraft,
  type ConceptionHandoff,
  type ConceptionIssue,
  type ConceptionModelReview,
  type ConceptionState,
  type ConceptionSource,
} from './conception'

type GenerationTask = Task & {
  payload: Task['payload'] & { operation?: string }
  result: Task['result'] & {
    partial_artifact_id?: string
    total_categories?: number
    completed_categories?: string[]
    pending_consistency_review?: boolean
  }
}
type FeedbackPanel = 'settings' | 'generation' | 'confirmation' | 'reverse'

export function ConceptionWorkspace({
  branch,
  account,
  modelReady,
  notify,
  onHandoff,
}: {
  branch: Branch
  account: string
  modelReady: boolean
  notify: Notify
  onHandoff: (destination: ConceptionDestination, handoff?: ConceptionHandoff) => void
}) {
  const path = `/branches/${encodeURIComponent(branch.id)}/conception`
  const storageKey = `ainovel-conception-edit-${branch.id}`
  const [state, setState] = useState<ConceptionState>()
  const [draft, setDraft] = useState<ConceptionDraft>()
  const [categoryId, setCategoryId] = useState('')
  const [categoryName, setCategoryName] = useState('')
  const [categoryTitleEdit, setCategoryTitleEdit] = useState('')
  const [instruction, setInstruction] = useState('')
  const [budget, setBudget] = useState('')
  const [completionNote, setCompletionNote] = useState('')
  const [projectReview, setProjectReview] = useState<{ key: string; itemIds: string[] }>()
  const [storyOpen, setStoryOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [tasks, setTasks] = useState<GenerationTask[]>([])
  const [projectTasks, setProjectTasks] = useState<GenerationTask[]>([])
  const [taskId, setTaskId] = useState('')
  const [candidate, setCandidate] = useState<{
    artifact: Artifact
    categories: CandidateCategory[]
    evidence?: Record<string, ConceptionSource>
    issues: ConceptionIssue[]
    modelReview?: ConceptionModelReview
  }>()
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [feedbackPanel, setFeedbackPanel] = useState<FeedbackPanel>('settings')
  const [storageError, setStorageError] = useState('')
  const [taskError, setTaskError] = useState('')
  const [artifactError, setArtifactError] = useState('')
  const [artifactReload, setArtifactReload] = useState(0)
  const running = useRef(false)
  const alive = useRef(true)
  const pending = useRef(new Map<string, { signature: string; key: string }>())
  const task = tasks.find((row) => row.id === taskId) || (taskId ? undefined : tasks[0])
  const activeTask = [...tasks, ...projectTasks].find((row) => ['queued', 'running'].includes(row.status))
  const artifactId = task?.result.artifact_id || task?.result.partial_artifact_id
  const categories = draft?.categories || state?.categories || []
  const category = categories.find((entry) => entry.id === categoryId) || categories[0]
  const choices = categories.flatMap((entry) => entry.items)
  const conflict = !!(
    draft &&
    state &&
    (draft.revision !== state.revision || draft.story_revision !== state.story_revision)
  )
  const dirty = !!draft
  const confirmation = state?.confirmation
  const readyHandoff = confirmation?.current && !dirty ? confirmation : undefined
  const projectImpacts = state?.project_impacts ?? []
  const projectReviewKey = `${branch.id}/${state?.project?.id}/${state?.revision}/${state?.story_revision}`
  const checkedProjectItems = projectReview?.key === projectReviewKey ? projectReview.itemIds : []
  const reconciledProjectItems = projectImpacts
    .filter((item) => item.reconciled || checkedProjectItems.includes(item.item_id))
    .map((item) => item.item_id)
  const panel = useRef<HTMLElement>(null)
  useLeaveGuard(dirty, '未保存的构思设定')

  const refresh = useCallback(async () => {
    const data = await api<ConceptionState>(path)
    if (alive.current)
      setState((current) =>
        current && (current.revision > data.revision || current.story_revision > data.story_revision)
          ? current
          : data,
      )
  }, [path])
  useEffect(() => {
    alive.current = true
    try {
      setDraft(readConceptionDraft(storageKey))
    } catch (failure) {
      setStorageError(failure instanceof Error ? failure.message : '浏览器草稿无法读取。')
    }
    void refresh().catch((failure: Error) => {
      if (alive.current) setError(failure.message)
    })
    return () => {
      alive.current = false
    }
  }, [refresh, storageKey])
  useEffect(() => {
    if (!draft) return
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(draft))
      setStorageError('')
    } catch {
      setStorageError('浏览器无法暂存这次编辑，请在离开前保存构思设定。')
    }
  }, [draft, storageKey])
  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const rows = await api<GenerationTask[]>(`/tasks?branch_id=${encodeURIComponent(branch.id)}`)
        if (active) {
          const generation = rows.filter((row) => row.kind === 'conception_generate')
          setTasks(generation.filter((row) => row.payload.operation !== 'project_brief'))
          setProjectTasks(generation.filter((row) => row.payload.operation === 'project_brief'))
          setTaskError('')
        }
      } catch (failure) {
        if (active) setTaskError(failure instanceof Error ? failure.message : '构思任务读取失败。')
      } finally {
        if (active) timer = setTimeout(() => void poll(), 1600)
      }
    }
    void poll()
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [branch.id])
  useEffect(() => {
    let active = true
    setCandidate(undefined)
    setArtifactError('')
    if (artifactId && task && task.id !== activeTask?.id) {
      void api<Artifact>(`/artifacts/${encodeURIComponent(artifactId)}`)
        .then((artifact) => {
          if (!active) return
          if (artifact.kind !== 'conception_bundle' || artifact.branch_id !== branch.id)
            throw new Error('草案归属不匹配，请重新选择本版本任务。')
          const categories = candidateCategories(artifact.payload.categories)
          const evidence =
            task.payload.operation === 'world_extract'
              ? conceptionSourceEvidence(artifact.payload.source_evidence)
              : undefined
          if (evidence && categories.some((category) => category.items.some((item) => !evidence[item.id])))
            throw new Error('提取草案缺少项目的正文依据，请保留任务记录并重新提取。')
          setCandidate({
            artifact,
            categories,
            evidence,
            issues: candidateIssues(artifact.payload.issues),
            modelReview: candidateModelReview(artifact.payload.model_review),
          })
        })
        .catch((failure: Error) => {
          if (active) setArtifactError(failure.message)
        })
    }
    return () => {
      active = false
    }
  }, [artifactId, task?.id, task?.payload.operation, activeTask?.id, branch.id, artifactReload])

  function clearDraft() {
    setDraft(undefined)
    try {
      sessionStorage.removeItem(storageKey)
      setStorageError('')
    } catch {
      setStorageError('浏览器暂存未能清理。重新进入时请核对服务端保存的构思修订。')
    }
  }
  function edit(transform: (categories: ConceptionCategory[]) => ConceptionCategory[]) {
    if (!state) return
    setDraft((current) => {
      const base = current || draftFromState(state)
      return { ...base, categories: transform(base.categories) }
    })
    setStatus('')
  }
  function updateCategory(next: ConceptionCategory) {
    edit((rows) => rows.map((entry) => (entry.id === next.id ? next : entry)))
  }
  async function perform(operation: () => Promise<void>, panel: FeedbackPanel = 'settings') {
    if (running.current) return
    running.current = true
    setBusy(true)
    setFeedbackPanel(panel)
    setError('')
    setStatus('')
    try {
      await operation()
    } catch (failure) {
      if (alive.current) setError(failure instanceof Error ? failure.message : '操作未完成，当前输入已保留。')
    } finally {
      running.current = false
      if (alive.current) setBusy(false)
    }
  }
  function feedback(panel: FeedbackPanel) {
    return (
      feedbackPanel === panel && (
        <>
          {error && <p role="alert">{error}</p>}
          {status && <p role="status">{status}</p>}
        </>
      )
    )
  }
  async function post<T>(action: string, payload: Record<string, unknown>, requestField: string) {
    const signature = JSON.stringify(payload)
    const previous = pending.current.get(action)
    const request = previous?.signature === signature ? previous : { signature, key: crypto.randomUUID() }
    pending.current.set(action, request)
    const response = await api<T>(`${path}/${action}`, 'POST', { ...payload, [requestField]: request.key })
    pending.current.delete(action)
    return response
  }
  function selectCategory(id: string) {
    setCategoryId(id)
    setCategoryTitleEdit('')
    panel.current?.focus()
  }
  async function save() {
    if (!draft) return
    const updated = await api<ConceptionState>(path, 'PUT', {
      ...baseRevision(draft),
      categories: draft.categories,
    })
    if (!alive.current) return
    setState(updated)
    clearDraft()
    setStatus('构思设定已保存。')
  }
  async function generate(id: string | null) {
    if (!state || draft) return
    const row = await post<GenerationTask>(
      'generate',
      {
        ...baseRevision(state),
        category_id: id,
        instruction: instruction.trim(),
        account,
        ...(state.project ? { project_confirmation_id: state.project.id } : {}),
        ...(budget ? { budget_limit: Number(budget) } : {}),
      },
      'client_request_id',
    )
    if (!alive.current) return
    setTasks((current) => [row, ...current.filter((entry) => entry.id !== row.id)])
    setTaskId(row.id)
    setStatus('已提交构思生成任务。')
  }
  async function generateProject(idea: string) {
    if (!state || draft) return
    const row = await post<GenerationTask>(
      'project-brief/generate',
      { ...baseRevision(state), idea, account, ...(budget ? { budget_limit: Number(budget) } : {}) },
      'client_request_id',
    )
    if (alive.current) setProjectTasks((current) => [row, ...current.filter((entry) => entry.id !== row.id)])
  }
  async function confirmProject(value: ProjectDraft) {
    let confirmed = false
    await perform(async () => {
      const signature = JSON.stringify(value)
      const action = 'project-brief'
      const prior = pending.current.get(action)
      const request = prior?.signature === signature ? prior : { signature, key: crypto.randomUUID() }
      pending.current.set(action, request)
      await api(`${path}/${action}`, 'PUT', { ...value, idempotency_key: request.key })
      pending.current.delete(action)
      await refresh()
      if (alive.current) {
        confirmed = true
        setStatus('立项已确认，生成设定时将使用新修订。')
      }
    })
    return confirmed
  }
  async function extractWorld(chapterIds: string[], limit?: number) {
    if (!state || draft) return
    const row = await post<GenerationTask>(
      'extract-world',
      {
        ...baseRevision(state),
        chapter_ids: chapterIds,
        account,
        ...(limit === undefined ? {} : { budget_limit: limit }),
      },
      'client_request_id',
    )
    if (!alive.current) return
    setTasks((current) => [row, ...current.filter((entry) => entry.id !== row.id)])
    setTaskId(row.id)
    setStatus('已提交世界设定提取任务。')
  }
  async function adopt(artifactId: string, selected: CandidateCategory[]) {
    if (!state || draft) return
    const updated = await post<ConceptionState>(
      'adopt',
      { ...baseRevision(state), artifact_id: artifactId, categories: selected },
      'idempotency_key',
    )
    if (!alive.current) return
    setState(updated)
    setStatus('所选构思已采用。')
  }
  async function reject(artifact: Artifact) {
    const updated = await api<Artifact>(`/artifacts/${encodeURIComponent(artifact.id)}/reject`, 'POST', {
      candidate_revision: artifact.candidate_revision,
    })
    if (!alive.current) return
    setCandidate((current) =>
      current?.artifact.id === updated.id ? { ...current, artifact: updated } : current,
    )
    setStatus('草案已拒绝，原草案保留在任务记录。')
  }
  async function confirm() {
    if (!state || draft) return
    const updated = await post<ConceptionState>(
      'confirm',
      {
        ...baseRevision(state),
        note: completionNote.trim(),
        reconciled_project_items: reconciledProjectItems,
      },
      'idempotency_key',
    )
    if (!alive.current) return
    setState(updated)
    setCompletionNote('')
    setStatus('作者确认已保存。写作和整书可使用这份构思快照。')
  }
  async function taskOperation(row: GenerationTask, action: 'cancel' | 'resume') {
    await api(
      `/tasks/${encodeURIComponent(row.id)}/${action}`,
      'POST',
      action === 'resume' ? { acknowledge_unknown: true } : {},
    )
    const updated = await api<GenerationTask>(`/tasks/${encodeURIComponent(row.id)}`)
    if (!alive.current) return
    setTasks((current) => current.map((entry) => (entry.id === updated.id ? updated : entry)))
    setStatus(action === 'cancel' ? '取消请求已提交。' : '续跑请求已提交。')
  }

  const itemNamesMissing = choices.some((item) => !item.title.trim())
  const issueLinks = choices.some((item) =>
    item.links?.some((id) => !choices.some((choice) => choice.id === id)),
  )
  const addName = categoryName.trim()
  const nameAvailable = (name: string) =>
    !!name && !categories.some((entry) => entry.title.toLocaleLowerCase() === name.toLocaleLowerCase())
  const canAdd = nameAvailable(addName)
  const customCategory = category && !(category.id in conceptionHints) ? category : undefined
  const titleEdit = categoryTitleEdit.trim()
  const canRename = nameAvailable(titleEdit)
  const candidateStale = !!(
    candidate &&
    (candidate.artifact.basis.conception_revision !== state?.revision ||
      candidate.artifact.basis.story_revision !== state?.story_revision ||
      !!(
        candidate.artifact.basis.project_confirmation_id &&
        candidate.artifact.basis.project_confirmation_id !== state?.project?.id
      ))
  )
  const sourceIds = new Set(state?.sources?.map((source) => source.item_id))
  const plannedPreview = state?.categories.flatMap((entry) => {
    if (entry.skipped) return []
    const count = entry.items.filter((item) => !sourceIds.has(item.id)).length
    return count ? [{ id: entry.id, title: entry.title, count }] : []
  })
  const validDerivedCount =
    state?.sources?.filter(
      (source) =>
        source.current &&
        state.categories.some(
          (entry) => !entry.skipped && entry.items.some((item) => item.id === source.item_id),
        ),
    ).length || 0
  const badBudget = !!budget && (!Number.isFinite(Number(budget)) || Number(budget) <= 0)
  const aiLocked = busy || dirty || !!activeTask || !modelReady || badBudget
  const index = category ? categories.indexOf(category) : -1

  return (
    <div className="conception-workspace">
      <div id="bench-project" tabIndex={-1}>
        {state && (
          <ProjectBriefPanel
            key={branch.id}
            project={state.project}
            storyRevision={state.story_revision}
            task={projectTasks[0]}
            busy={busy || dirty || (!!activeTask && activeTask.payload.operation !== 'project_brief')}
            modelReady={modelReady && !badBudget}
            onGenerate={(idea) => void perform(() => generateProject(idea))}
            onConfirm={confirmProject}
            onWorld={() => selectCategory('world')}
          />
        )}
      </div>
      <section id="bench-settings" tabIndex={-1} className="card" aria-label="按类别完善小说设定">
        <h2>构思工作台</h2>
        <p className="small muted">{branch.name} · 从任意类别开始，随时返回补充，也可以先跳过。</p>
        {feedback('settings')}
        {storageError && <p role="alert">{storageError}</p>}
        {!state && <p>{error ? '构思设定暂时无法读取。' : '正在读取这本小说的构思设定…'}</p>}
        <div className="actions">
          <button type="button" disabled={busy} onClick={() => void perform(refresh)}>
            核对最新修订
          </button>
          {dirty && (
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                if (window.confirm('放弃本窗口尚未保存的编辑，载入已保存构思？'))
                  void perform(async () => {
                    await refresh()
                    if (alive.current) clearDraft()
                  })
              }}
            >
              放弃本次编辑
            </button>
          )}
        </div>
        {state && category && (
          <>
            <p className="small muted">
              保存修订 {state.revision} · {dirty ? '有未保存编辑' : '已保存'}
              {activeTask ? ' · 有生成任务运行中' : ''}
            </p>
            {conflict && (
              <p role="alert">本窗口草稿所用的构思或正文修订已变化。请先另存需要的编辑，再载入最新版本。</p>
            )}
            <nav className="conception-steps" aria-label="构思类别">
              {categories.map((entry) => (
                <button
                  type="button"
                  key={entry.id}
                  aria-label={entry.title}
                  aria-describedby={`conception-count-${entry.id}`}
                  aria-current={entry.id === category.id ? 'step' : undefined}
                  onClick={() => selectCategory(entry.id)}
                >
                  <span>{entry.title}</span>
                  <small id={`conception-count-${entry.id}`}>
                    {entry.skipped ? '暂缓' : `${entry.items.length} 项`}
                  </small>
                </button>
              ))}
            </nav>
            <section ref={panel} tabIndex={-1} aria-labelledby="conception-step-title">
              <h3 id="conception-step-title">{category.title}</h3>
              <p className="small">{conceptionHints[category.id] || '逐项记录对这本小说有用的设定。'}</p>
              {customCategory && (
                <details key={category.id}>
                  <summary>管理当前自定义类别</summary>
                  <label>
                    新类别名称
                    <input
                      value={categoryTitleEdit}
                      maxLength={200}
                      onChange={(event) => setCategoryTitleEdit(event.target.value)}
                    />
                  </label>
                  <div className="actions">
                    <button
                      type="button"
                      disabled={busy || !canRename}
                      onClick={() => {
                        updateCategory({ ...customCategory, title: titleEdit })
                        setCategoryTitleEdit('')
                      }}
                    >
                      改名
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => {
                        if (!window.confirm(`移除「${customCategory.title}」及其项目和关联？保存后应用。`))
                          return
                        edit((rows) =>
                          removeConceptionLinks(
                            rows.filter((row) => row.id !== customCategory.id),
                            customCategory.items.map((item) => item.id),
                          ),
                        )
                        selectCategory(categories[0].id)
                      }}
                    >
                      移除自定义类别
                    </button>
                  </div>
                </details>
              )}
              {!!state.observed?.some((record) => record.category_id === category.id) && (
                <details>
                  <summary>据正文整理的已采用记录</summary>
                  <ul className="conception-issues">
                    {state.observed
                      .filter((record) => record.category_id === category.id)
                      .map((record) => (
                        <li key={record.id}>
                          {record.title} · 记录修订 {record.revision}
                        </li>
                      ))}
                  </ul>
                  <p className="small">可在下方旧作整理入口回查原文和记录。</p>
                </details>
              )}
              <fieldset disabled={busy}>
                <legend>编辑本类项目</legend>
                {!category.items.length && <p className="small muted">这个类别还没有设定项目。</p>}
                {category.items.map((item) => (
                  <div className="conception-item" key={item.id}>
                    <ConceptionItemEditor
                      item={item}
                      choices={choices}
                      worldSetting={category.id === 'world'}
                      onChange={(next) =>
                        updateCategory({
                          ...category,
                          items: category.items.map((row) => (row.id === next.id ? next : row)),
                        })
                      }
                    />
                    {state.sources
                      ?.filter((source) => source.item_id === item.id)
                      .map((source) => (
                        <ConceptionQuote
                          key={source.item_id}
                          source={source.evidence}
                          current={source.current}
                          edited={
                            source.author_edited ||
                            item.detail !==
                              state.categories
                                .find((entry) => entry.id === category.id)
                                ?.items.find((entry) => entry.id === item.id)?.detail
                          }
                        />
                      ))}
                    <button
                      type="button"
                      aria-label={`移除项目 ${item.title || '未命名项目'}`}
                      onClick={() => {
                        if (
                          !item.title ||
                          window.confirm(`从当前构思移除「${item.title}」及其关联？保存后应用。`)
                        )
                          edit((rows) =>
                            removeConceptionLinks(
                              rows.map((row) =>
                                row.id === category.id
                                  ? { ...row, items: row.items.filter((entry) => entry.id !== item.id) }
                                  : row,
                              ),
                              [item.id],
                            ),
                          )
                      }}
                    >
                      移除项目
                    </button>
                  </div>
                ))}
                <div className="actions">
                  <button
                    type="button"
                    disabled={category.items.length >= 40}
                    onClick={() =>
                      updateCategory({
                        ...category,
                        skipped: false,
                        items: [
                          ...category.items,
                          { id: crypto.randomUUID(), title: '', detail: '', links: [] },
                        ],
                      })
                    }
                  >
                    添加构思项目
                  </button>
                  <button
                    type="button"
                    onClick={() => updateCategory({ ...category, skipped: !category.skipped })}
                  >
                    {category.skipped ? '继续完善此类别' : '暂缓此类别'}
                  </button>
                </div>
                {itemNamesMissing && <p className="small muted">请填好各类别的项目名称再保存。</p>}
                {issueLinks && <p className="small muted">有项目仍关联着已移除的项目，请先核对相应关联。</p>}
                <div className="actions">
                  <button
                    type="button"
                    disabled={index < 1}
                    onClick={() => selectCategory(categories[index - 1].id)}
                  >
                    上一类别
                  </button>
                  <button
                    type="button"
                    disabled={index >= categories.length - 1}
                    onClick={() => selectCategory(categories[index + 1].id)}
                  >
                    下一类别
                  </button>
                  <button
                    type="button"
                    className="primary"
                    disabled={!dirty || conflict || itemNamesMissing || issueLinks}
                    onClick={() => void perform(save)}
                  >
                    保存构思设定
                  </button>
                </div>
              </fieldset>
            </section>
            <div className="conception-add-category">
              <label>
                类别名称
                <input
                  value={categoryName}
                  maxLength={200}
                  onChange={(event) => setCategoryName(event.target.value)}
                />
              </label>
              <button
                type="button"
                disabled={busy || !canAdd || categories.length >= 24}
                onClick={() => {
                  const id = crypto.randomUUID()
                  edit((rows) => [...rows, { id, title: addName, items: [], skipped: false }])
                  setCategoryId(id)
                  setCategoryName('')
                }}
              >
                添加构思类别
              </button>
            </div>
          </>
        )}
      </section>

      <section className="card" id="conception-generation" aria-label="构思生成与草案">
        <h2>AI 构思辅助</h2>
        {feedback('generation')}
        <p className="small">生成会发送本次要求、本工作版本已保存的构思、创作约定与题材。结果保留为草案。</p>
        {state?.project && <p className="small">将沿用立项修订 {state.project.revision}。</p>}
        {!modelReady && <p className="small muted">模型账户尚未配置密钥或单价。手动编辑和确认仍可使用。</p>}
        {dirty && <p className="small muted">请先保存本窗口的构思设定，然后生成或采用草案。</p>}
        <fieldset disabled={!state || busy}>
          <legend>本次生成要求</legend>
          <label>
            构思要求
            <textarea
              rows={3}
              maxLength={6000}
              value={instruction}
              onChange={(event) => setInstruction(event.target.value)}
              placeholder="例如：主角靠修补旧物破案。重点补出谜底与前文线索的关系。"
            />
          </label>
          <label>
            本次任务预算上限（可选，使用所选账户币种）
            <input
              type="number"
              min={0.01}
              step={0.01}
              value={budget}
              onChange={(event) => setBudget(event.target.value)}
            />
          </label>
          {badBudget && <p className="small">请填写大于零的本次预算，或留空使用账户剩余预算。</p>}
          <p className="small muted">
            当前类别：{category?.title || '尚未读取'}。一键生成会依次处理未暂缓的类别。
          </p>
          <div className="actions">
            <button
              type="button"
              disabled={aiLocked || !category}
              onClick={() => category && void perform(() => generate(category.id), 'generation')}
            >
              生成当前类别
            </button>
            <button
              className="primary"
              type="button"
              disabled={aiLocked || !categories.some((entry) => !entry.skipped)}
              onClick={() => void perform(() => generate(null), 'generation')}
            >
              一键生成全套草案
            </button>
          </div>
        </fieldset>
        {taskError && <p role="alert">{taskError}</p>}
        {!!tasks.length && (
          <label>
            构思任务记录
            <select value={task?.id || taskId} onChange={(event) => setTaskId(event.target.value)}>
              {tasks.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.payload.operation === 'world_extract' ? '旧作世界设定' : '构思生成'} ·{' '}
                  {label(row.status)} · {new Date(row.created * 1000).toLocaleString()} · {row.id.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
        )}
        {task && (
          <div aria-live="polite" className="conception-task">
            <p>
              {label(task.status)}
              {task.result.total_categories !== undefined &&
                ` · 已完成 ${task.result.completed_categories?.length || 0}/${task.result.total_categories} 个类别`}
            </p>
            {task.result.pending_consistency_review && (
              <p className="small">
                {activeTask?.id === task.id
                  ? '各类别草案已保存，正在进行 AI 一致性复核。'
                  : '各类别草案已保存，AI 一致性复核尚未完成。你可以核对已保存的草案，或从检查点继续复核。'}
              </p>
            )}
            {task.error && <p role="alert">{task.error}</p>}
            {activeTask?.id === task.id && (
              <button
                type="button"
                disabled={busy || !!task.cancel}
                onClick={() => void perform(() => taskOperation(task, 'cancel'), 'generation')}
              >
                取消构思生成
              </button>
            )}
            {['blocked_runtime', 'cancelled', 'paused'].includes(task.status) && (
              <button
                type="button"
                disabled={busy || !!activeTask}
                onClick={() => {
                  if (
                    window.confirm(
                      '按原依据从任务检查点续跑。已完成的类别会保留，结果未知的请求继续等待核对。',
                    )
                  )
                    void perform(() => taskOperation(task, 'resume'), 'generation')
                }}
              >
                {task.result.pending_consistency_review ? '从检查点继续复核' : '从检查点继续生成'}
              </button>
            )}
          </div>
        )}
        {artifactError && (
          <p role="alert">
            {artifactError}{' '}
            <button type="button" onClick={() => setArtifactReload((revision) => revision + 1)}>
              重新读取草案
            </button>
          </p>
        )}
        {candidate?.artifact.id === artifactId && candidate && (
          <>
            {!!candidate.artifact.basis.project_revision && (
              <p className="small">
                本草案使用立项修订 {String(candidate.artifact.basis.project_revision)}。
              </p>
            )}
            <ConceptionCandidateReview
              key={`${candidate.artifact.id}:${candidate.artifact.status}`}
              categories={state?.categories || []}
              candidates={candidate.categories}
              evidence={candidate.evidence}
              issues={candidate.issues}
              modelReview={candidate.modelReview}
              busy={busy || dirty || !!activeTask}
              stale={candidateStale}
              rejected={candidate.artifact.status === 'rejected'}
              onReject={
                task?.status === 'succeeded' && candidate.artifact.status === 'candidate'
                  ? () => void perform(() => reject(candidate.artifact), 'generation')
                  : undefined
              }
              onAdopt={(selected) => void perform(() => adopt(candidate.artifact.id, selected), 'generation')}
            />
          </>
        )}
      </section>

      <details className="card" onToggle={(event) => setSettingsOpen(event.currentTarget.open)}>
        <summary>创作约定与频道题材</summary>
        {settingsOpen && (
          <>
            <AuthorIntentPanel
              branchId={branch.id}
              novelId={branch.novel_id}
              account={account}
              modelReady={modelReady}
              notify={notify}
              onSaved={refresh}
            />
            <GenreSettings branchId={branch.id} notify={notify} onSaved={refresh} />
          </>
        )}
      </details>

      <details className="card" onToggle={(event) => setStoryOpen(event.currentTarget.open)}>
        <summary>从旧作正文反向提取</summary>
        <p className="small">
          在本工作版本选择保存的章节，核对提取候选与原文后逐项采用。采用的正文记录与上面的未来构思分别保存。
        </p>
        {storyOpen && (
          <>
            {feedback('reverse')}
            {task?.payload.operation === 'world_extract' && (
              <p className="small">
                当前提取任务{label(task.status)}。{' '}
                <a href="#conception-generation">查看世界设定草案与任务记录</a>
              </p>
            )}
            <ConceptionWorldExtract
              branchId={branch.id}
              storyRevision={state?.story_revision}
              locked={!state || busy || dirty || !!activeTask || !modelReady}
              onExtract={(ids, amount) => void perform(() => extractWorld(ids, amount), 'reverse')}
            />
            <StoryGraph
              branchId={branch.id}
              account={account}
              modelReady={modelReady}
              notify={notify}
              onSaved={refresh}
            />
          </>
        )}
      </details>

      <section className="card" aria-label="构思核对与交接">
        <h2>完整度与作者确认</h2>
        {feedback('confirmation')}
        <p className="small">提示供你决定是否继续完善。保留待办也可以确认。</p>
        {state && (
          <>
            {!!projectImpacts.length && (
              <fieldset disabled={busy || dirty}>
                <legend>立项修订影响</legend>
                {projectImpacts.map((item) => (
                  <label key={item.item_id}>
                    <input
                      type="checkbox"
                      checked={reconciledProjectItems.includes(item.item_id)}
                      disabled={item.reconciled}
                      onChange={(event) =>
                        setProjectReview({
                          key: projectReviewKey,
                          itemIds: event.target.checked
                            ? [...checkedProjectItems, item.item_id]
                            : checkedProjectItems.filter((id) => id !== item.item_id),
                        })
                      }
                    />
                    已按当前立项核对「{item.title}」 · 原依据{' '}
                    {item.source_project_revision === null || item.source_project_revision === undefined
                      ? item.source_project_id
                      : `立项修订 ${item.source_project_revision}`}
                  </label>
                ))}
              </fieldset>
            )}
            {state.issues.length ? (
              <ul className="conception-issues">
                {state.issues.map(({ category_id, message }, index) => (
                  <li key={`${category_id}-${index}`}>
                    {state.categories.find((entry) => entry.id === category_id)?.title || '构思核对'} ·{' '}
                    {message}
                  </li>
                ))}
              </ul>
            ) : (
              <p>当前已保存构思暂无完整度提示。</p>
            )}
            {confirmation && (
              <p className="notice">
                {confirmation.current ? '当前构思已确认' : '上次确认已成为历史'} · 构思修订{' '}
                {confirmation.revision}
                {confirmation.note ? ` · ${confirmation.note}` : ''}
              </p>
            )}
            {readyHandoff && (
              <details>
                <summary>查看交接范围</summary>
                <ul className="conception-issues">
                  {plannedPreview?.map((category) => (
                    <li key={category.id}>
                      {category.title} · {category.count} 项已保存设定
                    </li>
                  ))}
                  <li>
                    从旧作整理的有效正文记录与设定 {state.observed.length + validDerivedCount}{' '}
                    项，供整书规划与全局辅助参考。
                  </li>
                  <li>针对某章生成时，正文资料按该章有效范围取用。</li>
                </ul>
              </details>
            )}
            <label>
              保留事项说明
              <textarea
                rows={2}
                maxLength={4000}
                value={completionNote}
                onChange={(event) => setCompletionNote(event.target.value)}
              />
            </label>
            <div className="actions">
              <button
                className="primary"
                type="button"
                disabled={busy || dirty || reconciledProjectItems.length !== projectImpacts.length}
                onClick={() => void perform(confirm, 'confirmation')}
              >
                确认当前构思
              </button>
              <button
                type="button"
                onClick={() =>
                  onHandoff(
                    'writing',
                    readyHandoff ? { branchId: branch.id, confirmation: readyHandoff } : undefined,
                  )
                }
              >
                {readyHandoff ? '去写作（使用构思）' : '直接去写作'}
              </button>
              {readyHandoff && (
                <>
                  <button
                    type="button"
                    onClick={() => onHandoff('assisted', { branchId: branch.id, confirmation: readyHandoff })}
                  >
                    去半自动创作
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      onHandoff('automatic', { branchId: branch.id, confirmation: readyHandoff })
                    }
                  >
                    去全自动创作
                  </button>
                </>
              )}
            </div>
          </>
        )}
      </section>
    </div>
  )
}
