import { beforeEach, expect, test, vi } from 'vitest'
import { IDBFactory, IDBObjectStore } from 'fake-indexeddb'
import {
  createLocalChapter,
  createLocalProject,
  confirmLocalProjectBrief,
  confirmLocalOutline,
  confirmLocalSettings,
  confirmLocalSettingUse,
  confirmLocalStory,
  applySyncedObject,
  createLocalCheckpointBranch,
  exportLocalBackup,
  exportLocalManuscript,
  hasLocalSnapshotHistory,
  importLocalBackup,
  importLocalManuscript,
  listLocalProjects,
  manuscriptHash,
  readLocalChapterRestore,
  readLocalCheckpointRestore,
  readLocalCheckpointFork,
  readLocalPublications,
  readLocalHistory,
  readLocalOutline,
  readLocalRulePreview,
  readLocalProjectBrief,
  readLocalSnapshotHistory,
  readLocalSourceRevision,
  readLocalStory,
  restoreLocalChapter,
  restoreLocalCheckpoint,
  saveLocalChapter,
  validateManuscript,
  validateSyncedObject,
} from './offlineStore'
import { captureRecoveryCheckpoint, checkpointRestoreFingerprint } from './offlineCheckpointRestore'
import { outlinePromiseSeed } from './outlineSyncModel'
import { encodeWebManuscriptBackup } from './webManuscriptBackup'
import {
  decodePortableCheckpointHistory,
  verifyPortableCheckpoints,
  type PortableCheckpoint,
} from './portableCheckpoint'
import { createSettingsDraft, type SettingsVersion } from './settingsSyncModel'
import { captureStoryEvidence, parseStoryRecord } from './storySyncModel'

beforeEach(() => vi.stubGlobal('indexedDB', new IDBFactory()))

const bundle = (value: unknown) => new Blob([JSON.stringify(value)], { type: 'application/json' })

test('formal writes preserve authorship, parent IDs and older revisions across concurrent transactions', async () => {
  const a = await createLocalProject('遗铃')
  const b = await createLocalProject('另一作品')
  const chapter = await createLocalChapter(a.novel.id, '铜铃')
  const [root] = await readLocalHistory(a.novel.id, chapter.id)
  await expect(saveLocalChapter(b.novel.id, chapter.id, root.id, '外部写入')).rejects.toMatchObject({
    code: 'ownership',
  })

  const writes = await Promise.allSettled([
    saveLocalChapter(a.novel.id, chapter.id, root.id, '门内响铃😀'),
    saveLocalChapter(a.novel.id, chapter.id, root.id, '第二窗口的正文'),
  ])
  expect(writes.filter((write) => write.status === 'fulfilled')).toHaveLength(1)
  expect(writes.find((write) => write.status === 'rejected')).toMatchObject({
    reason: { code: 'conflict' },
  })
  const history = await readLocalHistory(a.novel.id, chapter.id)
  expect(history).toHaveLength(2)
  expect(history[1]).toMatchObject({ revision: 1, parent_id: root.id })
  expect(history[1].sha256).toBe(await manuscriptHash(history[1].content))
  const preview = await readLocalChapterRestore(a.novel.id, chapter.id, root.id)
  const restored = await restoreLocalChapter(a.novel.id, chapter.id, root.id, preview.basis)
  expect(restored).toMatchObject({ revision: 2, parent_id: history[1].id, content: root.content })
  expect((await readLocalHistory(a.novel.id, chapter.id)).slice(0, 2)).toEqual(history)
})

test('exchange retains stable IDs and exact UTF-8 text and checks hashes and dangling parents before import', async () => {
  const a = await createLocalProject('遗铃')
  const chapter = await createLocalChapter(a.novel.id, '得到旧铃')
  const [base] = await readLocalHistory(a.novel.id, chapter.id)
  await saveLocalChapter(a.novel.id, chapter.id, base.id, '\ufeff铜铃𠮷😀。\r\n她收好信物。')
  const pack = await exportLocalManuscript(a.novel.id)
  const invalidBody = structuredClone(pack)
  invalidBody.revisions[1].content += '外部篡改'
  await expect(validateManuscript(invalidBody)).rejects.toMatchObject({ code: 'format' })
  const badParent = structuredClone(pack)
  badParent.revisions[1].parent_id = badParent.revisions[1].id
  await expect(validateManuscript(badParent)).rejects.toMatchObject({ code: 'format' })
  const foreignRevision = structuredClone(pack)
  foreignRevision.revisions[1].chapter_id = 'another-chapter'
  await expect(validateManuscript(foreignRevision)).rejects.toMatchObject({ code: 'format' })
  await expect(
    importLocalManuscript(bundle({ ...pack, format: 'ainovel-manuscript/2' })),
  ).rejects.toMatchObject({
    code: 'format',
  })
  await expect(importLocalManuscript(bundle(pack))).rejects.toMatchObject({ code: 'conflict' })
  expect(await exportLocalManuscript(a.novel.id)).toEqual(pack)
  vi.stubGlobal('indexedDB', new IDBFactory())
  await importLocalManuscript(bundle(pack))
  expect(await listLocalProjects()).toEqual([{ novel: pack.novel, branch: pack.branch }])
  expect(await exportLocalManuscript(a.novel.id)).toEqual(pack)
})

test('independent backup rejects truncation, corruption and broken references before any work is installed', async () => {
  const initial = await createLocalProject('遗铃独立备份')
  const chapter = await createLocalChapter(initial.novel.id, '过桥')
  const [base] = await readLocalHistory(initial.novel.id, chapter.id)
  const latest = await saveLocalChapter(
    initial.novel.id,
    chapter.id,
    base.id,
    '她收起铜铃。𠮷😀\r\n再去寻找。',
  )
  const original = await exportLocalManuscript(initial.novel.id)
  const archive = await exportLocalBackup(initial.novel.id)
  const text = await archive.text()
  const [header, first, second, ...rest] = text.split('\n')
  const reordered = new Blob([[header, second, first, ...rest].join('\n')])
  const corrupted = new Blob([text.replace('她收起铜铃', '她放下铜铃')])
  const brokenSource = structuredClone(original)
  brokenSource.revisions[1].parent_id = 'missing-revision'
  const rehashed = await encodeWebManuscriptBackup(brokenSource, manuscriptHash)
  vi.stubGlobal('indexedDB', new IDBFactory())
  const other = await createLocalProject('其他已保存作品')
  for (const invalid of [archive.slice(0, -5), reordered, corrupted, rehashed]) {
    await expect(importLocalBackup(invalid)).rejects.toThrow()
    expect(await listLocalProjects()).toEqual([other])
  }
  await expect(importLocalBackup(archive)).resolves.toEqual({
    novel: original.novel,
    branch: original.branch,
  })
  expect(await exportLocalManuscript(initial.novel.id)).toEqual(original)
  expect((await readLocalHistory(initial.novel.id, chapter.id)).at(-1)).toEqual(latest)
  await expect(importLocalBackup(archive)).rejects.toMatchObject({ code: 'conflict' })
  expect(await exportLocalManuscript(initial.novel.id)).toEqual(original)
})

async function checkpointFixture() {
  const work = await createLocalProject('旧城遗铃')
  const chapter = await createLocalChapter(work.novel.id, '故人说铃')
  const second = await createLocalChapter(work.novel.id, '新城作坊')
  const [firstRoot] = await readLocalHistory(work.novel.id, chapter.id)
  const [secondRoot] = await readLocalHistory(work.novel.id, second.id)
  const original = await saveLocalChapter(work.novel.id, chapter.id, firstRoot.id, '原稿𠮷😀\r\n遗铃在门外。')
  await confirmLocalOutline(work.novel.id, null, {
    id: 'sealed-outline',
    parent_id: null,
    project_version_id: null,
    settings_version_id: null,
    reference_cards: [],
    promise_sources: {},
    plan: {
      summary: '追查旧城遗铃',
      promises: [],
      volumes: [
        {
          id: 'old-city',
          title: '旧城',
          summary: '回查原始铸记',
          units: [
            {
              id: 'old-bell',
              title: '旧铃',
              summary: '沿听来的铃声追查',
              chapters: [
                {
                  chapter_id: chapter.id,
                  goal: '确定铃的来源',
                  obstacle: '铃声失真',
                  turn: '看清刻痕',
                  result: '找到铸造者',
                  setting_ids: [],
                },
              ],
            },
          ],
        },
        {
          id: 'new-city',
          title: '新城',
          summary: '新的铸造册',
          units: [
            {
              id: 'new-bell',
              title: '铸册',
              summary: '调查铸册',
              chapters: [
                {
                  chapter_id: second.id,
                  goal: '找到铸册',
                  obstacle: '账目残缺',
                  turn: '取得副册',
                  result: '说明新城线索',
                  setting_ids: [],
                },
              ],
            },
          ],
        },
      ],
    },
  })
  const captured = await exportLocalManuscript(work.novel.id)
  await saveLocalChapter(work.novel.id, chapter.id, original.id, '正文已更新。')
  const pack = await exportLocalManuscript(work.novel.id)
  const { branch_id, ...first } = captured.chapters[0]
  const { branch_id: _otherBranchId, ...other } = captured.chapters[1]
  const volume: PortableCheckpoint = {
    version: 'ainovel-portable-checkpoint/1',
    id: 'volume-history',
    branch_id,
    label: '检查点 · 旧城😀',
    scope: 'volume',
    scope_id: 'old-city',
    purpose: 'manual',
    created: 1,
    source_manifest_sha256: await manuscriptHash('桌面原清单'),
    story_revision: captured.branch.story_revision,
    scoped_chapter_ids: [chapter.id],
    chapters: [
      { ...first, revision_id: original.id, sha256: original.sha256 },
      { ...other, revision_id: secondRoot.id, sha256: secondRoot.sha256 },
    ],
    shared: { project_id: null, settings_id: null, outline_id: 'sealed-outline' },
    reports: {},
  }
  const book: PortableCheckpoint = {
    ...volume,
    id: 'book-history',
    label: '全书归档',
    scope: 'book',
    scope_id: null,
    purpose: 'milestone',
    created: 2,
    scoped_chapter_ids: [chapter.id, second.id],
  }
  volume.restore_sources = {
    version: 'ainovel-portable-restore-sources/1',
    outline_live: true,
    story_heads: {},
    length_targets: [
      {
        scope: 'volume',
        scope_id: 'old-city',
        targets: { min_chars: 100, max_chars: null, expected_chapters: 1 },
      },
    ],
    scene_spans_for_review: 0,
    facts: [
      {
        subject: '学徒',
        kind: 'event',
        statement: '学徒看到了原先的文字。',
        truth: 'fact',
        story_time: '',
        evidence: {
          chapter_id: chapter.id,
          revision_id: original.id,
          start: 0,
          end: 6,
          quote_sha256: await manuscriptHash(original.content.slice(0, 6)),
        },
      },
    ],
  }
  const snapshot_history = { entries: [await sealCheckpoint(volume), await sealCheckpoint(book)] }
  return { pack: { ...pack, snapshot_history }, volume, book, original, secondRoot }
}

async function sealCheckpoint(
  checkpoint: PortableCheckpoint,
  raw = `${JSON.stringify(checkpoint, null, 2)}\r\n`,
) {
  return { id: checkpoint.id, sha256: await manuscriptHash(raw), body: raw }
}

test('sealed checkpoints survive manuscript and independent backup and point to the exact earlier chapter', async () => {
  const { pack, volume, book, original } = await checkpointFixture()
  expect(await hasLocalSnapshotHistory(pack.novel.id)).toBe(false)
  vi.stubGlobal('indexedDB', new IDBFactory())
  await importLocalManuscript(bundle(pack))
  expect(await hasLocalSnapshotHistory(pack.novel.id)).toBe(true)
  expect(await readLocalSnapshotHistory(pack.novel.id)).toEqual([book, volume])
  expect(await readLocalSourceRevision(pack.novel.id, volume.chapters[0].revision_id)).toEqual(original)
  expect((await readLocalHistory(pack.novel.id, original.chapter_id)).at(-1)?.content).toBe('正文已更新。')
  expect((await exportLocalManuscript(pack.novel.id)).snapshot_history).toEqual(pack.snapshot_history)
  const backup = await exportLocalBackup(pack.novel.id)
  vi.stubGlobal('indexedDB', new IDBFactory())
  await importLocalBackup(backup)
  expect(await readLocalSnapshotHistory(pack.novel.id)).toEqual([book, volume])
  expect((await exportLocalManuscript(pack.novel.id)).snapshot_history).toEqual(pack.snapshot_history)
})

test('a checkpoint with invalid seals, ownership, scoped chapter order or missing basis rejects the entire pack', async () => {
  const { pack, volume, book, original, secondRoot } = await checkpointFixture()
  const valid = pack.snapshot_history.entries[0]
  const sources = volume.restore_sources!
  const invalid = [
    { ...valid, sha256: 'a'.repeat(64) },
    await sealCheckpoint({ ...volume, branch_id: 'another-branch' }),
    await sealCheckpoint({ ...volume, source_manifest_sha256: 'malformed-source-sha' }),
    await sealCheckpoint({
      ...volume,
      chapters: [
        {
          ...volume.chapters[0],
          ...{ revision_id: secondRoot.id, revision: secondRoot.revision, sha256: secondRoot.sha256 },
        },
        volume.chapters[1],
      ],
    }),
    await sealCheckpoint({ ...volume, scoped_chapter_ids: [secondRoot.chapter_id] }),
    await sealCheckpoint({ ...volume, shared: { ...volume.shared, outline_id: 'discarded-outline' } }),
    await sealCheckpoint({ ...volume, reports: { workflow_inspection: 'draft-reports-arent-accepted' } }),
    await sealCheckpoint({ ...book, scoped_chapter_ids: book.scoped_chapter_ids.slice().reverse() }),
    await sealCheckpoint({
      ...volume,
      restore_sources: { ...sources, story_heads: { missing: { revision: 0, version_id: '1'.repeat(64) } } },
    }),
    await sealCheckpoint({
      ...volume,
      restore_sources: {
        ...sources,
        facts: sources.facts.map((fact) => ({ ...fact, evidence: { ...fact.evidence, end: 5 } })),
      },
    }),
    await sealCheckpoint(volume, valid.body.replace('"label":', '"l\\u0061bel":"隐匿文字", "label":')),
  ]
  vi.stubGlobal('indexedDB', new IDBFactory())
  const other = await createLocalProject('保留已保存作品')
  for (const entry of invalid) {
    const archive = await encodeWebManuscriptBackup(
      { ...pack, snapshot_history: { entries: [entry] } },
      manuscriptHash,
    )
    await expect(importLocalBackup(archive)).rejects.toMatchObject({ code: 'format' })
  }
  const remote = { ...original, id: 'source-only-revision' }
  const sourceOnly = await sealCheckpoint({
    ...volume,
    chapters: volume.chapters.map((chapter) =>
      chapter.id === remote.chapter_id ? { ...chapter, revision_id: remote.id } : chapter,
    ),
  })
  await expect(
    importLocalManuscript(
      bundle({ ...pack, source_revisions: [remote], snapshot_history: { entries: [sourceOnly] } }),
    ),
  ).rejects.toMatchObject({ code: 'format' })
  expect(await listLocalProjects()).toEqual([other])
  expect(await readLocalSnapshotHistory(other.novel.id)).toEqual([])
  const add = IDBObjectStore.prototype.add
  const failure = vi.spyOn(IDBObjectStore.prototype, 'add').mockImplementation(function (
    this: IDBObjectStore,
    value,
    key,
  ) {
    if (this.name === 'snapshotArchives') throw new DOMException('Storage full', 'QuotaExceededError')
    return add.call(this, value, key)
  })
  try {
    await expect(importLocalManuscript(bundle(pack))).rejects.toMatchObject({ code: 'quota' })
    expect(await listLocalProjects()).toEqual([other])
  } finally {
    failure.mockRestore()
  }
  await expect(importLocalManuscript(bundle(pack))).resolves.toMatchObject({ novel: pack.novel })
})

test('executable checkpoints bind setting proof to a native revision at or before the checkpoint', async () => {
  const { pack, volume, original } = await checkpointFixture()
  const latest = pack.revisions.find(
    (row) => row.chapter_id === original.chapter_id && row.revision > original.revision,
  )!
  const setting: SettingsVersion = {
    id: 'setting-with-proof',
    parent_id: null,
    project_version_id: null,
    categories: [],
    origins: {},
    note: '',
    reconciled_project_items: [],
    reviewed_setting_findings: [],
  }
  const checkpoint = { ...volume, shared: { ...volume.shared, settings_id: setting.id } }
  const sealed = decodePortableCheckpointHistory({ entries: [await sealCheckpoint(checkpoint)] })
  const revisions = new Map(pack.revisions.map((row) => [row.id, row]))
  const basis = {
    branch: pack.branch,
    chapterIds: new Set(pack.chapters.map(({ id }) => id)),
    revisions,
    sourceRevisions: new Map(),
    projects: new Set<string>(),
    settings: new Map([[setting.id, setting]]),
    outlines: new Map(pack.outline_history?.versions.map((row) => [row.id, row])),
    inspections: new Set<string>(),
    completions: new Set<string>(),
  }
  for (const revision of [original, latest]) {
    setting.origins.use = {
      origin: 'author',
      edited: false,
      artifact_id: null,
      device_id: null,
      project_version_id: null,
      evidence: {
        chapter_id: revision.chapter_id,
        revision_id: revision.id,
        start: 0,
        end: 1,
        quote_sha256: await manuscriptHash(revision.content[0]),
      },
    }
    const validation = verifyPortableCheckpoints(sealed, basis, manuscriptHash)
    if (revision === original) await expect(validation).resolves.toBeUndefined()
    else await expect(validation).rejects.toThrow('检查点')
  }
})

test('whole-book checkpoint restores atomically, rejects stale previews and supports returning to its recovery point', async () => {
  const { pack, book, volume, original } = await checkpointFixture()
  const source = { ...book, restore_sources: { ...volume.restore_sources!, facts: [], length_targets: [] } }
  vi.stubGlobal('indexedDB', new IDBFactory())
  await importLocalManuscript(
    bundle({ ...pack, snapshot_history: { entries: [await sealCheckpoint(source)] } }),
  )
  const id = pack.novel.id
  const chapterId = original.chapter_id
  const added = await createLocalChapter(id, '归档之…新章')
  const before = await readLocalCheckpointRestore(id, book.id)
  expect(before.blockers).toEqual([])
  expect(before.changes).toMatchObject([
    { id: chapterId, action: 'restore', currentRevision: 2, targetRevision: 1 },
    { id: added.id, action: 'archive', targetRevision: null },
  ])
  const current = (await readLocalHistory(id, chapterId)).at(-1)!
  const late = await saveLocalChapter(id, chapterId, current.id, '第二窗口新改，不可覆盖。')
  await expect(restoreLocalCheckpoint(id, book.id, before.basis, 'pending-request')).rejects.toMatchObject({
    code: 'conflict',
  })
  const fresh = await readLocalCheckpointRestore(id, book.id)
  const prior = await exportLocalManuscript(id)
  const publications = await readLocalPublications(pack.branch.id)
  const add = IDBObjectStore.prototype.add
  const failure = vi.spyOn(IDBObjectStore.prototype, 'add').mockImplementation(function (
    this: IDBObjectStore,
    value,
    key,
  ) {
    if (this.name === 'snapshotArchives') throw new DOMException('Storage full', 'QuotaExceededError')
    return add.call(this, value, key)
  })
  try {
    await expect(
      restoreLocalCheckpoint(id, book.id, fresh.basis, 'whole-book-request'),
    ).rejects.toMatchObject({ code: 'quota' })
    expect(await exportLocalManuscript(id)).toEqual(prior)
    expect(await readLocalPublications(pack.branch.id)).toEqual(publications)
  } finally {
    failure.mockRestore()
  }
  const result = await restoreLocalCheckpoint(id, book.id, fresh.basis, 'whole-book-request')
  expect(result.recoveryCheckpointId).toBe('whole-book-request')
  expect(result.chapters.find((chapter) => chapter.id === added.id)?.archived).toBe(1)
  expect(await restoreLocalCheckpoint(id, book.id, fresh.basis, 'whole-book-request')).toEqual(result)
  const restored = (await readLocalHistory(id, chapterId)).at(-1)!
  expect(restored).toMatchObject({
    parent_id: late.id,
    content: original.content,
    revision: late.revision + 1,
  })
  expect((await readLocalPublications(pack.branch.id)).map(({ objectId }) => objectId)).toContain(
    `revision:${restored.id}`,
  )
  const returning = await readLocalCheckpointRestore(id, result.recoveryCheckpointId)
  expect(returning.blockers).toEqual([])
  await restoreLocalCheckpoint(id, result.recoveryCheckpointId, returning.basis, 'return-request')
  expect((await readLocalHistory(id, chapterId)).at(-1)).toMatchObject({
    parent_id: restored.id,
    content: late.content,
  })
  expect(
    (await exportLocalManuscript(id)).chapters.find((chapter) => chapter.id === added.id)?.archived,
  ).toBe(0)
})

test('volume restore uses historical facts and targets, preserves the outside volume, and undoes from recovery', async () => {
  const { pack, volume, original, secondRoot } = await checkpointFixture()
  const id = pack.novel.id
  const second = await saveLocalChapter(id, secondRoot.chapter_id, secondRoot.id, '第二卷后来的正文。')
  const later = await exportLocalManuscript(id)
  const currentFirst = later.revisions.find(
    (row) => row.chapter_id === original.chapter_id && row.revision === 2,
  )!
  const originalFact = volume.restore_sources!.facts[0]
  const former = [
    {
      ...originalFact,
      statement: '后来改写了第一卷。',
      evidence: await captureStoryEvidence(currentFirst, 0, 2),
    },
    {
      ...originalFact,
      statement: '卷外仍保有后来记录。',
      evidence: await captureStoryEvidence(second, 0, 3),
    },
  ]
  const limit = { min_chars: 1, max_chars: null, expected_chapters: null }
  vi.stubGlobal('indexedDB', new IDBFactory())
  await importLocalManuscript(
    bundle({
      ...later,
      snapshot_history: pack.snapshot_history,
      restore_state: {
        facts: former,
        length_targets: [
          { scope: 'chapter', scope_id: original.chapter_id, targets: limit },
          { scope: 'volume', scope_id: 'new-city', targets: limit },
          { scope: 'book', scope_id: null, targets: limit },
        ],
      },
    }),
  )
  const currentOutline = (await readLocalOutline(id)).current!.version
  await confirmLocalOutline(id, currentOutline.id, {
    ...currentOutline,
    id: 'current-two-volumes',
    parent_id: currentOutline.id,
    plan: {
      ...currentOutline.plan,
      volumes: currentOutline.plan.volumes.map((row) => ({
        ...row,
        summary: row.id === 'old-city' ? '第一卷新改纲' : '第二卷新改纲',
      })),
    },
  })
  const preview = await readLocalCheckpointRestore(id, volume.id)
  expect(preview.blockers).toEqual([])
  expect(preview.outlineChanged).toBe(true)
  expect(preview).toMatchObject({ factCount: 1, lengthTargetCount: 2 })
  expect(preview.changes.map(({ id }) => id)).toEqual([original.chapter_id])
  const currentPublications = await readLocalPublications(pack.branch.id)
  const put = IDBObjectStore.prototype.put
  const failure = vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (
    this: IDBObjectStore,
    value,
    key,
  ) {
    if (this.name === 'restoreStates') throw new DOMException('Storage full', 'QuotaExceededError')
    return put.call(this, value, key)
  })
  try {
    await expect(
      restoreLocalCheckpoint(id, volume.id, preview.basis, 'volume-request'),
    ).rejects.toMatchObject({
      code: 'quota',
    })
  } finally {
    failure.mockRestore()
  }
  expect((await readLocalHistory(id, original.chapter_id)).at(-1)).toEqual(currentFirst)
  expect((await readLocalRulePreview(id)).facts.map(({ statement }) => statement)).toEqual(
    former.map(({ statement }) => statement),
  )
  const restored = await restoreLocalCheckpoint(id, volume.id, preview.basis, 'volume-request')
  expect(await restoreLocalCheckpoint(id, volume.id, preview.basis, 'volume-request')).toEqual(restored)
  const restoredHead = (await readLocalHistory(id, original.chapter_id)).at(-1)!
  expect(restoredHead.content).toBe(original.content)
  expect((await readLocalHistory(id, second.chapter_id)).at(-1)).toEqual(second)
  expect((await readLocalOutline(id)).current?.version.plan.volumes.map(({ summary }) => summary)).toEqual([
    '回查原始铸记',
    '第二卷新改纲',
  ])
  expect(
    (await readLocalPublications(pack.branch.id)).find(
      ({ objectId }) => objectId === `chapter:${second.chapter_id}`,
    ),
  ).toEqual(currentPublications.find(({ objectId }) => objectId === `chapter:${second.chapter_id}`))
  const rules = await readLocalRulePreview(id)
  expect(rules.facts).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        statement: originalFact.statement,
        evidence: { ...originalFact.evidence, revision_id: restoredHead.id },
        quote: original.content.slice(originalFact.evidence.start, originalFact.evidence.end),
      }),
      expect.objectContaining({ statement: former[1].statement, evidence: former[1].evidence }),
    ]),
  )
  expect(rules.book.lengthTarget).toEqual(limit)
  expect(rules.chapters.find(({ id }) => id === original.chapter_id)?.lengthTarget).toBeNull()
  expect(rules.volumes.map(({ lengthTarget }) => lengthTarget)).toEqual([
    volume.restore_sources!.length_targets[0].targets,
    limit,
  ])
  expect(rules.volumes[0].lengthIssues).toEqual(['正文字符数超出冻结目标'])
  const noLongerActive = await exportLocalManuscript(id)
  noLongerActive.chapters.find(({ id }) => id === original.chapter_id)!.archived = 1
  await expect(validateManuscript(noLongerActive)).rejects.toMatchObject({ code: 'format' })
  const exchanged = await exportLocalBackup(id)
  const originalFactory = indexedDB
  vi.stubGlobal('indexedDB', new IDBFactory())
  await importLocalBackup(exchanged)
  expect(await readLocalRulePreview(id)).toEqual(rules)
  vi.stubGlobal('indexedDB', originalFactory)
  const undo = await readLocalCheckpointRestore(id, restored.recoveryCheckpointId)
  await restoreLocalCheckpoint(id, restored.recoveryCheckpointId, undo.basis, 'undo-volume-request')
  const undoRules = await readLocalRulePreview(id)
  expect(undoRules.chapters.find(({ id }) => id === original.chapter_id)?.lengthTarget).toEqual(limit)
  expect(undoRules.volumes.map(({ lengthTarget }) => lengthTarget)).toEqual([null, limit])
  expect(undoRules.facts.map(({ statement }) => statement)).toEqual(former.map(({ statement }) => statement))

  const crossing = outlinePromiseSeed('必须在第二卷找到主人', '第一卷埋下铜铃线索。')
  await confirmLocalStory(id, {
    record_id: 'crossing',
    record: crossing,
    archived: false,
    baseVersionId: null,
    baseStoryRevision: (await readLocalStory(id)).storyRevision,
    evidence: { record: [], stages: [[]] },
  })
  const rolledOutline = (await readLocalOutline(id)).current!.version
  await confirmLocalOutline(id, rolledOutline.id, {
    ...rolledOutline,
    id: 'cross-volume-planning',
    parent_id: rolledOutline.id,
    plan: {
      ...rolledOutline.plan,
      promises: [
        {
          promise_id: 'crossing',
          plant_chapter_id: original.chapter_id,
          opens_chapter_id: second.chapter_id,
          closes_chapter_id: second.chapter_id,
          fulfillment: 'author_required',
        },
      ],
    },
    promise_sources: { crossing },
  })
  const blocked = await readLocalCheckpointRestore(id, volume.id)
  expect(blocked.blockers).toEqual([expect.stringContaining('伏笔窗口跨越本卷')])
  await expect(
    restoreLocalCheckpoint(id, volume.id, blocked.basis, 'cross-volume-request'),
  ).rejects.toMatchObject({ code: 'conflict' })
})

test('forked checkpoint persists its receipt atomically and replays concurrent requests only once', async () => {
  const { pack, volume, original } = await checkpointFixture()
  const id = pack.novel.id
  vi.stubGlobal('indexedDB', new IDBFactory())
  await importLocalManuscript(bundle(pack))
  const preview = await readLocalCheckpointFork(id, volume.id)
  expect(preview).toMatchObject({ blockers: [], chapterCount: 2, outline: 'confirmed' })
  const add = IDBObjectStore.prototype.add
  const failure = vi.spyOn(IDBObjectStore.prototype, 'add').mockImplementation(function (
    this: IDBObjectStore,
    value,
    key,
  ) {
    if (this.name === 'projects') throw new DOMException('Storage full', 'QuotaExceededError')
    return add.call(this, value, key)
  })
  const create = (basis = preview.basis) =>
    createLocalCheckpointBranch(id, volume.id, '独立恢复副本', basis, 'checkpoint-fork-request')
  try {
    await expect(create()).rejects.toMatchObject({ code: 'quota' })
  } finally {
    failure.mockRestore()
  }
  expect((await listLocalProjects()).map(({ novel }) => novel.id)).toEqual([id])
  const [first, second] = await Promise.all([create(), create()])
  expect(second).toEqual(first)
  await expect(create('changed-seal')).rejects.toMatchObject({ code: 'conflict' })
  const target = first.project.novel.id
  expect(target).not.toBe(id)
  const rules = await readLocalRulePreview(target)
  expect(rules.facts[0]).toMatchObject({ quote: original.content.slice(0, 6) })
  expect(rules.facts[0].evidence.revision_id).not.toBe(original.id)
  expect((await exportLocalManuscript(target)).evidence?.[0]).toMatchObject({
    title: '从归档另存的来源',
  })
  expect((await readLocalHistory(id, original.chapter_id)).at(-1)?.content).toBe('正文已更新。')
  expect(await listLocalProjects()).toHaveLength(2)
})

test('restoring a volume from an unbound outline retains adopted setting references outside it', async () => {
  const { pack, volume, original, secondRoot } = await checkpointFixture()
  const id = pack.novel.id
  const oldOutline = (await readLocalOutline(id)).current!.version
  expect(oldOutline.settings_version_id).toBeNull()

  const settings = createSettingsDraft('registered-between-outlines', null, null)
  settings.categories
    .find(({ id }) => id === 'characters')!
    .items.push({
      id: 'outside-keeper',
      title: '守簿人',
      detail: '由守簿人交还新城的铸册。',
      links: [],
    })
  settings.origins['outside-keeper'] = {
    origin: 'author',
    edited: false,
    artifact_id: null,
    device_id: null,
    project_version_id: null,
    evidence: null,
  }
  await confirmLocalSettings(id, null, settings)
  const afterSettings = await exportLocalManuscript(id)
  const point = {
    ...volume,
    story_revision: afterSettings.branch.story_revision,
    shared: { ...volume.shared, settings_id: settings.id },
    restore_sources: { ...volume.restore_sources!, facts: [], length_targets: [] },
  }

  const boundOutside = oldOutline.plan.volumes[1]
  const afterOutsideChange = {
    ...boundOutside,
    summary: '新城的铸册只由守簿人携带。',
    units: boundOutside.units.map((unit) => ({
      ...unit,
      chapters: unit.chapters.map((chapter) => ({ ...chapter, setting_ids: ['outside-keeper'] })),
    })),
  }
  await confirmLocalOutline(id, oldOutline.id, {
    ...oldOutline,
    id: 'bound-current-outline',
    parent_id: oldOutline.id,
    settings_version_id: settings.id,
    plan: { ...oldOutline.plan, volumes: [oldOutline.plan.volumes[0], afterOutsideChange] },
  })
  const current = await exportLocalManuscript(id)
  const activeVersion = current.outline_history!.versions.find(({ id }) => id === 'bound-current-outline')!
  const waitingPromise = 'unfinished-outside-promise'
  const pendingOutline = {
    ...oldOutline,
    id: 'pending-incoming-outline',
    parent_id: oldOutline.id,
    plan: {
      ...oldOutline.plan,
      promises: [
        {
          promise_id: waitingPromise,
          plant_chapter_id: secondRoot.chapter_id,
          opens_chapter_id: secondRoot.chapter_id,
          closes_chapter_id: secondRoot.chapter_id,
        },
      ],
    },
    promise_sources: { [waitingPromise]: outlinePromiseSeed('等待守簿人的回收记录', '') },
  }
  const sourceEntry = await sealCheckpoint(point)
  vi.stubGlobal('indexedDB', new IDBFactory())
  await importLocalManuscript(
    bundle({
      ...current,
      outline_history: {
        adopted_revision_id: pendingOutline.id,
        versions: [...current.outline_history!.versions, pendingOutline],
        required_story_records: [waitingPromise],
      },
      snapshot_history: { entries: [sourceEntry] },
    }),
  )
  const incoming = await readLocalOutline(id)
  expect(incoming.current).toBeUndefined()
  expect(incoming.restorePending).toMatchObject({
    revisionId: pendingOutline.id,
    requiredStoryRecords: [waitingPromise],
  })
  await confirmLocalOutline(id, null, { ...activeVersion, id: 'active-after-pending', parent_id: null })
  const pending = (await readLocalOutline(id)).restorePending!
  expect(pending).toEqual(incoming.restorePending)

  const fingerprintInput = { pack: await exportLocalManuscript(id), adoptedOutlineId: 'active-after-pending' }
  expect(
    checkpointRestoreFingerprint({ ...fingerprintInput, deferredOutline: pending }, sourceEntry),
  ).not.toBe(
    checkpointRestoreFingerprint(
      { ...fingerprintInput, deferredOutline: { ...pending, requiredStoryRecords: [] } },
      sourceEntry,
    ),
  )

  const preview = await readLocalCheckpointRestore(id, point.id)
  expect(preview.blockers).toEqual([])
  expect(preview.settingsChanged).toBe(false)
  expect(preview.outlineChanged).toBe(true)
  await restoreLocalCheckpoint(id, point.id, preview.basis, 'restore-unbound-volume')
  const restoredState = await readLocalOutline(id)
  const restored = restoredState.current!.version
  expect(restored).toMatchObject({ parent_id: 'active-after-pending', settings_version_id: settings.id })
  expect(restoredState.restorePending).toEqual(pending)
  expect(restored.plan.volumes).toEqual([oldOutline.plan.volumes[0], afterOutsideChange])
  expect((await readLocalHistory(id, original.chapter_id)).at(-1)?.content).toBe(original.content)
  expect((await readLocalHistory(id, secondRoot.chapter_id)).at(-1)).toEqual(secondRoot)
})

test('whole-book checkpoint applies project, setting, outline and story revisions without overwriting original confirmation', async () => {
  const work = await createLocalProject('两个版本的铃')
  const id = work.novel.id
  const branchId = work.branch.id
  const chapter = await createLocalChapter(id, '归还铃')
  const [root] = await readLocalHistory(id, chapter.id)
  const briefing = {
    position: '层层解开旧案',
    track: '线索式悬疑',
    genre_ids: ['xianxia'],
    audience: '偏好悬疑的读者',
    distinction: '证物都在首章出现',
    length: '十万字',
    cadence: '',
    ending: '解开铜铃旧案',
    final_image: '',
  }
  async function confirm(
    seed: string,
    prior: { project: string; settings: string; outline: string; story: string } | null,
    head: typeof root,
  ) {
    const text = seed === 'old' ? '门内归铃' : '改到门外'
    const project = await confirmLocalProjectBrief(id, prior?.project ?? null, {
      idea: text,
      brief: briefing,
    })
    const evidence = await captureStoryEvidence(head, 1, 3)
    const settings = createSettingsDraft(`settings-${seed}`, prior?.settings ?? null, project.id)
    settings.categories
      .find(({ id }) => id === 'characters')!
      .items.push(
        { id: 'bell', title: '旧铃线索', detail: text, links: [] },
        { id: 'witness', title: '现场片段', detail: text, links: [] },
      )
    settings.origins.bell = {
      origin: 'author',
      edited: false,
      artifact_id: null,
      device_id: null,
      project_version_id: project.id,
      evidence: null,
    }
    settings.origins.witness = { ...settings.origins.bell, evidence }
    await confirmLocalSettings(id, prior?.settings ?? null, settings)
    const outline = `outline-${seed}`
    await confirmLocalOutline(id, prior?.outline ?? null, {
      id: outline,
      parent_id: prior?.outline ?? null,
      project_version_id: project.id,
      settings_version_id: settings.id,
      plan: {
        summary: text,
        promises: [],
        volumes: [
          {
            id: 'v1',
            title: '第一卷',
            summary: text,
            units: [
              {
                id: 'u1',
                title: '一场',
                summary: text,
                chapters: [
                  {
                    chapter_id: chapter.id,
                    goal: text,
                    obstacle: '有人阻拦',
                    turn: '辨出铃声',
                    result: '确定归处',
                    setting_ids: ['bell'],
                  },
                ],
              },
            ],
          },
        ],
      },
      reference_cards: [],
      promise_sources: {},
    })
    const story = await confirmLocalStory(id, {
      record: parseStoryRecord({ kind: 'event', title: text, status: 'actual' }),
      record_id: 'bell-event',
      archived: false,
      baseStoryRevision: (await readLocalStory(id)).storyRevision,
      baseVersionId: prior?.story ?? null,
      evidence: { record: [evidence], stages: [] },
    })
    return { project: project.id, settings: settings.id, outline, story: story.id, evidence }
  }
  const oldRevision = await saveLocalChapter(id, chapter.id, root.id, '铃📯在屋内。')
  const earlier = await confirm('old', null, oldRevision)
  const old = await exportLocalManuscript(id)
  const oldCheckpoint = captureRecoveryCheckpoint(
    { pack: old, adoptedOutlineId: earlier.outline },
    'formal-checkpoint',
    await manuscriptHash('fixture'),
    1,
  )
  const latest = await saveLocalChapter(id, chapter.id, oldRevision.id, '铃📯在屋外。')
  const current = await confirm('new', earlier, latest)
  await confirmLocalSettingUse(id, chapter.id, {
    parent_id: null,
    outline_version_id: current.outline,
    settings_version_id: current.settings,
    item_id: 'bell',
    evidence: current.evidence,
    chapter_sha256: latest.sha256,
  })
  const pack = await exportLocalManuscript(id)
  vi.stubGlobal('indexedDB', new IDBFactory())
  await importLocalManuscript(
    bundle({ ...pack, snapshot_history: { entries: [await sealCheckpoint(oldCheckpoint)] } }),
  )
  const preview = await readLocalCheckpointRestore(id, oldCheckpoint.id)
  expect(preview).toMatchObject({
    blockers: [],
    projectChanged: true,
    settingsChanged: true,
    outlineChanged: true,
    storyRecordCount: 1,
    affectedSettingUses: [{ chapterTitle: chapter.title, title: '旧铃线索' }],
  })
  const result = await restoreLocalCheckpoint(id, oldCheckpoint.id, preview.basis, 'formal-restore')
  const written = (await readLocalHistory(id, chapter.id)).at(-1)!
  expect(written).toMatchObject({ content: oldRevision.content, parent_id: latest.id })
  const adoptedProject = (await readLocalProjectBrief(id)).current!.version
  expect(adoptedProject).toMatchObject({ idea: '门内归铃', parent_id: current.project })
  const adoptedOutline = await readLocalOutline(id)
  expect(adoptedOutline.settings).toMatchObject({
    parent_id: current.settings,
    project_version_id: adoptedProject.id,
    origins: { witness: { evidence: { revision_id: written.id } } },
  })
  expect(adoptedOutline.current?.version).toMatchObject({
    parent_id: current.outline,
    project_version_id: adoptedProject.id,
    settings_version_id: adoptedOutline.settings?.id,
    plan: { summary: '门内归铃' },
  })
  expect((await readLocalStory(id)).records[0]).toMatchObject({
    version: { parent_id: current.story, record: { title: '门内归铃' } },
    native: { evidence: { record: [written.id] } },
    invalidReasons: [],
  })
  expect((await readLocalPublications(branchId)).map(({ objectId }) => objectId)).toEqual(
    expect.arrayContaining([
      `project:${branchId}`,
      `settings:${branchId}`,
      `outline:${branchId}`,
      'story:bell-event',
    ]),
  )
  const rebuilt = await exportLocalManuscript(id)
  expect(rebuilt.story_history?.versions.some(({ id }) => id === current.story)).toBe(true)
  expect(rebuilt.setting_use_history).toEqual(pack.setting_use_history)
  const recovery = await readLocalCheckpointRestore(id, result.recoveryCheckpointId)
  expect(recovery.blockers).toEqual([])
  await restoreLocalCheckpoint(id, result.recoveryCheckpointId, recovery.basis, 'formal-undo')
  expect((await readLocalHistory(id, chapter.id)).at(-1)?.content).toBe(latest.content)
})

test('quota failure aborts the whole browser write and preserves existing formal versions', async () => {
  const a = await createLocalProject('遗铃')
  const chapter = await createLocalChapter(a.novel.id, '找到秤砣')
  const [base] = await readLocalHistory(a.novel.id, chapter.id)
  const previous = await exportLocalManuscript(a.novel.id)
  const add = IDBObjectStore.prototype.add
  const failure = vi.spyOn(IDBObjectStore.prototype, 'add').mockImplementation(function (
    this: IDBObjectStore,
    value,
    key,
  ) {
    if (this.name === 'revisions') throw new DOMException('Storage full', 'QuotaExceededError')
    return add.call(this, value, key)
  })
  try {
    await expect(saveLocalChapter(a.novel.id, chapter.id, base.id, '空间不足未写入')).rejects.toMatchObject({
      code: 'quota',
    })
    expect(await exportLocalManuscript(a.novel.id)).toEqual(previous)
  } finally {
    failure.mockRestore()
  }
})

test('an unsupported newer database opens read-only from this app’s perspective and keeps records intact', async () => {
  const futureVersion = 99
  await new Promise<void>((resolve, reject) => {
    const opening = indexedDB.open('ainovel-offline-workspace', futureVersion)
    opening.onupgradeneeded = () => opening.result.createObjectStore('v2').add('保留新版数据', 'data')
    opening.onsuccess = () => {
      opening.result.close()
      resolve()
    }
    opening.onerror = () => reject(opening.error)
  })
  await expect(listLocalProjects()).rejects.toMatchObject({ code: 'database' })
  const record = await new Promise((resolve, reject) => {
    const opening = indexedDB.open('ainovel-offline-workspace', futureVersion)
    opening.onsuccess = () => {
      const tx = opening.result.transaction('v2')
      const read = tx.objectStore('v2').get('data')
      read.onsuccess = () => resolve(read.result)
      tx.oncomplete = () => opening.result.close()
    }
    opening.onerror = () => reject(opening.error)
  })
  expect(record).toBe('保留新版数据')
})

test('formal M1 confirmations use a local CAS and keep incoming competing revisions immutable', async () => {
  const project = await createLocalProject('异地立项')
  const projectId = project.novel.id
  const branchId = project.branch.id
  const draft = {
    idea: '追查遗落的铜铃',
    brief: {
      position: '层层解开旧案',
      track: '线索式悬疑',
      genre_ids: ['xianxia'],
      audience: '偏好悬疑的读者',
      distinction: '证物都在首章出现',
      length: '十万字',
      cadence: '',
      ending: '解开铜铃旧案',
      final_image: '',
    },
  }
  const local = await confirmLocalProjectBrief(projectId, null, draft)
  const pending = await readLocalPublications(branchId)
  expect(pending.map(({ objectId }) => objectId).sort()).toEqual([
    `project-version:${local.id}`,
    `project:${branchId}`,
  ])
  const put = IDBObjectStore.prototype.put
  const failure = vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (
    this: IDBObjectStore,
    value,
    key,
  ) {
    if (this.name === 'publications') throw new DOMException('Storage full', 'QuotaExceededError')
    return put.call(this, value, key)
  })
  try {
    await expect(
      confirmLocalProjectBrief(projectId, local.id, { ...draft, idea: '未能排队的立项' }),
    ).rejects.toMatchObject({ code: 'quota' })
    expect((await readLocalProjectBrief(projectId)).versions.map(({ version }) => version)).toEqual([local])
    expect(await readLocalPublications(branchId)).toEqual(pending)
  } finally {
    failure.mockRestore()
  }
  await expect(confirmLocalProjectBrief(projectId, null, draft)).rejects.toMatchObject({ code: 'conflict' })
  const remote = { ...local, id: 'competing-project', parent_id: local.id, idea: '从线索中的草药重新立项' }
  const format = 'ainovel-manuscript-sync/1'
  const source = { device_id: 'other-device', change_id: 'remote-project-version', sequence: 4 }
  const adopted = await validateSyncedObject(project, `project:${branchId}`, {
    format,
    kind: 'project-head',
    branch_id: branchId,
    revision_id: remote.id,
  })
  const base = JSON.stringify(['project-head', branchId, local.id])
  await expect(applySyncedObject(projectId, `project:${branchId}`, adopted, base)).rejects.toMatchObject({
    code: 'missing',
  })
  const versionId = `project-version:${remote.id}`
  const object = await validateSyncedObject(project, versionId, {
    format,
    kind: 'project-version',
    branch_id: branchId,
    version: remote,
  })
  await expect(applySyncedObject(projectId, versionId, object, 'null', { source })).resolves.toBe(true)
  await expect(applySyncedObject(projectId, `project:${branchId}`, adopted, base)).resolves.toBe(true)
  const after = await readLocalProjectBrief(projectId)
  expect(after.current).toMatchObject({ version: remote, source })
  expect(after.versions.map(({ version }) => version.id)).toEqual(
    expect.arrayContaining([local.id, remote.id]),
  )
  const archive = await exportLocalManuscript(projectId)
  const history = archive.project_history!
  expect(history.adopted_revision_id).toBe(remote.id)
  expect(history.versions).toEqual(expect.arrayContaining([local, remote]))
  expect(JSON.stringify(archive)).not.toContain(source.change_id)
  await expect(
    validateManuscript({ ...archive, project_history: { ...history, versions: [remote] } }),
  ).rejects.toMatchObject({ code: 'format' })
  vi.stubGlobal('indexedDB', new IDBFactory())
  await importLocalManuscript(bundle(archive))
  const recovered = (await readLocalProjectBrief(projectId)).current
  expect(recovered).toMatchObject({ version: remote, fromPackage: true })
  expect(recovered?.source).toBeUndefined()
  expect((await exportLocalManuscript(projectId)).project_history).toEqual(history)
})

test('failure writing a chapter publication rolls back content and current head', async () => {
  const project = await createLocalProject('待发一致性')
  const chapter = await createLocalChapter(project.novel.id, '旧铃')
  const [base] = await readLocalHistory(project.novel.id, chapter.id)
  const pending = await readLocalPublications(project.branch.id)
  const put = IDBObjectStore.prototype.put
  const failure = vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (
    this: IDBObjectStore,
    value,
    key,
  ) {
    if (this.name === 'publications') throw new DOMException('Storage full', 'QuotaExceededError')
    return put.call(this, value, key)
  })
  try {
    await expect(
      saveLocalChapter(project.novel.id, chapter.id, base.id, '要么同时保存，要么回滚。'),
    ).rejects.toMatchObject({ code: 'quota' })
    expect(await readLocalHistory(project.novel.id, chapter.id)).toEqual([base])
    expect(await readLocalPublications(project.branch.id)).toEqual(pending)
  } finally {
    failure.mockRestore()
  }
})

test('an older comparison cannot adopt over a newer local publication with the same text', async () => {
  const project = await createLocalProject('同文不同版')
  const branchId = project.branch.id
  const key = project.novel.id
  const chapter = await createLocalChapter(key, '铜铃')
  const [old] = await readLocalHistory(key, chapter.id)
  const objectId = `chapter:${chapter.id}`
  const shown = (await readLocalPublications(branchId)).find((entry) => entry.objectId === objectId)!

  await saveLocalChapter(key, chapter.id, old.id, '一稿内容。')
  const preview = await readLocalChapterRestore(key, chapter.id, old.id)
  const restored = await restoreLocalChapter(key, chapter.id, old.id, preview.basis)
  expect(restored.sha256).toBe(old.sha256)
  const earlier = JSON.stringify([
    'chapter',
    chapter.id,
    branchId,
    chapter.position,
    chapter.title,
    old.sha256,
  ])
  const incoming = await validateSyncedObject(project, objectId, {
    format: 'ainovel-manuscript-sync/1',
    kind: 'chapter',
    chapter: { ...chapter, revision: 1 },
    head: {
      ...old,
      id: 'remote-head',
      parent_id: old.id,
      revision: 1,
      content: '他人改写。',
      sha256: await manuscriptHash('他人改写。'),
    },
  })
  expect(
    await applySyncedObject(key, objectId, incoming, earlier, {
      publicationId: shown.id,
      consumePublication: true,
    }),
  ).toBe(false)
  expect((await readLocalHistory(key, chapter.id)).at(-1)).toEqual(restored)
  expect((await readLocalPublications(branchId)).find((entry) => entry.objectId === objectId)?.id).not.toBe(
    shown.id,
  )
})

test('source histories recover exact evidence without replacing any locally numbered revision', async () => {
  const project = await createLocalProject('两地修订')
  const localId = project.novel.id
  const chapter = await createLocalChapter(localId, '拾铃')
  const [base] = await readLocalHistory(localId, chapter.id)
  const authorRevision = await saveLocalChapter(localId, chapter.id, base.id, '两份不同原稿。')
  const source = { ...authorRevision, id: 'remote-original', content: '远端作者原稿。' }
  source.sha256 = await manuscriptHash(source.content)
  const format = 'ainovel-manuscript-sync/1'
  const body = '以远端作者修订为依据。'
  const evidence = {
    format,
    kind: 'evidence',
    document: {
      id: 'linked-evidence',
      kind: 'inspection',
      title: '来源记录',
      body,
      sha256: await manuscriptHash(body),
      chapters: [{ id: chapter.id, revision: 1, revision_id: source.id, sha256: source.sha256 }],
    },
  }
  await expect(validateSyncedObject(project, 'evidence:linked-evidence', evidence)).rejects.toMatchObject({
    code: 'missing',
  })
  const sourceId = `revision:${source.id}`
  const sourceObject = await validateSyncedObject(project, sourceId, {
    format,
    kind: 'revision',
    branch_id: project.branch.id,
    revision: source,
  })
  await expect(applySyncedObject(localId, sourceId, sourceObject, 'null')).resolves.toBe(true)
  await expect(applySyncedObject(localId, sourceId, sourceObject, 'null')).resolves.toBe(true)
  const verified = await validateSyncedObject(project, 'evidence:linked-evidence', evidence)
  await expect(applySyncedObject(localId, 'evidence:linked-evidence', verified, 'null')).resolves.toBe(true)
  const pack = await exportLocalManuscript(localId)
  expect(pack.source_revisions).toEqual([source])
  expect(pack.revisions).toEqual([base, authorRevision])
  expect(pack.evidence).toEqual([evidence.document])
  vi.stubGlobal('indexedDB', new IDBFactory())
  await importLocalManuscript(bundle(pack))
  expect(await exportLocalManuscript(localId)).toEqual(pack)
})
