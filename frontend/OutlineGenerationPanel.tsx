import { useEffect, useRef, useState } from 'react'
import type { Notify } from './App'
import { api, ApiError, label } from './api'
import type { components } from './api-schema'
import {
  SelectedOutlineCards,
  selectedOutlineCardKey,
  selectedOutlineCardReference,
  type SelectedOutlineCard,
} from './SelectedOutlineCards'

type Plan = components['schemas']['OutlinePlan']
type GenerationTask = {
  id: string
  status: string
  error: string
  created: number
  artifact_id: string | null
}
type Proposal = {
  id: string
  base_revision: number
  confirmation_id: string | null
  project_revision: number | null
  current: boolean
  plan: Plan
}

export function OutlineGenerationPanel({
  branchId,
  account,
  modelReady,
  baseRevision,
  storyRevision,
  confirmationId,
  directoryChapterCount,
  dirty,
  existingReferenceCount = 0,
  referenceOptions,
  onRefreshReferences,
  onDirectoryReady,
  onLoad,
  notify,
}: {
  branchId: string
  account: string
  modelReady: boolean
  baseRevision: number
  storyRevision: number
  confirmationId: string | null
  directoryChapterCount: number
  dirty: boolean
  existingReferenceCount?: number
  referenceOptions: SelectedOutlineCard[]
  onRefreshReferences: () => Promise<void>
  onDirectoryReady: () => Promise<void>
  onLoad: (plan: Plan, confirmationId: string | null, candidateId: string) => void
  notify: Notify
}) {
  const workspace = `/branches/${branchId}/outline-workspace`
  const endpoint = workspace + '/generation'
  const [instruction, setInstruction] = useState('')
  const [tasks, setTasks] = useState<GenerationTask[]>([])
  const [selected, setSelected] = useState('')
  const [proposal, setProposal] = useState<Proposal>()
  const [selectedVersions, setSelectedVersions] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [directoryBusy, setDirectoryBusy] = useState(false)
  const [chapterCount, setChapterCount] = useState('2')
  const [directoryNotice, setDirectoryNotice] = useState<{ role: 'status' | 'alert'; text: string }>()
  const directoryPending = useRef<{ signature: string; key: string } | null>(null)
  const pending = useRef<{ signature: string; key: string } | null>(null)
  const pollEpoch = useRef(0)
  const running = tasks.some((task) => task.status === 'queued' || task.status === 'running')
  const selectedTask = tasks.find((task) => task.id === selected)
  const candidateId = selectedTask?.artifact_id
  const current = proposal?.id === candidateId ? proposal : undefined
  const canLoad = current?.current && current.base_revision === baseRevision && !dirty
  const hasChapters = directoryChapterCount > 0
  const directorySize = Number(chapterCount)
  const validDirectorySize = Number.isInteger(directorySize) && directorySize >= 1 && directorySize <= 100
  const working = busy || directoryBusy
  const chosenCards = selectedVersions.flatMap((id) => {
    const card = referenceOptions.find((option) => selectedOutlineCardKey(option) === id)
    return card ? [card] : []
  })
  const staleCards = chosenCards.length !== selectedVersions.length
  const canGenerate = !working && !running && !dirty && !staleCards && hasChapters && modelReady
  const latestInputs = useRef({ candidateId, dirty, baseRevision, storyRevision })
  latestInputs.current = { candidateId, dirty, baseRevision, storyRevision }
  useEffect(() => {
    let active = true
    async function poll() {
      const epoch = pollEpoch.current
      try {
        const rows = await api<GenerationTask[]>(endpoint)
        if (active && epoch === pollEpoch.current) {
          setTasks(rows)
          setSelected((id) => (rows.some((task) => task.id === id) ? id : (rows[0]?.id ?? '')))
        }
      } catch (error) {
        if (active) notify(error)
      }
    }
    void poll()
    const timer = setInterval(() => void poll(), 2500)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [endpoint, notify])

  useEffect(() => {
    if (!candidateId) return
    let active = true
    async function read() {
      try {
        const result = await api<Proposal>(endpoint + '/' + candidateId)
        if (active) setProposal(result)
      } catch (error) {
        if (active) notify(error)
      }
    }
    void read()
    return () => {
      active = false
    }
  }, [candidateId, endpoint, baseRevision, storyRevision, notify])

  async function createDirectory() {
    if (working || running || dirty || hasChapters || !validDirectorySize) return
    setDirectoryBusy(true)
    const values = {
      base_revision: baseRevision,
      base_story_revision: storyRevision,
      chapter_count: directorySize,
    }
    const signature = JSON.stringify(values)
    if (directoryPending.current?.signature !== signature)
      directoryPending.current = { signature, key: crypto.randomUUID() }
    let committed = false
    try {
      await api(workspace + '/chapter-directory', 'POST', {
        ...values,
        client_request_id: directoryPending.current.key,
      })
      committed = true
      await onDirectoryReady()
      directoryPending.current = null
      setDirectoryNotice({ role: 'status', text: '章节目录已建立。' })
    } catch (error) {
      const text = committed
        ? '章节目录已建立，请按原章数重试读取。'
        : error instanceof ApiError && error.status < 500
          ? error.message
          : '暂未确认章节目录的写入结果，请按原章数重试。'
      setDirectoryNotice({ role: 'alert', text })
      if (
        error instanceof ApiError &&
        ['outline_directory_changed', 'outline_conflict'].includes(error.code)
      ) {
        await onDirectoryReady().catch(notify)
      }
    } finally {
      setDirectoryBusy(false)
    }
  }

  async function generate() {
    if (!canGenerate) return
    setBusy(true)
    const values = {
      account,
      instruction,
      base_revision: baseRevision,
      base_story_revision: storyRevision,
      confirmation_id: confirmationId,
      ...(chosenCards.length > 0 && { reference_cards: chosenCards.map(selectedOutlineCardReference) }),
    }
    const signature = JSON.stringify(values)
    if (pending.current?.signature !== signature) pending.current = { signature, key: crypto.randomUUID() }
    try {
      const task = await api<GenerationTask>(endpoint, 'POST', {
        ...values,
        client_request_id: pending.current.key,
      })
      pollEpoch.current++
      setTasks((rows) => [task, ...rows.filter((row) => row.id !== task.id)])
      setSelected(task.id)
      pending.current = null
    } catch (error) {
      notify(error)
      if (error instanceof ApiError && error.code === 'reference_changed') {
        try {
          await onRefreshReferences()
        } catch (refreshError) {
          notify(refreshError)
        }
      } else if (error instanceof ApiError && error.code === 'outline_directory_changed') {
        await onDirectoryReady().catch(notify)
      }
    } finally {
      setBusy(false)
    }
  }

  async function refreshReferences() {
    setBusy(true)
    try {
      await onRefreshReferences()
    } catch (error) {
      notify(error)
    } finally {
      setBusy(false)
    }
  }

  async function load() {
    if (!candidateId) return
    setBusy(true)
    try {
      const fresh = await api<Proposal>(endpoint + '/' + candidateId)
      setProposal(fresh)
      const latest = latestInputs.current
      if (
        fresh.current &&
        fresh.id === latest.candidateId &&
        fresh.base_revision === latest.baseRevision &&
        latest.storyRevision === storyRevision &&
        !latest.dirty
      )
        onLoad(fresh.plan, fresh.confirmation_id, fresh.id)
    } catch (error) {
      notify(error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card" aria-label="大纲生成">
      <h3>生成大纲候选</h3>
      {!hasChapters && (
        <fieldset disabled={working || running || dirty}>
          <legend>首次规划的章节目录</legend>
          <label>
            新建规划章数
            <input
              type="number"
              min={1}
              max={100}
              value={chapterCount}
              onChange={(event) => setChapterCount(event.target.value)}
            />
          </label>
          <p className="small">先按章数建立空章目录，再生成四级大纲候选。</p>
          <button type="button" disabled={!validDirectorySize} onClick={() => void createDirectory()}>
            {directoryBusy
              ? '正在建立章节目录…'
              : directoryNotice?.role === 'alert'
                ? '重试建立章节目录'
                : '建立章节目录'}
          </button>
        </fieldset>
      )}
      {directoryNotice && <p role={directoryNotice.role}>{directoryNotice.text}</p>}
      <label>
        本次规划要求
        <textarea
          rows={2}
          maxLength={6000}
          value={instruction}
          onChange={(event) => setInstruction(event.target.value)}
        />
      </label>
      {existingReferenceCount > 0 && (
        <p className="small">
          最新保存的大纲中引用了 {existingReferenceCount} 张手法卡，本次会发送它们的摘要和修订标识。
        </p>
      )}
      <SelectedOutlineCards
        cards={referenceOptions}
        selected={selectedVersions}
        onChange={setSelectedVersions}
        onRefresh={() => void refreshReferences()}
        disabled={working || running}
      />
      <p className="small">
        {hasChapters &&
          `按当前 ${directoryChapterCount} 章的目录规划，发送当前确认立项及所选设定中已采用的条目。`}
        {dirty ? '保存当前编辑后可生成或载入。' : ''}
        {!modelReady ? '配置模型账户后可生成。' : ''}
      </p>
      <button type="button" disabled={!canGenerate} onClick={() => void generate()}>
        生成四级大纲候选
      </button>
      {!!tasks.length && (
        <label>
          大纲生成记录
          <select disabled={working} value={selected} onChange={(event) => setSelected(event.target.value)}>
            {tasks.map((task) => (
              <option value={task.id} key={task.id}>
                {label(task.status)} · {new Date(task.created * 1000).toLocaleString()}
              </option>
            ))}
          </select>
        </label>
      )}
      {selectedTask?.error && <p role="status">{selectedTask.error}</p>}
      {current && (
        <>
          <p>{current.current ? '等待作者核对' : '依据已改变，保留为历史候选'}</p>
          <p>
            大纲基线 {current.base_revision}
            {current.project_revision !== null ? ` · 立项修订 ${current.project_revision}` : ''}
          </p>
          <p>{current.plan.summary}</p>
          <p>
            {current.plan.volumes?.length ?? 0} 卷 · {current.plan.promises?.length ?? 0} 个计划伏笔窗口
          </p>
          <button type="button" disabled={!canLoad || working} onClick={() => void load()}>
            载入候选供编辑
          </button>
        </>
      )}
    </section>
  )
}
