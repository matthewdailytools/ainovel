import 'fake-indexeddb/auto'
import { expect, it } from 'vitest'
import { buildCheckpointFork, previewCheckpointFork } from './offlineCheckpointFork'
import {
  importLocalManuscript,
  manuscriptHash,
  readLocalOutline,
  validateManuscript,
  type LocalRevision,
  type ManuscriptPack,
} from './offlineStore'
import { outlineCardFromReference, outlinePromiseSeed, type OutlineVersion } from './outlineSyncModel'
import type { PortableCheckpoint } from './portableCheckpoint'
import { blankProjectBrief } from './projectSyncModel'
import type { ArchivedReferenceCard } from './referenceArchive'
import { prepareSettingsVersion, settingCategories } from './settingsSyncModel'
import { buildStoryVersion, captureStoryEvidence, compactJSON } from './storySyncModel'

const digest = manuscriptHash
const branch = {
  id: 'origin',
  novel_id: 'origin-book',
  name: '主线',
  mode: 'advisory',
  story_revision: 8,
  created: 0,
}

async function chapterRevision(id: string, chapter_id: string, content: string, parent?: LocalRevision) {
  return {
    id,
    chapter_id,
    content,
    sha256: await digest(content),
    cause: '保存原稿',
    created: 0,
    revision: (parent?.revision ?? -1) + 1,
    parent_id: parent?.id ?? null,
  }
}

async function seal(checkpoint: PortableCheckpoint) {
  const body = JSON.stringify(checkpoint)
  return { id: checkpoint.id, sha256: await digest(body), body }
}

async function fixture() {
  const first = await chapterRevision('prior', 'first', '')
  const saved = await chapterRevision('saved', 'first', '桥畔😀响起钟声。', first)
  const later = await chapterRevision('later', 'first', '作者随后更改了事件。', saved)
  const outside = await chapterRevision('outside', 'second', '第二卷原稿。')
  const added = await chapterRevision('added', 'third', '检查点之后新增。')
  const alias = { ...saved, id: 'source-alias' }
  const evidence = await captureStoryEvidence(alias, 2, 4)
  const projects = ['project-parent', 'project-saved', 'project-after'].map((id, index, rows) => ({
    id,
    parent_id: rows[index - 1] ?? null,
    idea: `立项 ${index}`,
    brief: {
      ...blankProjectBrief,
      position: '河岸的信号',
      track: '悬疑',
      audience: '解谜读者',
      distinction: '钟声记录案发顺序',
      length: '十万字',
      ending: '寻到钟声来源',
      genre_ids: ['mystery'],
    },
  }))
  const categories = settingCategories.map(([id, title]) => ({
    id,
    title,
    items: id === 'world' ? [{ id: 'bell', title: '钟声', detail: '桥头钟声为共同信号。' }] : [],
  }))
  const previousSettings = await prepareSettingsVersion({
    id: 'settings-parent',
    parent_id: null,
    categories,
    project_version_id: 'project-parent',
    origins: { bell: { origin: 'author', project_version_id: 'project-parent', evidence } },
  })
  const settings = await prepareSettingsVersion({
    ...previousSettings,
    id: 'settings-saved',
    parent_id: previousSettings.id,
    project_version_id: 'project-saved',
    reconciled_project_items: ['bell'],
  })
  const excerpt = '原文😀'
  const initialCard: Omit<ArchivedReferenceCard, 'id'> = {
    parent_id: null,
    session_id: 'session',
    card_id: 'card',
    revision: 1,
    assessment: 'incomplete',
    card: null,
    citation: null,
    goal: null,
    missing_fields: ['exceptions'],
  }
  const archivedCard = { ...initialCard, id: await digest(compactJSON([branch.id, initialCard])) }
  const ready: typeof initialCard = {
    ...initialCard,
    parent_id: archivedCard.id,
    revision: 2,
    assessment: 'ready',
    missing_fields: [],
    goal: '体会线索推进',
    card: {
      observation: '仅出现部分证据',
      interpretation: '引出下一步',
      technique: '局部收获',
      conditions: '调查仍有线索',
      exceptions: '替换人物与道具',
    },
    citation: {
      title: '参考资料',
      book_id: 'reference',
      unit_id: 'unit',
      file_sha256: await digest(excerpt),
      selection_sha256: await digest(excerpt),
      evidence_sha256: await digest(excerpt),
      selection_start: 0,
      selection_end: excerpt.length,
      evidence_start: 0,
      evidence_end: excerpt.length,
    },
  }
  const card = { ...ready, id: await digest(compactJSON([branch.id, ready])) }
  const summary = outlineCardFromReference(card)!
  const event = await buildStoryVersion(branch.id, {
    parent_id: null,
    record_id: 'event',
    archived: false,
    record: { kind: 'event', title: '钟响', status: 'actual', story_order: 1 },
    evidence: { record: [evidence], stages: [] },
    dependencies: {},
  })
  const seed = outlinePromiseSeed('钟声意味着什么', '')
  const promise = await buildStoryVersion(branch.id, {
    parent_id: null,
    record_id: 'promise',
    archived: false,
    record: seed,
    evidence: { record: [], stages: [[]] },
    dependencies: {},
  })
  const planted = await buildStoryVersion(branch.id, {
    parent_id: promise.id,
    record_id: 'promise',
    archived: false,
    record: { ...seed, stages: [...seed.stages, { status: 'planted', event_id: 'event', note: '' }] },
    evidence: { record: [], stages: [[], [evidence]] },
    dependencies: { event: event.id },
  })
  const advanced = await buildStoryVersion(branch.id, {
    parent_id: planted.id,
    record_id: 'promise',
    archived: false,
    record: {
      ...seed,
      title: '检查点以后改写',
      stages: [...seed.stages, { status: 'planted', event_id: 'event', note: '' }],
    },
    evidence: { record: [], stages: [[], [evidence]] },
    dependencies: { event: event.id },
  })
  const { session_id, card_id, revision, version_id } = summary
  const plannedChapter = (chapter_id: string) => ({
    chapter_id,
    goal: '记录钟声',
    obstacle: '河岸太暗',
    turn: '发现钟楼',
    result: '记下位置',
    setting_ids: [],
    reference_cards: [{ session_id, card_id, revision, version_id }],
  })
  const outline: OutlineVersion = {
    id: 'outline-saved',
    parent_id: 'outline-parent',
    project_version_id: 'project-saved',
    settings_version_id: settings.id,
    reference_cards: [summary],
    promise_sources: { promise: seed },
    plan: {
      summary: '调查钟声来源。',
      volumes: ['first', 'second'].map((id, index) => ({
        id: `volume-${index}`,
        title: '卷 ' + index,
        summary: '记录河岸的信号。',
        units: [
          {
            id: `unit-${index}`,
            title: '查证',
            summary: '记录信号发生的位置。',
            chapters: [plannedChapter(id)],
          },
        ],
      })),
      promises: [
        {
          promise_id: 'promise',
          plant_chapter_id: 'first',
          opens_chapter_id: 'first',
          closes_chapter_id: 'second',
        },
      ],
    },
  }
  const counterpart = {
    ...outline,
    id: 'outline-parent',
    parent_id: null,
    project_version_id: 'project-parent',
    settings_version_id: previousSettings.id,
  }
  const chapter = (record: LocalRevision, index: number) => ({
    id: record.chapter_id,
    branch_id: branch.id,
    title: `章 ${index}`,
    revision: record.revision,
    position: index + 1,
    archived: 0 as const,
    stale: 0 as const,
  })
  const before = [saved, outside]
    .map((row, index) => ({
      ...chapter(row, index),
      revision_id: row.id,
      sha256: row.sha256,
    }))
    .map(({ branch_id: _branch, ...row }) => row)
  const checkpoint: PortableCheckpoint = {
    version: 'ainovel-portable-checkpoint/1',
    id: 'checkpoint',
    branch_id: branch.id,
    label: '冻结第一卷',
    scope: 'volume',
    scope_id: 'volume-0',
    purpose: 'manual',
    created: 1,
    source_manifest_sha256: await digest('capture'),
    story_revision: 5,
    scoped_chapter_ids: ['first'],
    chapters: before,
    shared: { project_id: 'project-saved', settings_id: settings.id, outline_id: outline.id },
    reports: {},
    restore_sources: {
      version: 'ainovel-portable-restore-sources/1',
      outline_live: true,
      scene_spans_for_review: 2,
      story_heads: {
        event: { revision: 0, version_id: event.id },
        promise: { revision: 1, version_id: planted.id },
      },
      facts: [
        {
          subject: '钟声',
          kind: 'event',
          statement: '桥畔响起钟声。',
          truth: 'fact',
          story_time: '昨夜',
          evidence: { ...evidence, revision_id: saved.id },
        },
      ],
      length_targets: [
        {
          scope: 'chapter',
          scope_id: 'first',
          targets: { min_chars: 10, max_chars: 100, expected_chapters: null },
        },
        { scope: 'volume', scope_id: 'volume-1', targets: null },
      ],
    },
  }
  const native = (version_id: string, revision: number, record: string[], stages: string[][]) => ({
    version_id,
    revision,
    created: revision,
    evidence: { record, stages },
  })
  const pack: ManuscriptPack = {
    format: 'ainovel-manuscript/1',
    novel: { id: branch.novel_id, title: '原稿', brief: '活跃的创作思路', created: 0 },
    branch,
    chapters: [later, outside, added].map(chapter),
    revisions: [first, saved, later, outside, added],
    source_revisions: [alias],
    snapshot_history: { entries: [await seal(checkpoint)] },
    project_history: { adopted_revision_id: 'project-after', versions: projects },
    settings_history: { adopted_revision_id: settings.id, versions: [previousSettings, settings] },
    outline_history: { adopted_revision_id: outline.id, versions: [counterpart, outline] },
    reference_history: {
      adopted_heads: [{ session_id: 'session', card_id: 'card', revision_id: card.id }],
      versions: [archivedCard, card],
      source_devices: {},
      excerpts: { [card.id]: excerpt },
    },
    story_history: {
      adopted_heads: { event: event.id, promise: advanced.id },
      versions: [event, promise, planted, advanced],
      source_devices: {},
      native_history: {
        event: [{ ...native(event.id, 0, [saved.id], []), dependencies: {} }],
        promise: [
          { ...native(promise.id, 0, [], [[]]), dependencies: {} },
          { ...native(planted.id, 1, [], [[], [saved.id]]), dependencies: { event: 0 } },
          { ...native(advanced.id, 2, [], [[], [saved.id]]), dependencies: { event: 0 } },
        ],
      },
    },
  }
  return { pack, checkpoint, card, saved, later }
}

function fork(pack: ManuscriptPack, checkpoint: PortableCheckpoint) {
  let index = 0
  return buildCheckpointFork(pack, checkpoint, '卷档另存', {
    validate: validateManuscript,
    digest,
    id: () => `fork-${++index}`,
    now: () => 50,
  })
}

it('copies the frozen whole-book context of a volume point with new native identities and exact formal lineage', async () => {
  const { pack, checkpoint, card, saved, later } = await fixture()
  const original = JSON.stringify(pack)
  await expect(previewCheckpointFork(pack, checkpoint, validateManuscript)).resolves.toEqual({
    chapterCount: 2,
    storyRecordCount: 2,
    outline: 'confirmed',
    sceneSpansForReview: 2,
    blockers: [],
  })
  const result = await fork(pack, checkpoint)
  const copy = result.pack
  const [first, second] = copy.chapters
  expect(copy.chapters.map(({ position, revision }) => [position, revision])).toEqual([
    [1, 1],
    [2, 0],
  ])
  expect(copy.revisions.map(({ content }) => content)).toEqual(['', saved.content, '第二卷原稿。'])
  expect(copy.revisions[1].parent_id).toBe(copy.revisions[0].id)
  expect(first.id).not.toBe(saved.chapter_id)
  expect(JSON.stringify(copy)).not.toContain(later.content)
  expect(result.provenance).toEqual({
    sourceBranchId: branch.id,
    checkpointId: checkpoint.id,
    scope: 'volume',
    scopeId: 'volume-0',
    sourceManifestSha256: checkpoint.source_manifest_sha256,
    sceneSpansForReview: 2,
  })
  expect(JSON.stringify(pack)).toBe(original)
  expect(copy.source_revisions).toBeUndefined()
  expect(copy.snapshot_history).toBeUndefined()
  expect(copy.novel.brief).toBe('立项 1')
  expect(copy.project_history?.versions.map(({ idea }) => idea).sort()).toEqual(['立项 0', '立项 1'])
  const activeSettings = copy.settings_history?.versions.find(
    ({ id }) => id === copy.settings_history?.adopted_revision_id,
  )
  expect(activeSettings?.origins.bell.evidence).toMatchObject({
    chapter_id: first.id,
    revision_id: copy.revisions[1].id,
  })
  expect(copy.settings_history?.versions.find(({ id }) => id === activeSettings?.parent_id)).toBeDefined()
  const active = copy.outline_history?.versions.find(
    ({ id }) => id === copy.outline_history?.adopted_revision_id,
  )
  expect(active?.plan.promises[0]).toMatchObject({ plant_chapter_id: first.id, closes_chapter_id: second.id })
  expect(active?.reference_cards[0].version_id).not.toBe(card.id)
  expect(copy.reference_history?.versions.map(({ revision }) => revision)).toEqual([1, 2])
  expect(copy.reference_history?.excerpts).toBeUndefined()
  const story = copy.story_history!
  const event = story.versions.find(({ record }) => record.kind === 'event')!
  const head = story.versions.find(
    ({ id }) => id === story.adopted_heads[active!.plan.promises[0].promise_id],
  )!
  expect(story.versions).toHaveLength(3)
  expect(head.dependencies).toEqual({ [event.record_id]: event.id })
  expect(head.evidence.stages[1][0]).toMatchObject({
    chapter_id: first.id,
    revision_id: copy.revisions[1].id,
  })
  expect(copy.restore_state?.facts[0].evidence).toMatchObject({
    chapter_id: first.id,
    revision_id: copy.revisions[1].id,
  })
  expect(
    copy.restore_state?.length_targets.map(({ scope_id, targets }) => [scope_id, targets?.max_chars ?? null]),
  ).toEqual([
    [first.id, 100],
    ['volume-1', null],
  ])
  const restored = await importLocalManuscript(new Blob([JSON.stringify(copy)]))
  expect((await readLocalOutline(restored.novel.id)).current?.version.id).toBe(active?.id)
})

it('keeps a non-live formal outline archived on a book fork and rejects detached or read-only points', async () => {
  const { pack, checkpoint } = await fixture()
  const book = {
    ...checkpoint,
    id: 'book',
    scope: 'book' as const,
    scope_id: null,
    scoped_chapter_ids: ['first', 'second'],
    restore_sources: { ...checkpoint.restore_sources!, outline_live: false },
  }
  const archived = { ...book, id: 'archived', restore_sources: undefined }
  pack.snapshot_history = { entries: await Promise.all([seal(book), seal(archived)]) }
  const result = await fork(pack, book)
  expect(result.pack.outline_history).toMatchObject({ adopted_revision_id: null })
  expect(result.pack.outline_history?.versions).toHaveLength(2)
  await expect(fork(pack, { ...book, label: '未保存修改' })).rejects.toThrow('当前作品归档')
  await expect(fork(pack, archived)).rejects.toThrow('仅供归档查看')
  await expect(previewCheckpointFork(pack, archived, validateManuscript)).resolves.toMatchObject({
    blockers: [expect.stringContaining('仅供归档查看')],
  })
})
