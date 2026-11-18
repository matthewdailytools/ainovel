import {
  coversInspectionExcerpt,
  maxAuthorEvidence,
  maxSoftDeviations,
  softDeviationPolicy,
  softDeviations,
} from './inspectionEvidence'
import type { SettingUseVersion } from './settingUseSyncModel'
import { parseSettingsEvidence, type SettingsVersion } from './settingsSyncModel'
import { compactJSON, verifyStoryEvidence } from './storySyncModel'

type FrozenRevision = { id: string; chapter_id: string; revision: number; sha256: string; content: string }
type Digest = (text: string) => Promise<string>
type ChapterBinding = { id: string; revision: number; revision_id: string; sha256: string }
type ChapterManifest = Omit<ChapterBinding, 'revision_id'> & { title: string; position: number }
type Source = { chapter_id: string; revision: number; start: number; end: number; quote: string }

const statuses = ['green', 'yellow', 'red', 'pending', 'not_applicable', 'outdated'] as const
const completionChecks = ['manuscript', 'ending', 'characters', 'promises', 'inspection', 'delivery'] as const
export const deliveryFiles = ['正文.md', '设定册.md', '简介.md', '试读.md'] as const
type DeliveryFile = (typeof deliveryFiles)[number]
const trimArchiveText = (value: string) =>
  value.replace(/^[\p{White_Space}\x1c-\x1f]+|[\p{White_Space}\x1c-\x1f]+$/gu, '')

function invalid(message: string): never {
  throw new Error(message)
}

function record(value: unknown, keys?: readonly string[]) {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    return invalid('稿件包历史记录的格式无效。')
  const data = value as Record<string, unknown>
  if (keys && Object.keys(data).some((key) => !keys.includes(key)))
    invalid('稿件包历史记录包含无法识别的字段。')
  return data
}

function list(value: unknown, maximum: number, minimum = 0) {
  if (!Array.isArray(value) || value.length < minimum || value.length > maximum)
    return invalid('稿件包历史记录列表无效。')
  return value as unknown[]
}

function text(value: unknown, maximum: number, minimum = 0) {
  if (typeof value !== 'string' || value.length > maximum * 2 || /\p{Surrogate}/u.test(value))
    return invalid('稿件包历史记录文本无效。')
  const length = Array.from(value).length
  if (length < minimum || length > maximum) invalid('稿件包历史记录文本超出允许范围。')
  return value
}

function id(value: unknown) {
  const identifier = text(value, 128, 1)
  if (!/^[a-z0-9_-]+$/i.test(identifier)) invalid('稿件包历史记录编号无效。')
  return identifier
}

const optionalId = (value: unknown) => (value == null ? null : id(value))

function hash(value: unknown) {
  if (typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value))
    return invalid('稿件包历史记录的 SHA-256 无效。')
  return value
}

function integer(value: unknown, minimum = 0) {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < minimum)
    return invalid('稿件包历史记录包含无效的数字。')
  return value
}

export function inspectionSettingUses(basis: Record<string, unknown>) {
  if (!Object.hasOwn(basis, 'author_setting_uses')) return []
  return list(basis.author_setting_uses, 200000).map((entry) => {
    const row = record(entry, [
      'version_id',
      'outline_version_id',
      'settings_version_id',
      'item_id',
      'title',
      'revision',
      'chapter_sha256',
      'evidence',
    ])
    return {
      version_id: hash(row.version_id),
      outline_version_id: id(row.outline_version_id),
      settings_version_id: id(row.settings_version_id),
      item_id: id(row.item_id),
      title: text(row.title, 200, 1),
      revision: integer(row.revision),
      chapter_sha256: hash(row.chapter_sha256),
      evidence: parseSettingsEvidence(row.evidence),
    }
  })
}

type InspectionSettingUse = ReturnType<typeof inspectionSettingUses>[number]
type InspectionProofs = ReadonlyMap<string, { version: SettingUseVersion; settings: SettingsVersion }>

export async function verifyInspectionSettingUse(
  use: InspectionSettingUse,
  original: FrozenRevision | undefined,
  digest: Digest,
) {
  if (
    original?.id !== use.evidence.revision_id ||
    original.chapter_id !== use.evidence.chapter_id ||
    original.revision !== use.revision ||
    original.sha256 !== use.chapter_sha256 ||
    (await digest(original.content)) !== use.chapter_sha256
  )
    return invalid('缺少确切的设定正文使用原稿修订。')
  const quote = await verifyStoryEvidence(use.evidence, original)
  if (!trimArchiveText(quote)) invalid('报告的设定正文使用选区与原稿不一致。')
  return quote
}

function stamp(value: unknown) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0)
    return invalid('稿件包历史记录日期无效。')
  return value
}

function flag(value: unknown) {
  if (typeof value !== 'boolean') return invalid('稿件包历史记录状态无效。')
  return value
}

function choice<const T extends readonly string[]>(value: unknown, values: T): T[number] {
  if (typeof value !== 'string' || !values.includes(value))
    return invalid('稿件包历史记录包含无效的检查类型。')
  return value
}

function chapterManifest(value: Record<string, unknown>): ChapterManifest {
  return {
    id: id(value.id),
    title: text(value.title, 200, 1),
    position: integer(value.position, 1),
    revision: integer(value.revision),
    sha256: hash(value.sha256),
  }
}

function parseBinding(value: unknown): ChapterBinding {
  const row = record(value, ['id', 'revision', 'revision_id', 'sha256'])
  return {
    id: id(row.id),
    revision: integer(row.revision),
    revision_id: id(row.revision_id),
    sha256: hash(row.sha256),
  }
}

function verifyChapters(
  chapters: ChapterManifest[],
  bindings: ChapterBinding[],
  revisions: ReadonlyMap<string, FrozenRevision>,
) {
  if (chapters.length !== bindings.length || new Set(bindings.map(({ id }) => id)).size !== bindings.length)
    invalid('历史报告的正文清单和来源修订数量不一致。')
  return new Map(
    chapters.map((chapter, index) => {
      const link = bindings[index]
      const revision = revisions.get(link.revision_id)
      if (
        !revision ||
        link.id !== chapter.id ||
        link.revision !== chapter.revision ||
        link.sha256 !== chapter.sha256 ||
        revision.chapter_id !== link.id ||
        revision.revision !== link.revision ||
        revision.sha256 !== link.sha256
      )
        return invalid(`缺少历史报告所引用的准确正文修订 ${link.revision_id}。`)
      return [chapter.id, revision]
    }),
  )
}

function parseSource(value: unknown): Source {
  const row = record(value, ['chapter_id', 'revision', 'start', 'end', 'quote'])
  return {
    chapter_id: id(row.chapter_id),
    revision: integer(row.revision),
    start: integer(row.start),
    end: integer(row.end, 1),
    quote: text(row.quote, 4000, 1),
  }
}

function verifySource(source: Source, chapters: ReadonlyMap<string, FrozenRevision>, label = '作者') {
  const { chapter_id, revision, start, end, quote } = source
  const original = chapters.get(chapter_id)
  if (
    original?.revision !== revision ||
    end <= start ||
    end > original.content.length ||
    original.content.slice(start, end) !== quote ||
    !quote.trim()
  )
    invalid(`历史报告的${label}引文与原稿选区不一致。`)
  return source
}

function verifySources(
  value: unknown,
  chapters: ReadonlyMap<string, FrozenRevision>,
  limit = maxAuthorEvidence,
) {
  return list(value, limit).map((entry) => verifySource(parseSource(entry), chapters))
}

export function inspectionStateConflicts(basis: Record<string, unknown>) {
  if (!Object.hasOwn(basis, 'state_conflicts')) return []
  return list(basis.state_conflicts, 200_000, 1).map((entry) => {
    const group = record(entry, [
      'character_id',
      'category',
      'key',
      'audience',
      'story_order',
      'event_id',
      'sides',
    ])
    const story_order =
      group.story_order === null ? null : integer(group.story_order, -Number.MAX_SAFE_INTEGER)
    const event_id = group.event_id === null ? null : id(group.event_id)
    const sides = list(group.sides, 200_000, 2).map((entry) => {
      const side = record(entry, ['record_id', 'revision', 'title', 'value', 'event_id', 'sources'])
      return {
        record_id: id(side.record_id),
        revision: integer(side.revision),
        title: text(side.title, 200, 1),
        value: text(side.value, 4000, 1),
        event_id: id(side.event_id),
        sources: list(side.sources, 30, 1).map(parseSource),
      }
    })
    if (
      (story_order === null) === (event_id === null) ||
      new Set(sides.map(({ record_id }) => record_id)).size !== sides.length ||
      new Set(sides.map(({ value }) => value)).size < 2 ||
      (event_id && sides.some((side) => side.event_id !== event_id))
    )
      invalid('历史报告第 03 项缺少同一时点下互相矛盾的记录。')
    return {
      character_id: id(group.character_id),
      category: choice(group.category, ['knowledge', 'capability', 'resource', 'injury', 'other']),
      key: text(group.key, 100, 1),
      audience: choice(group.audience, ['author', 'character', 'reader']),
      story_order,
      event_id,
      sides,
    }
  })
}

function verifyStateConflicts(basis: Record<string, unknown>, chapters: ReadonlyMap<string, FrozenRevision>) {
  const groups = inspectionStateConflicts(basis)
  const frozen = new Map(
    list(basis.records, 200_000).map((value) => {
      const row = record(value)
      return [row.id, row]
    }),
  )
  for (const { character_id, category, key, audience, sides } of groups) {
    for (const { record_id, revision, title, value, event_id, sources } of sides) {
      const original = frozen.get(record_id)
      const expected = {
        character_id,
        category,
        key,
        audience,
        revision,
        title,
        value,
        event_id,
        kind: 'state',
        truth: 'actual',
      }
      if (
        !original ||
        Object.entries(expected).some(([field, value]) => original[field] !== value) ||
        !Array.isArray(original.invalid_reasons) ||
        original.invalid_reasons.length !== 0 ||
        !Array.isArray(original.sources)
      )
        return invalid('历史报告第 03 项的冲突与冻结人物状态记录不一致。')
      const excerpts = new Set(original.sources.map((entry) => compactJSON(entry)))
      for (const source of sources) {
        if (!excerpts.has(compactJSON(source))) invalid('历史报告第 03 项的冲突与冻结人物状态记录不一致。')
        verifySource(source, chapters, '第 03 项冲突')
      }
    }
  }
}

function parseAuthorReview(value: unknown, check: string, chapters: ReadonlyMap<string, FrozenRevision>) {
  const review = record(value, [
    'check_id',
    'status',
    'basis_note',
    'reason',
    'evidence',
    'source_candidate_id',
    'author_overrides_candidate',
    'candidate_result',
    'soft_deviation_report_ids',
  ])
  const status = choice(review.status, ['green', 'yellow', 'red', 'not_applicable'])
  const author_overrides_candidate = flag(review.author_overrides_candidate ?? false)
  const source_candidate_id = optionalId(review.source_candidate_id)
  const soft_deviation_report_ids = list(review.soft_deviation_report_ids ?? [], maxSoftDeviations).map(id)
  const evidence = verifySources(
    review.evidence ?? [],
    chapters,
    maxAuthorEvidence + soft_deviation_report_ids.length,
  )
  if (
    review.check_id !== check ||
    ((status === 'green' || status === 'red') && !evidence.length) ||
    (author_overrides_candidate && (status !== 'green' || !source_candidate_id)) ||
    new Set(soft_deviation_report_ids).size !== soft_deviation_report_ids.length
  )
    invalid('历史报告的作者裁定与原检查项不一致。')
  return {
    check_id: check,
    status,
    evidence,
    author_overrides_candidate,
    source_candidate_id,
    basis_note: text(review.basis_note, 2000, 1),
    reason: text(review.reason, 2000, 1),
    ...(soft_deviation_report_ids.length ? { soft_deviation_report_ids } : {}),
    ...(review.candidate_result != null
      ? { candidate_result: choice(review.candidate_result, ['pass', 'fail', 'unknown']) }
      : {}),
  }
}

function lengthTargets(value: unknown) {
  if (value == null) return null
  const targets = record(value, ['min_chars', 'max_chars', 'expected_chapters'])
  const min_chars = targets.min_chars == null ? null : integer(targets.min_chars)
  const max_chars = targets.max_chars == null ? null : integer(targets.max_chars, 1)
  const expected_chapters = targets.expected_chapters == null ? null : integer(targets.expected_chapters, 1)
  if ((min_chars != null && max_chars != null && min_chars > max_chars) || (expected_chapters ?? 0) > 1000)
    invalid('历史报告的篇幅目标无效。')
  return { min_chars, max_chars, expected_chapters }
}

async function parseInspection(
  value: unknown,
  revisions: ReadonlyMap<string, FrozenRevision>,
  digest: Digest,
  proofs: InspectionProofs,
) {
  const entry = record(value, ['id', 'created', 'chapter_bindings', 'report'])
  const chapter_bindings = list(entry.chapter_bindings, 5000).map(parseBinding)
  const report = record(entry.report, [
    'version',
    'scope',
    'scope_id',
    'chapters',
    'length_target',
    'items',
    'basis_sha256',
    'length_targets',
  ])
  const version = choice(report.version, ['m6-inspection/1'])
  const scope = choice(report.scope, ['chapter', 'volume', 'book'])
  const scope_id = optionalId(report.scope_id)
  const chapters = list(report.chapters, 5000).map((entry) => {
    const chapter = record(entry, ['id', 'title', 'position', 'revision', 'sha256', 'chars', 'stale'])
    return { ...chapterManifest(chapter), chars: integer(chapter.chars), stale: flag(chapter.stale) }
  })
  if (
    (scope === 'book') !== (scope_id === null) ||
    (scope === 'chapter' && (chapters.length !== 1 || chapters[0].id !== scope_id)) ||
    (scope === 'volume' && !chapters.length)
  )
    invalid('历史体检的范围与正文清单不一致。')
  const originals = verifyChapters(chapters, chapter_bindings, revisions)
  const items = list(report.items, 10, 10).map((value, index) => {
    const item = record(value, [
      'check_id',
      'title',
      'status',
      'summary',
      'basis',
      'can_mark_not_applicable',
      'basis_sha256',
      'author_review',
    ])
    const check_id = String(index + 1).padStart(2, '0')
    const status = choice(item.status, statuses)
    const basis = record(item.basis)
    const can_mark_not_applicable = flag(item.can_mark_not_applicable)
    if (item.check_id !== check_id || (status === 'not_applicable' && !can_mark_not_applicable))
      invalid('历史体检缺少十处检查或包含未获准的免查。')
    const author_review =
      item.author_review != null ? parseAuthorReview(item.author_review, check_id, originals) : undefined
    if (Object.hasOwn(basis, 'state_conflicts')) {
      if (check_id !== '03' || status !== 'red' || (author_review && author_review.status !== 'red'))
        invalid('有确定的人物状态冲突时，第 03 项须保留红项。')
      verifyStateConflicts(basis, originals)
    }
    const policy = basis.soft_deviation_policy
    const signed = author_review?.soft_deviation_report_ids ?? []
    if (policy != null && (check_id !== '05' || policy !== softDeviationPolicy))
      invalid('历史体检的软设定裁定口径无效。')
    if (policy && status === 'green') {
      const required = softDeviations(basis)
      if (
        author_review?.status !== 'green' ||
        !required?.length ||
        signed.length !== required.length ||
        required.some(
          ({ id, source }) =>
            !signed.includes(id) ||
            !author_review.evidence.some((excerpt) => coversInspectionExcerpt(excerpt, source)),
        )
      )
        invalid('历史体检的软设定签认缺少原偏离记录或对应引文。')
    } else if (signed.length) invalid('历史体检的软设定签认与原检查项不一致。')
    return {
      check_id,
      status,
      can_mark_not_applicable,
      title: text(item.title, 200, 1),
      summary: text(item.summary, 4000),
      basis,
      basis_sha256: hash(item.basis_sha256),
      ...(author_review ? { author_review } : {}),
    }
  })
  const relevant = new Map(chapters.map((chapter) => [chapter.id, chapter]))
  const used = new Set<string>()
  for (const item of items) {
    if (
      !Object.hasOwn(item.basis, 'author_setting_uses') &&
      !Object.hasOwn(item.basis, 'author_setting_use_outline_id')
    )
      continue
    if (item.check_id !== '08') invalid('设定正文使用依据须属于第 08 项。')
    const references = inspectionSettingUses(item.basis)
    if (!references.length) invalid('报告的设定正文使用依据缺少对应的冻结大纲及章纲计划。')
    const outlineId = id(item.basis.author_setting_use_outline_id)
    const plans = new Map(
      list(items[1].basis.plans, 5000).map((row) => {
        const plan = record(row)
        return [id(plan.chapter_id), new Set(list(plan.setting_ids, 200000).map(id))] as const
      }),
    )
    for (const use of references) {
      const chapter = relevant.get(use.evidence.chapter_id)
      const key = `${use.evidence.chapter_id}/${use.item_id}`
      if (used.has(key) || !chapter || chapter.stale || chapter.sha256 !== use.chapter_sha256)
        invalid('报告的设定正文使用依据不在对应章稿清单内。')
      used.add(key)
      const proof = proofs.get(use.version_id)
      if (!proof) return invalid('报告缺少原作者确认的设定使用版本。')
      const { version, settings } = proof
      const title = settings.categories
        .flatMap(({ items }) => items)
        .find(({ id }) => id === version.item_id)?.title
      if (
        version.id !== use.version_id ||
        version.action !== 'confirmed' ||
        use.outline_version_id !== outlineId ||
        !plans.get(use.evidence.chapter_id)?.has(use.item_id) ||
        version.outline_version_id !== use.outline_version_id ||
        version.settings_version_id !== use.settings_version_id ||
        settings.id !== use.settings_version_id ||
        version.item_id !== use.item_id ||
        version.chapter_sha256 !== use.chapter_sha256 ||
        compactJSON(version.evidence) !== compactJSON(use.evidence) ||
        title !== use.title
      )
        invalid('报告的设定正文使用依据与原作者确认不一致。')
      await verifyInspectionSettingUse(use, revisions.get(use.evidence.revision_id), digest)
    }
  }
  const basis_sha256 = hash(report.basis_sha256)
  if (
    basis_sha256 !== (await digest(JSON.stringify([scope, scope_id, items.map((item) => item.basis_sha256)])))
  )
    invalid('历史体检的汇总依据校验失败。')
  return {
    id: id(entry.id),
    created: stamp(entry.created),
    chapter_bindings,
    report: {
      version,
      scope,
      scope_id,
      chapters,
      basis_sha256,
      items,
      length_target: report.length_target == null ? null : record(report.length_target),
      length_targets: lengthTargets(report.length_targets),
    },
  }
}

export type ArchivedInspection = Awaited<ReturnType<typeof parseInspection>>

function parseDeliveryManifest(value: unknown) {
  const row = record(value, [
    'format',
    'branch_id',
    'title',
    'story_revision',
    'chapters',
    'project_id',
    'setting_id',
    'outline_id',
    'inspection_id',
    'length_target',
    'promises',
    'settings_catalogue',
    'ai_preparation_id',
    'files',
    'spoiler_scope',
    'sample_chapter_ids',
  ])
  const format = choice(row.format, ['book-completion/1', 'book-completion/2'])
  const setting_id = optionalId(row.setting_id)
  const settings_catalogue =
    format === 'book-completion/2'
      ? list(row.settings_catalogue, 240, 1).map((value) => {
          const setting = record(value, ['id', 'category_id', 'category_title', 'title', 'detail'])
          return {
            id: id(setting.id),
            category_id: id(setting.category_id),
            category_title: text(setting.category_title, 200, 1),
            title: text(setting.title, 200, 1),
            detail: text(setting.detail, 4000, 1),
          }
        })
      : undefined
  if (
    (format === 'book-completion/1' && 'settings_catalogue' in row) ||
    (settings_catalogue &&
      (!setting_id ||
        settings_catalogue.every(({ category_id }) => category_id === 'world') ||
        settings_catalogue.some(({ detail }) => !trimArchiveText(detail)) ||
        new Set(settings_catalogue.map(({ id }) => id)).size !== settings_catalogue.length))
  )
    invalid('历史交付的分类设定册或采用修订无效。')
  return {
    format,
    branch_id: id(row.branch_id),
    title: text(row.title, 200, 1),
    story_revision: integer(row.story_revision),
    chapters: list(row.chapters, 5000, 1).map((value) =>
      chapterManifest(record(value, ['id', 'title', 'position', 'revision', 'sha256'])),
    ),
    project_id: optionalId(row.project_id),
    setting_id,
    outline_id: optionalId(row.outline_id),
    inspection_id: id(row.inspection_id),
    length_target: row.length_target == null ? null : record(row.length_target),
    promises: list(row.promises, 5000).map((value) => {
      const promise = record(value, ['id', 'revision'])
      return { id: id(promise.id), revision: integer(promise.revision) }
    }),
    ...(settings_catalogue ? { settings_catalogue } : {}),
    ...(row.ai_preparation_id != null ? { ai_preparation_id: id(row.ai_preparation_id) } : {}),
    files: deliveryRecord(row.files, hash),
    spoiler_scope: choice(row.spoiler_scope, ['opening', 'whole']),
    sample_chapter_ids: list(row.sample_chapter_ids, 20, 1).map(id),
  }
}

function deliveryRecord<T>(value: unknown, parse: (value: unknown) => T): Record<DeliveryFile, T> {
  const files = record(value, deliveryFiles)
  return {
    '正文.md': parse(files['正文.md']),
    '设定册.md': parse(files['设定册.md']),
    '简介.md': parse(files['简介.md']),
    '试读.md': parse(files['试读.md']),
  }
}

function parseCompletionReview(value: unknown, chapters: ReadonlyMap<string, FrozenRevision>) {
  const row = record(value, [
    'basis_sha256',
    'read_note',
    'ending',
    'characters',
    'promises',
    'intro',
    'spoiler_scope',
    'sample_chapter_ids',
    'materials_reviewed',
    'preparation_id',
  ])
  const closure = (value: unknown) => {
    const note = record(value, ['note', 'sources'])
    const source = verifySources(note.sources ?? [], chapters)
    return { note: text(note.note, 4000, 1), sources: source }
  }
  return {
    basis_sha256: hash(row.basis_sha256),
    read_note: text(row.read_note, 4000, 1),
    ending: closure(row.ending),
    characters: closure(row.characters),
    promises: closure(row.promises),
    intro: text(row.intro, 4000, 1),
    spoiler_scope: choice(row.spoiler_scope, ['opening', 'whole']),
    sample_chapter_ids: list(row.sample_chapter_ids, 20, 1).map(id),
    materials_reviewed: flag(row.materials_reviewed),
    preparation_id: optionalId(row.preparation_id),
  }
}

async function parseCompletion(
  value: unknown,
  branchId: string,
  reports: ReadonlyMap<string, ArchivedInspection>,
  revisions: ReadonlyMap<string, FrozenRevision>,
  digest: Digest,
  reconstructMaterials = false,
) {
  const entry = record(value, [
    'id',
    'created',
    'chapter_bindings',
    'basis_sha256',
    'checks',
    'manifest',
    'materials',
    'review',
  ])
  const chapter_bindings = list(entry.chapter_bindings, 5000, 1).map(parseBinding)
  const basis_sha256 = hash(entry.basis_sha256)
  const manifest = parseDeliveryManifest(entry.manifest)
  const chapters = verifyChapters(manifest.chapters, chapter_bindings, revisions)
  const review = parseCompletionReview(entry.review, chapters)
  const checks = list(entry.checks, 6, 6).map((value, index) => {
    const check = record(value, ['id', 'title', 'status', 'detail'])
    if (check.id !== completionChecks[index] || check.status !== 'green')
      invalid('历史完本缺少原六项通过记录。')
    return {
      id: completionChecks[index],
      status: 'green' as const,
      title: text(check.title, 200, 1),
      detail: text(check.detail, 100_000),
    }
  })
  const report = reports.get(manifest.inspection_id)
  if (
    manifest.branch_id !== branchId ||
    basis_sha256 !== review.basis_sha256 ||
    report?.report.scope !== 'book' ||
    JSON.stringify(report.chapter_bindings) !== JSON.stringify(chapter_bindings) ||
    report.report.items.some(({ status }) => status !== 'green' && status !== 'not_applicable')
  )
    return invalid('历史完本缺少与原章稿对应的通过体检单。')
  const sample = manifest.chapters.slice(0, manifest.sample_chapter_ids.length)
  if (
    !review.read_note.trim() ||
    ![review.ending, review.characters, review.promises].every(({ note }) => note.trim()) ||
    !review.intro.trim() ||
    !review.materials_reviewed ||
    manifest.spoiler_scope !== review.spoiler_scope ||
    (manifest.ai_preparation_id ?? null) !== review.preparation_id ||
    JSON.stringify(manifest.sample_chapter_ids) !== JSON.stringify(review.sample_chapter_ids) ||
    JSON.stringify(sample.map(({ id }) => id)) !== JSON.stringify(review.sample_chapter_ids)
  )
    invalid('历史完本的作者复核和试读范围不一致。')
  const body = (rows: ChapterManifest[]) =>
    rows.map(({ id, title }) => `## ${title}\n\n${chapters.get(id)?.content ?? ''}`).join('\n\n')
  const ruleBasis = report.report.items[4].basis
  const rules = list(ruleBasis.rules, 240).map((value) => record(value))
  const { settings_catalogue, ai_preparation_id, files, spoiler_scope, sample_chapter_ids, ...core } =
    manifest
  if (settings_catalogue) {
    const originalWorld = rules.map((rule) => [id(rule.id), rule.title, rule.detail])
    const catalogueWorld = settings_catalogue
      .filter(({ category_id }) => category_id === 'world')
      .map(({ id, title, detail }) => [id, title, detail])
    if (
      manifest.setting_id !== ruleBasis.confirmation_id ||
      JSON.stringify(originalWorld) !== JSON.stringify(catalogueWorld) ||
      basis_sha256 !==
        (await digest(
          JSON.stringify([
            { ...core, settings_catalogue },
            report.report.chapters.map(({ stale }) => Number(stale)),
            ruleBasis.outline_stale,
            report.report.items.map(({ status }) => status),
          ]),
        ))
    )
      invalid('历史交付的分类设定册与原体检修订或冻结依据不符。')
  }
  const catalogue = settings_catalogue
    ? settings_catalogue.map(
        ({ category_title, title, detail }) => `## ${category_title} · ${title}\n\n${detail}`,
      )
    : rules.map((rule) => `## ${text(rule.title, 200, 1)}\n\n${text(rule.detail, 4000)}`)
  const workTitle = manifest.title
  const spoiler = review.spoiler_scope === 'opening' ? '仅开篇' : '含全书内容'
  const expected: Record<DeliveryFile, string> = {
    '正文.md': `# ${workTitle}\n\n${body(manifest.chapters)}\n`,
    '设定册.md': `# ${workTitle} · 设定册\n\n${catalogue.join('\n\n') || '本作没有单独确认的世界设定条目。'}\n`,
    '简介.md': `# ${workTitle} · 简介\n\n剧透范围：${spoiler}\n\n${trimArchiveText(review.intro)}\n`,
    '试读.md': `# ${workTitle} · 试读\n\n${body(sample)}\n`,
  }
  const materials =
    reconstructMaterials && entry.materials === undefined
      ? expected
      : deliveryRecord(entry.materials, (content) => text(content, 24 * 1024 * 1024))
  for (const filename of deliveryFiles) {
    if (
      materials[filename] !== expected[filename] ||
      (await digest(materials[filename])) !== manifest.files[filename]
    )
      invalid(`历史交付材料「${filename}」与冻结依据或校验值不符。`)
  }
  return {
    id: id(entry.id),
    created: stamp(entry.created),
    basis_sha256,
    chapter_bindings,
    manifest,
    review,
    checks,
    materials,
  }
}

export type ArchivedCompletion = Awaited<ReturnType<typeof parseCompletion>>
export type WorkflowHistory = { inspections: ArchivedInspection[]; completions: ArchivedCompletion[] }

type WorkflowKind = 'workflow-inspection' | 'workflow-completion'
export type WorkflowSyncObject = {
  [Kind in WorkflowKind]: {
    format: 'ainovel-manuscript-sync/1'
    kind: Kind
    branch_id: string
    entry_json: string
  }
}[WorkflowKind]

export function workflowObjectSignature(object: WorkflowSyncObject) {
  // The archived source keeps Python's original field order. Compare its enclosing
  // values independently from the order JSON objects arrived in each transport.
  const entry = record(JSON.parse(object.entry_json))
  // The files in a compact completion are reconstructed from the same manifest.
  // Each incoming shape is validated before being stored.
  if (object.kind === 'workflow-completion') delete entry.materials
  return JSON.stringify([object.kind, object.branch_id, entry], (_, value) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return value
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, value[key]]),
    )
  })
}

export function workflowSyncEntry(branchId: string, objectId: string, input: unknown) {
  const row = record(input, ['format', 'kind', 'branch_id', 'entry_json'])
  const kind = choice(row.kind, ['workflow-inspection', 'workflow-completion'])
  if (row.format !== 'ainovel-manuscript-sync/1' || row.branch_id !== branchId)
    return invalid('云端历史报告与当前工作版本不符。')
  const entry_json = text(row.entry_json, 1024 * 1024, 1)
  let decoded: unknown
  try {
    decoded = JSON.parse(entry_json)
  } catch {
    return invalid('云端历史报告的档案 JSON 无法读取。')
  }
  const entry = record(decoded)
  if (objectId !== `${kind}:${id(entry.id)}`) return invalid('云端历史报告的来源编号不一致。')
  const report = kind === 'workflow-completion' ? id(record(entry.manifest).inspection_id) : undefined
  const references =
    kind === 'workflow-inspection'
      ? list(record(entry.report).items, 10, 10).flatMap((item) =>
          inspectionSettingUses(record(record(item).basis)),
        )
      : []
  const revisionIds = new Set([
    ...list(entry.chapter_bindings, 5000).map((item) => parseBinding(item).revision_id),
    ...references.map(({ evidence }) => evidence.revision_id),
  ])
  const proofIds = new Set(references.map(({ version_id }) => version_id))
  const object: WorkflowSyncObject = {
    format: 'ainovel-manuscript-sync/1',
    kind,
    branch_id: branchId,
    entry_json,
  }
  return { object, entry, report, revisionIds, proofIds }
}

export async function validateWorkflowSyncEntry(
  data: ReturnType<typeof workflowSyncEntry>,
  report: ArchivedInspection | undefined,
  revisions: ReadonlyMap<string, FrozenRevision>,
  digest: Digest,
  proofs: InspectionProofs,
) {
  if (data.object.kind === 'workflow-inspection')
    return {
      kind: 'workflow-inspection' as const,
      entry: await parseInspection(data.entry, revisions, digest, proofs),
    }
  if (!report || report.id !== data.report) invalid('等待历史完本绑定的全书体检。')
  return {
    kind: 'workflow-completion' as const,
    entry: await parseCompletion(
      data.entry,
      data.object.branch_id,
      new Map([[report.id, report]]),
      revisions,
      digest,
      true,
    ),
  }
}

export async function validateWorkflowHistory(
  value: unknown,
  branchId: string,
  revisions: ReadonlyMap<string, FrozenRevision>,
  digest: Digest,
  proofs: InspectionProofs,
): Promise<WorkflowHistory> {
  const history = record(value, ['inspections', 'completions'])
  const inspections: ArchivedInspection[] = []
  const completions: ArchivedCompletion[] = []
  const used = new Set<string>()
  for (const value of list(history.inspections ?? [], 1000)) {
    const entry = await parseInspection(value, revisions, digest, proofs)
    if (used.has(entry.id)) invalid('稿件包的历史报告编号重复。')
    used.add(entry.id)
    inspections.push(entry)
  }
  const reports = new Map(inspections.map((entry) => [entry.id, entry]))
  for (const value of list(history.completions ?? [], 1000)) {
    const entry = await parseCompletion(value, branchId, reports, revisions, digest)
    if (used.has(entry.id)) invalid('稿件包的历史报告编号重复。')
    used.add(entry.id)
    completions.push(entry)
  }
  return { inspections, completions }
}
