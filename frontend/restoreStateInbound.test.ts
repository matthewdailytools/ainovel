import 'fake-indexeddb/auto'
import { IDBFactory } from 'fake-indexeddb'
import { expect, test } from 'vitest'
import {
  applySyncedObject,
  confirmLocalOutline,
  createLocalChapter,
  createLocalProject,
  exportLocalManuscript,
  importLocalManuscript,
  manuscriptHash,
  readLocalHistory,
  readLocalRulePreview,
  readLocalSyncSnapshot,
  saveLocalChapter,
  syncObjectSignature,
  validateSyncedObject,
  type LocalProject,
} from './offlineStore'
import type { OutlineVersion } from './outlineSyncModel'
import {
  buildRestoreStateVersion,
  restoreStateHead,
  restoreStateObjectId,
  type RestoreStateSyncObject,
} from './restoreStateSyncModel'
import { captureStoryEvidence } from './storySyncModel'

async function receive(project: LocalProject, object: RestoreStateSyncObject, expected = 'null') {
  const key = await restoreStateObjectId(object, manuscriptHash)
  const checked = await validateSyncedObject(project, key, object)
  return applySyncedObject(project.novel.id, key, checked, expected)
}

test('historically scoped volume clears adopt after an outline move and nonempty targets still wait', async () => {
  const project = await createLocalProject('历史卷篇幅清除')
  const {
    novel: { id: key },
    branch: { id: branch },
  } = project
  const chapter = await createLocalChapter(key, '渡口问询')
  const volume = (id: string, title: string) => ({
    id,
    title,
    summary: '调查渡口的来客。',
    units: [
      {
        id: `${id}-unit`,
        title: '问询',
        summary: '取得来客的口述。',
        chapters: [
          {
            chapter_id: chapter.id,
            goal: '取得回答',
            obstacle: '来客沉默',
            turn: '发现票根',
            result: '取得线索',
            setting_ids: [],
          },
        ],
      },
    ],
  })
  const original = {
    id: 'old-outline',
    parent_id: null,
    project_version_id: null,
    settings_version_id: null,
    plan: { summary: '从渡口打听人名。', volumes: [volume('old-volume', '首卷')], promises: [] },
    reference_cards: [],
    promise_sources: {},
  } satisfies OutlineVersion
  await confirmLocalOutline(key, null, original)
  const targets = { min_chars: 50, max_chars: null, expected_chapters: null }
  const draft = { scope: 'volume' as const, scope_id: 'old-volume', outline_version_id: original.id }
  const prior = await buildRestoreStateVersion(
    branch,
    {
      kind: 'length-target-version',
      draft: {
        ...draft,
        parent_id: null,
        targets,
      },
    },
    manuscriptHash,
  )
  expect(await receive(project, prior)).toBe(true)
  const previous = restoreStateHead(prior)
  expect(await receive(project, previous)).toBe(true)
  expect((await readLocalRulePreview(key)).volumes[0].lengthTarget).toEqual(targets)

  const replacement = {
    ...original,
    id: 'new-outline',
    parent_id: original.id,
    plan: {
      ...original.plan,
      volumes: [volume('new-volume', '重排卷')],
    },
  }
  await confirmLocalOutline(key, original.id, replacement)
  const version = (outline_version_id: string, values: typeof targets | null) =>
    buildRestoreStateVersion(
      branch,
      {
        kind: 'length-target-version',
        draft: { ...draft, outline_version_id, parent_id: prior.version.id, targets: values },
      },
      manuscriptHash,
    )
  await expect(receive(project, await version('missing-outline', null))).rejects.toMatchObject({
    code: 'missing',
  })
  await expect(receive(project, await version(replacement.id, null))).rejects.toMatchObject({
    code: 'format',
  })
  const waiting = await version(original.id, { ...targets, min_chars: 60 })
  expect(await receive(project, waiting)).toBe(true)
  await expect(
    receive(project, restoreStateHead(waiting), syncObjectSignature(previous)),
  ).rejects.toMatchObject({ code: 'missing' })

  const clear = await version(original.id, null)
  expect(await receive(project, clear)).toBe(true)
  const head = restoreStateHead(clear)
  expect(await receive(project, head, 'null')).toBe(false)
  expect(await receive(project, head, syncObjectSignature(previous))).toBe(true)
  expect((await exportLocalManuscript(key)).restore_state?.length_targets).toEqual([
    { scope: 'volume', scope_id: draft.scope_id, targets: null },
  ])
  const { restoreStateSync, publications } = await readLocalSyncSnapshot(key)
  expect(restoreStateSync.heads).toContainEqual([await restoreStateObjectId(head, manuscriptHash), head])
  expect(restoreStateSync.versions).toContainEqual(waiting)
  expect(publications.filter(({ objectId }) => objectId.startsWith('length-target-'))).toEqual([])
})

test.each([false, true])(
  'historical empty facts need a same-work archived directory, archived=%s',
  async (archived) => {
    const local = await createLocalProject('已归档章节的空事实')
    const chapter = await createLocalChapter(local.novel.id, '旧问询')
    const outside = await createLocalChapter(local.novel.id, '其他章')
    const [root] = await readLocalHistory(local.novel.id, chapter.id)
    const [otherRoot] = await readLocalHistory(local.novel.id, outside.id)
    const old = await saveLocalChapter(local.novel.id, chapter.id, root.id, '😀听见旧铃。')
    const fresh = await saveLocalChapter(local.novel.id, chapter.id, old.id, '后改的稿件。')
    const other = await saveLocalChapter(local.novel.id, outside.id, otherRoot.id, '还有另一位证人。')
    const pack = await exportLocalManuscript(local.novel.id)
    const contextFact = {
      subject: '另一章',
      kind: 'event' as const,
      truth: 'fact' as const,
      statement: '另有证人。',
      story_time: '',
      evidence: await captureStoryEvidence(other, 2, 6),
    }
    const database = globalThis.indexedDB
    globalThis.indexedDB = new IDBFactory()
    try {
      const project = await importLocalManuscript(
        new Blob([
          JSON.stringify({
            ...pack,
            chapters: pack.chapters.map((row) =>
              row.id === chapter.id ? { ...row, archived: Number(archived) } : row,
            ),
            restore_state: { facts: [contextFact], length_targets: [] },
          }),
        ]),
      )
      const incoming = (
        chapter_revision_id: string,
        parent_id: string | null = null,
        facts: (typeof contextFact)[] = [],
      ) =>
        buildRestoreStateVersion(
          project.branch.id,
          {
            kind: 'fact-set-version',
            draft: {
              chapter_id: chapter.id,
              chapter_revision_id,
              parent_id,
              facts,
            },
          },
          manuscriptHash,
        )
      await expect(receive(project, await incoming(other.id))).rejects.toMatchObject({ code: 'format' })
      await expect(receive(project, await incoming('missing-revision'))).rejects.toMatchObject({
        code: 'missing',
      })
      const previous = await incoming(old.id, null, [
        { ...contextFact, evidence: await captureStoryEvidence(old, 2, 5) },
      ])
      const empty = await incoming(old.id, previous.version.id)
      await expect(receive(project, empty)).rejects.toMatchObject({ code: 'missing' })
      expect(await receive(project, previous)).toBe(true)
      await expect(receive(project, restoreStateHead(previous))).rejects.toMatchObject({ code: 'missing' })
      expect(await receive(project, empty)).toBe(true)
      const head = restoreStateHead(empty)
      if (archived) expect(await receive(project, head)).toBe(true)
      else await expect(receive(project, head)).rejects.toMatchObject({ code: 'missing' })

      const snapshot = await readLocalSyncSnapshot(project.novel.id)
      const address = await restoreStateObjectId(head, manuscriptHash)
      expect(snapshot.restoreStateSync.heads.find(([id]) => id === address)).toEqual(
        archived ? [address, head] : undefined,
      )
      expect(snapshot.restoreStateSync.versions).toContainEqual(previous)
      const restored = await exportLocalManuscript(project.novel.id)
      expect(restored.chapters.find(({ id }) => id === chapter.id)).toMatchObject({
        archived: Number(archived),
        revision: fresh.revision,
      })
      expect(restored.restore_state?.facts).toEqual([contextFact])
    } finally {
      globalThis.indexedDB = database
    }
  },
)
