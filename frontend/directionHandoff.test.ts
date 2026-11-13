import { expect, test } from 'vitest'
import type { Artifact } from './api'
import {
  adviceDirectionBasis,
  checkDirectionTarget,
  directionExpansionInstruction,
  readDirectionExpansionInstruction,
} from './directionHandoff'

const advice = {
  id: 'advice-1',
  novel_id: 'novel-1',
  branch_id: 'branch-1',
  chapter_id: 'chapter-1',
  base_revision: 2,
  candidate_revision: 3,
  kind: 'advice',
  status: 'candidate',
  payload: { text: '方向一，或方向二。' },
  basis: {},
} satisfies Artifact & { novel_id: string; branch_id: string }
const target = { novelId: 'novel-1', branchId: 'branch-1', chapterId: 'chapter-1', revision: 4 }

test('恢复方向任务保留可编辑作者文本及来源，异常记录不能降成无来源输入', () => {
  const basis = adviceDirectionBasis(advice, target)
  const selected = '作者可以写下“方向来源记录：”，这仍是自己的要求。'
  const restored = readDirectionExpansionInstruction(directionExpansionInstruction(basis, selected))!
  expect(restored.instruction).toBe(selected)
  expect(restored.basis).toEqual(basis)
  expect(() => checkDirectionTarget(restored.basis, { ...target, chapterId: 'other' })).toThrow('目标已改变')
  expect(readDirectionExpansionInstruction('普通任务的自由输入')).toBeUndefined()
  expect(() => readDirectionExpansionInstruction('作者本次确认并可编辑的情节方向：\n坏来源')).toThrow(
    '来源记录无法核对',
  )
  expect(() =>
    readDirectionExpansionInstruction(
      directionExpansionInstruction(basis, selected).replace(
        '"candidate_revision":3',
        '"candidate_revision":-1',
      ),
    ),
  ).toThrow('来源记录无法核对')
})

test('作者选定文本独立于整份建议，保留建议版本、历史来源和精确目标', () => {
  const basis = adviceDirectionBasis(advice, target)
  const instruction = directionExpansionInstruction(basis, '改成只问路，不交换地图😀𠮷。')
  expect(instruction).toContain('改成只问路，不交换地图😀𠮷。')
  expect(instruction).not.toContain(advice.payload.text)
  expect(instruction).toContain('"candidate_revision":3')
  expect(instruction).toContain('"source_revision":2')
  expect(instruction).toContain('"loaded_revision":4')
  expect(instruction).toContain('不是本书设定或故事事实')
  expect(() => checkDirectionTarget(basis, { ...target, revision: 5 })).not.toThrow()
})

test('跨作品、工作版本、章节、无目标建议、拒绝和旧候选版本均不能静默套用', () => {
  for (const changed of [{ novelId: 'other' }, { branchId: 'other' }, { chapterId: 'other' }]) {
    expect(() => adviceDirectionBasis(advice, { ...target, ...changed })).toThrow('来源章节')
    expect(() =>
      checkDirectionTarget(adviceDirectionBasis(advice, target), { ...target, ...changed }),
    ).toThrow('目标已改变')
  }
  expect(() => adviceDirectionBasis({ ...advice, chapter_id: null }, target)).toThrow('来源章节')
  expect(() => adviceDirectionBasis({ ...advice, status: 'rejected' }, target)).toThrow('尚未拒绝')
  expect(() => adviceDirectionBasis({ ...advice, kind: 'chapter_draft' }, target)).toThrow('方向建议')
  expect(() => adviceDirectionBasis(advice, target, 2)).toThrow('来源建议已更新')
  expect(() => adviceDirectionBasis({ ...advice, status: 'accepted' }, target)).not.toThrow()
})
