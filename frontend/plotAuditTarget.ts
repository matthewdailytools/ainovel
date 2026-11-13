import { useEffect, useState } from 'react'
import { api, type Artifact } from './api'
import { readPlotAudit } from './plotAudit'

type ComparisonTarget = {
  kind: 'candidate' | 'saved_manuscript' | 'generated_candidate'
  chapter_id: string
  base_revision: number
  content_sha256: string
  candidate?: { id: string; revision: number }
}
export type PlotAuditTarget = {
  kind: 'saved_chapter' | 'candidate' | 'generated_candidate'
  chapterId: string
  revision: number
  sha256: string
  candidateId?: string
}
export type PlotAuditTargetState = {
  status: 'ready' | 'historical' | 'loading' | 'unavailable'
  message?: string
  candidateRevision?: number
  candidateText?: string
  savedChapter?: { id: string; revision: number; content: string }
}

const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
const revision = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
const hash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value)

export function readComparisonTarget(value: unknown): ComparisonTarget | undefined {
  if (
    !object(value) ||
    (value.kind !== 'candidate' &&
      value.kind !== 'saved_manuscript' &&
      value.kind !== 'generated_candidate') ||
    typeof value.chapter_id !== 'string' ||
    !value.chapter_id ||
    !revision(value.base_revision) ||
    !hash(value.content_sha256)
  )
    return undefined
  let candidate: ComparisonTarget['candidate']
  if (value.candidate !== undefined && value.candidate !== null) {
    if (
      !object(value.candidate) ||
      typeof value.candidate.id !== 'string' ||
      !value.candidate.id ||
      !revision(value.candidate.revision)
    )
      return undefined
    candidate = { id: value.candidate.id, revision: value.candidate.revision }
  }
  if ((value.kind === 'candidate') !== !!candidate) return undefined
  return {
    kind: value.kind,
    chapter_id: value.chapter_id,
    base_revision: value.base_revision,
    content_sha256: value.content_sha256,
    candidate,
  }
}

export function comparisonPlotTarget(value: unknown): PlotAuditTarget | undefined {
  if (!object(value)) return undefined
  const audit = readPlotAudit(value.plot_audit)
  const target = readComparisonTarget(value.target)
  if (value.target !== undefined && !target) return undefined
  if (target) {
    if (
      audit?.binding &&
      (audit.binding.candidate_sha256 !== target.content_sha256 ||
        (target.candidate && audit.binding.candidate_revision !== target.candidate.revision))
    )
      return undefined
    if (
      audit?.target &&
      (target.kind !== 'saved_manuscript' ||
        audit.target.chapter_id !== target.chapter_id ||
        audit.target.revision !== target.base_revision ||
        audit.target.content_sha256 !== target.content_sha256)
    )
      return undefined
    return {
      kind: target.kind === 'saved_manuscript' ? 'saved_chapter' : target.kind,
      chapterId: target.chapter_id,
      revision: target.candidate?.revision ?? target.base_revision,
      sha256: target.content_sha256,
      candidateId: target.candidate?.id,
    }
  }
  return (
    audit?.target && {
      kind: 'saved_chapter',
      chapterId: audit.target.chapter_id,
      revision: audit.target.revision,
      sha256: audit.target.content_sha256,
    }
  )
}

export function validatePlotAuditTarget(
  target: PlotAuditTarget,
  branchId: string,
  value: unknown,
  localText?: string,
): PlotAuditTargetState {
  if (
    !object(value) ||
    typeof value.id !== 'string' ||
    value.branch_id !== branchId ||
    value.id !== (target.kind === 'saved_chapter' ? target.chapterId : target.candidateId)
  )
    throw new Error('检查对象的身份或工作版本无法确认。')
  if (target.kind === 'saved_chapter') {
    if (!revision(value.revision) || typeof value.content !== 'string')
      throw new Error('保存稿内容或修订无法确认。')
    return {
      status: value.revision !== target.revision || value.archived ? 'historical' : 'ready',
      savedChapter: { id: value.id, revision: value.revision, content: value.content },
    }
  }
  if (
    value.chapter_id !== target.chapterId ||
    !revision(value.candidate_revision) ||
    !object(value.payload) ||
    typeof value.payload.text !== 'string' ||
    (value.kind !== 'chapter_draft' && value.kind !== 'text_patch')
  )
    throw new Error('被检候选的章节、修订或正文无法确认。')
  return {
    status:
      (target.kind === 'candidate' && value.candidate_revision !== target.revision) ||
      value.status === 'rejected'
        ? 'historical'
        : 'ready',
    candidateRevision: value.candidate_revision,
    candidateText: localText ?? value.payload.text,
  }
}

export async function loadPlotAuditTarget(
  target: PlotAuditTarget,
  branchId: string,
  read: (path: string) => Promise<unknown>,
) {
  if (target.kind === 'generated_candidate') throw new Error('生成候选须由当前显示对象确认。')
  if (target.kind === 'candidate' && !target.candidateId) throw new Error('被检候选身份无法确认。')
  const path =
    target.kind === 'saved_chapter'
      ? `/chapters/${encodeURIComponent(target.chapterId)}`
      : `/artifacts/${encodeURIComponent(target.candidateId || '')}`
  return validatePlotAuditTarget(target, branchId, await read(path))
}

export function createPlotAuditTargetLoader(
  read: (path: string) => Promise<unknown>,
  publish: (state: PlotAuditTargetState) => void,
) {
  let sequence = 0
  return {
    async load(target: PlotAuditTarget, branchId: string) {
      const request = ++sequence
      try {
        const state = await loadPlotAuditTarget(target, branchId, read)
        if (request === sequence) publish(state)
      } catch (error) {
        if (request === sequence)
          publish({
            status: 'unavailable',
            message: error instanceof Error ? error.message : '检查对象读取失败。',
          })
      }
    },
    invalidate() {
      sequence += 1
    },
  }
}

export function usePlotAuditTarget(
  value: unknown,
  branchId?: string,
  candidate?: Artifact,
  localText?: string,
  historical = false,
) {
  const target = comparisonPlotTarget(value)
  const key = JSON.stringify([branchId, target])
  const [loaded, setLoaded] = useState<{ key: string; state: PlotAuditTargetState }>()
  useEffect(() => {
    if (!target || !branchId || target.kind === 'generated_candidate' || historical) return
    const loader = createPlotAuditTargetLoader(api, (state) => setLoaded({ key, state }))
    const load = () => void loader.load(target, branchId)
    load()
    const timer = setInterval(load, 2000)
    return () => {
      loader.invalidate()
      clearInterval(timer)
    }
  }, [key, historical])
  let state: PlotAuditTargetState
  if (!target || !branchId)
    state = { status: 'unavailable', message: '检查对象或所属工作版本无法确认，不能作为当前稿结论。' }
  else if (historical) state = { status: 'historical' }
  else if (target.kind === 'generated_candidate') {
    try {
      state = validatePlotAuditTarget(
        { ...target, candidateId: candidate?.id },
        branchId,
        candidate,
        localText,
      )
    } catch (error) {
      state = {
        status: 'unavailable',
        message: error instanceof Error ? error.message : '生成候选无法确认。',
      }
    }
  } else state = loaded?.key === key ? loaded.state : { status: 'loading' }
  return { target, state }
}
