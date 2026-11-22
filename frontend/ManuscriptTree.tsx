import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Branch, type Chapter } from './api'
import type { Notify } from './App'
import { useLeaveGuard } from './leaveGuard'
import { settingLabels, type SettingKind } from './workspaceSettingsKinds'

type Volume = { id: string; title: string; start_chapter_id: string; revision: number }
const emptyEdit: Volume = { id: '', title: '', start_chapter_id: '', revision: 0 }

function chapterGroups(chapters: Chapter[], volumes: Volume[]) {
  const starts = new Map(volumes.map((volume) => [volume.start_chapter_id, volume]))
  const groups: { volume: Volume; chapters: Chapter[] }[] = []
  const ungrouped: Chapter[] = []
  let current = ungrouped
  for (const chapter of chapters) {
    const volume = starts.get(chapter.id)
    if (volume) {
      current = []
      groups.push({ volume, chapters: current })
    }
    current.push(chapter)
  }
  return { ungrouped, groups }
}

export function ManuscriptTree({
  branch,
  chapters,
  activeChapterId,
  setting,
  disabled,
  chooseChapter,
  chooseSetting,
  notify,
}: {
  branch: Branch
  chapters: Chapter[]
  activeChapterId?: string
  setting?: SettingKind
  disabled: boolean
  chooseChapter: (id: string) => void
  chooseSetting: (kind: SettingKind) => void
  notify: Notify
}) {
  const path = `/branches/${branch.id}/volumes`
  const [volumes, setVolumes] = useState<Volume[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [source, setSource] = useState(emptyEdit)
  const [edit, setEdit] = useState(emptyEdit)
  const [chaptersOpen, setChaptersOpen] = useState(true)
  const [settingsOpen, setSettingsOpen] = useState(true)
  const [closedVolumes, setClosedVolumes] = useState<string[]>([])
  const request = useRef(0)
  const submitting = useRef(false)
  const edited = source.title !== edit.title || source.start_chapter_id !== edit.start_chapter_id
  useLeaveGuard(edited, '分卷设置')

  const load = useCallback(async () => {
    const sequence = ++request.current
    setLoading(true)
    setError('')
    try {
      const rows = await api<Volume[]>(path)
      if (request.current === sequence) setVolumes(rows)
    } catch (error) {
      if (request.current === sequence) setError(error instanceof Error ? error.message : '分卷读取失败。')
    } finally {
      if (request.current === sequence) setLoading(false)
    }
  }, [path])
  useEffect(() => {
    void load()
    return () => {
      request.current++
    }
  }, [load])

  const { ungrouped, groups } = chapterGroups(chapters, volumes)
  const activeVolume = groups.find((group) =>
    group.chapters.some((chapter) => chapter.id === activeChapterId),
  )?.volume.id
  useEffect(() => {
    if (setting) setSettingsOpen(true)
    if (activeChapterId && !setting) {
      setChaptersOpen(true)
      if (activeVolume) setClosedVolumes((rows) => rows.filter((id) => id !== activeVolume))
    }
  }, [activeChapterId, activeVolume, setting])

  function selectVolume(id: string) {
    if (edited && !window.confirm('当前分卷设置尚未保存。放弃修改并切换？')) return
    const selected = volumes.find((row) => row.id === id) ?? emptyEdit
    setEdit(selected)
    setSource(selected)
    setError('')
    setNotice('')
  }

  async function saveVolume(remove = false) {
    if (submitting.current) return
    if (remove && !window.confirm(`移除「${source.title}」的分卷分组？章节仍按当前顺序排列。`)) return
    submitting.current = true
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const data = { title: edit.title, start_chapter_id: edit.start_chapter_id }
      const row = edit.id
        ? await api<Volume>(
            `${path}/${edit.id}`,
            remove ? 'DELETE' : 'PUT',
            remove ? { base_revision: source.revision } : { ...data, base_revision: source.revision },
          )
        : await api<Volume>(path, 'POST', data)
      setVolumes((items) =>
        remove
          ? items.filter((item) => item.id !== row.id)
          : [...items.filter((item) => item.id !== row.id), row],
      )
      const saved = remove ? emptyEdit : row
      setSource(saved)
      setEdit(saved)
      setNotice(remove ? '已移除分卷。' : '分卷已保存。')
    } catch (error) {
      setError(error instanceof Error ? error.message : '分卷保存失败。')
      notify(error)
    } finally {
      submitting.current = false
      setBusy(false)
    }
  }

  function chapterList(rows: Chapter[]) {
    return (
      <ol>
        {rows.map((chapter) => (
          <li key={chapter.id}>
            <button
              type="button"
              disabled={disabled}
              aria-current={!setting && chapter.id === activeChapterId ? 'true' : undefined}
              onClick={() => chooseChapter(chapter.id)}
            >
              <span className="chapter-number">{String(chapter.position).padStart(2, '0')}</span>
              <span>
                {chapter.title}
                <small>
                  {chapter.chars || 0} 字符（含空白）{chapter.stale ? ' · 前文变更，待复检' : ''}
                </small>
              </span>
            </button>
          </li>
        ))}
      </ol>
    )
  }

  const available = chapters.some(
    (chapter) => !volumes.some((volume) => volume.start_chapter_id === chapter.id),
  )
  const anchorArchived = !!edit.id && !chapters.some((chapter) => chapter.id === source.start_chapter_id)
  const newer = !!edit.id && volumes.find((row) => row.id === edit.id)?.revision !== source.revision
  return (
    <>
      <nav className="manuscript-tree" aria-label="作品树">
        <details open={chaptersOpen} onToggle={(event) => setChaptersOpen(event.currentTarget.open)}>
          <summary>正文</summary>
          <div className="chapter-list">
            {!chapters.length && <p className="small muted">暂无章节</p>}
            {ungrouped.length > 0 && chapterList(ungrouped)}
            {groups.map(({ volume, chapters }) => (
              <details
                key={volume.id}
                className="manuscript-volume"
                open={!closedVolumes.includes(volume.id)}
                onToggle={(event) => {
                  const open = event.currentTarget.open
                  setClosedVolumes((rows) => {
                    if (open) return rows.includes(volume.id) ? rows.filter((id) => id !== volume.id) : rows
                    return rows.includes(volume.id) ? rows : [...rows, volume.id]
                  })
                }}
              >
                <summary>{volume.title}</summary>
                {chapterList(chapters)}
              </details>
            ))}
          </div>
        </details>
        <details open={settingsOpen} onToggle={(event) => setSettingsOpen(event.currentTarget.open)}>
          <summary>设定</summary>
          <ul className="setting-tree">
            {(Object.entries(settingLabels) as [SettingKind, string][]).map(([kind, label]) => (
              <li key={kind}>
                <button
                  type="button"
                  disabled={disabled}
                  aria-current={setting === kind ? 'true' : undefined}
                  onClick={() => chooseSetting(kind)}
                >
                  {label}
                </button>
              </li>
            ))}
          </ul>
        </details>
      </nav>
      <details className="volume-management">
        <summary>分卷管理</summary>
        <p className="small muted">从所选起始章开始分组，直到下一卷。调整分组只改变目录显示。</p>
        <button type="button" disabled={loading || busy} onClick={() => void load()}>
          重新读取分卷
        </button>
        {loading && <p role="status">正在读取分卷…</p>}
        {error && <p role="alert">{error}</p>}
        {notice && <p role="status">{notice}</p>}
        <form
          onSubmit={(event) => {
            event.preventDefault()
            if (!newer && edited && edit.title.trim() && edit.start_chapter_id) void saveVolume()
          }}
        >
          <fieldset disabled={busy || loading}>
            <label>
              分卷
              <select value={edit.id} onChange={(event) => selectVolume(event.target.value)}>
                <option value="">新建分卷</option>
                {edit.id && !volumes.some((item) => item.id === edit.id) && (
                  <option value={edit.id}>{source.title} · 已移除</option>
                )}
                {volumes.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.title}
                  </option>
                ))}
              </select>
            </label>
            {anchorArchived && (
              <p className="small">原起始章已归档。选择新的起始章或恢复原章后可重新分组。</p>
            )}
            {!edit.id && !available && <p className="small">需要一个尚未用作卷首的章节。</p>}
            {newer && (
              <button type="button" onClick={() => selectVolume(edit.id)}>
                使用当前已保存的分卷
              </button>
            )}
            <label>
              卷名
              <input
                value={edit.title}
                maxLength={120}
                required
                onChange={(event) => setEdit({ ...edit, title: event.target.value })}
              />
            </label>
            <label>
              起始章
              <select
                value={edit.start_chapter_id}
                required
                onChange={(event) => setEdit({ ...edit, start_chapter_id: event.target.value })}
              >
                <option value="">选择章节</option>
                {anchorArchived && <option value={source.start_chapter_id}>原章节已归档</option>}
                {chapters.map((chapter) => (
                  <option
                    key={chapter.id}
                    value={chapter.id}
                    disabled={volumes.some(
                      (item) => item.id !== edit.id && item.start_chapter_id === chapter.id,
                    )}
                  >
                    {chapter.position} · {chapter.title}
                  </option>
                ))}
              </select>
            </label>
            <button type="submit" disabled={newer || !edited || !edit.title.trim() || !edit.start_chapter_id}>
              保存分卷
            </button>
            {edit.id && (
              <button type="button" disabled={newer} onClick={() => void saveVolume(true)}>
                移除分卷
              </button>
            )}
          </fieldset>
        </form>
      </details>
    </>
  )
}
