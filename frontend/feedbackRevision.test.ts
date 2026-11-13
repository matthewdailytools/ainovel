import { expect, test } from 'vitest'
import { checkFeedbackTarget, feedbackRevisionInstruction, type FeedbackHandoff } from './feedbackRevision'

const feedback: FeedbackHandoff = {
  id: 'author-feedback',
  branch_id: 'branch-a',
  chapter_id: 'chapter-a',
  title: '渡口',
  source_revision: 1,
  category: '审美偏好',
  text: '把解释改成具体操作。',
  source_quote: '她心里很着急。',
}

test('人工反馈来源与性质保留在请求，不冒充独立模型报告或当前引文', () => {
  const instruction = feedbackRevisionInstruction(feedback, '仅改我当前选择的一句。')
  expect(instruction).toContain('尚未由独立模型审稿证实')
  expect(instruction).toContain('不将审美偏好升级成作品约束')
  expect(instruction).toContain('"id":"author-feedback"')
  expect(instruction).toContain('"source_revision":1')
  expect(instruction).toContain('可能已过期，不是本次选区')
  expect(instruction).toContain('作者本次修订要求：仅改我当前选择的一句。')
})

test('反馈跨章跨版本不能套用，旧来源必须确认精确当前修订', () => {
  const current = { branchId: 'branch-a', chapterId: 'chapter-a', revision: 1, confirmedRevision: null }
  expect(() => checkFeedbackTarget(feedback, current)).not.toThrow()
  expect(() => checkFeedbackTarget(feedback, { ...current, branchId: 'branch-b' })).toThrow('反馈对应')
  expect(() => checkFeedbackTarget(feedback, { ...current, chapterId: 'chapter-b' })).toThrow('反馈对应')
  expect(() => checkFeedbackTarget(feedback, { ...current, revision: 2 })).toThrow('核对当前保存稿')
  expect(() => checkFeedbackTarget(feedback, { ...current, revision: 2, confirmedRevision: 2 })).not.toThrow()
  expect(() => checkFeedbackTarget(feedback, { ...current, revision: 3, confirmedRevision: 2 })).toThrow(
    '核对当前保存稿',
  )
})
