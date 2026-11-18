import {
  validateProjectDraft,
  validateProjectHistory,
  validateProjectSyncObject,
  type ProjectHeadObject,
  type ProjectHistory,
  type ProjectSource,
  type ProjectVersion,
  type ProjectVersionObject,
  type ProjectVersionRecord,
} from './projectSyncModel'
import {
  prepareSettingsVersion,
  validateSettingsHistory,
  validateSettingsSyncObject,
  validateSettingsVersionSources,
  type SettingsHeadObject,
  type SettingsHistory,
  type SettingsVersion,
  type SettingsVersionObject,
  type SettingsVersionRecord,
} from './settingsSyncModel'
import { createOutlineOfflineStore, outlineReadStores, outlineWriteStores } from './outlineOfflineStore'
import {
  outlineObjectSignature,
  validateOutlineHistory,
  validateOutlineSyncObject,
  type OutlineHeadObject,
  type OutlineHistory,
  type OutlineVersionObject,
} from './outlineSyncModel'

const DATABASE = 'ainovel-offline-workspace'
const FORMAT = 'ainovel-manuscript/1'
export const maxManuscriptBytes = 24 * 1024 * 1024

export type LocalNovel = { id: string; title: string; brief: string; created: number }
export type LocalBranch = {
  id: string
  novel_id: string
  name: string
  mode: string
  story_revision: number
  created: number
}
const evidenceKinds = ['project', 'settings', 'outline', 'inspection', 'completion'] as const
type EvidenceKind = (typeof evidenceKinds)[number]
export type LocalEvidence = {
  id: string
  kind: EvidenceKind
  title: string
  body: string
  sha256: string
  chapters: { id: string; revision: number; sha256: string; revision_id?: string }[]
}
export type LocalProject = { novel: LocalNovel; branch: LocalBranch; evidence?: LocalEvidence[] }
export type LocalChapter = {
  id: string
  branch_id: string
  position: number
  title: string
  revision: number
  archived: 0 | 1
  stale: 0 | 1
}
export type LocalRevision = {
  id: string
  chapter_id: string
  revision: number
  parent_id: string | null
  content: string
  sha256: string
  cause: string
  created: number
}
export type ManuscriptPack = LocalProject & {
  format: typeof FORMAT
  chapters: LocalChapter[]
  revisions: LocalRevision[]
  source_revisions?: LocalRevision[]
  project_history?: ProjectHistory
  settings_history?: SettingsHistory
  outline_history?: OutlineHistory
}
const SYNC_FORMAT = 'ainovel-manuscript-sync/1'
export type SyncedObject =
  | (LocalProject & { format: typeof SYNC_FORMAT; kind: 'branch' })
  | { format: typeof SYNC_FORMAT; kind: 'chapter'; chapter: LocalChapter; head: LocalRevision }
  | { format: typeof SYNC_FORMAT; kind: 'revision'; branch_id: string; revision: LocalRevision }
  | { format: typeof SYNC_FORMAT; kind: 'evidence'; document: LocalEvidence }
  | ProjectHeadObject
  | ProjectVersionObject
  | SettingsHeadObject
  | SettingsVersionObject
  | OutlineHeadObject
  | OutlineVersionObject

function revisionSignature(row: LocalRevision) {
  return [row.id, row.chapter_id, row.revision, row.parent_id, row.sha256, row.cause, row.created]
}

export function syncObjectSignature(object: SyncedObject | null) {
  if (object?.kind === 'branch') {
    const { novel, branch } = object
    return JSON.stringify(['branch', novel.id, novel.title, novel.brief, branch.id, branch.name, branch.mode])
  }
  if (object?.kind === 'chapter') {
    const { chapter, head } = object
    return JSON.stringify([
      'chapter',
      chapter.id,
      chapter.branch_id,
      chapter.position,
      chapter.title,
      head.sha256,
    ])
  }
  if (object?.kind === 'evidence') {
    const doc = object.document
    return JSON.stringify(['evidence', doc.id, doc.kind, doc.title, doc.sha256, doc.chapters])
  }
  if (object?.kind === 'revision') {
    return JSON.stringify(['revision', object.branch_id, ...revisionSignature(object.revision)])
  }
  if (object?.kind === 'project-head' || object?.kind === 'settings-head')
    return JSON.stringify([object.kind, object.branch_id, object.revision_id])
  if (object?.kind === 'project-version')
    return JSON.stringify([object.kind, object.branch_id, object.version])
  if (object?.kind === 'settings-version') {
    const { origins, ...data } = object.version
    const sources = Object.keys(origins)
      .sort()
      .map((id) => [id, origins[id]])
    return JSON.stringify([object.kind, object.branch_id, data, sources])
  }
  if (object?.kind === 'outline-head' || object?.kind === 'outline-version')
    return outlineObjectSignature(object)
  return 'null'
}

export function syncManuscriptObjects(pack: ManuscriptPack): Map<string, SyncedObject | null> {
  const objects = new Map<string, SyncedObject | null>([
    [
      `branch:${pack.branch.id}`,
      { format: SYNC_FORMAT, kind: 'branch', novel: pack.novel, branch: pack.branch },
    ],
  ])
  for (const revision of [...pack.revisions, ...(pack.source_revisions ?? [])].sort(
    (left, right) => left.revision - right.revision,
  )) {
    objects.set(`revision:${revision.id}`, {
      format: SYNC_FORMAT,
      kind: 'revision',
      branch_id: pack.branch.id,
      revision,
    })
  }
  const heads = new Map(pack.revisions.map((revision) => [revision.chapter_id, revision]))
  for (const chapter of pack.chapters) {
    const head = heads.get(chapter.id)
    if (!head || head.revision !== chapter.revision) invalid('本机稿件缺少当前修订。')
    objects.set(
      `chapter:${chapter.id}`,
      chapter.archived ? null : { format: SYNC_FORMAT, kind: 'chapter', chapter, head },
    )
  }
  for (const document of pack.evidence ?? []) {
    objects.set(`evidence:${document.id}`, { format: SYNC_FORMAT, kind: 'evidence', document })
  }
  return objects
}
type Store =
  | 'projects'
  | 'chapters'
  | 'revisions'
  | 'sourceRevisions'
  | 'projectHeads'
  | 'projectVersions'
  | 'publications'
  | 'settingsHeads'
  | 'settingsVersions'
  | 'outlineHeads'
  | 'outlineVersions'
  | 'outlinePromiseSeeds'
type LocalProjectVersion = ProjectVersionRecord & { branchId: string }
type LocalProjectHead = { branchId: string; versionId: string }
type LocalSettingsVersion = SettingsVersionRecord & { branchId: string }
export type LocalPublication = { branchId: string; objectId: string; id: string }
type ApplyOptions = { source?: ProjectSource; publicationId?: string | null; consumePublication?: boolean }
const projectStores = ['projects', 'projectHeads', 'projectVersions'] satisfies Store[]
const manuscriptStores = ['projects', 'chapters', 'revisions', 'sourceRevisions'] satisfies Store[]
const settingsDependencyStores = [
  ...manuscriptStores,
  'projectHeads',
  'projectVersions',
  'settingsHeads',
  'settingsVersions',
] satisfies Store[]

export class OfflineStoreError extends Error {
  constructor(
    readonly code: 'format' | 'ownership' | 'conflict' | 'quota' | 'database' | 'missing',
    message: string,
  ) {
    super(message)
  }
}

function result<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 6)
    let blocked = false
    request.onblocked = () => {
      blocked = true
      reject(new OfflineStoreError('database', '已有窗口占用浏览器存储，请关闭其他离线写作窗口后重试。'))
    }
    request.onupgradeneeded = (event) => {
      const db = request.result
      if (event.oldVersion < 1) {
        db.createObjectStore('projects', { keyPath: 'novel.id' }).createIndex('byBranch', 'branch.id', {
          unique: true,
        })
        db.createObjectStore('chapters', { keyPath: 'id' }).createIndex('byBranch', 'branch_id')
        const revisions = db.createObjectStore('revisions', { keyPath: 'id' })
        revisions.createIndex('byChapter', 'chapter_id')
        revisions.createIndex('byRevision', ['chapter_id', 'revision'], { unique: true })
      }
      if (event.oldVersion < 2) {
        db.createObjectStore('sourceRevisions', { keyPath: ['branchId', 'revision.id'] }).createIndex(
          'byBranch',
          'branchId',
        )
      }
      if (event.oldVersion < 3) {
        db.createObjectStore('projectHeads', { keyPath: 'branchId' })
        db.createObjectStore('projectVersions', { keyPath: ['branchId', 'version.id'] }).createIndex(
          'byBranch',
          'branchId',
        )
      }
      if (event.oldVersion < 4) {
        db.createObjectStore('publications', { keyPath: ['branchId', 'objectId'] }).createIndex(
          'byBranch',
          'branchId',
        )
      }
      if (event.oldVersion < 5) {
        db.createObjectStore('settingsHeads', { keyPath: 'branchId' })
        db.createObjectStore('settingsVersions', { keyPath: ['branchId', 'version.id'] }).createIndex(
          'byBranch',
          'branchId',
        )
      }
      db.createObjectStore('outlineHeads', { keyPath: 'branchId' })
      db.createObjectStore('outlineVersions', { keyPath: ['branchId', 'version.id'] }).createIndex(
        'byBranch',
        'branchId',
      )
      db.createObjectStore('outlinePromiseSeeds', { keyPath: ['branchId', 'id'] }).createIndex(
        'byBranch',
        'branchId',
      )
    }
    request.onsuccess = () => {
      const db = request.result
      db.onversionchange = () => db.close()
      if (blocked) db.close()
      else resolve(db)
    }
    request.onerror = () => reject(request.error)
  })
}

async function transact<T>(
  stores: Store[],
  mode: IDBTransactionMode,
  perform: (tx: IDBTransaction) => Promise<T>,
): Promise<T> {
  let db: IDBDatabase | undefined
  try {
    db = await openDatabase()
    const tx = db.transaction(stores, mode)
    const done = new Promise<DOMException | null>((resolve) => {
      tx.oncomplete = () => resolve(null)
      tx.onabort = () => resolve(tx.error ?? new DOMException('Transaction aborted', 'AbortError'))
    })
    let output: T
    try {
      output = await perform(tx)
    } catch (error) {
      try {
        tx.abort()
      } catch {
        // The failed request may already have aborted the transaction.
      }
      await done
      throw error
    }
    const failure = await done
    if (failure) throw failure
    return output
  } catch (error) {
    if (error instanceof OfflineStoreError) throw error
    if (error instanceof DOMException && error.name === 'QuotaExceededError')
      throw new OfflineStoreError('quota', '浏览器空间不足，保存失败。可暂留当前输入或下载草稿。')
    if (error instanceof DOMException && error.name === 'ConstraintError')
      throw new OfflineStoreError('conflict', '此记录的标识或修订已存在，本地数据保留。')
    throw new OfflineStoreError('database', '浏览器存储暂不可用或格式较新，已保留原存储。')
  } finally {
    db?.close()
  }
}

const newId = () => crypto.randomUUID().replaceAll('-', '')
const now = () => Date.now() / 1000

export async function manuscriptHash(text: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

function invalid(message: string): never {
  throw new OfflineStoreError('format', message)
}

function row(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) invalid('稿件交换包包含无效数据。')
  return value as Record<string, unknown>
}

function fields(value: unknown, allowed: string[]): Record<string, unknown> {
  const item = row(value)
  if (Object.keys(item).some((key) => !allowed.includes(key)))
    invalid('稿件交换包包含当前版本无法识别的字段。')
  return item
}

function content(value: unknown, max: number, name: string, required = false): string {
  if (
    typeof value !== 'string' ||
    value.length > max * 2 ||
    Array.from(value).length > max ||
    (required && !value.trim()) ||
    /\p{Surrogate}/u.test(value)
  )
    invalid(`${name}缺失、过长或包含无效字符。`)
  return value
}

function identity(value: unknown): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(value)) invalid('稿件交换包包含无效标识。')
  return value
}

function number(value: unknown, minimum = 0, integer = true): number {
  if (
    typeof value !== 'number' ||
    !Number.isFinite(value) ||
    value < minimum ||
    (integer && !Number.isSafeInteger(value))
  )
    invalid('稿件交换包包含无效序号或时间。')
  return value
}

function flag(value: unknown): 0 | 1 {
  if (value !== 0 && value !== 1) invalid('稿件交换包包含无效状态。')
  return value
}

function workingMode(value: unknown) {
  if (value !== 'advisory' && value !== 'assisted' && value !== 'automatic')
    invalid('稿件交换包包含无效工作模式。')
  return value
}

async function readProject(tx: IDBTransaction, projectId: string): Promise<LocalProject> {
  const project = await result<LocalProject | undefined>(tx.objectStore('projects').get(projectId))
  if (!project) throw new OfflineStoreError('missing', '离线作品已不存在，请重新打开。')
  return project
}

async function readChapter(
  tx: IDBTransaction,
  project: LocalProject,
  chapterId: string,
): Promise<LocalChapter> {
  const chapter = await result<LocalChapter | undefined>(tx.objectStore('chapters').get(chapterId))
  if (!chapter || chapter.branch_id !== project.branch.id || chapter.archived)
    throw new OfflineStoreError('ownership', '章节不属于当前作品，或已归档。')
  return chapter
}

async function listChaptersTx(tx: IDBTransaction, branchId: string): Promise<LocalChapter[]> {
  const chapters = await result<LocalChapter[]>(tx.objectStore('chapters').index('byBranch').getAll(branchId))
  return chapters.sort((left, right) => left.position - right.position)
}

async function historyTx(tx: IDBTransaction, chapterId: string): Promise<LocalRevision[]> {
  const revisions = await result<LocalRevision[]>(
    tx.objectStore('revisions').index('byChapter').getAll(chapterId),
  )
  return revisions.sort((left, right) => left.revision - right.revision)
}

export function listLocalProjects(): Promise<LocalProject[]> {
  return transact(['projects'], 'readonly', (tx) => result(tx.objectStore('projects').getAll()))
}

export async function createLocalProject(title: string, brief = ''): Promise<LocalProject> {
  const novel = {
    id: newId(),
    title: content(title.trim(), 200, '作品名', true),
    brief: content(brief, 30_000, '创作思路'),
    created: now(),
  }
  const project = {
    novel,
    branch: {
      id: newId(),
      novel_id: novel.id,
      name: '作者工作稿',
      mode: 'advisory',
      story_revision: 0,
      created: now(),
    },
  }
  await transact(['projects'], 'readwrite', (tx) => result(tx.objectStore('projects').add(project)))
  return project
}

export function listLocalChapters(projectId: string): Promise<LocalChapter[]> {
  return transact(['projects', 'chapters'], 'readonly', async (tx) =>
    (await listChaptersTx(tx, (await readProject(tx, projectId)).branch.id)).filter(
      (chapter) => !chapter.archived,
    ),
  )
}

export async function createLocalChapter(projectId: string, title: string): Promise<LocalChapter> {
  const chapterTitle = content(title.trim(), 200, '章节名', true)
  const emptyHash = await manuscriptHash('')
  return transact(['projects', 'chapters', 'revisions', 'publications'], 'readwrite', async (tx) => {
    const project = await readProject(tx, projectId)
    const chapters = await listChaptersTx(tx, project.branch.id)
    const chapter: LocalChapter = {
      id: newId(),
      branch_id: project.branch.id,
      position: (chapters.at(-1)?.position ?? 0) + 1,
      title: chapterTitle,
      revision: 0,
      archived: 0,
      stale: 0,
    }
    const revision: LocalRevision = {
      id: newId(),
      chapter_id: chapter.id,
      revision: 0,
      parent_id: null,
      content: '',
      sha256: emptyHash,
      cause: '新建空稿',
      created: now(),
    }
    project.branch.story_revision += 1
    await result(tx.objectStore('chapters').add(chapter))
    await result(tx.objectStore('revisions').add(revision))
    await result(tx.objectStore('projects').put(project))
    await markPublication(tx, chapter.branch_id, `revision:${revision.id}`, `chapter:${chapter.id}`)
    return chapter
  })
}

export function readLocalHistory(projectId: string, chapterId: string): Promise<LocalRevision[]> {
  return transact(['projects', 'chapters', 'revisions'], 'readonly', async (tx) => {
    await readChapter(tx, await readProject(tx, projectId), chapterId)
    return historyTx(tx, chapterId)
  })
}

export async function saveLocalChapter(
  projectId: string,
  chapterId: string,
  baseId: string,
  text: string,
  restoreId?: string,
): Promise<LocalRevision> {
  if (restoreId) {
    const previous = (await readLocalHistory(projectId, chapterId)).find((item) => item.id === restoreId)
    if (!previous) throw new OfflineStoreError('missing', '历史稿件已不存在。')
    text = previous.content
  }
  const body = content(text, 250_000, '正文')
  const sha256 = await manuscriptHash(body)
  return transact(['projects', 'chapters', 'revisions', 'publications'], 'readwrite', async (tx) => {
    const project = await readProject(tx, projectId)
    const chapter = await readChapter(tx, project, chapterId)
    const versions = tx.objectStore('revisions')
    const head = await result<LocalRevision | undefined>(
      versions.index('byRevision').get([chapterId, chapter.revision]),
    )
    if (!head || head.id !== baseId)
      throw new OfflineStoreError('conflict', '本章已有新修订。当前输入保留，请查看已保存的新稿后另行处理。')
    if (head.content === body && !restoreId) return head
    const revision = {
      id: newId(),
      chapter_id: chapterId,
      revision: head.revision + 1,
      parent_id: head.id,
      content: body,
      sha256,
      cause: restoreId ? '恢复历史为新修订' : '作者编辑',
      created: now(),
    }
    chapter.revision = revision.revision
    chapter.stale = 0
    project.branch.story_revision += 1
    await result(versions.add(revision))
    await result(tx.objectStore('chapters').put(chapter))
    await result(tx.objectStore('projects').put(project))
    await markPublication(tx, chapter.branch_id, `revision:${revision.id}`, `chapter:${chapter.id}`)
    return revision
  })
}

async function readManuscript(tx: IDBTransaction, project: LocalProject): Promise<ManuscriptPack> {
  const chapters = await listChaptersTx(tx, project.branch.id)
  const revisions = (await Promise.all(chapters.map((chapter) => historyTx(tx, chapter.id)))).flat()
  const sources = await result<{ revision: LocalRevision }[]>(
    tx.objectStore('sourceRevisions').index('byBranch').getAll(project.branch.id),
  )
  return {
    format: FORMAT,
    ...project,
    chapters,
    revisions,
    ...(sources.length ? { source_revisions: sources.map(({ revision }) => revision) } : {}),
  }
}

export function exportLocalManuscript(projectId: string): Promise<ManuscriptPack> {
  const stores = [...new Set([...settingsDependencyStores, ...outlineReadStores])]
  return transact(stores, 'readonly', async (tx) => {
    const project = await readProject(tx, projectId)
    const [manuscript, brief, settings, outlineHistory] = await Promise.all([
      readManuscript(tx, project),
      readProjectBrief(tx, project.branch.id),
      readSettings(tx, project.branch.id),
      outlineStore.readOutlineHistoryTx(tx, project),
    ])
    const sourceDevices = settings.versions.flatMap(({ version, source, recordedDevice }) => {
      const device = source?.device_id ?? recordedDevice
      return device ? [[version.id, device] as const] : []
    })
    return {
      ...manuscript,
      ...(brief.versions.length
        ? {
            project_history: {
              adopted_revision_id: brief.current?.version.id ?? null,
              versions: brief.versions.map(({ version }) => version),
            },
          }
        : {}),
      ...(settings.versions.length
        ? {
            settings_history: {
              adopted_revision_id: settings.current?.version.id ?? null,
              versions: settings.versions.map(({ version }) => version),
              ...(sourceDevices.length ? { source_devices: Object.fromEntries(sourceDevices) } : {}),
            },
          }
        : {}),
      ...(outlineHistory ? { outline_history: outlineHistory } : {}),
    }
  })
}

async function sourceRevision(tx: IDBTransaction, branchId: string, revisionId: string) {
  const source = await result<{ revision: LocalRevision } | undefined>(
    tx.objectStore('sourceRevisions').get([branchId, revisionId]),
  )
  if (source) return source.revision
  const local = await result<LocalRevision | undefined>(tx.objectStore('revisions').get(revisionId))
  if (!local) return undefined
  const chapter = await result<LocalChapter | undefined>(tx.objectStore('chapters').get(local.chapter_id))
  return chapter?.branch_id === branchId ? local : undefined
}

export function readLocalSourceRevision(projectId: string, revisionId: string) {
  return transact(['projects', 'chapters', 'revisions', 'sourceRevisions'], 'readonly', async (tx) => {
    const project = await readProject(tx, projectId)
    return sourceRevision(tx, project.branch.id, revisionId)
  })
}

function getProjectHead(tx: IDBTransaction, branchId: string) {
  return result<LocalProjectHead | undefined>(tx.objectStore('projectHeads').get(branchId))
}

function getProjectVersion(tx: IDBTransaction, branchId: string, versionId: string) {
  return result<LocalProjectVersion | undefined>(tx.objectStore('projectVersions').get([branchId, versionId]))
}

async function putProjectHead(tx: IDBTransaction, project: LocalProject, versionId: string) {
  await result(tx.objectStore('projectHeads').put({ branchId: project.branch.id, versionId }))
  project.branch.story_revision++
  await result(tx.objectStore('projects').put(project))
}

async function readProjectBrief(tx: IDBTransaction, branchId: string) {
  const versions = await result<LocalProjectVersion[]>(
    tx.objectStore('projectVersions').index('byBranch').getAll(branchId),
  )
  const head = await getProjectHead(tx, branchId)
  return { versions, current: versions.find(({ version }) => version.id === head?.versionId) }
}

export function readLocalProjectBrief(projectId: string) {
  return transact(projectStores, 'readonly', async (tx) =>
    readProjectBrief(tx, (await readProject(tx, projectId)).branch.id),
  )
}

function publicationRows(tx: IDBTransaction, branchId: string) {
  return result<LocalPublication[]>(tx.objectStore('publications').index('byBranch').getAll(branchId))
}

function readPublication(tx: IDBTransaction, branchId: string, objectId: string) {
  return result<LocalPublication | undefined>(tx.objectStore('publications').get([branchId, objectId]))
}

async function markPublication(tx: IDBTransaction, branchId: string, ...objects: string[]) {
  await Promise.all(
    objects.map((objectId) =>
      result(
        tx.objectStore('publications').put({ branchId, objectId, id: newId() } satisfies LocalPublication),
      ),
    ),
  )
}

export function readLocalPublications(branchId: string) {
  return transact(['publications'], 'readonly', (tx) => publicationRows(tx, branchId))
}

export function acknowledgeLocalPublications(records: LocalPublication[]) {
  return transact(['publications'], 'readwrite', async (tx) => {
    for (const { branchId, objectId, id } of records) {
      if ((await readPublication(tx, branchId, objectId))?.id === id)
        await result(tx.objectStore('publications').delete([branchId, objectId]))
    }
  })
}

export function readLocalSyncSnapshot(projectId: string) {
  const stores = [...new Set([...settingsDependencyStores, ...outlineReadStores, 'publications' as const])]
  return transact(stores, 'readonly', async (tx) => {
    const project = await readProject(tx, projectId)
    const branchId = project.branch.id
    const [manuscript, brief, settings, outline, publications] = await Promise.all([
      readManuscript(tx, project),
      readProjectBrief(tx, branchId),
      readSettings(tx, branchId),
      outlineStore.readOutlineTx(tx, project),
      publicationRows(tx, branchId),
    ])
    return { manuscript, brief, settings, outline, publications }
  })
}

export function confirmLocalProjectBrief(
  projectId: string,
  base: string | null,
  draft: Pick<ProjectVersion, 'idea' | 'brief'>,
) {
  const version = { id: newId(), parent_id: base, ...validateProjectDraft(draft.idea, draft.brief) }
  return transact([...projectStores, 'publications'], 'readwrite', async (tx) => {
    const project = await readProject(tx, projectId)
    const branchId = project.branch.id
    const current = await getProjectHead(tx, branchId)
    if ((current?.versionId ?? null) !== base)
      throw new OfflineStoreError('conflict', '立项在其他窗口已有新确认，请核对后再提交。')
    await result(tx.objectStore('projectVersions').add({ branchId, version } satisfies LocalProjectVersion))
    await putProjectHead(tx, project, version.id)
    await markPublication(tx, branchId, `project-version:${version.id}`, `project:${branchId}`)
    return version
  })
}

async function applyProjectObject(
  projectId: string,
  object: ProjectHeadObject | ProjectVersionObject,
  expected: string,
  options: ApplyOptions,
) {
  return transact([...projectStores, 'publications'], 'readwrite', async (tx) => {
    const project = await readProject(tx, projectId)
    const branchId = project.branch.id
    if (object.branch_id !== branchId) invalid('立项范围与工作版本不符。')
    if (object.kind === 'project-version') {
      const { source } = options
      const version = object.version
      const existing = await getProjectVersion(tx, branchId, version.id)
      if (
        existing &&
        syncObjectSignature({ ...object, version: existing.version }) !== syncObjectSignature(object)
      )
        invalid('同一立项的源修订保存了不同内容。')
      if (version.parent_id && !(await getProjectVersion(tx, branchId, version.parent_id)))
        throw new OfflineStoreError('missing', '缺少立项父版本，待云端历史到齐。')
      if ((!existing || !existing.source) && source) {
        await result(
          tx.objectStore('projectVersions').put({ branchId, version, source } satisfies LocalProjectVersion),
        )
      }
      return !!existing || !!source
    }
    const local = await getProjectHead(tx, branchId)
    const before = local ? { ...object, revision_id: local.versionId } : null
    const publication = await readPublication(tx, branchId, `project:${branchId}`)
    if (
      syncObjectSignature(before) !== expected ||
      (options.publicationId !== undefined && options.publicationId !== (publication?.id ?? null))
    )
      return false
    const version = await getProjectVersion(tx, branchId, object.revision_id)
    if (!version?.source) throw new OfflineStoreError('missing', '缺少采用立项的云端来源记录。')
    if (local?.versionId !== object.revision_id) await putProjectHead(tx, project, object.revision_id)
    if (options.consumePublication && publication)
      await result(tx.objectStore('publications').delete([branchId, publication.objectId]))
    return true
  })
}

function getSettingsHead(tx: IDBTransaction, branchId: string) {
  return result<LocalProjectHead | undefined>(tx.objectStore('settingsHeads').get(branchId))
}

function getSettingsVersion(tx: IDBTransaction, branchId: string, id: string) {
  return result<LocalSettingsVersion | undefined>(tx.objectStore('settingsVersions').get([branchId, id]))
}

async function readSettings(tx: IDBTransaction, branchId: string) {
  const [head, project, versions] = await Promise.all([
    getSettingsHead(tx, branchId),
    getProjectHead(tx, branchId),
    result<LocalSettingsVersion[]>(tx.objectStore('settingsVersions').index('byBranch').getAll(branchId)),
  ])
  return {
    current: versions.find(({ version }) => version.id === head?.versionId),
    versions,
    projectVersionId: project?.versionId ?? null,
  }
}

export function readLocalSettings(projectId: string) {
  return transact(['projects', 'settingsHeads', 'settingsVersions', 'projectHeads'], 'readonly', async (tx) =>
    readSettings(tx, (await readProject(tx, projectId)).branch.id),
  )
}

// Keep WebCrypto outside of IndexedDB transactions. The final write compares the immutable
// source IDs and hashes again, together with the adopted project and settings preconditions.
async function checkSettingsSources(projectId: string, version: SettingsVersion) {
  const checked = await transact(settingsDependencyStores, 'readonly', async (tx) => {
    const branchId = (await readProject(tx, projectId)).branch.id
    const origins = Object.values(version.origins)
    const projectIds = new Set([
      version.project_version_id,
      ...origins.map(({ project_version_id }) => project_version_id),
    ])
    const revisionIds = new Set(origins.flatMap(({ evidence }) => (evidence ? [evidence.revision_id] : [])))
    const projects = new Set<string>()
    const revisions = new Map<string, LocalRevision>()
    for (const id of projectIds) {
      if (id && (await getProjectVersion(tx, branchId, id))) projects.add(id)
    }
    for (const id of revisionIds) {
      const evidence = await sourceRevision(tx, branchId, id)
      if (evidence) revisions.set(id, evidence)
    }
    const parent = version.parent_id ? await getSettingsVersion(tx, branchId, version.parent_id) : undefined
    return { branchId, projects, revisions, parent }
  })
  await validateSettingsVersionSources(version, {
    project: (id) => checked.projects.has(id),
    settings: (id) => checked.parent?.version.id === id,
    revision: (id) => checked.revisions.get(id),
  })
  return checked
}

async function recheckSettingsSources(
  tx: IDBTransaction,
  branchId: string,
  checked: Awaited<ReturnType<typeof checkSettingsSources>>,
) {
  if (checked.branchId !== branchId) throw new OfflineStoreError('ownership', '设定来源不属于当前工作版本。')
  if (checked.parent && !(await getSettingsVersion(tx, branchId, checked.parent.version.id)))
    throw new OfflineStoreError('missing', '设定父版本不再可用，请重新同步。')
  for (const id of checked.projects) {
    if (!(await getProjectVersion(tx, branchId, id)))
      throw new OfflineStoreError('missing', '设定引用的立项版本不再可用，请重新同步。')
  }
  for (const original of checked.revisions.values()) {
    const found = await sourceRevision(tx, branchId, original.id)
    if (found?.sha256 !== original.sha256 || found.chapter_id !== original.chapter_id)
      throw new OfflineStoreError('missing', '设定引用的原稿已无法核验，请重新同步。')
  }
}

async function putSettingsHead(tx: IDBTransaction, project: LocalProject, versionId: string) {
  await result(
    tx
      .objectStore('settingsHeads')
      .put({ branchId: project.branch.id, versionId } satisfies LocalProjectHead),
  )
  project.branch.story_revision++
  await result(tx.objectStore('projects').put(project))
}

function assertSettingsProject(version: SettingsVersion, projectVersionId: string | null) {
  if (version.project_version_id !== projectVersionId)
    throw new OfflineStoreError('conflict', '本机采用的立项与设定依据不同，待核对并采用对应立项。')
}

export async function confirmLocalSettings(projectId: string, base: string | null, draft: SettingsVersion) {
  const version = await prepareSettingsVersion(draft)
  if (version.parent_id !== base) throw new OfflineStoreError('conflict', '设定父版本和当前确认依据不一致。')
  const sources = await checkSettingsSources(projectId, version)
  return transact([...settingsDependencyStores, 'publications'], 'readwrite', async (tx) => {
    const project = await readProject(tx, projectId)
    const branchId = project.branch.id
    const current = await getSettingsHead(tx, branchId)
    if ((current?.versionId ?? null) !== base)
      throw new OfflineStoreError('conflict', '本机已有另一版确认设定，请重新核对。')
    assertSettingsProject(version, (await getProjectHead(tx, branchId))?.versionId ?? null)
    await recheckSettingsSources(tx, branchId, sources)
    await result(tx.objectStore('settingsVersions').add({ branchId, version } satisfies LocalSettingsVersion))
    await putSettingsHead(tx, project, version.id)
    await markPublication(tx, branchId, `settings-version:${version.id}`, `settings:${branchId}`)
  })
}

async function applySettingsObject(
  projectId: string,
  object: SettingsHeadObject | SettingsVersionObject,
  expected: string,
  options: ApplyOptions,
) {
  const sources =
    object.kind === 'settings-version' ? await checkSettingsSources(projectId, object.version) : null
  return transact([...settingsDependencyStores, 'publications'], 'readwrite', async (tx) => {
    const project = await readProject(tx, projectId)
    const branchId = project.branch.id
    if (object.branch_id !== branchId) invalid('设定范围与工作版本不符。')
    if (object.kind === 'settings-version' && sources) {
      const { version } = object
      const existing = await getSettingsVersion(tx, branchId, version.id)
      if (
        existing &&
        syncObjectSignature({ ...object, version: existing.version }) !== syncObjectSignature(object)
      )
        invalid('同一设定的源修订保存了不同内容。')
      await recheckSettingsSources(tx, branchId, sources)
      const { source } = options
      if (source && (!existing?.source || source.sequence < existing.source.sequence)) {
        await result(
          tx
            .objectStore('settingsVersions')
            .put({ branchId, version, source } satisfies LocalSettingsVersion),
        )
      }
      return !!existing || !!source
    }
    if (object.kind !== 'settings-head') return false
    const local = await getSettingsHead(tx, branchId)
    const before = local ? { ...object, revision_id: local.versionId } : null
    const publication = await readPublication(tx, branchId, `settings:${branchId}`)
    if (
      syncObjectSignature(before) !== expected ||
      (options.publicationId !== undefined && options.publicationId !== (publication?.id ?? null))
    )
      return false
    const chosen = await getSettingsVersion(tx, branchId, object.revision_id)
    if (!chosen?.source) throw new OfflineStoreError('missing', '缺少采用设定的云端来源记录。')
    assertSettingsProject(chosen.version, (await getProjectHead(tx, branchId))?.versionId ?? null)
    if (local?.versionId !== object.revision_id) await putSettingsHead(tx, project, object.revision_id)
    if (options.consumePublication && publication)
      await result(tx.objectStore('publications').delete([branchId, publication.objectId]))
    return true
  })
}

function sourceLink(revision: LocalRevision, parent: LocalRevision | undefined) {
  return revision.revision === 0
    ? revision.parent_id === null
    : !!parent && parent.chapter_id === revision.chapter_id && parent.revision + 1 === revision.revision
}

const outlineStore = createOutlineOfflineStore({
  transact,
  result,
  readProject,
  listChaptersTx,
  getProjectHead,
  getProjectVersion,
  getSettingsHead,
  getSettingsVersion,
  readPublication,
  markPublication,
  fail: (code, message) => {
    throw new OfflineStoreError(code, message)
  },
})
export const { readLocalOutline, confirmLocalOutline } = outlineStore

function evidenceMatches(source: LocalEvidence['chapters'][number], target: LocalRevision | undefined) {
  return (
    target?.chapter_id === source.id && target.revision === source.revision && target.sha256 === source.sha256
  )
}

export async function validateSyncedObject(
  project: LocalProject,
  objectId: string,
  value: unknown,
): Promise<SyncedObject | null> {
  if (objectId.startsWith('project:') || objectId.startsWith('project-version:'))
    return validateProjectSyncObject(project.branch.id, objectId, value)
  if (objectId.startsWith('settings:') || objectId.startsWith('settings-version:'))
    return validateSettingsSyncObject(project.branch.id, objectId, value)
  if (objectId.startsWith('outline:') || objectId.startsWith('outline-version:'))
    return validateOutlineSyncObject(project.branch.id, objectId, value)
  if (value === null && objectId.startsWith('chapter:')) {
    identity(objectId.slice(8))
    return null
  }
  const object = row(value)
  if (object.format !== SYNC_FORMAT) invalid('云端对象格式不支持当前稿件。')
  if (object.kind === 'revision') {
    fields(value, ['format', 'kind', 'branch_id', 'revision'])
    const revision = await validateRevision(object.revision)
    if (object.branch_id !== project.branch.id || objectId !== `revision:${revision.id}`)
      invalid('来源修订与当前工作版本不符。')
    return { format: SYNC_FORMAT, kind: 'revision', branch_id: project.branch.id, revision }
  }
  if (object.kind === 'evidence') {
    const pack = await transact(manuscriptStores, 'readonly', (tx) => readManuscript(tx, project))
    const histories = new Map<string, LocalRevision[]>()
    for (const revision of pack.revisions) {
      if (!histories.has(revision.chapter_id)) histories.set(revision.chapter_id, [])
      histories.get(revision.chapter_id)!.push(revision)
    }
    const byId = new Map([...pack.revisions, ...(pack.source_revisions ?? [])].map((item) => [item.id, item]))
    const document = await validateEvidence(object.document, histories, byId)
    if (objectId !== `evidence:${document.id}`) invalid('云端依据编号无效。')
    return { format: SYNC_FORMAT, kind: 'evidence', document }
  }
  if (object.kind === 'branch') {
    const normalized = await validateManuscript({
      format: FORMAT,
      novel: object.novel,
      branch: object.branch,
      chapters: [],
      revisions: [],
    })
    if (
      objectId !== `branch:${project.branch.id}` ||
      normalized.branch.id !== project.branch.id ||
      normalized.novel.id !== project.novel.id
    )
      invalid('云端工作版本归属无效。')
    return { format: SYNC_FORMAT, kind: 'branch', novel: normalized.novel, branch: normalized.branch }
  }
  if (object.kind !== 'chapter') invalid('云端对象不属于可同步的稿件范围。')
  const chapter = row(object.chapter)
  const head = row(object.head)
  const revision = number(chapter.revision)
  if (
    objectId !== `chapter:${chapter.id}` ||
    chapter.archived !== 0 ||
    head.revision !== revision ||
    !(revision === 0 ? head.parent_id === null : typeof head.parent_id === 'string')
  )
    invalid('云端章稿修订无效。')
  const parent = head.parent_id === null ? null : identity(head.parent_id)
  const checked = await validateManuscript({
    format: FORMAT,
    novel: project.novel,
    branch: project.branch,
    chapters: [{ ...chapter, revision: 0 }],
    revisions: [{ ...head, revision: 0, parent_id: null }],
  })
  return {
    format: SYNC_FORMAT,
    kind: 'chapter',
    chapter: { ...checked.chapters[0], revision },
    head: { ...checked.revisions[0], revision, parent_id: parent },
  }
}

export function applySyncedObject(
  projectId: string,
  objectId: string,
  object: SyncedObject | null,
  expected: string,
  options: ApplyOptions = {},
) {
  if (object?.kind === 'project-head' || object?.kind === 'project-version')
    return applyProjectObject(projectId, object, expected, options)
  if (object?.kind === 'settings-head' || object?.kind === 'settings-version')
    return applySettingsObject(projectId, object, expected, options)
  if (object?.kind === 'outline-head' || object?.kind === 'outline-version')
    return outlineStore.applyOutlineObject(projectId, object, expected, options)
  return transact([...manuscriptStores, 'publications'], 'readwrite', async (tx) => {
    const project = await readProject(tx, projectId)
    const projectStore = tx.objectStore('projects')
    const branchId = project.branch.id
    if (object?.kind === 'revision') {
      const revision = object.revision
      const chapter = await result<LocalChapter | undefined>(
        tx.objectStore('chapters').get(revision.chapter_id),
      )
      const existing = await sourceRevision(tx, branchId, revision.id)
      if (
        object.branch_id !== branchId ||
        objectId !== `revision:${revision.id}` ||
        (chapter && chapter.branch_id !== branchId) ||
        (existing &&
          JSON.stringify(revisionSignature(existing)) !== JSON.stringify(revisionSignature(revision)))
      ) {
        invalid('相同来源修订的内容或归属不一致。')
      }
      if (existing) return true
      if (expected !== 'null') return false
      const parent = revision.parent_id ? await sourceRevision(tx, branchId, revision.parent_id) : undefined
      if (!sourceLink(revision, parent))
        throw new OfflineStoreError('missing', '缺少同一章的连续父修订，来源稿件将延后接收。')
      await result(tx.objectStore('sourceRevisions').add({ branchId, revision }))
      return true
    }
    if (object?.kind === 'evidence') {
      const before = project.evidence?.find(({ id }) => id === object.document.id)
      if (before || expected !== 'null') return false
      if ((project.evidence?.length ?? 0) >= 100) invalid('稿件的随稿依据数量已达上限。')
      for (const source of object.document.chapters) {
        const chapter = await result<LocalChapter | undefined>(tx.objectStore('chapters').get(source.id))
        const version = source.revision_id
          ? await sourceRevision(tx, branchId, source.revision_id)
          : chapter?.branch_id === branchId
            ? await result<LocalRevision | undefined>(
                tx.objectStore('revisions').index('byRevision').get([source.id, source.revision]),
              )
            : undefined
        if (!evidenceMatches(source, version)) invalid('需要准确的来源章稿后才能保存云端依据。')
      }
      project.evidence = [...(project.evidence ?? []), object.document]
      await result(projectStore.put(project))
      return true
    }
    if (object?.kind === 'branch') {
      if (syncObjectSignature({ format: SYNC_FORMAT, kind: 'branch', ...project }) !== expected) return false
      project.novel.title = object.novel.title
      project.novel.brief = object.novel.brief
      project.branch.name = object.branch.name
      project.branch.mode = object.branch.mode
      project.branch.story_revision++
      await result(projectStore.put(project))
      return true
    }
    const chapterId = objectId.slice(8)
    const chapters = tx.objectStore('chapters')
    const old = await result<LocalChapter | undefined>(chapters.get(chapterId))
    if (old && old.branch_id !== project.branch.id) invalid('同步章节属于其他本机工作版本。')
    const versions = tx.objectStore('revisions')
    const head = old
      ? await result<LocalRevision | undefined>(versions.index('byRevision').get([chapterId, old.revision]))
      : undefined
    if (old && !head) invalid('本机稿件缺少当前修订。')
    const local =
      old && !old.archived && head
        ? ({ format: SYNC_FORMAT, kind: 'chapter', chapter: old, head } as const)
        : null
    const publication = await readPublication(tx, branchId, objectId)
    if (
      syncObjectSignature(local) !== expected ||
      (options.publicationId !== undefined && options.publicationId !== (publication?.id ?? null))
    )
      return false
    if (!object) {
      if (old && !old.archived) await result(chapters.put({ ...old, archived: 1 }))
    } else {
      if (object.chapter.branch_id !== project.branch.id || object.chapter.id !== chapterId)
        invalid('云端章节归属无效。')
      if (
        (await listChaptersTx(tx, project.branch.id)).some(
          (chapter) =>
            !chapter.archived && chapter.id !== chapterId && chapter.position === object.chapter.position,
        )
      )
        invalid('云端章位与本机目录冲突。')
      let revision = head?.revision ?? 0
      const original = await sourceRevision(tx, branchId, object.head.id)
      const history: LocalRevision[] = []
      const sameSource = (left: LocalRevision, right: LocalRevision) =>
        JSON.stringify(revisionSignature(left)) === JSON.stringify(revisionSignature(right))
      if (original && sameSource(original, object.head)) {
        let cursor: LocalRevision | undefined = original
        while (cursor) {
          if (head && sameSource(cursor, head)) break
          if (await result(versions.getKey(cursor.id))) {
            history.length = 0
            break
          }
          history.push(cursor)
          cursor = cursor.parent_id ? await sourceRevision(tx, branchId, cursor.parent_id) : undefined
        }
        if (head && !cursor) history.length = 0
      }
      if (history.length && history.length === object.head.revision - (head?.revision ?? -1)) {
        for (const entry of history.reverse()) {
          await result(versions.add(entry))
          await result(tx.objectStore('sourceRevisions').delete([branchId, entry.id]))
        }
        revision = object.head.revision
      } else if (!head || head.sha256 !== object.head.sha256) {
        revision = head ? head.revision + 1 : 0
        await result(
          versions.add({
            ...object.head,
            id: newId(),
            revision,
            parent_id: head?.id ?? null,
            cause: `云同步，来源修订 ${object.head.id}`,
          }),
        )
      }
      await result(chapters.put({ ...object.chapter, archived: 0, stale: 0, revision }))
    }
    project.branch.story_revision++
    await result(projectStore.put(project))
    if (options.consumePublication && publication)
      await result(tx.objectStore('publications').delete([branchId, objectId]))
    return true
  })
}

async function validateEvidence(
  source: unknown,
  histories: Map<string, LocalRevision[]>,
  byId: Map<string, LocalRevision>,
): Promise<LocalEvidence> {
  const entry = fields(source, ['id', 'kind', 'title', 'body', 'sha256', 'chapters'])
  const id = identity(entry.id)
  const kind = entry.kind as EvidenceKind
  const title = content(entry.title, 200, '依据名称', true)
  const body = content(entry.body, 250_000, '依据内容')
  const hash = await manuscriptHash(body)
  if (
    !body.trim() ||
    !evidenceKinds.includes(kind) ||
    hash !== entry.sha256 ||
    !Array.isArray(entry.chapters) ||
    entry.chapters.length > 5000
  )
    invalid('稿件依据校验失败。')
  const chapters = entry.chapters.map((value) => {
    const reference = fields(value, ['id', 'revision', 'sha256', 'revision_id'])
    const identifier = identity(reference.id)
    const revision = number(reference.revision)
    const revision_id = reference.revision_id === undefined ? undefined : identity(reference.revision_id)
    const target = revision_id ? byId.get(revision_id) : histories.get(identifier)?.[revision]
    const link = {
      id: identifier,
      revision,
      sha256: content(reference.sha256, 64, '内容哈希', true),
      ...(revision_id ? { revision_id } : {}),
    }
    if (!evidenceMatches(link, target))
      throw new OfflineStoreError('missing', '缺少哈希一致的原章稿修订，随稿依据须等来源就绪后再采用。')
    return link
  })
  return { id, kind, title, body, sha256: hash, chapters }
}

async function validateRevision(source: unknown): Promise<LocalRevision> {
  const r = fields(source, [
    'id',
    'chapter_id',
    'revision',
    'parent_id',
    'content',
    'sha256',
    'cause',
    'created',
  ])
  const item = {
    id: identity(r.id),
    chapter_id: identity(r.chapter_id),
    revision: number(r.revision),
    parent_id: r.parent_id === null ? null : identity(r.parent_id),
    content: content(r.content, 250_000, '正文'),
    sha256: content(r.sha256, 64, '内容哈希', true),
    cause: content(r.cause, 4000, '修订原因'),
    created: number(r.created, 0, false),
  }
  if (item.sha256 !== (await manuscriptHash(item.content))) invalid('正文内容哈希校验失败。')
  return item
}

export async function validateManuscript(value: unknown): Promise<ManuscriptPack> {
  const pack = fields(value, [
    'format',
    'novel',
    'branch',
    'chapters',
    'revisions',
    'evidence',
    'source_revisions',
    'project_history',
    'settings_history',
    'outline_history',
  ])
  if (pack.format !== FORMAT) invalid('稿件交换包格式无法读取，请使用兼容版本。')
  const n = fields(pack.novel, ['id', 'title', 'brief', 'created'])
  const b = fields(pack.branch, ['id', 'novel_id', 'name', 'mode', 'story_revision', 'created'])
  const novel = {
    id: identity(n.id),
    title: content(n.title, 200, '作品名', true),
    brief: content(n.brief, 30_000, '创作思路'),
    created: number(n.created, 0, false),
  }
  const branch = {
    id: identity(b.id),
    novel_id: identity(b.novel_id),
    name: content(b.name, 200, '工作版本名', true),
    mode: workingMode(b.mode),
    story_revision: number(b.story_revision),
    created: number(b.created, 0, false),
  }
  if (branch.novel_id !== novel.id) invalid('工作版本与作品标识不匹配。')
  if (!Array.isArray(pack.chapters) || !Array.isArray(pack.revisions)) invalid('稿件交换包缺少目录或历史。')
  if (pack.chapters.length > 5000 || pack.revisions.length > 50_000)
    invalid('稿件交换包的章节或修订数量超过上限。')
  const chaptersById = new Map<string, LocalChapter>()
  const positions = new Set<number>()
  for (const source of pack.chapters) {
    const c = fields(source, ['id', 'branch_id', 'position', 'title', 'revision', 'archived', 'stale'])
    const chapter = {
      id: identity(c.id),
      branch_id: identity(c.branch_id),
      position: number(c.position, 1),
      title: content(c.title, 200, '章节名', true),
      revision: number(c.revision),
      archived: flag(c.archived),
      stale: flag(c.stale),
    }
    if (
      chapter.branch_id !== branch.id ||
      chaptersById.has(chapter.id) ||
      (!chapter.archived && positions.has(chapter.position))
    )
      invalid('章节归属无效、目录位置冲突或编号重复。')
    chaptersById.set(chapter.id, chapter)
    if (!chapter.archived) positions.add(chapter.position)
  }
  const byId = new Map<string, LocalRevision>()
  const histories = new Map<string, LocalRevision[]>()
  for (const source of pack.revisions) {
    const item = await validateRevision(source)
    if (!chaptersById.has(item.chapter_id) || byId.has(item.id)) invalid('修订归属无效或编号重复。')
    byId.set(item.id, item)
    if (!histories.has(item.chapter_id)) histories.set(item.chapter_id, [])
    histories.get(item.chapter_id)!.push(item)
  }
  const chapters = [...chaptersById.values()].sort((left, right) => left.position - right.position)
  const revisions = chapters.flatMap((chapter) => {
    const history = histories.get(chapter.id)?.sort((left, right) => left.revision - right.revision) ?? []
    if (
      history.length !== chapter.revision + 1 ||
      history.some(
        (item, index) => item.revision !== index || item.parent_id !== (history[index - 1]?.id ?? null),
      )
    )
      invalid('章节当前修订或父修订链不完整。')
    return history
  })
  const source_revisions: LocalRevision[] = []
  if (pack.source_revisions !== undefined) {
    if (!Array.isArray(pack.source_revisions) || pack.source_revisions.length > 50_000)
      invalid('来源稿件修订的数量超过上限或格式无效。')
    for (const value of pack.source_revisions) {
      const source = await validateRevision(value)
      if (byId.has(source.id)) invalid('来源修订编号重复。')
      byId.set(source.id, source)
      source_revisions.push(source)
    }
    for (const source of source_revisions) {
      if (!sourceLink(source, source.parent_id ? byId.get(source.parent_id) : undefined))
        invalid('来源稿件缺少同一章的连续父修订。')
    }
  }
  const evidence: LocalEvidence[] = []
  if (pack.evidence !== undefined) {
    if (!Array.isArray(pack.evidence) || pack.evidence.length > 100)
      invalid('稿件依据的数量超过上限或格式无效。')
    const known = new Set<string>()
    for (const source of pack.evidence) {
      const document = await validateEvidence(source, histories, byId)
      if (known.has(document.id)) invalid('随稿依据编号重复。')
      known.add(document.id)
      evidence.push(document)
    }
  }
  let project_history: ProjectHistory | undefined
  if (pack.project_history != null) {
    try {
      project_history = validateProjectHistory(pack.project_history)
    } catch (error) {
      invalid(error instanceof Error ? error.message : '立项历史格式无效。')
    }
  }
  let settings_history: SettingsHistory | undefined
  const projects = new Set(project_history?.versions.map(({ id }) => id))
  if (pack.settings_history != null) {
    try {
      settings_history = await validateSettingsHistory(pack.settings_history, projects, byId)
    } catch (error) {
      invalid(error instanceof Error ? error.message : '设定历史格式无效。')
    }
  }
  let outline_history: OutlineHistory | undefined
  if (pack.outline_history != null) {
    try {
      outline_history = await validateOutlineHistory(pack.outline_history, {
        projects,
        settings: new Map(settings_history?.versions.map((row) => [row.id, row])),
        chapters: new Set(chaptersById.keys()),
      })
    } catch (error) {
      invalid(error instanceof Error ? error.message : '大纲历史格式无效。')
    }
  }
  return {
    format: FORMAT,
    novel,
    branch,
    chapters,
    revisions,
    ...(evidence.length ? { evidence } : {}),
    ...(source_revisions.length ? { source_revisions } : {}),
    ...(project_history ? { project_history } : {}),
    ...(settings_history ? { settings_history } : {}),
    ...(outline_history ? { outline_history } : {}),
  }
}

export async function importLocalManuscript(file: Blob): Promise<LocalProject> {
  if (file.size > maxManuscriptBytes) invalid('稿件交换包超过 24 MiB。')
  let value: unknown
  try {
    value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer()))
  } catch {
    invalid('无法读取稿件交换包 JSON。')
  }
  const {
    novel,
    branch,
    chapters,
    revisions,
    evidence,
    source_revisions,
    project_history,
    settings_history,
    outline_history,
  } = await validateManuscript(value)
  const stores = [...new Set([...settingsDependencyStores, ...outlineWriteStores])]
  return transact(stores, 'readwrite', async (tx) => {
    const project = { novel, branch, ...(evidence?.length ? { evidence } : {}) }
    await result(tx.objectStore('projects').add(project))
    for (const chapter of chapters) await result(tx.objectStore('chapters').add(chapter))
    for (const revision of revisions) await result(tx.objectStore('revisions').add(revision))
    for (const revision of source_revisions ?? [])
      await result(tx.objectStore('sourceRevisions').add({ branchId: branch.id, revision }))
    if (project_history) {
      for (const version of project_history.versions) {
        await result(
          tx
            .objectStore('projectVersions')
            .add({ branchId: branch.id, version, fromPackage: true } satisfies LocalProjectVersion),
        )
      }
      if (project_history.adopted_revision_id)
        await result(
          tx.objectStore('projectHeads').add({
            branchId: branch.id,
            versionId: project_history.adopted_revision_id,
          } satisfies LocalProjectHead),
        )
    }
    if (settings_history) {
      for (const version of settings_history.versions) {
        const devices = settings_history.source_devices
        const recordedDevice = devices && Object.hasOwn(devices, version.id) ? devices[version.id] : undefined
        await result(
          tx.objectStore('settingsVersions').add({
            branchId: branch.id,
            version,
            fromPackage: true,
            ...(recordedDevice ? { recordedDevice } : {}),
          } satisfies LocalSettingsVersion),
        )
      }
      if (settings_history.adopted_revision_id)
        await result(
          tx.objectStore('settingsHeads').add({
            branchId: branch.id,
            versionId: settings_history.adopted_revision_id,
          } satisfies LocalProjectHead),
        )
    }
    if (outline_history) await outlineStore.restoreOutlineHistoryTx(tx, project, outline_history)
    return project
  })
}
