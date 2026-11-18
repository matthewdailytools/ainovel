import {
  applySyncedObject,
  acknowledgeLocalPublications,
  manuscriptHash,
  readLocalPublications,
  readLocalSyncSnapshot,
  syncManuscriptObjects,
  syncObjectSignature,
  validateSyncedObject,
  type LocalPublication,
  type LocalProject,
  type SyncedObject,
} from './offlineStore'
import {
  outlineChapterPlans,
  requiredOutlineStoryRecord,
  validateOutlineActiveBasis,
  validateOutlineVersionSources,
  type OutlineVersion,
} from './outlineSyncModel'
import {
  approveSyncResolution,
  listSyncHeads,
  markSyncBaseline,
  pullSyncPage,
  pushSyncPending,
  queueSyncChange,
  queueSyncResolution,
  readPublicationReceipts,
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

function orderVersions<T extends { version: { id: string; parent_id: string | null } }>(versions: T[]) {
  const byId = new Map(versions.map((entry) => [entry.version.id, entry]))
  const seen = new Set<string>()
  const ordered: T[] = []
  for (const entry of versions) {
    const path: T[] = []
    let revision: T | undefined = entry
    while (revision && !seen.has(revision.version.id)) {
      path.push(revision)
      seen.add(revision.version.id)
      revision = revision.version.parent_id ? byId.get(revision.version.parent_id) : undefined
    }
    ordered.push(...path.reverse())
  }
  return ordered
}

async function localObjects(project: LocalProject) {
  const {
    manuscript,
    brief: { versions, current },
    settings,
    outline,
    publications,
  } = await readLocalSyncSnapshot(project.novel.id)
  const objects = syncManuscriptObjects(manuscript)
  const format = 'ainovel-manuscript-sync/1'
  const branch_id = project.branch.id
  for (const { version } of orderVersions(versions))
    objects.set(`project-version:${version.id}`, { format, kind: 'project-version', branch_id, version })
  if (current)
    objects.set(`project:${branch_id}`, {
      format,
      kind: 'project-head',
      branch_id,
      revision_id: current.version.id,
    })
  for (const { version } of orderVersions(settings.versions))
    objects.set(`settings-version:${version.id}`, { format, kind: 'settings-version', branch_id, version })
  if (settings.current)
    objects.set(`settings:${branch_id}`, {
      format,
      kind: 'settings-head',
      branch_id,
      revision_id: settings.current.version.id,
    })
  for (const { version } of orderVersions(outline.versions))
    objects.set(`outline-version:${version.id}`, { format, kind: 'outline-version', branch_id, version })
  if (outline.current)
    objects.set(`outline:${branch_id}`, {
      format,
      kind: 'outline-head',
      branch_id,
      revision_id: outline.current.version.id,
    })
  return {
    objects,
    versions,
    settings,
    outline,
    publications: new Map(publications.map((record) => [record.objectId, record])),
  }
}

function publicationChangeId(scope: SyncScope, record: LocalPublication) {
  return manuscriptHash(
    JSON.stringify([scope.service, scope.workId, scope.deviceId, record.objectId, record.id]),
  )
}

async function acknowledgePublications(scope: SyncScope) {
  const pending = await readLocalPublications(scope.workId)
  if (!pending.length) return
  const ids = await Promise.all(pending.map((record) => publicationChangeId(scope, record)))
  const receipts = new Map(
    (await readPublicationReceipts(scope, ids)).map((receipt) => [receipt.change_id, receipt]),
  )
  await acknowledgeLocalPublications(
    pending.filter(({ objectId }, index) => receipts.get(ids[index])?.object_id === objectId),
  )
}

async function reconcile(project: LocalProject, scope: SyncScope) {
  const { blockedObjects } = await readSyncState(scope)
  const { objects: local, publications } = await localObjects(project)
  const deferred = new Map<string, string>()
  const priority = (id: string) => {
    if (id.startsWith('revision:') || id.startsWith('project-version:')) return 0
    if (id.startsWith('settings-version:')) return 2
    if (id.startsWith('settings:')) return 3
    if (id.startsWith('outline-version:')) return 4
    if (id.startsWith('outline:')) return 5
    if (id.startsWith('evidence:')) return 6
    return 1
  }
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
      (left.source?.sequence ?? left.receipt?.sequence ?? 0) -
        (right.source?.sequence ?? right.receipt?.sequence ?? 0),
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
      const publicationId = publications.get(head.id)?.id ?? null
      if (
        object?.kind === 'project-version' ||
        object?.kind === 'settings-version' ||
        object?.kind === 'outline-version'
      ) {
        const { device_id, change_id, sequence } = head.source ?? head.receipt
        await applySyncedObject(project.novel.id, head.id, object, signature, {
          source: { device_id, change_id, sequence },
        })
        await markSyncBaseline(scope, head.id, {
          revision: head.revision,
          signature: target,
          sequence: head.sequence,
        })
        continue
      }
      if (object?.kind === 'revision' && signature !== 'null' && signature !== target) {
        deferred.set(head.id, '云端和本机以同一来源编号保存了不同修订，待人工核查。')
        continue
      }
      const adoptsHead =
        head.id.startsWith('chapter:') ||
        head.id.startsWith('project:') ||
        head.id.startsWith('settings:') ||
        head.id.startsWith('outline:')
      if (
        resolution &&
        ((resolution.publicationId ?? null) !== publicationId || resolution.signature !== signature)
      )
        continue
      if (adoptsHead && publicationId && !resolution) {
        if (head.lastPublication?.id === head.revision)
          await markSyncBaseline(scope, head.id, {
            revision: head.revision,
            signature: target,
            sequence: head.sequence,
          })
        // An earlier accepted send becomes the CAS base. The newer local save remains pending.
        continue
      }
      if ((signature !== target && signature === safeBase) || (resolution && publicationId)) {
        if (
          !(await applySyncedObject(project.novel.id, head.id, object, signature, {
            publicationId,
            consumePublication: !!resolution,
          }))
        ) {
          deferred.set(head.id, '本机内容已变化，下次同步会重新核对。')
          continue
        }
      } else if (signature !== target && (resolution || head.receipt.device_id !== scope.deviceId)) {
        // A resolution acknowledges exactly the local version compared by its author.
        // If the author kept writing, submit their newer version against its prior baseline.
        continue
      }
      await markSyncBaseline(scope, head.id, {
        revision: head.revision,
        signature: target,
        sequence: head.sequence,
      })
    } catch (error) {
      deferred.set(head.id, error instanceof Error ? error.message : '云端对象待核对。')
    }
  }
  return deferred
}

function settingPublicationSources(object: SyncedObject | null): string[] {
  if (object?.kind === 'settings-head') return [`settings-version:${object.revision_id}`]
  if (object?.kind !== 'settings-version') return []
  const version = object.version
  const origins = Object.values(version.origins)
  const projects = new Set([
    version.project_version_id,
    ...origins.map(({ project_version_id }) => project_version_id),
  ])
  const revisions = new Set(origins.flatMap(({ evidence }) => (evidence ? [evidence.revision_id] : [])))
  return [
    ...(version.parent_id ? [`settings-version:${version.parent_id}`] : []),
    ...[...projects].flatMap((id) => (id ? [`project-version:${id}`] : [])),
    ...[...revisions].map((id) => `revision:${id}`),
  ]
}

function outlinePublicationSources(
  object: SyncedObject | null,
  current: OutlineVersion | undefined,
): string[] {
  if (object?.kind === 'outline-head' && current) {
    return [
      `outline-version:${object.revision_id}`,
      ...outlineChapterPlans(current.plan).map(({ chapter_id }) => `chapter:${chapter_id}`),
    ]
  }
  if (object?.kind !== 'outline-version') return []
  const { parent_id, project_version_id, settings_version_id } = object.version
  return [
    ...(parent_id ? [`outline-version:${parent_id}`] : []),
    ...(project_version_id ? [`project-version:${project_version_id}`] : []),
    ...(settings_version_id ? [`settings-version:${settings_version_id}`] : []),
  ]
}

function assertOutlineSources(
  snapshot: Pick<Awaited<ReturnType<typeof localObjects>>, 'outline' | 'settings' | 'versions'>,
  version: OutlineVersion,
) {
  const { outline, settings, versions } = snapshot
  const required = requiredOutlineStoryRecord(version, outline.restorePending)
  if (required) throw new Error(`等待原伏笔记录 ${required} 的实际进展或历史恢复。`)
  validateOutlineActiveBasis(version, {
    projectVersionId: outline.projectVersionId,
    settingsVersionId: outline.settings?.id ?? null,
  })
  return validateOutlineVersionSources(version, {
    project: (id) => versions.some(({ version }) => version.id === id),
    settings: (id) => settings.versions.find(({ version }) => version.id === id)?.version,
    outline: (id) => outline.versions.some(({ version }) => version.id === id),
    chapter: (id) => outline.chapters.find((chapter) => chapter.id === id),
    promise: (id) => outline.promises.find((promise) => promise.id === id),
  })
}

async function draftChanges(project: LocalProject, scope: SyncScope, deferred: Map<string, string>) {
  const unsent = new Map<string, string>()
  const state = await readSyncState(scope)
  const pending = new Set(
    state.pending.map((entry) => (entry.kind === 'push' ? entry.request.object_id : entry.object_id)),
  )
  const heads = new Map((await listSyncHeads(scope)).map((head) => [head.id, head]))
  const snapshot = await localObjects(project)
  const { objects, publications, settings, outline } = snapshot
  for (const [id, object] of objects) {
    if (object && new TextEncoder().encode(JSON.stringify(object)).length > 1024 * 1024 - 4096) {
      unsent.set(id, '本机对象超过单项同步限制，可导出稿件包供独立工作区检查。')
      continue
    }
    const marker = publications.get(id)
    const head = heads.get(id)
    const base = head?.baseline
    const signature = syncObjectSignature(object)
    if (
      (!marker && signature === (base?.signature ?? 'null')) ||
      pending.has(id) ||
      deferred.has(id) ||
      state.blockedObjects.includes(id)
    )
      continue
    if (
      object?.kind === 'settings-head' &&
      settings.current?.version.project_version_id !== settings.projectVersionId
    ) {
      unsent.set(id, '当前设定依据的立项已变化，请复核并重新确认后同步。')
      continue
    }
    if (settingPublicationSources(object).some((source) => !heads.has(source) && !pending.has(source))) {
      unsent.set(id, '立项、正文或父级设定来源尚未排到云端，当前设定继续保留。')
      continue
    }
    if (object?.kind === 'outline-head' && outline.current) {
      try {
        await assertOutlineSources(snapshot, outline.current.version)
      } catch (error) {
        unsent.set(id, error instanceof Error ? error.message : '当前大纲尚待核对。')
        continue
      }
    }
    if (
      outlinePublicationSources(object, outline.current?.version).some(
        (source) => !heads.has(source) && !pending.has(source),
      )
    ) {
      unsent.set(id, '立项、设定、章节或父级大纲来源尚未排到云端，当前大纲继续保留。')
      continue
    }
    const acknowledged = head?.lastPublication
    const parent =
      marker && acknowledged && acknowledged.sequence > (base?.sequence ?? 0)
        ? acknowledged.id
        : base?.revision
    await queueSyncChange(
      scope,
      {
        object_id: id,
        parent_revision: parent ?? null,
        payload: object as SyncPayload | null,
        tombstone: object === null,
      },
      marker ? await publicationChangeId(scope, marker) : undefined,
    )
    pending.add(id)
  }
  return unsent
}

export async function syncWebManuscript(project: LocalProject, scope: SyncScope, transport: SyncTransport) {
  if (scope.workId !== project.branch.id) throw new Error('同步范围与当前工作版本不一致。')
  async function exchange() {
    await pull(scope, transport)
    await acknowledgePublications(scope)
    const deferred = await reconcile(project, scope)
    const unsent = await draftChanges(project, scope, deferred)
    await pushSyncPending(scope, transport)
    await pull(scope, transport)
    await acknowledgePublications(scope)
    const remaining = await reconcile(project, scope)
    return {
      ...(await readSyncState(scope)),
      localPending: (await readLocalPublications(scope.workId)).length,
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
  const { objects, publications, versions, settings, outline } = await localObjects(project)
  const object = objects.get(compared.incoming.object_id) ?? null
  const publicationId = publications.get(compared.incoming.object_id)?.id ?? null
  return {
    ...compared,
    current: compared.current,
    object,
    versions,
    settings,
    outline,
    signature: syncObjectSignature(object),
    publicationId,
  }
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
  if (
    fresh.current.change_id !== compared.current.change_id ||
    fresh.signature !== compared.signature ||
    fresh.publicationId !== compared.publicationId
  )
    throw new Error('本机或云端版本已变化，请重新比较。')
  if (choice === 'local' && fresh.object?.kind === 'project-head') {
    const selected = fresh.object.revision_id
    if (!fresh.versions.some(({ version, source }) => source && version.id === selected))
      throw new Error('本机立项刚有新确认，请先同步源修订，再比较冲突。')
  }
  if (fresh.incoming.object_id.startsWith('settings:')) {
    const chosen =
      choice === 'local'
        ? fresh.object
        : choice === 'current'
          ? fresh.current.payload
          : fresh.incoming.payload
    const selection = chosen?.kind === 'settings-head' && 'revision_id' in chosen ? chosen.revision_id : null
    const entry = fresh.settings.versions.find(({ version }) => version.id === selection)
    if (!entry?.source) throw new Error('尚未取得所选设定的云端来源，请先同步源修订，再比较冲突。')
    if (entry.version.project_version_id !== fresh.settings.projectVersionId)
      throw new Error('所选设定依据的立项与本机不同，请先采用对应立项，再解决冲突。')
  }
  if (fresh.incoming.object_id.startsWith('outline:')) {
    const chosen =
      choice === 'local'
        ? fresh.object
        : choice === 'current'
          ? fresh.current.payload
          : fresh.incoming.payload
    const selection = chosen?.kind === 'outline-head' ? chosen.revision_id : null
    const entry = fresh.outline.versions.find(({ version }) => version.id === selection)
    if (!entry?.source) throw new Error('尚未取得所选大纲的云端来源，请先同步源修订，再比较冲突。')
    await assertOutlineSources(fresh, entry.version)
  }
  const resolutionId = crypto.randomUUID()
  const objectId = compared.incoming.object_id
  await approveSyncResolution(scope, objectId, {
    changeId: resolutionId,
    signature: compared.signature,
    publicationId: compared.publicationId,
  })
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
