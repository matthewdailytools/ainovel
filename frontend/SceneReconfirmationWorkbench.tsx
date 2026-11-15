import { useEffect, useRef, useState } from 'react'
import { api, type Chapter } from './api'
import { confirmLeave } from './leaveGuard'
import { SceneReconfirmation } from './SceneReconfirmationPanel'
import type { SceneRow } from './sceneReconfirmation'

export function SceneReconfirmationWorkbench({
  branchId,
  chapters,
  nameFor,
  onSaved,
}: {
  branchId: string
  chapters: Chapter[]
  nameFor: (id: string) => string
  onSaved: () => Promise<void>
}) {
  const [chapterId, setChapterId] = useState('')
  const [rows, setRows] = useState<SceneRow[]>([])
  const [sceneId, setSceneId] = useState('')
  const [dirty, setDirty] = useState(false)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const owner = JSON.stringify([branchId, chapterId])
  const liveOwner = useRef({ owner, epoch: 0 })
  if (liveOwner.current.owner !== owner) liveOwner.current = { owner, epoch: liveOwner.current.epoch + 1 }
  const epoch = liveOwner.current.epoch
  useEffect(() => {
    if (!chapterId) return
    let active = true
    setRows([])
    setSceneId('')
    setMessage('')
    setBusy(true)
    void api<{ scenes: SceneRow[] }>(`/branches/${branchId}/chapters/${chapterId}/scene-times`)
      .then((result) => {
        if (active) setRows(result.scenes)
      })
      .catch((error) => {
        if (active) setMessage(error instanceof Error ? error.message : '读取场景失败。')
      })
      .finally(() => {
        if (active) setBusy(false)
      })
    return () => {
      active = false
    }
  }, [owner, branchId, chapterId])
  const scene = rows.find((row) => row.id === sceneId)
  return (
    <details className="notice">
      <summary>重新核对已保存的提取场景</summary>
      {
        <section aria-label="提取场景重核工作台">
          <p>在原章节与同一场景中核对当前事件引用；仅作者明确确认后保存新修订，整书任务保持原状态。</p>
          <label>
            场景所在章节
            <select
              value={chapterId}
              onChange={(event) => {
                if (dirty && !confirmLeave()) return
                setChapterId(event.target.value)
                setDirty(false)
              }}
            >
              <option value="">请选择章节</option>
              {chapters.map((chapter) => (
                <option key={chapter.id} value={chapter.id}>
                  {chapter.title}
                </option>
              ))}
            </select>
          </label>
          {busy && <p role="status">正在读取已保存场景…</p>}
          {message && <p role="status">{message}</p>}
          {chapterId && !busy && !rows.length && <p>本章没有已保存的场景。</p>}
          {rows.map((row) => (
            <button
              type="button"
              key={row.id}
              onClick={() => {
                if (dirty && !confirmLeave()) return
                setSceneId(row.id)
                setDirty(false)
              }}
            >
              {row.payload.title} · 场景修订 {row.revision}
              {row.archived ? ' · 已归档' : row.stale ? ' · 待复核' : ''}
            </button>
          ))}
          {scene && (
            <SceneReconfirmation
              key={JSON.stringify([owner, scene.id])}
              branchId={branchId}
              chapterId={chapterId}
              scene={scene}
              nameFor={nameFor}
              onDirtyChange={setDirty}
              onSaved={async () => {
                const result = await api<{ scenes: SceneRow[] }>(
                  `/branches/${branchId}/chapters/${chapterId}/scene-times`,
                )
                if (liveOwner.current.owner !== owner || liveOwner.current.epoch !== epoch) return
                setRows(result.scenes)
                await onSaved()
              }}
            />
          )}
          {scene && !scene.payload.order_basis && <p>本场景没有已保存的提取事件依据，不能从这里重核。</p>}
        </section>
      }
    </details>
  )
}
