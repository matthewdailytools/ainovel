import type { LocalChapter } from './offlineStore'
import { bodyCountCharacters, bodyCountDecimals, bodyCountWhitespace } from './bodyCountUnicode16'
import {
  validateOutlineActiveBasis,
  validateOutlineVersionSources,
  type OutlineExistingPromise,
  type OutlineVersion,
} from './outlineSyncModel'
import type { ArchivedReferenceCard } from './referenceArchive'
import type { SettingsVersion } from './settingsSyncModel'
import { verifyStoryEvidence, type StoryVersion } from './storySyncModel'
import {
  lengthTargetKey,
  type PortableCheckpointLengthTarget,
  type PortableRestoreState,
} from './portableCheckpoint'

export { bodyCountMethod } from './bodyCountUnicode16'

// Keep the line boundaries, decimal digits and whitespace aligned with workflow_inspection.body_chars.
const formatLine = /^ {0,3}(?:#{1,6}[ \t]+[\s\S]*|(?:[-*_][ \t]*){3,}|(?:\x60{3,}|~{3,})[\s\S]*)$/u
const formatPrefix = new RegExp(
  '^(?: {0,3}(?:>[ \\t]*|[-+*][ \\t]+|[' + bodyCountDecimals + ']+[.)][ \\t]+))+',
  'u',
)
const inlineFormat = new RegExp(
  '(\\*{1,3}|_{1,3}|~~|\x60{1,2})([^' +
    bodyCountWhitespace +
    '][^\\n]*?)(?<![' +
    bodyCountWhitespace +
    '])\\1',
  'gu',
)
const countedChar = new RegExp('[' + bodyCountCharacters + ']', 'u')

export function bodyChars(text: string) {
  const body = text
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .map((line) => (formatLine.test(line) ? '' : line))
    .join('\n')
    .replace(/!\[[^\]\n]*\]\([^\n)]*\)/gu, '')
    .replace(/\[([^\]\n]+)\]\([^\n)]*\)/gu, '$1')
    .split('\n')
    .map((line) => line.replace(formatPrefix, ''))
    .join('\n')
    .replace(inlineFormat, '$2')
  let count = 0
  for (const c of body) if (countedChar.test(c)) count += 1
  return count
}

type RuleChapter = LocalChapter & { content: string; revisionId: string; sha256: string }
type RuleOutline = {
  current?: { version: OutlineVersion }
  versions: { version: OutlineVersion }[]
  projectVersionId: string | null
  settings: SettingsVersion | null
  promises: OutlineExistingPromise[]
  referenceVersions: ArchivedReferenceCard[]
  restorePending?: { reason: string }
}
type RuleStory = { version: StoryVersion; invalidReasons: string[] }
type LengthMeasurements = { chapters: number; chars: number; empty: number }

function measureTarget(target: PortableCheckpointLengthTarget | undefined, measured: LengthMeasurements) {
  if (!target) return {}
  const { targets } = target
  const lengthIssues = []
  if (!measured.chapters || measured.empty) lengthIssues.push('所查范围有缺章或空稿')
  if (targets?.expected_chapters != null && measured.chapters !== targets.expected_chapters)
    lengthIssues.push('章数偏离冻结目标')
  if (
    (targets?.min_chars != null && measured.chars < targets.min_chars) ||
    (targets?.max_chars != null && measured.chars > targets.max_chars)
  )
    lengthIssues.push('正文字符数超出冻结目标')
  return { lengthTarget: targets, lengthIssues }
}

export async function calculateOfflineRules(
  chapters: RuleChapter[],
  outline: RuleOutline,
  records: RuleStory[],
  restoreState?: PortableRestoreState,
) {
  let progress = 0
  const targets = new Map(restoreState?.length_targets.map((row) => [lengthTargetKey(row), row]))
  const active = chapters.filter(({ archived }) => !archived)
  const saved = active.map(({ id, position, title, content, revisionId, revision, stale }) => {
    // Desktop uses SQLite trim(content) on the saved manuscript directory.
    if (/[^ ]/u.test(content)) progress = Math.max(progress, position)
    const chars = bodyChars(content)
    return {
      id,
      position,
      title,
      revisionId,
      revision,
      stale: !!stale,
      chars,
      ...measureTarget(targets.get(`chapter:${id}`), { chapters: 1, chars, empty: chars ? 0 : 1 }),
    }
  })
  const summarize = (entries: typeof saved) => ({
    chapters: entries.length,
    chars: entries.reduce((total, { chars }) => total + chars, 0),
    empty: entries.filter(({ chars }) => !chars).length,
    stale: entries.filter(({ stale }) => stale).length,
  })
  const book = summarize(saved)
  const source = new Map(active.map((row) => [row.id, row]))
  const facts = []
  for (const fact of restoreState?.facts ?? []) {
    const { evidence } = fact
    const head = source.get(evidence.chapter_id)
    if (head?.revisionId !== evidence.revision_id) continue
    const quote = await verifyStoryEvidence(evidence, {
      id: head.revisionId,
      chapter_id: head.id,
      content: head.content,
      sha256: head.sha256,
    })
    facts.push({ ...fact, quote, chapterTitle: head.title, chapterRevision: head.revision })
  }
  const result = {
    chapters: saved,
    book: { ...book, ...measureTarget(targets.get('book:'), book) },
    volumes: [] as ({
      id: string
      title: string
    } & ReturnType<typeof summarize> &
      ReturnType<typeof measureTarget>)[],
    facts,
    promises: [] as {
      id: string
      title: string
      state: 'open' | 'due' | 'review'
      required?: true
      chapter?: { position: number; title: string }
    }[],
    outlineIssue: outline.restorePending?.reason,
    outlineRevision: undefined as string | undefined,
  }
  const version = outline.current?.version
  if (!version) return result
  const directory = new Map(saved.map((row) => [row.id, row]))
  const promises = new Map(outline.promises.map((promise) => [promise.id, promise]))
  const references = new Map(outline.referenceVersions.map((card) => [card.id, card]))
  try {
    validateOutlineActiveBasis(version, {
      projectVersionId: outline.projectVersionId,
      settingsVersionId: outline.settings?.id ?? null,
    })
    await validateOutlineVersionSources(version, {
      project: (id) => id === outline.projectVersionId,
      settings: () => outline.settings,
      outline: (id) => outline.versions.some(({ version }) => version.id === id),
      chapter: (id) => directory.get(id),
      promise: (id) => promises.get(id),
      reference: (id) => references.get(id),
    })
  } catch (error) {
    result.outlineIssue = error instanceof Error ? error.message : '当前大纲的依据待核对。'
    return result
  }
  result.outlineRevision = version.id
  result.volumes = version.plan.volumes.map(({ id, title, units }) => {
    const measured = summarize(
      units.flatMap(({ chapters }) => chapters.map(({ chapter_id }) => directory.get(chapter_id)!)),
    )
    return { id, title, ...measured, ...measureTarget(targets.get(`volume:${id}`), measured) }
  })
  const observed = new Map(records.map((row) => [row.version.record_id, row]))
  for (const window of version.plan.promises) {
    const id = window.promise_id
    const row = observed.get(id)
    const record = row?.version.record
    const seed = version.promise_sources[id]
    const title = record?.title ?? seed?.title ?? id
    const invalid = !!(
      row &&
      (row.invalidReasons.length || row.version.archived || record?.kind !== 'promise')
    )
    const closes = directory.get(window.closes_chapter_id)!
    const opens = directory.get(window.opens_chapter_id)!
    const latest = record?.kind === 'promise' ? record.stages.at(-1) : undefined
    if (window.fulfillment === 'author_required') {
      const fulfilled =
        !invalid &&
        latest?.status === 'resolved' &&
        row?.version.evidence.stages.at(-1)?.some(({ chapter_id }) => {
          const position = directory.get(chapter_id)?.position
          return position !== undefined && position >= opens.position && position <= closes.position
        })
      if (fulfilled) continue
      if (progress >= closes.position)
        result.promises.push({ id, title, state: 'due', chapter: closes, required: true })
      else if (invalid) result.promises.push({ id, title, state: 'review', required: true })
      else if (progress >= opens.position)
        result.promises.push({ id, title, state: 'open', chapter: closes, required: true })
      continue
    }
    if (invalid) {
      result.promises.push({ id, title, state: 'review' })
      continue
    }
    const status = latest?.status ?? seed?.stages[0].status
    if (status !== 'planned' && status !== 'planted' && status !== 'reinforced') continue
    if (progress >= closes.position) result.promises.push({ id, title, state: 'due', chapter: closes })
    else if (progress >= opens.position) result.promises.push({ id, title, state: 'open', chapter: closes })
  }
  return result
}

export type OfflineRules = Awaited<ReturnType<typeof calculateOfflineRules>>
