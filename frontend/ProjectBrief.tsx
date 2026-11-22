import { useEffect, useRef, useState } from 'react'
import { api, label } from './api'
import { useLeaveGuard } from './leaveGuard'

const fields = [
  { key: 'concept', label: '创作思路', max: 6000 },
  { key: 'positioning', label: '核心定位', max: 2000 },
  { key: 'audience', label: '目标读者', max: 2000 },
  { key: 'scope', label: '篇幅与更新计划', max: 2000 },
  { key: 'direction', label: '故事方向', max: 6000 },
  { key: 'ending', label: '预定结局', max: 4000 },
  { key: 'last_image', label: '最后一镜', max: 2000 },
] as const

type TransferField = 'direction' | 'ending'
type BriefValues = Record<(typeof fields)[number]['key'], string>
type BriefVersion = {
  id: string
  fields: BriefValues
  draft_id: string | null
  intent_id: string | null
  applied_fields: TransferField[]
}
type BriefState = {
  draft: BriefVersion | null
  adopted: BriefVersion | null
  author_contract: { id: string | null; direction: string; ending: string }
  referencing_tasks: { id: string; kind: string; status: string }[]
  referencing_task_count: number
}
type Edit = { baseId: string | null; fields: BriefValues }

const blank: BriefValues = {
  concept: '',
  positioning: '',
  audience: '',
  scope: '',
  direction: '',
  ending: '',
  last_image: '',
}

export function ProjectBrief({
  branchId,
  idea,
  observedContractId,
  onContractChanged,
}: {
  branchId: string
  idea: string
  observedContractId?: string | null
  onContractChanged: () => Promise<void>
}) {
  const path = `/branches/${encodeURIComponent(branchId)}/project-brief`
  const [state, setState] = useState<BriefState>()
  const [edit, setEdit] = useState<Edit>()
  const [selection, setSelection] = useState<TransferField[]>([])
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const alive = useRef(false)
  const running = useRef(false)
  const latestRead = useRef(0)
  const adoption = useRef<{ signature: string; key: string } | undefined>(undefined)
  const values = edit?.fields ?? state?.draft?.fields ?? { ...blank, concept: idea }
  const currentDraftId = state?.draft?.id ?? null
  const adoptedCurrent = currentDraftId !== null && currentDraftId === state?.adopted?.draft_id
  const stale = !!edit && edit.baseId !== currentDraftId
  const transfer = selection.filter((key) => !!values[key].trim())
  useLeaveGuard(!!edit || busy, '未保存的立项简报')

  useEffect(() => {
    alive.current = true
    const read = ++latestRead.current
    void api<BriefState>(path)
      .then((data) => {
        if (alive.current && read === latestRead.current) setState(data)
      })
      .catch((failure: unknown) => {
        if (alive.current && read === latestRead.current)
          setError(failure instanceof Error ? failure.message : '未能读取立项简报。')
      })
    return () => {
      alive.current = false
      latestRead.current++
    }
  }, [path, observedContractId])

  async function act(operation: () => Promise<void>) {
    if (running.current) return
    running.current = true
    latestRead.current++
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await operation()
    } catch (failure) {
      if (alive.current) setError(failure instanceof Error ? failure.message : '操作失败，输入已保留。')
    } finally {
      running.current = false
      if (alive.current) setBusy(false)
    }
  }

  function save() {
    if (!state || stale) return
    void act(async () => {
      const data = await api<BriefState>(path, 'PUT', {
        base_draft_id: edit?.baseId ?? currentDraftId,
        fields: values,
      })
      if (!alive.current) return
      setState(data)
      setEdit(undefined)
      setNotice('立项草稿已保存。')
    })
  }

  function confirm() {
    if (!currentDraftId || !state || edit) return
    const body = {
      draft_id: currentDraftId,
      base_adoption_id: state.adopted?.id ?? null,
      base_author_contract_id: state.author_contract.id,
      update_fields: transfer,
    }
    const signature = JSON.stringify(body)
    if (adoption.current?.signature !== signature) adoption.current = { signature, key: crypto.randomUUID() }
    const idempotency_key = adoption.current.key
    void act(async () => {
      const data = await api<BriefState>(path + '/adopt', 'POST', { ...body, idempotency_key })
      if (!alive.current) return
      adoption.current = undefined
      setState(data)
      setSelection([])
      setNotice(
        transfer.length && data.adopted?.applied_fields.length
          ? '立项简报已确认，所选字段已同步到创作约定。'
          : '立项简报已确认。',
      )
      if (data.author_contract.id !== body.base_author_contract_id) await onContractChanged()
    })
  }

  const heading = state?.draft
    ? adoptedCurrent
      ? '立项简报（已确认）'
      : '继续上次立项草稿'
    : '立项简报（可选）'

  return (
    <details className="card">
      <summary>{heading}</summary>
      <p className="small muted">填写已经想清楚的部分。保存后可回来继续，也能先去写正文。</p>
      {!state && !error && <p role="status">正在读取立项简报…</p>}
      {state && (
        <>
          {fields.map(({ key, label: title, max }) => (
            <label key={key}>
              {title}
              <textarea
                rows={key === 'concept' || key === 'direction' ? 3 : 2}
                maxLength={max}
                disabled={busy}
                value={values[key]}
                onChange={(event) => {
                  const value = event.target.value
                  setEdit((current) => ({
                    baseId: current ? current.baseId : currentDraftId,
                    fields: { ...(current?.fields ?? values), [key]: value },
                  }))
                }}
              />
            </label>
          ))}
          {state.draft && (
            <fieldset disabled={busy}>
              <legend>同步到创作约定（可选）</legend>
              {(['direction', 'ending'] as const).map((key) => {
                const title = key === 'direction' ? '故事方向' : '预定结局'
                return (
                  <div key={key}>
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={selection.includes(key)}
                        disabled={!values[key].trim()}
                        onChange={(event) => {
                          const checked = event.target.checked
                          setSelection((prior) =>
                            checked ? [...prior, key] : prior.filter((k) => k !== key),
                          )
                        }}
                      />
                      更新{title}
                    </label>
                    {selection.includes(key) && (
                      <p className="small">
                        当前{title}为 {state.author_contract[key] || '未设置'}
                      </p>
                    )}
                  </div>
                )
              })}
              {transfer.length > 0 && (
                <p className="small muted">同步的字段供后续新任务使用。既有任务可查看各自冻结的旧约定。</p>
              )}
              {transfer.length > 0 && state.referencing_task_count > 0 && (
                <details>
                  <summary>{state.referencing_task_count} 个任务引用当前约定</summary>
                  <ul className="small">
                    {state.referencing_tasks.map((task) => (
                      <li key={task.id}>
                        {task.kind === 'workflow' ? '整书创作' : '辅助任务'} · {task.id.slice(0, 8)} ·{' '}
                        {label(task.status)}
                      </li>
                    ))}
                  </ul>
                  {state.referencing_task_count > state.referencing_tasks.length && (
                    <p className="small muted">此处显示最近 {state.referencing_tasks.length} 个任务。</p>
                  )}
                </details>
              )}
            </fieldset>
          )}
          {stale && (
            <div className="notice">
              <p>已保存草稿有新版本，当前输入仍在。核对后可以保存为新的草稿版本。</p>
              <details>
                <summary>查看最新已保存的内容</summary>
                {fields.map(({ key, label: title }) =>
                  state.draft?.fields[key] ? (
                    <p key={key}>
                      <strong>{title}</strong> {state.draft.fields[key]}
                    </p>
                  ) : null,
                )}
              </details>
              <button
                type="button"
                disabled={busy}
                onClick={() => setEdit((prior) => prior && { ...prior, baseId: currentDraftId })}
              >
                保留我的输入继续编辑
              </button>
            </div>
          )}
          <div className="actions">
            <button
              type="button"
              disabled={busy || stale || !Object.values(values).some((value) => value.trim())}
              onClick={save}
            >
              保存立项草稿
            </button>
            {state.draft && (
              <button
                type="button"
                disabled={busy || !!edit || (adoptedCurrent && !transfer.length)}
                onClick={confirm}
              >
                确认立项简报
              </button>
            )}
          </div>
          {adoptedCurrent && <p className="small muted">当前保存稿已确认为本工作版本的立项简报。</p>}
          {state.adopted && !adoptedCurrent && (
            <details>
              <summary>查看上次确认的立项简报</summary>
              {fields.map(({ key, label: title }) =>
                state.adopted?.fields[key] ? (
                  <p key={key}>
                    <strong>{title}</strong> {state.adopted.fields[key]}
                  </p>
                ) : null,
              )}
            </details>
          )}
        </>
      )}
      {busy && <p role="status">正在提交立项简报…</p>}
      {notice && <p role="status">{notice}</p>}
      {error && (
        <div className="notice" role="alert">
          <p>{error}</p>
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void act(async () => {
                const data = await api<BriefState>(path)
                if (!alive.current) return
                setState(data)
                setSelection([])
                setNotice('已重新读取保存稿和创作约定。')
              })
            }
          >
            重新读取立项版本
          </button>
        </div>
      )}
    </details>
  )
}
