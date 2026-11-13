import { sceneOrderBasisState, type SceneEventWitness, type SceneOrderValue } from './sceneOrderBasis'

export function SceneOrderBasisDetails({
  scene,
  stale = false,
  historical = false,
  nameFor,
}: {
  scene: SceneOrderValue
  stale?: boolean
  historical?: boolean
  nameFor: (id: string) => string
}) {
  const { basis, state } = sceneOrderBasisState(scene, stale)
  if (state === 'legacy') return null
  const selected = new Set(basis?.refs.map((event) => event.event_id))
  const covered = basis?.covered_events.filter((event) => !selected.has(event.event_id)) ?? []
  const renderEvent = (event: SceneEventWitness) => {
    const name = nameFor(event.event_id)
    return (
      <li key={event.event_id}>
        {name && name !== event.event_id ? name : '事件名称暂不可用'} · 记录的故事顺序
        {event.story_order ?? '未知'}
      </li>
    )
  }
  return (
    <section aria-label="场景时点依据" style={{ overflowWrap: 'anywhere' }}>
      {historical && <p>以下是该历史绑定保留的时点依据，不代表当前正文仍适用。</p>}
      {state === 'stale' && <p role="alert">来源、事件或视角已变化，时点依据待复核；以下为保存的记录。</p>}
      {state === 'invalid' && (
        <p role="alert">时点依据无法核对，请重新读取或核对历史；不能视为时点已核实。</p>
      )}
      {basis?.kind === 'unknown' ? (
        <>
          <p>故事时点仍未知，不代表相关事件或人物知识已核实。</p>
          <p>保留未知的原因：{basis.reason}</p>
        </>
      ) : basis ? (
        <p>时点依据：{basis.refs.length}项选定事件。请结合本场景原文核对。</p>
      ) : null}
      {!!basis?.refs.length && (
        <details>
          <summary>查看所选事件依据（{basis.refs.length}项）</summary>
          <ul>{basis.refs.map(renderEvent)}</ul>
        </details>
      )}
      {!!covered.length && (
        <details>
          <summary>查看覆盖检查中的其他事件（{covered.length}项）</summary>
          <p>以下是生成候选时保留的覆盖记录，不表示这些事件已经采用或时点已核实。</p>
          <ul>{covered.map(renderEvent)}</ul>
        </details>
      )}
    </section>
  )
}
