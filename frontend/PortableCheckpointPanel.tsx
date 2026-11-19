import { useEffect, useState } from 'react'
import { ApiError, api } from './api'
import type { PortableCheckpoint } from './portableCheckpoint'

type ArchivedChapterDiff = {
  title: string
  snapshot: { revision: number; content: string }
  current: { revision: number; content: string; archived: 0 | 1 } | null
}

const scopeLabels = { book: '全书', volume: '卷' }
const date = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' })

function checkpointTime(seconds: number) {
  const time = new Date(seconds * 1000)
  return Number.isFinite(time.valueOf())
    ? { label: date.format(time), dateTime: time.toISOString() }
    : { label: '时间无法读取', dateTime: undefined }
}

function chapterError(error: unknown) {
  if (error instanceof ApiError && (error.code === 'snapshot_source' || error.code === 'not_found'))
    return '无法核验归档原稿。请核对原稿件包，补齐对应的章稿修订后重试。'
  return error instanceof Error ? error.message : '无法读取归档章稿。'
}

export function PortableCheckpointPanel({ branchId }: { branchId: string }) {
  const route = `/branches/${encodeURIComponent(branchId)}/portable-checkpoints`
  const [open, setOpen] = useState(false)
  const [checkpoints, setCheckpoints] = useState<PortableCheckpoint[]>()
  const [snapshotId, setSnapshotId] = useState('')
  const [chapterId, setChapterId] = useState('')
  const [diff, setDiff] = useState<ArchivedChapterDiff>()
  const [listError, setListError] = useState('')
  const [diffError, setDiffError] = useState('')
  const [listRetry, setListRetry] = useState(0)
  const [diffRetry, setDiffRetry] = useState(0)
  const snapshot = checkpoints?.find(({ id }) => id === snapshotId)
  const scopedIds = new Set(snapshot?.scoped_chapter_ids)
  const chapters = snapshot?.chapters.filter(({ id }) => scopedIds.has(id)) ?? []
  const selectedChapter = chapters.find(({ id }) => id === chapterId)
  const time = snapshot && checkpointTime(snapshot.created)

  useEffect(() => {
    if (!open) return
    let active = true
    setCheckpoints(undefined)
    setListError('')
    void api<PortableCheckpoint[]>(route)
      .then((entries) => {
        if (active) setCheckpoints(entries)
      })
      .catch((error: unknown) => {
        if (active) setListError(error instanceof Error ? error.message : '无法读取跨端检查点归档。')
      })
    return () => {
      active = false
    }
  }, [route, open, listRetry])

  useEffect(() => {
    if (!open || !snapshotId || !selectedChapter) return
    let active = true
    setDiff(undefined)
    setDiffError('')
    const chapterRoute = `${route}/${encodeURIComponent(snapshotId)}/chapters/${encodeURIComponent(chapterId)}`
    void api<ArchivedChapterDiff>(chapterRoute)
      .then((chapter) => {
        if (!active) return
        if (chapter.snapshot.revision !== selectedChapter.revision) {
          setDiffError('归档修订与清单不一致。请重新打开归档后核对。')
          return
        }
        setDiff(chapter)
      })
      .catch((error: unknown) => {
        if (active) setDiffError(chapterError(error))
      })
    return () => {
      active = false
    }
  }, [route, open, snapshotId, chapterId, selectedChapter, diffRetry])

  return (
    <details
      onToggle={(event) => {
        setOpen(event.currentTarget.open)
        setSnapshotId('')
        setChapterId('')
        setDiff(undefined)
        setDiffError('')
      }}
    >
      <summary>跨端检查点归档</summary>
      {open && (
        <section aria-label="跨端检查点归档（只读）">
          <p>只读归档，用于核对作品备份保留的章稿。</p>
          {listError ? (
            <>
              <p role="alert">{listError}</p>
              <button type="button" onClick={() => setListRetry((attempt) => attempt + 1)}>
                重新读取归档
              </button>
            </>
          ) : !checkpoints ? (
            <p role="status">正在读取跨端检查点…</p>
          ) : !checkpoints.length ? (
            <p>本机暂无跨端检查点归档。</p>
          ) : (
            <label>
              已归档检查点
              <select
                value={snapshotId}
                onChange={(event) => {
                  setSnapshotId(event.target.value)
                  setChapterId('')
                  setDiff(undefined)
                  setDiffError('')
                }}
              >
                <option value="">选择归档检查点</option>
                {checkpoints.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.label} · {scopeLabels[entry.scope]} · {checkpointTime(entry.created).label}
                  </option>
                ))}
              </select>
            </label>
          )}
          {snapshot && (
            <>
              <h3>{snapshot.label}</h3>
              <p>
                原范围 {scopeLabels[snapshot.scope]}
                {snapshot.scope_id && (
                  <>
                    {' '}
                    <code>{snapshot.scope_id}</code>
                  </>
                )}{' '}
                · {chapters.length} 章 · 故事版本 {snapshot.story_revision} · 归档时间{' '}
                <time dateTime={time?.dateTime}>{time?.label}</time>
              </p>
              {chapters.length ? (
                <label>
                  核对原稿章节
                  <select
                    value={chapterId}
                    onChange={(event) => {
                      setChapterId(event.target.value)
                      setDiff(undefined)
                      setDiffError('')
                    }}
                  >
                    <option value="">选择归档章节</option>
                    {chapters.map((chapter) => (
                      <option key={chapter.id} value={chapter.id}>
                        {chapter.position}. {chapter.title} · 修订 {chapter.revision}
                        {chapter.archived ? ' · 来源已归档' : ''}
                      </option>
                    ))}
                  </select>
                </label>
              ) : (
                <p>这份归档没有可核对的章稿。</p>
              )}
              {selectedChapter &&
                (diffError ? (
                  <>
                    <p role="alert">{diffError}</p>
                    <button type="button" onClick={() => setDiffRetry((attempt) => attempt + 1)}>
                      重试读取原稿
                    </button>
                  </>
                ) : !diff ? (
                  <p role="status">正在核验归档章稿…</p>
                ) : (
                  <section aria-label="归档章稿对照">
                    <h4>{diff.title}</h4>
                    <p role="status">已核验归档原稿。</p>
                    <h5>归档修订 {diff.snapshot.revision}</h5>
                    <pre className="report">{diff.snapshot.content}</pre>
                    <h5>
                      本机当前修订 {diff.current?.revision ?? '无'}
                      {diff.current?.archived ? ' · 已归档' : ''}
                    </h5>
                    <pre className="report">{diff.current?.content ?? '本机没有对应章稿。'}</pre>
                  </section>
                ))}
            </>
          )}
        </section>
      )}
    </details>
  )
}
