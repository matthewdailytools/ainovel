import type { Artifact, Task } from './api'

export const CHARACTER_FIELD_REPAIR_VERSION = 'character-field-repair/1'
export const CHARACTER_FIELD_REPAIR_V2_VERSION = 'character-field-repair/2'
type CharacterRepairVersion = typeof CHARACTER_FIELD_REPAIR_VERSION | typeof CHARACTER_FIELD_REPAIR_V2_VERSION

function repairVersion(value: unknown): value is CharacterRepairVersion {
  return value === CHARACTER_FIELD_REPAIR_VERSION || value === CHARACTER_FIELD_REPAIR_V2_VERSION
}
export type CharacterFieldDiff = {
  record_id: string
  field: 'notes' | 'profile'
  before: string
  after: string
  source_ids: string[]
  reason: string
}
export type CharacterRepairBasis = {
  version: CharacterRepairVersion
  root_artifact_id: string
  parent_artifact_id: string
  round: number
  input_sha256: string
  diff: CharacterFieldDiff[]
}
type RepairChain = {
  version: CharacterRepairVersion
  root_artifact_id: string
  current_artifact_id: string
  rounds: { round: number; parent_artifact_id: string; child_artifact_id?: string }[]
}
export type CharacterRepairAccess = {
  readOnly: boolean
  reason: string
  rootId?: string
  currentId?: string
  parentId?: string
}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}
function text(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}
function round(value: unknown): value is number {
  return value === 1 || value === 2
}

export function characterFieldRepairEnabled(value: unknown, adoption: unknown, maintenance: unknown) {
  return value === true && adoption === 'automatic' && maintenance === true
}

export function readCharacterRepairBasis(value: unknown): CharacterRepairBasis | undefined {
  if (
    !object(value) ||
    !repairVersion(value.version) ||
    !text(value.root_artifact_id) ||
    !text(value.parent_artifact_id) ||
    !round(value.round) ||
    typeof value.input_sha256 !== 'string' ||
    !/^[a-f0-9]{64}$/.test(value.input_sha256) ||
    !Array.isArray(value.diff) ||
    !value.diff.length ||
    value.diff.length > 120
  )
    return undefined
  const diff: CharacterFieldDiff[] = []
  const seen = new Set<string>()
  for (const row of value.diff) {
    if (
      !object(row) ||
      !text(row.record_id) ||
      (row.field !== 'notes' && row.field !== 'profile') ||
      typeof row.before !== 'string' ||
      !text(row.after) ||
      row.before === row.after ||
      !Array.isArray(row.source_ids) ||
      !row.source_ids.length ||
      row.source_ids.length > 30 ||
      !row.source_ids.every(text) ||
      new Set(row.source_ids).size !== row.source_ids.length ||
      !text(row.reason)
    )
      return undefined
    const key = `${row.record_id}/${row.field}`
    if (seen.has(key)) return undefined
    seen.add(key)
    diff.push({
      record_id: row.record_id,
      field: row.field,
      before: row.before,
      after: row.after,
      source_ids: row.source_ids,
      reason: row.reason,
    })
  }
  return {
    version: value.version,
    root_artifact_id: value.root_artifact_id,
    parent_artifact_id: value.parent_artifact_id,
    round: value.round,
    input_sha256: value.input_sha256,
    diff,
  }
}

function readChains(value: unknown): RepairChain[] | undefined {
  if (value === undefined) return []
  if (!object(value)) return undefined
  const chains: RepairChain[] = []
  for (const [key, raw] of Object.entries(value)) {
    if (
      !text(key) ||
      !object(raw) ||
      !repairVersion(raw.version) ||
      raw.root_artifact_id !== key ||
      !text(raw.current_artifact_id) ||
      raw.max_rounds !== 2 ||
      !Array.isArray(raw.rounds) ||
      raw.rounds.length > 2
    )
      return undefined
    const rounds: RepairChain['rounds'] = []
    let expectedParent = key
    for (const [index, item] of raw.rounds.entries()) {
      if (
        !object(item) ||
        item.round !== index + 1 ||
        !round(item.round) ||
        item.parent_artifact_id !== expectedParent ||
        (item.child_artifact_id !== undefined && !text(item.child_artifact_id))
      )
        return undefined
      rounds.push({
        round: item.round,
        parent_artifact_id: item.parent_artifact_id,
        child_artifact_id: item.child_artifact_id,
      })
      if (item.child_artifact_id) expectedParent = item.child_artifact_id
      else if (index !== raw.rounds.length - 1) return undefined
    }
    if (raw.current_artifact_id !== expectedParent) return undefined
    chains.push({
      version: raw.version,
      root_artifact_id: key,
      current_artifact_id: raw.current_artifact_id,
      rounds,
    })
  }
  return chains
}

export function characterRepairAccess(
  artifact: Pick<Artifact, 'id' | 'status' | 'basis'>,
  branchId: string,
  tasks: Task[],
  loaded: boolean,
  artifactStatuses: Record<string, string> = {},
): CharacterRepairAccess {
  const unavailable = { readOnly: true, reason: '尚未核对当前批次修订链，请刷新后再操作。' }
  if (!loaded) return unavailable
  const basis = readCharacterRepairBasis(artifact.basis.character_field_repair)
  if (artifact.basis.character_field_repair !== undefined && !basis)
    return { readOnly: true, reason: '人物字段修订信息无法核对，保留原候选，请刷新后检查。' }
  const matches: { chain: RepairChain; task: Task }[] = []
  for (const task of tasks) {
    if (task.branch_id !== branchId || task.kind !== 'workflow' || task.status === 'continued') continue
    const chains = readChains(task.result.character_field_repairs)
    if (!chains) return unavailable
    for (const chain of chains) {
      if (
        chain.root_artifact_id === basis?.root_artifact_id ||
        [
          chain.root_artifact_id,
          chain.current_artifact_id,
          ...chain.rounds.flatMap((r) => [r.parent_artifact_id, r.child_artifact_id]),
        ].includes(artifact.id)
      )
        matches.push({ chain, task })
    }
  }
  if (matches.length > 1) return { readOnly: true, reason: '多个当前批次指向此修订链，请先核对归属。' }
  if (!matches.length) return basis ? unavailable : { readOnly: false, reason: '' }
  const { chain, task } = matches[0]
  const location = {
    rootId: chain.root_artifact_id,
    currentId: chain.current_artifact_id,
    parentId: basis?.parent_artifact_id,
  }
  const frozenVersion = task.result.character_field_repair_version ?? CHARACTER_FIELD_REPAIR_VERSION
  if (
    !repairVersion(frozenVersion) ||
    chain.version !== frozenVersion ||
    (basis && basis.version !== chain.version)
  )
    return { ...unavailable, ...location }
  if (
    basis &&
    !chain.rounds.some(
      (r) =>
        r.round === basis.round &&
        r.parent_artifact_id === basis.parent_artifact_id &&
        r.child_artifact_id === artifact.id,
    )
  )
    return { ...unavailable, ...location }
  const accepted =
    artifactStatuses[chain.current_artifact_id] === 'accepted' ||
    (artifact.id === chain.current_artifact_id && artifact.status === 'accepted') ||
    task.result.structure_history?.some((r) => r.artifact_id === chain.current_artifact_id)
  if (accepted)
    return {
      ...location,
      readOnly: true,
      reason: '当前修订候选已采用，整条候选链只读，原候选和每轮记录保留。',
    }
  if (
    chain.current_artifact_id !== artifact.id ||
    chain.rounds.some((r) => r.parent_artifact_id === artifact.id && r.child_artifact_id)
  )
    return {
      ...location,
      readOnly: true,
      reason: '历史父候选已产生修订子候选，仅供对照，不能再次采用或拒绝。',
    }
  return { ...location, readOnly: false, reason: '' }
}

export async function currentRepairTasks(
  artifact: Pick<Artifact, 'task_id'>,
  branchId: string,
  rows: Task[],
  getTask: (id: string) => Promise<Task>,
): Promise<Task[]> {
  const result = rows.filter((t) => t.branch_id === branchId)
  let id = artifact.task_id
  let previousId: string | undefined
  const seen = new Set<string>()
  while (id) {
    if (seen.has(id) || seen.size >= 50) throw new Error('人物修订批次链无法确认，请保留候选并核对。')
    seen.add(id)
    const task = result.find((r) => r.id === id) || (await getTask(id))
    if (task.id !== id || task.branch_id !== branchId) throw new Error('候选批次不属于当前工作版本。')
    if (previousId && (task.kind !== 'workflow' || task.result.continued_from !== previousId))
      throw new Error('续跑任务来源无法核对，请刷新检查。')
    if (!result.some((r) => r.id === task.id)) result.push(task)
    if (task.kind !== 'workflow') break
    if (!task.result.continued_to) {
      if (task.status === 'continued') throw new Error('续跑任务尚未定位，请刷新核对。')
      break
    }
    previousId = task.id
    id = task.result.continued_to
  }
  return result
}
