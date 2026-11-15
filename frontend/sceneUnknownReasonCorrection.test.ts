import { describe, expect, it } from 'vitest'
import { textareaSelection } from './sceneReconfirmation'
import {
  canCorrectUnknownScene,
  canonical,
  correctionEvidence,
  correctionReceiptMatches,
  createCorrection,
  readCorrectionPreview,
  readCorrectionReceipt,
  readPendingCorrection,
  UNKNOWN_REASON_AUTHORITY,
  UNKNOWN_REASON_VERSION,
  type Pending,
  type Preview,
  type Receipt,
} from './sceneUnknownReasonCorrection'

function fixture() {
  const quote = '😀𠮷甲说先回铺。\r\n乙只记下日期未明。'
  const reason = '旧模型称本段存在冲突，尚未核实。'
  const preview: Preview = {
    version: UNKNOWN_REASON_VERSION,
    authority: UNKNOWN_REASON_AUTHORITY,
    scene_id: 'scene',
    scene: {
      id: 'scene',
      branch_id: 'branch',
      chapter_id: 'chapter',
      revision: 3,
      archived: 0,
      payload_sha256: 'a'.repeat(64),
      payload_hash_version: 'scene-time-public-payload/1',
      payload: {
        title: '回铺',
        source_revision: 2,
        start: 10,
        end: 10 + quote.length,
        story_order: null,
        story_time: '未知',
        audience: 'author',
        character_id: null,
        event_id: null,
        event_revision: null,
        note: '原说明',
        order_basis: {
          version: 'scene-event-refs/2',
          kind: 'unknown',
          reason,
          refs: [],
          covered_events: [
            {
              ref: 'E01',
              event_id: 'event',
              event_revision: 1,
              story_order: 5,
              record_sha256: 'e'.repeat(64),
            },
          ],
        },
      },
    },
    source: {
      chapter_id: 'chapter',
      revision: 2,
      start: 10,
      end: 10 + quote.length,
      quote,
      content_sha256: 'c'.repeat(64),
    },
    request_basis: {
      base_story_revision: 12,
      base_revision: 3,
      base_payload_sha256: 'a'.repeat(64),
      source_revision: 2,
      content_sha256: 'c'.repeat(64),
    },
  }
  const evidence = correctionEvidence(preview.source, 0, quote.length)!
  const request = createCorrection(
    preview,
    '同段未给出唯一时点；没有足够依据声称事件冲突。',
    [evidence],
    'explicit-correction-nonce',
    'author',
  )
  const pending: Pending = { preview, request }
  const receipt: Receipt = {
    version: UNKNOWN_REASON_VERSION,
    authority: UNKNOWN_REASON_AUTHORITY,
    actor: 'author',
    scene_id: 'scene',
    client_request_id: request.unknown_reason_correction.client_request_id,
    request_sha256: 'd'.repeat(64),
    reason: request.unknown_reason_correction.reason,
    previous_reason: reason,
    original_reason: reason,
    previous_scene_revision: 3,
    scene_revision: 4,
    base_story_revision: 12,
    story_revision: 13,
    source_revision: 2,
    content_sha256: 'c'.repeat(64),
    previous_payload_sha256: 'a'.repeat(64),
    previous_raw_json_sha256: 'b'.repeat(64),
    payload_sha256: 'f'.repeat(64),
    raw_json_sha256: '0'.repeat(64),
    corrected_at: 1234.5,
    evidence: [evidence],
    story_time_inferred: false,
    model_review_performed: false,
    literary_review_performed: false,
  }
  return { preview, pending, request, receipt }
}
describe('未知理由更正的冻结来源、选段与回执', () => {
  it('有效预览支持未知类型；构建只用完整原payload，独立嵌套原因，actor必须明确', () => {
    const { preview, request } = fixture()
    expect(readCorrectionPreview(preview, 'branch', 'chapter', 'scene')).toEqual(preview)
    expect(canCorrectUnknownScene(preview.scene)).toBe(true)
    expect(request.scene).toEqual(preview.scene.payload)
    expect(request.scene).not.toBe(preview.scene.payload)
    expect(request.scene.story_order).toBeNull()
    expect(request.unknown_reason_correction.actor).toBe('author')
    expect(Object.keys(request).sort()).toEqual([
      'archived',
      'base_revision',
      'base_story_revision',
      'scene',
      'unknown_reason_correction',
    ])
    expect(
      createCorrection(
        preview,
        '授权操作者按原文更正',
        [request.unknown_reason_correction.evidence[0]],
        'operator-nonce',
        'authorized_operator',
      ).unknown_reason_correction.actor,
    ).toBe('authorized_operator')
  })
  it('UTF16表情及扩展汉字不被截断，CRLF textarea位置映射回冻结原文', () => {
    const { preview } = fixture()
    expect(correctionEvidence(preview.source, 0, 1)).toBeUndefined()
    expect(correctionEvidence(preview.source, 2, 3)).toBeUndefined()
    expect(correctionEvidence(preview.source, 0, 4)?.quote).toBe('😀𠮷')
    const text = preview.source.quote.replace(/\r\n/g, '\n')
    const selection = textareaSelection(preview.source, text, 0, text.length)!
    expect(correctionEvidence(preview.source, selection.start, selection.end)?.quote).toBe(
      preview.source.quote,
    )
    expect(textareaSelection(preview.source, text + '改动', 0, 1)).toBeUndefined()
  })
  for (const damage of [
    'branch',
    'chapter',
    'id',
    'revision',
    'hash',
    'kind',
    'order',
    'basis-extra',
    'source-sha',
    'source-range',
  ] as const) {
    it('拒绝目标或冻结依据不一致：' + damage, () => {
      const { preview } = fixture()
      if (damage === 'branch') preview.scene.branch_id = 'other'
      if (damage === 'chapter') preview.scene.chapter_id = 'other'
      if (damage === 'id') preview.scene_id = 'other'
      if (damage === 'revision') preview.request_basis.base_revision++
      if (damage === 'hash') preview.request_basis.base_payload_sha256 = 'x'.repeat(64)
      if (damage === 'kind') preview.scene.payload.order_basis!.kind = 'event_refs'
      if (damage === 'order') preview.scene.payload.story_order = 5
      if (damage === 'basis-extra') Object.assign(preview.request_basis, { inferred_time: '傍晚' })
      if (damage === 'source-sha') preview.source.content_sha256 = 'f'.repeat(64)
      if (damage === 'source-range') preview.source.end++
      expect(readCorrectionPreview(preview, 'branch', 'chapter', 'scene')).toBeUndefined()
    })
  }
  it('原nonce完整恢复；场景、actor、证据或字段被混改时不猜补提交', () => {
    const { pending } = fixture()
    expect(readPendingCorrection(pending, 'branch', 'chapter', 'scene')).toEqual(pending)
    const mutations = [
      (value: Pending) => {
        value.request.scene.note = '偷换原说明'
      },
      (value: Pending) => {
        value.request.unknown_reason_correction.evidence[0].content_sha256 = 'f'.repeat(64)
      },
      (value: Pending) => {
        value.request.unknown_reason_correction.evidence[0].chapter_id = 'other'
      },
      (value: Pending) => {
        delete (
          value.request.unknown_reason_correction as Partial<typeof value.request.unknown_reason_correction>
        ).actor
      },
      (value: Pending) => {
        Object.assign(value.request, { reason: '错误顶层字段' })
      },
    ]
    for (const mutate of mutations) {
      const next = structuredClone(pending)
      mutate(next)
      expect(readPendingCorrection(next, 'branch', 'chapter', 'scene')).toBeUndefined()
    }
    expect(canonical({ z: 1, a: { b: 2 } })).toBe(canonical({ a: { b: 2 }, z: 1 }))
  })
  it('无依据、空原因、超长或非原文选段均不能提交', () => {
    const { preview, request } = fixture()
    const evidence = request.unknown_reason_correction.evidence
    expect(() => createCorrection(preview, '', evidence, 'valid-nonce', 'author')).toThrow()
    expect(() => createCorrection(preview, '原文未知', [], 'valid-nonce', 'author')).toThrow()
    expect(() => createCorrection(preview, 'a'.repeat(2001), evidence, 'valid-nonce', 'author')).toThrow()
    expect(() =>
      createCorrection(preview, '原文未知', [{ ...evidence[0], quote: '无依据' }], 'valid-nonce', 'author'),
    ).toThrow()
  })
  it('完整回执严格匹配原因、actor、原nonce和原始理由；不把更正当模型或文学审阅', () => {
    const { pending, receipt } = fixture()
    expect(readCorrectionReceipt(receipt, 'scene', receipt.client_request_id)).toEqual(receipt)
    expect(correctionReceiptMatches(receipt, pending)).toBe(true)
    for (const change of [
      { actor: 'authorized_operator' },
      { reason: '其他' },
      { original_reason: '篡改最初理由' },
      { source_revision: 4 },
    ])
      expect(correctionReceiptMatches({ ...receipt, ...change } as Receipt, pending)).toBe(false)
    for (const field of ['story_time_inferred', 'model_review_performed', 'literary_review_performed'])
      expect(
        readCorrectionReceipt({ ...receipt, [field]: true }, 'scene', receipt.client_request_id),
      ).toBeUndefined()
    expect(readCorrectionReceipt(receipt, 'other', receipt.client_request_id)).toBeUndefined()
    expect(readCorrectionReceipt(receipt, 'scene', 'other')).toBeUndefined()
    expect(
      readCorrectionReceipt({ ...receipt, actor: undefined }, 'scene', receipt.client_request_id),
    ).toBeUndefined()
  })
})
