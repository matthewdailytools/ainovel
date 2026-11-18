import { beforeEach, expect, test, vi } from 'vitest'
import { IDBFactory, IDBObjectStore } from 'fake-indexeddb'
import {
  createLocalChapter,
  createLocalProject,
  applySyncedObject,
  exportLocalManuscript,
  importLocalManuscript,
  listLocalProjects,
  manuscriptHash,
  readLocalHistory,
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
    const opening = indexedDB.open('ainovel-offline-workspace', 3)
    opening.onupgradeneeded = () => opening.result.createObjectStore('v2').add('保留新版数据', 'data')
    opening.onsuccess = () => {
      opening.result.close()
      resolve()
    }
    opening.onerror = () => reject(opening.error)
  })
  await expect(listLocalProjects()).rejects.toMatchObject({ code: 'database' })
  const record = await new Promise((resolve, reject) => {
    const opening = indexedDB.open('ainovel-offline-workspace', 3)
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
