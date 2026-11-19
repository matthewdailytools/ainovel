import { workflowTransferRestrictions } from './workflowPrivacy'

type JSONValue = null | boolean | number | string | JSONValue[] | { [key: string]: JSONValue }
export type SyncPayload = { [key: string]: JSONValue }

export type SyncScope = {
  service: string
  workId: string
  deviceId: string
}

export type PushChange = {
  change_id: string
  object_id: string
  parent_revision: string | null
  payload: SyncPayload | null
  tombstone: boolean
}

export type SyncReceipt = PushChange & {
  sequence: number
  status: 'applied' | 'conflict' | 'resolved'
  device_id: string
  head_revision: string
  conflict_id: string | null
}

export const CHAPTER_DIRECTORY_PREFIX = 'chapter-directory:'
const CHAPTER_ORDER_PART = /^chapter-order-part-[a-f0-9]{64}$/
export const MAX_DIRECTORY_CHAPTERS = 5000

export type ChapterDirectoryRequest = {
  change_id: string
  moves: { chapter_id: string; position: number; next_position: number; head_revision: string }[]
}
type StagedChapterDirectory = {
  request: ChapterDirectoryRequest
  publications: { objectId: string; id: string }[]
  error?: string
}

export type Resolution =
  | { choice: 'current' | 'incoming'; parent_revision: string }
  | { choice: 'merge'; parent_revision: string; payload: SyncPayload | null; tombstone: boolean }
  | { choice: 'merge_prose'; parent_revision: string; content: string }
type ResolutionRequest = Resolution & { change_id: string }

export type SyncTransport = {
  push: (change: PushChange) => Promise<unknown>
  pull: (after: number) => Promise<unknown>
  receipt: (changeId: string) => Promise<unknown | null>
  resolve: (conflictId: string, request: ResolutionRequest) => Promise<unknown>
  applyChapterDirectory?: (request: ChapterDirectoryRequest) => Promise<unknown>
  verifyLedger?: () => Promise<void>
}

type Pending =
  | { kind: 'push'; request: PushChange }
  | { kind: 'resolve'; object_id: string; conflict_id: string; request: ResolutionRequest }

export function pendingPayloads(pending: readonly Pending[]): [string, SyncPayload | null][] {
  return pending.map((entry) =>
    entry.kind === 'push'
      ? [entry.request.object_id, entry.request.payload]
      : [entry.object_id, entry.request.choice === 'merge' ? entry.request.payload : null],
  )
}

export class SyncResolutionChanged extends Error {}
export class SyncChapterDirectoryRejected extends Error {}
export class SyncRestoreStateRejected extends Error {
  constructor(
    readonly code: 'sync_restore_state_dependency' | 'sync_restore_state_integrity' | 'sync_object_size',
    message: string,
  ) {
    super(message)
  }
}
type Replica = {
  key: string
  enabled: boolean
  cursor: number
  serial: number
  directory?: StagedChapterDirectory
}
type LedgerBinding = { workId: string; service: string; ledgerId: string }
type LedgerCheckpoint = { ledger_id: string; cursor: number }
type Queued = { key: string; id: string; position: number; pending: Pending }
type Log = { key: string; id: string; receipt: SyncReceipt; pending?: Pending }
export type SyncBaseline = { revision: string; signature: string; sequence?: number }
type Approval = { changeId: string; signature: string; publicationId?: string | null }
type Head = {
  key: string
  id: string
  revision: string
  sequence: number
  firstRevision?: { id: string; sequence: number }
  lastPublication?: { id: string; sequence: number }
  baseline?: SyncBaseline
  approval?: Approval
  directoryParts?: string[]
}
type Conflict = { key: string; id: string; objectId: string; opened?: boolean; resolvedBy?: string }

const DATABASE = 'ainovel-sync-client'
const SYNC_LOG_STORES = ['outbox', 'receipts', 'heads', 'conflicts']
const STORES = ['replicas', 'ledgers', ...SYNC_LOG_STORES]
const BODY_LIMIT = 1024 * 1024

export class SyncClientError extends Error {
  constructor(
    readonly code: 'disabled' | 'invalid' | 'storage' | 'conflict' | 'ledger',
    message: string,
  ) {
    super(message)
  }
}

export class SyncLedgerChanged extends SyncClientError {
  constructor() {
    super(
      'ledger',
      '云端账本与此作品的同步历史不一致。本机稿和待同步记录已保留，请用独立副本连接恢复后的服务。',
    )
  }
}

function invalid(message = '同步载荷或回执格式无效。'): never {
  throw new SyncClientError('invalid', message)
}

function identifier(value: unknown): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9._:-]{1,128}$/.test(value)) invalid()
  return value
}

function scopeKey(scope: SyncScope): string {
  if (!scope.service.trim() || scope.service.length > 256) invalid('同步服务标识无效。')
  return JSON.stringify([scope.service, identifier(scope.workId), identifier(scope.deviceId)])
}

function plain(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || ![Object.prototype, null].includes(Object.getPrototypeOf(value)))
    invalid()
  return value as Record<string, unknown>
}

function canonicalValue(value: unknown, depth: number): JSONValue {
  if (depth > 40) invalid('同步 JSON 嵌套过深。')
  if (typeof value === 'string' && /\p{Surrogate}/u.test(value)) invalid('同步文本包含无效字符。')
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (Array.isArray(value)) return Array.from(value, (item) => canonicalValue(item, depth + 1))
  return Object.fromEntries(
    Object.entries(plain(value))
      .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
      .map(([key, item]) => [canonicalValue(key, depth + 1) as string, canonicalValue(item, depth + 1)]),
  )
}

function body(value: unknown, tombstone: boolean, limit = BODY_LIMIT): SyncPayload | null {
  if (tombstone) {
    if (value !== null) invalid('删除变更的正文须为空。')
    return null
  }
  const payload = canonicalValue(plain(value), 0) as SyncPayload
  if (new TextEncoder().encode(JSON.stringify(payload)).byteLength > limit)
    invalid(limit === BODY_LIMIT ? '同步对象超过 1 MiB。' : '目录批次清单超过体积限制。')
  return payload
}

function readReceipt(value: unknown): SyncReceipt {
  const item = plain(value)
  const status = item.status
  if (status !== 'applied' && status !== 'conflict' && status !== 'resolved') invalid()
  const sequence = item.sequence
  if (typeof sequence !== 'number' || !Number.isSafeInteger(sequence) || sequence < 1) invalid()
  const tombstone = item.tombstone
  if (typeof tombstone !== 'boolean') invalid()
  const id = identifier(item.object_id)
  const directory = id.startsWith(CHAPTER_DIRECTORY_PREFIX)
  const receipt = {
    sequence,
    status,
    change_id: identifier(item.change_id),
    object_id: id,
    device_id: identifier(item.device_id),
    head_revision: identifier(item.head_revision),
    parent_revision: item.parent_revision === null ? null : identifier(item.parent_revision),
    conflict_id: item.conflict_id === null ? null : identifier(item.conflict_id),
    payload: body(item.payload, tombstone, directory ? BODY_LIMIT * 4 : BODY_LIMIT),
    tombstone,
  } satisfies SyncReceipt
  if (
    (status === 'conflict' &&
      (receipt.conflict_id !== receipt.change_id || receipt.head_revision === receipt.change_id)) ||
    (status !== 'conflict' && receipt.head_revision !== receipt.change_id) ||
    (status === 'resolved' && !receipt.conflict_id) ||
    (status === 'applied' && receipt.conflict_id !== null)
  )
    invalid()
  if (
    (directory &&
      (status !== 'applied' ||
        tombstone ||
        receipt.parent_revision !== null ||
        id !== CHAPTER_DIRECTORY_PREFIX + receipt.change_id)) ||
    (receipt.change_id.startsWith('chapter-order-part-') &&
      (!CHAPTER_ORDER_PART.test(receipt.change_id) ||
        !id.startsWith('chapter:') ||
        status !== 'applied' ||
        tombstone ||
        !receipt.parent_revision))
  )
    invalid('目录批次回执的编号或类型无效。')
  return receipt
}

function readPage(value: unknown, after: number) {
  const page = plain(value)
  if (!Array.isArray(page.changes) || page.changes.length > 100 || typeof page.has_more !== 'boolean')
    invalid()
  const changes = page.changes.map(readReceipt)
  if (
    changes.some((row, index) => row.sequence !== after + index + 1) ||
    page.cursor !== after + changes.length ||
    (changes.length === 0 && page.has_more)
  )
    invalid('同步记录不连续，已保留原游标。')
  return { changes, cursor: after + changes.length, has_more: page.has_more }
}

function readCheckpoint(value: unknown): LedgerCheckpoint {
  const { ledger_id, cursor } = plain(value)
  if (
    typeof ledger_id !== 'string' ||
    !/^[a-f0-9]{32}$/.test(ledger_id) ||
    typeof cursor !== 'number' ||
    !Number.isSafeInteger(cursor) ||
    cursor < 0
  )
    invalid('无法核对云端账本身份或游标。')
  return { ledger_id, cursor }
}

const wait = <T>(request: IDBRequest<T>) =>
  new Promise<T>((resolve, reject) => {
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })

function openDatabase(): Promise<IDBDatabase> {
  const request = indexedDB.open(DATABASE, 2)
  request.onupgradeneeded = ({ oldVersion }) => {
    if (oldVersion === 0) {
      request.result.createObjectStore('replicas', { keyPath: 'key' })
      for (const store of SYNC_LOG_STORES)
        request.result.createObjectStore(store, { keyPath: ['key', 'id'] }).createIndex('byReplica', 'key')
    }
    request.result.createObjectStore('ledgers', { keyPath: 'workId' })
  }
  return wait(request)
}

async function transact<T>(
  mode: IDBTransactionMode,
  operation: (tx: IDBTransaction) => Promise<T>,
): Promise<T> {
  let db: IDBDatabase | undefined
  try {
    db = await openDatabase()
    const tx = db.transaction(STORES, mode)
    const done = new Promise<DOMException | null>((resolve) => {
      tx.oncomplete = () => resolve(null)
      tx.onabort = () => resolve(tx.error ?? new DOMException('Transaction aborted', 'AbortError'))
    })
    let value: T
    try {
      value = await operation(tx)
    } catch (error) {
      try {
        tx.abort()
      } catch {
        // IndexedDB may have aborted the failed request already.
      }
      await done
      throw error
    }
    const failure = await done
    if (failure) throw failure
    return value
  } catch (error) {
    if (error instanceof SyncClientError) throw error
    throw new SyncClientError('storage', '同步记录未能写入浏览器存储，请检查空间后重试。')
  } finally {
    db?.close()
  }
}

async function replica(tx: IDBTransaction, key: string): Promise<Replica> {
  return (
    (await wait<Replica | undefined>(tx.objectStore('replicas').get(key))) ?? {
      key,
      enabled: false,
      cursor: 0,
      serial: 0,
    }
  )
}

export async function setSyncEnabled(scope: SyncScope, enabled: boolean): Promise<void> {
  const key = scopeKey(scope)
  await transact('readwrite', async (tx) => {
    await wait(tx.objectStore('replicas').put({ ...(await replica(tx, key)), enabled }))
  })
}

function ledgerBinding(tx: IDBTransaction, workId: string) {
  return wait<LedgerBinding | undefined>(tx.objectStore('ledgers').get(workId))
}

export async function enableSyncLedger(scope: SyncScope, value: unknown) {
  const key = scopeKey(scope)
  const checkpoint = readCheckpoint(value)
  await transact('readwrite', async (tx) => {
    const state = await replica(tx, key)
    const binding = await ledgerBinding(tx, scope.workId)
    if (binding) {
      if (
        binding.service !== scope.service ||
        binding.ledgerId !== checkpoint.ledger_id ||
        checkpoint.cursor < state.cursor
      )
        throw new SyncLedgerChanged()
    } else {
      const existing = await wait<Replica[]>(tx.objectStore('replicas').getAll())
      if (
        existing.some(
          (entry) =>
            JSON.parse(entry.key)[1] === scope.workId && (entry.enabled || entry.cursor || entry.serial),
        )
      )
        throw new SyncLedgerChanged()
      await wait(
        tx.objectStore('ledgers').add({
          workId: scope.workId,
          service: scope.service,
          ledgerId: checkpoint.ledger_id,
        } satisfies LedgerBinding),
      )
    }
    await wait(tx.objectStore('replicas').put({ ...state, enabled: true }))
  })
}

export async function requireSyncLedger(scope: SyncScope, value?: unknown) {
  const key = scopeKey(scope)
  const checkpoint = value === undefined ? undefined : readCheckpoint(value)
  return transact('readonly', async (tx) => {
    const binding = await ledgerBinding(tx, scope.workId)
    const state = await replica(tx, key)
    if (
      !binding ||
      binding.service !== scope.service ||
      (checkpoint && (binding.ledgerId !== checkpoint.ledger_id || checkpoint.cursor < state.cursor))
    )
      throw new SyncLedgerChanged()
    return binding.ledgerId
  })
}

async function append(scope: SyncScope, pending: Pending): Promise<string> {
  const key = scopeKey(scope)
  return transact('readwrite', async (tx) => {
    const state = await replica(tx, key)
    if (!state.enabled) throw new SyncClientError('disabled', '请先为当前作品启用同步。')
    const id = pending.request.change_id
    const queued = await wait<Queued | undefined>(tx.objectStore('outbox').get([key, id]))
    if (queued) {
      if (JSON.stringify(queued.pending) !== JSON.stringify(pending)) invalid('变更标识已用于其他输入。')
      return id
    }
    const recorded = await wait<Log | undefined>(tx.objectStore('receipts').get([key, id]))
    if (recorded) {
      if (JSON.stringify(recorded.pending) !== JSON.stringify(pending)) invalid('变更标识已用于其他输入。')
      return id
    }
    await wait(
      tx.objectStore('outbox').add({ key, id, position: state.serial + 1, pending } satisfies Queued),
    )
    await wait(tx.objectStore('replicas').put({ ...state, serial: state.serial + 1 }))
    return id
  })
}

export function queueSyncChange(
  scope: SyncScope,
  value: Omit<PushChange, 'change_id'>,
  changeId: string = crypto.randomUUID(),
): Promise<string> {
  if (typeof value.tombstone !== 'boolean') invalid()
  const request = {
    change_id: identifier(changeId),
    object_id: identifier(value.object_id),
    parent_revision: value.parent_revision === null ? null : identifier(value.parent_revision),
    payload: body(value.payload, value.tombstone),
    tombstone: value.tombstone,
  }
  return append(scope, { kind: 'push', request })
}

export function queueSyncResolution(
  scope: SyncScope,
  objectId: string,
  conflictId: string,
  value: Resolution,
  changeId: string = crypto.randomUUID(),
): Promise<string> {
  if (value.choice === 'merge' && typeof value.tombstone !== 'boolean') invalid()
  if (
    value.choice === 'merge_prose' &&
    (!objectId.startsWith('chapter:') ||
      typeof value.content !== 'string' ||
      value.content.length > 500_000 ||
      Array.from(value.content).length > 250_000 ||
      /\p{Surrogate}/u.test(value.content))
  )
    invalid('合并正文超出篇幅上限，或包含无效字符。')
  const request = {
    change_id: identifier(changeId),
    parent_revision: identifier(value.parent_revision),
    ...(value.choice === 'merge'
      ? { choice: value.choice, payload: body(value.payload, value.tombstone), tombstone: value.tombstone }
      : value.choice === 'merge_prose'
        ? { choice: value.choice, content: value.content }
        : { choice: value.choice }),
  }
  return append(scope, {
    kind: 'resolve',
    object_id: identifier(objectId),
    conflict_id: identifier(conflictId),
    request,
  })
}

async function record(tx: IDBTransaction, key: string, receipt: SyncReceipt, pending?: Pending) {
  const log = tx.objectStore('receipts')
  const previous = await wait<Log | undefined>(log.get([key, receipt.change_id]))
  if (previous && JSON.stringify(previous.receipt) !== JSON.stringify(receipt))
    invalid('同步回执内容发生改变。')
  if (!previous || (pending && !previous.pending)) {
    await wait(
      log.put({ key, id: receipt.change_id, receipt, ...(pending ? { pending } : {}) } satisfies Log),
    )
  }
  if (receipt.status !== 'conflict') {
    const store = tx.objectStore('heads')
    const head = await wait<Head | undefined>(store.get([key, receipt.object_id]))
    const firstRevision =
      (receipt.object_id.startsWith('project-version:') ||
        receipt.object_id.startsWith('settings-version:') ||
        receipt.object_id.startsWith('outline-version:') ||
        receipt.object_id.startsWith('story-version:') ||
        receipt.object_id.startsWith('setting-use-version:') ||
        receipt.object_id.startsWith('reference-card-version:') ||
        receipt.object_id.startsWith('workflow-inspection:') ||
        receipt.object_id.startsWith('workflow-completion:')) &&
      (!head?.firstRevision || head.firstRevision.sequence > receipt.sequence)
        ? { id: receipt.change_id, sequence: receipt.sequence }
        : head?.firstRevision
    const latest = !head || head.sequence < receipt.sequence
    const lastPublication =
      pending?.kind === 'push' && (!head?.lastPublication || head.lastPublication.sequence < receipt.sequence)
        ? { id: receipt.change_id, sequence: receipt.sequence }
        : head?.lastPublication
    const directoryParts =
      !previous && CHAPTER_ORDER_PART.test(receipt.change_id)
        ? [...(head?.directoryParts ?? []), receipt.change_id]
        : head?.directoryParts
    if (
      latest ||
      firstRevision !== head?.firstRevision ||
      lastPublication !== head?.lastPublication ||
      directoryParts !== head?.directoryParts
    ) {
      await wait(
        store.put({
          ...head,
          key,
          id: receipt.object_id,
          revision: latest ? receipt.change_id : head.revision,
          sequence: latest ? receipt.sequence : head.sequence,
          ...(firstRevision ? { firstRevision } : {}),
          ...(lastPublication ? { lastPublication } : {}),
          ...(directoryParts ? { directoryParts } : {}),
        } satisfies Head),
      )
    }
  }
  if (receipt.conflict_id) {
    const store = tx.objectStore('conflicts')
    const id = receipt.conflict_id
    const conflict = (await wait<Conflict | undefined>(store.get([key, id]))) ?? {
      key,
      id,
      objectId: receipt.object_id,
    }
    if (receipt.status === 'resolved') conflict.resolvedBy = receipt.change_id
    else conflict.opened = true
    await wait(store.put(conflict))
  }
}

async function readSyncStateTx(tx: IDBTransaction, key: string, scope: SyncScope) {
  const [state, pending, conflicts, ledger] = await Promise.all([
    replica(tx, key),
    wait<Queued[]>(tx.objectStore('outbox').index('byReplica').getAll(key)),
    wait<Conflict[]>(tx.objectStore('conflicts').index('byReplica').getAll(key)),
    ledgerBinding(tx, scope.workId),
  ])
  const unresolved = conflicts.filter((entry) => entry.opened && !entry.resolvedBy)
  return {
    enabled: state.enabled,
    cursor: state.cursor,
    ledgerId: ledger?.service === scope.service ? ledger.ledgerId : null,
    pending: pending.sort((left, right) => left.position - right.position).map((entry) => entry.pending),
    conflicts: unresolved.map((entry) => entry.id),
    blockedObjects: [...new Set(unresolved.map((entry) => entry.objectId))],
    ...(state.directory ? { directory: state.directory } : {}),
  }
}

export function readSyncState(scope: SyncScope) {
  const key = scopeKey(scope)
  return transact('readonly', (tx) => readSyncStateTx(tx, key, scope))
}

export function stageChapterDirectory(scope: SyncScope, directory: StagedChapterDirectory) {
  const key = scopeKey(scope)
  return transact('readwrite', async (tx) => {
    const state = await replica(tx, key)
    if (!state.enabled) throw new SyncClientError('disabled', '请先为当前作品启用同步。')
    if (state.directory) return state.directory
    await wait(tx.objectStore('replicas').put({ ...state, directory }))
    return directory
  })
}

export function recordChapterDirectoryRejection(scope: SyncScope, changeId: string, error: string | null) {
  const key = scopeKey(scope)
  return transact('readwrite', async (tx) => {
    const state = await replica(tx, key)
    if (state.directory?.request.change_id !== changeId) return
    if (error === null && !state.directory.error) return
    await wait(
      tx.objectStore('replicas').put({
        ...state,
        directory: error === null ? undefined : { ...state.directory, error },
      } satisfies Replica),
    )
  })
}

export function readSyncProgress(scope: SyncScope) {
  const key = scopeKey(scope)
  return transact('readonly', async (tx) => {
    const [state, heads] = await Promise.all([
      readSyncStateTx(tx, key, scope),
      wait<Head[]>(tx.objectStore('heads').index('byReplica').getAll(key)),
    ])
    const blocked = new Set(state.blockedObjects)
    const receivedUnapplied = heads.filter(
      (head) =>
        head.revision !== head.baseline?.revision &&
        head.revision !== head.lastPublication?.id &&
        !blocked.has(head.id),
    ).length
    return { ...state, receivedUnapplied }
  })
}

async function receiptOf(tx: IDBTransaction, key: string, id?: string | null) {
  return id
    ? ((await wait<Log | undefined>(tx.objectStore('receipts').get([key, id])))?.receipt ?? null)
    : null
}

export function readChapterBeforeArchive(scope: SyncScope, archived: SyncReceipt) {
  const key = scopeKey(scope)
  return transact('readonly', async (tx) => {
    let retired = archived
    while (retired.parent_revision) {
      const previous = await receiptOf(tx, key, retired.parent_revision)
      if (
        !previous ||
        previous.object_id !== retired.object_id ||
        previous.sequence >= retired.sequence ||
        previous.status === 'conflict'
      )
        invalid('待归档章节缺少可核对的云端父版本。')
      if (!previous.tombstone) return previous
      retired = previous
    }
    return null
  })
}

export async function readSyncHead(scope: SyncScope, objectId: string) {
  const key = scopeKey(scope)
  return transact('readonly', async (tx) =>
    receiptOf(
      tx,
      key,
      (await wait<Head | undefined>(tx.objectStore('heads').get([key, objectId])))?.revision,
    ),
  )
}

export async function listSyncHeads(scope: SyncScope) {
  const key = scopeKey(scope)
  return transact('readonly', async (tx) => {
    const heads = await wait<Head[]>(tx.objectStore('heads').index('byReplica').getAll(key))
    return Promise.all(
      heads.map(async (head) => ({
        ...head,
        receipt: await receiptOf(tx, key, head.revision),
        source: head.firstRevision ? await receiptOf(tx, key, head.firstRevision.id) : undefined,
      })),
    )
  })
}

function directoryMoves(scope: SyncScope, manifest: SyncReceipt) {
  const value = manifest.payload
  if (
    manifest.object_id !== CHAPTER_DIRECTORY_PREFIX + manifest.change_id ||
    value?.format !== 'ainovel-manuscript-sync/1' ||
    value.kind !== 'chapter-directory' ||
    value.branch_id !== scope.workId ||
    Object.keys(value).length !== 4 ||
    !Array.isArray(value.moves) ||
    value.moves.length < 2 ||
    value.moves.length > MAX_DIRECTORY_CHAPTERS
  )
    invalid('目录批次清单与工作版本不符。')
  const chapters = new Set<string>()
  const previousPositions = new Set<number>()
  const positions = new Set<number>()
  return value.moves.map((entry) => {
    const move = plain(entry)
    const { chapter_id, position, next_position, head_revision, receipt_id } = move
    if (
      Object.keys(move).length !== 5 ||
      typeof position !== 'number' ||
      typeof next_position !== 'number' ||
      !Number.isSafeInteger(position) ||
      !Number.isSafeInteger(next_position) ||
      position < 1 ||
      next_position < 1 ||
      position === next_position ||
      previousPositions.has(position) ||
      positions.has(next_position)
    )
      invalid('目录批次的章位分配无效。')
    const chapterId = identifier(chapter_id)
    if (chapters.has(chapterId)) invalid('同一章节在目录批次中出现多次。')
    chapters.add(chapterId)
    previousPositions.add(position)
    positions.add(next_position)
    return {
      chapterId,
      from: position,
      to: next_position,
      parentId: identifier(head_revision),
      childId: identifier(receipt_id),
    }
  })
}

export function verifyChapterDirectoryReceipt(
  scope: SyncScope,
  value: unknown,
  request: ChapterDirectoryRequest,
) {
  const receipt = readReceipt(value)
  const moves = directoryMoves(scope, receipt)
  if (
    receipt.change_id !== request.change_id ||
    receipt.device_id !== scope.deviceId ||
    moves.length !== request.moves.length ||
    moves.some(({ chapterId, from, to, parentId }, index) => {
      const move = request.moves[index]
      return (
        chapterId !== move.chapter_id ||
        from !== move.position ||
        to !== move.next_position ||
        parentId !== move.head_revision
      )
    })
  )
    invalid('目录批次回执与本机冻结提交不符。')
  return receipt
}

export function readChapterDirectory(scope: SyncScope, manifest: SyncReceipt) {
  const moves = directoryMoves(scope, manifest)
  const key = scopeKey(scope)
  return transact('readonly', async (tx) =>
    Promise.all(
      moves.map(async ({ chapterId, from, to, parentId, childId }, index) => {
        const id = `chapter:${chapterId}`
        const [parent, child] = await Promise.all([receiptOf(tx, key, parentId), receiptOf(tx, key, childId)])
        if (
          !parent ||
          parent.status === 'conflict' ||
          parent.tombstone ||
          parent.object_id !== id ||
          !child ||
          !CHAPTER_ORDER_PART.test(childId) ||
          child.object_id !== id ||
          child.device_id !== manifest.device_id ||
          child.parent_revision !== parentId ||
          child.sequence !== manifest.sequence - moves.length + index ||
          parent.sequence >= manifest.sequence - moves.length
        )
          invalid('目录批次缺少准确且连续的章稿回执。')
        return { chapterId, from, to, parent, child }
      }),
    ),
  )
}

export function markChapterDirectory(
  scope: SyncScope,
  manifest: SyncReceipt,
  members: { objectId: string; receiptId: string; baseline?: SyncBaseline }[],
  own = false,
) {
  const key = scopeKey(scope)
  return transact('readwrite', async (tx) => {
    const store = tx.objectStore('heads')
    const directory = await wait<Head | undefined>(store.get([key, manifest.object_id]))
    if (!directory || directory.revision !== manifest.change_id) invalid('目录批次的同步状态已变化，请重试。')
    if (directory.baseline?.revision === manifest.change_id) return
    const chapters = await Promise.all(
      members.map(({ objectId }) => wait<Head | undefined>(store.get([key, objectId]))),
    )
    for (const [index, entry] of members.entries()) {
      const chapter = chapters[index]
      if (!chapter?.directoryParts?.includes(entry.receiptId)) invalid('目录批次成员尚未全部接收。')
      await wait(
        store.put({
          ...chapter,
          directoryParts: chapter.directoryParts.filter((id) => id !== entry.receiptId),
          ...(entry.baseline ? { baseline: entry.baseline, approval: undefined } : {}),
        } satisfies Head),
      )
    }
    await wait(
      store.put({
        ...directory,
        baseline: { revision: directory.revision, signature: 'null', sequence: directory.sequence },
      } satisfies Head),
    )
    if (own) {
      const state = await replica(tx, key)
      if (state.directory?.request.change_id === manifest.change_id) {
        await wait(tx.objectStore('replicas').put({ ...state, directory: undefined } satisfies Replica))
      }
    }
  })
}

export function readPublicationReceipts(scope: SyncScope, changeIds: string[]) {
  const key = scopeKey(scope)
  return transact('readonly', async (tx) => {
    const store = tx.objectStore('receipts')
    const entries = await Promise.all(changeIds.map((id) => wait<Log | undefined>(store.get([key, id]))))
    return entries.flatMap((log) => (log?.pending?.kind === 'push' ? [log.receipt] : []))
  })
}

export async function markSyncBaseline(scope: SyncScope, objectId: string, baseline: SyncBaseline) {
  const key = scopeKey(scope)
  await transact('readwrite', async (tx) => {
    const store = tx.objectStore('heads')
    const head = await wait<Head | undefined>(store.get([key, objectId]))
    if (head?.revision === baseline.revision) {
      await wait(store.put({ ...head, baseline, approval: undefined } satisfies Head))
    }
  })
}

export async function approveSyncResolution(scope: SyncScope, objectId: string, approval: Approval) {
  const key = scopeKey(scope)
  await transact('readwrite', async (tx) => {
    const store = tx.objectStore('heads')
    const head = await wait<Head | undefined>(store.get([key, objectId]))
    if (!head) invalid('云端对象已改变，请重新比较。')
    await wait(store.put({ ...head, approval } satisfies Head))
  })
}

export async function readSyncConflict(scope: SyncScope, conflictId: string) {
  const key = scopeKey(scope)
  return transact('readonly', async (tx) => {
    const conflict = await wait<Conflict | undefined>(tx.objectStore('conflicts').get([key, conflictId]))
    const incoming = await receiptOf(tx, key, conflictId)
    if (!conflict?.opened || !incoming) return null
    const head = await wait<Head | undefined>(tx.objectStore('heads').get([key, incoming.object_id]))
    return {
      incoming,
      base: await receiptOf(tx, key, incoming.parent_revision),
      competing: await receiptOf(tx, key, incoming.head_revision),
      current: await receiptOf(tx, key, head?.revision),
      resolvedBy: conflict.resolvedBy ?? null,
    }
  })
}

function checkReply(scope: SyncScope, pending: Pending, receipt: SyncReceipt) {
  const request = pending.request
  if (
    receipt.change_id !== request.change_id ||
    receipt.parent_revision !== request.parent_revision ||
    receipt.device_id !== scope.deviceId
  )
    invalid('回执不属于本设备提交的变更。')
  if (pending.kind === 'resolve') {
    if (
      receipt.status !== 'resolved' ||
      receipt.object_id !== pending.object_id ||
      receipt.conflict_id !== pending.conflict_id
    )
      invalid('回执不属于所选冲突处理。')
  } else if (
    receipt.status === 'resolved' ||
    receipt.object_id !== pending.request.object_id ||
    receipt.tombstone !== pending.request.tombstone ||
    JSON.stringify(receipt.payload) !== JSON.stringify(pending.request.payload)
  )
    invalid('回执与发出的变更不一致。')
}

export async function pushSyncPending(
  scope: SyncScope,
  transport: SyncTransport,
  limit = 50,
  privateInspections?: ReadonlySet<string>,
  onRestoreRejected?: (
    change: Pending,
    error: SyncRestoreStateRejected,
  ) => Promise<'retain' | 'discard' | false>,
  directoryHolds?: { objects: ReadonlySet<string>; prepared: ReadonlySet<string> },
) {
  const key = scopeKey(scope)
  const directoryBlocked = await transact('readonly', async (tx) => {
    const [state, heads] = await Promise.all([
      replica(tx, key),
      wait<Head[]>(tx.objectStore('heads').index('byReplica').getAll(key)),
    ])
    return new Set([
      ...heads.filter((head) => head.directoryParts?.length).map(({ id }) => id),
      ...(state.directory?.request.moves.map(({ chapter_id }) => `chapter:${chapter_id}`) ?? []),
    ])
  })
  const held = new Set<string>()
  let count = 0
  while (count < limit) {
    const state = await readSyncState(scope)
    const { deferred } = workflowTransferRestrictions(pendingPayloads(state.pending), privateInspections)
    const pending = state.pending.find((entry) => {
      const id = entry.kind === 'push' ? entry.request.object_id : entry.object_id
      const revision = entry.kind === 'push' ? entry.request.payload?.revision : undefined
      const sourceChapter =
        id.startsWith('revision:') &&
        revision &&
        typeof revision === 'object' &&
        !Array.isArray(revision) &&
        typeof revision.chapter_id === 'string'
          ? `chapter:${revision.chapter_id}`
          : id
      return (
        !held.has(entry.request.change_id) &&
        !deferred.has(id) &&
        !directoryBlocked.has(sourceChapter) &&
        (!directoryHolds?.objects.has(sourceChapter) ||
          directoryHolds.prepared.has(entry.request.change_id)) &&
        (entry.kind === 'resolve' || !state.blockedObjects.includes(id))
      )
    })
    if (!state.enabled || !pending) break
    let response: unknown
    try {
      response =
        pending.kind === 'push'
          ? await transport.push(pending.request)
          : await transport.resolve(pending.conflict_id, pending.request)
    } catch (error) {
      if (error instanceof SyncLedgerChanged) throw error
      // The request may have committed. Recover its original receipt before any caller retry.
      let receiptRead = false
      try {
        response = await transport.receipt(pending.request.change_id)
        receiptRead = true
      } catch (reason) {
        if (reason instanceof SyncLedgerChanged) throw reason
        response = null
      }
      if (response === null) {
        if (receiptRead && error instanceof SyncRestoreStateRejected && onRestoreRejected) {
          const outcome = await onRestoreRejected(pending, error)
          if (outcome) {
            if (outcome === 'discard' && pending.kind === 'push') {
              await transact('readwrite', async (tx) => {
                await wait(tx.objectStore('outbox').delete([key, pending.request.change_id]))
              })
            }
            held.add(pending.request.change_id)
            continue
          }
        }
        if (pending.kind === 'resolve' && error instanceof SyncResolutionChanged) {
          await transact('readwrite', async (tx) => {
            await wait(tx.objectStore('outbox').delete([key, pending.request.change_id]))
          })
        }
        throw error
      }
    }
    const receipt = readReceipt(response)
    checkReply(scope, pending, receipt)
    await transact('readwrite', async (tx) => {
      await record(tx, key, receipt, pending)
      await wait(tx.objectStore('outbox').delete([key, receipt.change_id]))
    })
    count++
  }
  return count
}

export async function pullSyncPage(scope: SyncScope, transport: SyncTransport) {
  const key = scopeKey(scope)
  const state = await transact('readonly', (tx) => replica(tx, key))
  if (!state.enabled) return { count: 0, hasMore: false }
  const page = readPage(await transport.pull(state.cursor), state.cursor)
  return transact('readwrite', async (tx) => {
    const latest = await replica(tx, key)
    if (!latest.enabled) return { count: 0, hasMore: false }
    if (latest.cursor !== state.cursor) return { count: 0, hasMore: true }
    for (const receipt of page.changes) {
      const pending =
        receipt.device_id === scope.deviceId
          ? await wait<Queued | undefined>(tx.objectStore('outbox').get([key, receipt.change_id]))
          : undefined
      if (pending) {
        checkReply(scope, pending.pending, receipt)
        await wait(tx.objectStore('outbox').delete([key, receipt.change_id]))
      }
      await record(tx, key, receipt, pending?.pending)
    }
    await wait(tx.objectStore('replicas').put({ ...latest, cursor: page.cursor }))
    return { count: page.changes.length, hasMore: page.has_more }
  })
}
