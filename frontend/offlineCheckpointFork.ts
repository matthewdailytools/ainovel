import type { LocalRevision, ManuscriptPack } from './offlineStore'
import { forkOutline, forkReferences, forkSettings, forkStory } from './offlineForkModel'
import { outlineChapterPlans, type OutlineVersion } from './outlineSyncModel'
import { decodePortableCheckpointHistory, type PortableCheckpoint } from './portableCheckpoint'
import type { ReferenceHistory } from './referenceArchive'
import type { SettingsEvidence, SettingsVersion } from './settingsSyncModel'
import { compactJSON, type StoryHistory, type StoryNativeRevision, type StoryVersion } from './storySyncModel'

type ForkIO = {
  validate: (value: unknown) => Promise<ManuscriptPack>
  digest: (value: string) => Promise<string>
  id?: () => string
  now?: () => number
}

export type CheckpointForkPreview = {
  chapterCount: number
  storyRecordCount: number
  outline: 'confirmed' | 'archived' | 'missing'
  sceneSpansForReview: number
  blockers: string[]
}

function must<T>(value: T | undefined, message: string): T {
  if (value === undefined) throw new Error(message)
  return value
}

function indexVersions<T extends { id: string }>(history?: { versions: T[] }) {
  return new Map(history?.versions.map((version) => [version.id, version]))
}

function lineage<T extends { id: string; parent_id: string | null }>(
  index: ReadonlyMap<string, T>,
  heads: Iterable<string | null>,
  canCopy?: (version: T) => boolean,
): T[] {
  const selected = new Map<string, T>()
  for (const head of heads) {
    let id = head
    while (id && !selected.has(id)) {
      const version = must(index.get(id), '归档恢复分支缺少正式来源修订。')
      if (canCopy && !canCopy(version)) {
        if (id === head) throw new Error('归档恢复分支缺少正式来源引用的当时章稿。')
        break
      }
      selected.set(id, version)
      id = version.parent_id
    }
  }
  return [...selected.values()].map((version) =>
    version.parent_id && !selected.has(version.parent_id) ? { ...version, parent_id: null } : version,
  )
}

function selectReferences(
  history: ReferenceHistory | undefined,
  outlines: readonly OutlineVersion[],
  currentOutline: OutlineVersion | undefined,
) {
  const pinnedByOutline = (outline: OutlineVersion) =>
    outline.reference_cards.flatMap(({ version_id }) => version_id ?? [])
  const required = new Set(outlines.flatMap(pinnedByOutline))
  if (!required.size) return undefined
  const pinned = new Set(currentOutline ? pinnedByOutline(currentOutline) : [])
  const versions = lineage(indexVersions(history), required)
  const included = new Set(versions.map(({ id }) => id))
  const heads = new Map<string, ReferenceHistory['adopted_heads']>()
  for (const { id, session_id, card_id } of versions) {
    if (!pinned.has(id)) continue
    const key = compactJSON([session_id, card_id])
    const scoped = heads.get(key) ?? []
    scoped.push({ session_id, card_id, revision_id: id })
    heads.set(key, scoped)
  }
  return {
    versions,
    adopted_heads: [...heads.values()].flatMap((rows) => (rows.length === 1 ? rows : [])),
    source_devices: Object.fromEntries(
      Object.entries(history?.source_devices ?? {}).filter(([id]) => included.has(id)),
    ),
  }
}

function selectStory(
  history: StoryHistory | undefined,
  checkpoint: NonNullable<PortableCheckpoint['restore_sources']>,
) {
  const heads = Object.entries(checkpoint.story_heads)
  if (!heads.length) return undefined
  const originals = indexVersions(history)
  const native = new Map<string, StoryNativeRevision>()
  const native_history = Object.fromEntries(
    heads.map(([id, head]) => {
      const rows = history?.native_history[id]?.filter(({ revision }) => revision <= head.revision)
      if (rows?.at(-1)?.version_id !== head.version_id)
        throw new Error('归档恢复分支缺少已保存的故事记录修订。')
      for (const row of rows) if (!native.has(row.version_id)) native.set(row.version_id, row)
      return [id, rows]
    }),
  )
  const versions: StoryVersion[] = [...native].map(([id, row]) => {
    const version = must(originals.get(id), '归档恢复分支缺少正式故事修订。')
    const bind = (rows: SettingsEvidence[], ids: string[]) =>
      rows.map((evidence, index) => ({ ...evidence, revision_id: ids[index] }))
    return {
      ...version,
      parent_id: version.parent_id && native.has(version.parent_id) ? version.parent_id : null,
      evidence: {
        record: bind(version.evidence.record, row.evidence.record),
        stages: version.evidence.stages.map((group, index) => bind(group, row.evidence.stages[index])),
      },
    }
  })
  return {
    versions,
    native_history,
    adopted_heads: Object.fromEntries(heads.map(([id, { version_id }]) => [id, version_id])),
    source_devices: Object.fromEntries(
      Object.entries(history?.source_devices ?? {}).filter(([id]) => native.has(id)),
    ),
  }
}

function capturedRevision(
  evidence: SettingsEvidence,
  originals: ReadonlyMap<string, LocalRevision>,
  captured: ReadonlyMap<string, LocalRevision>,
) {
  const original = originals.get(evidence.revision_id)
  const row = original && captured.get(`${original.chapter_id}:${original.revision}`)
  return row?.sha256 === original?.sha256 ? row : undefined
}

function rebindSettings(
  versions: SettingsVersion[],
  native: (evidence: SettingsEvidence) => LocalRevision | undefined,
) {
  return versions.map((version) => ({
    ...version,
    origins: Object.fromEntries(
      Object.entries(version.origins).map(([id, origin]) => [
        id,
        origin.evidence
          ? {
              ...origin,
              evidence: {
                ...origin.evidence,
                revision_id: must(native(origin.evidence), '归档中缺少设定引用的当时章稿。').id,
              },
            }
          : origin,
      ]),
    ),
  }))
}

function selectForkSources(pack: ManuscriptPack, checkpoint: PortableCheckpoint) {
  const sealed =
    pack.snapshot_history &&
    decodePortableCheckpointHistory(pack.snapshot_history).find(
      ({ checkpoint: row }) => row.id === checkpoint.id,
    )?.checkpoint
  if (!sealed || compactJSON(sealed) !== compactJSON(JSON.parse(JSON.stringify(checkpoint))))
    throw new Error('找不到与当前作品归档一致的检查点。')
  const restore = sealed.restore_sources
  if (!restore) throw new Error('该检查点仅供归档查看，缺少另存恢复分支所需的正式来源。')

  const captured = new Map(sealed.chapters.map((chapter) => [chapter.id, chapter]))
  const revisions = pack.revisions.filter(
    ({ chapter_id, revision }) => revision <= (captured.get(chapter_id)?.revision ?? -1),
  )
  const shared = sealed.shared
  const outlines = lineage(indexVersions(pack.outline_history), [shared.outline_id], (version) =>
    outlineChapterPlans(version.plan).every(({ chapter_id }) => captured.has(chapter_id)),
  )
  const selectedOutline = outlines.find(({ id }) => id === shared.outline_id)
  if (restore.outline_live && selectedOutline) {
    let position = 0
    for (const { chapter_id } of outlineChapterPlans(selectedOutline.plan)) {
      const chapter = captured.get(chapter_id)
      if (!chapter || chapter.archived || chapter.position < position)
        throw new Error('归档的正式大纲与当时采用的章节目录不一致。')
      position = chapter.position
    }
  }
  const originalRevisions = new Map(
    [...pack.revisions, ...(pack.source_revisions ?? [])].map((row) => [row.id, row]),
  )
  const nativeAddresses = new Map(revisions.map((row) => [`${row.chapter_id}:${row.revision}`, row]))
  const native = (evidence: SettingsEvidence) =>
    capturedRevision(evidence, originalRevisions, nativeAddresses)
  const settings = rebindSettings(
    lineage(
      indexVersions(pack.settings_history),
      [shared.settings_id, ...outlines.map((row) => row.settings_version_id)],
      ({ origins }) => Object.values(origins).every(({ evidence }) => !evidence || native(evidence)),
    ),
    native,
  )
  const projectIndex = indexVersions(pack.project_history)
  const projects = lineage(projectIndex, [
    shared.project_id,
    ...outlines.map(({ project_version_id }) => project_version_id),
    ...settings.flatMap(({ project_version_id, origins }) => [
      project_version_id,
      ...Object.values(origins).map((origin) => origin.project_version_id),
    ]),
  ])
  const references = selectReferences(pack.reference_history, outlines, selectedOutline)
  const story = selectStory(pack.story_history, restore)
  return {
    sealed,
    restore,
    captured,
    revisions,
    shared,
    outlines,
    selectedOutline,
    settings,
    projects,
    projectIndex,
    references,
    story,
  }
}

export async function previewCheckpointFork(
  source: ManuscriptPack,
  checkpoint: PortableCheckpoint,
  validate: ForkIO['validate'],
): Promise<CheckpointForkPreview> {
  const result: CheckpointForkPreview = {
    chapterCount: checkpoint.chapters.length,
    storyRecordCount: Object.keys(checkpoint.restore_sources?.story_heads ?? {}).length,
    outline: checkpoint.restore_sources?.outline_live
      ? 'confirmed'
      : checkpoint.shared.outline_id
        ? 'archived'
        : 'missing',
    sceneSpansForReview: checkpoint.restore_sources?.scene_spans_for_review ?? 0,
    blockers: [],
  }
  try {
    selectForkSources(await validate(source), checkpoint)
  } catch (error) {
    result.blockers.push(error instanceof Error ? error.message : '无法核验归档恢复分支的正式来源。')
  }
  return result
}

export async function buildCheckpointFork(
  source: ManuscriptPack,
  checkpoint: PortableCheckpoint,
  name: string,
  io: ForkIO,
) {
  const title = name.trim()
  if (!title || Array.from(title).length > 200 || /\p{Surrogate}/u.test(title))
    throw new Error('副本名称须为 1 至 200 个有效字符。')
  const pack = await io.validate(source)
  const {
    sealed,
    restore,
    captured,
    revisions,
    shared,
    outlines,
    selectedOutline,
    settings,
    projects,
    projectIndex,
    references,
    story,
  } = selectForkSources(pack, checkpoint)

  const fresh = io.id ?? (() => crypto.randomUUID().replaceAll('-', ''))
  const created = (io.now ?? (() => Date.now() / 1000))()
  const novelId = fresh()
  const branchId = fresh()
  const ids = (values: Iterable<string>) => new Map([...new Set(values)].map((id) => [id, fresh()]))
  const chapterIds = ids(captured.keys())
  const revisionIds = ids(revisions.map(({ id }) => id))
  const projectIds = ids(projects.map(({ id }) => id))
  const settingsIds = ids(settings.map(({ id }) => id))
  const outlineIds = ids(outlines.map(({ id }) => id))
  const records = ids([
    ...Object.keys(restore.story_heads),
    ...outlines.flatMap(({ plan }) => plan.promises.map(({ promise_id }) => promise_id)),
  ])
  const mapped = (rows: ReadonlyMap<string, string>, id: string) =>
    must(rows.get(id), '归档恢复分支缺少原始编号的对应关系。')
  const optional = (rows: ReadonlyMap<string, string>, id: string | null) =>
    id === null ? null : mapped(rows, id)
  const referenceIds = new Map<string, string>()
  const reference = references && (await forkReferences(references, branchId, io.digest, referenceIds))
  const storyHistory = story && (await forkStory(story, branchId, records, chapterIds, revisionIds))
  // A remote source alias and its equivalent native revision can resolve to the same frozen version.
  if (storyHistory) storyHistory.versions = [...indexVersions(storyHistory).values()]
  const adoptedOutlineId = restore.outline_live ? shared.outline_id : null
  const required = restore.outline_live
    ? (selectedOutline?.plan.promises.flatMap(({ promise_id }) =>
        Object.hasOwn(selectedOutline.promise_sources, promise_id) ? [] : [promise_id],
      ) ?? [])
    : []
  const outline = outlines.length
    ? forkOutline(
        {
          adopted_revision_id: adoptedOutlineId,
          versions: outlines,
          ...(required.length ? { required_story_records: required } : {}),
        },
        outlineIds,
        projectIds,
        settingsIds,
        chapterIds,
        records,
        referenceIds,
      )
    : undefined
  const selectedProject = shared.project_id && projectIndex.get(shared.project_id)
  const copy: ManuscriptPack = {
    format: pack.format,
    novel: { id: novelId, title, brief: selectedProject ? selectedProject.idea : '', created },
    branch: { ...pack.branch, id: branchId, novel_id: novelId, name: title, story_revision: 1, created },
    chapters: sealed.chapters.map(({ revision_id: _id, sha256: _hash, ...chapter }) => ({
      ...chapter,
      id: mapped(chapterIds, chapter.id),
      branch_id: branchId,
    })),
    revisions: revisions.map((row) => ({
      ...row,
      id: mapped(revisionIds, row.id),
      chapter_id: mapped(chapterIds, row.chapter_id),
      parent_id: optional(revisionIds, row.parent_id),
    })),
    ...(projects.length
      ? {
          project_history: {
            adopted_revision_id: optional(projectIds, shared.project_id),
            versions: projects.map((row) => ({
              ...row,
              id: mapped(projectIds, row.id),
              parent_id: optional(projectIds, row.parent_id),
            })),
          },
        }
      : {}),
    ...(settings.length
      ? {
          settings_history: forkSettings(
            {
              adopted_revision_id: shared.settings_id,
              versions: settings,
            },
            settingsIds,
            projectIds,
            chapterIds,
            revisionIds,
          ),
        }
      : {}),
    ...(outline ? { outline_history: outline } : {}),
    ...(reference ? { reference_history: reference } : {}),
    ...(storyHistory ? { story_history: storyHistory } : {}),
    restore_state: {
      facts: restore.facts
        .filter(({ evidence }) => captured.get(evidence.chapter_id)?.archived === 0)
        .map((fact) => ({
          ...fact,
          evidence: {
            ...fact.evidence,
            chapter_id: mapped(chapterIds, fact.evidence.chapter_id),
            revision_id: mapped(revisionIds, fact.evidence.revision_id),
          },
        })),
      length_targets: restore.length_targets.map((target) => ({
        ...target,
        scope_id: target.scope === 'chapter' ? optional(chapterIds, target.scope_id) : target.scope_id,
      })),
    },
  }
  return {
    pack: await io.validate(copy),
    provenance: {
      sourceBranchId: sealed.branch_id,
      checkpointId: sealed.id,
      scope: sealed.scope,
      scopeId: sealed.scope_id,
      sourceManifestSha256: sealed.source_manifest_sha256,
      sceneSpansForReview: restore.scene_spans_for_review,
    },
  }
}
