import { describe, expect, it } from 'vitest'
import {
  canReconfirmScene,
  createReconfirmation,
  readPreview,
  readReceipt,
  receiptMatchesRequest,
  selectedEvidence,
  textareaSelection,
} from './sceneReconfirmation'

const sha = 'a'.repeat(64)
const witness = {
  ref: 'E0',
  event_id: 'event0',
  event_revision: 1,
  story_order: 7,
  record_sha256: sha,
}
const source = {
  chapter_id: 'chapter',
  revision: 1,
  start: 10,
  end: 21,
  quote: '😀𠮷回铺时天未黑。',
}
function rawPreview() {
  return {
    version: 'scene-author-reconfirmation/1',
    authority: 'author_confirmation_not_model_review',
    scene_id: 'scene',
    payload_hash_version: 'scene-time-public-payload/1',
    scene: {
      id: 'scene',
      branch_id: 'branch',
      chapter_id: 'chapter',
      revision: 0,
      archived: 0,
      payload_sha256: sha,
      payload_hash_version: 'scene-time-public-payload/1',
      payload: {
        title: '回铺',
        source_revision: 1,
        start: source.start,
        end: source.end,
        story_order: 7,
        story_time: '回铺后至傍晚',
        audience: 'character',
        character_id: 'character',
        order_basis: {
          version: 'scene-event-refs/2',
          kind: 'event_refs',
          reason: '',
          refs: [witness],
          covered_events: [witness],
        },
      },
    },
    source: { ...source },
    request_basis: {
      base_story_revision: 3,
      base_revision: 0,
      base_payload_sha256: sha,
      source_revision: 1,
      content_sha256: sha,
      refs: [{ ...witness, event_revision: 2 }],
      story_time: '回铺后至傍晚',
      time_evidence: [],
    },
  }
}
function preview() {
  const result = readPreview(rawPreview(), 'branch', 'chapter', 'scene')
  if (!result) throw new Error('fixture preview invalid')
  return result
}
describe('提取场景人工重核边界', () => {
  it('textarea CRLF与单独CR归一后仍映射回服务器UTF16原文，保留表情与扩展汉字边界', () => {
    const quote = '😀\r\n𠮷回铺\r天未黑。'
    const original = { chapter_id: 'c', revision: 2, start: 30, end: 30 + quote.length, quote }
    const displayed = quote.replace(/\r\n?/g, '\n')
    const from = displayed.indexOf('𠮷'),
      to = displayed.indexOf('黑') + 1
    const selected = textareaSelection(original, displayed, from, to)!
    expect(selectedEvidence(original, selected.start, selected.end)).toEqual({
      chapter_id: 'c',
      revision: 2,
      start: 34,
      end: 30 + quote.indexOf('黑') + 1,
      quote: '𠮷回铺\r天未黑',
    })
    expect(textareaSelection(original, '篡改显示值', from, to)).toBeUndefined()
    expect(textareaSelection(original, displayed, 0, displayed.length)).toEqual({
      start: 0,
      end: quote.length,
    })
    const half = textareaSelection(original, displayed, from + 1, to)!
    expect(selectedEvidence(original, half.start, half.end)).toBeUndefined()
  })
  it('默认只刷新服务器当前引用并保留原时间，不增加范围或视角字段', () => {
    const value = preview()
    expect(canReconfirmScene(value.scene)).toBe(true)
    const request = createReconfirmation(
      value,
      '已重新阅读',
      value.request_basis.story_time,
      [],
      'same-request-1',
    )
    expect(request).toEqual({
      ...rawPreview().request_basis,
      reason: '已重新阅读',
      client_request_id: 'same-request-1',
    })
    expect(request).not.toHaveProperty('order_basis')
    expect(request).not.toHaveProperty('author_reconfirmed')
  })
  it.each(['branch', 'chapter', 'scene'])('拒绝错误%s目标', (kind) => {
    expect(
      readPreview(
        rawPreview(),
        kind === 'branch' ? 'wrong' : 'branch',
        kind === 'chapter' ? 'wrong' : 'chapter',
        kind === 'scene' ? 'wrong' : 'scene',
      ),
    ).toBeUndefined()
  })
  it.each(['hash', 'source', 'reorder', 'originalTime', 'archived', 'unknown'])(
    '拒绝错误依据：%s',
    (kind) => {
      const value = rawPreview()
      if (kind === 'hash') value.request_basis.base_payload_sha256 = 'b'.repeat(64)
      if (kind === 'source') value.source.revision = 2
      if (kind === 'reorder') value.request_basis.refs[0].ref = 'E1'
      if (kind === 'originalTime') value.request_basis.story_time = '被悄悄替换'
      if (kind === 'archived') value.scene.archived = 1
      if (kind === 'unknown') value.scene.payload.order_basis.kind = 'unknown'
      expect(readPreview(value, 'branch', 'chapter', 'scene')).toBeUndefined()
    },
  )
  it('修改时间必须使用原范围内连续UTF16原文，拒绝半个表情或扩展汉字', () => {
    const value = preview()
    expect(() => createReconfirmation(value, '核对', '天未黑', [], 'a-request-1')).toThrow('连续原文')
    expect(selectedEvidence(source, 1, 6)).toBeUndefined()
    expect(selectedEvidence(source, 2, 3)).toBeUndefined()
    const quote = selectedEvidence(source, 4, source.quote.length)!
    expect(quote.start).toBe(14)
    expect(quote.quote).toBe('回铺时天未黑。')
    expect(createReconfirmation(value, '核对', '天未黑', [quote], 'a-request-1').time_evidence).toEqual([
      quote,
    ])
    expect(() =>
      createReconfirmation(value, '核对', '天未黑', [{ ...quote, chapter_id: 'wrong' }], 'a-request-1'),
    ).toThrow('不对应')
  })
  it('已归档、未知、无hash能力不开放重核入口', () => {
    const row = preview().scene
    expect(canReconfirmScene({ ...row, archived: 1 })).toBe(false)
    expect(canReconfirmScene({ ...row, payload_sha256: undefined })).toBe(false)
    expect(
      canReconfirmScene({
        ...row,
        payload: { ...row.payload, story_order: null },
      }),
    ).toBe(false)
  })
  it('回执必须确属同场景同请求并保留原事件身份', () => {
    const receipt = {
      version: 'scene-author-reconfirmation/1',
      authority: 'author_confirmation_not_model_review',
      scene_id: 'scene',
      client_request_id: 'request-1',
      old_revision: 0,
      revision: 1,
      story_revision: 4,
      base_story_revision: 3,
      source_revision: 1,
      reason: '人工核对',
      previous_story_time: '原时间',
      story_time: '原时间',
      previous_refs: [witness],
      current_refs: [{ ...witness, event_revision: 2 }],
      time_evidence: [],
      covered_events_unchanged: true,
      payload_hash_version: 'scene-time-public-payload/1',
      payload_sha256: sha,
      old_payload_sha256: sha,
      request_sha256: sha,
      content_sha256: sha,
      old_raw_json_sha256: sha,
      raw_json_sha256: sha,
    }
    expect(readReceipt(receipt, 'scene', 'request-1')).toBeDefined()
    const parsed = readReceipt(receipt, 'scene', 'request-1')!
    const request = {
      ...preview().request_basis,
      client_request_id: 'request-1',
      reason: '人工核对',
      story_time: '原时间',
    }
    expect(receiptMatchesRequest(parsed, request)).toBe(true)
    expect(
      receiptMatchesRequest(
        { ...parsed, current_refs: [{ ...parsed.current_refs[0], record_sha256: 'b'.repeat(64) }] },
        request,
      ),
    ).toBe(false)
    expect(
      receiptMatchesRequest(
        { ...parsed, current_refs: [{ ...parsed.current_refs[0], event_revision: 3 }] },
        request,
      ),
    ).toBe(false)
    expect(receiptMatchesRequest({ ...parsed, time_evidence: [source] }, request)).toBe(false)
    expect(receiptMatchesRequest({ ...parsed, base_story_revision: 4 }, request)).toBe(false)
    expect(readReceipt({ ...receipt, base_story_revision: undefined }, 'scene', 'request-1')).toBeUndefined()
    expect(readReceipt({ ...receipt, story_revision: 6 }, 'scene', 'request-1')).toBeUndefined()
    expect(readReceipt(receipt, 'wrong', 'request-1')).toBeUndefined()
    expect(readReceipt({ ...receipt, previous_refs: [null] }, 'scene', 'request-1')).toBeUndefined()
    expect(
      readReceipt({ ...receipt, current_refs: [{ ...witness, event_id: 'another' }] }, 'scene', 'request-1'),
    ).toBeUndefined()
  })
})
