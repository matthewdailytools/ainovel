import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { expect, test } from 'vitest'
import {
  FLOW_ENTAILMENT_LEGACY_VERSION,
  FLOW_ENTAILMENT_VERSION,
  readSourceFlowReview,
  SourceFlowDetails,
} from './SourceFlowDetails'
import { FrozenPlotAuditPolicy } from './PlotAuditDetails'
import { readPlotAudit } from './plotAudit'

function guard() {
  return {
    role_observation_version: 'source-role-observation/2',
    entities: { E01: '林芷', E02: '许舟' },
    objects: { O01: '修船办法的询问' },
    source_entailment: {
      version: FLOW_ENTAILMENT_LEGACY_VERSION,
      input_sha256: 'a'.repeat(64),
      status: 'supported',
      note: '只查本次正文。',
      proposals: [
        {
          flow_id: 'F01',
          object_id: 'O01',
          supplier_id: 'E01',
          recipient_id: 'E02',
          stage: 'completed',
          certainty: 'explicit',
          evidence: '林芷问许舟如何修船，许舟没有答。',
          note: '提问已经发生。',
        },
      ],
      checks: [
        {
          flow_id: 'F01',
          result: 'supported',
          verified_fields: ['object_id', 'supplier_id', 'recipient_id', 'stage', 'certainty', 'evidence'],
          evidence: '林芷问许舟如何修船，许舟没有答。',
          reason: '发问已经发生，未把回答方向当作询问方向。',
        },
      ],
    },
  }
}
function audit(directionGuard: unknown) {
  return {
    version: 'author-plot-audit/2',
    status: 'unverified',
    summary: '仍须作者核对。',
    interpretation_note: '原解释保留。',
    policy: '不自动采纳。',
    items: [],
    checks: [],
    findings: [],
    direction_guard: directionGuard,
  }
}

test('新观察按实际内容和双方展示，拒答不等于没有提问', () => {
  const parsed = readSourceFlowReview(guard())!
  expect(parsed.checks[0].result).toBe('supported')
  expect(readPlotAudit(audit(guard()))?.source_flow_review).toEqual(parsed)
  const html = renderToStaticMarkup(createElement(SourceFlowDetails, { review: parsed }))
  for (const value of ['林芷', '许舟', '修船办法的询问', '原文支持', '原观察引用', '出处复核', '已发生'])
    expect(html).toContain(value)
  expect(html).not.toContain('正文通过')
})
test.each(['unknown', 'contradicted'])('保留%s观察及证据而不宣布正文违约', (result) => {
  const value = guard()
  value.source_entailment.status = 'needs_review'
  value.source_entailment.checks[0].result = result
  value.source_entailment.checks[0].verified_fields = result === 'unknown' ? ['object_id'] : []
  const parsed = readSourceFlowReview(value)!
  expect(parsed.checks[0].result).toBe(result)
  const html = renderToStaticMarkup(createElement(SourceFlowDetails, { review: parsed }))
  expect(html).toContain('来源未知不等于正文违约')
  expect(html).toContain(result === 'unknown' ? '仍未确认' : '原文明示反证')
})
test('空相关观察单列不适用，不伪称得到正向依据', () => {
  const value = guard()
  value.source_entailment.status = 'not_applicable'
  value.source_entailment.proposals = []
  value.source_entailment.checks = []
  expect(readSourceFlowReview(value)?.status).toBe('not_applicable')
})
test.each([
  (value: ReturnType<typeof guard>) => {
    value.role_observation_version = 'source-role-observation/9'
  },
  (value: ReturnType<typeof guard>) => {
    value.source_entailment.version = 'source-flow-entailment/9'
  },
  (value: ReturnType<typeof guard>) => {
    value.source_entailment.input_sha256 = 'bad'
  },
  (value: ReturnType<typeof guard>) => {
    value.source_entailment.checks = []
  },
  (value: ReturnType<typeof guard>) => {
    value.source_entailment.proposals.push(value.source_entailment.proposals[0])
  },
  (value: ReturnType<typeof guard>) => {
    value.source_entailment.checks[0].flow_id = 'F02'
  },
  (value: ReturnType<typeof guard>) => {
    value.source_entailment.checks[0].verified_fields = ['stage']
  },
  (value: ReturnType<typeof guard>) => {
    value.source_entailment.checks[0].verified_fields[0] = 'stage'
  },
  (value: ReturnType<typeof guard>) => {
    value.source_entailment.checks[0].verified_fields[0] = 'invented'
  },
  (value: ReturnType<typeof guard>) => {
    value.source_entailment.checks[0].evidence = '   '
  },
  (value: ReturnType<typeof guard>) => {
    value.source_entailment.checks[0].reason = ''
  },
  (value: ReturnType<typeof guard>) => {
    value.source_entailment.checks[0].result = 'contradicted'
    value.source_entailment.status = 'needs_review'
  },
  (value: ReturnType<typeof guard>) => {
    value.source_entailment.proposals[0].supplier_id = 'E03'
  },
  (value: ReturnType<typeof guard>) => {
    value.source_entailment.proposals[0].object_id = 'O02'
  },
  (value: ReturnType<typeof guard>) => {
    value.source_entailment.status = 'needs_review'
  },
])('不完整或自相矛盾的来源复核不能作为结构化有效报告 %#', (change) => {
  const value = guard()
  change(value)
  expect(readSourceFlowReview(value)).toBeUndefined()
  expect(readPlotAudit(audit(value))).toBeUndefined()
})
test('旧报告没有新政策时仍可读取，缺少新版补充时不冒充已复核', () => {
  expect(readPlotAudit(audit(undefined))).toBeDefined()
  expect(readPlotAudit(audit({ role_observation_version: 'source-role-observation/1' }))).toBeDefined()
  expect(readPlotAudit(audit({ role_observation_version: 'source-role-observation/2' }))).toBeUndefined()
})
test('原文中的脚本仅作为文字显示', () => {
  const value = guard()
  value.source_entailment.proposals[0].evidence = '<script>window.secret()</script>'
  const html = renderToStaticMarkup(
    createElement(SourceFlowDetails, { review: readSourceFlowReview(value)! }),
  )
  expect(html).not.toContain('<script>')
  expect(html).toContain('&lt;script&gt;')
})
test('冻结政策说明区分新旧任务，不宣称原任务已升级', () => {
  const old = renderToStaticMarkup(createElement(FrozenPlotAuditPolicy, { enabled: true }))
  const current = renderToStaticMarkup(
    createElement(FrozenPlotAuditPolicy, { enabled: true, flowVersion: FLOW_ENTAILMENT_VERSION }),
  )
  expect(old).toContain('未启用新版流向出处复核')
  expect(current).toContain('已冻结正文流向观察的独立出处复核')
  expect(current).toContain('额外模型费用')
})

test.each([null, {}, { toString: null }, [], 1].map((status) => ({ status })))(
  '损坏的JSON状态安全拒绝而不抛异常 %#',
  ({ status }) => {
    const value = guard()
    const malformed = { ...value, source_entailment: { ...value.source_entailment, status } }
    expect(() => readSourceFlowReview(malformed)).not.toThrow()
    expect(readSourceFlowReview(malformed)).toBeUndefined()
    expect(() => readPlotAudit(audit(malformed))).not.toThrow()
    expect(readPlotAudit(audit(malformed))).toBeUndefined()
  },
)
test.each(['', '   '])('明确观察无有效引文不能成为结构化有效来源 %#', (evidence) => {
  const value = guard()
  value.source_entailment.proposals[0].evidence = evidence
  expect(readSourceFlowReview(value)).toBeUndefined()
})
test('未知检查可无引文但不能以纯空白冒充引文', () => {
  const value = guard()
  value.source_entailment.status = 'needs_review'
  Object.assign(value.source_entailment.checks[0], {
    result: 'unknown',
    verified_fields: [],
    evidence: '   ',
  })
  expect(readSourceFlowReview(value)).toBeUndefined()
  value.source_entailment.checks[0].evidence = ''
  expect(readSourceFlowReview(value)?.checks[0].result).toBe('unknown')
})
function guardedAudit(result: string, original = true, supplement = true) {
  const source = guard()
  source.source_entailment.status = 'needs_review'
  source.source_entailment.checks[0].result = 'unknown'
  source.source_entailment.checks[0].verified_fields = ['object_id']
  const check = {
    item_id: 'P01',
    result,
    observed: '已发问',
    evidence: '她问了。',
    reason: '来源仍待核对。',
    repair: '',
    guard_override: {
      code: 'flow_source_unverified',
      expected_supplier: '林芷',
      expected_recipient: '许舟',
      object: '询问',
    },
    ...(original
      ? {
          original_model_check: {
            item_id: 'P01',
            result: 'pass',
            observed: '已发问',
            evidence: '她问了。',
            reason: '原模型依据。',
            repair: '',
          },
        }
      : {}),
  }
  return {
    ...audit(supplement ? source : undefined),
    status: result === 'pass' ? 'model_pass' : 'unverified',
    items: [
      {
        id: 'P01',
        author_evidence: '她问。',
        actor: '她',
        action: '问',
        object: '询问',
        direction: '',
        result: '',
        certainty: 'explicit',
        recipient_scope: 'unspecified',
        scope_condition: '',
      },
    ],
    checks: [check],
  }
}
test('新未知守卫要求unknown、原pass和合法出处补充，不得标成通过', () => {
  expect(readPlotAudit(guardedAudit('unknown'))?.checks[0].result).toBe('unknown')
  expect(readPlotAudit(guardedAudit('pass', false))).toBeUndefined()
  expect(readPlotAudit(guardedAudit('pass', true))).toBeUndefined()
  expect(readPlotAudit(guardedAudit('unknown', false))).toBeUndefined()
  expect(readPlotAudit(guardedAudit('unknown', true, false))).toBeUndefined()
})
test.each(['source-role-observation/9', null, {}].map((version) => ({ version })))(
  '未知观察版本不能冒充当前有效报告 %#',
  ({ version }) => {
    expect(readPlotAudit(audit({ role_observation_version: version }))).toBeUndefined()
  },
)
