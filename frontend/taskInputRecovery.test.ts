import { expect, test } from 'vitest'
import type { Task } from './api'
import { checkedRecoveryReferences, prepareTaskInputRecovery, type RecoveryTarget } from './taskInputRecovery'

const content = '开篇😀𠮷。她检查刻度，再把记录放回原处。'
const target: RecoveryTarget = {
  novelId: 'novel',
  branchId: 'branch',
  chapterId: 'chapter',
  revision: 3,
  savedContent: content,
  draftContent: content,
}
const task: Task = {
  id: 'task',
  created: 1,
  novel_id: 'novel',
  branch_id: 'branch',
  kind: 'module',
  status: 'succeeded',
  cancel: 0,
  error: '',
  result: {},
  payload: {
    account: 'offline',
    action: 'writing.rewrite_selection',
    instruction: '保持检查结果，只补一个具体动作。',
    ...{
      novel_id: 'novel',
      branch_id: 'branch',
      chapter_id: 'chapter',
      base_revision: 3,
      reference_ids: ['ref'],
      selection_start: 2,
      selection_end: 6,
      selection_text: '😀𠮷',
    },
  },
}
const excerpt = {
  id: 'ref',
  novel_id: 'novel',
  unit_id: 'unit',
  book_id: 'book',
  start: 0,
  end: 4,
  quote: '水声渐低',
  note: '只借节奏',
  title: '原创练习',
  source: 'upload',
}
const withPayload = (values: Record<string, unknown>): Task => ({
  ...task,
  payload: { ...task.payload, ...values },
})

test('恢复同作品原引用及非BMP选区，输入对象和资料不变', () => {
  const before = JSON.stringify([task, target, excerpt])
  const ready = prepareTaskInputRecovery(task, target, [excerpt])
  expect(ready.references.map((row) => [row.id, row.status])).toEqual([['ref', 'available']])
  expect(checkedRecoveryReferences(ready, ['ref'])).toEqual(['ref'])
  expect(ready.selection).toEqual({ start: 2, end: 6, text: '😀𠮷' })
  expect(JSON.stringify([task, target, excerpt])).toBe(before)
})

test('全局资料可恢复；缺失资料逐项保留而不以同名资料替换', () => {
  const ready = prepareTaskInputRecovery(withPayload({ reference_ids: ['ref', 'missing'] }), target, [
    { ...excerpt, novel_id: null },
    { ...excerpt, id: 'same-title' },
  ])
  expect(ready.references.map((row) => [row.id, row.status])).toEqual([
    ['ref', 'available'],
    ['missing', 'missing'],
  ])
  expect(checkedRecoveryReferences(ready, ['ref'])).toEqual(['ref'])
  expect(() => checkedRecoveryReferences(ready, ['missing'])).toThrow('当前可用')
})

test('其他作品摘录、缺少归属及重复标识均不可恢复', () => {
  for (const rows of [
    [{ ...excerpt, novel_id: 'other' }],
    [{ ...excerpt, novel_id: undefined }],
    [excerpt, excerpt],
  ]) {
    const ready = prepareTaskInputRecovery(task, target, rows)
    expect(ready.references[0].status).toBe('unavailable')
    expect(() => checkedRecoveryReferences(ready, ['ref'])).toThrow()
  }
})

test('跨作品/分支任务及payload身份不符明确停止', () => {
  for (const changed of [{ novel_id: 'other' }, { branch_id: 'other' }]) {
    expect(() => prepareTaskInputRecovery({ ...task, ...changed }, target, [excerpt])).toThrow('不属于')
    expect(() => prepareTaskInputRecovery(withPayload(changed), target, [excerpt])).toThrow('依据不完整')
  }
})

test.each([{ chapterId: 'other' }, { revision: 4 }, { draftContent: content + '新手写' }])(
  '目标已变化%j时保留原引用但禁止旧选区',
  (change) => {
    const ready = prepareTaskInputRecovery(task, { ...target, ...change }, [excerpt])
    expect(ready.selection).toBeNull()
    expect(ready.selectionNotice).toContain('重新选择')
    expect(ready.references[0].status).toBe('available')
  },
)

test.each([
  { selection_text: '不是原句' },
  { selection_start: 3, selection_end: 4, selection_text: content.slice(3, 4) },
  { selection_start: -1 },
  { selection_end: content.length + 1 },
  { selection_start: 2.5 },
])('不可靠UTF16选区%j不能恢复', (change) => {
  expect(prepareTaskInputRecovery(withPayload(change), target, [excerpt]).selection).toBeNull()
})

test('没有原选区/原引用的旧任务仍明确告知清空和重选', () => {
  const ready = prepareTaskInputRecovery(
    withPayload({ reference_ids: [], selection_start: null, selection_end: null, selection_text: null }),
    target,
    [excerpt],
  )
  expect(ready.references).toEqual([])
  expect(ready.selection).toBeNull()
  expect(ready.selectionNotice).toContain('原任务没有选区')
})

test('资料核对期间内容或归属变化、任务设置变化使确认依据失效', () => {
  const before = prepareTaskInputRecovery(task, target, [excerpt])
  for (const change of [
    { quote: '潮声渐低' },
    { note: '新笔记' },
    { source: 'new source' },
    { novel_id: null },
  ]) {
    expect(prepareTaskInputRecovery(task, target, [{ ...excerpt, ...change }]).basis).not.toBe(before.basis)
  }
  expect(
    prepareTaskInputRecovery(withPayload({ instruction: '改变要求' }), target, [excerpt]).basis,
  ).not.toBe(before.basis)
})

test('非法清单/设置不降成空资料；未确认或重复ID不能注入额外引用', () => {
  for (const change of [
    { reference_ids: ['ref', 'ref'] },
    { reference_ids: [2] },
    { reference_ids: 'ref' },
    { fixed: '锁定' },
    { writing_profile: false },
  ]) {
    expect(() => prepareTaskInputRecovery(withPayload(change), target, [excerpt])).toThrow()
  }
  const ready = prepareTaskInputRecovery(task, target, [excerpt])
  expect(() => checkedRecoveryReferences(ready, ['ref', 'ref'])).toThrow()
  expect(() => checkedRecoveryReferences(ready, ['new-ref'])).toThrow()
})
