import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import {
  api,
  label,
  type Artifact,
  type Branch,
  type Chapter,
  type Excerpt,
  type ModuleInput,
  type Novel,
  type Task,
  type GenreProfile,
} from './api'
import type { Notify } from './App'
import { Difference, Editor, type Selection } from './Editor'
import { ManuscriptTree } from './ManuscriptTree'
import type { SettingKind } from './workspaceSettingsKinds'
import { readWorkspacePosition, rememberWorkspacePosition, type ReadingPosition } from './workspacePosition'
import { readDraft, writeDraft, type Draft } from './drafts'
import { GenreProfileDetails, GenreProfileNotice } from './GenreProfile'
import { AuthorContractDetails, AuthorContractNotice, type AuthorContract } from './AuthorIntent'
import { ChapterContext } from './ChapterContext'
import { ChapterOutline, ChapterSettings, useWritingContext } from './WritingContext'
import { ChapterChecks, chapterCheckKind } from './ChapterChecks'
import { ContinuityLedger, TodayBar, useContinuityLedger, type LedgerTarget } from './ContinuityLedger'
import { ChapterTechniqueReferences, supportsTechniqueReferences } from './TechniqueReferences'
import { RevisionPanel, RevisionTraceDetails } from './RevisionPanel'
import { ReferenceComparison } from './ReferenceWorkspace'
import { ManuscriptImport } from './ManuscriptImport'
import { SceneTimePanel } from './SceneTimePanel'
import { ConditionalReviewNotice } from './ConditionalReviewNotice'
import {
  MemoryAcceptanceRecord,
  MemorySelectionPanel,
  useMemorySelection,
  type CandidateAcceptanceInput,
} from './MemorySelection'
import { FrozenPlotAuditPolicy, PlotAuditDetails, PlotAuditOption } from './PlotAuditDetails'
import { plotAuditEnabled, plotAuditFromPayload } from './plotAudit'
import {
  CandidateHistory,
  CandidateRecheckAction,
  CandidateRecheckReport,
  candidateRecheckSourceMode,
} from './candidateRecheck'
import {
  SourceCorrectionPanel,
  SourceCorrectionArtifact,
  SourceCorrectionTaskNotice,
} from './SourceCorrectionPanel'
import {
  isCorrectionReceiptKind,
  correctionKindLabel,
  SOURCE_CORRECTION_KIND,
  CORRECTED_RECHECK_KIND,
} from './sourceCorrection'
import { confirmLeave, useLeaveGuard } from './leaveGuard'
import { latestArtifact, refreshArtifactList } from './artifactRefresh'
import { checkFeedbackTarget, feedbackRevisionInstruction, type FeedbackHandoff } from './feedbackRevision'
import type { ConceptionHandoff } from './conception'
import {
  adviceDirectionBasis,
  checkDirectionTarget,
  directionExpansionInstruction,
  readDirectionExpansionInstruction,
  type DirectionBasis,
} from './directionHandoff'

const actions = [
  ['writing.advise', '卡点诊断与方向'],
  ['writing.rewrite_selection', '改写选区'],
  ['writing.expand_plot', '按情节补写整章'],
  ['review.chapter', '本章审稿'],
  ['memory.extract', '提取故事事实'],
  ['ideation.explore', '创意与定位'],
  ['outline.plan', '大纲规划'],
  ['character.generate', '人物设计'],
  ['world.generate', '世界设定'],
] as const
const designActions = new Set<ModuleInput['action']>(['world.generate', 'character.generate', 'outline.plan'])
const designKinds = new Set(['world', 'characters', 'outline'])
const workspaceScopes = ['workspace-setting', 'workspace-chat']
const savedLocally = '已保存到本机'
const cachingDraft = '正在缓存到此浏览器…'
const WorkspaceChat = lazy(() =>
  import('./WorkspaceChat').then(({ WorkspaceChat }) => ({ default: WorkspaceChat })),
)
const WorkspaceSettings = lazy(() =>
  import('./WorkspaceSettings').then(({ WorkspaceSettings }) => ({ default: WorkspaceSettings })),
)
const actionsWithConception = new Set<ModuleInput['action']>([
  'writing.advise',
  'writing.rewrite_selection',
  'writing.expand_plot',
  'review.chapter',
  'revision.targeted',
])
const presets = [
  '第三人称限定视角，克制白话，动作有因果，避免重复解释。',
  '对白：人物用词有区别，保留停顿与潜台词，不替人物解释每一句话。',
  '打斗：交代距离、资源、意图与代价，动作连续，不让能力突然升级。',
  '心理：由当前刺激引出选择，用行为承载情绪。',
  '环境：只写影响当下行动和感受的细节。',
  '日常：用具体事务推进关系，让小事保留后续作用。',
  '悬念：信息差有依据，线索可以回查，章末保留明确的问题。',
]

export function Writing({
  novel,
  branch,
  account,
  modelReady,
  notify,
  feedbackHandoff,
  onClearFeedback,
  conceptionHandoff,
  onConceptionSettings,
}: {
  novel: Novel
  branch: Branch
  account: string
  modelReady: boolean
  notify: Notify
  feedbackHandoff?: FeedbackHandoff
  onClearFeedback: () => void
  conceptionHandoff?: ConceptionHandoff
  onConceptionSettings: () => void
}) {
  const [importOpen, setImportOpen] = useState(false)
  const [chapters, setChapters] = useState<Chapter[]>([])
  const [loading, setLoading] = useState(true)
  const [opening, setOpening] = useState(false)
  const loadSequence = useRef(0)
  const [archived, setArchived] = useState<Chapter[]>([])
  const [archiveConfirmed, setArchiveConfirmed] = useState(false)
  const [chapter, setChapter] = useState<Chapter>()
  const [setting, setSetting] = useState<SettingKind>()
  const [settingsVersion, setSettingsVersion] = useState(0)
  const settingPane = useRef<HTMLElement>(null)
  const chapterPane = useRef<HTMLElement>(null)
  const lastPaneWasSetting = useRef(false)
  useEffect(() => {
    if (setting) settingPane.current?.focus()
    else if (lastPaneWasSetting.current) chapterPane.current?.focus()
    lastPaneWasSetting.current = !!setting
  }, [setting])
  const [editorKey, setEditorKey] = useState('')
  const [focusedEditorKey, setFocusedEditorKey] = useState('')
  const [text, setText] = useState('')
  const wordCount = useMemo(() => {
    let count = 0
    for (const character of text) {
      if (!/\s/u.test(character)) count++
    }
    return count
  }, [text])
  const [saved, setSaved] = useState('未打开章节')
  const [dirty, setDirty] = useState(0)
  const [selection, setSelection] = useState<Selection>({ start: 0, end: 0, text: '' })
  const [cursor, setCursor] = useState(0)
  const [editorScroll, setEditorScroll] = useState<number>()
  const chapterViews = useRef(new Map<string, { revision: number; cursor: number; scrollTop: number }>())
  const [note, setNote] = useState('')
  const reading = useRef({
    revision: 0,
    cursor: 0,
    note: '',
    pending: undefined as ReadingPosition | undefined,
  })
  const pendingReading = useRef<Promise<void> | null>(null)
  const [recovery, setRecovery] = useState<Draft>()
  const writingContext = useWritingContext(branch.id, chapter)
  const ledger = useContinuityLedger(branch.id, chapter)
  const [ledgerTarget, setLedgerTarget] = useState<LedgerTarget>()
  const [ledgerSelection, setLedgerSelection] = useState<{ start: number; end: number }>()
  const [action, setAction] = useState<ModuleInput['action']>(
    feedbackHandoff ? 'writing.rewrite_selection' : 'writing.advise',
  )
  const [instruction, setInstruction] = useState(feedbackHandoff?.text || '')
  const [plotContractCheck, setPlotContractCheck] = useState(false)
  const [sourceIdentityContext, setSourceIdentityContext] = useState(false)
  const [designScope, setDesignScope] = useState<'book' | 'chapter'>('book')
  const [directionBasis, setDirectionBasis] = useState<DirectionBasis>()
  const directionForm = useRef({ action, instruction })
  directionForm.current = { action, instruction }
  const instructionField = useRef<HTMLTextAreaElement>(null)
  useLeaveGuard(!!directionBasis, '已载入的作者选定方向')
  const [confirmedFeedbackRevision, setConfirmedFeedbackRevision] = useState<number | null>(null)
  const [fixed, setFixed] = useState('')
  const [profile, setProfile] = useState(presets[0])
  const [useConception, setUseConception] = useState(true)
  const conception = conceptionHandoff?.confirmation
  const conceptionSnapshotId = useConception && actionsWithConception.has(action) ? conception?.id : undefined
  const [seenContract, setSeenContract] = useState<AuthorContract>()
  const [seenGenre, setSeenGenre] = useState<GenreProfile>()
  const [references, setReferences] = useState<Excerpt[]>([])
  const [selectedRefs, setSelectedRefs] = useState<string[]>([])
  const [selectedTechniques, setSelectedTechniques] = useState<string[]>([])
  const [submittedTechniques, setSubmittedTechniques] = useState('')
  const techniqueEnabled = supportsTechniqueReferences(action)
  const techniqueSignature = selectedTechniques.join(',')
  useLeaveGuard(techniqueEnabled && techniqueSignature !== submittedTechniques, '未提交的本章手法选用')
  const assistantFormSignature = JSON.stringify([
    action,
    instruction,
    designScope,
    fixed,
    profile,
    selectedRefs,
    plotContractCheck,
    sourceIdentityContext,
    conceptionSnapshotId,
  ])
  const [submittedAssistantForm, setSubmittedAssistantForm] = useState(assistantFormSignature)
  useLeaveGuard(assistantFormSignature !== submittedAssistantForm, '未提交的写作辅助设置')
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [candidateId, setCandidateId] = useState('')
  const [tasks, setTasks] = useState<Task[]>([])
  const [stream, setStream] = useState('')
  const [transcript, setTranscript] = useState('')
  const [busy, setBusy] = useState(false)
  const [history, setHistory] = useState<{ id: string; revision: number; cause: string; content: string }[]>(
    [],
  )
  const [showHistory, setShowHistory] = useState(false)
  const [historyLoading, setHistoryLoading] = useState(false)
  const historyRequest = useRef(0)
  const candidateRequest = useRef(0)
  const [rename, setRename] = useState('')
  const current = useRef<Chapter | undefined>(undefined)
  const draftText = useRef('')
  const cachedDraft = useRef<Draft | undefined>(undefined)
  const cacheRequest = useRef(0)
  const saving = useRef<Promise<Chapter> | null>(null)
  const pendingRequest = useRef<{ signature: string; key: string } | null>(null)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])
  const refresh = async () => setChapters(await api(`/branches/${branch.id}/chapters`))
  async function loadChapter(id: string, at?: number, authoritative = false, leaveConfirmed = false) {
    if (current.current && current.current.id !== id && !leaveConfirmed && !confirmLeave()) return
    const token = ++loadSequence.current
    ++historyRequest.current
    setHistoryLoading(false)
    setOpening(true)
    try {
      const row = await api<Chapter>(`/chapters/${id}`)
      if (!alive.current || token !== loadSequence.current) return
      if (authoritative) await cacheDraft(row, row.content)
      const draft = authoritative ? undefined : await readDraft(id).catch(() => undefined)
      if (!alive.current || token !== loadSequence.current) return
      if (directionBasis && directionBasis.chapter_id !== id) {
        setDirectionBasis(undefined)
        setInstruction('')
        setAction('writing.advise')
      }
      if (row.id !== current.current?.id) {
        setSelectedTechniques([])
        setSubmittedTechniques('')
      }
      current.current = row
      setChapter(row)
      setLedgerTarget(undefined)
      setLedgerSelection(undefined)
      setRename(row.title)
      setHistory([])
      setShowHistory(false)
      setCandidateId('')
      setRecovery(undefined)
      const recoverable =
        draft && !draft.fromOtherWindow && draft.text !== row.content && draft.base === row.revision
      draftText.current = recoverable ? draft.text : row.content
      cachedDraft.current = recoverable ? draft : undefined
      const view = chapterViews.current.get(id)
      const sameDraft = !authoritative && view?.revision === row.revision
      const start = Math.min(draftText.current.length, at ?? (sameDraft ? view.cursor : 0))
      reading.current.cursor = start
      setSelection({ start, end: start, text: '' })
      setCursor(start)
      setEditorScroll(at === undefined && sameDraft ? view.scrollTop : undefined)
      setText(draftText.current)
      const nextEditorKey = id + ':' + row.revision + ':' + Date.now()
      setEditorKey(nextEditorKey)
      if (recoverable) {
        setSaved('已从此浏览器恢复草稿，等待保存到本机')
        setDirty((d) => d + 1)
      } else if (
        draft &&
        draft.text !== row.content &&
        (draft.base !== row.revision || draft.fromOtherWindow)
      ) {
        setRecovery(draft)
        setSaved('发现不同基线的浏览器草稿，请比较')
      } else setSaved(savedLocally)
      return nextEditorKey
    } finally {
      if (alive.current && token === loadSequence.current) setOpening(false)
    }
  }
  useEffect(() => {
    let active = true
    setLoading(true)
    void (async () => {
      try {
        const [rows, state] = await Promise.all([
          api<Chapter[]>(`/branches/${branch.id}/chapters`),
          api<ReadingPosition>(`/branches/${branch.id}/reading-state`),
        ])
        if (!active) return
        setChapters(rows)
        const cached = readWorkspacePosition(branch.id, rows, state)
        const handoff = rows.find((c) => c.id === feedbackHandoff?.chapter_id)
        const remembered = rows.find((c) => c.id === state.chapter_id)
        reading.current.note = cached?.note ?? state.note
        setNote(reading.current.note)
        reading.current.revision = state.revision
        if (handoff) await loadChapter(handoff.id, 0)
        else if (cached) {
          chapterViews.current.set(cached.chapterId, {
            revision: cached.chapterRevision,
            cursor: cached.cursor,
            scrollTop: cached.scrollTop ?? 0,
          })
          await loadChapter(cached.chapterId, cached.scrollTop === undefined ? cached.cursor : undefined)
        } else if (rows.length) await loadChapter(remembered?.id ?? rows[0].id, remembered ? state.cursor : 0)
      } catch (e) {
        if (active) notify(e)
      } finally {
        if (active) setLoading(false)
      }
    })()
    return () => {
      active = false
    }
  }, [branch.id])
  async function cacheDraft(chapter: Chapter, text: string) {
    const request = ++cacheRequest.current
    const draft = { id: chapter.id, base: chapter.revision, text, updated: Date.now() }
    const cached = await writeDraft(draft).then(
      () => true,
      () => false,
    )
    if (request === cacheRequest.current) cachedDraft.current = cached ? draft : undefined
    return cached
  }
  async function save(): Promise<Chapter> {
    if (saving.current) return saving.current
    const run = async (): Promise<Chapter> => {
      if (!current.current) throw new Error('请先新建或打开章节。')
      while (current.current!.content !== draftText.current) {
        const snapshot: Chapter = current.current!
        const content: string = draftText.current
        setSaved('正在保存到本机…')
        await cacheDraft(snapshot, content)
        const row: Chapter = await api<Chapter>(`/chapters/${snapshot.id}`, 'PUT', {
          base_revision: snapshot.revision,
          content,
        })
        const view = chapterViews.current.get(row.id)
        if (view?.revision === snapshot.revision)
          chapterViews.current.set(row.id, { ...view, revision: row.revision })
        current.current = row
        await cacheDraft(row, draftText.current)
        if (alive.current) setChapter(row)
      }
      if (alive.current) {
        setSaved(savedLocally)
        await refresh().catch(notify)
      }
      return current.current!
    }
    saving.current = run()
      .catch((error) => {
        const cached = cachedDraft.current
        setSaved(
          cached &&
            cached.id === current.current?.id &&
            cached.base === current.current.revision &&
            cached.text === draftText.current
            ? '保存失败，浏览器草稿保留；可下载草稿后比较'
            : '保存到本机失败，请先下载当前草稿',
        )
        throw error
      })
      .finally(() => {
        saving.current = null
      })
    return saving.current
  }
  function change(value: string) {
    const snapshot = current.current
    if (!snapshot) return
    draftText.current = value
    setText(value)
    setSaved(cachingDraft)
    setDirty((d) => d + 1)
    const pending = cacheDraft(snapshot, value)
    const request = cacheRequest.current
    void pending.then((cached) => {
      if (!alive.current || current.current !== snapshot || request !== cacheRequest.current) return
      setSaved((status) =>
        status === cachingDraft
          ? cached
            ? '草稿已缓存到此浏览器'
            : '浏览器草稿缓存失败，请保存到本机或下载草稿'
          : status,
      )
    })
  }
  useEffect(() => {
    function remember() {
      const chapter = current.current
      if (!chapter || draftText.current !== chapter.content) return
      const { cursor, note, revision, pending } = reading.current
      const view = chapterViews.current.get(chapter.id)
      rememberWorkspacePosition(branch.id, {
        chapterId: chapter.id,
        chapterRevision: chapter.revision,
        readingRevision: revision,
        cursor,
        note,
        pending,
        scrollTop: view?.revision === chapter.revision ? view.scrollTop : undefined,
      })
    }
    window.addEventListener('pagehide', remember)
    return () => {
      window.removeEventListener('pagehide', remember)
      remember()
    }
  }, [branch.id])
  useEffect(() => {
    if (!dirty || recovery) return
    const timer = setTimeout(() => void save().catch(notify), 900)
    return () => clearTimeout(timer)
  }, [dirty, recovery])
  useEffect(() => {
    const leave = (event: BeforeUnloadEvent) => {
      if (current.current && draftText.current !== current.current.content) {
        event.preventDefault()
        event.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', leave)
    return () => window.removeEventListener('beforeunload', leave)
  }, [])
  useEffect(() => {
    if (!chapter || !editorKey) return
    let active = true
    async function sync() {
      await pendingReading.current
      const row = current.current
      if (
        !active ||
        !row ||
        row.id !== chapter?.id ||
        row.revision !== chapter.revision ||
        draftText.current !== row.content
      )
        return
      const position = {
        chapter_id: row.id,
        cursor: Math.min(reading.current.cursor, row.content.length),
        note: reading.current.note,
        revision: reading.current.revision,
      }
      reading.current.pending = position
      pendingReading.current = api<{ revision: number }>(`/branches/${branch.id}/reading-state`, 'PUT', {
        chapter_id: position.chapter_id,
        cursor: position.cursor,
        note: position.note,
        base_revision: position.revision,
      })
        .then((row) => {
          reading.current.revision = row.revision
        })
        .catch((error) => {
          if (alive.current) notify(error)
        })
        .finally(() => {
          reading.current.pending = undefined
          pendingReading.current = null
        })
    }
    const timer = setTimeout(() => void sync(), 1200)
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [chapter?.id, chapter?.revision, selection.end, note])
  useEffect(() => {
    let active = true
    const poll = async () => {
      try {
        const [a, t, r] = await Promise.all([
          api<Artifact[]>(`/artifacts?branch_id=${branch.id}`),
          api<Task[]>(`/tasks?branch_id=${branch.id}`),
          api<Excerpt[]>(`/library/excerpts?novel_id=${novel.id}`),
        ])
        if (active) {
          setArtifacts((rows) => refreshArtifactList(rows, a))
          setTasks(t.filter((task) => task.kind !== 'workspace_chat'))
          setReferences(r)
        }
      } catch (e) {
        if (active) notify(e)
      }
    }
    void poll()
    const timer = setInterval(() => void poll(), 2500)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [branch.id, novel.id, notify])
  const activeTask = tasks.find((t) => t.status === 'running' || t.status === 'queued')
  useEffect(() => {
    setStream('')
    if (!activeTask) return
    const source = new EventSource(`/api/tasks/${activeTask.id}/events`)
    source.onmessage = (event) => {
      const row = JSON.parse(event.data)
      if (row.kind === 'text') setStream((value) => (value + row.payload.delta).slice(-60_000))
    }
    source.addEventListener('end', () => source.close())
    return () => source.close()
  }, [activeTask?.id])
  async function doAction(fn: () => Promise<unknown>) {
    if (busy) return
    setBusy(true)
    try {
      await fn()
    } catch (e) {
      notify(e)
    } finally {
      setBusy(false)
    }
  }
  function openCandidate(id: string) {
    if (!confirmLeave()) return
    const request = ++candidateRequest.current
    const chapterId = current.current?.id
    const chapterRequest = loadSequence.current
    void doAction(async () => {
      const target = await api<Artifact>(`/artifacts/${id}`)
      if (
        !alive.current ||
        request !== candidateRequest.current ||
        chapterRequest !== loadSequence.current ||
        chapterId !== current.current?.id
      )
        return
      if (target.branch_id !== branch.id || (target.chapter_id && target.chapter_id !== chapterId))
        throw new Error('候选目标已变化，请在对应章节重新选择。')
      setArtifacts((rows) =>
        rows.some((row) => row.id === target.id)
          ? rows.map((row) => (row.id === target.id ? latestArtifact(row, target) : row))
          : [target, ...rows],
      )
      setCandidateId(target.id)
    })
  }
  async function toggleHistory() {
    if (busy || opening || historyLoading || !current.current) return
    const chapterId = current.current.id
    const sequence = loadSequence.current
    const request = ++historyRequest.current
    setHistoryLoading(true)
    const saved: { chapter?: Chapter } = {}
    try {
      await doAction(async () => {
        saved.chapter = await save()
      })
      const row = saved.chapter
      const matchesTarget = () =>
        alive.current &&
        historyRequest.current === request &&
        loadSequence.current === sequence &&
        current.current?.id === chapterId &&
        current.current.revision === row?.revision
      if (!row || !matchesTarget()) return
      const versions = await api<typeof history>(`/chapters/${chapterId}/history`)
      if (!matchesTarget()) return
      setHistory(versions)
      setShowHistory((shown) => !shown)
    } catch (error) {
      if (alive.current && historyRequest.current === request) notify(error)
    } finally {
      if (alive.current && historyRequest.current === request) setHistoryLoading(false)
    }
  }
  function chooseSetting(kind: SettingKind) {
    if (busy || opening || loading || setting === kind || !confirmLeave(workspaceScopes)) return
    void doAction(async () => {
      if (current.current && draftText.current !== current.current.content) await save()
      setSetting(kind)
    })
  }
  function chooseChapter(id: string) {
    if (busy || opening || loading) return
    if (current.current?.id === id) {
      if (setting && confirmLeave(workspaceScopes)) setSetting(undefined)
      return
    }
    if (!(setting ? confirmLeave(workspaceScopes) : confirmLeave())) return
    void doAction(async () => {
      if (current.current && draftText.current !== current.current.content) await save()
      await loadChapter(id, undefined, false, true)
      if (current.current?.id === id) setSetting(undefined)
    })
  }
  function settingsSaved() {
    writingContext.reload()
    setSeenGenre(undefined)
    setSeenContract(undefined)
    setSettingsVersion((value) => value + 1)
  }
  async function createChapter() {
    if (!confirmLeave(setting ? workspaceScopes : undefined)) return
    await doAction(async () => {
      if (current.current) await save()
      const row = await api<Chapter>(`/branches/${branch.id}/chapters`, 'POST', {
        title: `第${chapters.length + 1}章`,
      })
      await refresh()
      const key = await loadChapter(row.id, undefined, false, true)
      if (key) {
        setSetting(undefined)
        setFocusedEditorKey(key)
      }
    })
  }
  async function loadAdviceDirection(source: Artifact, selected: string): Promise<boolean> {
    if (feedbackHandoff) throw new Error('请先退出反馈修订，再载入本章方向。')
    const row = current.current
    if (!row) throw new Error('请先打开建议对应章节。')
    const target = { novelId: novel.id, branchId: branch.id, chapterId: row.id, revision: row.revision }
    adviceDirectionBasis(source, target)
    const before = directionForm.current
    const chapterSequence = loadSequence.current
    const fresh = await api<Artifact>(`/artifacts/${source.id}`)
    if (!alive.current) return false
    const basis = adviceDirectionBasis(fresh, target, source.candidate_revision)
    if (current.current?.id !== row.id || chapterSequence !== loadSequence.current || opening)
      throw new Error('正在打开其他章节，选定方向保留，请回到来源章节后重试。')
    if (
      directionForm.current.action !== before.action ||
      directionForm.current.instruction !== before.instruction
    )
      throw new Error('读取来源期间补写表单已被编辑，选定方向仍保留，请核对后重新载入。')
    const direction = selected.trim()
    if (!direction) throw new Error('请先填写作者选定的本章方向。')
    if (
      before.instruction.trim() &&
      before.instruction !== direction &&
      !window.confirm(
        '将替换当前“情节或具体要求”，并切换为按情节补写整章。固定条件、写法和资料包保留；不会生成、采纳或改动正文。继续载入？',
      )
    )
      return false
    setDirectionBasis({ ...basis, loaded_revision: current.current.revision })
    setAction('writing.expand_plot')
    setInstruction(direction)
    instructionField.current?.focus()
    return true
  }
  async function generate(event: React.FormEvent) {
    event.preventDefault()
    await doAction(async () => {
      const row = current.current ? await save() : undefined
      if (seenGenre?.branch_id !== branch.id) throw new Error('正在读取本版本题材配置，请稍后再生成。')
      if (seenContract?.branch_id !== branch.id) throw new Error('正在读取本版本创作约定，请稍后再生成。')
      if (action === 'writing.rewrite_selection' && !selection.text)
        throw new Error('请在正文中选择要改写的文字。')
      const bookDesign = designActions.has(action) && designScope === 'book'
      if (designActions.has(action) && !bookDesign && !row)
        throw new Error('本章资料需要先打开目标章节；也可以改选本书设定。')
      if (feedbackHandoff) {
        if (action !== 'writing.rewrite_selection')
          throw new Error('反馈修订使用作者选区改写；若要执行其他动作，请先退出反馈修订。')
        if (!row) throw new Error('请先打开反馈对应章节。')
        checkFeedbackTarget(feedbackHandoff, {
          branchId: branch.id,
          chapterId: row.id,
          revision: row.revision,
          confirmedRevision: confirmedFeedbackRevision,
        })
      }
      if (directionBasis && action === 'writing.expand_plot') {
        if (!row) throw new Error('请先打开选定方向对应章节。')
        const target = { novelId: novel.id, branchId: branch.id, chapterId: row.id, revision: row.revision }
        checkDirectionTarget(directionBasis, target)
        const source = await api<Artifact>(`/artifacts/${directionBasis.advice_id}`)
        adviceDirectionBasis(source, target, directionBasis.candidate_revision)
      }
      const submittedInstruction = feedbackHandoff
        ? feedbackRevisionInstruction(feedbackHandoff, instruction)
        : directionBasis && action === 'writing.expand_plot'
          ? directionExpansionInstruction(directionBasis, instruction)
          : instruction
      if (submittedInstruction.length > 20_000)
        throw new Error('本次要求与来源记录合计过长，请缩短本次要求后再生成。')
      const data: ModuleInput = {
        include_scene_ranges: false,
        ...(conceptionSnapshotId ? { conception_snapshot_id: conceptionSnapshotId } : {}),
        action,
        novel_id: novel.id,
        branch_id: branch.id,
        chapter_id: bookDesign ? null : (row?.id ?? null),
        base_revision: bookDesign ? null : (row?.revision ?? null),
        instruction: submittedInstruction,
        plot_contract_check: plotAuditEnabled(action, plotContractCheck),
        source_identity_context: plotAuditEnabled(action, plotContractCheck) && sourceIdentityContext,
        fixed: fixed
          .split('\n')
          .map((v) => v.trim())
          .filter(Boolean),
        writing_profile: profile,
        genre_profile_basis: {
          source_artifact_id: seenGenre.source_artifact_id ?? null,
          catalog_version: seenGenre.catalog_version,
        },
        author_contract_basis: {
          source_artifact_id: seenContract.source_artifact_id,
          contract_version: seenContract.contract_version,
        },
        reference_ids: selectedRefs,
        ...(techniqueEnabled && row && selectedTechniques.length
          ? { technique_reference_ids: selectedTechniques }
          : {}),
        account,
        max_output_tokens: 8192,
        context_position: bookDesign ? 0 : selection.start,
        ...(action === 'writing.rewrite_selection'
          ? { selection_start: selection.start, selection_end: selection.end, selection_text: selection.text }
          : {}),
      }
      const signature = JSON.stringify(data)
      if (pendingRequest.current?.signature !== signature)
        pendingRequest.current = { signature, key: crypto.randomUUID() }
      data.client_request_id = pendingRequest.current.key
      const task = await api<Task>('/modules', 'POST', data)
      setSubmittedAssistantForm(assistantFormSignature)
      if (techniqueEnabled) setSubmittedTechniques(techniqueSignature)
      setTasks((rows) => [task, ...rows.filter((row) => row.id !== task.id)])
      pendingRequest.current = null
      setStream('')
    })
  }
  function downloadDraft() {
    const url = URL.createObjectURL(new Blob([draftText.current], { type: 'text/plain;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url
    a.download = (chapter?.title || '草稿') + '.txt'
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 2000)
  }
  const visibleArtifacts = artifacts.filter((a) => !a.chapter_id || a.chapter_id === chapter?.id)
  const candidate =
    visibleArtifacts.find((a) => a.id === candidateId) ||
    visibleArtifacts.find((a) => a.status === 'candidate') ||
    visibleArtifacts[0]
  const visitSourceCandidate = (target: Artifact) => {
    void doAction(async () => {
      if (!confirmLeave() || !target.chapter_id) return
      if (current.current?.id !== target.chapter_id) {
        if (current.current) await save()
        await loadChapter(target.chapter_id)
      }
      if (current.current?.id !== target.chapter_id) return
      setArtifacts((rows) =>
        rows.some((row) => row.id === target.id)
          ? rows.map((row) => (row.id === target.id ? latestArtifact(row, target) : row))
          : [target, ...rows],
      )
      setCandidateId(target.id)
    })
  }
  function visitLedger(target: LedgerTarget) {
    void doAction(async () => {
      if (!current.current || ledger.busy) return
      if (draftText.current !== current.current.content) await save()
      if (target.chapter_id !== current.current.id || target.source) await loadChapter(target.chapter_id)
      const row = current.current
      if (row?.id !== target.chapter_id) return
      setLedgerTarget({ ...target })
      if (!target.source) return
      const { start, end, quote, revision } = target.source
      if (row.revision !== revision || row.content.slice(start, end) !== quote) {
        ledger.reload()
        throw new Error('来源已改变。旧引文仍可在台账中查看。')
      }
      const editorOffset = (position: number) => row.content.slice(0, position).replace(/\r\n?/g, '\n').length
      setLedgerSelection({ start: editorOffset(start), end: editorOffset(end) })
    })
  }
  return (
    <div className="writing-layout">
      <aside className="chapter-sidebar" aria-label="作品目录">
        <div className="section-title">
          <span className="eyebrow">作品目录</span>
          <button onClick={createChapter} disabled={busy || loading}>
            ＋ 新章
          </button>
        </div>
        <h2>{novel.title}</h2>
        <p className="muted">
          {loading
            ? '正在读取章节…'
            : `${chapters.length} 章 · ${chapters.filter((c) => c.stale).length} 章待复检`}
        </p>
        <ManuscriptTree
          branch={branch}
          chapters={chapters}
          activeChapterId={chapter?.id}
          setting={setting}
          disabled={busy || opening || loading}
          chooseChapter={chooseChapter}
          chooseSetting={chooseSetting}
          notify={notify}
        />
        <div className="sidebar-bottom" hidden={!!setting}>
          <label>
            下次从这里继续
            <textarea
              rows={3}
              value={note}
              maxLength={5000}
              onChange={(e) => {
                reading.current.note = e.target.value
                setNote(e.target.value)
              }}
              placeholder="当前卡点、下一场景、要查的资料…"
            />
          </label>
          <details
            onToggle={(event) => {
              if (event.currentTarget.open)
                void api<Chapter[]>(`/branches/${branch.id}/archived`).then(setArchived).catch(notify)
            }}
          >
            <summary>章节整理与归档</summary>
            <div className="actions">
              {(['up', 'down'] as const).map((direction) => (
                <button
                  key={direction}
                  disabled={
                    !chapter ||
                    busy ||
                    (direction === 'up' ? chapters[0]?.id === chapter.id : chapters.at(-1)?.id === chapter.id)
                  }
                  onClick={() =>
                    void doAction(async () => {
                      const row = await save()
                      const context = await api<{ branch: Branch }>(`/branches/${branch.id}/context`)
                      await api(`/chapters/${row.id}/move`, 'POST', {
                        direction,
                        base_story_revision: context.branch.story_revision,
                      })
                      await refresh()
                      await loadChapter(row.id)
                    })
                  }
                >
                  {direction === 'up' ? '上移一章' : '下移一章'}
                </button>
              ))}
            </div>
            <label className="check">
              <input
                type="checkbox"
                checked={archiveConfirmed}
                onChange={(e) => setArchiveConfirmed(e.target.checked)}
              />
              将当前章移出目录，正文和历史保留，可恢复
            </label>
            <button
              disabled={!chapter || !archiveConfirmed || busy}
              onClick={() =>
                void doAction(async () => {
                  const row = await save()
                  await api(`/chapters/${row.id}/archive`, 'POST', {
                    base_revision: row.revision,
                    archived: true,
                  })
                  const rows = await api<Chapter[]>(`/branches/${branch.id}/chapters`)
                  setChapters(rows)
                  setArchiveConfirmed(false)
                  if (rows.length) await loadChapter(rows[0].id)
                  else {
                    current.current = undefined
                    setChapter(undefined)
                    draftText.current = ''
                    setText('')
                    setSaved('未打开章节')
                  }
                  setArchived(await api(`/branches/${branch.id}/archived`))
                })
              }
            >
              归档当前章节
            </button>
            {archived.map((c) => (
              <div className="excerpt" key={c.id}>
                <span>{c.title}</span>
                <button
                  disabled={busy}
                  onClick={() =>
                    void doAction(async () => {
                      await api(`/chapters/${c.id}/archive`, 'POST', {
                        base_revision: c.revision,
                        archived: false,
                      })
                      setArchived(await api(`/branches/${branch.id}/archived`))
                      await refresh()
                      await loadChapter(c.id)
                    })
                  }
                >
                  恢复到目录末尾
                </button>
              </div>
            ))}
          </details>
          <details>
            <summary>导入与导出</summary>
            <button onClick={() => setImportOpen(true)}>长篇拆章预览与导入</button>
            <label className="file-control">
              导入为新章节
              <input
                type="file"
                accept=".txt,.md"
                onChange={(event) => {
                  const file = event.target.files?.[0]
                  if (!file) return
                  void doAction(async () => {
                    const form = new FormData()
                    form.append('file', file)
                    const row = await api<Chapter>(`/branches/${branch.id}/import`, 'POST', form)
                    await refresh()
                    await loadChapter(row.id)
                  })
                  event.target.value = ''
                }}
              />
            </label>
            <a className="button" href={`/api/branches/${branch.id}/export?format=md`} download>
              导出 Markdown
            </a>
            <a className="button" href={`/api/branches/${branch.id}/export?format=txt`} download>
              导出 TXT
            </a>
          </details>
        </div>
      </aside>
      {setting && (
        <section ref={settingPane} className="workspace-setting-pane" aria-label="设定编辑" tabIndex={-1}>
          <Suspense fallback={<p role="status">正在打开设定…</p>}>
            <WorkspaceSettings
              key={setting}
              branch={branch}
              kind={setting}
              account={account}
              modelReady={modelReady}
              notify={notify}
              onSaved={settingsSaved}
            />
          </Suspense>
        </section>
      )}
      <section
        ref={chapterPane}
        className="manuscript"
        aria-label="正文写作"
        tabIndex={-1}
        hidden={!!setting}
      >
        {importOpen && (
          <ManuscriptImport
            key={branch.id}
            branchId={branch.id}
            notify={notify}
            onImported={refresh}
            onClose={() => setImportOpen(false)}
          />
        )}
        {chapter ? (
          <>
            <div className="manuscript-toolbar" role="group" aria-label="正文工具栏">
              <div className="manuscript-actions">
                <div className="manuscript-count" role="group" aria-label="字数" title="计入标点，忽略空白">
                  字数 <strong>{wordCount.toLocaleString('zh-CN')}</strong> 字
                </div>
                <button
                  className="primary"
                  onClick={() =>
                    void doAction(async () => {
                      await save()
                    })
                  }
                  disabled={busy || opening}
                >
                  保存
                </button>
                <button
                  onClick={() => void toggleHistory()}
                  disabled={busy || opening || historyLoading}
                  aria-busy={historyLoading}
                >
                  版本历史
                </button>
              </div>
              <div className="save-status" role="status">
                {historyLoading ? '正在读取当前章版本历史…' : saved}
              </div>
            </div>
            <div className="chapter-heading">
              <span>第 {chapter.position} 章</span>
              <label>
                <span className="sr-only">章节标题</span>
                <input
                  value={rename}
                  onChange={(e) => setRename(e.target.value)}
                  onBlur={() => {
                    if (rename.trim() && rename !== chapter.title)
                      void doAction(async () => {
                        await api(`/chapters/${chapter.id}`, 'PATCH', { title: rename.trim() })
                        await refresh()
                      })
                  }}
                  maxLength={200}
                />
              </label>
              <div className="muted">
                修订 {chapter.revision}
                {chapter.stale ? ' · 受前文修改影响，需重新审阅' : ''}
              </div>
            </div>
            <ChapterOutline
              key={chapter.id}
              branchId={branch.id}
              chapterId={chapter.id}
              context={writingContext}
              disabled={busy || opening}
            />
            {recovery && (
              <div className="notice">
                <strong>浏览器草稿与服务中的修订不同。</strong>
                <p>
                  草稿基线 {recovery.base}，当前修订 {chapter.revision}。请比较后决定，服务稿尚未改动。
                </p>
                <Difference before={chapter.content} after={recovery.text} />
                <button
                  onClick={() => {
                    setRecovery(undefined)
                    change(recovery.text)
                    setEditorKey(chapter.id + ':recovered:' + Date.now())
                  }}
                >
                  将此草稿载入编辑区
                </button>
                <button
                  onClick={() => {
                    setRecovery(undefined)
                    void cacheDraft(chapter, chapter.content)
                    setSaved(savedLocally)
                  }}
                >
                  使用本机服务稿
                </button>
              </div>
            )}
            {showHistory && (
              <div className="history">
                <h3>恢复会生成新修订，当前稿保留在历史中</h3>
                {history.map((h) => (
                  <details key={h.id}>
                    <summary>
                      修订 {h.revision} · {h.cause}
                    </summary>
                    <pre>{h.content || '（空稿）'}</pre>
                    <button
                      disabled={busy}
                      onClick={() =>
                        void doAction(async () => {
                          const row = await save()
                          await api(`/chapters/${chapter.id}/restore`, 'POST', {
                            base_revision: row.revision,
                            revision_id: h.id,
                          })
                          await loadChapter(chapter.id, 0, true)
                          await refresh()
                        })
                      }
                    >
                      恢复为新修订
                    </button>
                  </details>
                ))}
              </div>
            )}
            <h2 className="editor-label">正文</h2>
            <Editor
              id={editorKey}
              initial={text}
              cursor={cursor}
              scrollTop={editorScroll}
              revealSelection={ledgerSelection}
              readOnly={busy || opening}
              focusOnOpen={focusedEditorKey === editorKey}
              onChange={change}
              onSelection={(selected) => {
                reading.current.cursor = selected.end
                setSelection(selected)
              }}
              onViewPosition={(cursor, scrollTop) => {
                const row = current.current
                if (!setting && row?.id === chapter.id)
                  chapterViews.current.set(row.id, { revision: row.revision, cursor, scrollTop })
              }}
            />
            <div className="editor-footer">
              <span>
                {selection.text
                  ? `已选择 ${selection.end - selection.start} 个 UTF-16 单元，可局部改写`
                  : '直接写作，无需先建大纲或配置模型'}
              </span>
              <button onClick={downloadDraft}>下载当前草稿</button>
            </div>
          </>
        ) : (
          <div className="empty">
            <h2>{loading ? '正在读取作品' : '从一个场景开始'}</h2>
            <p>{loading ? '正在恢复章节与工作位置…' : '你可以先写，再慢慢整理人物和世界。'}</p>
            <button className="primary" onClick={createChapter} disabled={busy || loading}>
              新建第一章
            </button>
          </div>
        )}
      </section>
      <aside className="assistant-panel" aria-label="创作辅助">
        <Suspense fallback={<p role="status">正在打开 AI 对话…</p>}>
          <WorkspaceChat
            key={setting || chapter?.id || 'empty'}
            branch={branch}
            setting={setting}
            chapter={setting ? undefined : chapter}
            account={account}
            modelReady={modelReady}
            notify={notify}
            refreshKey={settingsVersion}
            draftDirty={!setting && (!!recovery || opening || text !== chapter?.content)}
          />
        </Suspense>
        <div className="writing-chapter-tools" hidden={!!setting}>
          <h2>本章工具与设置</h2>
          {chapter && !opening && (
            <>
              <ChapterSettings
                key={chapter.id}
                context={writingContext}
                branchId={branch.id}
                chapterId={chapter.id}
              />
              <ChapterTechniqueReferences
                key={chapter.id}
                branchId={branch.id}
                chapterId={chapter.id}
                selected={selectedTechniques}
                enabled={techniqueEnabled}
                disabled={busy || !!activeTask}
                onChange={setSelectedTechniques}
              />
              <ChapterChecks
                key={chapter.id}
                chapter={chapter}
                settings={writingContext.data?.settings}
                saved={text === chapter.content && !recovery}
                disabled={busy || !!activeTask}
                account={account}
                modelReady={modelReady}
                tasks={tasks}
                onTask={(task) => setTasks((rows) => [task, ...rows.filter((row) => row.id !== task.id)])}
              />
              <ContinuityLedger
                ledger={ledger}
                chapter={chapter}
                selection={selection}
                saved={text === chapter.content && !recovery}
                disabled={busy}
                settings={writingContext.data?.settings}
                target={ledgerTarget}
                visit={visitLedger}
              />
            </>
          )}
          <AuthorContractNotice
            key={`intent-${settingsVersion}`}
            branchId={branch.id}
            onSettings={() => chooseSetting('intent')}
            onResolved={setSeenContract}
          />
          <ChapterContext
            branchId={branch.id}
            chapter={chapter}
            artifacts={artifacts}
            onExtract={() => {
              setAction('memory.extract')
              setInstruction(
                '提取当前正文中有逐字依据的事件、人物认知、能力资源和伏笔；未知保留未知，候选先供作者核对。',
              )
            }}
          />
          {chapter && !opening && (
            <SceneTimePanel
              key={chapter.id}
              branchId={branch.id}
              chapter={chapter}
              cursor={selection.start}
              selection={selection}
              notify={notify}
            />
          )}
          <GenreProfileNotice
            key={`reader-${settingsVersion}`}
            branchId={branch.id}
            onSettings={() => chooseSetting('reader')}
            onResolved={setSeenGenre}
          />
          {conception && (
            <div className="notice">
              <label className="check">
                <input
                  type="checkbox"
                  checked={useConception}
                  onChange={(event) => setUseConception(event.target.checked)}
                />
                使用已确认构思（修订 {conception.revision}）
              </label>
              <p className="small">
                {actionsWithConception.has(action) ? '所选任务会使用构思设定。' : '当前动作使用独立资料。'}
              </p>
              <button type="button" onClick={onConceptionSettings}>
                回到构思工作台
              </button>
            </div>
          )}
          {feedbackHandoff && (
            <section className="notice" aria-label="手动反馈局部修订">
              <h3>手动反馈 → 作者选区修订</h3>
              <p>
                {feedbackHandoff.category} · {feedbackHandoff.title} · 来源修订{' '}
                {feedbackHandoff.source_revision}
              </p>
              <p>
                这是人工意见，未作为独立模型审稿结论。请核对原反馈，亲自在正文选择修订范围并确认要求；生成结果仍是待采用候选。
              </p>
              <p>{feedbackHandoff.text}</p>
              <blockquote>{feedbackHandoff.source_quote || '原反馈没有引用，请自行定位。'}</blockquote>
              {chapter?.id !== feedbackHandoff.chapter_id ? (
                <p role="alert">当前不是反馈对应章节，请从目录回到“{feedbackHandoff.title}”。</p>
              ) : chapter.revision !== feedbackHandoff.source_revision ? (
                <label className="check">
                  <input
                    type="checkbox"
                    checked={confirmedFeedbackRevision === chapter.revision}
                    onChange={(e) => setConfirmedFeedbackRevision(e.target.checked ? chapter.revision : null)}
                  />
                  我已核对当前保存稿修订 {chapter.revision}，并将重新选择范围；旧反馈引用不代表当前正文
                </label>
              ) : (
                <p>当前稿与反馈来源修订一致，仍需亲自选择范围。</p>
              )}
              <p>
                当前选区：{selection.start}—{selection.end}（UTF-16）。仅所选文字可被候选替换。
              </p>
              <button onClick={onClearFeedback}>退出反馈修订，保留正文</button>
            </section>
          )}
          <form onSubmit={generate}>
            {tasks[0]?.payload.instruction && actions.some(([id]) => id === tasks[0].payload.action) && (
              <button
                type="button"
                onClick={() => {
                  try {
                    const previous = tasks[0].payload
                    const restored =
                      previous.action === 'writing.expand_plot'
                        ? readDirectionExpansionInstruction(previous.instruction || '')
                        : undefined
                    if (restored) {
                      if (!current.current) throw new Error('请先打开方向任务对应章节。')
                      checkDirectionTarget(restored.basis, {
                        novelId: novel.id,
                        branchId: branch.id,
                        chapterId: current.current.id,
                        revision: current.current.revision,
                      })
                    }
                    if (directionBasis && !confirmLeave()) return
                    setDirectionBasis(restored?.basis)
                    setInstruction(restored?.instruction ?? previous.instruction ?? '')
                    setAction(previous.action || 'writing.advise')
                    setPlotContractCheck(
                      plotAuditEnabled(previous.action || '', previous.plot_contract_check),
                    )
                    setSourceIdentityContext(previous.source_identity_context === true)
                    if (previous.action && designActions.has(previous.action))
                      setDesignScope(
                        'chapter_id' in previous && typeof previous.chapter_id === 'string'
                          ? 'chapter'
                          : 'book',
                      )
                    setFixed(previous.fixed?.join('\n') || '')
                    setProfile(previous.writing_profile || presets[0])
                  } catch (error) {
                    notify(error)
                  }
                }}
              >
                恢复最近任务的输入
              </button>
            )}
            <label>
              这次要做什么
              <select
                value={action}
                onChange={(e) => {
                  const next = e.target.value as ModuleInput['action']
                  if (designActions.has(next)) setDesignScope('book')
                  if (!plotAuditEnabled(next, true)) setPlotContractCheck(false)
                  setAction(next)
                }}
              >
                {actions.map(([id, title]) => (
                  <option key={id} value={id}>
                    {title}
                  </option>
                ))}
              </select>
            </label>
            {designActions.has(action) && (
              <>
                <label>
                  设定作用范围
                  <select
                    value={designScope}
                    onChange={(e) => setDesignScope(e.target.value as 'book' | 'chapter')}
                  >
                    <option value="book">本书设定（采用后供后续任务使用）</option>
                    <option value="chapter">本章资料（仅保存参考）</option>
                  </select>
                </label>
                <p className="small muted">
                  {designScope === 'book'
                    ? '生成独立的本书设定候选，作者采用后才供当前工作版本的后续任务使用。'
                    : `生成${chapter ? `“${chapter.title}”的` : '当前章的'}参考资料，采用仅保存在本章，不进入全书设定。`}
                  世界、人物和粗纲都是创作安排，不自动成为已发生的故事事实；已有章级资料不会自动升级为本书设定。
                </p>
              </>
            )}
            {directionBasis && (
              <section className="notice" aria-label="作者选定方向来源">
                <p role="status">
                  方向已载入本章补写表单；载入操作本身未生成或改动正文。可继续编辑情节要求。
                </p>
                <p className="small muted">
                  来源建议 {directionBasis.advice_id} · 候选版本 {directionBasis.candidate_revision} ·
                  原稿修订 {directionBasis.source_revision ?? '未绑定'}；目标章 {chapter?.title}（
                  {directionBasis.chapter_id}），载入时保存稿修订 {directionBasis.loaded_revision}
                  。建议是启发记录，不是本书设定。
                </p>
                <button type="button" onClick={() => setDirectionBasis(undefined)}>
                  清除方向来源，保留当前要求
                </button>
              </section>
            )}
            <label>
              情节或具体要求
              <textarea
                ref={instructionField}
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                rows={5}
                required
                maxLength={20_000}
                placeholder="例如：两人在渡口议价，交易失败且不发生战斗。补足试探和收场。"
              />
            </label>
            {plotAuditEnabled(action, true) && (
              <PlotAuditOption
                checked={plotContractCheck}
                disabled={busy}
                onChange={setPlotContractCheck}
                identityChecked={sourceIdentityContext}
                onIdentityChange={setSourceIdentityContext}
              />
            )}
            <details>
              <summary>固定条件、写法与资料包</summary>
              <label>
                必须保持的条件（每行一条）
                <textarea value={fixed} onChange={(e) => setFixed(e.target.value)} rows={3} />
              </label>
              <label>
                常用写法
                <select
                  onChange={(e) => setProfile(e.target.value)}
                  value={presets.includes(profile) ? profile : ''}
                >
                  <option value="" disabled>
                    自定义写法
                  </option>
                  {presets.map((p, i) => (
                    <option key={p} value={p}>
                      {['基础', '对白', '打斗', '心理', '环境', '日常', '悬念'][i]}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                表达设置
                <textarea value={profile} onChange={(e) => setProfile(e.target.value)} rows={3} />
              </label>
              <fieldset>
                <legend>主动发送的摘录</legend>
                {references.length ? (
                  references.map((r) => (
                    <label className="check" key={r.id}>
                      <input
                        type="checkbox"
                        checked={selectedRefs.includes(r.id)}
                        onChange={(e) =>
                          setSelectedRefs((ids) =>
                            e.target.checked ? [...ids, r.id] : ids.filter((id) => id !== r.id),
                          )
                        }
                      />
                      <span>
                        {r.title}：{r.quote.slice(0, 70)}
                      </span>
                    </label>
                  ))
                ) : (
                  <p className="muted">在资料书库保存摘录后，可在这里选入资料包。</p>
                )}
              </fieldset>
            </details>
            <p className="small muted">
              {designActions.has(action) && designScope === 'book'
                ? '发送已采用本书设定、当前工作版本的有效故事资料及最近正文、主动选入的资料；不限定在当前打开章之前。'
                : '发送当前章、有效前文与主动选入的资料。'}
              候选与正文分开保存。
            </p>
            <button
              className="primary wide"
              disabled={
                busy ||
                loading ||
                !modelReady ||
                !!activeTask ||
                seenGenre?.branch_id !== branch.id ||
                seenContract?.branch_id !== branch.id
              }
            >
              {!modelReady
                ? '请先配置模型账户和单价'
                : busy
                  ? '正在提交…'
                  : activeTask
                    ? '本版本有任务执行中'
                    : seenGenre?.branch_id !== branch.id || seenContract?.branch_id !== branch.id
                      ? '正在读取本书约定…'
                      : '生成候选 →'}
            </button>
          </form>
          {activeTask && (
            <div className="notice" role="status">
              <strong>
                {correctionKindLabel(activeTask.kind) ||
                  (activeTask.kind === chapterCheckKind ? '单项校验 · ' : '') ||
                  (activeTask.kind === 'candidate_recheck' ? '候选复检 · ' : '')}
                {label(activeTask.status)}
              </strong>
              <p>关闭浏览器不取消本机任务。</p>
              {activeTask.kind === 'candidate_recheck' && (
                <p>正在按原任务冻结契约复核候选，不自动改稿、采纳或继续整书。</p>
              )}
              <SourceCorrectionTaskNotice task={activeTask} />
              {activeTask.kind !== SOURCE_CORRECTION_KIND && (
                <FrozenPlotAuditPolicy
                  enabled={
                    activeTask.kind === CORRECTED_RECHECK_KIND || activeTask.payload.plot_contract_check
                  }
                  identityEnabled={activeTask.payload.source_identity_context}
                  identityVersion={activeTask.result.source_identity_context_version}
                  flowVersion={activeTask.result.flow_source_entailment_version}
                  version={activeTask.result.plot_contract_version}
                  mode={
                    activeTask.kind === CORRECTED_RECHECK_KIND
                      ? 'assisted'
                      : candidateRecheckSourceMode(activeTask)
                  }
                />
              )}
              {activeTask.result.author_contract_snapshot && (
                <details>
                  <summary>本次任务冻结的创作约定</summary>
                  <AuthorContractDetails contract={activeTask.result.author_contract_snapshot} />
                </details>
              )}
              {activeTask.result.conception_snapshot && (
                <p className="small">
                  本次任务使用了构思修订 {activeTask.result.conception_snapshot.revision}。
                </p>
              )}
              {activeTask.result.genre_profile_snapshot &&
                !activeTask.result.genre_profile_snapshot.legacy_unconfigured && (
                  <details>
                    <summary>本次任务冻结的题材配置</summary>
                    <GenreProfileDetails profile={activeTask.result.genre_profile_snapshot} />
                  </details>
                )}
              <button
                onClick={() =>
                  void doAction(async () => {
                    await api(`/tasks/${activeTask.id}/cancel`, 'POST')
                    setTasks(await api(`/tasks?branch_id=${branch.id}`))
                  })
                }
              >
                {activeTask.cancel ? '正在取消…' : '取消生成'}
              </button>
              {stream && (
                <details>
                  <summary>查看流式草稿（尚未采纳）</summary>
                  <pre className="stream">{stream}</pre>
                </details>
              )}
            </div>
          )}
          {tasks[0]?.status === 'blocked_runtime' && (
            <div className="notice">
              <strong>最近任务已停止</strong>
              <p>{tasks[0].error}</p>
              <p className="small">可能已发生的费用保留在账本中。修改指令后可明确发起新任务。</p>
            </div>
          )}
          <section className="candidates">
            <details>
              <summary>任务历史与失败产物</summary>
              {tasks.slice(0, 10).map((task) => (
                <div className="excerpt" key={task.id}>
                  <strong>
                    {label(task.status)} · {task.id.slice(0, 8)}
                  </strong>
                  <p>{task.error}</p>
                  <SourceCorrectionTaskNotice task={task} />
                  {task.kind !== SOURCE_CORRECTION_KIND && (
                    <FrozenPlotAuditPolicy
                      enabled={task.kind === CORRECTED_RECHECK_KIND || task.payload.plot_contract_check}
                      identityEnabled={task.payload.source_identity_context}
                      identityVersion={task.result.source_identity_context_version}
                      flowVersion={task.result.flow_source_entailment_version}
                      version={task.result.plot_contract_version}
                      mode={
                        task.kind === CORRECTED_RECHECK_KIND ? 'assisted' : candidateRecheckSourceMode(task)
                      }
                    />
                  )}
                  <button
                    disabled={busy}
                    onClick={() =>
                      void doAction(async () => {
                        const data = await api<{
                          steps: { fingerprint: string; result: { text: string } }[]
                          partials: { payload: { text: string } }[]
                          scope: string
                        }>(`/tasks/${task.id}/transcript`)
                        setTranscript(
                          data.scope +
                            '\n\n' +
                            data.partials
                              .map((p) => '中断输出：\n' + p.payload.text)
                              .concat(
                                data.steps.map(
                                  (s) => '步骤 ' + s.fingerprint.slice(0, 12) + '：\n' + s.result.text,
                                ),
                              )
                              .join('\n\n'),
                        )
                      })
                    }
                  >
                    查看保存的步骤与中断输出
                  </button>
                </div>
              ))}
              {transcript && <pre className="stream">{transcript}</pre>}
            </details>
            <h3>候选与检查结果</h3>
            {visibleArtifacts.length ? (
              <>
                <label>
                  比较候选
                  <select
                    value={candidate?.id || ''}
                    disabled={busy || opening}
                    onChange={(e) => openCandidate(e.target.value)}
                  >
                    {visibleArtifacts.map((a, i) => (
                      <option key={a.id} value={a.id}>
                        候选 {visibleArtifacts.length - i} ·{' '}
                        {correctionKindLabel(a.kind) ||
                          (a.kind === 'candidate_recheck' ? '候选复检报告' : a.kind)}{' '}
                        · {isCorrectionReceiptKind(a.kind) ? '只读记录' : label(a.status)}
                      </option>
                    ))}
                  </select>
                </label>
                {candidate && isCorrectionReceiptKind(candidate.kind) ? (
                  <SourceCorrectionArtifact
                    key={candidate.id}
                    artifact={candidate}
                    branchId={branch.id}
                    disabled={busy || opening}
                    onTarget={visitSourceCandidate}
                  />
                ) : candidate?.kind === 'candidate_recheck' ? (
                  <CandidateRecheckReport
                    key={candidate.id}
                    artifact={candidate}
                    branchId={branch.id}
                    disabled={busy || opening}
                    onTarget={(target) => {
                      void doAction(async () => {
                        if (!confirmLeave() || !target.chapter_id) return
                        if (current.current?.id !== target.chapter_id) {
                          if (current.current) await save()
                          await loadChapter(target.chapter_id)
                        }
                        if (current.current?.id !== target.chapter_id) return
                        setArtifacts((rows) =>
                          rows.some((row) => row.id === target.id)
                            ? rows.map((row) => (row.id === target.id ? latestArtifact(row, target) : row))
                            : [target, ...rows],
                        )
                        setCandidateId(target.id)
                      })
                    }}
                  />
                ) : candidate ? (
                  <Candidate
                    key={candidate.id}
                    artifact={candidate}
                    onIntentSettings={() => chooseSetting('intent')}
                    novelId={novel.id}
                    branchId={branch.id}
                    chapter={chapter}
                    account={account}
                    modelReady={modelReady}
                    revisionDisabled={!!activeTask || busy}
                    recheckDisabled={!!activeTask || opening}
                    sourceArtifacts={artifacts}
                    onSourceStarted={(task) => {
                      setTasks((rows) => [task, ...rows.filter((row) => row.id !== task.id)])
                      setCandidateId(candidate.id)
                    }}
                    onSourceRefresh={async () => {
                      const target = current.current?.id
                      const load = loadSequence.current
                      const comparison = candidateRequest.current
                      const latest = await api<Artifact[]>(`/artifacts?branch_id=${branch.id}`)
                      if (
                        alive.current &&
                        current.current?.id === target &&
                        loadSequence.current === load &&
                        candidateRequest.current === comparison
                      )
                        setArtifacts((rows) => refreshArtifactList(rows, latest))
                    }}
                    manuscriptUnsaved={text !== chapter?.content || !!recovery}
                    directionDisabled={!!activeTask || busy || opening || !!feedbackHandoff}
                    onDirection={loadAdviceDirection}
                    notify={notify}
                    beforeRun={save}
                    onStarted={(task) => {
                      setTasks((rows) => [task, ...rows.filter((row) => row.id !== task.id)])
                      setCandidateId('')
                    }}
                    busy={busy}
                    perform={doAction}
                    onAccept={async (request) => {
                      const target = current.current?.id
                      const load = loadSequence.current
                      const comparison = candidateRequest.current
                      const matches = () =>
                        alive.current &&
                        current.current?.id === target &&
                        loadSequence.current === load &&
                        candidateRequest.current === comparison
                      if (current.current) await save()
                      if (!matches()) return
                      await api(`/artifacts/${candidate.id}/accept`, 'POST', request)
                      if (!matches()) return
                      if (chapter) await loadChapter(chapter.id, 0, true)
                      if (!alive.current || current.current?.id !== target) return
                      const latest = await api<Artifact[]>(`/artifacts?branch_id=${branch.id}`)
                      setArtifacts((rows) => refreshArtifactList(rows, latest))
                      await refresh()
                    }}
                    onUpdate={async () => {
                      const latest = await api<Artifact[]>(`/artifacts?branch_id=${branch.id}`)
                      setArtifacts((rows) => refreshArtifactList(rows, latest))
                    }}
                  />
                ) : null}
              </>
            ) : (
              <p className="empty-note">有想法时亲自写。需要帮助时，再生成一份候选。</p>
            )}
          </section>
        </div>
      </aside>
      {!setting && <TodayBar ledger={ledger} disabled={busy || opening || ledger.busy} visit={visitLedger} />}
    </div>
  )
}

function Candidate({
  artifact,
  onIntentSettings,
  revisionDisabled,
  recheckDisabled,
  sourceArtifacts,
  onSourceStarted,
  onSourceRefresh,
  manuscriptUnsaved,
  novelId,
  branchId,
  chapter,
  account,
  modelReady,
  directionDisabled,
  onDirection,
  notify,
  beforeRun,
  onStarted,
  busy,
  perform,
  onAccept,
  onUpdate,
}: {
  artifact: Artifact
  onIntentSettings: () => void
  revisionDisabled: boolean
  recheckDisabled: boolean
  sourceArtifacts: Artifact[]
  onSourceStarted: (task: Task) => void
  onSourceRefresh: () => Promise<void>
  manuscriptUnsaved: boolean
  novelId: string
  branchId: string
  chapter?: Chapter
  account: string
  modelReady: boolean
  directionDisabled: boolean
  onDirection: (source: Artifact, selected: string) => Promise<boolean>
  notify: Notify
  beforeRun: () => Promise<Chapter | undefined>
  onStarted: (task: Task) => void
  busy: boolean
  perform: (fn: () => Promise<unknown>) => Promise<void>
  onAccept: (request: CandidateAcceptanceInput) => Promise<void>
  onUpdate: () => Promise<void>
}) {
  const [edit, setEdit] = useState(artifact.payload.text || '')
  const [editRevision, setEditRevision] = useState(artifact.candidate_revision)
  const candidateChanged = editRevision !== artifact.candidate_revision
  const memorySelection = useMemorySelection(artifact, `${novelId}/${branchId}/${chapter?.id}`)
  const [selectedDirection, setSelectedDirection] = useState('')
  useLeaveGuard(!!selectedDirection.trim(), '尚未载入的作者选定方向')
  let directionError = ''
  if (artifact.kind === 'advice') {
    try {
      if (!chapter) throw new Error('请先打开建议对应章节。')
      adviceDirectionBasis(artifact, {
        novelId,
        branchId,
        chapterId: chapter.id,
        revision: chapter.revision,
      })
    } catch (error) {
      directionError = error instanceof Error ? error.message : '方向来源暂不可核对。'
    }
  }
  const [baseline, setBaseline] = useState<string>()
  const [comparisonError, setComparisonError] = useState('')
  useEffect(() => {
    let active = true
    if (artifact.chapter_id && artifact.base_revision !== null) {
      void api<{ revision: number; content: string }[]>(`/chapters/${artifact.chapter_id}/history`)
        .then((rows) => {
          if (!active) return
          const original = rows.find((row) => row.revision === artifact.base_revision)
          if (original) setBaseline(original.content)
          else setComparisonError('生成时的原稿版本暂不可读取；候选内容仍保留。')
        })
        .catch(() => {
          if (active) setComparisonError('原稿版本读取失败，请重新打开候选。')
        })
    }
    return () => {
      active = false
    }
  }, [artifact.id, artifact.chapter_id, artifact.base_revision])
  const after =
    artifact.kind === 'text_patch'
      ? (baseline || '').slice(0, artifact.payload.start) +
        edit +
        (baseline || '').slice(artifact.payload.end)
      : edit
  const plotAudit = plotAuditFromPayload(artifact.payload)
  const plotAuditHistorical =
    plotAudit.historical ||
    candidateChanged ||
    (artifact.payload.text !== undefined && edit !== artifact.payload.text) ||
    (artifact.kind === 'review' &&
      (chapter?.revision !== artifact.base_revision || chapter?.id !== artifact.chapter_id))
  const auditedText =
    artifact.kind === 'review'
      ? chapter?.id === artifact.chapter_id
        ? chapter.content
        : undefined
      : artifact.payload.text === undefined
        ? undefined
        : edit
  return (
    <div className="candidate">
      {designKinds.has(artifact.kind) && (
        <p className="notice" role="status">
          {artifact.chapter_id
            ? '本章资料：采用只保存为本章参考，不进入全书设定。'
            : '本书设定：作者采用后供当前工作版本的后续任务使用。'}
          这是创作安排，不自动写为已发生的故事事实。
        </p>
      )}
      <p className="small muted">
        {label(artifact.status)} · 基线修订 {artifact.base_revision ?? '设定'}。改变原文后采纳会再次校验。
      </p>
      {candidateChanged && (
        <div className="notice" role="status">
          <p>候选已在其他窗口更新。本地编辑仍保留，可先复制，再载入已保存的候选进行比较。</p>
          <button
            onClick={() =>
              void perform(async () => {
                await navigator.clipboard.writeText(edit)
              })
            }
          >
            复制本地候选
          </button>
          <button
            onClick={() => {
              setEdit(artifact.payload.text || '')
              setEditRevision(artifact.candidate_revision)
            }}
          >
            载入已保存候选
          </button>
        </div>
      )}
      {artifact.payload.text !== undefined ? (
        <>
          <label>
            候选内容
            <textarea
              rows={9}
              value={edit}
              onChange={(e) => setEdit(e.target.value)}
              readOnly={artifact.status !== 'candidate'}
            />
          </label>
          <p className="small muted">候选正文：{[...edit].filter((c) => !/\s/u.test(c)).length} 非空白字符</p>
          {artifact.status === 'candidate' && edit !== artifact.payload.text && (
            <button
              disabled={busy || candidateChanged}
              onClick={() =>
                void perform(async () => {
                  const updated = await api<Artifact>(`/artifacts/${artifact.id}`, 'PATCH', {
                    text: edit,
                    candidate_revision: editRevision,
                  })
                  setEditRevision(updated.candidate_revision)
                  await onUpdate()
                })
              }
            >
              保存候选编辑（原检查失效）
            </button>
          )}
          {['text_patch', 'chapter_draft'].includes(artifact.kind) && (
            <details>
              <summary>查看原文与候选差异</summary>
              <div className="diff-labels">
                <span>生成时原稿（修订 {artifact.base_revision}）</span>
                <span>候选正文</span>
              </div>
              {baseline !== undefined ? (
                <Difference before={baseline} after={after} beforeLabel="生成时原稿" />
              ) : (
                <p role="status">{comparisonError || '正在读取生成时原稿…'}</p>
              )}
            </details>
          )}
        </>
      ) : artifact.kind === 'review' ? (
        <details>
          <summary>审稿原始记录</summary>
          <pre className="report">{JSON.stringify(artifact.payload, null, 2)}</pre>
        </details>
      ) : (
        <pre className="report">{JSON.stringify(artifact.payload, null, 2)}</pre>
      )}
      {(artifact.kind === 'review' || !!artifact.payload.review) && (
        <RevisionPanel
          review={artifact}
          novelId={novelId}
          chapter={chapter}
          account={account}
          modelReady={modelReady}
          disabled={revisionDisabled}
          notify={notify}
          beforeRun={beforeRun}
          onStarted={onStarted}
        />
      )}
      <ConditionalReviewNotice
        value={artifact.kind === 'review' ? artifact.payload : artifact.payload.review}
      />
      {!!artifact.payload.review && (
        <details>
          <summary>查看本章检查依据</summary>
          <pre className="report">{JSON.stringify(artifact.payload.review, null, 2)}</pre>
        </details>
      )}
      {artifact.kind === 'chapter_draft' && (
        <CandidateRecheckAction
          artifact={artifact}
          chapter={chapter}
          branchId={branchId}
          account={account}
          modelReady={modelReady}
          disabled={recheckDisabled}
          busy={busy}
          unsaved={manuscriptUnsaved || candidateChanged || edit !== (artifact.payload.text || '')}
          perform={perform}
          onStarted={onStarted}
        />
      )}
      {artifact.kind === 'chapter_draft' && artifact.status === 'candidate' && (
        <SourceCorrectionPanel
          artifact={artifact}
          chapter={chapter}
          novelId={novelId}
          branchId={branchId}
          account={account}
          modelReady={modelReady}
          disabled={recheckDisabled || busy}
          unsaved={manuscriptUnsaved || candidateChanged || edit !== (artifact.payload.text || '')}
          artifacts={sourceArtifacts}
          onTask={onSourceStarted}
          onRefresh={onSourceRefresh}
        />
      )}
      {['text_patch', 'chapter_draft'].includes(artifact.kind) && (
        <CandidateHistory artifactId={artifact.id} />
      )}
      {artifact.kind !== 'reference_comparison' && (
        <PlotAuditDetails
          value={plotAudit.value}
          candidateRevision={artifact.candidate_revision}
          candidateText={auditedText}
          historical={plotAuditHistorical}
          savedChapter={artifact.kind === 'review' ? chapter : undefined}
          expectSavedChapter={artifact.kind === 'review'}
          baseRevision={artifact.kind === 'review' ? artifact.base_revision : undefined}
        />
      )}
      <ReferenceComparison
        value={
          artifact.kind === 'reference_comparison' ? artifact.payload : artifact.payload.reference_comparison
        }
        candidateText={auditedText}
        historical={plotAuditHistorical}
        candidate={artifact.kind === 'reference_comparison' ? undefined : artifact}
        branchId={branchId}
      />
      {!!artifact.payload.previous_revision_trace && (
        <details>
          <summary>编辑前的原问题复检（已失效）</summary>
          <p className="small muted">候选已被编辑，以下是历史依据，不能作为当前候选通过的结论。</p>
          <RevisionTraceDetails trace={artifact.payload.previous_revision_trace} />
        </details>
      )}
      {!!artifact.basis.author_contract && (
        <details>
          <summary>候选生成时的创作约定</summary>
          <AuthorContractDetails contract={artifact.basis.author_contract as AuthorContract} />
        </details>
      )}
      {artifact.basis.genre_profile && !artifact.basis.genre_profile.legacy_unconfigured && (
        <details>
          <summary>候选生成时的频道与题材</summary>
          <GenreProfileDetails profile={artifact.basis.genre_profile} />
        </details>
      )}
      {artifact.kind === 'intent' && !!artifact.payload.author_text && (
        <div className="notice">
          <p>此结果是AI整理提案。请在创作约定表单中核对原话、推导和锁定项后确认。</p>
          <button type="button" onClick={onIntentSettings}>
            前往创作约定核对提案
          </button>
        </div>
      )}
      {artifact.kind === 'advice' && (
        <section aria-label="选择方向后补写">
          <h4>选择方向后补写</h4>
          <p className="small muted">
            从建议中挑选一种方向，改成你愿意采用的情节。只载入本章补写表单，由你再决定是否生成。
          </p>
          <label>
            作者选定的本章方向
            <textarea
              value={selectedDirection}
              onChange={(event) => setSelectedDirection(event.target.value)}
              rows={4}
              maxLength={18_000}
            />
          </label>
          <p className="small muted">
            来源建议 {artifact.id} · 候选版本 {artifact.candidate_revision} · 目标章{' '}
            {chapter?.title || '未打开'}。固定条件、写法和资料包保留。
          </p>
          {directionError && <p role="status">{directionError}</p>}
          {chapter && artifact.base_revision !== chapter.revision && (
            <p className="small muted">
              建议基于原稿修订 {artifact.base_revision ?? '未绑定'}，当前保存稿已是修订 {chapter.revision}
              ；请按当前正文重新核对方向。
            </p>
          )}
          <button
            type="button"
            disabled={
              directionDisabled ||
              !!directionError ||
              candidateChanged ||
              edit !== (artifact.payload.text || '') ||
              !selectedDirection.trim()
            }
            onClick={() =>
              void perform(async () => {
                if (await onDirection(artifact, selectedDirection)) setSelectedDirection('')
              })
            }
          >
            将选定方向载入本章补写（不生成）
          </button>
        </section>
      )}
      {artifact.status === 'candidate' && (
        <MemorySelectionPanel
          selection={memorySelection}
          disabled={busy || candidateChanged || edit !== (artifact.payload.text || '')}
        />
      )}
      <MemoryAcceptanceRecord key={artifact.id} artifact={artifact} />
      <div className="actions">
        <button
          className="primary"
          disabled={
            artifact.kind.startsWith('reference_') ||
            artifact.kind === 'story_extraction' ||
            (artifact.kind === 'intent' && !!artifact.payload.author_text) ||
            busy ||
            candidateChanged ||
            !!memorySelection.error ||
            artifact.status !== 'candidate' ||
            edit !== (artifact.payload.text || '')
          }
          onClick={() =>
            void perform(async () => {
              const request = memorySelection.input('manual-' + artifact.id)
              await onAccept(request)
              memorySelection.acknowledge(request)
            })
          }
        >
          {['text_patch', 'chapter_draft'].includes(artifact.kind)
            ? '采纳到正文'
            : artifact.kind === 'memory'
              ? '采纳到故事记录'
              : artifact.kind === 'advice'
                ? '保存建议供参考'
                : designKinds.has(artifact.kind)
                  ? artifact.chapter_id
                    ? '保存为本章参考'
                    : '采用为本书设定'
                  : '采用此结果'}
        </button>
        <button
          disabled={busy || artifact.status !== 'candidate'}
          onClick={() =>
            void perform(async () => {
              await api(`/artifacts/${artifact.id}/reject`, 'POST', { candidate_revision: editRevision })
              await onUpdate()
            })
          }
        >
          拒绝
        </button>
      </div>
      <p className="small muted">正文采纳后，可在版本历史中恢复采纳前稿件。</p>
      {artifact.kind === 'advice' && (
        <p className="small muted">保存建议只记录本结果，不会自动改动本书约定、正文或故事事实。</p>
      )}
    </div>
  )
}
