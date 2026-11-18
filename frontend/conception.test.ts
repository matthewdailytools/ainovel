import { describe, expect, it } from 'vitest'
import {
  candidateIssues,
  candidateModelReview,
  conceptionSourceEvidence,
  removeConceptionLinks,
} from './conception'

describe('构思工作台接受的候选与关联', () => {
  it('逐字展示以 UTF-16 位置保存的正文来源', () => {
    const source = { chapter_id: 'chapter1', revision: 2, start: 3, end: 6, quote: '😀井' }
    expect(conceptionSourceEvidence({ item1: source })).toEqual({ item1: source })
    expect(conceptionSourceEvidence({})).toEqual({})
    expect(() => conceptionSourceEvidence({ item1: { ...source, end: 5 } })).toThrow(/正文依据格式/)
    expect(() => conceptionSourceEvidence({ item1: { ...source, revision: -1 } })).toThrow(/正文依据格式/)
  })

  it('允许无提示的单步草案，拒绝损坏的一键草案提示', () => {
    expect(candidateIssues(undefined)).toEqual([])
    expect(candidateIssues([{ category_id: 'core', message: '有待核对的条件' }])).toHaveLength(1)
    expect(() => candidateIssues([{ category_id: 'core', message: null }])).toThrow(/核对提示格式/)
  })

  it('区分尚无模型复核、完成复核未提问题及含有复核建议的草案', () => {
    expect(candidateModelReview(undefined)).toBeUndefined()
    const review = { version: 'v1', issues: [] }
    expect(candidateModelReview(review)).toEqual(review)
    expect(
      candidateModelReview({ ...review, issues: [{ category_id: 'events', message: '核对前因' }] })?.issues,
    ).toHaveLength(1)
    expect(() => candidateModelReview({ version: 'v1' })).toThrow(/AI 一致性复核格式/)
  })

  it('项目或类别移除时清除关联，保留其他项目和原有版本', () => {
    const categories = [
      {
        id: 'events',
        title: '事件',
        skipped: false,
        items: [{ id: 'event', title: '行动', detail: '核查旧物', links: ['previous', 'person'] }],
      },
    ]
    const edited = removeConceptionLinks(categories, ['previous'])
    expect(edited[0].items[0]).toEqual({ ...categories[0].items[0], links: ['person'] })
    expect(categories[0].items[0].links).toEqual(['previous', 'person'])
  })
})
