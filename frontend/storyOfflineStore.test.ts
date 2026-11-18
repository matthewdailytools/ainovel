import 'fake-indexeddb/auto'
import { expect, it } from 'vitest'
import {
  applySyncedObject,
  confirmLocalStory,
  createLocalChapter,
  createLocalProject,
  exportLocalManuscript,
  manuscriptHash,
  readLocalChapterRestore,
  readLocalHistory,
  readLocalPublications,
  readLocalStory,
  restoreLocalChapter,
  saveLocalChapter,
  syncObjectSignature,
  validateManuscript,
  type LocalRevision,
} from './offlineStore'
import { buildStoryVersion, captureStoryEvidence, parseStoryRecord } from './storySyncModel'

it('作者确认故事和原文依据时原子追加历史及待发，旧选区随正文变更过期', async () => {
  const work = await createLocalProject('故事确认 · 铜铃')
  const chapter = await createLocalChapter(work.novel.id, '第一章')
  const [initial] = await readLocalHistory(work.novel.id, chapter.id)
  const native = await saveLocalChapter(work.novel.id, chapter.id, initial.id, '铃😀响')
  const before = await readLocalStory(work.novel.id)
  const record = parseStoryRecord({ kind: 'event', title: '铜铃响起', status: 'actual' })
  const draft = {
    record,
    record_id: 'bell-event',
    archived: false,
    baseStoryRevision: before.storyRevision,
    baseVersionId: null,
    evidence: { record: [await captureStoryEvidence(native, 1, 3)], stages: [] },
  }
  const saved = await confirmLocalStory(work.novel.id, draft)
  const adopted = await readLocalStory(work.novel.id)
  expect(adopted.records[0]).toMatchObject({
    version: saved,
    native: { evidence: { record: [native.id] } },
    invalidReasons: [],
  })
  expect((await readLocalPublications(work.branch.id)).map(({ objectId }) => objectId)).toEqual(
    expect.arrayContaining(['story:bell-event', `story-version:${saved.id}`]),
  )
  await expect(confirmLocalStory(work.novel.id, draft)).rejects.toThrow('故事版本已变化')
  await saveLocalChapter(work.novel.id, chapter.id, native.id, '铃不再响')
  const stale = await readLocalStory(work.novel.id)
  expect(stale.records[0].invalidReasons).toContain('来源章节已有变化或待复检。')
  await expect(
    confirmLocalStory(work.novel.id, {
      ...draft,
      baseVersionId: saved.id,
      baseStoryRevision: stale.storyRevision,
    }),
  ).rejects.toThrow('引用章节已有新保存版本')
  expect((await readLocalStory(work.novel.id)).history).toHaveLength(1)
})

it('单章恢复预览也绑定本章采用的新故事证据和同步队列', async () => {
  const work = await createLocalProject('恢复预览依据 · 铜铃')
  const projectId = work.novel.id
  const chapter = await createLocalChapter(projectId, '拾铃')
  const [original] = await readLocalHistory(projectId, chapter.id)
  const current = await saveLocalChapter(projectId, chapter.id, original.id, '铃😀响')
  const preview = await readLocalChapterRestore(projectId, chapter.id, original.id)
  expect(preview.references).toEqual([])

  const story = await readLocalStory(projectId)
  await confirmLocalStory(projectId, {
    record: parseStoryRecord({ kind: 'event', title: '铜铃响起', status: 'actual' }),
    record_id: 'bell-event',
    archived: false,
    baseStoryRevision: story.storyRevision,
    baseVersionId: null,
    evidence: { record: [await captureStoryEvidence(current, 1, 3)], stages: [] },
  })
  const withEvent = await readLocalStory(projectId)
  await confirmLocalStory(projectId, {
    record: parseStoryRecord({
      kind: 'promise',
      title: '铃为何响起',
      depends_on: ['bell-event'],
      stages: [{ status: 'planned' }],
    }),
    record_id: 'bell-promise',
    archived: false,
    baseStoryRevision: withEvent.storyRevision,
    baseVersionId: null,
    evidence: { record: [], stages: [[]] },
  })
  const pending = await readLocalPublications(work.branch.id)
  await expect(restoreLocalChapter(projectId, chapter.id, original.id, preview.basis)).rejects.toMatchObject({
    code: 'conflict',
  })
  expect(await readLocalHistory(projectId, chapter.id)).toEqual([original, current])
  expect(await readLocalPublications(work.branch.id)).toEqual(pending)

  const updated = await readLocalChapterRestore(projectId, chapter.id, original.id)
  expect(updated.references).toMatchObject([
    {
      kind: 'story_record',
      id: 'bell-event',
      source: { revision: current.revision, start: 1, end: 3, quote: '😀' },
    },
  ])
  expect(updated.promises).toMatchObject([{ id: 'bell-promise', sources: [], status: 'planned' }])
  const restored = await restoreLocalChapter(projectId, chapter.id, original.id, updated.basis)
  expect(restored).toMatchObject({ content: original.content, parent_id: current.id })
  expect((await readLocalStory(projectId)).records[0].invalidReasons).not.toEqual([])
})

it('远端源稿只有正式采用为同全文原稿后才可激活实际记录，归档保留绑定', async () => {
  const work = await createLocalProject('故事采用 · 渡口')
  const { branch } = work
  const chapter = await createLocalChapter(work.novel.id, '渡口')
  const [initial] = await readLocalHistory(work.novel.id, chapter.id)
  const local = await saveLocalChapter(work.novel.id, chapter.id, initial.id, '本机原稿。')
  const fromDesktop = '手记：铃响😀。'
  const remote: LocalRevision = {
    id: 'desktop-chapter-revision',
    chapter_id: chapter.id,
    parent_id: initial.id,
    revision: 1,
    content: fromDesktop,
    sha256: await manuscriptHash(fromDesktop),
    cause: '桌面作者编辑',
    created: 2,
  }
  const form = 'ainovel-manuscript-sync/1' as const
  const source = { device_id: 'desktop', change_id: 'story-change', sequence: 1 }
  await applySyncedObject(
    work.novel.id,
    `revision:${remote.id}`,
    {
      format: form,
      kind: 'revision',
      branch_id: branch.id,
      revision: remote,
    },
    'null',
    { source },
  )
  const version = await buildStoryVersion(branch.id, {
    parent_id: null,
    record_id: 'bell-event',
    archived: false,
    record: { kind: 'event', title: '渡口确认铃声', status: 'actual' },
    dependencies: {},
    evidence: { record: [await captureStoryEvidence(remote, 3, 5)] },
  })
  await applySyncedObject(
    work.novel.id,
    `story-version:${version.id}`,
    {
      format: form,
      kind: 'story-version',
      branch_id: branch.id,
      version,
    },
    'null',
    { source },
  )
  const head = {
    format: form,
    kind: 'story-head' as const,
    branch_id: branch.id,
    record_id: version.record_id,
    revision_id: version.id,
  }
  await expect(applySyncedObject(work.novel.id, 'story:bell-event', head, 'null', {})).rejects.toThrow(
    '尚未在本机采用',
  )
  const before = {
    format: form,
    kind: 'chapter' as const,
    chapter: { ...chapter, revision: 1 },
    head: local,
  }
  const incoming = { ...before, head: remote }
  expect(
    await applySyncedObject(
      work.novel.id,
      `chapter:${chapter.id}`,
      incoming,
      syncObjectSignature(before),
      {},
    ),
  ).toBe(true)
  expect(await applySyncedObject(work.novel.id, 'story:bell-event', head, 'null', {})).toBe(true)
  const current = (await readLocalHistory(work.novel.id, chapter.id)).at(-1)!
  expect(current.id).not.toBe(remote.id)
  const pack = await exportLocalManuscript(work.novel.id)
  expect(pack.story_history?.native_history['bell-event'][0].evidence.record).toEqual([current.id])
  expect(pack.story_history?.versions[0].evidence.record[0].revision_id).toBe(remote.id)
  await expect(validateManuscript(pack)).resolves.toMatchObject({ story_history: pack.story_history })
})
