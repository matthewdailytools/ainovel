import type { ScopeReceipt } from './preparedScopeObjectCorrection'

export function PreparedScopeObjectEvidence({ receipt }: { receipt: ScopeReceipt }) {
  if (!('object_alias_resolution' in receipt)) return null
  const aliases = receipt.object_alias_resolution
  const relation = {
    same_transfer_same_instance: '同一次传递中的同一份材料',
    different_transfer_or_instance: '另一传递或另一份材料',
    unknown: '仍无法核实',
  }
  return (
    <section className="notice" aria-label="材料称谓关联证据">
      <h4>
        材料称谓关联 ·{' '}
        {aliases.state === 'not_needed'
          ? '本次无需额外关联'
          : aliases.state === 'supported'
            ? '原文关联已核对'
            : '仍待核对'}
      </h4>
      <p>
        只核对同一给出方、接收方和阶段下，两种材料称谓是否指向同一次传递的同一份材料。
        不同副本、另一次交付或其他称谓不能因此合并；这不会新增本书事实，也不代表正文通过。
        关联结论来自模型，仍需结合以下原文审阅。
      </p>
      {aliases.state === 'not_needed' && (
        <p>本次原文观察可直接对应，未派发额外称谓关联；全部事项与固定条件仍须独立核对。</p>
      )}
      {aliases.state === 'unverified' && (
        <p role="status">关联尚未唯一核实，保留原解释与观察，不能据此继续正文复检。</p>
      )}
      {aliases.items.map((item) => {
        const before = receipt.snapshot.prepared_contract.items.find((row) => row.id === item.item_id)
        return (
          <article className="fact" key={item.item_id}>
            <strong>
              {item.item_id} ·{' '}
              {item.status === 'supported'
                ? '已形成原文关联'
                : item.status === 'different'
                  ? '材料或传递不同'
                  : '仍未知'}
            </strong>
            <p>
              原事项材料称谓：{before?.transfer_object || '未指定'}；{before?.supplier || '未指定'} →{' '}
              {before?.recipient || '未指定'}；阶段：{before?.transfer_scope || '未知'}。
            </p>
            <p>{item.reason}</p>
            {item.checks.map((check) => {
              const observation = receipt.inventory.inventory.observations.find(
                (row) => row.observation_id === check.observation_id,
              )
              return (
                <div className="excerpt" key={check.observation_id}>
                  <p>
                    独立观察材料称谓：{observation?.transfer_object || '未指定'}；关系：
                    {relation[check.relation]}。
                  </p>
                  <p>{check.reason}</p>
                  {check.evidence_source ? (
                    <blockquote>{check.evidence_source.text}</blockquote>
                  ) : (
                    <p className="muted">未取得可用于确认关联的连续原文。</p>
                  )}
                </div>
              )
            })}
          </article>
        )
      })}
      <p>{aliases.note}</p>
    </section>
  )
}
