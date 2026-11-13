import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { expect, test } from 'vitest'
import { SceneOrderBasisDetails } from './SceneOrderBasisDetails'
import {
  hasSceneOrderBasis,
  readSceneOrderBasis,
  sceneOrderBasisState,
  sceneSaveMessage,
  type SceneOrderBasis,
} from './sceneOrderBasis'

const witness = (id: string, order: number | null = 10) => ({
  ref: `source-${id}`,
  event_id: id,
  event_revision: 2,
  story_order: order,
  record_sha256: 'a'.repeat(64),
})
const basis: SceneOrderBasis = {
  version: 'scene-event-refs/2',
  kind: 'event_refs',
  reason: '',
  refs: [witness('first'), witness('second')],
  covered_events: [witness('first'), witness('second')],
}
const names: Record<string, string> = { first: '取出量尺', second: '记录潮位' }
const nameFor = (id: string) => names[id] || id

test('多事件时点完整读取，展示名称及各自故事顺序，不曝光内部校验字段', () => {
  const scene = { story_order: 10, order_basis: structuredClone(basis) }
  const before = JSON.stringify(scene)
  expect(readSceneOrderBasis(scene.order_basis)).toEqual(basis)
  expect(sceneOrderBasisState(scene).state).toBe('event_refs')
  const html = renderToStaticMarkup(createElement(SceneOrderBasisDetails, { scene, nameFor }))
  expect(html).toContain('查看所选事件依据（2项）')
  expect(html).toContain('取出量尺')
  expect(html).toContain('记录潮位')
  expect(html).not.toContain('source-first')
  expect(html).not.toContain('record_sha256')
  expect(html).not.toContain('a'.repeat(64))
  expect(html).not.toContain('查看覆盖检查中的其他事件')
  expect(JSON.stringify(scene)).toBe(before)
})

test('未知时点保留原因及覆盖审计，未采用的覆盖记录不会显示成已采纳事实', () => {
  const scene = {
    story_order: null,
    order_basis: { ...basis, kind: 'unknown', reason: '同段含先后两个事件，尚无唯一时点。', refs: [] },
  }
  expect(sceneOrderBasisState(scene).state).toBe('unknown')
  const html = renderToStaticMarkup(createElement(SceneOrderBasisDetails, { scene, nameFor }))
  expect(html).toContain('保留未知的原因：同段含先后两个事件，尚无唯一时点。')
  expect(html).toContain('故事时点仍未知')
  expect(html).toContain('以下是生成候选时保留的覆盖记录，不表示这些事件已经采用或时点已核实。')
  expect(html).toContain('查看覆盖检查中的其他事件（2项）')
  expect(html).not.toContain('<p>时点已核实')
})

test('来源过期与历史依据均保留可读，不能当成当前有效结论', () => {
  const scene = { story_order: 10, order_basis: basis }
  expect(sceneOrderBasisState(scene, true).state).toBe('stale')
  const html = renderToStaticMarkup(
    createElement(SceneOrderBasisDetails, { scene, stale: true, historical: true, nameFor }),
  )
  expect(html).toContain('role="alert"')
  expect(html).toContain('来源、事件或视角已变化')
  expect(html).toContain('不代表当前正文仍适用')
  expect(html).toContain('记录潮位')
})

test.each([
  { ...basis, version: 'future-version' },
  { ...basis, refs: [witness('first'), witness('first')] },
  { ...basis, refs: [{ ...witness('first'), event_revision: -1 }] },
  { ...basis, kind: 'unknown', reason: '   ' },
  { ...basis, covered_events: undefined },
])('损坏或不支持的依据不能静默当作手工无依据记录：%j', (value) => {
  expect(readSceneOrderBasis(value)).toBeUndefined()
  expect(hasSceneOrderBasis({ order_basis: value })).toBe(true)
  expect(sceneOrderBasisState({ story_order: 10, order_basis: value }).state).toBe('invalid')
})

test('旧单事件和手工时点保持兼容，新依据与时点冲突时仅显示待核对', () => {
  expect(hasSceneOrderBasis({})).toBe(false)
  expect(hasSceneOrderBasis({ order_basis: null })).toBe(false)
  expect(sceneOrderBasisState({ story_order: null }).state).toBe('legacy')
  expect(sceneOrderBasisState({ story_order: 11, order_basis: basis }).state).toBe('invalid')
  expect(
    renderToStaticMarkup(createElement(SceneOrderBasisDetails, { scene: { story_order: null }, nameFor })),
  ).toBe('')
})

test('理由和事件名按文本转义，名称未载入不以内部编号填充产品界面', () => {
  const scene = {
    story_order: null,
    order_basis: { ...basis, kind: 'unknown', reason: '<script>alert(1)</script>😀𠮷', refs: [] },
  }
  const html = renderToStaticMarkup(
    createElement(SceneOrderBasisDetails, { scene, nameFor: (id: string) => id }),
  )
  expect(html).toContain('&lt;script&gt;alert(1)&lt;/script&gt;😀𠮷')
  expect(html).not.toContain('<script>')
  expect(html).toContain('事件名称暂不可用')
  expect(html).not.toContain('source-first')
})

test('保存未知范围不宣称故事时点已核实，已知手工时点与归档提示保留', () => {
  expect(sceneSaveMessage(null)).toContain('故事时点仍未知')
  expect(sceneSaveMessage(null)).not.toContain('时点已确认')
  expect(sceneSaveMessage(10)).toBe('场景时点已确认，不修改正文。正文后续变化时须重新复核范围。')
  expect(sceneSaveMessage(null, true)).toBe('已归档保存的绑定，历史保留。')
})
