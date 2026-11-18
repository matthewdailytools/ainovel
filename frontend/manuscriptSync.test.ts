import { beforeEach, expect, test } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import {
  confirmLocalProjectBrief,
  createLocalChapter,
  createLocalProject,
  readLocalHistory,
  readLocalPublications,
  saveLocalChapter,
} from './offlineStore'
import { syncWebManuscript } from './manuscriptSync'
import {
  readSyncState,
  setSyncEnabled,
  type PushChange,
  type SyncReceipt,
  type SyncTransport,
} from './syncClient'

beforeEach(() => {
  globalThis.indexedDB = new IDBFactory()
})

function service(device: string, objectId: string, acceptedBeforeDisconnect: boolean) {
  const submitted: PushChange[] = []
  const log: SyncReceipt[] = []
  const latest = new Map<string, SyncReceipt>()
  let disconnect = true
  const transport: SyncTransport = {
    async push(request) {
      submitted.push(request)
      const lost = disconnect && request.object_id === objectId
      if (lost) {
        disconnect = false
        if (!acceptedBeforeDisconnect) throw new Error('请求发送中断')
      }
      const old = latest.get(request.object_id)
      const conflict = (old?.change_id ?? null) !== request.parent_revision
      const receipt: SyncReceipt = {
        ...request,
        sequence: log.length + 1,
        status: conflict ? 'conflict' : 'applied',
        device_id: device,
        head_revision: conflict ? old!.change_id : request.change_id,
        conflict_id: conflict ? request.change_id : null,
      }
      log.push(receipt)
      if (!conflict) latest.set(receipt.object_id, receipt)
      if (lost) throw new Error('响应丢失')
      return receipt
    },
    async receipt() {
      return null
    },
    async pull(after) {
      return { changes: log.slice(after), cursor: log.length, has_more: false }
    },
    async resolve() {
      throw new Error('没有请求决议')
    },
  }
  return { transport, latest, submitted, log }
}

test.each([false, true])(
  'chapter keeps a later save across an interrupted send, server accepted=%s',
  async (accepted) => {
    const project = await createLocalProject('待同步章稿')
    const scope = { service: 'http://127.0.0.1:8888', workId: project.branch.id, deviceId: 'web' }
    const key = project.novel.id
    const chapter = await createLocalChapter(key, '铜铃')
    const id = `chapter:${chapter.id}`
    const [root] = await readLocalHistory(key, chapter.id)
    const original = await saveLocalChapter(key, chapter.id, root.id, '较早稿')
    const cloud = service(scope.deviceId, id, accepted)
    await setSyncEnabled(scope, true)
    await expect(syncWebManuscript(project, scope, cloud.transport)).rejects.toThrow()
    const prepared = (await readSyncState(scope)).pending.find(
      (item) => item.kind === 'push' && item.request.object_id === id,
    )
    expect(prepared?.request.change_id).toBe(
      cloud.submitted.find((request) => request.object_id === id)?.change_id,
    )
    const previous = (await readLocalPublications(scope.workId)).find(({ objectId }) => objectId === id)
    const next = await saveLocalChapter(key, chapter.id, original.id, '中断后继续写的下一稿')
    const fresh = (await readLocalPublications(scope.workId)).find(({ objectId }) => objectId === id)
    expect(fresh?.id).not.toBe(previous?.id)

    const firstRetry = await syncWebManuscript(project, scope, cloud.transport)
    if (!accepted) expect(firstRetry.localPending).toBeGreaterThan(0)
    await syncWebManuscript(project, scope, cloud.transport)
    expect(cloud.submitted.filter(({ change_id }) => change_id === prepared?.request.change_id)).toHaveLength(
      accepted ? 1 : 2,
    )
    const heads = cloud.log.filter(({ object_id }) => object_id === id)
    expect(heads.map(({ status }) => status)).toEqual(['applied', 'applied'])
    expect(heads[1].parent_revision).toBe(heads[0].change_id)
    expect(cloud.latest.get(id)?.payload?.head).toMatchObject({ id: next.id, content: next.content })
    expect(await readLocalPublications(scope.workId)).toEqual([])

    const intermediate = await saveLocalChapter(key, chapter.id, next.id, '短暂的第三稿')
    const restore = await saveLocalChapter(key, chapter.id, intermediate.id, '', next.id)
    expect(restore.sha256).toBe(next.sha256)
    await syncWebManuscript(project, scope, cloud.transport)
    expect(cloud.latest.get(id)?.payload?.head).toMatchObject({ id: restore.id })
  },
)

test('M1 workspace retains its later adoption when the prior head push must be retried', async () => {
  const project = await createLocalProject('待同步立项')
  const scope = { service: 'http://127.0.0.1:8888', workId: project.branch.id, deviceId: 'web' }
  const id = `project:${scope.workId}`
  const brief = {
    position: '追查旧铃案',
    track: '线索式悬疑',
    genre_ids: ['xianxia'],
    audience: '偏好悬疑的读者',
    distinction: '只用首章证物',
    length: '三卷十万字',
    cadence: '',
    ending: '旧铃案告破',
    final_image: '',
  }
  const first = await confirmLocalProjectBrief(project.novel.id, null, { idea: '首版立项', brief })
  const cloud = service(scope.deviceId, id, false)
  await setSyncEnabled(scope, true)
  await expect(syncWebManuscript(project, scope, cloud.transport)).rejects.toThrow()
  const next = await confirmLocalProjectBrief(project.novel.id, first.id, { idea: '修订后的立项', brief })
  const firstRetry = await syncWebManuscript(project, scope, cloud.transport)
  expect(firstRetry.localPending).toBe(1)
  await syncWebManuscript(project, scope, cloud.transport)
  const adopted = cloud.log.filter(({ object_id }) => object_id === id)
  expect(adopted.map(({ status, payload }) => [status, payload?.revision_id])).toEqual([
    ['applied', first.id],
    ['applied', next.id],
  ])
  expect(adopted[1].parent_revision).toBe(adopted[0].change_id)
  expect(cloud.latest.get(`project-version:${next.id}`)?.payload?.version).toMatchObject({
    parent_id: first.id,
  })
  expect(await readLocalPublications(scope.workId)).toEqual([])
})
