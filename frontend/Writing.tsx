import { useEffect, useRef, useState } from 'react'
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
import { readDraft, writeDraft, type Draft } from './drafts'
import { GenreProfileDetails, GenreProfileNotice } from './GenreProfile'
import { AuthorContractDetails, AuthorContractNotice, type AuthorContract } from './AuthorIntent'
import { ChapterContext } from './ChapterContext'
import { RevisionPanel, RevisionTraceDetails } from './RevisionPanel'
import { ReferenceComparison } from './ReferenceWorkspace'
import { ManuscriptImport } from './ManuscriptImport'
import { SceneTimePanel } from './SceneTimePanel'
import { confirmLeave, useLeaveGuard } from './leaveGuard'
import { checkFeedbackTarget, feedbackRevisionInstruction, type FeedbackHandoff } from './feedbackRevision'
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
  onGenreSettings,
  feedbackHandoff,
  onClearFeedback,
}: {
  novel: Novel
  branch: Branch
  account: string
  modelReady: boolean
  notify: Notify
  onGenreSettings: () => void
  feedbackHandoff?: FeedbackHandoff
  onClearFeedback: () => void
}) {
  const [importOpen, setImportOpen] = useState(false)
  const [chapters, setChapters] = useState<Chapter[]>([])
  const [loading, setLoading] = useState(true)
  const [opening, setOpening] = useState(false)
  const loadSequence = useRef(0)
  const [archived, setArchived] = useState<Chapter[]>([])
  const [archiveConfirmed, setArchiveConfirmed] = useState(false)
  const [chapter, setChapter] = useState<Chapter>()
  const [editorKey, setEditorKey] = useState('')
  const [text, setText] = useState('')
  const [saved, setSaved] = useState('未打开章节')
  const [dirty, setDirty] = useState(0)
  const [selection, setSelection] = useState<Selection>({ start: 0, end: 0, text: '' })
  const [cursor, setCursor] = useState(0)
  const [note, setNote] = useState('')
  const reading = useRef({ revision: 0 })
  const [recovery, setRecovery] = useState<Draft>()
  const [action, setAction] = useState<ModuleInput['action']>(
    feedbackHandoff ? 'writing.rewrite_selection' : 'writing.advise',
  )
  const [instruction, setInstruction] = useState(feedbackHandoff?.text || '')
  const [designScope, setDesignScope] = useState<'book' | 'chapter'>('book')
  const [directionBasis, setDirectionBasis] = useState<DirectionBasis>()
  const directionForm = useRef({ action, instruction })
  directionForm.current = { action, instruction }
  const instructionField = useRef<HTMLTextAreaElement>(null)
  useLeaveGuard(!!directionBasis, '已载入的作者选定方向')
  const [confirmedFeedbackRevision, setConfirmedFeedbackRevision] = useState<number | null>(null)
  const [fixed, setFixed] = useState('')
  const [profile, setProfile] = useState(presets[0])
  const [seenContract, setSeenContract] = useState<AuthorContract>()
  const [seenGenre, setSeenGenre] = useState<GenreProfile>()
  const [references, setReferences] = useState<Excerpt[]>([])
  const [selectedRefs, setSelectedRefs] = useState<string[]>([])
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
  const [rename, setRename] = useState('')
  const current = useRef<Chapter | undefined>(undefined)
  const draftText = useRef('')
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
  async function loadChapter(id: string, at = 0, authoritative = false) {
    if (current.current && current.current.id !== id && !confirmLeave()) return
    const token = ++loadSequence.current
    setOpening(true)
    try {
      const row = await api<Chapter>(`/chapters/${id}`)
      if (!alive.current || token !== loadSequence.current) return
      if (authoritative) await writeDraft({ id, base: row.revision, text: row.content, updated: Date.now() })
      const draft = await readDraft(id)
      if (!alive.current || token !== loadSequence.current) return
      if (directionBasis && directionBasis.chapter_id !== id) {
        setDirectionBasis(undefined)
        setInstruction('')
        setAction('writing.advise')
      }
      current.current = row
      setChapter(row)
      setRename(row.title)
      setHistory([])
      setShowHistory(false)
      setSelection({ start: at, end: at, text: '' })
      setCursor(at)
      setCandidateId('')
      setRecovery(undefined)
      const recoverable =
        draft && !draft.fromOtherWindow && draft.text !== row.content && draft.base === row.revision
      draftText.current = recoverable ? draft.text : row.content
      setText(draftText.current)
      setEditorKey(id + ':' + row.revision + ':' + Date.now())
      if (recoverable) {
        setSaved('已恢复浏览器草稿，尚未保存到本机服务')
        setDirty((d) => d + 1)
      } else if (
        draft &&
        draft.text !== row.content &&
        (draft.base !== row.revision || draft.fromOtherWindow)
      ) {
        setRecovery(draft)
        setSaved('发现不同基线的浏览器草稿，请比较')
      } else setSaved('已保存')
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
          api<{ chapter_id: string | null; cursor: number; note: string; revision: number }>(
            `/branches/${branch.id}/reading-state`,
          ),
        ])
        if (!active) return
        setChapters(rows)
        setNote(state.note)
        reading.current.revision = state.revision
        if (rows.length)
          await loadChapter(
            rows.find((c) => c.id === feedbackHandoff?.chapter_id)?.id ||
              rows.find((c) => c.id === state.chapter_id)?.id ||
              rows[0].id,
            feedbackHandoff ? 0 : state.cursor,
          )
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
  async function save(): Promise<Chapter> {
    if (saving.current) return saving.current
    const run = async (): Promise<Chapter> => {
      if (!current.current) throw new Error('请先新建或打开章节。')
      while (current.current!.content !== draftText.current) {
        const snapshot: Chapter = current.current!
        const content: string = draftText.current
        setSaved('保存中…')
        await writeDraft({ id: snapshot.id, base: snapshot.revision, text: content, updated: Date.now() })
        const row: Chapter = await api<Chapter>(`/chapters/${snapshot.id}`, 'PUT', {
          base_revision: snapshot.revision,
          content,
        })
        current.current = row
        await writeDraft({ id: row.id, base: row.revision, text: draftText.current, updated: Date.now() })
        if (alive.current) setChapter(row)
      }
      if (alive.current) {
        setSaved('已保存')
        await refresh()
      }
      return current.current!
    }
    saving.current = run()
      .catch((error) => {
        setSaved('保存失败，浏览器草稿保留；可下载草稿后比较')
        throw error
      })
      .finally(() => {
        saving.current = null
      })
    return saving.current
  }
  function change(value: string) {
    draftText.current = value
    setText(value)
    setSaved('未保存')
    setDirty((d) => d + 1)
    if (current.current)
      void writeDraft({
        id: current.current.id,
        base: current.current.revision,
        text: value,
        updated: Date.now(),
      }).catch(() => {
        setSaved('浏览器草稿保存失败，请立即保存或下载草稿')
      })
  }
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
    const timer = setTimeout(() => {
      if (!current.current || draftText.current !== current.current.content) return
      void api<{ revision: number }>(`/branches/${branch.id}/reading-state`, 'PUT', {
        chapter_id: current.current.id,
        cursor: Math.min(selection.end, current.current.content.length),
        note,
        base_revision: reading.current.revision,
      })
        .then((row) => {
          reading.current.revision = row.revision
        })
        .catch(notify)
    }, 1200)
    return () => clearTimeout(timer)
  }, [chapter?.id, selection.end, note])
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
          setArtifacts(a)
          setTasks(t)
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
  async function createChapter() {
    await doAction(async () => {
      if (current.current) await save()
      const row = await api<Chapter>(`/branches/${branch.id}/chapters`, 'POST', {
        title: `第${chapters.length + 1}章`,
      })
      await refresh()
      await loadChapter(row.id)
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
        action,
        novel_id: novel.id,
        branch_id: branch.id,
        chapter_id: bookDesign ? null : (row?.id ?? null),
        base_revision: bookDesign ? null : (row?.revision ?? null),
        instruction: submittedInstruction,
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
  return (
    <div className="writing-layout">
      <aside className="chapter-sidebar">
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
        <ol className="chapter-list">
          {chapters.map((c) => (
            <li key={c.id}>
              <button
                aria-current={c.id === chapter?.id ? 'true' : undefined}
                onClick={() =>
                  void doAction(async () => {
                    if (current.current) await save()
                    await loadChapter(c.id)
                  })
                }
              >
                <span className="chapter-number">{String(c.position).padStart(2, '0')}</span>
                <span>
                  {c.title}
                  <small>
                    {c.chars || 0} 字符（含空白）{c.stale ? ' · 前文变更，待复检' : ''}
                  </small>
                </span>
              </button>
            </li>
          ))}
        </ol>
        <div className="sidebar-bottom">
          <label>
            下次从这里继续
            <textarea
              rows={3}
              value={note}
              onChange={(e) => setNote(e.target.value)}
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
      <section className="manuscript" aria-label="正文写作">
        {importOpen && (
          <ManuscriptImport
            key={branch.id}
            branchId={branch.id}
            notify={notify}
            onImported={refresh}
            onClose={() => setImportOpen(false)}
          />
        )}
        <div className="manuscript-toolbar">
          <span className="eyebrow">作者工作稿</span>
          <span className="save-status" role="status">
            {saved}
          </span>
          <button
            onClick={() =>
              void doAction(async () => {
                await save()
              })
            }
            disabled={!chapter || busy}
          >
            保存
          </button>
          <button
            onClick={() =>
              void doAction(async () => {
                if (!chapter) return
                await save()
                setHistory(await api(`/chapters/${chapter.id}/history`))
                setShowHistory(!showHistory)
              })
            }
            disabled={!chapter}
          >
            版本历史
          </button>
        </div>
        {chapter ? (
          <>
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
                {[...text].filter((c) => !/\s/u.test(c)).length} 非空白字符 · 修订 {chapter.revision}
                {chapter.stale ? ' · 受前文修改影响，需重新审阅' : ''}
              </div>
            </div>
            {recovery && (
              <div className="notice">
                <strong>浏览器草稿与服务中的修订不同。</strong>
                <p>
                  草稿基线 {recovery.base}，当前修订 {chapter.revision}。请比较后决定，服务稿尚未改动。
                </p>
                <Difference before={chapter.content} after={recovery.text} />
                <button
                  onClick={() => {
                    draftText.current = recovery.text
                    setText(recovery.text)
                    setEditorKey(chapter.id + ':recovered:' + Date.now())
                    setRecovery(undefined)
                    setDirty((d) => d + 1)
                    setSaved('待保存的人工合并稿')
                  }}
                >
                  将此草稿载入编辑区
                </button>
                <button
                  onClick={() => {
                    setRecovery(undefined)
                    void writeDraft({
                      id: chapter.id,
                      base: chapter.revision,
                      text: chapter.content,
                      updated: Date.now(),
                    })
                    setSaved('已保存')
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
            <Editor
              id={editorKey}
              initial={text}
              cursor={cursor}
              readOnly={busy || opening}
              onChange={change}
              onSelection={setSelection}
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
        <div className="section-title">
          <h2>创作辅助</h2>
          <span className="badge">作者决定采纳</span>
        </div>
        <AuthorContractNotice
          branchId={branch.id}
          onSettings={onGenreSettings}
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
        <GenreProfileNotice branchId={branch.id} onSettings={onGenreSettings} onResolved={setSeenGenre} />
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
              <p role="status">方向已载入本章补写表单；载入操作本身未生成或改动正文。可继续编辑情节要求。</p>
              <p className="small muted">
                来源建议 {directionBasis.advice_id} · 候选版本 {directionBasis.candidate_revision} · 原稿修订{' '}
                {directionBasis.source_revision ?? '未绑定'}；目标章 {chapter?.title}（
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
            <strong>{label(activeTask.status)}</strong>
            <p>关闭浏览器不取消本机任务。</p>
            {activeTask.result.author_contract_snapshot && (
              <details>
                <summary>本次任务冻结的创作约定</summary>
                <AuthorContractDetails contract={activeTask.result.author_contract_snapshot} />
              </details>
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
                  onChange={(e) => {
                    if (confirmLeave()) setCandidateId(e.target.value)
                  }}
                >
                  {visibleArtifacts.map((a, i) => (
                    <option key={a.id} value={a.id}>
                      候选 {visibleArtifacts.length - i} · {a.kind} · {label(a.status)}
                    </option>
                  ))}
                </select>
              </label>
              {candidate && (
                <Candidate
                  key={candidate.id}
                  artifact={candidate}
                  onIntentSettings={onGenreSettings}
                  novelId={novel.id}
                  branchId={branch.id}
                  chapter={chapter}
                  account={account}
                  modelReady={modelReady}
                  revisionDisabled={!!activeTask || busy}
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
                  onAccept={async () => {
                    if (current.current) await save()
                    await api(`/artifacts/${candidate.id}/accept`, 'POST', {
                      idempotency_key: 'manual-' + candidate.id,
                      candidate_revision: candidate.candidate_revision,
                    })
                    if (chapter) await loadChapter(chapter.id, 0, true)
                    setArtifacts(await api(`/artifacts?branch_id=${branch.id}`))
                    await refresh()
                  }}
                  onUpdate={async () => setArtifacts(await api(`/artifacts?branch_id=${branch.id}`))}
                />
              )}
            </>
          ) : (
            <p className="empty-note">有想法时亲自写。需要帮助时，再生成一份候选。</p>
          )}
        </section>
      </aside>
    </div>
  )
}

function Candidate({
  artifact,
  onIntentSettings,
  revisionDisabled,
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
  onAccept: () => Promise<void>
  onUpdate: () => Promise<void>
}) {
  const [edit, setEdit] = useState(artifact.payload.text || '')
  const [editRevision, setEditRevision] = useState(artifact.candidate_revision)
  const candidateChanged = editRevision !== artifact.candidate_revision
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
      {!!artifact.payload.review && (
        <details>
          <summary>查看本章检查依据</summary>
          <pre className="report">{JSON.stringify(artifact.payload.review, null, 2)}</pre>
        </details>
      )}
      <ReferenceComparison value={artifact.payload.reference_comparison} />
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
      <div className="actions">
        <button
          className="primary"
          disabled={
            artifact.kind.startsWith('reference_') ||
            artifact.kind === 'story_extraction' ||
            (artifact.kind === 'intent' && !!artifact.payload.author_text) ||
            busy ||
            candidateChanged ||
            artifact.status !== 'candidate' ||
            edit !== (artifact.payload.text || '')
          }
          onClick={() => void perform(onAccept)}
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
