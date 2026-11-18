import { IDBFactory } from 'fake-indexeddb'
import { beforeEach, expect, test, vi } from 'vitest'
import {
  applySyncedObject,
  confirmLocalOutline,
  confirmLocalSettingUse,
  confirmLocalSettings,
  createLocalChapter,
  createLocalProject,
  exportLocalBackup,
  exportLocalManuscript,
  importLocalBackup,
  manuscriptHash,
  readLocalChapterRestore,
  readLocalHistory,
  readLocalOutlineSettingUse,
  readLocalPublications,
  readLocalSettingUse,
  restoreLocalChapter,
  saveLocalChapter,
  syncObjectSignature,
  validateManuscript,
  withdrawLocalSettingUse,
} from './offlineStore'
import { buildSettingUseVersion, settingUseVersionSlot } from './settingUseSyncModel'
import { prepareSettingsVersion, settingCategories } from './settingsSyncModel'
import { captureStoryEvidence } from './storySyncModel'

beforeEach(() => vi.stubGlobal('indexedDB', new IDBFactory()))
const format = 'ainovel-manuscript-sync/1' as const

async function fixture() {
  const work = await createLocalProject('赤钥 · 设定使用')
  const project = work.novel.id
  const branch_id = work.branch.id
  const chapter = await createLocalChapter(project, '交还赤钥')
  const [initial] = await readLocalHistory(project, chapter.id)
  const saved = await saveLocalChapter(project, chapter.id, initial.id, '😀阿陶收回旧钥。')
  const title = '赤钥分配簿'
  const settings = await prepareSettingsVersion({
    id: 'adopted-settings',
    parent_id: null,
    project_version_id: null,
    categories: [
      ...settingCategories.map(([id, title]) => ({ id, title, skipped: false, items: [] })),
      {
        id: 'resources',
        title: '资源',
        skipped: false,
        items: [{ id: 'red-keys', title, detail: '从簿中核对赤钥。' }],
      },
    ],
    origins: { 'red-keys': { origin: 'author' } },
  })
  await confirmLocalSettings(project, null, settings)
  await confirmLocalOutline(project, null, {
    id: 'adopted-outline',
    parent_id: null,
    settings_version_id: settings.id,
    project_version_id: null,
    plan: {
      summary: '归还钥匙',
      promises: [],
      volumes: [
        {
          id: 'one',
          title: '卷一',
          summary: '找到主人',
          units: [
            {
              id: 'two',
              title: '归还',
              summary: '查到分配簿',
              chapters: [
                {
                  chapter_id: chapter.id,
                  goal: '还钥匙',
                  obstacle: '失去账簿',
                  turn: '找到阿陶',
                  result: '已归还',
                  setting_ids: ['red-keys'],
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
  const input = {
    parent_id: null,
    outline_version_id: 'adopted-outline',
    settings_version_id: settings.id,
    item_id: 'red-keys',
    chapter_sha256: saved.sha256,
    evidence: await captureStoryEvidence(saved, 0, 4),
  }
  return { project, branch_id, chapter, saved, input, title }
}

test('正式使用证据按章稿与采用头 CAS，撤回保留全文、父版本和发送标记', async () => {
  const { project, branch_id, chapter, saved, input, title } = await fixture()
  expect((await readLocalOutlineSettingUse(project))[0]).toMatchObject({ title, proof: undefined })
  const first = await confirmLocalSettingUse(project, chapter.id, input)
  const selection = await captureStoryEvidence(saved, 4, 6)
  await expect(
    confirmLocalSettingUse(project, chapter.id, { ...input, evidence: selection }),
  ).rejects.toMatchObject({ code: 'conflict' })
  expect((await readLocalSettingUse(project, chapter.id)).records).toMatchObject([
    { status: 'current', quote: '😀阿陶' },
  ])

  await saveLocalChapter(project, chapter.id, saved.id, '第二次收回另一枚钥匙。')
  expect((await readLocalOutlineSettingUse(project))[0].proof?.status).toBe('outdated')
  await expect(
    confirmLocalSettingUse(project, chapter.id, { ...input, parent_id: first.id, evidence: selection }),
  ).rejects.toMatchObject({ code: 'conflict' })
  const withdrawn = await withdrawLocalSettingUse(project, chapter.id, first.id)
  expect(withdrawn).toMatchObject({ parent_id: first.id, evidence: first.evidence, action: 'withdrawn' })
  expect((await readLocalSettingUse(project, chapter.id)).records).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ status: 'withdrawn', quote: '😀阿陶' }),
      expect.objectContaining({ status: 'superseded', quote: '😀阿陶' }),
    ]),
  )
  expect((await readLocalHistory(project, chapter.id)).at(-1)?.content).toBe('第二次收回另一枚钥匙。')
  expect((await readLocalPublications(branch_id)).map(({ objectId }) => objectId)).toContain(
    `setting-use-version:${withdrawn.id}`,
  )
})

test('恢复单章前核对当前设定使用证据，其他作品确认不影响已预览依据', async () => {
  const { project, chapter, saved, input, title } = await fixture()
  const [target] = await readLocalHistory(project, chapter.id)
  const preview = () => readLocalChapterRestore(project, chapter.id, target.id)
  const restore = (basis: string) => restoreLocalChapter(project, chapter.id, target.id, basis)
  const initial = await preview()
  expect(initial.references.filter(({ kind }) => kind === 'setting_use')).toEqual([])

  const first = await confirmLocalSettingUse(project, chapter.id, input)
  await expect(restore(initial.basis)).rejects.toMatchObject({ code: 'conflict' })
  const signed = await preview()
  expect(signed.references.filter(({ kind }) => kind === 'setting_use')).toEqual([
    {
      kind: 'setting_use',
      id: first.id,
      title,
      source: {
        chapter_id: chapter.id,
        revision_id: saved.id,
        revision: saved.revision,
        start: 0,
        end: 4,
        quote: '😀阿陶',
      },
    },
  ])

  const withdrawn = await withdrawLocalSettingUse(project, chapter.id, first.id)
  await expect(restore(signed.basis)).rejects.toMatchObject({ code: 'conflict' })
  expect((await preview()).references.filter(({ kind }) => kind === 'setting_use')).toEqual([])

  const restoredProof = await confirmLocalSettingUse(project, chapter.id, {
    ...input,
    parent_id: withdrawn.id,
  })
  const ready = await preview()
  const other = await fixture()
  await confirmLocalSettingUse(other.project, other.chapter.id, other.input)
  expect((await preview()).basis).toBe(ready.basis)
  const restored = await restore(ready.basis)
  expect(restored).toMatchObject({ revision: saved.revision + 1, content: target.content })
  expect((await readLocalSettingUse(project, chapter.id)).records).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        entry: expect.objectContaining({ version: restoredProof }),
        status: 'outdated',
        quote: '😀阿陶',
      }),
    ]),
  )
})

test('无确证的同文源只可回查，正式采用关系随独立备份保留并拒绝被篡改', async () => {
  const { project, branch_id, chapter, saved, input } = await fixture()
  const [initial] = await readLocalHistory(project, chapter.id)
  const preview = () => readLocalChapterRestore(project, chapter.id, initial.id)
  const source = { ...saved, id: 'foreign-saved' }
  const remote = { format, kind: 'revision' as const, branch_id, revision: source }
  await applySyncedObject(project, `revision:${source.id}`, remote, 'null')
  const version = await buildSettingUseVersion(
    branch_id,
    {
      ...input,
      evidence: await captureStoryEvidence(source, 0, 4),
      action: 'confirmed',
      actor: 'author',
    },
    manuscriptHash,
  )
  const slot_id = await settingUseVersionSlot(branch_id, version, manuscriptHash)
  await applySyncedObject(
    project,
    `setting-use-version:${version.id}`,
    { format, kind: 'setting-use-version', branch_id, version },
    'null',
    {
      source: { device_id: 'desktop', change_id: 'source-proof', sequence: 1 },
    },
  )
  await applySyncedObject(
    project,
    `setting-use:${slot_id}`,
    { format, kind: 'setting-use-head', branch_id, slot_id, revision_id: version.id },
    'null',
  )
  expect((await readLocalSettingUse(project, chapter.id)).records[0]).toMatchObject({
    status: 'outdated',
    quote: '😀阿陶',
  })
  expect((await preview()).references.some(({ kind }) => kind === 'setting_use')).toBe(false)
  expect((await exportLocalManuscript(project)).setting_use_history?.native_bindings).toBeUndefined()

  const previous = {
    format,
    kind: 'chapter' as const,
    chapter: { ...chapter, revision: saved.revision },
    head: saved,
  }
  await applySyncedObject(
    project,
    `chapter:${chapter.id}`,
    { ...previous, head: source },
    syncObjectSignature(previous),
  )
  expect((await readLocalOutlineSettingUse(project))[0].proof?.status).toBe('current')
  expect((await preview()).references.find(({ kind }) => kind === 'setting_use')?.source).toMatchObject({
    revision_id: source.id,
    revision: source.revision,
    quote: '😀阿陶',
  })
  const pack = await exportLocalManuscript(project)
  expect(pack.setting_use_history?.native_bindings).toEqual({ [source.id]: saved.id })
  await expect(
    validateManuscript({
      ...pack,
      setting_use_history: {
        ...pack.setting_use_history,
        native_bindings: {
          [source.id]: pack.revisions[0].id,
        },
      },
    }),
  ).rejects.toThrow('正文采用关系')

  const backup = await exportLocalBackup(project)
  vi.stubGlobal('indexedDB', new IDBFactory())
  const restored = await importLocalBackup(backup)
  expect((await readLocalSettingUse(restored.novel.id, chapter.id)).records[0]).toMatchObject({
    status: 'current',
    quote: '😀阿陶',
  })
})

test('历史中有同一正式源 ID 时，仅明确的新版采用关系恢复证据当前性', async () => {
  const { project, chapter, saved, input } = await fixture()
  await confirmLocalSettingUse(project, chapter.id, input)
  const changed = await saveLocalChapter(project, chapter.id, saved.id, '另一次交割。')
  const manual = await saveLocalChapter(project, chapter.id, changed.id, saved.content)
  expect((await readLocalSettingUse(project, chapter.id)).records[0].status).toBe('outdated')
  expect((await exportLocalManuscript(project)).setting_use_history?.native_bindings).toBeUndefined()
  const independent = await saveLocalChapter(project, chapter.id, manual.id, '这次没有交割。')
  const oldHead = {
    format,
    kind: 'chapter' as const,
    chapter: { ...chapter, revision: independent.revision },
    head: independent,
  }
  await applySyncedObject(
    project,
    `chapter:${chapter.id}`,
    { ...oldHead, chapter: { ...chapter, revision: saved.revision }, head: saved },
    syncObjectSignature(oldHead),
  )
  const adopted = (await readLocalHistory(project, chapter.id)).at(-1)!
  expect(adopted.id).not.toBe(saved.id)
  expect(adopted.revision).toBeGreaterThan(manual.revision)
  expect((await readLocalSettingUse(project, chapter.id)).records[0].status).toBe('current')
  const pack = await exportLocalManuscript(project)
  expect(pack.setting_use_history?.native_bindings).toEqual({ [saved.id]: adopted.id })
  await expect(
    validateManuscript({
      ...pack,
      setting_use_history: {
        ...pack.setting_use_history,
        native_bindings: {
          [adopted.id]: saved.id,
        },
      },
    }),
  ).rejects.toThrow('正文采用关系')

  const backup = await exportLocalBackup(project)
  vi.stubGlobal('indexedDB', new IDBFactory())
  const restored = await importLocalBackup(backup)
  expect((await readLocalSettingUse(restored.novel.id, chapter.id)).records[0]).toMatchObject({
    status: 'current',
    quote: '😀阿陶',
  })
})
