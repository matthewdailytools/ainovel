import { ApiError, type Task } from './api'
import * as legacy from './preparedScopeCorrection'
import { canonical, object, ownerKey, type Owner } from './sourceCorrection'

export const SCOPE_OBJECT_VERSION = 'prepared-source-scope-correction/2'
export const SCOPE_OBJECT_BODY_VERSION = 'scope-corrected-candidate-recheck/2'
export const OBJECT_ALIAS_VERSION = 'prepared-source-object-alias/1'
export const SCOPE_KIND = legacy.SCOPE_KIND
export const scopeEligibility = legacy.scopeEligibility
type Evidence = { source_id: string; text: string; start: number; end: number }
export type ObjectAliasResolution = {
  version: typeof OBJECT_ALIAS_VERSION
  state: 'not_needed' | 'supported' | 'unverified'
  input_sha256: string
  required_item_ids: string[]
  items: {
    item_id: string
    status: 'supported' | 'unknown' | 'different'
    observation_id: string
    checks: {
      observation_id: string
      relation: 'same_transfer_same_instance' | 'different_transfer_or_instance' | 'unknown'
      evidence_source: Evidence | null
      reason: string
    }[]
    reason: string
  }[]
  note: string
}
type ObjectPreview = Omit<legacy.ScopePreview, 'version'> & {
  version: typeof SCOPE_OBJECT_VERSION
  verify_object_aliases: true
}
type ObjectReceipt = Omit<legacy.ScopeReceipt, 'version' | 'snapshot' | 'preservation'> & {
  version: typeof SCOPE_OBJECT_VERSION
  snapshot: Omit<legacy.ScopeReceipt['snapshot'], 'version'> & {
    version: typeof SCOPE_OBJECT_VERSION
    verify_object_aliases: true
  }
  object_alias_resolution: ObjectAliasResolution
  preservation:
    | null
    | (Omit<NonNullable<legacy.ScopeReceipt['preservation']>, 'item_checks'> & {
        item_checks: (NonNullable<legacy.ScopeReceipt['preservation']>['item_checks'][number] & {
          object_alias_check: 'pass' | 'fail' | 'unknown' | 'not_needed'
        })[]
      })
}
type ObjectBodyPreview = Omit<legacy.ScopeBodyPreview, 'version'> & {
  version: typeof SCOPE_OBJECT_BODY_VERSION
  verify_object_aliases: true
}
type LegacyReport = NonNullable<ReturnType<typeof legacy.readScopeReport>>
export type ScopePreview = legacy.ScopePreview | ObjectPreview
export type ScopeReceipt = legacy.ScopeReceipt | ObjectReceipt
export type ScopeBodyPreview = legacy.ScopeBodyPreview | ObjectBodyPreview
export type ScopeReport =
  | LegacyReport
  | (Omit<LegacyReport, 'version'> & {
      version: typeof SCOPE_OBJECT_BODY_VERSION
    })
type ObjectRequest = legacy.ScopeCapsule['request'] & { verify_object_aliases?: true }
export type ScopeCapsule = Omit<legacy.ScopeCapsule, 'preview' | 'request'> & {
  preview: ScopePreview | ScopeBodyPreview
  request: ObjectRequest
}
export type ScopeOperationState = {
  status: 'idle' | 'sending' | 'uncertain' | 'rejected' | 'confirmed' | 'unavailable'
  pending?: ScopeCapsule
  task?: Task
  message: string
}

const hash = (v: unknown): v is string => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v)
const text = (v: unknown, max = 2000): v is string => typeof v === 'string' && [...v].length <= max
const nonempty = (v: unknown, max = 2000): v is string => text(v, max) && !!v.trim()
const same = (a: unknown, b: unknown) => canonical(a) === canonical(b)
const stringIds = (v: unknown): v is string[] =>
  Array.isArray(v) && v.every((id) => nonempty(id, 100)) && new Set(v).size === v.length
export const objectAliasesEnabled = (value: unknown) => object(value) && value.verify_object_aliases === true
export const isScopeBodyVersion = (value: unknown) =>
  value === legacy.SCOPE_BODY_VERSION || value === SCOPE_OBJECT_BODY_VERSION
const legacyVersion = (value: unknown) =>
  value === legacy.SCOPE_VERSION || value === legacy.SCOPE_BODY_VERSION
export const scopePreviewPath = (artifactId: string, verifyObjectAliases: boolean) =>
  `/artifacts/${encodeURIComponent(artifactId)}/prepared-source-scope-preview${verifyObjectAliases ? '?' + new URLSearchParams({ verify_object_aliases: 'true' }) : ''}`

export function readScopePreview(
  value: unknown,
  owner: Owner,
  verifyObjectAliases?: boolean,
): ScopePreview | undefined {
  if (!object(value)) return
  if (value.version === legacy.SCOPE_VERSION) {
    if (verifyObjectAliases === true || 'verify_object_aliases' in value) return
    return legacy.readScopePreview(value, owner)
  }
  if (
    value.version !== SCOPE_OBJECT_VERSION ||
    value.verify_object_aliases !== true ||
    verifyObjectAliases === false
  )
    return
  const parsed = legacy.readScopePreview({ ...value, version: legacy.SCOPE_VERSION }, owner)
  return parsed ? { ...parsed, version: SCOPE_OBJECT_VERSION, verify_object_aliases: true } : undefined
}

function legacyReceiptValue(value: Record<string, unknown>, parsedReceipt = false) {
  const snapshot = object(value.snapshot) ? value.snapshot : {}
  const wireContract = (contract: unknown) => {
    if (!parsedReceipt || !object(contract) || !Array.isArray(contract.items)) return contract
    return {
      ...contract,
      items: contract.items.map((item) =>
        object(item) ? Object.fromEntries(Object.entries(item).filter(([key]) => key !== 'id')) : item,
      ),
    }
  }
  const preservation =
    object(value.preservation) && Array.isArray(value.preservation.item_checks)
      ? {
          ...value.preservation,
          item_checks: value.preservation.item_checks.map((row) => {
            if (!object(row)) return row
            return Object.fromEntries(Object.entries(row).filter(([key]) => key !== 'object_alias_check'))
          }),
        }
      : value.preservation
  return {
    ...value,
    version: legacy.SCOPE_VERSION,
    snapshot: {
      ...snapshot,
      version: legacy.SCOPE_VERSION,
      prepared_contract: wireContract(snapshot.prepared_contract),
    },
    corrected_contract: wireContract(value.corrected_contract),
    preservation,
  }
}

function readObjectAliases(value: unknown, source: legacy.ScopeReceipt): ObjectAliasResolution | undefined {
  if (
    !object(value) ||
    value.version !== OBJECT_ALIAS_VERSION ||
    !['not_needed', 'supported', 'unverified'].includes(String(value.state)) ||
    !hash(value.input_sha256) ||
    !stringIds(value.required_item_ids) ||
    !Array.isArray(value.items) ||
    value.items.length !== value.required_item_ids.length ||
    !text(value.note, 2000)
  )
    return
  const required = value.required_item_ids
  if (required.some((id) => !source.snapshot.item_ids.includes(id))) return
  const observations = source.inventory.inventory.observations
  const units = source.inventory.sources
  const original = source.snapshot.prepared_contract.items
  const usable = ['recipient_exclusivity', 'named_destination', 'explicitly_unrestricted']
  const comparableFor = (before: (typeof original)[number]) =>
    ['attempted', 'completed', 'planned'].includes(before.transfer_scope) &&
    !!before.supplier &&
    !!before.recipient &&
    !!before.transfer_object &&
    before.supplier !== before.recipient
      ? observations.filter(
          (row) =>
            !!row.transfer_object &&
            row.supplier === before.supplier &&
            row.recipient === before.recipient &&
            row.transfer_scope === before.transfer_scope,
        )
      : []
  const expectedRequired =
    source.inventory.status === 'supported'
      ? original
          .filter((before) => {
            const rows = comparableFor(before)
            return (
              source.snapshot.item_ids.includes(before.id) &&
              rows.length > 0 &&
              rows.some((row) => usable.includes(row.kind)) &&
              (rows.length !== 1 || rows[0].transfer_object !== before.transfer_object)
            )
          })
          .map((row) => row.id)
      : []
  if (!same(required, expectedRequired)) return
  for (let index = 0; index < value.items.length; index++) {
    const item = value.items[index]
    const before = original.find((row) => row.id === required[index])
    if (
      !object(item) ||
      item.item_id !== required[index] ||
      !before ||
      !['supported', 'unknown', 'different'].includes(String(item.status)) ||
      !text(item.observation_id, 100) ||
      !Array.isArray(item.checks) ||
      !nonempty(item.reason, 1200)
    )
      return
    const comparable = comparableFor(before)
    if (item.checks.length !== comparable.length) return
    for (let checkIndex = 0; checkIndex < item.checks.length; checkIndex++) {
      const check = item.checks[checkIndex]
      if (
        !object(check) ||
        check.observation_id !== comparable[checkIndex].observation_id ||
        !['same_transfer_same_instance', 'different_transfer_or_instance', 'unknown'].includes(
          String(check.relation),
        ) ||
        !nonempty(check.reason, 1200)
      )
        return
      if (check.evidence_source !== null) {
        const evidence = check.evidence_source
        if (
          !object(evidence) ||
          !units.some((unit) => same(unit, evidence)) ||
          ![comparable[checkIndex].evidence, comparable[checkIndex].restriction_evidence].includes(
            String(evidence.source_id),
          )
        )
          return
      }
      if (check.relation !== 'unknown' && check.evidence_source === null) return
    }
    const matching = item.checks.filter(
      (row) => object(row) && row.relation === 'same_transfer_same_instance',
    )
    const uniquelySupported =
      matching.length === 1 &&
      object(matching[0]) &&
      comparable.some(
        (row) => row.observation_id === matching[0].observation_id && usable.includes(row.kind),
      ) &&
      item.checks.every(
        (row) => object(row) && (row === matching[0] || row.relation === 'different_transfer_or_instance'),
      )
    const complete = item.checks.every((row) => object(row) && row.relation !== 'unknown')
    const expectedStatus = uniquelySupported
      ? 'supported'
      : complete && matching.length === 0
        ? 'different'
        : 'unknown'
    if (item.status !== expectedStatus) return
    if (item.status === 'supported') {
      if (!object(matching[0]) || item.observation_id !== matching[0].observation_id) return
    } else if (item.observation_id !== '') return
  }
  const supported = value.items.every((item) => object(item) && item.status === 'supported')
  const expected = required.length === 0 ? 'not_needed' : supported ? 'supported' : 'unverified'
  if (value.state !== expected) return
  // A mismatching observation used by a proposal must have an explicitly
  // grounded association for that same P item, not a global synonym table.
  for (const item of source.proposal.items) {
    const before = original.find((row) => row.id === item.item_id)
    if (!before) return
    const mismatched = item.observation_ids.some((id) => {
      const observation = observations.find((row) => row.observation_id === id)
      return observation && observation.transfer_object !== before.transfer_object
    })
    if (mismatched && !required.includes(item.item_id)) return
    if (required.includes(item.item_id) && item.status === 'supported') {
      const alias = value.items.find((row) => object(row) && row.item_id === item.item_id)
      if (
        !object(alias) ||
        alias.status !== 'supported' ||
        !item.observation_ids.includes(String(alias.observation_id))
      )
        return
    }
  }
  return value as ObjectAliasResolution
}

export function readScopeReceipt(value: unknown): ScopeReceipt | undefined {
  if (!object(value)) return
  if (value.version === legacy.SCOPE_VERSION) {
    if (
      'object_alias_resolution' in value ||
      (object(value.snapshot) && 'verify_object_aliases' in value.snapshot)
    )
      return
    return legacy.readScopeReceipt(value)
  }
  if (
    value.version !== SCOPE_OBJECT_VERSION ||
    !['ready', 'unverified'].includes(String(value.state)) ||
    !object(value.snapshot) ||
    value.snapshot.verify_object_aliases !== true
  )
    return
  const normalized = legacyReceiptValue(value)
  // Validate the unchanged old structural rules without treating their narrower
  // readiness formula as the new independent alias verdict.
  const parsed =
    legacy.readScopeReceipt({ ...normalized, state: 'ready' }) ||
    legacy.readScopeReceipt({ ...normalized, state: 'unverified' })
  if (!parsed) return
  const aliases = readObjectAliases(value.object_alias_resolution, parsed)
  if (!aliases || (aliases.state === 'unverified' && value.preservation !== null)) return
  if (value.preservation !== null) {
    if (!object(value.preservation) || !Array.isArray(value.preservation.item_checks)) return
    for (const row of value.preservation.item_checks) {
      if (!object(row) || !['pass', 'fail', 'unknown', 'not_needed'].includes(String(row.object_alias_check)))
        return
      if (aliases.required_item_ids.includes(String(row.item_id))) {
        if (
          row.object_alias_check === 'not_needed' ||
          (value.state === 'ready' && row.object_alias_check !== 'pass')
        )
          return
      } else if (row.object_alias_check !== 'not_needed') return
    }
  }
  const aliasChecksPassed =
    object(value.preservation) &&
    Array.isArray(value.preservation.item_checks) &&
    value.preservation.item_checks.every(
      (check) =>
        object(check) &&
        (aliases.required_item_ids.includes(String(check.item_id))
          ? check.object_alias_check === 'pass'
          : check.object_alias_check === 'not_needed'),
    )
  const ready =
    parsed.state === 'ready' && ['not_needed', 'supported'].includes(aliases.state) && aliasChecksPassed
  if ((value.state === 'ready') !== Boolean(ready)) return
  return {
    ...value,
    snapshot: { ...value.snapshot, prepared_contract: parsed.snapshot.prepared_contract },
    corrected_contract: parsed.corrected_contract,
  } as ObjectReceipt
}

export function readScopeBodyPreview(
  value: unknown,
  owner: Owner,
  receiptId: string,
  receipt: ScopeReceipt,
): ScopeBodyPreview | undefined {
  if (!object(value)) return
  if (value.version === legacy.SCOPE_BODY_VERSION) {
    if (receipt.version !== legacy.SCOPE_VERSION || 'verify_object_aliases' in value) return
    return legacy.readScopeBodyPreview(value, owner, receiptId, receipt)
  }
  if (
    value.version !== SCOPE_OBJECT_BODY_VERSION ||
    value.verify_object_aliases !== true ||
    receipt.version !== SCOPE_OBJECT_VERSION ||
    receipt.state !== 'ready'
  )
    return
  const original = legacy.readScopeReceipt(legacyReceiptValue(receipt, true))
  if (
    !original ||
    !legacy.readScopeBodyPreview({ ...value, version: legacy.SCOPE_BODY_VERSION }, owner, receiptId, original)
  )
    return
  return value as ObjectBodyPreview
}
export function readScopeReport(value: unknown): ScopeReport | undefined {
  if (!object(value)) return
  if (value.version === legacy.SCOPE_BODY_VERSION) return legacy.readScopeReport(value)
  if (value.version !== SCOPE_OBJECT_BODY_VERSION) return
  const metadata =
    object(value.plot_audit) && object(value.plot_audit.source_scope_correction)
      ? value.plot_audit.source_scope_correction
      : undefined
  if (
    !metadata ||
    metadata.version !== SCOPE_OBJECT_VERSION ||
    !object(metadata.object_alias_resolution) ||
    metadata.object_alias_resolution.version !== OBJECT_ALIAS_VERSION ||
    !['not_needed', 'supported'].includes(String(metadata.object_alias_resolution.state))
  )
    return
  const parsed = legacy.readScopeReport({ ...value, version: legacy.SCOPE_BODY_VERSION })
  return parsed ? (value as ScopeReport) : undefined
}

function legacyCapsuleValue(value: Record<string, unknown>): Record<string, unknown> | undefined {
  if (!object(value.preview) || !object(value.request)) return
  const source = value.phase === 'source'
  const expected = source ? SCOPE_OBJECT_VERSION : SCOPE_OBJECT_BODY_VERSION
  if (value.preview.version !== expected || value.preview.verify_object_aliases !== true) return
  if (source ? value.request.verify_object_aliases !== true : 'verify_object_aliases' in value.request) return
  const request = source
    ? Object.fromEntries(Object.entries(value.request).filter(([key]) => key !== 'verify_object_aliases'))
    : value.request
  return {
    ...value,
    preview: { ...value.preview, version: source ? legacy.SCOPE_VERSION : legacy.SCOPE_BODY_VERSION },
    request,
  }
}
export function readScopeCapsule(value: unknown, key: string): ScopeCapsule | undefined {
  if (!object(value) || !object(value.preview)) return
  if (legacyVersion(value.preview.version)) return legacy.readScopeCapsule(value, key)
  const normalized = legacyCapsuleValue(value)
  return normalized && legacy.readScopeCapsule(normalized, key) ? (value as ScopeCapsule) : undefined
}
export function readScopeTask(value: unknown, capsule: ScopeCapsule): Task | undefined {
  if (legacyVersion(capsule.preview.version))
    return legacy.readScopeTask(value, capsule as legacy.ScopeCapsule)
  if (!object(value) || !object(value.payload) || !object(value.result)) return
  const source = capsule.phase === 'source'
  const key = source ? 'prepared_source_scope_snapshot' : 'source_corrected_recheck_snapshot'
  const snapshot = value.result[key]
  if (
    !object(snapshot) ||
    snapshot.verify_object_aliases !== true ||
    snapshot.version !== capsule.preview.version
  )
    return
  if (source) {
    if (
      value.payload.verify_object_aliases !== true ||
      value.payload.prepared_source_scope_version !== SCOPE_OBJECT_VERSION ||
      value.result.prepared_source_scope_version !== SCOPE_OBJECT_VERSION
    )
      return
  } else if (value.result.source_corrected_recheck_version !== SCOPE_OBJECT_BODY_VERSION) return
  const normalizedCapsule = legacyCapsuleValue(capsule)
  if (!normalizedCapsule) return
  const normalized = {
    ...value,
    payload: { ...value.payload, ...(source ? { prepared_source_scope_version: legacy.SCOPE_VERSION } : {}) },
    result: {
      ...value.result,
      ...(source
        ? { prepared_source_scope_version: legacy.SCOPE_VERSION }
        : { source_corrected_recheck_version: legacy.SCOPE_BODY_VERSION }),
      [key]: { ...snapshot, version: source ? legacy.SCOPE_VERSION : legacy.SCOPE_BODY_VERSION },
    },
  }
  return legacy.readScopeTask(normalized, normalizedCapsule as legacy.ScopeCapsule)
    ? (value as Task)
    : undefined
}

export function createScopeOperation(
  key: string,
  call: (path: string, method?: string, body?: unknown) => Promise<unknown>,
  storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>,
  publish: (value: ScopeOperationState) => void,
) {
  const storageKey = 'ainovel.prepared-scope-correction.1:' + key
  let alive = true,
    flight = false,
    sequence = 0,
    state: ScopeOperationState = { status: 'idle', message: '' }
  const emit = (next: ScopeOperationState) => {
    state = next
    if (alive) publish(next)
  }
  try {
    const saved = storage.getItem(storageKey)
    if (saved) {
      const pending = readScopeCapsule(JSON.parse(saved), key)
      if (!pending) throw Error('invalid')
      state = { status: 'uncertain', pending, message: '已恢复原标识；仅查询，不重发模型请求。' }
    }
  } catch {
    state = { status: 'unavailable', message: '无法完整恢复稳定提交，原记录保留，不能新建标识。' }
  }
  async function send(pending: ScopeCapsule, lookup: boolean) {
    if (!alive || flight) return
    flight = true
    const token = ++sequence
    emit({ status: 'sending', pending, message: lookup ? '正在查询原回执…' : '正在提交独立任务…' })
    try {
      const target =
        pending.phase === 'source' ? 'prepared-source-scope-corrections' : 'recheck-source-correction'
      let raw = await call(
        `/artifacts/${encodeURIComponent(pending.owner.artifactId)}/${target}${lookup ? '?' + new URLSearchParams({ client_request_id: pending.request.client_request_id }) : ''}`,
        lookup ? 'GET' : 'POST',
        lookup ? undefined : pending.request,
      )
      if (lookup && pending.phase === 'source') {
        if (
          !object(raw) ||
          raw.source_candidate_id !== pending.owner.artifactId ||
          raw.client_request_id !== pending.request.client_request_id
        )
          throw Error('原回执目标不符。')
        if (raw.task === null) throw Error('此刻尚未查到原提交；保留原标识继续只读查询。')
        raw = raw.task
      }
      const task = readScopeTask(raw, pending)
      if (!task) throw Error('任务未对应冻结来源、请求或版本，原标识保留。')
      if (alive && token === sequence)
        emit({ status: 'confirmed', pending, task, message: '独立任务已确认；不采纳或恢复正文。' })
    } catch (error) {
      if (!alive || token !== sequence) return
      const rejected =
        !lookup &&
        error instanceof ApiError &&
        error.status >= 400 &&
        error.status < 500 &&
        error.code !== 'invalid_response'
      emit({
        status: rejected ? 'rejected' : 'uncertain',
        pending,
        message:
          error instanceof ApiError && lookup && error.status === 404
            ? '此刻尚未查到原提交，继续只查原标识。'
            : error instanceof Error
              ? error.message
              : '结果未知，原标识保留。',
      })
    } finally {
      flight = false
    }
  }
  return {
    get state() {
      return state
    },
    async start(pending: ScopeCapsule) {
      if (!alive || flight || state.status !== 'idle' || !readScopeCapsule(pending, key)) return
      try {
        storage.setItem(storageKey, JSON.stringify(pending))
      } catch {
        emit({ status: 'unavailable', message: '无法保存稳定标识，未派发。' })
        return
      }
      await send(pending, false)
    },
    async lookup() {
      if (state.pending && !['idle', 'unavailable'].includes(state.status)) await send(state.pending, true)
    },
    releaseKnown() {
      if (flight || !['idle', 'confirmed', 'rejected'].includes(state.status)) return false
      try {
        storage.removeItem(storageKey)
      } catch {
        emit({ ...state, message: '无法清理已知提交，原标识保留。' })
        return false
      }
      emit({ status: 'idle', message: '' })
      return true
    },
    dispose() {
      alive = false
      sequence++
    },
  }
}
