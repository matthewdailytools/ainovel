import type { OutlineVersion } from './outlineSyncModel'

const version = 'ainovel-portable-checkpoint/1' as const
const maxCheckpoints = 5000
const maxChapters = 5000
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
}

type SealedCheckpoint = { entry: PortableCheckpointEntry; checkpoint: PortableCheckpoint }
type ChapterRevision = { chapter_id: string; revision: number; sha256: string; content: string }
export type PortableCheckpointSources = {
  branch: { id: string; story_revision: number }
  chapterIds: ReadonlySet<string>
  revisions: ReadonlyMap<string, ChapterRevision>
  projects: ReadonlySet<string>
  settings: ReadonlySet<string>
  outlines: ReadonlyMap<string, OutlineVersion>
  inspections: ReadonlySet<string>
  completions: ReadonlySet<string>
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

function text(value: unknown): string {
  if (
    typeof value !== 'string' ||
    !value.trim() ||
    value.length > 400 ||
    [...value].length > 200 ||
    /\p{Surrogate}/u.test(value)
  )
    invalid()
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
    projects,
    settings,
    outlines,
    inspections,
    completions,
  }: PortableCheckpointSources,
  digest: (body: string) => Promise<string>,
) {
  const checkedRevisions = new Set<string>()
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
    if (checkpoint.scope === 'volume') {
      const outline = outline_id && outlines.get(outline_id)
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
  }
}
