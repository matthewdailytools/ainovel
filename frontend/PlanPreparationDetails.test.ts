import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { expect, test } from 'vitest'
import { PlanPreparationDetails, planPreparationNeedsReview } from './PlanPreparationDetails'

const render = (props: Parameters<typeof PlanPreparationDetails>[0]) =>
  renderToStaticMarkup(createElement(PlanPreparationDetails, { mode: 'automatic', enabled: true, ...props }))

test('旧批次与未知政策不伪称已启用新准备，也不显示不可信的就绪结论', () => {
  expect(render({})).toContain('不自动升级')
  const unknown = render({ version: 'future', value: { '1': { status: 'ready' } } })
  expect(unknown).toContain('无法确认')
  expect(unknown).not.toContain('已冻结执行安排')
  expect(render({ mode: 'assisted' })).toBe('')
  const records = { '1': { status: 'needs_review' }, '2': { status: 'ready' } }
  expect(planPreparationNeedsReview(undefined, records, 1)).toBe(false)
  expect(planPreparationNeedsReview('workflow-plan-preparation/1', records, 2)).toBe(false)
  expect(planPreparationNeedsReview('workflow-plan-preparation/1', records, 1)).toBe(true)
})

test('不完整或损坏的准备记录保持可见且不冒充就绪', () => {
  const html = render({
    version: 'workflow-plan-preparation/1',
    value: { '1': { status: 'ready' }, '2': null },
  })
  expect(html).toContain('记录不完整')
  expect(html).toContain('暂无法读取')
  expect(html).not.toContain('已冻结执行安排')
  const stopped = render({
    version: 'workflow-plan-preparation/1',
    value: { '1': { status: 'checking' } },
    taskStatus: 'blocked_runtime',
  })
  expect(stopped).toContain('准备未在运行')
  expect(stopped).not.toContain('正在核对原安排')
})

test('作者可核对原章纲、开放附注和最终执行安排，文本以纯文字转义', () => {
  const original = '保留三个待验原因，不确认真相。<script>alert(1)</script>'
  const html = render({
    version: 'workflow-plan-preparation/1',
    value: {
      '1': {
        status: 'ready',
        original_plot: original,
        effective_plot: original + '\n只送达登记，不领取尾款。',
        original_plot_sha256: 'a'.repeat(64),
        effective_plot_sha256: 'b'.repeat(64),
        prepared_contract: { items: [{ certainty: 'explicit' }] },
        choices: [
          { round: 1, addendum: '只送达登记，不领取尾款。', rationale: '明确开放细节，未知原因保持。' },
        ],
        preservation_checks: [{ round: 1, result: { checks: [{ result: 'pass' }] } }],
      },
    },
  })
  expect(html).toContain('原AI章纲')
  expect(html).toContain('本章执行章纲')
  expect(html).toContain('未知原因保持')
  expect(html).toContain('已冻结执行安排，正文仍须核对')
  expect(html).toContain('&lt;script&gt;')
  expect(html).not.toContain('<script>')
  expect(html).toContain('不根据已写正文倒改计划')
  expect(html).toContain('不等于小说质量通过')
})
