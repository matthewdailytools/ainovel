import { useEffect, useRef, useState, type SetStateAction } from 'react'
import { discardOwnDraft, readDraft, writeDraft } from './drafts'

type DraftOptions<T> = {
  key: string
  label: string
  owner: () => Promise<string>
  parse: (value: unknown) => T
}

function unpackDraft(text: string): unknown {
  const raw: unknown = JSON.parse(text)
  if (!raw || typeof raw !== 'object' || !('format' in raw) || raw.format !== 1 || !('value' in raw))
    throw new Error('无法识别本机草稿。')
  return raw.value
}

export function useWorkbenchDraft<T>({ key, label, owner, parse }: DraftOptions<T>) {
  const [draft, setValue] = useState<T>()
  const [recovery, setRecovery] = useState<{ value: T; foreign: boolean }>()
  const [loaded, setLoaded] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const current = useRef<T | undefined>(undefined)
  const write = useRef(0)
  useEffect(() => {
    let active = true
    void owner()
      .then((id) => readDraft(key, id))
      .then((draft) => {
        if (active && draft)
          setRecovery({ value: parse(unpackDraft(draft.text)), foreign: draft.fromOtherWindow === true })
      })
      .catch(() => {
        if (active) setError(`无法读取本机${label}草稿。`)
      })
      .finally(() => {
        if (active) setLoaded(true)
      })
    return () => {
      active = false
      write.current++
    }
  }, [key, label, owner, parse])

  function setDraft(update: SetStateAction<T | undefined>) {
    const next =
      typeof update === 'function'
        ? (update as (value: T | undefined) => T | undefined)(current.current)
        : update
    current.current = next
    setValue(next)
    setRecovery(undefined)
    setStatus(next === undefined ? '' : `正在暂存${label}草稿…`)
    setError('')
    const attempt = ++write.current
    void owner()
      .then((id) =>
        next === undefined
          ? discardOwnDraft(key, id)
          : writeDraft(
              { id: key, base: 0, text: JSON.stringify({ format: 1, value: next }), updated: Date.now() },
              id,
            ),
      )
      .then(
        () => {
          if (attempt === write.current && next !== undefined) setStatus(`${label}草稿已暂存到本机。`)
        },
        () => {
          if (attempt !== write.current) return
          setStatus('')
          setError(
            next === undefined
              ? '未能清除本机草稿，请重开核对。'
              : `无法暂存${label}草稿，当前输入仍在本页。`,
          )
        },
      )
  }

  return { draft, setDraft, recovery, loaded, status, error }
}

export function WorkbenchDraftRecovery({
  label,
  foreign,
  disabled,
  onEdit,
  onDiscard,
}: {
  label: string
  foreign: boolean
  disabled: boolean
  onEdit: () => void
  onDiscard: () => void
}) {
  return (
    <section aria-label={`本机${label}草稿恢复`}>
      <p>{foreign ? `另一窗口有未确认的${label}草稿。` : `有未确认的本机${label}草稿。`}</p>
      <button type="button" disabled={disabled} onClick={onEdit}>
        {foreign ? `取用另一窗口的${label}草稿副本` : `继续编辑本机${label}草稿`}
      </button>
      {!foreign && (
        <button
          type="button"
          disabled={disabled}
          onClick={() => {
            if (window.confirm(`放弃这份未确认的本机${label}草稿？`)) onDiscard()
          }}
        >
          放弃本机{label}草稿
        </button>
      )}
    </section>
  )
}
