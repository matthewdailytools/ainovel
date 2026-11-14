import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { expect, test } from 'vitest'
import {
  readSourceIdentityContext,
  SourceIdentityDetails,
  SourceIdentityOption,
  SOURCE_IDENTITY_VERSION,
} from './sourceIdentity'
import { readPlotAudit } from './plotAudit'
import { FrozenPlotAuditPolicy } from './PlotAuditDetails'

function context() {
  return {
    version: SOURCE_IDENTITY_VERSION,
    source_sha256: 'a'.repeat(64),
    original_entities: { E01: '修船匠', E02: '沈砚' },
    entities: { E01: '渡口一位修船匠', E02: '沈砚' },
    proposals: [
      {
        entity_id: 'E01',
        label: '渡口一位修船匠',
        status: 'supported',
        evidence: '沈砚从渡口一位修船匠借尺。',
        reason: '原话的地点限定。',
      },
      {
        entity_id: 'E02',
        label: '沈砚',
        status: 'supported',
        evidence: '沈砚从渡口一位修船匠借尺。',
        reason: '保留原名。',
      },
    ],
    checks: [
      {
        entity_id: 'E01',
        result: 'pass',
        evidence: '沈砚从渡口一位修船匠借尺。',
        reason: '限定有原话依据。',
      },
      { entity_id: 'E02', result: 'pass', evidence: '沈砚从渡口一位修船匠借尺。', reason: '原名相同。' },
    ],
    unverified_ids: [] as string[],
    policy: '不写正式人物身份，作者仍须核对。',
  }
}
function audit(identity: unknown, status = 'model_pass') {
  return {
    version: 'author-plot-audit/2',
    source: { kind: 'author_plot', text_sha256: 'a'.repeat(64) },
    candidate_sha256: 'b'.repeat(64),
    candidate_revision: 1,
    status,
    summary: '工程样本',
    policy: '仅模型辅助',
    interpretation_note: '原解释保留',
    items: [
      {
        id: 'P01',
        author_evidence: '沈砚借尺',
        actor: '沈砚',
        action: '借',
        object: '尺',
        direction: '修船匠→沈砚',
        result: '借得',
        certainty: 'explicit',
        recipient_scope: 'unspecified',
        scope_condition: '',
      },
    ],
    checks: [
      {
        item_id: 'P01',
        result: status === 'unverified' ? 'unknown' : 'pass',
        observed: '借尺',
        evidence: '原文',
        reason: '工程样本',
        repair: '',
      },
    ],
    findings: [],
    direction_guard: { source_identity_context: identity },
  }
}

test('完整限定与原名分别保留，来源哈希绑定原情节', () => {
  const value = context()
  expect(readSourceIdentityContext(value)).toEqual(value)
  expect(readPlotAudit(audit(value))?.source_identity_context).toEqual(value)
  expect(value.original_entities.E01).toBe('修船匠')
})

test('扩展汉字按后端字符上限读取，不因UTF-16双码元误拒绝', () => {
  const value = context(),
    name = '𠮷'.repeat(200)
  value.original_entities.E01 = value.entities.E01 = value.proposals[0].label = name
  value.proposals[0].evidence = value.checks[0].evidence = name
  expect(readSourceIdentityContext(value)).toBeDefined()
  value.original_entities.E01 = value.entities.E01 = value.proposals[0].label = name + '𠮷'
  value.proposals[0].evidence = value.checks[0].evidence = name + '𠮷'
  expect(readSourceIdentityContext(value)).toBeUndefined()
})
test('未知身份只显示原名并维持未知，不能借标签宣称通过', () => {
  const value = context()
  value.entities.E01 = '修船匠'
  value.checks[0].result = 'unknown'
  value.checks[0].evidence = ''
  value.unverified_ids = ['E01']
  expect(readSourceIdentityContext(value)).toEqual(value)
  expect(readPlotAudit(audit(value))).toBeUndefined()
  expect(readPlotAudit(audit(value, 'unverified'))).toBeDefined()
})
test.each([
  'duplicate',
  'missing',
  'wrong_id',
  'wrong_effective',
  'wrong_quote',
  'invalid_version',
  'unknown_extra',
  'wrong_hash',
])('拒绝损坏或错绑定的限定报告：%s', (damage) => {
  const value = context()
  if (damage === 'duplicate') value.proposals[1].entity_id = 'E01'
  if (damage === 'missing') value.checks.pop()
  if (damage === 'wrong_id') value.proposals[0].entity_id = 'E00'
  if (damage === 'wrong_effective') value.entities.E01 = '候选中猜出的老吴'
  if (damage === 'wrong_quote') value.checks[0].evidence = '其他原话'
  if (damage === 'invalid_version') value.version = 'future'
  if (damage === 'unknown_extra') value.unverified_ids = ['E99']
  if (damage === 'wrong_hash') value.source_sha256 = 'c'.repeat(64)
  if (damage !== 'wrong_hash') expect(readSourceIdentityContext(value)).toBeUndefined()
  expect(readPlotAudit(audit(value))).toBeUndefined()
})
test('不包含限定的新旧情节报告继续读取，不自动创建身份核对记录', () => {
  const plain = audit(undefined)
  expect(readPlotAudit(plain)).toBeDefined()
  expect(readPlotAudit(plain)?.source_identity_context).toBeUndefined()
})
test('限定与引文按文本转义，显示原角色、独立检查及费用选择', () => {
  const raw = context()
  raw.entities.E01 = raw.proposals[0].label = '渡口<script>修船匠'
  raw.proposals[0].evidence = raw.checks[0].evidence = '渡口<script>修船匠'
  const value = readSourceIdentityContext(raw)
  expect(value).toBeDefined()
  const html = renderToStaticMarkup(createElement(SourceIdentityDetails, { value: value! }))
  expect(html).toContain('&lt;script&gt;')
  expect(html).not.toContain('<script>')
  expect(html).toContain('修船匠')
  expect(html).toContain('独立核对')
  const option = renderToStaticMarkup(
    createElement(SourceIdentityOption, { checked: false, disabled: true, onChange: () => undefined }),
  )
  expect(option).toContain('disabled')
  expect(option).toContain('增加提取和独立核对两步调用')
})
test('冻结选项按实际版本展示，旧任务不升级', () => {
  const enabled = renderToStaticMarkup(
    createElement(FrozenPlotAuditPolicy, {
      enabled: true,
      identityEnabled: true,
      identityVersion: SOURCE_IDENTITY_VERSION,
    }),
  )
  expect(enabled).toContain('已开启原话角色限定补充')
  const old = renderToStaticMarkup(createElement(FrozenPlotAuditPolicy, { enabled: true }))
  expect(old).toContain('旧任务不自动升级')
})
