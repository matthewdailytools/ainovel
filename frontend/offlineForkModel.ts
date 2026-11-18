import type { LocalRevision, ManuscriptPack } from './offlineStore'
import type { OutlineCardReference, OutlineHistory } from './outlineSyncModel'
import type { ReferenceHistory } from './referenceArchive'
import type { SettingsEvidence, SettingsHistory } from './settingsSyncModel'
import {
  buildStoryVersion,
  compactJSON,
  orderStoryVersions,
  type StoryHistory,
  type StoryRecord,
  type StoryVersion,
} from './storySyncModel'

export type OfflineForkSelection = {
  chapterId: string
  revisionId: string
  content: string
  sha256: string
}

type ForkIO = {
  validate: (value: unknown) => Promise<ManuscriptPack>
  digest: (value: string) => Promise<string>
  id?: () => string
  now?: () => number
}

type Ids = Map<string, string>

function idOf(ids: Ids, id: string) {
  const mapped = ids.get(id)
  if (!mapped) throw new Error('副本缺少必须保留的来源编号 ' + id + '。')
  return mapped
}

const optionalId = (ids: Ids, id: string | null) => (id === null ? null : idOf(ids, id))

function deviceIds(devices: Record<string, string>, ids: Ids) {
  return Object.fromEntries(Object.entries(devices).map(([id, device]) => [idOf(ids, id), device]))
}

function rekeyEvidence(entry: SettingsEvidence, chapters: Ids, revisions: Ids) {
  return {
    ...entry,
    chapter_id: idOf(chapters, entry.chapter_id),
    revision_id: idOf(revisions, entry.revision_id),
  }
}

function forkSettings(history: SettingsHistory, settings: Ids, projects: Ids, chapters: Ids, revisions: Ids) {
  return {
    adopted_revision_id: optionalId(settings, history.adopted_revision_id),
    versions: history.versions.map((version) => ({
      ...version,
      id: idOf(settings, version.id),
      parent_id: optionalId(settings, version.parent_id),
      project_version_id: optionalId(projects, version.project_version_id),
      origins: Object.fromEntries(
        Object.entries(version.origins).map(([id, origin]) => [
          id,
          {
            ...origin,
            project_version_id: optionalId(projects, origin.project_version_id),
            evidence: origin.evidence && rekeyEvidence(origin.evidence, chapters, revisions),
          },
        ]),
      ),
    })),
    ...(history.source_devices ? { source_devices: deviceIds(history.source_devices, settings) } : {}),
  }
}

async function forkReferences(
  history: ReferenceHistory,
  branchId: string,
  digest: ForkIO['digest'],
  ids: Ids,
) {
  const versions = []
  for (const { id, ...before } of [...history.versions].sort(
    (left, right) => left.revision - right.revision,
  )) {
    const version = { ...before, parent_id: optionalId(ids, before.parent_id) }
    const mapped = await digest(compactJSON([branchId, version]))
    ids.set(id, mapped)
    versions.push({ ...version, id: mapped })
  }
  return {
    versions,
    adopted_heads: history.adopted_heads.map((head) => ({
      ...head,
      revision_id: idOf(ids, head.revision_id),
    })),
    source_devices: deviceIds(history.source_devices, ids),
  }
}

function forkRecord(record: StoryRecord, records: Ids): StoryRecord {
  const names = (ids: string[]) => ids.map((id) => idOf(records, id))
  const related = (id: string | null) => (id ? idOf(records, id) : id)
  const depends_on = names(record.depends_on)
  switch (record.kind) {
    case 'character':
      return { ...record, depends_on }
    case 'event':
      return { ...record, depends_on, participants: names(record.participants), after: names(record.after) }
    case 'state':
      return {
        ...record,
        depends_on,
        character_id: idOf(records, record.character_id),
        event_id: idOf(records, record.event_id),
        until_event_id: related(record.until_event_id),
      }
    case 'relationship':
      return {
        ...record,
        depends_on,
        from_id: idOf(records, record.from_id),
        to_id: idOf(records, record.to_id),
        event_id: related(record.event_id),
      }
    case 'promise':
      return {
        ...record,
        depends_on,
        stages: record.stages.map((stage) => ({
          ...stage,
          event_id: related(stage.event_id),
        })),
      }
  }
}

async function forkStory(
  history: StoryHistory,
  branchId: string,
  records: Ids,
  chapters: Ids,
  revisions: Ids,
) {
  const ids: Ids = new Map()
  const versions: StoryVersion[] = []
  const evidence = (rows: SettingsEvidence[]) => rows.map((row) => rekeyEvidence(row, chapters, revisions))
  for (const before of orderStoryVersions(
    new Map(history.versions.map((version) => [version.id, version])),
  )) {
    const version = await buildStoryVersion(branchId, {
      parent_id: optionalId(ids, before.parent_id),
      record_id: idOf(records, before.record_id),
      archived: before.archived,
      record: forkRecord(before.record, records),
      evidence: { record: evidence(before.evidence.record), stages: before.evidence.stages.map(evidence) },
      dependencies: Object.fromEntries(
        Object.entries(before.dependencies).map(([id, version]) => [idOf(records, id), idOf(ids, version)]),
      ),
    })
    ids.set(before.id, version.id)
    versions.push(version)
  }
  const adopted_heads = Object.fromEntries(
    Object.entries(history.adopted_heads).map(([id, version]) => [idOf(records, id), idOf(ids, version)]),
  )
  const native_history = Object.fromEntries(
    Object.entries(history.native_history).map(([id, rows]) => [
      idOf(records, id),
      rows.map((row) => ({
        ...row,
        version_id: idOf(ids, row.version_id),
        dependencies: Object.fromEntries(
          Object.entries(row.dependencies).map(([record, revision]) => [idOf(records, record), revision]),
        ),
        evidence: {
          record: row.evidence.record.map((id) => idOf(revisions, id)),
          stages: row.evidence.stages.map((group) => group.map((id) => idOf(revisions, id))),
        },
      })),
    ]),
  )
  return { adopted_heads, versions, source_devices: deviceIds(history.source_devices, ids), native_history }
}

function forkOutline(
  history: OutlineHistory,
  outlines: Ids,
  projects: Ids,
  settings: Ids,
  chapters: Ids,
  records: Ids,
  references: Ids,
): OutlineHistory {
  function rekeyCard<T extends OutlineCardReference>(ref: T): T {
    return ref.version_id ? { ...ref, version_id: idOf(references, ref.version_id) } : { ...ref }
  }
  return {
    adopted_revision_id: optionalId(outlines, history.adopted_revision_id),
    versions: history.versions.map((version) => ({
      ...version,
      id: idOf(outlines, version.id),
      parent_id: optionalId(outlines, version.parent_id),
      project_version_id: optionalId(projects, version.project_version_id),
      settings_version_id: optionalId(settings, version.settings_version_id),
      plan: {
        ...version.plan,
        volumes: version.plan.volumes.map((volume) => ({
          ...volume,
          units: volume.units.map((unit) => ({
            ...unit,
            chapters: unit.chapters.map((chapter) => ({
              ...chapter,
              chapter_id: idOf(chapters, chapter.chapter_id),
              ...(chapter.reference_cards ? { reference_cards: chapter.reference_cards.map(rekeyCard) } : {}),
            })),
          })),
        })),
        promises: version.plan.promises.map((window) => ({
          promise_id: idOf(records, window.promise_id),
          plant_chapter_id: idOf(chapters, window.plant_chapter_id),
          opens_chapter_id: idOf(chapters, window.opens_chapter_id),
          closes_chapter_id: idOf(chapters, window.closes_chapter_id),
        })),
      },
      reference_cards: version.reference_cards.map(rekeyCard),
      promise_sources: Object.fromEntries(
        Object.entries(version.promise_sources).map(([id, seed]) => [idOf(records, id), seed]),
      ),
    })),
    ...(history.source_devices ? { source_devices: deviceIds(history.source_devices, outlines) } : {}),
    ...(history.required_story_records
      ? { required_story_records: history.required_story_records.map((id) => idOf(records, id)) }
      : {}),
  }
}

export async function buildOfflineFork(
  source: ManuscriptPack,
  selected: OfflineForkSelection,
  name: string,
  io: ForkIO,
) {
  const pack = await io.validate(source)
  const title = name.trim()
  if (!title || Array.from(title).length > 200 || /\p{Surrogate}/u.test(title))
    throw new Error('副本名称须为 1 至 200 个有效字符。')
  const chapter = pack.chapters.find(({ id }) => id === selected.chapterId)
  if (!chapter) throw new Error('原作品中找不到所比较的章节及其历史。')
  const originals = [...pack.revisions, ...(pack.source_revisions ?? [])]
  const known = originals.find(({ id }) => id === selected.revisionId)
  if (!known) throw new Error('缺少所选章稿的正式来源修订，请先同步原始版本。')
  if (
    known.chapter_id !== chapter.id ||
    known.sha256 !== selected.sha256 ||
    known.content !== selected.content
  )
    throw new Error('所选章稿与原作品中的同一来源修订不一致。')
  const occupied = pack.chapters.filter(({ archived, id }) => !archived && id !== chapter.id)
  const position = occupied.some((row) => row.position === chapter.position)
    ? Math.max(...occupied.map((row) => row.position)) + 1
    : chapter.position
  if (!Number.isSafeInteger(position)) throw new Error('副本中没有可分配的新章节位置。')

  const fresh = io.id ?? (() => crypto.randomUUID().replaceAll('-', ''))
  const created = (io.now ?? (() => Date.now() / 1000))()
  const novelId = fresh()
  const branchId = fresh()
  const scope = (values: string[]): Ids => new Map([...new Set(values)].map((id) => [id, fresh()]))
  const chapters = scope([
    ...pack.chapters.map(({ id }) => id),
    ...originals.map(({ chapter_id }) => chapter_id),
  ])
  const revisions = scope(originals.map(({ id }) => id))
  const projectIds = scope(pack.project_history?.versions.map(({ id }) => id) ?? [])
  const settingsIds = scope(pack.settings_history?.versions.map(({ id }) => id) ?? [])
  const outlineIds = scope(pack.outline_history?.versions.map(({ id }) => id) ?? [])
  const records = scope([
    ...(pack.story_history?.versions.map(({ record_id }) => record_id) ?? []),
    ...(pack.outline_history?.versions.flatMap(({ plan }) =>
      plan.promises.map(({ promise_id }) => promise_id),
    ) ?? []),
    ...(pack.outline_history?.required_story_records ?? []),
  ])
  const remap = (row: LocalRevision): LocalRevision => ({
    ...row,
    id: idOf(revisions, row.id),
    chapter_id: idOf(chapters, row.chapter_id),
    parent_id: optionalId(revisions, row.parent_id),
  })

  const active = new Map(pack.chapters.map(({ id, revision }) => [id, revision]))
  const heads = new Map(
    pack.revisions
      .filter(({ chapter_id, revision }) => active.get(chapter_id) === revision)
      .map((row) => [row.chapter_id, row]),
  )
  const head = heads.get(chapter.id)!
  const newChapterId = idOf(chapters, chapter.id)
  const changes = head.content !== selected.content
  const recheck = changes || chapter.archived
  const newRevisionId = changes ? fresh() : idOf(revisions, head.id)
  const selectedRevision: LocalRevision | undefined = changes
    ? {
        id: newRevisionId,
        chapter_id: newChapterId,
        revision: head.revision + 1,
        parent_id: idOf(revisions, head.id),
        content: selected.content,
        sha256: selected.sha256,
        cause: '另存冲突章稿副本',
        created,
      }
    : undefined
  const restored = pack.revisions.flatMap((row) =>
    row === head && selectedRevision ? [remap(row), selectedRevision] : [remap(row)],
  )

  const referenceIds: Ids = new Map()
  const reference =
    pack.reference_history &&
    (await forkReferences(pack.reference_history, branchId, io.digest, referenceIds))
  const story =
    pack.story_history && (await forkStory(pack.story_history, branchId, records, chapters, revisions))
  const outline =
    pack.outline_history &&
    forkOutline(pack.outline_history, outlineIds, projectIds, settingsIds, chapters, records, referenceIds)
  const project = pack.project_history && {
    adopted_revision_id: optionalId(projectIds, pack.project_history.adopted_revision_id),
    versions: pack.project_history.versions.map((version) => ({
      ...version,
      id: idOf(projectIds, version.id),
      parent_id: optionalId(projectIds, version.parent_id),
    })),
  }
  const settings =
    pack.settings_history && forkSettings(pack.settings_history, settingsIds, projectIds, chapters, revisions)
  const newChapters = pack.chapters
    .map((row) => {
      const isSelected = row.id === chapter.id
      return {
        ...row,
        id: idOf(chapters, row.id),
        branch_id: branchId,
        ...(isSelected ? { position, archived: 0 as const, revision: row.revision + Number(changes) } : {}),
        stale:
          recheck && (isSelected || (row.position > position && heads.get(row.id)?.content))
            ? (1 as const)
            : row.stale,
      }
    })
    .sort((a, b) => a.position - b.position)
  if (position !== chapter.position) {
    const chapterOrder = new Map(newChapters.map(({ id }, index) => [id, index]))
    restored.sort((a, b) => chapterOrder.get(a.chapter_id)! - chapterOrder.get(b.chapter_id)!)
  }
  const output: ManuscriptPack = {
    format: pack.format,
    novel: { ...pack.novel, title, id: novelId, created },
    branch: { ...pack.branch, id: branchId, novel_id: novelId, name: title, created },
    chapters: newChapters,
    revisions: restored,
    ...(pack.source_revisions ? { source_revisions: pack.source_revisions.map(remap) } : {}),
    ...(project ? { project_history: project } : {}),
    ...(settings ? { settings_history: settings } : {}),
    ...(reference ? { reference_history: reference } : {}),
    ...(story ? { story_history: story } : {}),
    ...(outline ? { outline_history: outline } : {}),
  }
  return {
    pack: output,
    provenance: {
      sourceBranchId: pack.branch.id,
      chapterId: chapter.id,
      sourceRevisionId: selected.revisionId,
      newChapterId,
      newRevisionId,
    },
  }
}
