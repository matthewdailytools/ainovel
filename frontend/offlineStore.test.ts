import { beforeEach, expect, test, vi } from 'vitest'
import { IDBFactory, IDBObjectStore } from 'fake-indexeddb'
import {
  createLocalChapter,
  createLocalProject,
  confirmLocalProjectBrief,
  applySyncedObject,
  exportLocalManuscript,
  importLocalManuscript,
  listLocalProjects,
  manuscriptHash,
  readLocalPublications,
  readLocalHistory,
  readLocalProjectBrief,
  saveLocalChapter,
  validateManuscript,
  validateSyncedObject,
} from './offlineStore'

beforeEach(() => vi.stubGlobal('indexedDB', new IDBFactory()))

const bundle = (value: unknown) => new Blob([JSON.stringify(value)], { type: 'application/json' })

test('formal writes preserve authorship, parent IDs and older revisions across concurrent transactions', async () => {
  const a = await createLocalProject('遗铃')
  const b = await createLocalProject('另一作品')
  const chapter = await createLocalChapter(a.novel.id, '铜铃')
  const [root] = await readLocalHistory(a.novel.id, chapter.id)
  await expect(saveLocalChapter(b.novel.id, chapter.id, root.id, '外部写入')).rejects.toMatchObject({
    code: 'ownership',
  })

  const writes = await Promise.allSettled([
    saveLocalChapter(a.novel.id, chapter.id, root.id, '门内响铃😀'),
    saveLocalChapter(a.novel.id, chapter.id, root.id, '第二窗口的正文'),
  ])
  expect(writes.filter((write) => write.status === 'fulfilled')).toHaveLength(1)
  expect(writes.find((write) => write.status === 'rejected')).toMatchObject({
    reason: { code: 'conflict' },
  })
  const history = await readLocalHistory(a.novel.id, chapter.id)
  expect(history).toHaveLength(2)
  expect(history[1]).toMatchObject({ revision: 1, parent_id: root.id })
  expect(history[1].sha256).toBe(await manuscriptHash(history[1].content))
  const restored = await saveLocalChapter(a.novel.id, chapter.id, history[1].id, '', root.id)
  expect(restored).toMatchObject({ revision: 2, parent_id: history[1].id, content: root.content })
  expect((await readLocalHistory(a.novel.id, chapter.id)).slice(0, 2)).toEqual(history)
})

test('exchange retains stable IDs and exact UTF-8 text and checks hashes and dangling parents before import', async () => {
  const a = await createLocalProject('遗铃')
  const chapter = await createLocalChapter(a.novel.id, '得到旧铃')
  const [base] = await readLocalHistory(a.novel.id, chapter.id)
  await saveLocalChapter(a.novel.id, chapter.id, base.id, '\ufeff铜铃𠮷😀。\r\n她收好信物。')
  const pack = await exportLocalManuscript(a.novel.id)
  const invalidBody = structuredClone(pack)
  invalidBody.revisions[1].content += '外部篡改'
  await expect(validateManuscript(invalidBody)).rejects.toMatchObject({ code: 'format' })
  const badParent = structuredClone(pack)
  badParent.revisions[1].parent_id = badParent.revisions[1].id
  await expect(validateManuscript(badParent)).rejects.toMatchObject({ code: 'format' })
  const foreignRevision = structuredClone(pack)
  foreignRevision.revisions[1].chapter_id = 'another-chapter'
  await expect(validateManuscript(foreignRevision)).rejects.toMatchObject({ code: 'format' })
  await expect(
    importLocalManuscript(bundle({ ...pack, format: 'ainovel-manuscript/2' })),
  ).rejects.toMatchObject({
    code: 'format',
  })
  await expect(importLocalManuscript(bundle(pack))).rejects.toMatchObject({ code: 'conflict' })
  expect(await exportLocalManuscript(a.novel.id)).toEqual(pack)
  vi.stubGlobal('indexedDB', new IDBFactory())
  await importLocalManuscript(bundle(pack))
  expect(await listLocalProjects()).toEqual([{ novel: pack.novel, branch: pack.branch }])
  expect(await exportLocalManuscript(a.novel.id)).toEqual(pack)
})

test('quota failure aborts the whole browser write and preserves existing formal versions', async () => {
  const a = await createLocalProject('遗铃')
  const chapter = await createLocalChapter(a.novel.id, '找到秤砣')
  const [base] = await readLocalHistory(a.novel.id, chapter.id)
  const previous = await exportLocalManuscript(a.novel.id)
  const add = IDBObjectStore.prototype.add
  const failure = vi.spyOn(IDBObjectStore.prototype, 'add').mockImplementation(function (
    this: IDBObjectStore,
    value,
    key,
  ) {
    if (this.name === 'revisions') throw new DOMException('Storage full', 'QuotaExceededError')
    return add.call(this, value, key)
  })
  try {
    await expect(saveLocalChapter(a.novel.id, chapter.id, base.id, '空间不足未写入')).rejects.toMatchObject({
      code: 'quota',
    })
    expect(await exportLocalManuscript(a.novel.id)).toEqual(previous)
  } finally {
    failure.mockRestore()
  }
})

test('an unsupported newer database opens read-only from this app’s perspective and keeps records intact', async () => {
  await new Promise<void>((resolve, reject) => {
    const opening = indexedDB.open('ainovel-offline-workspace', 6)
    opening.onupgradeneeded = () => opening.result.createObjectStore('v2').add('保留新版数据', 'data')
    opening.onsuccess = () => {
      opening.result.close()
      resolve()
    }
    opening.onerror = () => reject(opening.error)
  })
  await expect(listLocalProjects()).rejects.toMatchObject({ code: 'database' })
  const record = await new Promise((resolve, reject) => {
    const opening = indexedDB.open('ainovel-offline-workspace', 6)
    opening.onsuccess = () => {
      const tx = opening.result.transaction('v2')
      const read = tx.objectStore('v2').get('data')
      read.onsuccess = () => resolve(read.result)
      tx.oncomplete = () => opening.result.close()
    }
    opening.onerror = () => reject(opening.error)
  })
  expect(record).toBe('保留新版数据')
})

test('formal M1 confirmations use a local CAS and keep incoming competing revisions immutable', async () => {
  const project = await createLocalProject('异地立项')
  const projectId = project.novel.id
  const branchId = project.branch.id
  const draft = {
    idea: '追查遗落的铜铃',
    brief: {
      position: '层层解开旧案',
      track: '线索式悬疑',
      genre_ids: ['xianxia'],
      audience: '偏好悬疑的读者',
      distinction: '证物都在首章出现',
      length: '十万字',
      cadence: '',
      ending: '解开铜铃旧案',
      final_image: '',
    },
  }
  const local = await confirmLocalProjectBrief(projectId, null, draft)
  const pending = await readLocalPublications(branchId)
  expect(pending.map(({ objectId }) => objectId).sort()).toEqual([
    `project-version:${local.id}`,
    `project:${branchId}`,
  ])
  const put = IDBObjectStore.prototype.put
  const failure = vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (
    this: IDBObjectStore,
    value,
    key,
  ) {
    if (this.name === 'publications') throw new DOMException('Storage full', 'QuotaExceededError')
    return put.call(this, value, key)
  })
  try {
    await expect(
      confirmLocalProjectBrief(projectId, local.id, { ...draft, idea: '未能排队的立项' }),
    ).rejects.toMatchObject({ code: 'quota' })
    expect((await readLocalProjectBrief(projectId)).versions.map(({ version }) => version)).toEqual([local])
    expect(await readLocalPublications(branchId)).toEqual(pending)
  } finally {
    failure.mockRestore()
  }
  await expect(confirmLocalProjectBrief(projectId, null, draft)).rejects.toMatchObject({ code: 'conflict' })
  const remote = { ...local, id: 'competing-project', parent_id: local.id, idea: '从线索中的草药重新立项' }
  const format = 'ainovel-manuscript-sync/1'
  const source = { device_id: 'other-device', change_id: 'remote-project-version', sequence: 4 }
  const adopted = await validateSyncedObject(project, `project:${branchId}`, {
    format,
    kind: 'project-head',
    branch_id: branchId,
    revision_id: remote.id,
  })
  const base = JSON.stringify(['project-head', branchId, local.id])
  await expect(applySyncedObject(projectId, `project:${branchId}`, adopted, base)).rejects.toMatchObject({
    code: 'missing',
  })
  const versionId = `project-version:${remote.id}`
  const object = await validateSyncedObject(project, versionId, {
    format,
    kind: 'project-version',
    branch_id: branchId,
    version: remote,
  })
  await expect(applySyncedObject(projectId, versionId, object, 'null', { source })).resolves.toBe(true)
  await expect(applySyncedObject(projectId, `project:${branchId}`, adopted, base)).resolves.toBe(true)
  const after = await readLocalProjectBrief(projectId)
  expect(after.current).toMatchObject({ version: remote, source })
  expect(after.versions.map(({ version }) => version.id)).toEqual(
    expect.arrayContaining([local.id, remote.id]),
  )
  const archive = await exportLocalManuscript(projectId)
  const history = archive.project_history!
  expect(history.adopted_revision_id).toBe(remote.id)
  expect(history.versions).toEqual(expect.arrayContaining([local, remote]))
  expect(JSON.stringify(archive)).not.toContain(source.change_id)
  await expect(
    validateManuscript({ ...archive, project_history: { ...history, versions: [remote] } }),
  ).rejects.toMatchObject({ code: 'format' })
  vi.stubGlobal('indexedDB', new IDBFactory())
  await importLocalManuscript(bundle(archive))
  const recovered = (await readLocalProjectBrief(projectId)).current
  expect(recovered).toMatchObject({ version: remote, fromPackage: true })
  expect(recovered?.source).toBeUndefined()
  expect((await exportLocalManuscript(projectId)).project_history).toEqual(history)
})

test('failure writing a chapter publication rolls back content and current head', async () => {
  const project = await createLocalProject('待发一致性')
  const chapter = await createLocalChapter(project.novel.id, '旧铃')
  const [base] = await readLocalHistory(project.novel.id, chapter.id)
  const pending = await readLocalPublications(project.branch.id)
  const put = IDBObjectStore.prototype.put
  const failure = vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (
    this: IDBObjectStore,
    value,
    key,
  ) {
    if (this.name === 'publications') throw new DOMException('Storage full', 'QuotaExceededError')
    return put.call(this, value, key)
  })
  try {
    await expect(
      saveLocalChapter(project.novel.id, chapter.id, base.id, '要么同时保存，要么回滚。'),
    ).rejects.toMatchObject({ code: 'quota' })
    expect(await readLocalHistory(project.novel.id, chapter.id)).toEqual([base])
    expect(await readLocalPublications(project.branch.id)).toEqual(pending)
  } finally {
    failure.mockRestore()
  }
})

test('an older comparison cannot adopt over a newer local publication with the same text', async () => {
  const project = await createLocalProject('同文不同版')
  const branchId = project.branch.id
  const key = project.novel.id
  const chapter = await createLocalChapter(key, '铜铃')
  const [old] = await readLocalHistory(key, chapter.id)
  const objectId = `chapter:${chapter.id}`
  const shown = (await readLocalPublications(branchId)).find((entry) => entry.objectId === objectId)!

  const edit = await saveLocalChapter(key, chapter.id, old.id, '一稿内容。')
  const restored = await saveLocalChapter(key, chapter.id, edit.id, '', old.id)
  expect(restored.sha256).toBe(old.sha256)
  const earlier = JSON.stringify([
    'chapter',
    chapter.id,
    branchId,
    chapter.position,
    chapter.title,
    old.sha256,
  ])
  const incoming = await validateSyncedObject(project, objectId, {
    format: 'ainovel-manuscript-sync/1',
    kind: 'chapter',
    chapter: { ...chapter, revision: 1 },
    head: {
      ...old,
      id: 'remote-head',
      parent_id: old.id,
      revision: 1,
      content: '他人改写。',
      sha256: await manuscriptHash('他人改写。'),
    },
  })
  expect(
    await applySyncedObject(key, objectId, incoming, earlier, {
      publicationId: shown.id,
      consumePublication: true,
    }),
  ).toBe(false)
  expect((await readLocalHistory(key, chapter.id)).at(-1)).toEqual(restored)
  expect((await readLocalPublications(branchId)).find((entry) => entry.objectId === objectId)?.id).not.toBe(
    shown.id,
  )
})

test('source histories recover exact evidence without replacing any locally numbered revision', async () => {
  const project = await createLocalProject('两地修订')
  const localId = project.novel.id
  const chapter = await createLocalChapter(localId, '拾铃')
  const [base] = await readLocalHistory(localId, chapter.id)
  const authorRevision = await saveLocalChapter(localId, chapter.id, base.id, '两份不同原稿。')
  const source = { ...authorRevision, id: 'remote-original', content: '远端作者原稿。' }
  source.sha256 = await manuscriptHash(source.content)
  const format = 'ainovel-manuscript-sync/1'
  const body = '以远端作者修订为依据。'
  const evidence = {
    format,
    kind: 'evidence',
    document: {
      id: 'linked-evidence',
      kind: 'inspection',
      title: '来源记录',
      body,
      sha256: await manuscriptHash(body),
      chapters: [{ id: chapter.id, revision: 1, revision_id: source.id, sha256: source.sha256 }],
    },
  }
  await expect(validateSyncedObject(project, 'evidence:linked-evidence', evidence)).rejects.toMatchObject({
    code: 'missing',
  })
  const sourceId = `revision:${source.id}`
  const sourceObject = await validateSyncedObject(project, sourceId, {
    format,
    kind: 'revision',
    branch_id: project.branch.id,
    revision: source,
  })
  await expect(applySyncedObject(localId, sourceId, sourceObject, 'null')).resolves.toBe(true)
  await expect(applySyncedObject(localId, sourceId, sourceObject, 'null')).resolves.toBe(true)
  const verified = await validateSyncedObject(project, 'evidence:linked-evidence', evidence)
  await expect(applySyncedObject(localId, 'evidence:linked-evidence', verified, 'null')).resolves.toBe(true)
  const pack = await exportLocalManuscript(localId)
  expect(pack.source_revisions).toEqual([source])
  expect(pack.revisions).toEqual([base, authorRevision])
  expect(pack.evidence).toEqual([evidence.document])
  vi.stubGlobal('indexedDB', new IDBFactory())
  await importLocalManuscript(bundle(pack))
  expect(await exportLocalManuscript(localId)).toEqual(pack)
})
