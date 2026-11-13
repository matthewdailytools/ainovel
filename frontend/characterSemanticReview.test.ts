import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { expect, test } from 'vitest'
import { CharacterSemanticReview } from './StoryExtraction'
import {
  characterReviewState,
  createExtractionRequestGuard,
  extractionSourcesStale,
  readCharacterSemanticReview,
  type CharacterSemanticReview as Report,
} from './characterSemanticReview'

const report: Report = {
  version: 'character-field-entailment/1',
  candidate_revision: 3,
  input_sha256: 'a'.repeat(64),
  status: 'supported',
  results: [
    {
      record_id: 'person-1',
      field: 'profile',
      result: 'supported',
      reason: '原文明确描述其为渡工。',
      evidence: [{ chapter_id: 'chapter-1', revision: 2, start: 4, end: 10, quote: '林舟是渡工。' }],
    },
  ],
  reviewer: { account: 'test-account', provider: 'test-provider', model: 'test-model' },
  created: 1700000000,
}

function render(value: unknown, candidateRevision = 3, sourceStale = false) {
  return renderToStaticMarkup(
    createElement(CharacterSemanticReview, {
      value,
      candidateRevision,
      sourceStale,
      nameFor: (id) => (id === 'person-1' ? '林舟' : id),
      chapterNameFor: (id) => (id === 'chapter-1' ? '渡口' : id),
    }),
  )
}

test('当前有效报告保留字段证据、模型与版本，容忍派生报告额外字段', () => {
  expect(readCharacterSemanticReview({ ...report, extra: { ignored: true } })).toEqual(report)
  expect(characterReviewState(report, 3, false).state).toBe('supported')
  const html = render(report)
  for (const text of [
    '林舟 · 档案：来源支持',
    '原文明确描述其为渡工。',
    '渡口 · 保存稿修订2 · 位置4—10',
    '林舟是渡工。',
    'test-provider / test-model',
    'test-account',
    'character-field-entailment/1',
    '不等于作者认可',
    '作者仍可明确选择采用',
  ])
    expect(html).toContain(text)
})

test('缺失报告不能显示通过，刷新提示明确', () => {
  for (const value of [undefined, null]) {
    expect(characterReviewState(value, 3, false).state).toBe('missing')
    expect(render(value)).toContain('尚无人物字段复核报告')
    expect(render(value)).toContain('人工整理请直接核对原文')
    expect(render(value)).not.toContain('均有来源支持')
  }
})

test.each([
  { ...report, version: 'character-field-entailment/2' },
  { ...report, candidate_revision: '3' },
  { ...report, reviewer: { model: 'test-model' } },
  { ...report, results: [{ ...report.results[0], field: 'title' }] },
  { ...report, results: [{ ...report.results[0], result: 'passed' }] },
  {
    ...report,
    results: [{ ...report.results[0], evidence: [{ ...report.results[0].evidence[0], start: -1 }] }],
  },
  { ...report, results: 'supported' },
])('异常报告不能冒充来源支持（%#）', (value) => {
  expect(readCharacterSemanticReview(value)).toBeUndefined()
  expect(characterReviewState(value, 3, false).state).toBe('invalid')
  expect(render(value)).toContain('报告格式无法核对')
  expect(render(value)).not.toContain('均有来源支持')
})

test.each(['unknown', 'contradicted'] as const)('字段%s不因报告总状态supported而显示通过', (result) => {
  const value = { ...report, results: [{ ...report.results[0], result }] }
  expect(characterReviewState(value, 3, false).state).toBe('needs_review')
  expect(render(value)).toContain(result === 'unknown' ? '依据不足，待核对' : '与来源矛盾，待核对')
  expect(render(value)).not.toContain('均有来源支持')
})

test('候选修订变化使报告成为历史，来源stale同时保留需重新核对提示', () => {
  expect(characterReviewState(report, 4, false).state).toBe('historical')
  expect(characterReviewState(report, 4, true).state).toBe('historical')
  const html = render(report, 4, true)
  expect(html).toContain('历史报告已失效')
  expect(html).toContain('报告候选修订3；当前候选修订4')
  expect(html).toContain('来源正文或故事已变化或待复检，需重新核对')
  expect(html).not.toContain('均有来源支持')
})

test('来源正文或故事stale不沿用supported报告，空字段报告不声称通过', () => {
  expect(characterReviewState(report, 3, true).state).toBe('stale')
  expect(render(report, 3, true)).toContain('下方仅为历史报告')
  expect(render(report, 3, true)).not.toContain('均有来源支持')
  expect(characterReviewState({ ...report, results: [] }, 3, false).state).toBe('not_required')
  expect(characterReviewState({ ...report, status: 'needs_review', results: [] }, 3, false).state).toBe(
    'needs_review',
  )
})

test('保存稿修订、删除与待复检均使来源需重新核对', () => {
  const manifest = [{ id: 'chapter-1', revision: 2 }]
  const current = [{ id: 'chapter-1', revision: 2, stale: 0 }]
  expect(extractionSourcesStale(manifest, current)).toBe(false)
  expect(extractionSourcesStale(manifest, [{ ...current[0], revision: 3 }])).toBe(true)
  expect(extractionSourcesStale(manifest, [{ ...current[0], stale: 1 }])).toBe(true)
  expect(extractionSourcesStale(manifest, [])).toBe(true)
})

test('复核理由和引用以文本展示，保留中文及不执行HTML', () => {
  const text = '<script>模型原话😀𠮷</script>'
  const html = render({
    ...report,
    results: [
      {
        ...report.results[0],
        reason: text,
        evidence: [{ ...report.results[0].evidence[0], quote: text }],
      },
    ],
  })
  expect(html).toContain('&lt;script&gt;模型原话😀𠮷&lt;/script&gt;')
  expect(html).not.toContain('<script>')
})

test('刷新与切换后的旧响应不能覆盖最新候选，卸载后请求同样失效', async () => {
  const guard = createExtractionRequestGuard()
  let resolveOld: () => void = () => undefined
  let resolveNew: () => void = () => undefined
  const oldResponse = new Promise<void>((resolve) => (resolveOld = resolve))
  const newResponse = new Promise<void>((resolve) => (resolveNew = resolve))
  const shown: string[] = []
  const oldCurrent = guard.begin()
  const oldLoad = oldResponse.then(() => {
    if (oldCurrent()) shown.push('旧候选')
  })
  const newCurrent = guard.begin()
  const newLoad = newResponse.then(() => {
    if (newCurrent()) shown.push('当前候选的新报告')
  })
  resolveNew()
  await newLoad
  resolveOld()
  await oldLoad
  expect(shown).toEqual(['当前候选的新报告'])
  guard.invalidate()
  expect(newCurrent()).toBe(false)
})
