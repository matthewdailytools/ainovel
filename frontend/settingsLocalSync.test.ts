import { beforeEach, expect, test, vi } from 'vitest'
import { IDBFactory, IDBObjectStore } from 'fake-indexeddb'
import {
  applySyncedObject,
  confirmLocalProjectBrief,
  confirmLocalSettings,
  createLocalChapter,
  createLocalProject,
  exportLocalManuscript,
  importLocalManuscript,
  manuscriptHash,
  readLocalHistory,
  readLocalPublications,
  readLocalSettings,
  saveLocalChapter,
  syncObjectSignature,
  validateSyncedObject,
} from './offlineStore'
import { settingCategories, type SettingsVersion } from './settingsSyncModel'
import { syncWebManuscript } from './manuscriptSync'
import { setSyncEnabled, type SyncPayload, type SyncReceipt, type SyncTransport } from './syncClient'

beforeEach(() => vi.stubGlobal('indexedDB', new IDBFactory()))

const format = 'ainovel-manuscript-sync/1'
const brief = {
  position: '追查失落铜铃',
  track: '线索悬疑',
  genre_ids: ['xianxia'],
  audience: '偏好推理的读者',
  distinction: '线索在首章出现',
  length: '三卷十万字',
  cadence: '',
  ending: '找到铜铃原主',
  final_image: '',
}

async function prepare() {
  const project = await createLocalProject('铜铃设定')
  const work = project.novel.id
  const branch_id = project.branch.id
  const convention = await confirmLocalProjectBrief(work, null, { idea: '追查铜铃', brief })
  const chapter = await createLocalChapter(work, '寻找旧铃')
  const [initial] = await readLocalHistory(work, chapter.id)
  const oralHistory = '铃内刻有𠮷字。'
  const edit = await saveLocalChapter(work, chapter.id, initial.id, `卷首提示：${oralHistory}`)
  const item = { id: 'bell-rule', title: '旧铃刻痕', detail: '铜铃里刻有名字。', links: [] }
  const version: SettingsVersion = {
    id: 'remote-settings-a',
    parent_id: null,
    project_version_id: convention.id,
    categories: settingCategories.map(([id, title]) => ({
      id,
      title,
      items: id === 'world' ? [item] : [],
      skipped: false,
    })),
    origins: {
      [item.id]: {
        origin: 'author',
        edited: false,
        artifact_id: null,
        device_id: null,
        project_version_id: convention.id,
        evidence: {
          chapter_id: chapter.id,
          revision_id: edit.id,
          start: 5,
          end: 5 + oralHistory.length,
          quote_sha256: await manuscriptHash(oralHistory),
        },
      },
    },
    note: '确认铜铃刻痕来源。',
    reconciled_project_items: [],
    reviewed_setting_findings: [],
  }
  const source = { device_id: 'desktop', change_id: 'settings-source-a', sequence: 4 }

  async function store(candidate: SettingsVersion) {
    const id = `settings-version:${candidate.id}`
    const object = await validateSyncedObject(project, id, {
      format,
      kind: 'settings-version',
      branch_id,
      version: candidate,
    })
    return applySyncedObject(work, id, object, 'null', { source })
  }

  async function adopt(candidate: SettingsVersion, previous?: SettingsVersion) {
    const id = `settings:${branch_id}`
    const value = { format, kind: 'settings-head', branch_id, revision_id: candidate.id }
    const object = await validateSyncedObject(project, id, value)
    return applySyncedObject(
      work,
      id,
      object,
      previous
        ? syncObjectSignature({ ...value, format, kind: 'settings-head', revision_id: previous.id })
        : 'null',
    )
  }

  return { work, project, branch_id, version, convention, item, store, adopt }
}

test('M3 source prerequisites are checked per stable parent, M1 ID, and exact UTF-16 evidence SHA', async () => {
  const { version, convention, item, work, store, adopt } = await prepare()
  const original = version.origins[item.id]
  await expect(store({ ...version, parent_id: 'missing-settings-parent' })).rejects.toThrow(
    '缺少设定的父版本',
  )
  await expect(
    store({
      ...version,
      project_version_id: 'missing-project',
      origins: {
        [item.id]: { ...original, project_version_id: 'missing-project' },
      },
    }),
  ).rejects.toThrow('缺少设定引用的立项版本')
  await expect(
    store({
      ...version,
      origins: {
        [item.id]: { ...original, evidence: { ...original.evidence!, revision_id: 'missing-source' } },
      },
    }),
  ).rejects.toThrow('缺少设定引用的准确正文修订')
  await expect(
    store({
      ...version,
      origins: {
        [item.id]: { ...original, evidence: { ...original.evidence!, quote_sha256: '0'.repeat(64) } },
      },
    }),
  ).rejects.toThrow('设定引用与源正文不符')
  expect(await readLocalSettings(work)).toMatchObject({
    current: undefined,
    versions: [],
    projectVersionId: convention.id,
  })

  await expect(store(version)).resolves.toBe(true)
  await expect(adopt(version)).resolves.toBe(true)
  expect((await readLocalSettings(work)).current).toMatchObject({ version, source: { device_id: 'desktop' } })
  const other = { ...version, id: 'remote-settings-b', parent_id: version.id, note: '另一版设定' }
  await store(other)
  await confirmLocalProjectBrief(work, convention.id, { idea: '改为寻找铃匠', brief })
  await expect(adopt(other, version)).rejects.toThrow('本机采用的立项与设定依据不同')
  expect((await readLocalSettings(work)).current?.version.id).toBe(version.id)
})

test('M3 version and adopted head roll back if durable marking fails, then queue atomically on retry', async () => {
  const { work, branch_id, version, store, adopt } = await prepare()
  await store(version)
  await adopt(version)
  const before = await readLocalPublications(branch_id)
  const next = { ...version, id: 'web-settings-new', parent_id: version.id, note: '保留证据新增作者备注。' }
  const put = IDBObjectStore.prototype.put
  const fail = vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (
    this: IDBObjectStore,
    value,
    key,
  ) {
    if (this.name === 'publications') throw new DOMException('Storage full', 'QuotaExceededError')
    return put.call(this, value, key)
  })
  try {
    await expect(confirmLocalSettings(work, version.id, next)).rejects.toMatchObject({ code: 'quota' })
    expect((await readLocalSettings(work)).versions.map(({ version }) => version.id)).toEqual([version.id])
    expect(await readLocalPublications(branch_id)).toEqual(before)
  } finally {
    fail.mockRestore()
  }
  await confirmLocalSettings(work, version.id, next)
  expect((await readLocalSettings(work)).current?.version).toEqual(next)
  const queued = (await readLocalPublications(branch_id)).filter(({ objectId }) =>
    objectId.startsWith('settings'),
  )
  expect(queued.map(({ objectId }) => objectId).sort()).toEqual([
    `settings-version:${next.id}`,
    `settings:${branch_id}`,
  ])
  await expect(
    confirmLocalSettings(work, version.id, { ...next, id: 'stale-setting' }),
  ).rejects.toMatchObject({ code: 'conflict' })
})

test('M3 first confirmation uses an empty head CAS and a package preserves parent, competing version and source', async () => {
  const { work, version, store, branch_id } = await prepare()
  const candidate = { ...version, id: 'local-first' }
  await confirmLocalSettings(work, null, candidate)
  await expect(
    confirmLocalSettings(work, null, { ...version, id: 'stale-empty-head' }),
  ).rejects.toMatchObject({
    code: 'conflict',
  })
  const child = { ...candidate, id: 'local-m3-child', parent_id: candidate.id }
  await confirmLocalSettings(work, candidate.id, child)
  const side = { ...candidate, id: 'm3-side', parent_id: candidate.id }
  await store(side)
  const packageFile = await exportLocalManuscript(work)
  expect(packageFile.settings_history).toMatchObject({
    adopted_revision_id: child.id,
    source_devices: { [side.id]: 'desktop' },
  })
  vi.stubGlobal('indexedDB', new IDBFactory())
  const tampered = structuredClone(packageFile)
  const source = tampered.settings_history!.versions.find(({ id }) => id === child.id)!.origins['bell-rule']
  source.evidence!.quote_sha256 = '0'.repeat(64)
  await expect(importLocalManuscript(new Blob([JSON.stringify(tampered)]))).rejects.toThrow('源正文不符')
  await importLocalManuscript(new Blob([JSON.stringify(packageFile)]))
  const result = await readLocalSettings(work)
  expect(result.current).toMatchObject({ version: child, fromPackage: true })
  expect(result.versions.find(({ version }) => version.id === side.id)).toMatchObject({
    version: side,
    fromPackage: true,
    recordedDevice: 'desktop',
  })
  expect(await readLocalPublications(branch_id)).toEqual([])
  expect((await exportLocalManuscript(work)).settings_history).toEqual(packageFile.settings_history)
})

test('M3 replay waits for dependencies and uploads new project and manuscript sources before settings', async () => {
  const seeded = await prepare()
  const { project, branch_id, convention, version, work, item } = seeded
  const pack = await exportLocalManuscript(work)
  const reference = pack.revisions.at(-1)!
  const localSettingsId = `settings:${branch_id}`
  const log: SyncReceipt[] = []
  const heads = new Map<string, string>()
  const scope = { service: 'http://127.0.0.1:8888', deviceId: 'web', workId: branch_id }
  function publish(object_id: string, payload: SyncPayload, device_id = 'desktop', change?: string) {
    const sequence = log.length + 1
    const change_id = change ?? `tested-origin-${sequence}`
    const receipt: SyncReceipt = {
      change_id,
      object_id,
      payload,
      device_id,
      sequence,
      parent_revision: heads.get(object_id) ?? null,
      status: 'applied',
      tombstone: false,
      head_revision: change_id,
      conflict_id: null,
    }
    log.push(receipt)
    heads.set(object_id, change_id)
    return receipt
  }
  const outbound: string[] = []
  const transport: SyncTransport = {
    async push(request) {
      expect(request.parent_revision).toBe(heads.get(request.object_id) ?? null)
      outbound.push(request.object_id)
      return publish(request.object_id, request.payload!, scope.deviceId, request.change_id)
    },
    async pull(after) {
      return { changes: log.slice(after), cursor: log.length, has_more: false }
    },
    async receipt() {
      return null
    },
    async resolve() {
      throw new Error('当前用例无冲突')
    },
  }
  vi.stubGlobal('indexedDB', new IDBFactory())
  const cold = await importLocalManuscript(
    new Blob([
      JSON.stringify({
        format: pack.format,
        novel: pack.novel,
        branch: pack.branch,
        chapters: [],
        revisions: [],
      }),
    ]),
  )
  await setSyncEnabled(scope, true)
  publish(`branch:${branch_id}`, { format, kind: 'branch', novel: pack.novel, branch: pack.branch })
  const versionObject = { format, kind: 'settings-version', branch_id, version }
  publish(`settings-version:${version.id}`, versionObject)
  publish(localSettingsId, { format, kind: 'settings-head', branch_id, revision_id: version.id })
  const waiting = await syncWebManuscript(cold, scope, transport)
  expect(waiting.deferred.map(({ object_id }) => object_id)).toEqual([
    `settings-version:${version.id}`,
    localSettingsId,
  ])
  expect((await readLocalSettings(work)).current).toBeUndefined()

  publish(`project-version:${convention.id}`, {
    format,
    kind: 'project-version',
    branch_id,
    version: convention,
  })
  publish(`project:${branch_id}`, { format, kind: 'project-head', branch_id, revision_id: convention.id })
  for (const revision of pack.revisions)
    publish(`revision:${revision.id}`, { format, kind: 'revision', branch_id, revision })
  const chapter = pack.chapters[0]
  publish(`chapter:${chapter.id}`, { format, kind: 'chapter', chapter, head: reference })
  expect((await syncWebManuscript(cold, scope, transport)).deferred).toEqual([])
  expect((await readLocalSettings(work)).current?.version.id).toBe(version.id)

  const rebasedProject = await confirmLocalProjectBrief(work, convention.id, {
    idea: '追查另一枚铜铃',
    brief,
  })
  const recent = await saveLocalChapter(work, chapter.id, reference.id, '只有外壁有铭文。')
  const next: SettingsVersion = {
    ...version,
    id: 'web-settings-next',
    parent_id: version.id,
    project_version_id: rebasedProject.id,
    origins: {
      [item.id]: {
        ...version.origins[item.id],
        edited: true,
        project_version_id: rebasedProject.id,
        evidence: {
          chapter_id: chapter.id,
          revision_id: recent.id,
          start: 0,
          end: recent.content.length,
          quote_sha256: recent.sha256,
        },
      },
    },
  }
  await confirmLocalSettings(work, version.id, next)
  const finished = await syncWebManuscript(project, scope, transport)
  expect(finished.localPending).toBe(0)
  expect(outbound.indexOf(`revision:${recent.id}`)).toBeLessThan(
    outbound.indexOf(`settings-version:${next.id}`),
  )
  expect(outbound.indexOf(`project-version:${rebasedProject.id}`)).toBeLessThan(
    outbound.indexOf(`settings-version:${next.id}`),
  )
  expect(outbound.at(-1)).toBe(localSettingsId)
  expect(log.at(-1)?.payload?.revision_id).toBe(next.id)
})
