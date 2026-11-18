import { describe, expect, it } from 'vitest'
import {
  describeOutlineVersion,
  outlineCardFromReference,
  outlinePromiseSeed,
  prepareOutlineVersion,
  validateOutlineActiveBasis,
  validateOutlineHistory,
  validateOutlineSyncObject,
  validateOutlineVersionSources,
} from './outlineSyncModel'
import type { ArchivedReferenceCard } from './referenceArchive'
import { prepareSettingsVersion, settingCategories } from './settingsSyncModel'

function fixture() {
  const address = { session_id: 'reference', card_id: 'method', revision: 3 }
  const chapter = (id: string) => ({
    chapter_id: id,
    goal: '找信物',
    obstacle: '身份无法核验',
    turn: '发现伪证',
    result: '锁定证人',
    setting_ids: ['bell'],
    reference_cards: [address],
  })
  const plan = {
    summary: '追查遗铃',
    volumes: [
      {
        id: 'volume',
        title: '遗铃',
        summary: '找出替换者',
        units: [
          { id: 'unit', title: '旧巷', summary: '收集证供', chapters: [chapter('first'), chapter('second')] },
        ],
      },
    ],
    promises: [
      {
        promise_id: 'promise',
        plant_chapter_id: 'first',
        opens_chapter_id: 'first',
        closes_chapter_id: 'second',
      },
    ],
  }
  return {
    id: 'outline-two',
    parent_id: 'outline-one',
    project_version_id: 'project',
    settings_version_id: 'settings',
    plan,
    reference_cards: [
      {
        ...address,
        technique: '换身份时展示线索',
        conditions: '读者知情',
        exceptions: '不得原样引用',
        source_title: '对标作品',
      },
    ],
    promise_sources: { promise: outlinePromiseSeed('铃声变调', '后续比较两次铃声') },
  }
}

async function setting(observed = false) {
  return prepareSettingsVersion({
    id: 'settings',
    parent_id: null,
    project_version_id: 'project',
    categories: settingCategories.map(([id, title]) => ({
      id,
      title,
      items: id === 'world' ? [{ id: 'bell', title: '遗铃', detail: '响两次' }] : [],
    })),
    origins: {
      bell: {
        origin: 'author',
        ...(observed
          ? {
              evidence: {
                chapter_id: 'first',
                revision_id: 'original',
                start: 0,
                end: 1,
                quote_sha256: '1'.repeat(64),
              },
            }
          : {}),
      },
    },
  })
}

describe('离线大纲正式版本', () => {
  it('保留四级计划、冻结卡和计划种子，并拒绝同修订地址错配与额外原文', () => {
    const input = fixture()
    const payload = {
      format: 'ainovel-manuscript-sync/1',
      kind: 'outline-version',
      branch_id: 'work',
      version: input,
    }
    const wire = validateOutlineSyncObject('work', 'outline-version:outline-two', payload)
    expect(wire).toMatchObject({
      version: {
        parent_id: 'outline-one',
        promise_sources: { promise: { sources: [], stages: [{ status: 'planned', sources: [] }] } },
        reference_cards: [{ ...input.reference_cards[0] }],
      },
    })
    expect(describeOutlineVersion(prepareOutlineVersion(input))).toContain(
      '铃声变调 · first → first → second',
    )
    expect(() => validateOutlineSyncObject('different', 'outline-version:outline-two', payload)).toThrow(
      '工作版本',
    )
    expect(() => validateOutlineSyncObject('work', 'outline-version:wrong', payload)).toThrow('修订编号')
    expect(() =>
      prepareOutlineVersion({ ...input, reference_cards: [{ ...input.reference_cards[0], quote: '原文' }] }),
    ).toThrow('无法识别')
    expect(() => prepareOutlineVersion({ ...input, reference_cards: [] })).toThrow('冻结摘要')
  })

  it('拒绝携带实际事件、正文证据或倒序窗口的正式大纲', () => {
    const input = fixture()
    const seed = input.promise_sources.promise
    expect(() =>
      prepareOutlineVersion({
        ...input,
        promise_sources: { promise: { ...seed, stages: [{ status: 'resolved' }] } },
      }),
    ).toThrow('初始计划阶段')
    expect(() =>
      prepareOutlineVersion({
        ...input,
        promise_sources: { promise: { ...seed, sources: [{ quote: '作者原文' }] } },
      }),
    ).toThrow('超出允许数量')
    expect(() =>
      prepareOutlineVersion({
        ...input,
        plan: { ...input.plan, promises: [{ ...input.plan.promises[0], plant_chapter_id: 'second' }] },
      }),
    ).toThrow('按章序')
    expect(() =>
      prepareOutlineVersion({
        ...input,
        plan: { ...input.plan, new_promises: [{ title: '未确认的计划' }] },
      }),
    ).toThrow('超出允许数量')
  })

  it('按 SHA-256 区分同号卡片，仅接受对应正式修订的冻结摘要', async () => {
    const original = fixture()
    const card: ArchivedReferenceCard = {
      id: 'a'.repeat(64),
      parent_id: null,
      ...original.plan.volumes[0].units[0].chapters[0].reference_cards[0],
      assessment: 'ready',
      card: {
        observation: '只提供一部分证据',
        interpretation: '推进下一步选择',
        technique: '先接触一处可复查线索',
        conditions: '可以核对获得的证据',
        exceptions: '替换原人物与道具',
      },
      citation: {
        book_id: 'book',
        unit_id: 'source',
        title: '异地保留的读书札记',
        file_sha256: '1'.repeat(64),
        selection_start: 2,
        selection_end: 54,
        selection_sha256: '2'.repeat(64),
        evidence_sha256: '3'.repeat(64),
        evidence_start: 4,
        evidence_end: 30,
      },
      goal: '考察作者怎样保留继续推进的动作',
      missing_fields: [],
    }
    const frozen = outlineCardFromReference(card)!
    const { session_id, card_id, revision, version_id } = frozen
    const alternate = { ...card, id: 'b'.repeat(64) }
    const unit = original.plan.volumes[0].units[0]
    const edited = {
      ...original,
      parent_id: null,
      plan: {
        ...original.plan,
        volumes: [
          {
            ...original.plan.volumes[0],
            units: [
              {
                ...unit,
                chapters: unit.chapters.map((chapter) => ({
                  ...chapter,
                  reference_cards: [{ session_id, card_id, revision, version_id }],
                })),
              },
            ],
          },
        ],
      },
      reference_cards: [frozen],
    }
    const archive = { adopted_revision_id: original.id, versions: [edited] }
    const scope = {
      projects: new Set(['project']),
      settings: new Map([['settings', await setting()]]),
      chapters: new Set(['first', 'second']),
    }
    const checked = await validateOutlineHistory(archive, {
      ...scope,
      references: new Map([
        [alternate.id, alternate],
        [card.id, card],
      ]),
    })
    expect(checked.versions[0].reference_cards[0]).toEqual(frozen)
    expect(Object.keys(frozen).sort()).toEqual(
      [
        'session_id',
        'card_id',
        'revision',
        'version_id',
        'technique',
        'conditions',
        'exceptions',
        'source_title',
      ].sort(),
    )
    await expect(
      validateOutlineHistory(archive, {
        ...scope,
        references: new Map([[alternate.id, alternate]]),
      }),
    ).rejects.toThrow('缺少大纲引用的完整正式手法卡修订')
    await expect(
      validateOutlineHistory(archive, {
        ...scope,
        references: new Map([[card.id, alternate]]),
      }),
    ).rejects.toThrow('地址或冻结摘要')
    await expect(
      validateOutlineHistory(
        {
          ...archive,
          versions: [
            {
              ...edited,
              reference_cards: [{ ...frozen, conditions: '改写适用条件' }],
            },
          ],
        },
        { ...scope, references: new Map([[card.id, card]]) },
      ),
    ).rejects.toThrow('地址或冻结摘要')
  })

  it('核对原父版本、立项设定、章节顺序和伏笔初始来源，保留真实进度边界', async () => {
    const version = prepareOutlineVersion(fixture())
    const settings = await setting()
    const observed = await setting(true)
    const ids = ['first', 'second']
    const lookup = {
      project: (id: string) => id === 'project',
      outline: (id: string) => id === 'outline-one',
      settings: () => settings,
      chapter: (id: string) => ({ id, title: id, position: ids.indexOf(id) + 1 }),
      promise: () => null,
    }
    await expect(validateOutlineVersionSources(version, lookup)).resolves.toBeUndefined()
    await expect(validateOutlineVersionSources(version, { ...lookup, outline: () => null })).rejects.toThrow(
      '父版本',
    )
    await expect(
      validateOutlineVersionSources(version, { ...lookup, settings: () => observed }),
    ).rejects.toThrow('有效世界设定')
    await expect(
      validateOutlineVersionSources(version, {
        ...lookup,
        chapter: (id) => ({ id, title: id, position: 2 - ids.indexOf(id) }),
      }),
    ).rejects.toThrow('章节目录一致')
    await expect(
      validateOutlineVersionSources(version, {
        ...lookup,
        promise: () => ({ id: 'promise', title: '铃声变调', seed: outlinePromiseSeed('不同的初始计划', '') }),
      }),
    ).rejects.toThrow('初始计划不一致')
    expect(() =>
      validateOutlineActiveBasis(version, { projectVersionId: 'project', settingsVersionId: 'other' }),
    ).toThrow('对应设定')
  })
})
