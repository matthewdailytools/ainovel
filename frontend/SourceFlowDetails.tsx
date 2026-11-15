export const FLOW_ENTAILMENT_LEGACY_VERSION = 'source-flow-entailment/1'
export const FLOW_ENTAILMENT_VERSION = 'source-flow-entailment/2'
export const isSourceFlowVersion = (value: unknown) =>
  value === FLOW_ENTAILMENT_LEGACY_VERSION || value === FLOW_ENTAILMENT_VERSION
const fields = ['object_id', 'supplier_id', 'recipient_id', 'stage', 'certainty', 'evidence'] as const
type VerifiedField = (typeof fields)[number]
const stages = ['offered', 'requested', 'completed', 'planned', 'refused', 'unknown'] as const
type Flow = {
  flow_id: string
  object_id: string
  supplier_id: string
  recipient_id: string
  stage: (typeof stages)[number]
  certainty: 'explicit' | 'ambiguous'
  evidence: string
  note: string
}
type FlowCheck = {
  flow_id: string
  result: 'supported' | 'unknown' | 'contradicted'
  verified_fields: VerifiedField[]
  evidence: string
  reason: string
}
type FlowParticipant = {
  flow_id: string
  side: 'supplier_id' | 'recipient_id'
  status: 'known_other' | 'unknown'
  label: string
  excluded_entity_ids: string[]
  evidence: string
  reason: string
}
type FlowInstance = {
  left_flow_id: string
  right_flow_id: string
  result: 'same' | 'different' | 'unknown'
  kind: 'physical' | 'content' | 'unknown'
  evidence: string
  reason: string
}
export type SourceFlowReview = {
  version: string
  input_sha256: string
  status: 'not_applicable' | 'supported' | 'needs_review'
  entities: Record<string, string>
  objects: Record<string, string>
  proposals: Flow[]
  checks: FlowCheck[]
  participants?: FlowParticipant[]
  instances?: FlowInstance[]
  note: string
}

const boundedText = (value: unknown, maximum: number, required = false): value is string =>
  typeof value === 'string' &&
  value.length <= maximum &&
  (required ? !!value.trim() : value === '' || !!value.trim())

function readAssociations(
  value: Record<string, unknown>,
  flows: Flow[],
  checks: FlowCheck[],
  entities: Record<string, string>,
): { participants: FlowParticipant[]; instances: FlowInstance[] } | undefined {
  if (
    !Array.isArray(value.participants) ||
    value.participants.length > 24 ||
    !Array.isArray(value.instances) ||
    value.instances.length > 24
  )
    return
  const participants: FlowParticipant[] = [],
    instances: FlowInstance[] = [],
    sides = new Set<string>(),
    pairs = new Set<string>()
  const supported = (id: string) => checks.some((row) => row.flow_id === id && row.result === 'supported')
  for (const row of value.participants) {
    if (
      !object(row) ||
      typeof row.flow_id !== 'string' ||
      (row.side !== 'supplier_id' && row.side !== 'recipient_id') ||
      (row.status !== 'known_other' && row.status !== 'unknown') ||
      typeof row.label !== 'string' ||
      row.label.length > 200 ||
      (row.status === 'known_other' && !row.label.trim()) ||
      !Array.isArray(row.excluded_entity_ids) ||
      row.excluded_entity_ids.length > 24 ||
      row.excluded_entity_ids.some((id) => typeof id !== 'string' || !Object.hasOwn(entities, id)) ||
      new Set(row.excluded_entity_ids).size !== row.excluded_entity_ids.length ||
      !boundedText(row.evidence, 800, row.status === 'known_other') ||
      !boundedText(row.reason, 1200, true) ||
      Object.keys(row).sort().join(',') !== 'evidence,excluded_entity_ids,flow_id,label,reason,side,status'
    )
      return
    const flow = flows.find((flow) => flow.flow_id === row.flow_id),
      sideKey = `${row.flow_id}:${row.side}`
    if (
      !flow ||
      flow[row.side] !== 'unknown' ||
      sides.has(sideKey) ||
      (row.status === 'known_other'
        ? !row.excluded_entity_ids.length || !supported(row.flow_id)
        : row.excluded_entity_ids.length !== 0)
    )
      return
    sides.add(sideKey)
    participants.push(row as FlowParticipant)
  }
  for (const row of value.instances) {
    if (
      !object(row) ||
      typeof row.left_flow_id !== 'string' ||
      typeof row.right_flow_id !== 'string' ||
      row.left_flow_id === row.right_flow_id ||
      (row.result !== 'same' && row.result !== 'different' && row.result !== 'unknown') ||
      (row.kind !== 'physical' && row.kind !== 'content' && row.kind !== 'unknown') ||
      !boundedText(row.evidence, 800, row.result !== 'unknown') ||
      !boundedText(row.reason, 1200, true) ||
      Object.keys(row).sort().join(',') !== 'evidence,kind,left_flow_id,reason,result,right_flow_id'
    )
      return
    const left = flows.find((flow) => flow.flow_id === row.left_flow_id),
      right = flows.find((flow) => flow.flow_id === row.right_flow_id),
      pair = [row.left_flow_id, row.right_flow_id].sort().join(':')
    if (
      !left ||
      !right ||
      left.object_id !== right.object_id ||
      pairs.has(pair) ||
      (row.result !== 'unknown' &&
        (!supported(row.left_flow_id) || !supported(row.right_flow_id) || row.kind === 'unknown'))
    )
      return
    pairs.add(pair)
    instances.push(row as FlowInstance)
  }
  return { participants, instances }
}
const object = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value)
function names(value: unknown, prefix: string): Record<string, string> | undefined {
  if (!object(value) || Object.keys(value).length > 24 || !Object.keys(value).length) return
  const result: Record<string, string> = {}
  for (const [id, name] of Object.entries(value)) {
    if (!new RegExp(`^${prefix}(?:0[1-9]|1\\d|2[0-4])$`).test(id) || typeof name !== 'string' || !name.trim())
      return
    result[id] = name
  }
  return result
}

/** Validate the source-only supplement separately from the final plot verdict. */
export function readSourceFlowReview(guard: unknown): SourceFlowReview | undefined {
  if (!object(guard)) return
  const value = guard.source_entailment
  if (
    !object(value) ||
    !isSourceFlowVersion(value.version) ||
    guard.role_observation_version !==
      (value.version === FLOW_ENTAILMENT_VERSION
        ? 'source-role-observation/3'
        : 'source-role-observation/2') ||
    typeof value.input_sha256 !== 'string' ||
    !/^[a-f0-9]{64}$/u.test(value.input_sha256) ||
    typeof value.status !== 'string' ||
    !['not_applicable', 'supported', 'needs_review'].includes(value.status) ||
    !Array.isArray(value.proposals) ||
    value.proposals.length > 24 ||
    !Array.isArray(value.checks) ||
    value.checks.length !== value.proposals.length ||
    (value.note !== undefined && typeof value.note !== 'string')
  )
    return
  const entities = names(guard.entities, 'E'),
    objects = names(guard.objects, 'O')
  if (!entities || !objects) return
  const proposals: Flow[] = [],
    checks: FlowCheck[] = []
  for (const row of value.proposals) {
    if (
      !object(row) ||
      typeof row.flow_id !== 'string' ||
      !/^F(?:0[1-9]|1\d|2[0-4])$/u.test(row.flow_id) ||
      typeof row.object_id !== 'string' ||
      !Object.hasOwn(objects, row.object_id) ||
      typeof row.supplier_id !== 'string' ||
      !(row.supplier_id === 'unknown' || Object.hasOwn(entities, row.supplier_id)) ||
      typeof row.recipient_id !== 'string' ||
      !(row.recipient_id === 'unknown' || Object.hasOwn(entities, row.recipient_id)) ||
      !stages.includes(row.stage as Flow['stage']) ||
      (row.certainty !== 'explicit' && row.certainty !== 'ambiguous') ||
      typeof row.evidence !== 'string' ||
      (row.evidence !== '' && !row.evidence.trim()) ||
      (row.certainty === 'explicit' && !row.evidence.trim()) ||
      typeof row.note !== 'string'
    )
      return
    proposals.push({
      flow_id: row.flow_id,
      object_id: row.object_id,
      supplier_id: row.supplier_id,
      recipient_id: row.recipient_id,
      stage: row.stage as Flow['stage'],
      certainty: row.certainty,
      evidence: row.evidence,
      note: row.note,
    })
  }
  const ids = proposals.map((row) => row.flow_id)
  if (new Set(ids).size !== ids.length) return
  for (const row of value.checks) {
    if (
      !object(row) ||
      typeof row.flow_id !== 'string' ||
      !ids.includes(row.flow_id) ||
      (row.result !== 'supported' && row.result !== 'unknown' && row.result !== 'contradicted') ||
      !Array.isArray(row.verified_fields) ||
      row.verified_fields.length > fields.length ||
      row.verified_fields.some((field) => !fields.includes(field)) ||
      new Set(row.verified_fields).size !== row.verified_fields.length ||
      typeof row.evidence !== 'string' ||
      (row.evidence !== '' && !row.evidence.trim()) ||
      typeof row.reason !== 'string' ||
      !row.reason.trim() ||
      ((row.result !== 'unknown' || row.verified_fields.length > 0) && !row.evidence.trim()) ||
      (row.result === 'supported' && row.verified_fields.length !== fields.length) ||
      (row.result === 'contradicted' && row.verified_fields.length === fields.length)
    )
      return
    checks.push({
      flow_id: row.flow_id,
      result: row.result,
      verified_fields: row.verified_fields as VerifiedField[],
      evidence: row.evidence,
      reason: row.reason,
    })
  }
  if (new Set(checks.map((row) => row.flow_id)).size !== ids.length) return
  const expectedStatus =
    ids.length === 0
      ? 'not_applicable'
      : checks.every((row) => row.result === 'supported')
        ? 'supported'
        : 'needs_review'
  if (value.status !== expectedStatus) return
  const associations =
    value.version === FLOW_ENTAILMENT_VERSION
      ? readAssociations(value, proposals, checks, entities)
      : undefined
  if (value.version === FLOW_ENTAILMENT_VERSION && !associations) return
  return {
    version: value.version,
    input_sha256: value.input_sha256,
    status: expectedStatus,
    entities,
    objects,
    proposals,
    checks,
    ...associations,
    note: value.note ?? '',
  }
}

const fieldLabels: Record<VerifiedField, string> = {
  object_id: '内容所指',
  supplier_id: '给出方',
  recipient_id: '接收方',
  stage: '行动阶段',
  certainty: '确定性',
  evidence: '原观察引文',
}
const stageLabels: Record<Flow['stage'], string> = {
  offered: '提出给出',
  requested: '提出索取',
  completed: '已发生',
  planned: '未来计划',
  refused: '拒绝',
  unknown: '阶段未知',
}
const resultLabels = { supported: '原文支持', unknown: '仍未确认', contradicted: '原文明示反证' }
export function SourceFlowDetails({ review }: { review: SourceFlowReview }) {
  return (
    <details>
      <summary>正文流向观察的出处复核 · {review.proposals.length}项</summary>
      <p>
        观察与出处复核只使用本次正文及名称表。提问本身与所求答案分别核对；有引文仍需确认内容、双方和阶段。
      </p>
      <p>
        以下是观察提案的复核，正文是否符合原情节以逐项核对为准。未获支持的观察及原结论保留；来源未知不等于正文违约。
      </p>
      {review.status === 'not_applicable' && <p>本次没有需要复核的相关流向观察。</p>}
      {review.proposals.map((flow) => {
        const check = review.checks.find((row) => row.flow_id === flow.flow_id)!
        const name = (id: string) => (id === 'unknown' ? '身份未知' : review.entities[id])
        return (
          <article className="fact" key={flow.flow_id}>
            <strong>
              {flow.flow_id} · {resultLabels[check.result]}
            </strong>
            <p>
              原观察提案：{name(flow.supplier_id)} → {name(flow.recipient_id)}；内容：
              {review.objects[flow.object_id]}；{stageLabels[flow.stage]}；
              {flow.certainty === 'explicit' ? '观察曾标明确' : '观察保留歧义'}。
            </p>
            <p>原观察引用：</p>
            <blockquote>{flow.evidence || '未提供引文'}</blockquote>
            {flow.note && <p>原观察备注：{flow.note}</p>}
            <p>出处复核：{check.reason}</p>
            <blockquote>{check.evidence || '未取得可定位依据'}</blockquote>
            <p>
              本次核实的字段：{check.verified_fields.map((field) => fieldLabels[field]).join('、') || '尚无'}
              。
            </p>
          </article>
        )
      })}
      {review.participants?.map((participant) => (
        <article className="fact" key={`${participant.flow_id}:${participant.side}`}>
          <strong>
            {participant.flow_id} · {fieldLabels[participant.side]}身份排除核对
          </strong>
          <p>
            {participant.status === 'known_other'
              ? `正文中的${participant.label}可与以下称谓区分：${participant.excluded_entity_ids.map((id) => review.entities[id]).join('、')}。`
              : '身份仍未确认，未据此排除任何称谓。'}
          </p>
          <p>{participant.reason}</p>
          <blockquote>{participant.evidence || '未取得可定位依据'}</blockquote>
        </article>
      ))}
      {review.instances?.map((instance) => (
        <article className="fact" key={[instance.left_flow_id, instance.right_flow_id].sort().join(':')}>
          <strong>
            {instance.left_flow_id} / {instance.right_flow_id} ·
            {instance.result === 'same'
              ? '同一实例'
              : instance.result === 'different'
                ? '不同实例'
                : '实例关联未知'}
          </strong>
          <p>
            {instance.kind === 'physical'
              ? '实物'
              : instance.kind === 'content'
                ? '信息内容'
                : '实例类型未知'}
            ：{instance.reason}
          </p>
          <blockquote>{instance.evidence || '未取得可定位依据'}</blockquote>
        </article>
      ))}
      {review.version === FLOW_ENTAILMENT_VERSION && (
        <p>
          身份排除和实例关联只说明上述局部依据；不同实物不豁免“仅向”同一内容的限制，也不代表整份正文或文学质量通过。
        </p>
      )}
      {review.note && <p>{review.note}</p>}
      <p className="small muted">
        版本：{review.version}；本次输入摘要：{review.input_sha256}。该补充仅对应本份报告绑定的正文版本。
      </p>
    </details>
  )
}
