import {
  applySyncedObject,
  exportLocalManuscript,
  syncManuscriptObjects,
  syncObjectSignature,
  validateSyncedObject,
  type LocalProject,
} from './offlineStore'
import {
  approveSyncResolution,
  listSyncHeads,
  markSyncBaseline,
  pullSyncPage,
  pushSyncPending,
  queueSyncChange,
  queueSyncResolution,
  readSyncConflict,
  readSyncState,
  SyncResolutionChanged,
  type SyncPayload,
  type SyncScope,
  type SyncTransport,
} from './syncClient'

export function cloudScope(service: string, workId: string, deviceId: string): SyncScope {
  const url = new URL(service.trim())
  if (
    url.username ||
    url.password ||
    url.pathname !== '/' ||
    url.search ||
    url.hash ||
    (url.protocol !== 'https:' &&
      !(url.protocol === 'http:' && ['127.0.0.1', '[::1]'].includes(url.hostname))) ||
    !/^[A-Za-z0-9._:-]{1,128}$/.test(deviceId)
  )
    throw new Error('请填写 HTTPS 或回环 HTTP 服务地址，以及有效的设备编号。')
  return { service: url.origin, workId, deviceId }
}

export function httpSyncTransport(scope: SyncScope, token: string): SyncTransport {
  if (!/^[A-Za-z0-9_-]{32,128}$/.test(token)) throw new Error('请输入同步服务签发的设备凭据。')
  const endpoint = `${scope.service}/api/sync/works/${encodeURIComponent(scope.workId)}`
  async function request(path: string, payload?: unknown, optional = false) {
    const response = await fetch(endpoint + path, {
      method: payload === undefined ? 'GET' : 'POST',
      credentials: 'omit',
      redirect: 'error',
      cache: 'no-store',
      headers: {
        Authorization: `Bearer ${token}`,
        ...(payload === undefined ? {} : { 'Content-Type': 'application/json' }),
      },
      signal: AbortSignal.timeout(15_000),
      ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
    })
    if (optional && response.status === 404) return null
    const result = await response.json()
    if (!response.ok) {
      if (response.status === 409 && ['sync_head_changed', 'sync_conflict_resolved'].includes(result.code)) {
        throw new SyncResolutionChanged('云端版本已更新。请再次同步并重新比较。')
      }
      throw new Error(result.message || `同步请求失败（HTTP ${response.status}）。`)
    }
    return result
  }
  return {
    push: (change) => request('/push', change),
    pull: (after) => request(`/pull?after=${after}&limit=50`),
    receipt: (id) => request('/receipts/' + encodeURIComponent(id), undefined, true),
    resolve: (id, data) => request('/conflicts/' + encodeURIComponent(id) + '/resolve', data),
  }
}

async function pull(scope: SyncScope, transport: SyncTransport) {
  for (let page = 0; page < 8; page++) {
    if (!(await pullSyncPage(scope, transport)).hasMore) return
  }
  throw new Error('尚有云端历史待读取，已保留游标，请再次同步。')
}

async function reconcile(project: LocalProject, scope: SyncScope) {
  const { blockedObjects } = await readSyncState(scope)
  const local = syncManuscriptObjects(await exportLocalManuscript(project.novel.id))
  const deferred = new Map<string, string>()
  const priority = (id: string) => (id.startsWith('revision:') ? 0 : id.startsWith('evidence:') ? 2 : 1)
  const depth = (payload: SyncPayload | null | undefined) => {
    const revision = payload?.revision
    return revision &&
      typeof revision === 'object' &&
      !Array.isArray(revision) &&
      typeof revision.revision === 'number'
      ? revision.revision
      : 0
  }
  const heads = (await listSyncHeads(scope)).sort(
    (left, right) =>
      priority(left.id) - priority(right.id) ||
      depth(left.receipt?.payload) - depth(right.receipt?.payload) ||
      (left.receipt?.sequence ?? 0) - (right.receipt?.sequence ?? 0),
  )
  for (const head of heads) {
    if (!head.receipt || head.baseline?.revision === head.revision || blockedObjects.includes(head.id))
      continue
    try {
      const object = await validateSyncedObject(project, head.id, head.receipt.payload)
      const target = syncObjectSignature(object)
      const signature = syncObjectSignature(local.get(head.id) ?? null)
      const resolution = head.approval?.changeId === head.revision ? head.approval : undefined
      const safeBase = resolution?.signature ?? head.baseline?.signature ?? 'null'
      if (object?.kind === 'revision' && signature !== 'null' && signature !== target) {
        deferred.set(head.id, '云端和本机以同一来源编号保存了不同修订，待人工核查。')
        continue
      }
      if (signature !== target && signature === safeBase) {
        if (!(await applySyncedObject(project.novel.id, head.id, object, signature))) {
          deferred.set(head.id, '本机内容已变化，下次同步会重新核对。')
          continue
        }
      } else if (signature !== target && (resolution || head.receipt.device_id !== scope.deviceId)) {
        // A resolution acknowledges exactly the local version compared by its author.
        // If the author kept writing, submit their newer version against its prior baseline.
        continue
      }
      await markSyncBaseline(scope, head.id, { revision: head.revision, signature: target })
    } catch (error) {
      deferred.set(head.id, error instanceof Error ? error.message : '云端对象待核对。')
    }
  }
  return deferred
}

async function draftChanges(project: LocalProject, scope: SyncScope, deferred: Map<string, string>) {
  const unsent = new Map<string, string>()
  const state = await readSyncState(scope)
  const pending = new Set(
    state.pending.map((entry) => (entry.kind === 'push' ? entry.request.object_id : entry.object_id)),
  )
  const heads = new Map((await listSyncHeads(scope)).map((head) => [head.id, head]))
  const objects = syncManuscriptObjects(await exportLocalManuscript(project.novel.id))
  for (const [id, object] of objects) {
    if (object && new TextEncoder().encode(JSON.stringify(object)).length > 1024 * 1024 - 4096) {
      unsent.set(id, '本机对象超过单项同步限制，可导出稿件包供独立工作区检查。')
      continue
    }
    const base = heads.get(id)?.baseline
    const signature = syncObjectSignature(object)
    if (
      signature === (base?.signature ?? 'null') ||
      pending.has(id) ||
      deferred.has(id) ||
      state.blockedObjects.includes(id)
    )
      continue
    await queueSyncChange(scope, {
      object_id: id,
      parent_revision: base?.revision ?? null,
      payload: object as SyncPayload | null,
      tombstone: object === null,
    })
  }
  return unsent
}

export async function syncWebManuscript(project: LocalProject, scope: SyncScope, transport: SyncTransport) {
  if (scope.workId !== project.branch.id) throw new Error('同步范围与当前工作版本不一致。')
  async function exchange() {
    await pull(scope, transport)
    const deferred = await reconcile(project, scope)
    const unsent = await draftChanges(project, scope, deferred)
    await pushSyncPending(scope, transport)
    await pull(scope, transport)
    const remaining = await reconcile(project, scope)
    return {
      ...(await readSyncState(scope)),
      deferred: [...new Map([...unsent, ...remaining])].map(([object_id, reason]) => ({ object_id, reason })),
    }
  }
  if (typeof navigator !== 'undefined' && navigator.locks) {
    return navigator.locks.request(`ainovel-sync/${scope.workId}`, { ifAvailable: true }, (lock) => {
      if (!lock) throw new Error('另一标签页正在同步此作品，请稍后重试。')
      return exchange()
    })
  }
  return exchange()
}

export async function prepareWebResolution(project: LocalProject, scope: SyncScope, id: string) {
  const compared = await readSyncConflict(scope, id)
  if (!compared?.current || compared.resolvedBy) throw new Error('冲突已变化，请再次同步。')
  const object =
    syncManuscriptObjects(await exportLocalManuscript(project.novel.id)).get(compared.incoming.object_id) ??
    null
  return { ...compared, current: compared.current, object, signature: syncObjectSignature(object) }
}

export async function resolveWebManuscript(
  project: LocalProject,
  scope: SyncScope,
  transport: SyncTransport,
  compared: Awaited<ReturnType<typeof prepareWebResolution>>,
  choice: 'current' | 'incoming' | 'local',
) {
  const id = compared.incoming.conflict_id!
  const fresh = await prepareWebResolution(project, scope, id)
  if (fresh.current.change_id !== compared.current.change_id || fresh.signature !== compared.signature)
    throw new Error('本机或云端版本已变化，请重新比较。')
  const resolutionId = crypto.randomUUID()
  const objectId = compared.incoming.object_id
  await approveSyncResolution(scope, objectId, { changeId: resolutionId, signature: compared.signature })
  const parent_revision = compared.current.change_id
  await queueSyncResolution(
    scope,
    objectId,
    id,
    choice === 'local'
      ? {
          choice: 'merge',
          parent_revision,
          payload: fresh.object as SyncPayload | null,
          tombstone: !fresh.object,
        }
      : { choice, parent_revision },
    resolutionId,
  )
  return syncWebManuscript(project, scope, transport)
}
