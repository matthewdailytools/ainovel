import type { Artifact } from './api'

export type DirectionBasis = {
  advice_id: string
  candidate_revision: number
  novel_id: string
  branch_id: string
  chapter_id: string
  source_revision: number | null
  loaded_revision: number
}

type Target = { novelId: string; branchId: string; chapterId: string; revision: number }

export function adviceDirectionBasis(
  artifact: Artifact & { novel_id?: unknown; branch_id?: unknown },
  target: Target,
  expectedRevision = artifact.candidate_revision,
): DirectionBasis {
  if (artifact.kind !== 'advice' || !['candidate', 'accepted'].includes(artifact.status))
    throw new Error('只有尚未拒绝的方向建议可以由作者选择并载入。')
  if (
    artifact.novel_id !== target.novelId ||
    artifact.branch_id !== target.branchId ||
    artifact.chapter_id !== target.chapterId
  )
    throw new Error('建议不属于当前作品、工作版本或章节，请回到来源章节后选择方向。')
  if (artifact.candidate_revision !== expectedRevision)
    throw new Error('来源建议已更新，选定方向仍保留；请载入已保存的候选并重新核对。')
  return {
    advice_id: artifact.id,
    candidate_revision: artifact.candidate_revision,
    novel_id: target.novelId,
    branch_id: target.branchId,
    chapter_id: target.chapterId,
    source_revision: artifact.base_revision,
    loaded_revision: target.revision,
  }
}

export function checkDirectionTarget(basis: DirectionBasis, target: Target) {
  if (
    basis.novel_id !== target.novelId ||
    basis.branch_id !== target.branchId ||
    basis.chapter_id !== target.chapterId
  )
    throw new Error('选定方向的目标已改变，请回到来源章节或清除方向来源后重新填写要求。')
}

export function directionExpansionInstruction(basis: DirectionBasis, authorDirection: string) {
  return `作者本次确认并可编辑的情节方向：\n${authorDirection}\n\n方向来源记录：${JSON.stringify(basis)}\n以上来源只用于追踪作者从建议中作出的选择，不是本书设定或故事事实。仅按作者本次确认的方向补写，继续保持本次固定条件、写法与主动选入的资料。`
}

export function readDirectionExpansionInstruction(instruction: string) {
  const prefix = '作者本次确认并可编辑的情节方向：\n'
  if (!instruction.startsWith(prefix)) return undefined
  const marker = '\n\n方向来源记录：'
  const start = instruction.lastIndexOf(marker)
  const end = instruction.indexOf('\n以上来源只用于追踪', start)
  try {
    if (start < prefix.length || end < start) throw new Error('invalid source')
    const basis: unknown = JSON.parse(instruction.slice(start + marker.length, end))
    if (!basis || typeof basis !== 'object') throw new Error('invalid source')
    const data = basis as Record<string, unknown>
    if (
      !['advice_id', 'novel_id', 'branch_id', 'chapter_id'].every(
        (key) => typeof data[key] === 'string' && data[key] !== '',
      ) ||
      !Number.isSafeInteger(data.candidate_revision) ||
      (data.candidate_revision as number) < 0 ||
      !Number.isSafeInteger(data.loaded_revision) ||
      (data.loaded_revision as number) < 0 ||
      !(
        data.source_revision === null ||
        (Number.isSafeInteger(data.source_revision) && (data.source_revision as number) >= 0)
      )
    )
      throw new Error('invalid source')
    return { basis: basis as DirectionBasis, instruction: instruction.slice(prefix.length, start) }
  } catch {
    throw new Error('最近任务的方向来源记录无法核对，当前输入保留；请从原建议重新选择方向。')
  }
}
