import 'fake-indexeddb/auto'
import { expect, it } from 'vitest'
import { bodyChars } from './offlineRules'
import {
  confirmLocalOutline,
  confirmLocalStory,
  createLocalChapter,
  createLocalProject,
  readLocalHistory,
  readLocalRulePreview,
  readLocalStory,
  saveLocalChapter,
} from './offlineStore'
import { outlinePromiseSeed, prepareOutlineVersion } from './outlineSyncModel'
import { captureStoryEvidence } from './storySyncModel'

it('按 M6 同一有效字符口径排除 Markdown 标记、空白和不可见字符', () => {
  expect(bodyChars('## 排版标题\n- **铜铃** [响了](https://example.invalid)\n---\n> 入门。😀\u200b')).toBe(8)
  expect(bodyChars('٣. 甲 [乙](url) ![插图](file)\n~~~text\n戊\u200b，😀\n~~~\u0085')).toBe(5)
  expect(bodyChars('### 排版标题\r\n   ***\r\n\u00a0\u2028\ufeff\u200b')).toBe(1)
  expect(bodyChars('   ***\n\u00a0\u2028\ufeff\u200b')).toBe(0)
  // Chromium's newer Unicode classifies U+088F as assigned. Desktop Unicode 16 does not.
  expect(bodyChars('甲\u088f\u{1fa89}')).toBe(2)
  expect(bodyChars('甲>乙\n> 引')).toBe(4)
})

it.each([undefined, 'author_required'] as const)(
  '按正式稿计算伏笔窗口和原文有效性（%s）',
  async (fulfillment) => {
    const { novel } = await createLocalProject('离线规则核算')
    const work = novel.id
    const first = await createLocalChapter(work, '入门')
    const second = await createLocalChapter(work, '检查')
    const last = await createLocalChapter(work, '追问')
    async function save(id: string, text: string) {
      const previous = (await readLocalHistory(work, id)).at(-1)!
      return saveLocalChapter(work, id, previous.id, text)
    }
    const base = await save(first.id, '## 第一章\n- [铃](https://example.invalid)😀。')
    expect(await readLocalRulePreview(work)).toMatchObject({
      book: { chapters: 3, chars: 3, empty: 2, stale: 0 },
      outlineRevision: undefined,
      chapters: [
        { id: first.id, revisionId: base.id, chars: 3 },
        { id: second.id, chars: 0 },
        { id: last.id, chars: 0 },
      ],
    })
    const seed = outlinePromiseSeed('是谁藏起铜铃', '')
    const chapter = (chapter_id: string) => ({
      chapter_id,
      goal: '找到铜铃',
      obstacle: '门打不开',
      turn: '发现记号',
      result: '确认下一步',
      setting_ids: [],
    })
    const outline = prepareOutlineVersion({
      id: 'outline-local',
      parent_id: null,
      project_version_id: null,
      settings_version_id: null,
      plan: {
        summary: '查找铃声的来源',
        volumes: [
          {
            id: 'start',
            title: '起因',
            summary: '进入门内',
            units: [{ id: 'intro', title: '入门', summary: '查看铜铃', chapters: [chapter(first.id)] }],
          },
          {
            id: 'end',
            title: '查证',
            summary: '查找原因',
            units: [
              {
                id: 'clues',
                title: '线索',
                summary: '辨别物件',
                chapters: [chapter(second.id), chapter(last.id)],
              },
            ],
          },
        ],
        promises: [
          {
            promise_id: 'bell',
            plant_chapter_id: first.id,
            opens_chapter_id: second.id,
            closes_chapter_id: last.id,
            ...(fulfillment ? { fulfillment } : {}),
          },
        ],
      },
      promise_sources: { bell: seed },
    })
    await confirmLocalOutline(work, null, outline)
    const before = await readLocalRulePreview(work)
    expect(before.outlineRevision).toBe(outline.id)
    expect(before.volumes).toEqual([
      { id: 'start', title: '起因', chapters: 1, chars: 3, empty: 0, stale: 0 },
      { id: 'end', title: '查证', chapters: 2, chars: 0, empty: 2, stale: 0 },
    ])
    expect(before.promises).toEqual([])
    await save(second.id, '____')
    await expect(readLocalRulePreview(work)).resolves.toMatchObject({
      book: { chars: 3, empty: 2 },
      promises: [{ id: 'bell', state: 'open' }],
    })
    const resolution = await save(last.id, '师姐找到了藏起来的铜铃。')
    await expect(readLocalRulePreview(work)).resolves.toMatchObject({
      book: { empty: 1 },
      promises: [{ id: 'bell', state: 'due', chapter: { position: 3, title: '追问' } }],
    })
    const story = await readLocalStory(work)
    const plantedAt = base.content.indexOf('铃')
    await confirmLocalStory(work, {
      record_id: 'bell',
      baseStoryRevision: story.storyRevision,
      baseVersionId: null,
      archived: false,
      record: {
        ...seed,
        stages: [
          ...seed.stages,
          { status: 'planted', event_id: null, sources: [], note: '' },
          { status: 'resolved', event_id: null, sources: [], note: '' },
        ],
      },
      evidence: {
        record: [],
        stages: [
          [],
          [await captureStoryEvidence(base, plantedAt, plantedAt + 1)],
          [await captureStoryEvidence(resolution, 0, 2)],
        ],
      },
    })
    await expect(readLocalRulePreview(work)).resolves.toMatchObject({ promises: [] })
    await save(last.id, '门内没有找到铜铃。')
    await expect(readLocalRulePreview(work)).resolves.toMatchObject({
      promises: [
        fulfillment ? { id: 'bell', state: 'due', required: true } : { id: 'bell', state: 'review' },
      ],
    })
  },
)
