import { IDBFactory, IDBObjectStore } from 'fake-indexeddb'
import { beforeEach, expect, test, vi } from 'vitest'
import {
  pullSyncPage,
  pushSyncPending,
  queueSyncChange,
  queueSyncResolution,
  readSyncConflict,
  readSyncHead,
  readSyncState,
  setSyncEnabled,
  type PushChange,
  type SyncReceipt,
  type SyncScope,
  type SyncTransport,
} from './syncClient'

const first = { service: 'isolated', workId: 'work', deviceId: 'first' }
const second = { ...first, deviceId: 'second' }
const chapter = 'chapter:one'

beforeEach(() => vi.stubGlobal('indexedDB', new IDBFactory()))

function isolatedCloud() {
  const changes: SyncReceipt[] = []
  const heads = new Map<string, string>()
  const submissions: string[] = []

  function transport(scope: SyncScope): SyncTransport {
    return {
      async push(change) {
        submissions.push(change.change_id)
        const prior = changes.find((entry) => entry.change_id === change.change_id)
        if (prior) return prior
        const head = heads.get(change.object_id) ?? null
        const collision = head !== change.parent_revision
        const result = {
          ...change,
          device_id: scope.deviceId,
          sequence: changes.length + 1,
          status: collision ? 'conflict' : 'applied',
          head_revision: collision ? head! : change.change_id,
          conflict_id: collision ? change.change_id : null,
        } satisfies SyncReceipt
        changes.push(result)
        if (!collision) heads.set(change.object_id, change.change_id)
        return result
      },
      async pull(after) {
        const rows = changes.slice(after, after + 2)
        return { changes: rows, cursor: after + rows.length, has_more: after + rows.length < changes.length }
      },
      async receipt(id) {
        return changes.find((row) => row.device_id === scope.deviceId && row.change_id === id) ?? null
      },
      async resolve(conflictId, request) {
        const prior = changes.find((entry) => entry.change_id === request.change_id)
        if (prior) return prior
        const conflict = changes.find((entry) => entry.change_id === conflictId)!
        const current = changes.find((entry) => entry.change_id === request.parent_revision)!
        if (heads.get(conflict.object_id) !== current.change_id) throw new Error('head changed')
        const selected = request.choice === 'incoming' ? conflict : current
        const result = {
          change_id: request.change_id,
          object_id: conflict.object_id,
          parent_revision: request.parent_revision,
          payload: request.choice === 'merge' ? request.payload : selected.payload,
          tombstone: request.choice === 'merge' ? request.tombstone : selected.tombstone,
          status: 'resolved',
          conflict_id: conflictId,
          device_id: scope.deviceId,
          sequence: changes.length + 1,
          head_revision: request.change_id,
        } satisfies SyncReceipt
        changes.push(result)
        heads.set(conflict.object_id, result.change_id)
        return result
      },
    }
  }
  return { changes, transport, submissions }
}

const write = (text: string, parent_revision: string | null = null): Omit<PushChange, 'change_id'> => ({
  object_id: chapter,
  tombstone: false,
  parent_revision,
  payload: { format: 'test/1', text },
})

async function pullAll(scope: SyncScope, transport: SyncTransport) {
  while ((await pullSyncPage(scope, transport)).hasMore) {
    // Each cursor is durable before the next page.
  }
}

test('outbox is opt-in, scoped to service/work/device, and retains an unknown receipt on retry', async () => {
  const cloud = isolatedCloud()
  await expect(queueSyncChange(first, write('未启用'))).rejects.toMatchObject({ code: 'disabled' })
  await setSyncEnabled(first, true)
  const input = write('离线写好的正文')
  await queueSyncChange(first, input, 'one')
  await queueSyncChange(first, input, 'one')
  await expect(queueSyncChange(first, write('不同输入'), 'one')).rejects.toMatchObject({ code: 'invalid' })
  for (const other of [second, { ...first, workId: 'other' }, { ...first, service: 'different' }]) {
    expect(await readSyncState(other)).toMatchObject({ enabled: false, pending: [] })
    expect(await pushSyncPending(other, cloud.transport(other))).toBe(0)
  }
  const transport = cloud.transport(first)
  await expect(
    pushSyncPending(first, {
      ...transport,
      async push(change) {
        await transport.push(change)
        throw new Error('reply lost')
      },
      async receipt() {
        throw new Error('offline before receipt')
      },
    }),
  ).rejects.toThrow('reply lost')
  expect(await readSyncState(first)).toMatchObject({
    pending: [{ request: { change_id: 'one' } }],
    cursor: 0,
  })
  await setSyncEnabled(first, false)
  expect(await pushSyncPending(first, transport)).toBe(0)
  await setSyncEnabled(first, true)
  expect(await pushSyncPending(first, transport)).toBe(1)
  expect(cloud.submissions).toEqual(['one', 'one'])
  expect(cloud.changes).toHaveLength(1)
  expect(await readSyncState(first)).toMatchObject({ pending: [], cursor: 0 })
  await expect(queueSyncChange(first, input, 'one')).resolves.toBe('one')
  await pullAll(first, transport)
  expect(await readSyncHead(first, chapter)).toMatchObject({ change_id: 'one' })
  expect(await readSyncState(first)).toMatchObject({ pending: [], cursor: 1 })
})

test('receipt recovery does not advance cursor past unrelated events and malformed pages are rejected atomically', async () => {
  const cloud = isolatedCloud()
  const transport = cloud.transport(first)
  await setSyncEnabled(first, true)
  await cloud
    .transport(second)
    .push({ ...write('另一对象'), object_id: 'branch:work', change_id: 'external' })
  await queueSyncChange(first, write('当前章'), 'local')
  const sent = await pushSyncPending(first, {
    ...transport,
    async push(change) {
      await transport.push(change)
      throw new Error('reply lost')
    },
  })
  expect(sent).toBe(1)
  expect(await readSyncState(first)).toMatchObject({ pending: [], cursor: 0 })
  await expect(
    pullSyncPage(first, {
      ...transport,
      async pull() {
        return { cursor: 2, has_more: false, changes: [cloud.changes[1]] }
      },
    }),
  ).rejects.toMatchObject({ code: 'invalid' })
  expect(await readSyncState(first)).toMatchObject({ cursor: 0 })
  expect(await readSyncHead(first, 'branch:work')).toBeNull()
  const concurrent = await Promise.all([pullSyncPage(first, transport), pullSyncPage(first, transport)])
  expect(concurrent.map((page) => page.count).sort()).toEqual([0, 2])
  expect(await readSyncState(first)).toMatchObject({ cursor: 2, pending: [] })
  expect(await readSyncHead(first, 'branch:work')).toMatchObject({ change_id: 'external' })
})

test('two devices retain base/edit/delete and propagate explicit resolution without reviving a tombstone', async () => {
  const cloud = isolatedCloud()
  const a = cloud.transport(first)
  const b = cloud.transport(second)
  await setSyncEnabled(first, true)
  await setSyncEnabled(second, true)
  await queueSyncChange(first, write('依据稿'), 'base')
  await pushSyncPending(first, a)
  await pullAll(second, b)
  await queueSyncChange(first, write('设备甲改稿', 'base'), 'edited')
  await queueSyncChange(
    second,
    { object_id: chapter, parent_revision: 'base', payload: null, tombstone: true },
    'deleted',
  )
  await pushSyncPending(first, a)
  await pushSyncPending(second, b)
  await pullAll(second, b)
  expect(await readSyncConflict(second, 'deleted')).toMatchObject({
    incoming: { tombstone: true },
    current: { change_id: 'edited', payload: { text: '设备甲改稿' } },
    base: { change_id: 'base', payload: { text: '依据稿' } },
    resolvedBy: null,
  })
  await queueSyncResolution(
    second,
    chapter,
    'deleted',
    { parent_revision: 'edited', choice: 'incoming' },
    'resolved',
  )
  await pushSyncPending(second, b)
  await pullAll(first, a)
  expect(await readSyncState(first)).toMatchObject({ conflicts: [], cursor: 4 })
  expect(await readSyncHead(first, chapter)).toMatchObject({
    status: 'resolved',
    change_id: 'resolved',
    tombstone: true,
    payload: null,
  })
  expect(await readSyncConflict(first, 'deleted')).toMatchObject({
    resolvedBy: 'resolved',
    competing: { change_id: 'edited' },
  })
})

test('failed local pull commit leaves both head and cursor unchanged until a successful replay', async () => {
  const cloud = isolatedCloud()
  const transport = cloud.transport(first)
  await setSyncEnabled(first, true)
  await cloud.transport(second).push({ ...write('未安装的远端稿'), change_id: 'from-second' })
  const put = IDBObjectStore.prototype.put
  const failure = vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (
    this: IDBObjectStore,
    value,
    key,
  ) {
    if (this.name === 'replicas') throw new DOMException('Storage full', 'QuotaExceededError')
    return put.call(this, value, key)
  })
  try {
    await expect(pullSyncPage(first, transport)).rejects.toMatchObject({ code: 'storage' })
    expect(await readSyncState(first)).toMatchObject({ cursor: 0 })
    expect(await readSyncHead(first, chapter)).toBeNull()
  } finally {
    failure.mockRestore()
  }
  await expect(pullSyncPage(first, transport)).resolves.toMatchObject({ count: 1, hasMore: false })
  expect(await readSyncHead(first, chapter)).toMatchObject({ change_id: 'from-second' })
})
