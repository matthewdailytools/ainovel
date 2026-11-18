import { describe, expect, it } from 'vitest'
import {
  describeOutlineVersion,
  outlinePromiseSeed,
  prepareOutlineVersion,
  validateOutlineActiveBasis,
  validateOutlineSyncObject,
  validateOutlineVersionSources,
} from './outlineSyncModel'
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
