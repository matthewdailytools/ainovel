import { useCallback, useEffect, useRef, useState } from 'react'
import {
  OfflineStoreError,
  confirmLocalOutline,
  confirmLocalSettings,
  confirmLocalStory,
  createLocalChapter,
  createLocalProject,
  exportLocalManuscript,
  importLocalManuscript,
  listLocalChapters,
  listLocalProjects,
  maxManuscriptBytes,
  readLocalHistory,
  readLocalOutline,
  readLocalSourceRevision,
  readLocalSettings,
  readLocalStory,
  saveLocalChapter,
  type LocalChapter,
  type LocalProject,
  type LocalRevision,
} from './offlineStore'
import './offlineWorkspace.css'
import { WebSyncPanel } from './WebSyncPanel'
import { PortableEvidence } from './PortableEvidence'
import { LocalProjectBrief } from './OfflineProjectBrief'
import { OfflineSettings } from './OfflineSettings'
import { OfflineOutline } from './OfflineOutline'
import { OfflineWorkflowHistory } from './OfflineWorkflowHistory'
import { OfflineReferenceHistory } from './OfflineReferenceHistory'
import { OfflineStory } from './OfflineStory'
import { discardOwnDraft, readDraft, writeDraft, type Draft } from './drafts'

type EditorState = {
  chapter: LocalChapter
  history: LocalRevision[]
  base: LocalRevision
  text: string
}

function download(filename: string, blob: Blob) {
  const href = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = href
  link.download = filename
  link.click()
  setTimeout(() => URL.revokeObjectURL(href), 1000)
}

async function prepareShell() {
  const active = await navigator.serviceWorker.getRegistration(location.href)
  if (active?.active && active.scope === new URL('./', location.href).href) return
  const registration = await navigator.serviceWorker.register('./sw.js', { scope: './' })
  if (registration.active) return
  const worker = registration.installing ?? registration.waiting
  if (!worker) throw new Error('Missing offline worker')
  await new Promise<void>((resolve, reject) => {
    const state = () => {
      if (worker.state !== 'activated' && worker.state !== 'redundant') return
      worker.removeEventListener('statechange', state)
      if (worker.state === 'activated') resolve()
      else reject(new Error('Offline shell unavailable'))
    }
    worker.addEventListener('statechange', state)
    state()
  })
}

export function OfflineWorkspace() {
  const [ready, setReady] = useState(false)
  const [shell, setShell] = useState('正在准备离线页面…')
  const [projects, setProjects] = useState<LocalProject[]>([])
  const [selected, setSelected] = useState<LocalProject>()
  const [chapters, setChapters] = useState<LocalChapter[]>([])
  const [editor, setEditor] = useState<EditorState>()
  const [preview, setPreview] = useState<LocalRevision>()
  const [conflict, setConflict] = useState<LocalRevision[]>()
  const [recovery, setRecovery] = useState<Draft>()
  const [draftNotice, setDraftNotice] = useState('')
  const [projectTitle, setProjectTitle] = useState('')
  const [chapterTitle, setChapterTitle] = useState('')
  const [localBusy, setBusy] = useState(false)
  const [syncBusy, setSyncBusy] = useState(false)
  const [projectDirty, setProjectDirty] = useState(false)
  const [settingsDirty, setSettingsDirty] = useState(false)
  const [outlineDirty, setOutlineDirty] = useState(false)
  const [storyDirty, setStoryDirty] = useState(false)
  const [referenceDirty, setReferenceDirty] = useState(false)
  const [syncMergeDirty, setSyncMergeDirty] = useState(false)
  const [includedExcerpts, setIncludedExcerpts] = useState<string[]>([])
  const [workbenchRefresh, setWorkbenchRefresh] = useState(0)
  const busy = localBusy || syncBusy
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const locked = useRef(false)
  const dirty = !!editor && editor.text !== editor.base.content
  const draftId = selected && editor ? `offline/${selected.branch.id}/${editor.chapter.id}` : undefined
  const projectId = selected?.novel.id
  const loadSettings = useCallback(() => {
    if (!projectId) return Promise.resolve({ versions: [], projectVersionId: null })
    return readLocalSettings(projectId)
  }, [projectId])
  const loadOutline = useCallback(() => {
    if (!projectId) throw new Error('请先打开离线作品。')
    return readLocalOutline(projectId)
  }, [projectId])
  const loadStory = useCallback(() => {
    if (!projectId) throw new Error('请先打开离线作品。')
    return readLocalStory(projectId)
  }, [projectId])
  const refreshWorkbench = useCallback(() => setWorkbenchRefresh((revision) => revision + 1), [])

  useEffect(() => {
    let active = true
    listLocalProjects()
      .then((rows) => {
        if (active) {
          setProjects(rows)
          setReady(true)
        }
      })
      .catch((reason) => {
        if (active) showError(reason)
      })
    if (!('serviceWorker' in navigator)) setShell('此浏览器无法缓存离线页面。')
    else
      prepareShell()
        .then(() => {
          if (active) setShell('离线页面就绪')
        })
        .catch(() => {
          if (active) setShell('离线页面准备失败，本地已保存的稿件仍可导出。')
        })
    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  useEffect(() => {
    if (!dirty || !editor || !draftId) return
    let active = true
    const timer = setTimeout(() => {
      void writeDraft({
        id: draftId,
        base: editor.base.revision,
        revisionId: editor.base.id,
        text: editor.text,
        updated: Date.now(),
      }).then(
        () => {
          if (active) setDraftNotice('输入已暂存为草稿。')
        },
        () => {
          if (active) setDraftNotice('无法暂存当前输入，请下载草稿以保留这段文字。')
        },
      )
    }, 250)
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [dirty, editor, draftId])

  function showError(reason: unknown) {
    setError(reason instanceof Error ? reason.message : '操作失败，请重试。')
  }

  function mayLeave(workChange = false) {
    if (
      workChange &&
      (projectDirty || settingsDirty || outlineDirty || storyDirty || referenceDirty || syncMergeDirty) &&
      !window.confirm('工作台有未确认的修改。是否离开当前作品？')
    )
      return false
    return !dirty || window.confirm('这一章有未提交的输入。是否离开当前编辑区？')
  }

  async function run(operation: () => Promise<void>) {
    if (locked.current || syncBusy) return
    locked.current = true
    setBusy(true)
    setError('')
    setStatus('')
    try {
      await operation()
    } catch (reason) {
      showError(reason)
    } finally {
      locked.current = false
      setBusy(false)
    }
  }

  async function openProject(project: LocalProject) {
    const rows = await listLocalChapters(project.novel.id)
    setSelected(project)
    setChapters(rows)
    setEditor(undefined)
    setConflict(undefined)
    setRecovery(undefined)
    setDraftNotice('')
    setPreview(undefined)
    setIncludedExcerpts([])
  }

  async function openChapter(chapter: LocalChapter) {
    if (!selected) return
    const history = await readLocalHistory(selected.novel.id, chapter.id)
    const base = history.at(-1)
    if (!base) throw new Error('找不到此章的保存修订。')
    setEditor({ chapter, history, base, text: base.content })
    setConflict(undefined)
    setRecovery(undefined)
    setDraftNotice('')
    setPreview(undefined)
    try {
      const saved = await readDraft(`offline/${selected.branch.id}/${chapter.id}`)
      if (saved && saved.text !== base.content) setRecovery(saved)
    } catch {
      setDraftNotice('无法读取编辑草稿，已打开正式保存稿。')
    }
  }

  async function save(restore?: LocalRevision) {
    if (!selected || !editor) return
    try {
      const revision = await saveLocalChapter(
        selected.novel.id,
        editor.chapter.id,
        editor.base.id,
        editor.text,
        restore?.id,
      )
      const history = editor.history.some((old) => old.id === revision.id)
        ? editor.history
        : [...editor.history, revision]
      setEditor({ ...editor, history, base: revision, text: revision.content })
      setChapters((rows) =>
        rows.map((row) => (row.id === editor.chapter.id ? { ...row, revision: revision.revision } : row)),
      )
      setConflict(undefined)
      setPreview(undefined)
      setStatus(`本地已保存 · 修订 ${revision.revision}`)
      refreshWorkbench()
      setRecovery(undefined)
      setDraftNotice('')
      if (draftId) void discardOwnDraft(draftId).catch(() => undefined)
    } catch (reason) {
      if (reason instanceof OfflineStoreError && reason.code === 'conflict') {
        setConflict(await readLocalHistory(selected.novel.id, editor.chapter.id))
      }
      throw reason
    }
  }

  return (
    <main className="offline-workspace">
      <header>
        <div>
          <h1>离线写作</h1>
          <p>{shell}</p>
        </div>
        <a href="/">打开本机工作台</a>
      </header>
      {status && <p role="status">{status}</p>}
      {error && <p role="alert">{error}</p>}
      <div className="offline-columns">
        <aside aria-label="离线作品与章节">
          <form
            onSubmit={(event) => {
              event.preventDefault()
              if (!mayLeave(true)) return
              void run(async () => {
                const project = await createLocalProject(projectTitle)
                setProjects((rows) => [...rows, project])
                setProjectTitle('')
                await openProject(project)
              })
            }}
          >
            <label>
              新作品名
              <input
                required
                maxLength={200}
                value={projectTitle}
                onChange={(e) => setProjectTitle(e.target.value)}
              />
            </label>
            <button type="submit" disabled={busy || !ready}>
              本地新建作品
            </button>
          </form>
          <label>
            离线作品
            <select
              value={selected?.novel.id ?? ''}
              disabled={busy || !ready}
              onChange={(event) => {
                const project = projects.find((item) => item.novel.id === event.target.value)
                if (project && mayLeave(true)) void run(() => openProject(project))
              }}
            >
              <option value="" disabled>
                选择作品
              </option>
              {projects.map((project) => (
                <option key={project.novel.id} value={project.novel.id}>
                  {project.novel.title}
                </option>
              ))}
            </select>
          </label>
          {selected && (
            <>
              <LocalProjectBrief
                key={`project-${selected.branch.id}`}
                projectId={selected.novel.id}
                refreshToken={workbenchRefresh}
                disabled={busy}
                onDirty={setProjectDirty}
                onConfirmed={refreshWorkbench}
              />
              <OfflineSettings
                key={`settings-${selected.branch.id}`}
                read={loadSettings}
                refreshToken={workbenchRefresh}
                disabled={busy}
                onDirty={setSettingsDirty}
                onConfirm={async (base, version) => {
                  await confirmLocalSettings(selected.novel.id, base, version)
                  refreshWorkbench()
                }}
              />
              <OfflineOutline
                key={`outline-${selected.branch.id}`}
                read={loadOutline}
                refreshToken={workbenchRefresh}
                disabled={busy}
                onDirty={setOutlineDirty}
                onConfirm={(base, version) => confirmLocalOutline(selected.novel.id, base, version)}
              />
              <OfflineStory
                key={`story-${selected.branch.id}`}
                read={loadStory}
                readChapter={async (id) => (await readLocalHistory(selected.novel.id, id)).at(-1)}
                chapters={chapters}
                refreshToken={workbenchRefresh}
                disabled={busy}
                onDirty={setStoryDirty}
                onConfirm={async (draft) => {
                  await confirmLocalStory(selected.novel.id, draft)
                  refreshWorkbench()
                }}
              />
              <OfflineReferenceHistory
                key={`reference-${selected.branch.id}`}
                projectId={selected.novel.id}
                refreshToken={workbenchRefresh}
                disabled={busy}
                onDirty={setReferenceDirty}
                onConfirmed={refreshWorkbench}
                includedExcerpts={includedExcerpts}
                setIncludedExcerpts={setIncludedExcerpts}
              />
              <PortableEvidence
                key={selected.branch.id}
                docs={selected.evidence ?? []}
                chapters={chapters}
                readSource={(id) => readLocalSourceRevision(selected.novel.id, id)}
              />
              <OfflineWorkflowHistory
                key={`workflow-${selected.branch.id}`}
                projectId={selected.novel.id}
                refreshToken={workbenchRefresh}
                download={download}
              />
              <WebSyncPanel
                key={selected.branch.id}
                project={selected}
                unsaved={
                  dirty ||
                  projectDirty ||
                  settingsDirty ||
                  outlineDirty ||
                  storyDirty ||
                  referenceDirty ||
                  localBusy
                }
                onBusy={setSyncBusy}
                onMergeDraftDirty={setSyncMergeDirty}
                onApplied={async () => {
                  const saved = (await listLocalProjects()).find(
                    (work) => work.branch.id === selected.branch.id,
                  )
                  if (!saved) return
                  refreshWorkbench()
                  setProjects(await listLocalProjects())
                  const latest = await listLocalChapters(saved.novel.id)
                  setSelected(saved)
                  setChapters(latest)
                  const chapter = latest.find((entry) => entry.id === editor?.chapter.id)
                  if (chapter) await openChapter(chapter)
                  else setEditor(undefined)
                }}
              />
              <form
                onSubmit={(event) => {
                  event.preventDefault()
                  if (!mayLeave()) return
                  void run(async () => {
                    const chapter = await createLocalChapter(selected.novel.id, chapterTitle)
                    setChapters((rows) => [...rows, chapter])
                    setChapterTitle('')
                    refreshWorkbench()
                    await openChapter(chapter)
                  })
                }}
              >
                <label>
                  新章节名
                  <input
                    required
                    maxLength={200}
                    value={chapterTitle}
                    onChange={(e) => setChapterTitle(e.target.value)}
                  />
                </label>
                <button type="submit" disabled={busy}>
                  本地新建章节
                </button>
              </form>
              <nav aria-label="离线章节目录">
                {chapters.map((chapter) => (
                  <button
                    type="button"
                    key={chapter.id}
                    disabled={busy}
                    aria-current={editor?.chapter.id === chapter.id || undefined}
                    onClick={() => {
                      if (mayLeave()) void run(() => openChapter(chapter))
                    }}
                  >
                    {chapter.position}. {chapter.title}
                  </button>
                ))}
              </nav>
            </>
          )}
        </aside>
        <section aria-label="离线正文">
          {editor ? (
            <>
              <h2>{editor.chapter.title}</h2>
              <p>
                修订 {editor.base.revision} · {dirty ? '有未保存输入' : '当前稿已保存到浏览器'}
              </p>
              {draftNotice && <p role="status">{draftNotice}</p>}
              {recovery && !dirty && (
                <details>
                  <summary>
                    {recovery.fromOtherWindow ? '有其他窗口暂存的草稿' : '有未提交的暂存草稿'}
                  </summary>
                  <p>
                    暂存时的修订 {recovery.base} ·{' '}
                    {recovery.revisionId === editor.base.id ? '依据仍有效' : '保存稿已有新版本'}
                  </p>
                  <pre>{recovery.text || '空稿'}</pre>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      const source = editor.history.find((row) => row.id === recovery.revisionId)
                      if (recovery.revisionId !== editor.base.id) setConflict(editor.history)
                      setEditor({ ...editor, base: source ?? editor.base, text: recovery.text })
                      setRecovery(undefined)
                    }}
                  >
                    载入暂存草稿
                  </button>
                </details>
              )}
              <label>
                章节正文
                <textarea
                  rows={18}
                  value={editor.text}
                  readOnly={busy}
                  onChange={(event) => {
                    const text = event.target.value
                    setEditor((value) => value && { ...value, text })
                    setDraftNotice('')
                    if (draftId && text === editor.base.content)
                      void discardOwnDraft(draftId).catch(() => undefined)
                    setStatus('')
                  }}
                />
              </label>
              <div className="offline-actions">
                <button
                  type="button"
                  className="primary"
                  disabled={busy || !dirty || !!conflict}
                  onClick={() => void run(() => save())}
                >
                  保存正文到浏览器
                </button>
                <button
                  type="button"
                  onClick={() =>
                    download('当前草稿.txt', new Blob([editor.text], { type: 'text/plain;charset=utf-8' }))
                  }
                >
                  下载当前草稿
                </button>
              </div>
              {conflict && (
                <section aria-label="并发稿件">
                  <h3>另一个窗口已有新保存稿</h3>
                  <pre>{conflict.at(-1)?.content}</pre>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      const base = conflict.at(-1)
                      if (!base) return
                      setEditor({ ...editor, base, history: conflict })
                      setConflict(undefined)
                      setError('')
                      setStatus('已读取新的保存修订作为基线。当前输入仍待保存。')
                    }}
                  >
                    保留我的输入，改用新基线
                  </button>
                </section>
              )}
              <details>
                <summary>本章版本历史</summary>
                <label>
                  历史修订
                  <select
                    value={preview?.id ?? ''}
                    onChange={(event) =>
                      setPreview(editor.history.find((item) => item.id === event.target.value))
                    }
                  >
                    <option value="">选择修订</option>
                    {[...editor.history].reverse().map((item) => (
                      <option key={item.id} value={item.id}>
                        修订 {item.revision} · {item.cause}
                      </option>
                    ))}
                  </select>
                </label>
                {preview && (
                  <>
                    <pre>{preview.content || '空稿'}</pre>
                    <button
                      type="button"
                      disabled={busy || dirty || preview.id === editor.base.id}
                      onClick={() => void run(() => save(preview))}
                    >
                      恢复为最新修订
                    </button>
                  </>
                )}
              </details>
            </>
          ) : (
            <p>选择或新建章节后开始写作。</p>
          )}
        </section>
      </div>
      <section className="offline-transfer" aria-label="稿件交换">
        <h2>稿件交换</h2>
        <p>导出当前工作版本及历史。手法卡原句仅随明确勾选的版本一起导出。</p>
        <button
          type="button"
          disabled={!selected || busy}
          onClick={() => {
            if (!selected) return
            void run(async () => {
              const pack = await exportLocalManuscript(selected.novel.id, includedExcerpts)
              const file = new Blob([JSON.stringify(pack)], { type: 'application/json' })
              if (file.size > maxManuscriptBytes) throw new Error('稿件交换包超过 24 MiB 导入上限。')
              download('ainovel-稿件交换.json', file)
              setIncludedExcerpts([])
              setStatus('已下载已保存稿件与历史。')
            })
          }}
        >
          导出稿件交换包
        </button>
        <label>
          导入新离线作品
          <input
            type="file"
            accept=".json,application/json"
            disabled={busy || !ready}
            onChange={(event) => {
              const file = event.target.files?.[0]
              event.target.value = ''
              if (!file || !mayLeave(true)) return
              void run(async () => {
                const project = await importLocalManuscript(file)
                setProjects((rows) => [...rows, project])
                await openProject(project)
                setStatus('已校验并导入稿件及历史。')
              })
            }}
          />
        </label>
      </section>
    </main>
  )
}
