import { createHash } from 'node:crypto'
import type { Task } from '../frontend/api'
import type { ClarificationInput } from '../frontend/authorSourceClarification'
import { AUTHORED_SCOPE_CURRENT_VERSION } from '../frontend/authorSourceClarification'
import { EXECUTION_KEY, OBSERVATION_EXECUTION_VERSION } from '../frontend/authorSourceObservationRetry'
import { object } from '../frontend/planRestart'
import { clarificationFixture } from './author-source-clarification'
import { observationRetryFixture } from './author-source-observation-retry'

const hash = (value: unknown) =>
  createHash('sha256')
    .update(typeof value === 'string' ? value : JSON.stringify(value))
    .digest('hex')

/** Anonymous structural doubles only; no real manuscript, model return or paid diagnostic. */
export function clarificationFormatFailureFixture(
  id = 'clarification-format-parent',
  branch = 'clarification-format-branch',
) {
  const base = clarificationFixture(id, branch),
    { source, preview, entry } = base,
    proof = structuredClone(observationRetryFixture(id, branch).preview.known_returned_proof),
    texts = [1, 2, 3].map((round) =>
      JSON.stringify({
        input_sha256: hash({ version: AUTHORED_SCOPE_CURRENT_VERSION, source: entry }),
        observations: [
          {
            observation_id: 'O01',
            kind: 'named_destination',
            supplier: '',
            recipient: '来客',
            transfer_object: '账页',
            transfer_scope: 'completed',
            evidence: 'SCOPE0002',
            restriction_evidence: '',
            condition: '',
            reason: '工程替身保留故意不匹配的原句锚，不能当成有效观察。',
          },
        ],
        unit_checks: [
          {
            source_id: 'SCOPE0001',
            status: 'covered',
            observation_ids: ['O01'],
            reason: '工程引用失败样例。',
          },
          {
            source_id: 'SCOPE0002',
            status: 'not_scope_related',
            observation_ids: [],
            reason: '仍保持等候。',
          },
        ],
        note: `离线工程返回${round}，不表示模型产出质量。`,
      }),
    )
  proof.steps = texts.map((text, index) => ({
    fingerprint: hash(`${id}-offline-step-${index + 1}`),
    call_id: `${id}-offline-call-${index + 1}`,
    raw_sha256: hash({ text, call_id: `${id}-offline-call-${index + 1}` }),
    dispatch_event_id: 101 + index * 3,
    issues: ['工程示例：观察锚未匹配'],
  }))
  proof.calls = proof.calls.map((call, index) => ({ ...call, id: proof.steps[index].call_id }))
  proof.call_ids = proof.steps.map((step) => step.call_id)
  proof.errors = proof.steps.map((step) => [...step.issues])
  proof.stopped_event_id = 110
  proof.sha256 = hash({ steps: proof.steps, calls: proof.calls })
  const snapshot = {
    ...object(object(source.result)!.authored_source_scope_snapshot),
    version: AUTHORED_SCOPE_CURRENT_VERSION,
    [EXECUTION_KEY]: OBSERVATION_EXECUTION_VERSION,
  }
  const executionSnapshot = {
    version: OBSERVATION_EXECUTION_VERSION,
    task_id: id,
    novel_id: source.novel_id,
    branch_id: branch,
    kind: 'workflow',
    payload_sha256: hash(source.payload),
    source_policy_version: AUTHORED_SCOPE_CURRENT_VERSION,
    source_snapshot_sha256: hash(snapshot),
    legacy_receipts: {},
    creation: 'new_task',
  }
  const metadata = {
    [EXECUTION_KEY]: OBSERVATION_EXECUTION_VERSION,
    authored_source_observation_execution_snapshot: executionSnapshot,
    authored_source_observation_execution_sha256: hash(executionSnapshot),
  }
  const record = {
    version: AUTHORED_SCOPE_CURRENT_VERSION,
    source: entry,
    source_sha256: entry.sha256,
    phases: {
      observe: {
        owner_task_id: id,
        attempt: proof.attempt,
        status: 'started',
        not_sent_history: [],
        before_steps: [],
        before_calls: [],
      },
    },
    inherited: null,
    state: 'pending',
    status: 'observing',
    ...metadata,
  }
  source.error = '离线工程观察返回均未通过锚校验，尚无正文。'
  Object.assign(source, { attempt: proof.attempt })
  Object.assign(source.result, metadata, {
    authored_source_scope_version: AUTHORED_SCOPE_CURRENT_VERSION,
    authored_source_scope_snapshot: snapshot,
    authored_source_scope_receipts: { [entry.key]: record },
  })
  Object.assign(preview.policies, {
    authored_source_scope_version: AUTHORED_SCOPE_CURRENT_VERSION,
    [EXECUTION_KEY]: OBSERVATION_EXECUTION_VERSION,
  })
  preview.source_review = {
    state: 'pending',
    inventory: null,
    contract: null,
    bindings: null,
    observation_failure: {
      kind: 'known_returned_invalid_observation',
      proof,
      raw_returns: proof.steps.map((step, index) => ({ ...structuredClone(step), text: texts[index] })),
      notice: '离线工程示例：三份原始返回完整但格式失败，旧started现场保留，inventory为空。',
    },
  }
  preview.budget.source_steps = structuredClone(proof)
  preview.budget.observation_failure = structuredClone(proof)
  Object.assign(preview.baseline, {
    preview_basis_sha256: hash({ id, kind: 'format-failure-preview' }),
    source_receipt_sha256: hash(record),
    progress_sha256: hash(source.result),
  })
  preview.request_basis.preview_basis_sha256 = preview.baseline.preview_basis_sha256
  base.request.preview_basis_sha256 = preview.baseline.preview_basis_sha256

  function child(input: ClarificationInput = base.request): Task {
    const value = base.child(input),
      receipt = object(object(value.result)!.author_source_clarification)!
    const label = input.actor === 'author' ? '作者' : '授权操作者',
      combined = `${entry.text}\n\n【${label}本章补充澄清；与原话分开保存】\n${input.clarification}`
    receipt.source_entry = { ...entry, text: combined, sha256: hash(combined) }
    receipt.previous_source_receipt = structuredClone(record)
    receipt.observation_failure = structuredClone(proof)
    object(value.result)!.authored_source_scope_receipts = {}
    return value
  }
  return { ...base, record, child }
}
