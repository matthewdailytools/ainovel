import type { OutlineVersion } from './outlineSyncModel'
import { parseSettingsEvidence, type SettingsEvidence, type SettingsVersion } from './settingsSyncModel'
import {
  validateStoryEffectiveGraph,
  verifyStoryEvidence,
  type StoryHistory,
  type StoryVersion,
} from './storySyncModel'

const version = 'ainovel-portable-checkpoint/1' as const
const maxCheckpoints = 5000
const maxChapters = 5000
const maxStoryVersions = 20_000
const maxBody = 4 * 1024 * 1024
const hashPattern = /^[a-f0-9]{64}$/

export type PortableCheckpointEntry = { id: string; sha256: string; body: string }
export type PortableCheckpointHistory = { entries: PortableCheckpointEntry[] }
type CheckpointChapter = {
  id: string
  position: number
  title: string
  revision: number
  revision_id: string
  sha256: string
  archived: 0 | 1
  stale: 0 | 1
}
export type PortableCheckpointLengthTarget = {
  scope: 'chapter' | 'volume' | 'book'
  scope_id: string | null
  targets: { min_chars: number | null; max_chars: number | null; expected_chapters: number | null } | null
}
const factKinds = [
  'event',
  'knowledge',
  'capability',
  'resource',
  'relationship',
  'promise',
  'world',
] as const
const factTruths = ['fact', 'belief', 'uncertain', 'plan'] as const
export type PortableCheckpointFact = {
  subject: string
  kind: (typeof factKinds)[number]
  statement: string
  truth: (typeof factTruths)[number]
  story_time: string
  evidence: SettingsEvidence
}
export type PortableRestoreState = {
  facts: PortableCheckpointFact[]
  length_targets: PortableCheckpointLengthTarget[]
}
type CheckpointRestoreSources = PortableRestoreState & {
  version: 'ainovel-portable-restore-sources/1'
  outline_live: boolean
  story_heads: Record<string, { revision: number; version_id: string }>
  scene_spans_for_review: number
}
export type PortableCheckpoint = {
  version: typeof version
  id: string
  branch_id: string
  label: string
  scope: 'volume' | 'book'
  scope_id: string | null
  purpose: 'manual' | 'milestone' | 'pre_restore'
  created: number
  source_manifest_sha256: string
  story_revision: number
  scoped_chapter_ids: string[]
  chapters: CheckpointChapter[]
  shared: { project_id: string | null; settings_id: string | null; outline_id: string | null }
  reports: { workflow_inspection?: string; book_completion?: string }
  restore_sources?: CheckpointRestoreSources
}

type SealedCheckpoint = { entry: PortableCheckpointEntry; checkpoint: PortableCheckpoint }
type ChapterRevision = { chapter_id: string; revision: number; sha256: string; content: string }
export type PortableCheckpointSources = {
  branch: { id: string; story_revision: number }
  chapterIds: ReadonlySet<string>
  revisions: ReadonlyMap<string, ChapterRevision>
  sourceRevisions: ReadonlyMap<string, ChapterRevision>
  projects: ReadonlySet<string>
  settings: ReadonlyMap<string, SettingsVersion>
  outlines: ReadonlyMap<string, OutlineVersion>
  inspections: ReadonlySet<string>
  completions: ReadonlySet<string>
  storyHistory?: StoryHistory
}

function invalid(): never {
  throw new Error('卷书检查点的格式、归属或校验值无效。')
}

function fields(value: unknown, allowed: string[]): Record<string, unknown> {
  if (
    !value ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    Object.keys(value).some((key) => !allowed.includes(key))
  )
    invalid()
  return value as Record<string, unknown>
}

function id(value: unknown): string {
  if (typeof value !== 'string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(value)) invalid()
  return value
}

function nullableId(value: unknown) {
  return value === null ? null : id(value)
}

function text(value: unknown, limit = 200, allowEmpty = false): string {
  if (typeof value !== 'string' || value.length > limit * 2 || /\p{Surrogate}/u.test(value)) invalid()
  if ((!allowEmpty && !value.trim()) || [...value].length > limit) invalid()
  return value
}

function hash(value: unknown): string {
  if (typeof value !== 'string' || !hashPattern.test(value)) invalid()
  return value
}

function integer(value: unknown, min = 0): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < min) invalid()
  return value
}

function flag(value: unknown): 0 | 1 {
  if (value !== 0 && value !== 1) invalid()
  return value
}

function sameIds(left: readonly string[], right: readonly string[]) {
  return left.length === right.length && left.every((value, index) => value === right[index])
}

function parseRestoreSources(value: unknown): CheckpointRestoreSources {
  const source = fields(value, [
    'version',
    'outline_live',
    'story_heads',
    'facts',
    'length_targets',
    'scene_spans_for_review',
  ])
  if (source.version !== 'ainovel-portable-restore-sources/1' || typeof source.outline_live !== 'boolean')
    invalid()
  if (!source.story_heads || typeof source.story_heads !== 'object' || Array.isArray(source.story_heads))
    invalid()
  const heads = Object.entries(source.story_heads)
  if (heads.length > maxStoryVersions) invalid()
  const story_heads = Object.fromEntries(
    heads.map(([key, value]) => {
      const head = fields(value, ['revision', 'version_id'])
      return [id(key), { revision: integer(head.revision), version_id: hash(head.version_id) }]
    }),
  )
  return {
    version: source.version,
    outline_live: source.outline_live,
    story_heads,
    ...parseRestoreState(source),
    scene_spans_for_review: integer(source.scene_spans_for_review),
  }
}

function parseRestoreState({
  facts: factRows,
  length_targets: targetRows,
}: Record<string, unknown>): PortableRestoreState {
  if (!Array.isArray(factRows) || factRows.length > maxStoryVersions) invalid()
  const facts = factRows.map((value) => {
    const row = fields(value, ['subject', 'kind', 'statement', 'truth', 'story_time', 'evidence'])
    const kind = factKinds.find((value) => value === row.kind)
    const truth = factTruths.find((value) => value === row.truth)
    const subject = text(row.subject, 200, true)
    const statement = text(row.statement, 2000, true)
    if (!subject || !statement || !kind || !truth) invalid()
    return {
      subject,
      kind,
      statement,
      truth,
      story_time: text(row.story_time, 200, true),
      evidence: parseSettingsEvidence(row.evidence),
    }
  })
  if (!Array.isArray(targetRows) || targetRows.length > maxChapters) invalid()
  const length_targets = targetRows.map((value): PortableCheckpointLengthTarget => {
    const row = fields(value, ['scope', 'scope_id', 'targets'])
    const { scope } = row
    if (scope !== 'chapter' && scope !== 'volume' && scope !== 'book') invalid()
    const scope_id = nullableId(row.scope_id)
    if ((scope === 'book') !== (scope_id === null)) invalid()
    if (row.targets === null) return { scope, scope_id, targets: null }
    const fieldsByName = fields(row.targets, ['min_chars', 'max_chars', 'expected_chapters'])
    const min_chars = fieldsByName.min_chars == null ? null : integer(fieldsByName.min_chars)
    const max_chars = fieldsByName.max_chars == null ? null : integer(fieldsByName.max_chars, 1)
    const expected_chapters =
      fieldsByName.expected_chapters == null ? null : integer(fieldsByName.expected_chapters, 1)
    if (
      (expected_chapters !== null && expected_chapters > 1000) ||
      (min_chars !== null && max_chars !== null && min_chars > max_chars)
    )
      invalid()
    return { scope, scope_id, targets: { min_chars, max_chars, expected_chapters } }
  })
  return { facts, length_targets }
}

export function lengthTargetKey({
  scope,
  scope_id,
}: Pick<PortableCheckpointLengthTarget, 'scope' | 'scope_id'>) {
  return `${scope}:${scope_id ?? ''}`
}

export async function validatePortableRestoreState(
  value: unknown,
  heads: ReadonlyMap<string, ChapterRevision & { id: string }>,
  volumes: ReadonlySet<string>,
  archivedChapters: ReadonlySet<string>,
) {
  const state = parseRestoreState(fields(value, ['facts', 'length_targets']))
  verifyTargetScopes(state.length_targets, heads, volumes)
  await verifyFacts(state.facts, ({ chapter_id }) =>
    archivedChapters.has(chapter_id) ? undefined : heads.get(chapter_id),
  )
  return state
}

// JSON.parse has already checked the syntax. Inspect keys because re-export preserves the exact source
// and an overwritten duplicate key could otherwise carry fields absent from the validated projection.
function uniqueKeys(body: string) {
  const objects: Set<string>[] = []
  let stringStart = 0
  let stringEnd = 0
  for (let index = 0; index < body.length; index++) {
    const character = body[index]
    if (character === '"') {
      stringStart = index
      while (body[++index] !== '"') if (body[index] === '\\') index++
      stringEnd = index + 1
    } else if (character === '{') {
      objects.push(new Set())
    } else if (character === '}') {
      objects.pop()
    } else if (character === ':') {
      const keys = objects.at(-1)!
      const key: string = JSON.parse(body.slice(stringStart, stringEnd))
      if (keys.has(key)) invalid()
      keys.add(key)
    }
  }
}

function decode(value: unknown): SealedCheckpoint {
  const envelope = fields(value, ['id', 'sha256', 'body'])
  const body = envelope.body
  if (
    typeof body !== 'string' ||
    body.length > maxBody * 2 ||
    (body.length > maxBody && [...body].length > maxBody) ||
    /\p{Surrogate}/u.test(body)
  )
    invalid()
  const entry = { id: id(envelope.id), sha256: hash(envelope.sha256), body }
  let decoded: unknown
  try {
    decoded = JSON.parse(body)
  } catch {
    invalid()
  }
  uniqueKeys(body)
  const checkpoint = fields(decoded, [
    'version',
    'id',
    'branch_id',
    'label',
    'scope',
    'scope_id',
    'purpose',
    'created',
    'source_manifest_sha256',
    'story_revision',
    'scoped_chapter_ids',
    'chapters',
    'shared',
    'reports',
    'restore_sources',
  ])
  const { scope, purpose, created } = checkpoint
  if (
    checkpoint.version !== version ||
    checkpoint.id !== entry.id ||
    (scope !== 'volume' && scope !== 'book') ||
    (purpose !== 'manual' && purpose !== 'milestone' && purpose !== 'pre_restore') ||
    typeof created !== 'number' ||
    !Number.isFinite(created) ||
    created < 0
  )
    invalid()
  const scope_id = nullableId(checkpoint.scope_id)
  if ((scope === 'book') !== (scope_id === null)) invalid()
  if (
    !Array.isArray(checkpoint.chapters) ||
    checkpoint.chapters.length > maxChapters ||
    !Array.isArray(checkpoint.scoped_chapter_ids) ||
    checkpoint.scoped_chapter_ids.length > maxChapters
  )
    invalid()
  const chapters = checkpoint.chapters.map((value) => {
    const chapter = fields(value, [
      'id',
      'position',
      'title',
      'revision',
      'revision_id',
      'sha256',
      'archived',
      'stale',
    ])
    return {
      id: id(chapter.id),
      position: integer(chapter.position, 1),
      title: text(chapter.title),
      revision: integer(chapter.revision),
      revision_id: id(chapter.revision_id),
      sha256: hash(chapter.sha256),
      archived: flag(chapter.archived),
      stale: flag(chapter.stale),
    }
  })
  const scoped_chapter_ids = checkpoint.scoped_chapter_ids.map(id)
  const active = chapters.filter(({ archived }) => !archived)
  const activeIds = new Set(active.map(({ id }) => id))
  if (
    new Set(chapters.map(({ id }) => id)).size !== chapters.length ||
    active.some(({ position }, index) => index > 0 && position <= active[index - 1].position) ||
    new Set(scoped_chapter_ids).size !== scoped_chapter_ids.length ||
    (scope === 'book' &&
      !sameIds(
        scoped_chapter_ids,
        active.map(({ id }) => id),
      )) ||
    (scope === 'volume' &&
      (!scoped_chapter_ids.length || scoped_chapter_ids.some((id) => !activeIds.has(id))))
  )
    invalid()
  const shared = fields(checkpoint.shared, ['project_id', 'settings_id', 'outline_id'])
  const reports = fields(checkpoint.reports, ['workflow_inspection', 'book_completion'])
  return {
    entry,
    checkpoint: {
      version,
      id: entry.id,
      branch_id: id(checkpoint.branch_id),
      label: text(checkpoint.label),
      scope,
      scope_id,
      purpose,
      created,
      source_manifest_sha256: hash(checkpoint.source_manifest_sha256),
      story_revision: integer(checkpoint.story_revision),
      scoped_chapter_ids,
      chapters,
      shared: {
        project_id: nullableId(shared.project_id),
        settings_id: nullableId(shared.settings_id),
        outline_id: nullableId(shared.outline_id),
      },
      reports: {
        ...(reports.workflow_inspection === undefined
          ? {}
          : { workflow_inspection: id(reports.workflow_inspection) }),
        ...(reports.book_completion === undefined ? {} : { book_completion: id(reports.book_completion) }),
      },
      ...(checkpoint.restore_sources != null
        ? { restore_sources: parseRestoreSources(checkpoint.restore_sources) }
        : {}),
    },
  }
}

export function decodePortableCheckpointHistory(value: unknown): SealedCheckpoint[] {
  const history = fields(value, ['entries'])
  if (!Array.isArray(history.entries) || !history.entries.length || history.entries.length > maxCheckpoints)
    invalid()
  const entries = history.entries.map(decode)
  if (new Set(entries.map(({ entry }) => entry.id)).size !== entries.length) invalid()
  return entries
}

export async function verifyPortableCheckpoints(
  sealed: SealedCheckpoint[],
  {
    branch,
    chapterIds,
    revisions,
    sourceRevisions,
    projects,
    settings,
    outlines,
    inspections,
    completions,
    storyHistory,
  }: PortableCheckpointSources,
  digest: (body: string) => Promise<string>,
) {
  const checkedRevisions = new Set<string>()
  const nativeByAddress = new Map(
    [...revisions.values()].map((row) => [`${row.chapter_id}:${row.revision}`, row]),
  )
  for (const { entry, checkpoint } of sealed) {
    const { project_id, settings_id, outline_id } = checkpoint.shared
    const { workflow_inspection, book_completion } = checkpoint.reports
    if (
      checkpoint.branch_id !== branch.id ||
      checkpoint.story_revision > branch.story_revision ||
      (project_id !== null && !projects.has(project_id)) ||
      (settings_id !== null && !settings.has(settings_id)) ||
      (outline_id !== null && !outlines.has(outline_id)) ||
      (workflow_inspection !== undefined && !inspections.has(workflow_inspection)) ||
      (book_completion !== undefined && !completions.has(book_completion)) ||
      (await digest(entry.body)) !== entry.sha256
    )
      invalid()
    for (const chapter of checkpoint.chapters) {
      const revision = revisions.get(chapter.revision_id)
      if (
        !chapterIds.has(chapter.id) ||
        !revision ||
        revision.chapter_id !== chapter.id ||
        revision.revision !== chapter.revision ||
        revision.sha256 !== chapter.sha256
      )
        invalid()
      if (!checkedRevisions.has(chapter.revision_id)) {
        if ((await digest(revision.content)) !== chapter.sha256) invalid()
        checkedRevisions.add(chapter.revision_id)
      }
    }
    const outline = outline_id ? outlines.get(outline_id) : undefined
    if (checkpoint.scope === 'volume') {
      if (!outline) invalid()
      const volume = outline.plan.volumes.find(({ id }) => id === checkpoint.scope_id)
      if (
        !volume ||
        !sameIds(
          checkpoint.scoped_chapter_ids,
          volume.units.flatMap(({ chapters }) => chapters.map(({ chapter_id }) => chapter_id)),
        )
      )
        invalid()
    }
    if (checkpoint.restore_sources)
      await verifyRestoreSources(checkpoint, checkpoint.restore_sources, outline, storyHistory, revisions, {
        version: settings_id === null ? undefined : settings.get(settings_id),
        sourceRevisions,
        nativeByAddress,
      })
  }
}

async function verifyRestoreSources(
  checkpoint: PortableCheckpoint,
  sources: CheckpointRestoreSources,
  outline: OutlineVersion | undefined,
  story: StoryHistory | undefined,
  revisions: PortableCheckpointSources['revisions'],
  settings: {
    version?: SettingsVersion
    sourceRevisions: PortableCheckpointSources['sourceRevisions']
    nativeByAddress: ReadonlyMap<string, ChapterRevision>
  },
) {
  const chapterById = new Map(checkpoint.chapters.map((row) => [row.id, row]))
  if (settings.version) {
    if (settings.version.project_version_id !== checkpoint.shared.project_id) invalid()
    for (const { evidence } of Object.values(settings.version.origins)) {
      if (!evidence) continue
      const revision =
        settings.sourceRevisions.get(evidence.revision_id) ?? revisions.get(evidence.revision_id)
      const captured = chapterById.get(evidence.chapter_id)
      if (
        !revision ||
        !captured ||
        revision.chapter_id !== captured.id ||
        revision.revision > captured.revision ||
        settings.nativeByAddress.get(`${captured.id}:${revision.revision}`)?.sha256 !== revision.sha256
      )
        invalid()
      await verifyStoryEvidence(evidence, { id: evidence.revision_id, ...revision })
    }
  }
  verifyTargetScopes(sources.length_targets, chapterById, new Set(outline?.plan.volumes.map(({ id }) => id)))
  const heads = new Map(Object.entries(sources.story_heads))
  const versions = new Map(story?.versions.map((row) => [row.id, row]))
  const selectedStories = new Map<string, StoryVersion>()
  for (const [recordId, head] of heads) {
    const version = versions.get(head.version_id)
    const history =
      story && Object.hasOwn(story.native_history, recordId) ? story.native_history[recordId] : undefined
    if (
      !version ||
      version.record_id !== recordId ||
      !history ||
      !history.some((row) => row.revision === head.revision && row.version_id === head.version_id)
    )
      invalid()
    selectedStories.set(recordId, version)
    for (const row of history) {
      if (row.revision > head.revision) break
      if (
        Object.entries(row.dependencies).some(([id, revision]) => (heads.get(id)?.revision ?? -1) < revision)
      )
        invalid()
      for (const id of [...row.evidence.record, ...row.evidence.stages.flat()]) {
        const revision = revisions.get(id)
        const captured = revision && chapterById.get(revision.chapter_id)
        if (!revision || !captured || revision.revision > captured.revision) invalid()
      }
    }
  }
  validateStoryEffectiveGraph(selectedStories)
  if (sources.outline_live) {
    if (
      !outline ||
      outline.project_version_id !== checkpoint.shared.project_id ||
      (outline.settings_version_id !== null && outline.settings_version_id !== checkpoint.shared.settings_id)
    )
      invalid()
    for (const window of outline.plan.promises) {
      const version = selectedStories.get(window.promise_id)
      if (!version || version.archived || version.record.kind !== 'promise') invalid()
    }
  }
  await verifyFacts(sources.facts, (evidence) => {
    const chapter = chapterById.get(evidence.chapter_id)
    const version = revisions.get(evidence.revision_id)
    return chapter?.revision_id === evidence.revision_id && version
      ? { id: evidence.revision_id, ...version }
      : undefined
  })
}

function verifyTargetScopes(
  targets: PortableCheckpointLengthTarget[],
  chapters: { has: (id: string) => boolean },
  volumes: ReadonlySet<string>,
) {
  const targetKeys = new Set<string>()
  for (const target of targets) {
    const { scope, scope_id } = target
    const key = lengthTargetKey(target)
    if (
      targetKeys.has(key) ||
      (scope === 'chapter' && !chapters.has(scope_id!)) ||
      (scope === 'volume' && !volumes.has(scope_id!))
    )
      invalid()
    targetKeys.add(key)
  }
}

async function verifyFacts(
  facts: PortableCheckpointFact[],
  head: (evidence: SettingsEvidence) => (ChapterRevision & { id: string }) | undefined,
) {
  for (const { evidence } of facts) {
    const quote = await verifyStoryEvidence(evidence, head(evidence))
    if ([...quote].length > 2000) invalid()
  }
}
