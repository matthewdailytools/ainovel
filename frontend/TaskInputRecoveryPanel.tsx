import { useEffect, useRef, useState } from 'react'
import { api, type Task } from './api'
import {
  checkedRecoveryReferences,
  prepareTaskInputRecovery,
  type RecoveryTarget,
  type TaskInputRecovery,
} from './taskInputRecovery'

export function TaskInputRecoveryPanel({
  task,
  target,
  formBasis,
  disabled,
  onRestore,
}: {
  task: Task
  target: RecoveryTarget
  formBasis: string
  disabled: boolean
  onRestore: (preview: TaskInputRecovery, referenceIds: string[]) => boolean
}) {
  const [preview, setPreview] = useState<TaskInputRecovery>()
  const [chosen, setChosen] = useState<string[]>([])
  const [confirmed, setConfirmed] = useState(false)
  const [working, setWorking] = useState(false)
  const [message, setMessage] = useState('')
  const generation = useRef(0)
  const alive = useRef(true)
  const restored = useRef(false)
  const identity = JSON.stringify([task.id, target, formBasis, disabled])
  const currentIdentity = useRef(identity)
  currentIdentity.current = identity
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      ++generation.current
    }
  }, [])
  useEffect(() => {
    ++generation.current
    setPreview(undefined)
    setChosen([])
    setConfirmed(false)
    setWorking(false)
    setMessage(restored.current ? '已恢复任务输入及确认的资料。尚未生成或采纳正文。' : '')
    restored.current = false
  }, [identity])
  async function read(apply: boolean) {
    const token = ++generation.current
    const basis = identity
    const active = () => alive.current && token === generation.current && basis === currentIdentity.current
    setWorking(true)
    setMessage('正在读取原任务和当前可用资料…')
    try {
      const [freshTask, references] = await Promise.all([
        api<Task>(`/tasks/${encodeURIComponent(task.id)}`),
        api<unknown>(`/library/excerpts?novel_id=${encodeURIComponent(target.novelId)}`),
      ])
      if (!active()) return
      if (freshTask.id !== task.id) throw new Error('返回的任务身份已变化，请重新读取。')
      const fresh = prepareTaskInputRecovery(freshTask, target, references)
      if (apply) {
        if (!preview || fresh.basis !== preview.basis)
          throw new Error('原任务或资料在核对后发生变化，未恢复。请重新读取并确认。')
        const ids = checkedRecoveryReferences(fresh, chosen)
        if (!confirmed) throw new Error('请先确认本次恢复范围。')
        if (onRestore(fresh, ids)) {
          restored.current = true
          setPreview(undefined)
          setConfirmed(false)
          setMessage('已恢复任务输入及确认的资料。尚未生成或采纳正文。')
        }
      } else {
        setPreview(fresh)
        setChosen(fresh.references.filter((row) => row.status === 'available').map((row) => row.id))
        setConfirmed(false)
        setMessage('请核对原引用、当前资料内容及选区状态；取消不会改变当前输入和资料选择。')
      }
    } catch (error) {
      if (active()) {
        setConfirmed(false)
        setMessage(error instanceof Error ? error.message : '读取失败，当前输入与资料选择保留。')
      }
    } finally {
      if (active()) setWorking(false)
    }
  }
  return (
    <section className="notice" aria-label="恢复最近任务的输入与资料">
      <button type="button" disabled={disabled || working} onClick={() => void read(false)}>
        {preview ? '重新读取任务与资料' : '恢复最近任务的输入'}
      </button>
      {disabled && <p>请先结束当前操作或退出反馈修订，再核对要恢复的任务。</p>}
      {message && <p role="status">{message}</p>}
      {working && !preview && (
        <button
          type="button"
          onClick={() => {
            ++generation.current
            setWorking(false)
            setMessage('已取消恢复，当前输入和资料选择保留。')
          }}
        >
          取消恢复
        </button>
      )}
      {preview && (
        <>
          <p>
            原任务 {preview.task.id}。原任务只保存摘录标识，没有历史资料内容快照；以下为当前可用内容，
            不保证重现当时的模型上下文。
          </p>
          <fieldset>
            <legend>原任务引用清单</legend>
            {!preview.references.length && <p>原任务未选入摘录；确认后当前资料选择将清空。</p>}
            {preview.references.map((row) => (
              <div key={row.id}>
                {row.status === 'available' ? (
                  <label className="check">
                    <input
                      type="checkbox"
                      disabled={working}
                      checked={chosen.includes(row.id)}
                      onChange={(event) => {
                        setChosen((ids) =>
                          event.target.checked ? [...ids, row.id] : ids.filter((id) => id !== row.id),
                        )
                        setConfirmed(false)
                      }}
                    />
                    <span>
                      {row.excerpt?.title} · {row.id} · 当前可用
                    </span>
                  </label>
                ) : (
                  <p>
                    {row.id} · {row.status === 'missing' ? '当前列表未找到' : '当前不可用'}
                    ，请在资料书库重新核对，不会替换为其他摘录。
                  </p>
                )}
                {row.excerpt && (
                  <details>
                    <summary>查看当前摘录内容与来源</summary>
                    <p>{row.excerpt.source}</p>
                    <blockquote>{row.excerpt.quote}</blockquote>
                    <p>{row.excerpt.note}</p>
                  </details>
                )}
              </div>
            ))}
          </fieldset>
          <p>{preview.selectionNotice}</p>
          <label className="check">
            <input
              type="checkbox"
              disabled={working}
              checked={confirmed}
              onChange={(event) => setConfirmed(event.target.checked)}
            />
            我已核对当前内容；用所选 {chosen.length}{' '}
            项替换当前资料选择，未选或不可用的原引用不发送；选区不符时重新选择
          </label>
          <div className="actions">
            <button
              type="button"
              disabled={disabled || working || !confirmed}
              onClick={() => void read(true)}
            >
              确认恢复输入与资料
            </button>
            <button
              type="button"
              onClick={() => {
                ++generation.current
                setPreview(undefined)
                setConfirmed(false)
                setWorking(false)
                setMessage('已取消恢复，当前输入和资料选择保留。')
              }}
            >
              取消恢复
            </button>
          </div>
        </>
      )}
    </section>
  )
}
