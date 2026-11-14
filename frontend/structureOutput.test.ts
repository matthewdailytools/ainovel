import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { expect, test } from 'vitest'
import { FrozenStructureOutputPolicy, StructureOutputSettings } from './StructureOutputSettings'
import {
  DEFAULT_STRUCTURE_OUTPUT_TOKENS,
  frozenStructureOutput,
  structureOutputChoice,
  workflowStructureOutput,
} from './structureOutput'

test('新表单默认8192，只有明确选择才用16384，关闭结构维护时不沿用高上限', () => {
  expect(DEFAULT_STRUCTURE_OUTPUT_TOKENS).toBe(8192)
  expect(workflowStructureOutput(structureOutputChoice('8192'), true)).toBe(8192)
  expect(workflowStructureOutput(structureOutputChoice('16384'), true)).toBe(16384)
  expect(workflowStructureOutput(structureOutputChoice('16384'), false)).toBe(8192)
})

test.each(['', '8192.5', '8192 ', '32768', 'NaN'])('选择入口拒绝意外值 %s', (value) => {
  expect(() => structureOutputChoice(value)).toThrow('请选择 8192 或 16384')
})

test('旧任务缺少上限时沿用8192；冻结字段支持CLI合法整数，不按当前UI选项改写', () => {
  expect(frozenStructureOutput(undefined)).toEqual({ tokens: 8192, legacy: true })
  for (const tokens of [64, 4096, 8192, 16384])
    expect(frozenStructureOutput(tokens)).toEqual({ tokens, legacy: false })
})

test.each([null, '16384', true, 63, 8192.5, 16385, Number.NaN])(
  '异常冻结字段明确待核对，不静默变为8192 %s',
  (value) => {
    expect(frozenStructureOutput(value)).toBeUndefined()
    const html = renderToStaticMarkup(
      createElement(FrozenStructureOutputPolicy, { value, maintenance: true }),
    )
    expect(html).toContain('暂无法核对')
    expect(html).not.toContain('首轮结构输出上限：8192')
  },
)

test('低频设置有原生标签、说明和两种上限，忙时不可重复调整', () => {
  const html = renderToStaticMarkup(
    createElement(StructureOutputSettings, {
      value: 16384,
      disabled: true,
      onChange: () => undefined,
    }),
  )
  expect(html).toContain('<details>')
  expect(html).toContain('结构整理的输出设置')
  expect(html).toContain('首轮结构输出上限（tokens）')
  expect(html).toContain('<select disabled="" aria-describedby=')
  expect(html).toContain('<option value="16384" selected="">16384</option>')
  expect(html).toContain('8192（默认）')
  expect(html).toContain('仅控制首轮结构整理')
  expect(html).toContain('有限修正最高可使用16384')
  expect(html).toContain('同一任务、共享预算保护')
  expect(html).toContain('改变这里不会升级旧任务')
})

test('冻结policy从任务值读取，历史缺省与当前显式16k区分，关闭维护不显示未使用的额度', () => {
  const legacy = renderToStaticMarkup(
    createElement(FrozenStructureOutputPolicy, { value: undefined, maintenance: true }),
  )
  expect(legacy).toContain('首轮结构输出上限：8192 tokens')
  expect(legacy).toContain('历史任务未单独设置')
  const explicit = renderToStaticMarkup(
    createElement(FrozenStructureOutputPolicy, { value: 16384, maintenance: true }),
  )
  expect(explicit).toContain('首轮结构输出上限：16384 tokens')
  expect(explicit).not.toContain('历史任务未单独设置')
  const disabled = renderToStaticMarkup(
    createElement(FrozenStructureOutputPolicy, { value: 16384, maintenance: false }),
  )
  expect(disabled).toContain('结构维护未启用')
  expect(disabled).not.toContain('16384')
})
