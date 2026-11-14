export const SOURCE_IDENTITY_VERSION = 'source-identity-context/1'
type Proposal = {
  entity_id: string
  label: string
  status: 'supported' | 'unknown'
  evidence: string
  reason: string
}
type Check = { entity_id: string; result: 'pass' | 'fail' | 'unknown'; evidence: string; reason: string }
export type SourceIdentityContext = {
  version: string
  source_sha256: string
  original_entities: Record<string, string>
  entities: Record<string, string>
  proposals: Proposal[]
  checks: Check[]
  unverified_ids: string[]
  policy: string
}
function object(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}
function names(value: unknown): Record<string, string> | undefined {
  if (!object(value) || !Object.keys(value).length || Object.keys(value).length > 24) return
  const result: Record<string, string> = {}
  for (const [id, name] of Object.entries(value)) {
    if (
      !/^E(?:0[1-9]|1\d|2[0-4])$/u.test(id) ||
      typeof name !== 'string' ||
      !name.trim() ||
      [...name].length > 200
    )
      return
    result[id] = name
  }
  return result
}
export function readSourceIdentityContext(value: unknown): SourceIdentityContext | undefined {
  if (
    !object(value) ||
    value.version !== SOURCE_IDENTITY_VERSION ||
    typeof value.source_sha256 !== 'string' ||
    !/^[a-f0-9]{64}$/u.test(value.source_sha256) ||
    typeof value.policy !== 'string' ||
    !Array.isArray(value.proposals) ||
    !Array.isArray(value.checks) ||
    !Array.isArray(value.unverified_ids)
  )
    return
  const original = names(value.original_entities),
    effective = names(value.entities)
  if (!original || !effective) return
  const ids = Object.keys(original).sort()
  if (
    JSON.stringify(ids) !== JSON.stringify(Object.keys(effective).sort()) ||
    value.proposals.length !== ids.length ||
    value.checks.length !== ids.length
  )
    return
  const proposals: Proposal[] = [],
    checks: Check[] = []
  for (const row of value.proposals) {
    if (
      !object(row) ||
      typeof row.entity_id !== 'string' ||
      !ids.includes(row.entity_id) ||
      typeof row.label !== 'string' ||
      [...row.label].length > 200 ||
      (row.status !== 'supported' && row.status !== 'unknown') ||
      typeof row.evidence !== 'string' ||
      typeof row.reason !== 'string' ||
      !row.reason.trim()
    )
      return
    if (
      row.status === 'supported'
        ? !row.label.trim() ||
          !row.label.includes(original[row.entity_id]) ||
          !row.evidence.includes(row.label)
        : row.label !== ''
    )
      return
    proposals.push({
      entity_id: row.entity_id,
      label: row.label,
      status: row.status,
      evidence: row.evidence,
      reason: row.reason,
    })
  }
  for (const row of value.checks) {
    if (
      !object(row) ||
      typeof row.entity_id !== 'string' ||
      !ids.includes(row.entity_id) ||
      (row.result !== 'pass' && row.result !== 'fail' && row.result !== 'unknown') ||
      typeof row.evidence !== 'string' ||
      typeof row.reason !== 'string' ||
      !row.reason.trim()
    )
      return
    const proposed = proposals.find((p) => p.entity_id === row.entity_id)
    if (
      !proposed ||
      (row.result === 'pass' && (proposed.status !== 'supported' || !row.evidence.includes(proposed.label)))
    )
      return
    checks.push({ entity_id: row.entity_id, result: row.result, evidence: row.evidence, reason: row.reason })
  }
  if (
    new Set(proposals.map((p) => p.entity_id)).size !== ids.length ||
    new Set(checks.map((p) => p.entity_id)).size !== ids.length
  )
    return
  const unverified = ids.filter(
    (id) =>
      proposals.find((p) => p.entity_id === id)!.status !== 'supported' ||
      checks.find((p) => p.entity_id === id)!.result !== 'pass',
  )
  if (
    !value.unverified_ids.every((x) => typeof x === 'string') ||
    JSON.stringify([...value.unverified_ids].sort()) !== JSON.stringify(unverified)
  )
    return
  for (const id of ids)
    if (
      effective[id] !==
      (unverified.includes(id) ? original[id] : proposals.find((p) => p.entity_id === id)!.label)
    )
      return
  return {
    version: value.version,
    source_sha256: value.source_sha256,
    original_entities: original,
    entities: effective,
    proposals,
    checks,
    unverified_ids: unverified,
    policy: value.policy,
  }
}
export function SourceIdentityOption({
  checked,
  disabled,
  onChange,
}: {
  checked: boolean
  disabled?: boolean
  onChange: (value: boolean) => void
}) {
  return (
    <div>
      <label className="check">
        <input
          type="checkbox"
          checked={checked}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked)}
        />
        按原话补充角色限定
      </label>
      <p className="small muted">
        区分同称谓的不同人物，只补充情节原话已有的地点或关系。涉及物品或信息流向时增加提取和独立核对两步调用，格式或引用修正可能继续增加费用；原话不足仍保留未知。
      </p>
    </div>
  )
}
export function SourceIdentityDetails({ value }: { value: SourceIdentityContext }) {
  return (
    <section aria-label="原话角色限定核对">
      <h4>原话角色限定核对</h4>
      <p role="status">
        {value.unverified_ids.length
          ? '仍有角色限定未核实，不能据此标通过。'
          : '模型已分别提取和核对限定词，仍须作者核对身份。'}
      </p>
      <p>{value.policy}</p>
      {value.proposals.map((p) => {
        const check = value.checks.find((c) => c.entity_id === p.entity_id)!
        return (
          <article className="fact" key={p.entity_id}>
            <strong>
              {p.entity_id} · {value.original_entities[p.entity_id]} → {value.entities[p.entity_id]}
            </strong>
            <p>
              限定提案：{p.status === 'supported' ? p.label : '未知'}；独立核对：
              {{ pass: '模型标支持', fail: '模型标不符', unknown: '未核实' }[check.result]}。
            </p>
            <blockquote>{p.evidence}</blockquote>
            <p>{p.reason}</p>
            <blockquote>{check.evidence}</blockquote>
            <p>{check.reason}</p>
          </article>
        )
      })}
      <p className="small muted">
        版本：{value.version}；原话SHA-256：{value.source_sha256}。
      </p>
    </section>
  )
}
