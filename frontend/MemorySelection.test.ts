import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { expect, test } from 'vitest'
import type { Artifact } from './api'
import {
  candidateMemory,
  memorySelectionBinding,
  memorySelectionRequest,
  MemorySelectionPanel,
  readMemorySelectionReceipt,
  useMemorySelection,
} from './MemorySelection'

function candidate(): Artifact {
  return {
    id: 'candidate',
    kind: 'chapter_draft',
    branch_id: 'branch',
    chapter_id: 'chapter',
    base_revision: 0,
    candidate_revision: 1,
    memory_facts_sha256: 'a'.repeat(64),
    status: 'candidate',
    basis: {},
    payload: {
      memory: {
        facts: [
          { statement: '林舟保存记录😀𠮷。', story_time: '午后（原候选）', evidence: '林舟把本子放回桌上。' },
          { statement: '<script>原样待核对</script>', story_time: '未知', evidence: '原文中的连续引句。' },
        ],
      },
    },
  }
}
const payload = (value: unknown) => ({ ...candidate(), payload: { memory: value } })

test('正文及局部patch的原记忆列表保持，报告和无记忆产物不引入选择UI', () => {
  const original = candidate(),
    before = structuredClone(original)
  expect(candidateMemory(original).facts).toEqual(
    before.payload.memory && (before.payload.memory as { facts: unknown[] }).facts,
  )
  expect(candidateMemory({ ...original, kind: 'text_patch' }).facts).toHaveLength(2)
  for (const artifact of [
    undefined,
    { ...original, kind: 'candidate_recheck' },
    { ...original, payload: {} },
    payload({}),
    payload({ facts: [] }),
  ])
    expect(candidateMemory(artifact)).toEqual({ facts: [], error: '' })
  expect(original).toEqual(before)
})

test.each([null, 'invalid', { facts: null }, { facts: 'invalid' }])(
  '损坏记忆结构不默默解释为作者全不选（%#）',
  (value) => {
    expect(candidateMemory(payload(value)).error).toContain('没有自动省略')
  },
)

test('选择范围随身份、修订、目标和实际facts变化失效，同版审稿metadata不清掉选择', () => {
  const source = candidate(),
    binding = memorySelectionBinding(source, 'target')
  expect(memorySelectionBinding(structuredClone(source), 'target')).toBe(binding)
  expect(
    memorySelectionBinding(
      { ...source, payload: { ...source.payload, review: { summary: '同版检查' } } },
      'target',
    ),
  ).toBe(binding)
  for (const change of [
    { id: 'other' },
    { candidate_revision: 2 },
    { chapter_id: 'other' },
    { branch_id: 'other' },
    { base_revision: 1 },
    { memory_facts_sha256: 'b'.repeat(64) },
    { payload: { memory: { facts: [{ statement: '新记忆' }] } } },
  ])
    expect(memorySelectionBinding({ ...source, ...change }, 'target')).not.toBe(binding)
  expect(memorySelectionBinding(source, 'other-target')).not.toBe(binding)
})

function Panel({ artifact = candidate() }: { artifact?: Artifact }) {
  return createElement(MemorySelectionPanel, { selection: useMemorySelection(artifact, 'target') })
}
test('首次展示默认全选，原时点/引文完整保留、中文和模型文本安全转义', () => {
  const html = renderToStaticMarkup(createElement(Panel))
  expect(html.match(/checked=""/g)).toHaveLength(2)
  expect(html).toContain('午后（原候选）')
  expect(html).toContain('林舟把本子放回桌上。')
  expect(html).toContain('本章审稿通过不表示这些派生记录已由作者核定')
  expect(html).toContain('&lt;script&gt;原样待核对&lt;/script&gt;')
  expect(html).not.toContain('<script>')
  expect(html).toContain('overflow-wrap:anywhere')
})
test('空记忆正文不增加选择控件，但人工采纳仍带空列表服务端hash', () => {
  const html = renderToStaticMarkup(createElement(Panel, { artifact: { ...candidate(), payload: {} } }))
  expect(html).toBe('')
  expect(memorySelectionRequest({ ...candidate(), payload: {} }, [], '')).toMatchObject({
    memory_fact_indices: [],
    memory_facts_sha256: 'a'.repeat(64),
  })
})

test.each([{ indices: [] }, { indices: [0] }])(
  '同owner新版未明确读取前input硬拒绝旧选择$indices',
  ({ indices }) => {
    expect(() => memorySelectionRequest(candidate(), indices, '原选择理由', true)).toThrow(
      '请读取新版记忆并重新选择',
    )
    const latest = { ...candidate(), memory_facts_sha256: 'b'.repeat(64) }
    expect(memorySelectionRequest(latest, [0, 1], '', false)).toMatchObject({
      memory_fact_indices: [0, 1],
      memory_facts_sha256: 'b'.repeat(64),
      memory_selection_reason: '',
    })
  },
)

test.each(['chapter_draft', 'text_patch'])('空%s列表缺来源hash也不能人工采纳', (kind) => {
  const artifact = { ...candidate(), kind, payload: {}, memory_facts_sha256: undefined }
  expect(() => memorySelectionRequest(artifact, [], '')).toThrow('记忆来源信息缺失')
})

test('非正文产物保留原采纳字段，不附带记忆范围', () => {
  expect(memorySelectionRequest({ ...candidate(), kind: 'world' }, [], '')).toEqual({ candidate_revision: 1 })
})

function receipt() {
  return {
    artifact_id: 'candidate',
    receipt: {
      artifact_id: 'candidate',
      revision: 1,
      memory_selection: {
        version: 'candidate-memory-selection/1',
        fact_count: 2,
        selected_indices: [0],
        omitted_indices: [1],
        reason: '原文没有午后，先不采用该项时点。',
        candidate_revision: 1,
      },
    },
  }
}
test('采纳回执保持原选择与理由，不从新正文反推或追认历史回执', () => {
  expect(readMemorySelectionReceipt(receipt(), 'candidate')).toMatchObject({
    state: 'recorded',
    selection: { selected_indices: [0], omitted_indices: [1] },
  })
  expect(
    readMemorySelectionReceipt(
      { artifact_id: 'candidate', receipt: { artifact_id: 'candidate', revision: 1 } },
      'candidate',
    )?.state,
  ).toBe('legacy')
  expect(readMemorySelectionReceipt({ artifact_id: 'candidate', receipt: null }, 'candidate')?.state).toBe(
    'none',
  )
})
test.each([
  { artifact_id: 'other', receipt: receipt().receipt },
  { ...receipt(), receipt: { ...receipt().receipt, artifact_id: 'other' } },
  {
    ...receipt(),
    receipt: {
      ...receipt().receipt,
      memory_selection: { ...receipt().receipt.memory_selection, version: 'future' },
    },
  },
  {
    ...receipt(),
    receipt: {
      ...receipt().receipt,
      memory_selection: {
        ...receipt().receipt.memory_selection,
        selected_indices: [1],
        omitted_indices: [1],
      },
    },
  },
  {
    ...receipt(),
    receipt: {
      ...receipt().receipt,
      memory_selection: { ...receipt().receipt.memory_selection, selected_indices: [2] },
    },
  },
  {
    ...receipt(),
    receipt: {
      ...receipt().receipt,
      memory_selection: { ...receipt().receipt.memory_selection, reason: ' ' },
    },
  },
])('错对象/版本/索引/空省略理由的回执不作为有效记录（%#）', (value) => {
  expect(readMemorySelectionReceipt(value, 'candidate')).toBeUndefined()
})
test('明确全部不采用的回执保留正文采纳与零事实决定', () => {
  const value = receipt()
  value.receipt.memory_selection.selected_indices = []
  value.receipt.memory_selection.omitted_indices = [0, 1]
  expect(readMemorySelectionReceipt(value, 'candidate')).toMatchObject({
    state: 'recorded',
    selection: { selected_indices: [], omitted_indices: [0, 1] },
  })
})
