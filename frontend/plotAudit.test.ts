import { createHash } from 'node:crypto'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { expect, test } from 'vitest'
import { FrozenPlotAuditPolicy, PlotAuditDetails, PlotAuditOption } from './PlotAuditDetails'
import { ReferenceComparison } from './ReferenceWorkspace'
import {
  isBoundPlotAuditVersion,
  PLOT_AUDIT_VERSION,
  plotAuditEnabled,
  plotAuditFromPayload,
  plotAuditState,
  readPlotAudit,
} from './plotAudit'

const text = '沈砚把旧尺还给老陈😀𠮷。'
const hash = createHash('sha256').update(text).digest('hex')
const audit = {
  version: 'author-plot-audit/1',
  source: { kind: 'author_instruction', text_sha256: 'a'.repeat(64) },
  candidate_sha256: hash,
  candidate_revision: 0,
  status: 'model_pass',
  summary: '本次候选保留还尺方向。',
  policy: '模型辅助核对，作者决定采用。',
  interpretation_note: '解释提案不是作者新增硬条件。',
  items: [
    {
      id: 'P01',
      author_evidence: '沈砚把旧尺还给老陈。',
      actor: '沈砚',
      action: '归还',
      object: '旧尺',
      direction: '沈砚→老陈',
      result: '完成归还',
      certainty: 'explicit',
    },
  ],
  checks: [
    {
      item_id: 'P01',
      result: 'pass',
      observed: '沈砚还尺',
      evidence: text,
      reason: '给出方与取得方一致。',
      repair: '',
    },
  ],
  findings: [],
}
const auditV2 = {
  ...audit,
  version: 'author-plot-audit/2',
  items: audit.items.map((item) => ({ ...item, recipient_scope: 'only', scope_condition: '本次尚未公开前' })),
}

function parsed(value: unknown = audit) {
  const result = readPlotAudit(value)
  expect(result).toBeDefined()
  return result!
}
function render(value: unknown, historical = false) {
  return renderToStaticMarkup(
    createElement(PlotAuditDetails, {
      value,
      historical,
      candidateRevision: 0,
      candidateSha256: hash,
    }),
  )
}

test('当前报告严格解析并显示来源、逐项证据和版本，不把普通核对称为参考', () => {
  const data = parsed()
  expect(plotAuditState(data, { revision: 0, sha256: hash })).toBe('model_pass')
  const html = render(audit)
  for (const part of [
    '本次原始指令摘录',
    '沈砚→老陈',
    text,
    audit.summary,
    audit.policy,
    audit.interpretation_note,
    'author-plot-audit/1',
    hash,
    '不等于作者认可或文学质量通过',
  ])
    expect(html).toContain(part)
  expect(html).not.toContain('参考写法对照')
})

test('有内容绑定的v1保留原版本与结论，不误标正文失效或假装执行v2', () => {
  expect(PLOT_AUDIT_VERSION).toBe('author-plot-audit/2')
  expect(isBoundPlotAuditVersion('author-plot-audit/1')).toBe(true)
  expect(isBoundPlotAuditVersion('author-plot-audit/2')).toBe(true)
  expect(plotAuditState(parsed(audit), { revision: 0, sha256: hash })).toBe('model_pass')
  const html = render(audit)
  expect(html).toContain('未含限定接收者补充')
  expect(html).toContain('可按原有范围核对绑定内容，未执行v2补充核对')
  expect(html).not.toContain('历史报告已失效')
  expect(html).not.toContain('版本或内容绑定未核实')
  expect(parsed(audit).items[0].recipient_scope).toBeUndefined()
})

test('v2明确展示限定接收者范围及条件，保持真实版本', () => {
  const value = parsed(auditV2)
  expect(value.items[0]).toMatchObject({ recipient_scope: 'only', scope_condition: '本次尚未公开前' })
  expect(plotAuditState(value, { revision: 0, sha256: hash })).toBe('model_pass')
  const html = render(auditV2)
  expect(html).toContain('author-plot-audit/2')
  expect(html).toContain('接收者范围：仅限指定接收者')
  expect(html).toContain('范围条件：本次尚未公开前')
  expect(html).not.toContain('未含限定接收者补充')
})

test.each([
  { ...auditV2, items: audit.items },
  { ...auditV2, items: [{ ...auditV2.items[0], recipient_scope: undefined }] },
  { ...auditV2, items: [{ ...auditV2.items[0], recipient_scope: 'some' }] },
  { ...auditV2, items: [{ ...auditV2.items[0], recipient_scope: { toString: null } }] },
  { ...auditV2, items: [{ ...auditV2.items[0], scope_condition: undefined }] },
  { ...auditV2, items: [{ ...auditV2.items[0], scope_condition: { toString: null } }] },
])('v2缺范围字段或异常对象不能冒充有效报告（%#）', (value) => {
  expect(readPlotAudit(value)).toBeUndefined()
  expect(render(value)).toContain('报告格式无法核对')
})

function restrictedAudit() {
  return {
    ...auditV2,
    status: 'unverified',
    summary: '身份及限定条件时点仍待核对。',
    original_model_summary: '原模型摘要保留，仅供回看。',
    checks: [
      {
        ...audit.checks[0],
        result: 'unknown',
        observed: '发现同物品由沈砚给出，但补充接收者身份未确认。',
        reason: '额外接收者与限定条件时点待核对，不推断unknown身份。',
        repair: '保留待核对。',
        original_model_check: audit.checks[0],
        guard_override: {
          code: 'restricted_recipient_unverified',
          expected_supplier: '沈砚',
          expected_recipient: '老陈',
          object: '旧尺',
          recipient_scope: 'only',
          scope_condition: '本次尚未公开前',
          policy: 'unknown不是已确认身份，不新增严重结论。',
          observations: [
            {
              object_id: 'O01',
              supplier_id: 'E01',
              recipient_id: 'unknown',
              stage: 'completed',
              certainty: 'explicit',
              evidence: text,
              note: '模型备注提到陆栖，仍未完成实体映射。',
            },
          ],
        },
      },
    ],
  }
}

test('v2限定范围补充unknown优先，原pass及观察可回看但不压过最终待核对', () => {
  const audit = restrictedAudit()
  const value = parsed(audit)
  expect(value.checks[0].original_model_check?.result).toBe('pass')
  expect(value.checks[0].guard_override?.observations?.[0].recipient_id).toBe('unknown')
  expect(plotAuditState(value, { revision: 0, sha256: hash })).toBe('unverified')
  const html = render(audit)
  for (const part of [
    '待核对（含歧义或未知）',
    '额外接收者与限定条件时点待核对',
    '接收者编号：unknown',
    '不能根据备注猜测其具体人物',
    '改判前模型结论（仅历史，不代表最终核对）',
    '原模型曾标pass',
    '原模型摘要保留，仅供回看。',
  ])
    expect(html).toContain(part)
  expect(html).not.toContain('模型标一致')
  expect(html).not.toContain('接收者：陆栖')
  expect(plotAuditState(parsed({ ...audit, status: 'model_pass' }), {})).toBe('unverified')
})

test.each([
  { ...restrictedAudit(), checks: [{ ...restrictedAudit().checks[0], original_model_check: undefined }] },
  {
    ...restrictedAudit(),
    checks: [
      {
        ...restrictedAudit().checks[0],
        original_model_check: { ...audit.checks[0], result: { toString: null } },
      },
    ],
  },
  { ...restrictedAudit(), checks: [{ ...restrictedAudit().checks[0], result: 'pass' }] },
  {
    ...restrictedAudit(),
    checks: [
      {
        ...restrictedAudit().checks[0],
        guard_override: { ...restrictedAudit().checks[0].guard_override, code: { toString: null } },
      },
    ],
  },
  {
    ...restrictedAudit(),
    checks: [
      {
        ...restrictedAudit().checks[0],
        guard_override: { ...restrictedAudit().checks[0].guard_override, observations: [{ toString: null }] },
      },
    ],
  },
])('异常改判快照或观察保持非法，不抛隐式类型转换错误（%#）', (value) => {
  expect(readPlotAudit(value)).toBeUndefined()
  expect(render(value)).toContain('不能视为一致')
})

test('冻结政策显示服务端实际版本，旧缺marker为v1，关闭时不声称已执行版本', () => {
  const policy = (enabled: unknown, version?: unknown) =>
    renderToStaticMarkup(createElement(FrozenPlotAuditPolicy, { enabled, version }))
  expect(policy(true)).toContain('冻结版本：author-plot-audit/1')
  expect(policy(true)).toContain('未含限定接收者补充')
  expect(policy(true, 'author-plot-audit/2')).toContain('冻结版本：author-plot-audit/2')
  expect(policy(true, 'author-plot-audit/2')).not.toContain('未含限定接收者补充')
  expect(policy(false, 'author-plot-audit/2')).toContain('未开启')
  expect(policy(false, 'author-plot-audit/2')).not.toContain('冻结版本')
  expect(policy(true, { toString: null })).toContain('冻结版本无法确认')
})

test('全自动章纲明确是AI来源，半自动作者情节保持独立标签', () => {
  const workflow = { ...audit, source: { ...audit.source, kind: 'workflow_plan' } }
  expect(render(workflow)).toContain('AI章纲逐项核对')
  expect(render(workflow)).toContain('AI章纲摘录（不是作者原话或新增硬条件）')
  expect(render({ ...audit, source: { ...audit.source, kind: 'author_plot' } })).toContain('作者情节摘录')
})

test.each([
  { ...audit, items: 'not-array' },
  { ...audit, checks: [] },
  { ...audit, items: [audit.items[0], audit.items[0]], checks: [audit.checks[0], audit.checks[0]] },
  { ...audit, checks: [{ ...audit.checks[0], item_id: 'P99' }] },
  { ...audit, checks: [{ ...audit.checks[0], result: 'passed' }] },
  { ...audit, checks: [{ ...audit.checks[0], evidence: '' }] },
  { ...audit, source: { ...audit.source, kind: 'reference' } },
  { ...audit, candidate_revision: '0' },
  { ...audit, candidate_sha256: '' },
  { ...audit, findings: {} },
  { ...audit, status: { toString: null } },
])('异常报告不能冒充一致，原始内容仍可查看（%#）', (value) => {
  expect(readPlotAudit(value)).toBeUndefined()
  const html = render(value)
  expect(html).toContain('报告格式无法核对，不能视为一致')
  expect(html).toContain('查看原始情节核对报告')
  expect(html).not.toContain('模型标一致，仍须作者核对')
})

test('unknown和ambiguous不因总状态model_pass而显示整体一致', () => {
  const unknown = { ...audit, checks: [{ ...audit.checks[0], result: 'unknown', evidence: '' }] }
  const ambiguous = { ...audit, items: [{ ...audit.items[0], certainty: 'ambiguous' }] }
  for (const value of [unknown, ambiguous]) {
    expect(plotAuditState(parsed(value), {})).toBe('unverified')
    expect(render(value)).toContain('待核对（含歧义或未知）')
    expect(render(value)).not.toContain('模型标一致，仍须作者核对')
  }
})

test('候选编辑、修订或内容hash变化使报告失效，未完成内容核验不显示通过', () => {
  const data = parsed()
  expect(plotAuditState(data, { revision: 1 })).toBe('historical')
  expect(plotAuditState(data, { sha256: 'b'.repeat(64) })).toBe('historical')
  expect(plotAuditState(data, { edited: true })).toBe('historical')
  expect(plotAuditState(data, { hashPending: true })).toBe('pending')
  expect(render(audit, true)).toContain('历史报告已失效')
})

test('独立保存稿审查核对章节身份、保存修订和正文hash，不能按空候选正文确认', () => {
  const value = {
    ...audit,
    target: { kind: 'saved_chapter', chapter_id: 'chapter-1', revision: 2, content_sha256: hash },
  }
  const report = parsed(value)
  expect(report.target).toEqual(value.target)
  const target = {
    revision: 0,
    sha256: hash,
    expectSavedChapter: true,
    baseRevision: 2,
    savedChapter: { id: 'chapter-1', revision: 2, sha256: hash },
  }
  expect(plotAuditState(report, target)).toBe('model_pass')
  for (const changed of [
    { savedChapter: undefined },
    { baseRevision: 1 },
    { savedChapter: { ...target.savedChapter, id: 'chapter-other' } },
    { savedChapter: { ...target.savedChapter, revision: 3 } },
    { savedChapter: { ...target.savedChapter, sha256: 'b'.repeat(64) } },
  ])
    expect(plotAuditState(report, { ...target, ...changed })).toBe('historical')
  expect(plotAuditState(parsed(), target)).toBe('historical')
  const html = renderToStaticMarkup(
    createElement(PlotAuditDetails, {
      value,
      candidateRevision: 0,
      candidateSha256: hash,
      savedChapter: { id: 'chapter-1', revision: 2, content: text },
      expectSavedChapter: true,
      baseRevision: 2,
    }),
  )
  expect(html).toContain('检查对象：保存稿 chapter-1 · 修订2')
  expect(html).toContain('模型标一致，仍须作者核对')
})

test('旧参考报告与未知版本仅作历史，缺报告不产生通过提示', () => {
  const { version, source, candidate_sha256, candidate_revision, ...legacy } = audit
  expect(plotAuditState(parsed(legacy), {})).toBe('legacy')
  expect(plotAuditState(parsed({ ...audit, version: 'author-plot-audit/9' }), {})).toBe('legacy')
  expect(render(legacy)).toContain('历史报告：版本或内容绑定未核实')
  expect(plotAuditState(parsed({ ...legacy, version: 'old-unknown' }), {})).toBe('legacy')
  expect(plotAuditState(parsed({ ...legacy, version: 'author-plot-audit/1' }), {})).toBe('legacy')
  expect(render(undefined)).toBe('')
  expect(render(null)).toBe('')
})

test('不符、findings及附加流向原始记录不丢失，模型文本按文本转义', () => {
  const untrusted = '<script>原话😀𠮷</script>'
  const value = {
    ...audit,
    status: 'failed',
    checks: [{ ...audit.checks[0], result: 'fail', reason: untrusted, repair: '改回原方向。' }],
    findings: [
      { severity: 'blocker', chapter: 1, quote: text, reason: untrusted, repair: '保留原稿并核对。' },
    ],
    direction_guard: { observations: ['附加独立流向记录'] },
  }
  expect(plotAuditState(parsed(value), {})).toBe('failed')
  const html = render(value)
  for (const part of [
    '发现不符',
    '严重问题',
    '改回原方向。',
    '保留原稿并核对。',
    '附加独立流向记录',
    '&lt;script&gt;原话😀𠮷&lt;/script&gt;',
  ])
    expect(html).toContain(part)
  expect(html).not.toContain('<script>')
})

test('顶层或review内报告及编辑前报告均可定位，旧报告带历史标记', () => {
  expect(plotAuditFromPayload({ plot_audit: audit })).toEqual({ value: audit, historical: false })
  expect(
    plotAuditFromPayload({ review: { plot_audit: audit, summary_before_plot_audit: '原审查摘要' } }),
  ).toEqual({ value: audit, historical: false })
  expect(plotAuditFromPayload({ previous_review: { plot_audit: audit } })).toEqual({
    value: audit,
    historical: true,
  })
  expect(plotAuditFromPayload({ previous_plot_audit: audit })).toEqual({ value: audit, historical: true })
})

test('仅正文补写、本章审稿与workflow接受开启，新开关明确费用且默认未选', () => {
  for (const action of ['writing.expand_plot', 'review.chapter', 'workflow']) {
    expect(plotAuditEnabled(action, true)).toBe(true)
    for (const value of [false, undefined, 'true', 1]) expect(plotAuditEnabled(action, value)).toBe(false)
  }
  for (const action of ['writing.rewrite_selection', 'revision.targeted', 'writing.advise'])
    expect(plotAuditEnabled(action, true)).toBe(false)
  const html = renderToStaticMarkup(
    createElement(PlotAuditOption, { checked: false, onChange: () => undefined }),
  )
  expect(html).not.toContain('checked=""')
  expect(html).toContain('通常增加解析和对照两步模型调用')
  expect(html).toContain('流向可能再增加一次')
  expect(html).toContain('每轮复检均增加费用')
  expect(renderToStaticMarkup(createElement(FrozenPlotAuditPolicy, { enabled: undefined }))).toContain(
    '未开启',
  )
  expect(
    renderToStaticMarkup(createElement(FrozenPlotAuditPolicy, { enabled: true, mode: 'automatic' })),
  ).toContain('来源是AI章纲')
})

test('参考对照复用通用情节组件并保留原参考规则与重合说明', () => {
  const html = renderToStaticMarkup(
    createElement(ReferenceComparison, {
      value: {
        summary: '参考对照摘要',
        plot_audit: audit,
        rules: [{ rule_id: 'rule-1', result: 'pass', evidence: '参考证据', reason: '参考规则理由' }],
        concrete_plot_similarities: ['具体对应提示'],
        overlap: { coverage: '重合范围说明', matching: '重合匹配说明', hits: [] },
      },
    }),
  )
  for (const part of [
    '参考写法对照',
    '参考对照摘要',
    '参考规则理由',
    '参考证据',
    '具体对应提示',
    '重合范围说明',
    '情节逐项核对',
  ])
    expect(html).toContain(part)
})
