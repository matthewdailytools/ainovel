import { useEffect, useRef, useState } from 'react'
import {
  api,
  type Artifact,
  type ModuleInput,
  type Task,
  type IntentFields,
  type AuthorContract,
} from './api'
import type { Notify } from './App'

export type { IntentFields, AuthorContract } from './api'
const emptyFields: IntentFields = {
  emotion_target: '',
  core_hook: '',
  direction: '',
  ending: '',
  fixed_results: [],
  prohibitions: [],
  open_details: [],
}
const names: Record<keyof IntentFields, string> = {
  emotion_target: '情绪与阅读体验',
  core_hook: '核心钩子与追问',
  direction: '故事方向',
  ending: '终局倾向',
  fixed_results: '锁定结果',
  prohibitions: '禁止改变',
  open_details: '允许补充的空间',
}
function fieldSource(contract: AuthorContract, key: string) {
  if (!contract.provenance || typeof contract.provenance !== 'object') return ''
  const sources = (contract.provenance as { field_sources?: Record<string, unknown> }).field_sources
  const value = sources?.[key]
  if (Array.isArray(value))
    return value.some((row: { origin?: string }) => row.origin === 'ai_proposal')
      ? '含 AI 推导，待确认'
      : '来自作者原话'
  if (!value || typeof value !== 'object') return ''
  return (
    (
      {
        author_confirmed_proposal: '作者已确认 AI 提案',
        author_edited: '作者已编辑确认',
        author_provided: '作者填写',
      } as Record<string, string>
    )[String((value as { origin?: string }).origin)] || ''
  )
}
export function AuthorContractDetails({
  contract,
  proposal = false,
}: {
  contract: AuthorContract
  proposal?: boolean
}) {
  return (
    <div>
      <p className="small muted">
        {proposal
          ? 'AI 整理提案，尚未采用'
          : contract.configured
            ? '已采用的作者约定'
            : '尚未采用创作约定，可直接写作'}{' '}
        · 约定版本 {contract.contract_version}
      </p>
      {contract.author_text && <blockquote>{contract.author_text}</blockquote>}
      {contract.intent_contract &&
        (Object.keys(names) as (keyof IntentFields)[]).map((key) => {
          const value = contract.intent_contract![key]
          if (!value.length) return null
          return (
            <p key={key}>
              <strong>{names[key]}：</strong>
              {Array.isArray(value) ? value.join('；') : value}
              {fieldSource(contract, key) && <small className="muted"> · {fieldSource(contract, key)}</small>}
            </p>
          )
        })}
      <p className="small muted">
        来源 {contract.source_artifact_id?.slice(0, 8) || '未设置'} · 故事版本{' '}
        {contract.source_story_revision ?? '未设置'}。题材建议与模型推导不得静默改变作者锁定项。
      </p>
    </div>
  )
}

export function AuthorContractNotice({
  branchId,
  onSettings,
  onResolved,
}: {
  branchId: string
  onSettings: () => void
  onResolved: (value: AuthorContract | undefined) => void
}) {
  const [contract, setContract] = useState<AuthorContract>()
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    let active = true
    setContract(undefined)
    setError('')
    onResolved(undefined)
    void api<AuthorContract>(`/branches/${branchId}/author-contract`)
      .then((value) => {
        if (active) {
          setContract(value)
          onResolved(value)
        }
      })
      .catch((e: unknown) => {
        if (active) setError(e instanceof Error ? e.message : '读取创作约定失败')
      })
    return () => {
      active = false
    }
  }, [branchId, retry, onResolved])
  return (
    <details className="notice">
      <summary>
        本书创作约定 ·{' '}
        {error ? '读取失败' : contract ? (contract.configured ? '已采用' : '可选，尚未设置') : '正在读取'}
      </summary>
      {error ? <p role="alert">{error}</p> : contract && <AuthorContractDetails contract={contract} />}
      <div className="actions">
        <button type="button" onClick={onSettings}>
          设置情绪、钩子与方向
        </button>
        <button type="button" onClick={() => setRetry((v) => v + 1)}>
          刷新创作约定
        </button>
      </div>
    </details>
  )
}

function pendingProposals(artifacts: Artifact[]) {
  const used = new Set(
    artifacts
      .filter((row) => row.kind === 'intent' && row.status === 'accepted')
      .map(
        (row) =>
          (row.payload.provenance as { source_candidate_id?: string } | undefined)?.source_candidate_id,
      ),
  )
  return artifacts.filter(
    (row) =>
      row.kind === 'intent' &&
      row.status === 'candidate' &&
      row.payload.intent_contract &&
      row.payload.author_text &&
      !used.has(row.id),
  )
}

export function AuthorIntentPanel({
  branchId,
  novelId,
  account,
  modelReady,
  notify,
  onSaved,
}: {
  branchId: string
  novelId: string
  account: string
  modelReady: boolean
  notify: Notify
  onSaved: () => Promise<void>
}) {
  const [adopted, setAdopted] = useState<AuthorContract>()
  const [authorText, setAuthorText] = useState('')
  const [fields, setFields] = useState<IntentFields>(emptyFields)
  const [proposal, setProposal] = useState<Artifact>()
  const [proposals, setProposals] = useState<Artifact[]>([])
  const [sourceCandidate, setSourceCandidate] = useState<string | null>(null)
  const [task, setTask] = useState<Task>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [reload, setReload] = useState(0)
  const pending = useRef<{ signature: string; key: string } | null>(null)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    let active = true
    void Promise.all([
      api<AuthorContract>(`/branches/${branchId}/author-contract`),
      api<Artifact[]>(`/artifacts?branch_id=${branchId}`),
    ])
      .then(([value, artifacts]) => {
        if (!active) return
        const pending = pendingProposals(artifacts)
        setProposals(pending)
        setProposal(pending[0])
        setAdopted(value)
        setAuthorText(value.author_text)
        setFields(value.intent_contract || emptyFields)
        setSourceCandidate(null)
        setError('')
        setStatus('已载入采用版本；表单修改后需确认才参与后续生成。')
      })
      .catch((e: unknown) => {
        if (active) setError(e instanceof Error ? e.message : '读取创作约定失败')
      })
    return () => {
      active = false
      alive.current = false
    }
  }, [branchId, reload])
  const activeTask = task && ['queued', 'running'].includes(task.status)
  useEffect(() => {
    if (!activeTask || !task) return
    let active = true
    const poll = async () => {
      try {
        const row = await api<Task>(`/tasks/${task.id}`)
        if (!active) return
        if (row.status === 'succeeded') {
          const artifacts = await api<Artifact[]>(`/artifacts?branch_id=${branchId}`)
          if (active) {
            const pending = pendingProposals(artifacts)
            setProposals(pending)
            setProposal(
              artifacts.find((a) => a.id === (row.result.artifact_id || row.result.latest_artifact)),
            )
            setStatus('AI 提案已保存。先载入表单核对，确认采用后才成为本书约定。')
          }
        } else if (!['queued', 'running'].includes(row.status))
          setStatus(row.error || '整理任务已停止，原输入保留。')
        if (active) setTask(row)
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
  }, [task?.id, activeTask, branchId, notify])
  async function perform(fn: () => Promise<void>) {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await fn()
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e.message : '操作失败，输入保留')
      notify(e)
    } finally {
      if (alive.current) setBusy(false)
    }
  }
  async function compile() {
    if (!adopted) return
    const payload: ModuleInput = {
      include_scene_ranges: false,
      action: 'intent.compile',
      context_position: 0,
      novel_id: novelId,
      branch_id: branchId,
      instruction: authorText,
      writing_profile: '只整理创作约定，不生成正文。',
      max_output_tokens: 4096,
      account,
      author_contract_basis: {
        source_artifact_id: adopted.source_artifact_id,
        contract_version: adopted.contract_version,
      },
    }
    const signature = JSON.stringify(payload)
    if (pending.current?.signature !== signature) pending.current = { signature, key: crypto.randomUUID() }
    payload.client_request_id = pending.current.key
    const row = await api<Task>('/modules', 'POST', payload)
    if (!alive.current) return
    setTask(row)
    setProposal(undefined)
    pending.current = null
    setStatus('正在按作者原话整理提案，按当前账户计费；可取消，不会自动采用。')
  }
  async function save() {
    for (const [key, value] of Object.entries(fields)) {
      if (
        Array.isArray(value) &&
        (value.filter((v) => v.trim()).length > 20 || value.some((v) => v.length > 2000))
      )
        throw new Error(`${names[key as keyof IntentFields]}最多20项，每项不超过2000字。`)
    }
    const current = await api<AuthorContract>(`/branches/${branchId}/author-contract`)
    if (!adopted || current.source_artifact_id !== adopted.source_artifact_id)
      throw new Error('创作约定已在别处更新。当前输入保留，请先复制，再重新载入采用版本。')
    await api(`/branches/${branchId}/definitions`, 'POST', {
      kind: 'intent',
      text: authorText,
      intent_contract: Object.fromEntries(
        Object.entries(fields).map(([key, value]) => [
          key,
          Array.isArray(value) ? value.map((v) => v.trim()).filter(Boolean) : value.trim(),
        ]),
      ),
      intent_source_candidate_id: sourceCandidate,
      base_story_revision: current.story_revision,
    })
    const value = await api<AuthorContract>(`/branches/${branchId}/author-contract`)
    if (!alive.current) return
    setAdopted(value)
    const remaining = proposals.filter((row) => row.id !== sourceCandidate)
    setProposals(remaining)
    setProposal(remaining[0])
    setSourceCandidate(null)
    setStatus('已确认并采用。后续新任务继承本约定；旧候选保留其生成时来源。')
    await onSaved()
  }
  function change(key: keyof IntentFields, value: string) {
    setFields((old) => ({ ...old, [key]: Array.isArray(old[key]) ? value.split('\n') : value }))
    setStatus('创作约定草稿尚未采用。')
  }
  return (
    <section className="card" id="author-intent">
      <h2>创作约定：情绪、钩子与方向</h2>
      <p>
        写一句想法也可以开始。按需区分锁定条件与开放空间，整理提案后由你确认；无需先填写人物百科或完整大纲。
      </p>
      {adopted && (
        <details key="adopted-contract">
          <summary>查看当前采用的创作约定</summary>
          <AuthorContractDetails contract={adopted} />
        </details>
      )}
      {error && (
        <p role="alert" className="notice">
          {error}
        </p>
      )}
      <details key="contract-editor">
        <summary>编写或整理创作约定（确认后生效）</summary>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            void perform(save)
          }}
        >
          <fieldset disabled={busy || !adopted}>
            <label>
              作者原话与创作想法
              <textarea
                rows={4}
                value={authorText}
                onChange={(e) => {
                  setAuthorText(e.target.value)
                  setStatus('原话草稿尚未采用。')
                }}
                maxLength={20_000}
                required
              />
            </label>
            <div className="actions">
              <button
                type="button"
                disabled={!modelReady || !!activeTask || !authorText.trim()}
                onClick={() => void perform(compile)}
              >
                用当前模型整理为待确认提案
              </button>
            </div>
            <p className="small muted">
              {modelReady ? `使用账户 ${account}，遵守已配置的累计预算。` : '未配置模型仍可手动填写并采用。'}{' '}
              AI 补出的情绪、钩子、方向属于提案；锁定结果和禁止项须由作者明确确认。
            </p>
            {(Object.keys(names) as (keyof IntentFields)[]).map((key) => (
              <label key={key}>
                {names[key]}
                {Array.isArray(fields[key]) ? '（每行一项，可留空）' : '（可留空）'}
                <textarea
                  rows={2}
                  value={
                    Array.isArray(fields[key])
                      ? (fields[key] as string[]).join('\n')
                      : (fields[key] as string)
                  }
                  onChange={(e) => change(key, e.target.value)}
                  maxLength={Array.isArray(fields[key]) ? 40_000 : key === 'direction' ? 6000 : 4000}
                />
              </label>
            ))}
            <button className="primary" disabled={!authorText.trim() || !!activeTask}>
              确认并采用编辑后的创作约定
            </button>
          </fieldset>
        </form>
        {activeTask && (
          <button
            disabled={busy}
            onClick={() =>
              void perform(async () => {
                await api(`/tasks/${task!.id}/cancel`, 'POST')
                setStatus('已请求取消，正在等待任务保存现场。')
              })
            }
          >
            取消整理提案
          </button>
        )}
        {proposals.length > 1 && (
          <label>
            待确认创作提案
            <select
              value={proposal?.id || ''}
              onChange={(e) => setProposal(proposals.find((row) => row.id === e.target.value))}
            >
              {proposals.map((row, index) => (
                <option value={row.id} key={row.id}>
                  提案 {proposals.length - index} · {row.id.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
        )}
        {proposal && (
          <div className="notice">
            <h3>AI 整理提案 · 尚未采用</h3>
            <AuthorContractDetails
              proposal
              contract={{
                contract_version: String(proposal.payload.contract_version || '1.0.0'),
                configured: false,
                author_text: String(proposal.payload.author_text || ''),
                intent_contract: proposal.payload.intent_contract as IntentFields,
                provenance: proposal.payload.provenance,
                source_artifact_id: proposal.id,
                source_story_revision:
                  typeof proposal.basis.story_revision === 'number' ? proposal.basis.story_revision : null,
              }}
            />
            <button
              disabled={busy || !!activeTask}
              onClick={() => {
                const proposed = proposal.payload.intent_contract as IntentFields | undefined
                if (!proposed) return
                setFields({ ...emptyFields, ...proposed })
                setAuthorText(String(proposal.payload.author_text || authorText))
                setSourceCandidate(proposal.id)
                setStatus('已载入 AI 提案供你编辑，尚未采用；请逐项核对后确认。')
              }}
            >
              载入表单核对与编辑
            </button>
          </div>
        )}
      </details>
      <p role="status" className="small">
        {status}
      </p>
      <button disabled={busy || !!activeTask} onClick={() => setReload((v) => v + 1)}>
        重新载入采用版本（替换表单草稿）
      </button>
    </section>
  )
}
