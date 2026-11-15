import { useEffect, useId, useRef, useState } from 'react'
import { api, type Artifact } from './api'
import type { components } from './api-schema'

export type CandidateAcceptanceInput = Omit<
  components['schemas']['AcceptInput'],
  'memory_selection_reason'
> & {
  candidate_revision: number
  memory_fact_indices?: number[]
  memory_selection_reason?: string
  memory_facts_sha256?: string
}
type Choice = { owner: string; binding: string; indices: number[]; reason: string }
const VERSION = 'candidate-memory-selection/1'
const object = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value)
const text = (value: unknown) => (typeof value === 'string' ? value : '')

export function candidateMemory(artifact?: Artifact): { facts: unknown[]; error: string } {
  if (!artifact || !['chapter_draft', 'text_patch'].includes(artifact.kind)) return { facts: [], error: '' }
  const memory = artifact.payload.memory
  if (memory === undefined) return { facts: [], error: '' }
  if (!object(memory) || (memory.facts !== undefined && !Array.isArray(memory.facts)))
    return { facts: [], error: '记忆候选格式无法核对，请重新读取；没有自动省略或采纳任何记录。' }
  return { facts: Array.isArray(memory.facts) ? memory.facts : [], error: '' }
}

export function memorySelectionBinding(artifact: Artifact | undefined, target: string): string {
  return JSON.stringify([
    target,
    artifact?.id,
    artifact?.candidate_revision,
    artifact?.branch_id,
    artifact?.chapter_id,
    artifact?.base_revision,
    artifact?.memory_facts_sha256,
    candidateMemory(artifact),
  ])
}

function memorySelectionError(
  artifact: Artifact | undefined,
  indices: number[],
  reason: string,
  needsReconfirmation: boolean,
) {
  if (needsReconfirmation) return '候选或记忆来源已变化，原选择已失效；请读取新版记忆并重新选择后再采纳。'
  const memory = candidateMemory(artifact)
  if (memory.error) return memory.error
  if (artifact && ['chapter_draft', 'text_patch'].includes(artifact.kind)) {
    if (
      typeof artifact.memory_facts_sha256 !== 'string' ||
      !/^[0-9a-f]{64}$/.test(artifact.memory_facts_sha256)
    )
      return '记忆来源信息缺失，请重新读取候选后再采纳。'
    if (memory.facts.some((_, index) => !indices.includes(index)) && !reason.trim())
      return '省略记忆候选时，请填写不采纳的原因。'
  }
  return ''
}

export function memorySelectionRequest(
  artifact: Artifact,
  indices: number[],
  reason: string,
  needsReconfirmation = false,
): Omit<CandidateAcceptanceInput, 'idempotency_key'> {
  const error = memorySelectionError(artifact, indices, reason, needsReconfirmation)
  if (error) throw new Error(error)
  const base = { candidate_revision: artifact.candidate_revision }
  if (!['chapter_draft', 'text_patch'].includes(artifact.kind)) return base
  const omitted = candidateMemory(artifact).facts.some((_, index) => !indices.includes(index))
  return {
    ...base,
    memory_fact_indices: [...indices].sort((a, b) => a - b),
    memory_selection_reason: omitted ? reason.trim() : '',
    memory_facts_sha256: artifact.memory_facts_sha256!,
  }
}

export function useMemorySelection(artifact?: Artifact, target = '') {
  const memory = candidateMemory(artifact)
  const binding = memorySelectionBinding(artifact, target)
  const ownerIdentity = JSON.stringify([target, artifact?.id, artifact?.branch_id, artifact?.chapter_id])
  const ownerSession = useRef({ identity: ownerIdentity, epoch: 0, confirmedBinding: binding })
  if (ownerSession.current.identity !== ownerIdentity)
    ownerSession.current = {
      identity: ownerIdentity,
      epoch: ownerSession.current.epoch + 1,
      confirmedBinding: binding,
    }
  const owner = JSON.stringify([ownerIdentity, ownerSession.current.epoch])
  const responseScope = JSON.stringify([owner, binding])
  const currentBinding = useRef(responseScope)
  currentBinding.current = responseScope
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  const [choice, setChoice] = useState<Choice>()
  const all = memory.facts.map((_, index) => index)
  const initial = { owner, binding, indices: all, reason: '' }
  const needsReconfirmation = ownerSession.current.confirmedBinding !== binding
  const current = choice?.owner === owner && choice.binding === binding ? choice : initial
  const omitted = all.filter((index) => !current.indices.includes(index))
  const error = memorySelectionError(artifact, current.indices, current.reason, needsReconfirmation)
  const pending = useRef<{ signature: string; key: string } | undefined>(undefined)
  function change(update: (value: Choice) => Choice) {
    if (needsReconfirmation) return
    setChoice((previous) =>
      update(previous?.owner === owner && previous.binding === binding ? previous : initial),
    )
  }
  function input(fallbackKey: string): CandidateAcceptanceInput {
    if (!artifact) throw new Error('请先选择正文候选。')
    const selection = memorySelectionRequest(artifact, current.indices, current.reason, needsReconfirmation)
    if (!['chapter_draft', 'text_patch'].includes(artifact.kind))
      return { ...selection, idempotency_key: fallbackKey }
    setChoice(current)
    const signature = JSON.stringify([owner, binding, selection])
    if (pending.current?.signature !== signature) pending.current = { signature, key: crypto.randomUUID() }
    return { ...selection, idempotency_key: pending.current.key }
  }
  return {
    facts: memory.facts,
    indices: current.indices,
    reason: current.reason,
    omitted,
    error,
    needsReconfirmation,
    confirmSource: () => {
      if (
        !mounted.current ||
        ownerSession.current.identity !== ownerIdentity ||
        currentBinding.current !== responseScope
      )
        return
      ownerSession.current.confirmedBinding = binding
      pending.current = undefined
      setChoice(initial)
    },
    isCurrent: () => mounted.current && currentBinding.current === responseScope,
    include: (index: number, included: boolean) =>
      change((value) => ({
        ...value,
        indices: included
          ? [...value.indices.filter((item) => item !== index), index].sort((a, b) => a - b)
          : value.indices.filter((item) => item !== index),
      })),
    selectAll: () => change((value) => ({ ...value, indices: all })),
    selectNone: () => change((value) => ({ ...value, indices: [] })),
    setReason: (reason: string) => change((value) => ({ ...value, reason })),
    input,
    acknowledge: (request: CandidateAcceptanceInput) => {
      if (pending.current?.key === request.idempotency_key) pending.current = undefined
    },
  }
}

export function MemorySelectionPanel({
  selection,
  disabled = false,
}: {
  selection: ReturnType<typeof useMemorySelection>
  disabled?: boolean
}) {
  const reasonId = useId()
  if (selection.needsReconfirmation)
    return (
      <section aria-label="正文附带的记忆候选" style={{ overflowWrap: 'anywhere' }}>
        <p role="status">{selection.error}</p>
        <button type="button" disabled={disabled} onClick={selection.confirmSource}>
          读取新版记忆并重新选择
        </button>
      </section>
    )
  if (!selection.facts.length) return selection.error ? <p role="status">{selection.error}</p> : null
  return (
    <section aria-label="正文附带的记忆候选" style={{ overflowWrap: 'anywhere' }}>
      <details>
        <summary>
          随正文采纳的记忆候选 · 已选 {selection.indices.length} / {selection.facts.length} 项
        </summary>
        <p className="small muted">
          默认全选。逐项核对原陈述、故事时点和正文依据；本章审稿通过不表示这些派生记录已由作者核定。全不选可仅采纳正文，原候选和选择回执仍保留。
        </p>
        <div className="actions">
          <button type="button" disabled={disabled} onClick={selection.selectAll}>
            全选记忆候选
          </button>
          <button type="button" disabled={disabled} onClick={selection.selectNone}>
            全不选（仅采纳正文）
          </button>
        </div>
        {selection.facts.map((fact, index) => {
          const row = object(fact) ? fact : {}
          return (
            <article className="fact" key={index}>
              <label className="check">
                <input
                  type="checkbox"
                  checked={selection.indices.includes(index)}
                  disabled={disabled}
                  onChange={(event) => selection.include(index, event.target.checked)}
                />
                采纳记忆 {index + 1}：{text(row.statement) || '（陈述格式待核对）'}
              </label>
              <p>故事时点（原记录）：{text(row.story_time) || '（原记录未提供时点）'}</p>
              <blockquote>{text(row.evidence) || '（原记录未提供正文依据）'}</blockquote>
            </article>
          )
        })}
        {selection.omitted.length > 0 && (
          <label>
            不采纳部分记忆的原因
            <textarea
              value={selection.reason}
              maxLength={2000}
              required
              disabled={disabled}
              rows={3}
              aria-describedby={reasonId}
              onChange={(event) => selection.setReason(event.target.value)}
            />
          </label>
        )}
      </details>
      <p id={reasonId} role="status" className="small">
        {selection.error ||
          `将采纳 ${selection.indices.length} 项记忆，省略 ${selection.omitted.length} 项；正文仍按当前候选采纳。`}
      </p>
    </section>
  )
}

export function readMemorySelectionReceipt(value: unknown, artifactId: string) {
  if (
    !object(value) ||
    value.artifact_id !== artifactId ||
    (value.receipt !== null && !object(value.receipt))
  )
    return undefined
  if (value.receipt === null) return { state: 'none' as const }
  const receipt = value.receipt
  if (receipt.artifact_id !== artifactId) return undefined
  if (receipt.memory_selection === undefined) return { state: 'legacy' as const, receipt }
  const row = receipt.memory_selection
  if (
    !object(row) ||
    row.version !== VERSION ||
    !Number.isSafeInteger(row.fact_count) ||
    Number(row.fact_count) < 0 ||
    !Number.isSafeInteger(row.candidate_revision) ||
    Number(row.candidate_revision) < 0 ||
    !Array.isArray(row.selected_indices) ||
    !Array.isArray(row.omitted_indices) ||
    typeof row.reason !== 'string'
  )
    return undefined
  const count = Number(row.fact_count)
  const token = row.memory_facts_sha256
  if (token !== undefined && token !== null && (typeof token !== 'string' || !/^[a-f0-9]{64}$/.test(token)))
    return undefined
  const indices = [...row.selected_indices, ...row.omitted_indices]
  if (
    indices.length !== count ||
    new Set(indices).size !== count ||
    indices.some((index) => !Number.isSafeInteger(index) || Number(index) < 0 || Number(index) >= count) ||
    (row.omitted_indices.length > 0 && !row.reason.trim())
  )
    return undefined
  return {
    state: 'recorded' as const,
    receipt,
    selection: {
      fact_count: count,
      candidate_revision: Number(row.candidate_revision),
      selected_indices: row.selected_indices as number[],
      omitted_indices: row.omitted_indices as number[],
      reason: row.reason,
      memory_facts_sha256: typeof token === 'string' ? token : undefined,
    },
  }
}

export function MemoryAcceptanceRecord({ artifact }: { artifact: Artifact }) {
  const [record, setRecord] = useState<{ id: string; value: ReturnType<typeof readMemorySelectionReceipt> }>()
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const current = useRef(artifact.id)
  current.current = artifact.id
  const request = useRef(0)
  useEffect(() => {
    return () => {
      request.current++
    }
  }, [artifact.id])
  if (!candidateMemory(artifact).facts.length || artifact.status !== 'accepted') return null
  const loaded = record?.id === artifact.id ? record.value : undefined
  const facts = candidateMemory(artifact).facts
  const canMapFacts =
    loaded?.state === 'recorded' &&
    loaded.selection.candidate_revision === artifact.candidate_revision &&
    loaded.selection.fact_count === facts.length &&
    typeof loaded.selection.memory_facts_sha256 === 'string' &&
    loaded.selection.memory_facts_sha256 === artifact.memory_facts_sha256
  async function load() {
    const id = artifact.id,
      sequence = ++request.current
    const matches = () => current.current === id && request.current === sequence
    setLoading(true)
    setError('')
    try {
      const raw = await api<unknown>(`/artifacts/${encodeURIComponent(id)}/acceptance`)
      if (!matches()) return
      const value = readMemorySelectionReceipt(raw, id)
      if (!value) throw new Error('采纳选择回执格式无法核对，未把旧记录解释为当前选择。')
      setRecord({ id, value })
    } catch (failure) {
      if (matches()) setError(failure instanceof Error ? failure.message : '读取采纳记录失败。')
    } finally {
      if (matches()) setLoading(false)
    }
  }
  return (
    <details
      style={{ overflowWrap: 'anywhere' }}
      onToggle={(event) => {
        if (event.currentTarget.open && !loaded && !loading) void load()
      }}
    >
      <summary>正文采纳与记忆选择记录</summary>
      <p className="small muted">
        这是该候选当时的采纳回执；恢复或继续改稿后仍可查，不代表当前正文、文学质量或所有事实已验证。
      </p>
      {loading && <p role="status">正在读取采纳回执…</p>}
      {error && (
        <>
          <p role="status">{error}</p>
          <button type="button" onClick={() => void load()}>
            重新读取采纳回执
          </button>
        </>
      )}
      {loaded?.state === 'none' && <p>尚无采纳回执。</p>}
      {loaded?.state === 'legacy' && <p>历史回执未记录逐项记忆选择，不据此推定已核定全部事实。</p>}
      {loaded?.state === 'recorded' && (
        <>
          <p>
            原候选修订 {loaded.selection.candidate_revision}：采纳 {loaded.selection.selected_indices.length}{' '}
            / {loaded.selection.fact_count} 项记忆，省略 {loaded.selection.omitted_indices.length} 项。
          </p>
          <p>
            采纳原序号：
            {loaded.selection.selected_indices.map((index) => index + 1).join('、') || '无（仅采纳正文）'}
            ；省略原序号：{loaded.selection.omitted_indices.map((index) => index + 1).join('、') || '无'}。
          </p>
          {loaded.selection.reason && <p>原选择理由：{loaded.selection.reason}</p>}
          {canMapFacts ? (
            facts.map((fact, index) => {
              const row = object(fact) ? fact : {}
              return (
                <article className="fact" key={index}>
                  <strong>
                    原记忆 {index + 1} ·{' '}
                    {loaded.selection.selected_indices.includes(index) ? '当时采纳' : '当时省略'}
                  </strong>
                  <p>{text(row.statement) || '（陈述格式待核对）'}</p>
                  <p>故事时点（原记录）：{text(row.story_time) || '（原记录未提供时点）'}</p>
                  <blockquote>{text(row.evidence) || '（原记录未提供正文依据）'}</blockquote>
                </article>
              )
            })
          ) : (
            <p>当前候选的记忆范围尚未与原回执核实，仅保留当时的原序号和理由，不拼接为已确认的事实。</p>
          )}
        </>
      )}
      {loaded && 'receipt' in loaded && (
        <pre className="report">{JSON.stringify(loaded.receipt, null, 2)}</pre>
      )}
    </details>
  )
}
