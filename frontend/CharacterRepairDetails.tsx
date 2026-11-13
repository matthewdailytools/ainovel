import {
  characterFieldRepairEnabled,
  readCharacterRepairBasis,
  type CharacterRepairAccess,
} from './characterFieldRepair'

export function CharacterFieldRepairOption({
  checked,
  adoption,
  maintenance,
  busy,
  onChange,
}: {
  checked: boolean
  adoption: 'manual' | 'automatic'
  maintenance: boolean
  busy: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <>
      <label className="check">
        <input
          type="checkbox"
          checked={characterFieldRepairEnabled(checked, adoption, maintenance)}
          disabled={busy || adoption !== 'automatic' || !maintenance}
          aria-describedby="character-field-repair-note"
          onChange={(event) => onChange(event.target.checked)}
        />
        允许有限修订未采纳人物字段
      </label>
      <p id="character-field-repair-note" className="small">
        默认关闭，仅自动采纳且维护结构时可用。人物说明、档案来源复核未通过时，最多修订2轮并独立复核，增加调用费用；原候选与每轮差异保留。仍未通过时等待作者核对。
      </p>
    </>
  )
}

export function FrozenCharacterFieldRepairPolicy({ enabled }: { enabled: unknown }) {
  return (
    <p className="small muted" aria-label="本批次人物字段修订政策">
      人物字段有限修订：
      {enabled === true
        ? '已启用，最多2轮；增加调用费用，保留原候选与修订记录。'
        : '未启用；旧任务缺少此政策时按关闭处理，不因当前表单改变。'}
    </p>
  )
}

export function CharacterFieldRepair({
  value,
  access,
  currentId,
  busy,
  nameFor,
  onSelect,
}: {
  value: unknown
  access: CharacterRepairAccess
  currentId: string
  busy: boolean
  nameFor: (id: string) => string
  onSelect: (id: string) => void
}) {
  const basis = readCharacterRepairBasis(value)
  if (!basis && value === undefined && !access.rootId) return null
  const links = [
    { id: access.rootId || basis?.root_artifact_id, label: '查看原候选' },
    { id: access.parentId || basis?.parent_artifact_id, label: '查看上一轮候选' },
    { id: access.currentId, label: '查看当前修订候选' },
  ]
  return (
    <section aria-label="人物字段有限修订" style={{ overflowWrap: 'anywhere' }}>
      <h3>人物字段有限修订</h3>
      <p>原候选与每轮记录保留；仅修订未采纳人物的说明、档案，正文不变，仍须独立复核。</p>
      {access.reason && <p role="status">{access.reason}</p>}
      <div className="actions">
        {links
          .filter((link) => link.id)
          .map((link) => (
            <button
              type="button"
              key={link.label}
              disabled={busy || !access.rootId || link.id === currentId}
              onClick={() => {
                if (link.id) onSelect(link.id)
              }}
            >
              {link.label}
            </button>
          ))}
      </div>
      {basis ? (
        <>
          <p>
            第{basis.round}轮／最多2轮 · {basis.version}
          </p>
          <p>
            原候选：{basis.root_artifact_id}；上一轮：{basis.parent_artifact_id}；当前所看：{currentId}。
          </p>
          {basis.diff.map((diff) => (
            <article className="fact" key={`${diff.record_id}/${diff.field}`}>
              <h4>
                {nameFor(diff.record_id)} · {diff.field === 'notes' ? '说明' : '档案'}
              </h4>
              <h5>修订前候选</h5>
              <p style={{ whiteSpace: 'pre-wrap' }}>{diff.before || '（空）'}</p>
              <h5>修订后候选</h5>
              <p style={{ whiteSpace: 'pre-wrap' }}>{diff.after}</p>
              <p>修订理由：{diff.reason}</p>
              <p>
                本轮来源编号：{diff.source_ids.join('、')}
                。原句请对照下方该人物的原文来源，编号不代表新增事实。
              </p>
            </article>
          ))}
        </>
      ) : value !== undefined ? (
        <p>修订差异格式无法核对，保留原数据，不据此解锁历史候选。</p>
      ) : (
        <p>
          {access.currentId === access.rootId
            ? '尚未产生子候选，原候选及原复核报告保留。'
            : '这是本条修订链的原候选，可选择当前修订候选对照。'}
        </p>
      )}
    </section>
  )
}
