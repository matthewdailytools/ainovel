import 'fake-indexeddb/auto'
import { expect, it } from 'vitest'
import {
  applySyncedObject,
  confirmLocalOutline,
  createLocalChapter,
  createLocalProject,
  exportLocalManuscript,
  importLocalManuscript,
  readLocalOutline,
  readLocalPublications,
  syncObjectSignature,
  validateSyncedObject,
} from './offlineStore'
import {
  outlinePromiseSeed,
  type OutlineHeadObject,
  type OutlineHistory,
  type OutlineVersion,
} from './outlineSyncModel'

function plan(chapterId: string): OutlineVersion {
  return {
    id: 'local-first',
    parent_id: null,
    project_version_id: null,
    settings_version_id: null,
    plan: {
      summary: '找到是谁换了铜铃',
      volumes: [
        {
          id: 'volume',
          title: '第一卷',
          summary: '记录旧城线索',
          units: [
            {
              id: 'unit',
              title: '寻铃',
              summary: '先比较铸记',
              chapters: [
                {
                  chapter_id: chapterId,
                  goal: '找到真铃',
                  obstacle: '夜色',
                  turn: '发现划痕',
                  result: '锁定证人',
                  setting_ids: [],
                },
              ],
            },
          ],
        },
      ],
      promises: [
        {
          promise_id: 'bell-origin',
          plant_chapter_id: chapterId,
          opens_chapter_id: chapterId,
          closes_chapter_id: chapterId,
        },
      ],
    },
    reference_cards: [],
    promise_sources: { 'bell-origin': outlinePromiseSeed('遗铃来历', '对比正反两段铸记。') },
  }
}

it('确认原子保存大纲版本、采用头、计划种子与发送标记，拒绝旧窗口及种子改写', async () => {
  const work = await createLocalProject('燕影 · 原子确认')
  const chapter = await createLocalChapter(work.novel.id, '发现铸记')
  const original = plan(chapter.id)
  await confirmLocalOutline(work.novel.id, null, original)
  const current = await readLocalOutline(work.novel.id)
  expect(current.current?.version).toEqual(original)
  expect(current.promises).toEqual([
    { id: 'bell-origin', title: '遗铃来历', seed: original.promise_sources['bell-origin'] },
  ])
  expect((await readLocalPublications(work.branch.id)).map(({ objectId }) => objectId)).toEqual(
    expect.arrayContaining([`outline:${work.branch.id}`, 'outline-version:local-first']),
  )

  const next = { ...original, id: 'local-next', parent_id: original.id }
  await expect(confirmLocalOutline(work.novel.id, null, next)).rejects.toThrow('当前确认依据不一致')
  const colliding = {
    ...next,
    promise_sources: { 'bell-origin': outlinePromiseSeed('同编号但另一个伏笔', '') },
  }
  await expect(confirmLocalOutline(work.novel.id, original.id, colliding)).rejects.toThrow('初始计划不一致')
  expect(await readLocalOutline(work.novel.id)).toEqual(current)
  expect(
    (await readLocalPublications(work.branch.id)).some(
      ({ objectId }) => objectId === 'outline-version:local-next',
    ),
  ).toBe(false)
})

it('先缓存远端来源，采用时核对章目录和发送标记，保留历史与原伏笔种子', async () => {
  const work = await createLocalProject('燕影 · 远端采用')
  const chapter = await createLocalChapter(work.novel.id, '发现铸记')
  const branch_id = work.branch.id
  const branch = `outline:${branch_id}`
  const options = { source: { device_id: 'desktop', change_id: 'change', sequence: 1 } }
  const original = plan(chapter.id)
  await confirmLocalOutline(work.novel.id, null, original)
  const localHead: OutlineHeadObject = {
    format: 'ainovel-manuscript-sync/1',
    kind: 'outline-head',
    branch_id,
    revision_id: original.id,
  }
  const version = {
    ...original,
    id: 'desktop-next',
    parent_id: original.id,
    plan: { ...original.plan, summary: '查找桌面找到的新证人' },
  }
  const parsed = await validateSyncedObject(work, 'outline-version:desktop-next', {
    format: 'ainovel-manuscript-sync/1',
    kind: 'outline-version',
    branch_id,
    version,
  })
  expect(
    await applySyncedObject(work.novel.id, 'outline-version:desktop-next', parsed, 'null', options),
  ).toBe(true)
  expect((await readLocalOutline(work.novel.id)).current?.version.id).toBe(original.id)
  const remoteHead = { ...localHead, revision_id: version.id }
  const expected = syncObjectSignature(localHead)
  expect(
    await applySyncedObject(work.novel.id, branch, remoteHead, expected, { publicationId: 'old-marker' }),
  ).toBe(false)
  const publication = (await readLocalPublications(branch_id)).find(({ objectId }) => objectId === branch)!
  expect(
    await applySyncedObject(work.novel.id, branch, remoteHead, expected, {
      publicationId: publication.id,
      consumePublication: true,
    }),
  ).toBe(true)
  const chosen = await readLocalOutline(work.novel.id)
  expect(chosen.current?.version.id).toBe('desktop-next')
  expect(chosen.versions.map((row) => row.version.id).sort()).toEqual(['desktop-next', 'local-first'])
  expect(chosen.promises[0].seed).toEqual(original.promise_sources['bell-origin'])
  expect((await readLocalPublications(branch_id)).some(({ objectId }) => objectId === branch)).toBe(false)
})

it('交换包保留采用大纲和来源设备，缺失实际伏笔时留档并延后采用', async () => {
  for (const [id, required] of [
    ['planned', []],
    ['advanced', ['bell-origin']],
  ] as const) {
    const branchId = `archive-${id}`
    const novelId = `outline-novel-${id}`
    const chapterId = `opening-${id}`
    const version = plan(chapterId)
    const outline_history: OutlineHistory = {
      adopted_revision_id: version.id,
      versions: [version],
      source_devices: { [version.id]: 'archived-desktop' },
      ...(required.length ? { required_story_records: [...required] } : {}),
    }
    const pack = {
      format: 'ainovel-manuscript/1',
      novel: { id: novelId, title: '交换大纲 · 遗铃', brief: '', created: 1 },
      branch: {
        id: branchId,
        novel_id: novelId,
        name: '默认',
        mode: 'advisory',
        story_revision: 0,
        created: 1,
      },
      chapters: [
        {
          id: chapterId,
          branch_id: branchId,
          position: 1,
          title: '门口识铃',
          revision: 0,
          archived: 0,
          stale: 0,
        },
      ],
      revisions: [
        {
          id: `chapter-revision-${id}`,
          chapter_id: chapterId,
          revision: 0,
          parent_id: null,
          content: '',
          sha256: 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
          cause: 'create',
          created: 1,
        },
      ],
      outline_history,
    }
    await importLocalManuscript(new Blob([JSON.stringify(pack)]))
    const data = await readLocalOutline(novelId)
    expect(data.versions[0]).toMatchObject({ recordedDeviceId: 'archived-desktop', version })
    expect((await exportLocalManuscript(novelId)).outline_history).toEqual(outline_history)
    if (!required.length) {
      expect(data.current?.version.id).toBe(version.id)
      expect(data.promises[0].seed).toEqual(version.promise_sources['bell-origin'])
      continue
    }
    expect(data.current).toBeUndefined()
    expect(data.promises).toEqual([])
    expect(data.restorePending).toMatchObject({ revisionId: version.id, requiredStoryRecords: required })
    const objectId = `outline-version:${version.id}`
    const source = { device_id: 'cloud-desktop', change_id: 'cloud-change', sequence: 1 }
    expect(
      await applySyncedObject(
        novelId,
        objectId,
        {
          format: 'ainovel-manuscript-sync/1',
          kind: 'outline-version',
          branch_id: branchId,
          version,
        },
        'null',
        { source },
      ),
    ).toBe(true)
    await expect(
      applySyncedObject(
        novelId,
        `outline:${branchId}`,
        {
          format: 'ainovel-manuscript-sync/1',
          kind: 'outline-head',
          branch_id: branchId,
          revision_id: version.id,
        },
        'null',
        {},
      ),
    ).rejects.toThrow('等待原伏笔记录 bell-origin')
    expect((await readLocalOutline(novelId)).promises).toEqual([])
  }
})
