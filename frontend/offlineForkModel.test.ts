import 'fake-indexeddb/auto'
import { expect, it } from 'vitest'
import { buildOfflineFork, type OfflineForkSelection } from './offlineForkModel'
import {
  exportLocalManuscript,
  importLocalManuscript,
  manuscriptHash,
  readLocalHistory,
  readLocalOutline,
  readLocalStory,
  saveLocalChapter,
  validateManuscript,
  type LocalRevision,
  type ManuscriptPack,
} from './offlineStore'
import { outlineCardFromReference, outlinePromiseSeed } from './outlineSyncModel'
import { blankProjectBrief } from './projectSyncModel'
import type { ArchivedReferenceCard } from './referenceArchive'
import { prepareSettingsVersion, settingCategories } from './settingsSyncModel'
import {
  buildStoryVersion,
  captureStoryEvidence,
  compactJSON,
  parseStoryRecord,
  storyReferences,
  type StoryHistory,
} from './storySyncModel'

const branchId = 'original-work'
const digest = manuscriptHash
const settingsDevice = 'writer'

function fork(source: ManuscriptPack, choice: OfflineForkSelection, prefix = 'forked') {
  let index = 0
  return buildOfflineFork(source, choice, '分歧副本', {
    validate: validateManuscript,
    digest,
    id: () => prefix + '-' + ++index,
    now: () => 123,
  })
}

async function revision(
  id: string,
  chapter_id: string,
  content: string,
  entry?: LocalRevision,
): Promise<LocalRevision> {
  return {
    id,
    chapter_id,
    content,
    sha256: await digest(content),
    revision: (entry?.revision ?? -1) + 1,
    parent_id: entry?.id ?? null,
    cause: '',
    created: 0,
  }
}

async function fixture() {
  const initial = await revision('initial', 'first', '')
  const native = await revision('saved', 'first', '铃😀响后师姐退回门内。', initial)
  const source = { ...native, id: 'adopted-source' }
  const alternative = await revision('remote-choice', 'first', '门内没有响铃。', initial)
  const nextInitial = await revision('next-initial', 'second', '')
  const next = await revision('next-saved', 'second', '第二章追查门内的铜铃。', nextInitial)
  const proof = await captureStoryEvidence(source, 1, 3)
  const brief = {
    ...blankProjectBrief,
    position: '追索遗铃',
    track: '线索悬疑',
    audience: '悬疑读者',
    distinction: '依响铃判断门的位置',
    length: '十万字',
    ending: '找到守门人',
    genre_ids: ['xianxia'],
  }
  const categories = settingCategories.map(([id, title]) => ({
    id,
    title,
    items:
      id === 'world'
        ? [
            { id: 'bell', title: '铃声限制', detail: '铜铃走到门内才响。' },
            { id: 'observed', title: '铜铃的观察', detail: '师姐曾退回门内。' },
          ]
        : [],
  }))
  const origins = {
    bell: { origin: 'author' },
    observed: { origin: 'author', project_version_id: 'first-project', evidence: proof },
  }
  const settings = await prepareSettingsVersion({
    id: 'first-settings',
    parent_id: null,
    project_version_id: 'first-project',
    categories,
    origins,
  })
  const reconfirmed = await prepareSettingsVersion({
    ...settings,
    id: 'current-settings',
    parent_id: settings.id,
    project_version_id: 'current-project',
    reconciled_project_items: ['observed'],
  })

  const referenceDraft: Omit<ArchivedReferenceCard, 'id'> = {
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
  const former = { ...referenceDraft, id: await digest(compactJSON([branchId, referenceDraft])) }
  const quote = '参😀'
  const readyDraft: typeof referenceDraft = {
    ...referenceDraft,
    parent_id: former.id,
    revision: 2,
    assessment: 'ready',
    goal: '分析线索的推进力度',
    missing_fields: [],
    card: {
      observation: '只提供一部分证据',
      interpretation: '推进下一步选择',
      technique: '局部收获导向调查',
      conditions: '人物有办法继续调查',
      exceptions: '替换原人物和道具',
    },
    citation: {
      title: '作者可引用的资料',
      book_id: 'book',
      unit_id: 'unit',
      file_sha256: await digest(quote),
      selection_sha256: await digest(quote),
      evidence_sha256: await digest(quote),
      selection_start: 0,
      selection_end: 3,
      evidence_start: 0,
      evidence_end: 3,
    },
  }
  const pinned = { ...readyDraft, id: await digest(compactJSON([branchId, readyDraft])) }
  const summary = outlineCardFromReference(pinned)!

  const story: StoryHistory = { adopted_heads: {}, versions: [], source_devices: {}, native_history: {} }
  const last = new Map<string, number>()
  async function add(record_id: string, draft: unknown, evidence = false) {
    const record = parseStoryRecord(draft)
    const deps = [...storyReferences(record)]
    const dependency = (id: string) => story.adopted_heads[id]
    const version = await buildStoryVersion(branchId, {
      parent_id: dependency(record_id) ?? null,
      record_id,
      archived: false,
      record,
      evidence: {
        record: evidence ? [proof] : [],
        stages:
          record.kind === 'promise'
            ? record.stages.map((stage) => (stage.status === 'planned' ? [] : [proof]))
            : [],
      },
      dependencies: Object.fromEntries(deps.map((id) => [id, dependency(id)])),
    })
    const revision = (last.get(record_id) ?? -1) + 1
    story.versions.push(version)
    story.adopted_heads[record_id] = version.id
    story.source_devices[version.id] = settingsDevice
    ;(story.native_history[record_id] ??= []).push({
      revision,
      version_id: version.id,
      created: 1,
      dependencies: Object.fromEntries(deps.map((id) => [id, last.get(id)!])),
      evidence: {
        record: version.evidence.record.map(() => native.id),
        stages: version.evidence.stages.map((group) => group.map(() => native.id)),
      },
    })
    last.set(record_id, revision)
  }
  await add('observer', { kind: 'character', title: '守门人' })
  await add('observer', { kind: 'character', title: '守门人', profile: '管着唯一的门。' })
  await add('owner', { kind: 'character', title: '师姐' })
  await add(
    'event',
    { kind: 'event', title: '响铃', status: 'actual', story_order: 1, participants: ['owner'] },
    true,
  )
  await add(
    'next-event',
    {
      kind: 'event',
      title: '复查',
      status: 'actual',
      story_order: 2,
      participants: ['observer'],
      after: ['event'],
    },
    true,
  )
  await add(
    'state',
    {
      kind: 'state',
      title: '获知限制',
      character_id: 'owner',
      event_id: 'event',
      until_event_id: 'next-event',
      category: 'knowledge',
      key: '开门的记号',
      value: '铃声有两次',
      audience: 'character',
      truth: 'actual',
    },
    true,
  )
  await add(
    'relation',
    {
      kind: 'relationship',
      title: '认识了守门人',
      from_id: 'owner',
      to_id: 'observer',
      event_id: 'next-event',
      relation: '见过',
      truth: 'actual',
    },
    true,
  )
  const seed = outlinePromiseSeed('谁掌管门铃', '找到真正控制铃声的人。')
  await add('promise', seed)
  await add('promise', {
    ...seed,
    stages: [
      { status: 'planned', event_id: '' },
      { status: 'planted', event_id: 'event' },
      { status: 'resolved', event_id: 'next-event' },
    ],
  })

  const pack: ManuscriptPack = {
    format: 'ainovel-manuscript/1',
    novel: { id: 'original-novel', title: '原稿', brief: '追索遗铃。', created: 0 },
    branch: {
      id: branchId,
      novel_id: 'original-novel',
      name: '主线',
      mode: 'advisory',
      story_revision: 10,
      created: 0,
    },
    chapters: ['first', 'second'].map((id, index) => ({
      id,
      branch_id: branchId,
      position: index + 1,
      revision: 1,
      title: '章节 ' + (index + 1),
      archived: 0,
      stale: 0,
    })),
    revisions: [initial, native, nextInitial, next],
    source_revisions: [source, alternative],
    evidence: [
      {
        id: 'original-report',
        kind: 'inspection',
        title: '原章检查',
        body: '在原章核对铃声。',
        sha256: await digest('在原章核对铃声。'),
        chapters: [
          { id: native.chapter_id, revision: native.revision, revision_id: native.id, sha256: native.sha256 },
        ],
      },
    ],
    project_history: {
      adopted_revision_id: 'current-project',
      versions: [
        { id: 'first-project', parent_id: null, idea: '查找铜铃。', brief },
        { id: 'current-project', parent_id: 'first-project', idea: '追查开门记号。', brief },
      ],
    },
    settings_history: {
      adopted_revision_id: reconfirmed.id,
      versions: [settings, reconfirmed],
      source_devices: { [settings.id]: settingsDevice },
    },
    reference_history: {
      adopted_heads: [{ session_id: pinned.session_id, card_id: pinned.card_id, revision_id: pinned.id }],
      versions: [former, pinned],
      source_devices: { [pinned.id]: settingsDevice },
      excerpts: { [pinned.id]: quote },
    },
    story_history: story,
    outline_history: {
      adopted_revision_id: 'current-outline',
      versions: ['initial-outline', 'current-outline'].map((id, index) => ({
        id,
        parent_id: index ? 'initial-outline' : null,
        project_version_id: 'current-project',
        settings_version_id: reconfirmed.id,
        reference_cards: [summary],
        plan: {
          summary: '根据铃声核对门内证据。',
          volumes: [
            {
              id: 'volume',
              title: '遗铃案',
              summary: '识别门内的铜铃。',
              units: [
                {
                  id: 'unit',
                  title: '核查',
                  summary: '检查门的位置。',
                  chapters: ['first', 'second'].map((chapter_id) => ({
                    chapter_id,
                    goal: '找到铃',
                    obstacle: '门有人守',
                    turn: '看到记号',
                    result: '辨认了门的位置',
                    setting_ids: ['bell'],
                    reference_cards: [
                      {
                        session_id: pinned.session_id,
                        card_id: pinned.card_id,
                        revision: pinned.revision,
                        version_id: pinned.id,
                      },
                    ],
                  })),
                },
              ],
            },
          ],
          promises: ['promise', 'plan-only'].map((id) => ({
            promise_id: id,
            plant_chapter_id: 'first',
            opens_chapter_id: 'first',
            closes_chapter_id: 'second',
          })),
        },
        promise_sources: { promise: seed, 'plan-only': outlinePromiseSeed('独立伏笔', '') },
      })),
      source_devices: { 'initial-outline': settingsDevice },
      required_story_records: ['promise'],
    },
  }
  const selection = {
    chapterId: alternative.chapter_id,
    revisionId: alternative.id,
    content: alternative.content,
    sha256: alternative.sha256,
  }
  return { pack, selection, native, source, pinned, former }
}

it('只在新作品中保存所选章稿，书内版本引用、故事事实和历史证据重新核对', async () => {
  const f = await fixture()
  const original = JSON.stringify(f.pack)
  const originalProject = await importLocalManuscript(new Blob([original]))
  const { pack, provenance } = await fork(f.pack, f.selection)
  await expect(validateManuscript(pack)).resolves.toEqual(pack)
  expect(JSON.stringify(f.pack)).toBe(original)
  expect(provenance).toMatchObject({
    sourceBranchId: branchId,
    chapterId: 'first',
    sourceRevisionId: 'remote-choice',
  })
  expect(provenance.newChapterId).not.toBe('first')
  expect(pack.novel.title).toBe('分歧副本')
  expect(pack.branch.id).not.toBe(branchId)
  expect(pack.chapters.map(({ stale, revision }) => [stale, revision])).toEqual([
    [1, 2],
    [1, 1],
  ])
  expect(pack.evidence).toBeUndefined()
  const selected = pack.revisions.find(({ id }) => id === provenance.newRevisionId)!
  const prior = pack.revisions.find(({ id }) => id === selected.parent_id)!
  expect(selected).toMatchObject({ content: f.selection.content, sha256: f.selection.sha256 })
  expect(prior).toMatchObject({ content: f.native.content, sha256: f.native.sha256 })
  expect(pack.source_revisions?.map(({ sha256 }) => sha256)).toEqual([f.source.sha256, selected.sha256])
  expect(pack.source_revisions?.every(({ parent_id }) => parent_id === prior.parent_id)).toBe(true)

  const [firstProject, project] = pack.project_history!.versions
  expect(project.parent_id).toBe(firstProject.id)
  expect(pack.project_history!.adopted_revision_id).toBe(project.id)
  const [firstSettings, settings] = pack.settings_history!.versions
  expect(settings.parent_id).toBe(firstSettings.id)
  expect(pack.settings_history!.adopted_revision_id).toBe(settings.id)
  expect(settings.project_version_id).toBe(project.id)
  expect(settings.origins.observed).toMatchObject({
    project_version_id: firstProject.id,
    evidence: { chapter_id: provenance.newChapterId, revision_id: pack.source_revisions![0].id },
  })
  expect(settings.reconciled_project_items).toEqual(['observed'])
  const [oldReference, pinned] = pack.reference_history!.versions
  expect(pinned.id).not.toBe(f.pinned.id)
  expect(pinned.parent_id).toBe(oldReference.id)
  expect(pack.reference_history?.excerpts).toBeUndefined()
  expect(pack.reference_history?.source_devices[pinned.id]).toBe(settingsDevice)
  expect(pack.reference_history?.adopted_heads[0].revision_id).toBe(pinned.id)
  expect(pinned.citation).toEqual(f.pinned.citation)

  const [before, outline] = pack.outline_history!.versions
  expect(pack.outline_history!.adopted_revision_id).toBe(outline.id)
  expect(outline).toMatchObject({
    parent_id: before.id,
    project_version_id: project.id,
    settings_version_id: settings.id,
  })
  expect(outline.reference_cards[0].version_id).toBe(pinned.id)
  expect(outline.plan.volumes[0].units[0].chapters[0]).toMatchObject({
    chapter_id: provenance.newChapterId,
    reference_cards: [{ version_id: pinned.id }],
  })
  const story = pack.story_history!
  const head = (title: string) =>
    story.versions.find(
      (version) => version.record.title === title && story.adopted_heads[version.record_id] === version.id,
    )!
  const observer = head('守门人')
  const owner = head('师姐')
  const firstEvent = head('响铃')
  const nextEvent = head('复查')
  const activePromise = head('谁掌管门铃')
  expect(story.native_history[observer.record_id]).toHaveLength(2)
  expect(observer.parent_id).toBe(story.native_history[observer.record_id][0].version_id)
  expect(nextEvent.record).toMatchObject({
    participants: [observer.record_id],
    after: [firstEvent.record_id],
  })
  expect(nextEvent.dependencies).toEqual({
    [observer.record_id]: observer.id,
    [firstEvent.record_id]: firstEvent.id,
  })
  expect(head('获知限制').record).toMatchObject({
    character_id: owner.record_id,
    event_id: firstEvent.record_id,
    until_event_id: nextEvent.record_id,
  })
  expect(head('认识了守门人').record).toMatchObject({
    from_id: owner.record_id,
    to_id: observer.record_id,
    event_id: nextEvent.record_id,
  })
  expect(activePromise.record).toMatchObject({
    stages: [
      { status: 'planned', event_id: '' },
      { status: 'planted', event_id: firstEvent.record_id },
      { status: 'resolved', event_id: nextEvent.record_id },
    ],
  })
  expect(activePromise.evidence.stages[2][0].revision_id).toBe(pack.source_revisions![0].id)
  expect(story.native_history[activePromise.record_id][1].evidence.stages[2]).toEqual([prior.id])
  expect(pack.outline_history?.required_story_records).toEqual([activePromise.record_id])
  const [progressed, planOnly] = outline.plan.promises
  expect(progressed.promise_id).toBe(activePromise.record_id)
  expect(progressed.closes_chapter_id).toBe(pack.chapters[1].id)
  expect(story.adopted_heads).not.toHaveProperty(planOnly.promise_id)
  expect(outline.promise_sources[planOnly.promise_id].stages).toMatchObject([{ status: 'planned' }])

  const copy = await importLocalManuscript(new Blob([JSON.stringify(pack)]))
  expect((await readLocalOutline(copy.novel.id)).current?.version.id).toBe(outline.id)
  const copiedStories = await readLocalStory(copy.novel.id)
  expect(
    copiedStories.records.find(({ version }) => version.id === activePromise.id)?.invalidReasons,
  ).toContain('来源章节已有变化或待复检。')
  await saveLocalChapter(copy.novel.id, provenance.newChapterId, selected.id, '作者在副本继续改稿。')
  expect((await readLocalHistory(copy.novel.id, provenance.newChapterId)).at(-1)?.parent_id).toBe(selected.id)
  expect((await readLocalHistory(originalProject.novel.id, 'first')).at(-1)?.content).toBe(f.native.content)
  expect((await exportLocalManuscript(copy.novel.id)).evidence).toBeUndefined()
  expect((await exportLocalManuscript(originalProject.novel.id)).evidence?.[0].id).toBe('original-report')
})

it('拒绝错绑同编号正文、伪造摘要及父修订缺失，原字节一致时不追加无内容变化的版', async () => {
  const { pack, selection, native, former } = await fixture()
  await expect(
    fork(pack, { ...selection, content: '不同正文', sha256: await digest('不同正文') }),
  ).rejects.toThrow('同一来源修订不一致')
  await expect(fork(pack, { ...selection, chapterId: 'second' })).rejects.toThrow('同一来源修订不一致')
  await expect(fork(pack, { ...selection, sha256: '0'.repeat(64) })).rejects.toThrow('同一来源修订不一致')
  await expect(fork(pack, { ...selection, revisionId: 'not-known-yet' })).rejects.toThrow('先同步原始版本')
  const missing = structuredClone(pack)
  missing.reference_history!.versions = missing.reference_history!.versions.filter(
    ({ id }) => id !== former.id,
  )
  await expect(fork(missing, selection)).rejects.toThrow('父修订')
  const tampered = structuredClone(pack)
  tampered.story_history!.versions.at(-1)!.record.title = '换掉了原始故事记录'
  await expect(fork(tampered, selection)).rejects.toThrow('版本哈希')
  const unchanged = await fork(pack, {
    ...selection,
    revisionId: native.id,
    sha256: native.sha256,
    content: native.content,
  })
  expect(unchanged.pack.chapters[0]).toMatchObject({ revision: 1, stale: 0 })
  expect(unchanged.pack.revisions).toHaveLength(pack.revisions.length)
})

it('可把归档章的已核对在用来源只恢复到副本，被复用的位置顺延', async () => {
  const { pack, selection, native } = await fixture()
  pack.chapters[0].archived = 1
  const free = await fork(pack, {
    ...selection,
    revisionId: native.id,
    content: native.content,
    sha256: native.sha256,
  })
  expect(free.pack.chapters[0]).toMatchObject({ archived: 0, revision: 1, position: 1, stale: 1 })
  expect(free.pack.chapters[1]).toMatchObject({ stale: 1 })

  pack.chapters[1].position = 1
  const original = JSON.stringify(pack)
  const moved = await fork(pack, selection, 'archived-fork')
  const [occupant, revived] = moved.pack.chapters
  expect(occupant).toMatchObject({ archived: 0, position: 1, stale: 0 })
  expect(revived).toMatchObject({
    id: moved.provenance.newChapterId,
    archived: 0,
    revision: 2,
    position: 2,
    stale: 1,
  })
  expect(JSON.stringify(pack)).toBe(original)
  await expect(validateManuscript(moved.pack)).resolves.toEqual(moved.pack)
  const project = await importLocalManuscript(new Blob([JSON.stringify(moved.pack)]))
  const outline = await readLocalOutline(project.novel.id)
  expect(outline.current).toBeUndefined()
  expect(outline.versions).toHaveLength(2)
  expect(outline.restorePending?.reason).toContain('章节目录一致')
  expect((await readLocalHistory(project.novel.id, revived.id)).at(-1)?.content).toBe(selection.content)
})
