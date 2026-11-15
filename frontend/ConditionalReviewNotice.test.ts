import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { expect, test } from 'vitest'
import {
  ConditionalReviewNotice,
  hasNotTriggeredCheck,
  notTriggeredLabel,
  CONDITIONAL_REVIEW_VERSION,
} from './ConditionalReviewNotice'

function sample() {
  return {
    conditional_policy_version: CONDITIONAL_REVIEW_VERSION,
    conditional_book_conditions: ['若涉及母亲，应保持她尚不知情。'],
    checks: [
      {
        condition: '若涉及母亲，应保持她尚不知情。',
        result: 'not_triggered',
        scope: 'book',
        applicability: 'not_triggered',
        chapter_obligation: 'not_required',
        explanation: '本章交涉未涉及母亲，整书仍待核对。',
        evidence: '',
      },
    ],
  }
}

const render = (value: unknown) => renderToStaticMarkup(createElement(ConditionalReviewNotice, { value }))

test('有效新版条件以未触发独立展示，不称为通过或已经兑现', () => {
  const value = sample()
  expect(hasNotTriggeredCheck(value)).toBe(true)
  expect(notTriggeredLabel(value.checks[0], value)).toBe('本章未触发，整书待核对')
  const html = render(value)
  expect(html).toContain('本章未触发，整书待核对')
  expect(html).toContain(value.checks[0].condition)
  expect(html).toContain(value.checks[0].explanation)
  expect(html).toContain('不表示已经兑现')
  expect(html).not.toContain('通过')
  expect(html).not.toContain('<button')
})

test.each([
  undefined,
  null,
  {},
  { checks: 'not_triggered' },
  { checks: [null, 'pass', {}, { result: 'pass' }, { result: 'fail' }, { result: 'unknown' }] },
])('旧报告或无未触发项不追加新政策说明（%#）', (value) => {
  expect(hasNotTriggeredCheck(value)).toBe(false)
  expect(render(value)).toBe('')
})

test.each([
  { conditional_policy_version: undefined },
  { conditional_policy_version: 'conditional-book-check/9' },
  { conditional_book_conditions: undefined },
  { conditional_book_conditions: '若涉及母亲，应保持她尚不知情。' },
  { conditional_book_conditions: [] },
  { conditional_book_conditions: ['其他全书条件'] },
  { conditional_book_conditions: [sample().checks[0].condition, null] },
  { checks: [{ ...sample().checks[0], condition: '' }], conditional_book_conditions: [''] },
  { checks: [{ ...sample().checks[0], scope: 'chapter' }] },
  { checks: [{ ...sample().checks[0], applicability: 'uncertain' }] },
  { checks: [{ ...sample().checks[0], chapter_obligation: undefined }] },
  { checks: [{ ...sample().checks[0], chapter_obligation: 'required' }] },
  { checks: [{ ...sample().checks[0], chapter_obligation: 'uncertain' }] },
  { checks: [{ ...sample().checks[0], explanation: ' ' }] },
])('缺政策或适用依据时只显示待核对，不能冒充新版有效未触发（%#）', (change) => {
  const value = { ...sample(), ...change }
  const html = render(value)
  expect(html).toContain('未触发政策或依据无法核对，仍待核对')
  expect(html).not.toContain('本章未触发，整书待核对')
  expect(html).not.toContain('通过')
})

test('中文长约定与模型文本使用React转义，保留可重排而不执行脚本', () => {
  const value = sample()
  value.checks[0].condition = '<script>外部原话😀𠮷</script>' + '若涉及此人仍需逐项核对'.repeat(40)
  value.conditional_book_conditions = [value.checks[0].condition]
  value.checks[0].explanation = '<img src=x onerror=alert(1)>'
  const html = render(value)
  expect(html).toContain('&lt;script&gt;外部原话😀𠮷&lt;/script&gt;')
  expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;')
  expect(html).toContain('overflow-wrap:anywhere')
  expect(html).not.toContain('<script>')
  expect(html).not.toContain('<img')
})
