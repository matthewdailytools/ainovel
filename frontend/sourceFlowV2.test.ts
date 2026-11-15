import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { expect, test } from 'vitest'
import { FLOW_ENTAILMENT_VERSION, readSourceFlowReview, SourceFlowDetails } from './SourceFlowDetails'
import { readPlotAudit } from './plotAudit'

function guard() {
  const flow = (id: string, supplier: string, recipient: string) => ({
    flow_id: id,
    object_id: 'O01',
    supplier_id: supplier,
    recipient_id: recipient,
    stage: 'completed',
    certainty: 'explicit',
    evidence: '甲把旧尺交给乙，乙将自己的另一把尺借给丙。',
    note: '分别保留原观察。',
  })
  return {
    role_observation_version: 'source-role-observation/3',
    entities: { E01: '甲', E02: '乙' },
    objects: { O01: '尺' },
    source_entailment: {
      version: FLOW_ENTAILMENT_VERSION,
      input_sha256: 'a'.repeat(64),
      status: 'supported',
      proposals: [flow('F01', 'E01', 'E02'), flow('F02', 'E02', 'unknown')],
      checks: ['F01', 'F02'].map((flow_id) => ({
        flow_id,
        result: 'supported',
        verified_fields: ['object_id', 'supplier_id', 'recipient_id', 'stage', 'certainty', 'evidence'],
        evidence: '甲把旧尺交给乙，乙将自己的另一把尺借给丙。',
        reason: '只核对正文中的实际流向。',
      })),
      participants: [
        {
          flow_id: 'F02',
          side: 'recipient_id',
          status: 'known_other',
          label: '丙',
          excluded_entity_ids: ['E01', 'E02'],
          evidence: '乙将自己的另一把尺借给丙',
          reason: '正文将丙与甲、乙分别叙述。',
        },
      ] as Array<Record<string, unknown>>,
      instances: [
        {
          left_flow_id: 'F01',
          right_flow_id: 'F02',
          result: 'different',
          kind: 'physical',
          evidence: '乙将自己的另一把尺借给丙',
          reason: '第二把明确为乙自己的另一把。',
        },
      ] as Array<Record<string, unknown>>,
    },
  }
}

test('v2 parses and displays local exclusion evidence while preserving original unknown participants', () => {
  const value = guard(),
    parsed = readSourceFlowReview(value)!
  expect(parsed.proposals[1].recipient_id).toBe('unknown')
  expect(parsed.participants?.[0].excluded_entity_ids).toEqual(['E01', 'E02'])
  expect(parsed.instances?.[0].kind).toBe('physical')
  const audit = readPlotAudit({
    version: 'author-plot-audit/2',
    status: 'unverified',
    summary: '另行逐项核对。',
    policy: '保留原稿。',
    interpretation_note: '',
    items: [],
    checks: [],
    findings: [],
    direction_guard: value,
  })
  expect(audit?.source_flow_review).toEqual(parsed)
  const html = renderToStaticMarkup(createElement(SourceFlowDetails, { review: parsed }))
  for (const part of ['身份排除核对', '丙', '不同实例', '另一把', '不代表整份正文或文学质量通过'])
    expect(html).toContain(part)
})

test('v2 requires its own observation version and both explicit association arrays', () => {
  const value = guard()
  expect(
    readSourceFlowReview({ ...value, role_observation_version: 'source-role-observation/2' }),
  ).toBeUndefined()
  expect(
    readSourceFlowReview({
      ...value,
      source_entailment: { ...value.source_entailment, participants: undefined },
    }),
  ).toBeUndefined()
  expect(
    readSourceFlowReview({
      ...value,
      source_entailment: { ...value.source_entailment, instances: undefined },
    }),
  ).toBeUndefined()
  value.source_entailment.participants = []
  value.source_entailment.instances = []
  expect(readSourceFlowReview(value)).toBeDefined()
})

test.each([
  { flow_id: 'F03' },
  { side: 'supplier_id' },
  { status: 'invented' },
  { label: '' },
  { excluded_entity_ids: [] },
  { excluded_entity_ids: ['E03'] },
  { excluded_entity_ids: ['E01', 'E01'] },
  { evidence: '' },
  { reason: '' },
  { status: 'unknown', excluded_entity_ids: ['E01'] },
])('v2 rejects invalid participant exclusions %#', (change) => {
  const value = guard()
  Object.assign(value.source_entailment.participants[0], change)
  expect(readSourceFlowReview(value)).toBeUndefined()
})

test.each([
  { right_flow_id: 'F03' },
  { right_flow_id: 'F01' },
  { kind: 'unknown' },
  { evidence: '' },
  { reason: '' },
])('v2 rejects invalid instance proof %#', (change) => {
  const value = guard()
  Object.assign(value.source_entailment.instances[0], change)
  expect(readSourceFlowReview(value)).toBeUndefined()
})

test('v2 rejects duplicate unordered instance pairs and participant sides', () => {
  const value = guard()
  value.source_entailment.instances.push({
    ...value.source_entailment.instances[0],
    left_flow_id: 'F02',
    right_flow_id: 'F01',
  })
  expect(readSourceFlowReview(value)).toBeUndefined()
  const other = guard()
  other.source_entailment.participants.push({ ...other.source_entailment.participants[0] })
  expect(readSourceFlowReview(other)).toBeUndefined()
})

test('v2 cannot present association proof from an unsupported flow', () => {
  const value = guard()
  value.source_entailment.status = 'needs_review'
  value.source_entailment.checks[1].result = 'unknown'
  value.source_entailment.checks[1].verified_fields = []
  expect(readSourceFlowReview(value)).toBeUndefined()
  Object.assign(value.source_entailment.participants[0], { status: 'unknown', excluded_entity_ids: [] })
  Object.assign(value.source_entailment.instances[0], { result: 'unknown', kind: 'unknown' })
  expect(readSourceFlowReview(value)).toBeDefined()
})
