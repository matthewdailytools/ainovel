import { createHash } from 'node:crypto'
import type { Task } from '../frontend/api'
import {
  AUTHOR_CLARIFICATION_VERSION,
  AUTHORED_SCOPE_VERSION,
  type ClarificationInput,
  type ClarificationPreview,
} from '../frontend/authorSourceClarification'

const hash = (value: unknown) =>
  createHash('sha256')
    .update(typeof value === 'string' ? value : JSON.stringify(value))
    .digest('hex')
export const SOURCE = '掌柜和伙计都站在门内，他把账页交给来客。交接后两人继续等候，不离开门口。'
export const ADDITION = '本章交给来客的是伙计；掌柜只在旁等候。'

/** Generic browser/parser boundary fixture; formal API evidence is run separately. */
export function clarificationFixture(
  id = 'author-clarification-source',
  branch = 'author-clarification-branch',
) {
  const entry = {
    key: 'chapter:2',
    number: 2,
    kind: 'author_plot' as const,
    text: SOURCE,
    sha256: hash(SOURCE),
  }
  const inventory = {
    version: AUTHORED_SCOPE_VERSION,
    status: 'supported',
    source_sha256: entry.sha256,
    inventory: {
      observations: [{ observation_id: 'O01', kind: 'named_destination', supplier: '', recipient: '来客' }],
    },
  }
  const contract = {
    items: [{ actor: '他', certainty: 'ambiguous' }],
    note: '双回指仍未明，未要求角色必须有姓名。',
  }
  const bindings = {
    version: AUTHORED_SCOPE_VERSION,
    items: [
      { item_id: 'P01', observation_ids: ['O01'], status: 'supported', reason: '接收者明确，主体仍未明。' },
    ],
  }
  const record = {
    state: 'unverified',
    source: entry,
    source_sha256: entry.sha256,
    contract,
    bindings,
    phases: {
      observe: {
        status: 'completed',
        owner_task_id: id,
        attempt: 1,
        response: inventory,
        response_sha256: hash(inventory),
      },
      interpret: {
        status: 'completed',
        owner_task_id: id,
        attempt: 1,
        response: { contract, bindings },
        response_sha256: hash({ contract, bindings }),
      },
    },
  }
  const plots = [
    { number: 1, title: '此前记录', plot: '先保存前章记录。', fixed: [] },
    { number: 2, title: '门内交接', plot: SOURCE, fixed: ['两人不离开门口'] },
    { number: 3, title: '后续核验', plot: '以后才核验。', fixed: [] },
  ]
  const payload = {
    mode: 'assisted' as const,
    account: 'deepseek',
    chapters: 3,
    completion_scope: 'segment' as const,
    plots,
    budget_limit: 2,
    adoption: 'manual' as const,
    plot_contract_check: true,
    client_request_id: 'original-' + id,
  }
  const result = {
    next_chapter: 2,
    expected_story_revision: 12,
    repair_round: 1,
    plan: { chapters: plots, ending: '后续核验，未提前结局。' },
    plot_contract_version: 'author-plot-audit/2',
    flow_source_entailment_version: 'source-flow-entailment/2',
    authored_source_scope_version: AUTHORED_SCOPE_VERSION,
    authored_source_scope_snapshot: { version: AUTHORED_SCOPE_VERSION, sources: [entry] },
    authored_source_scope_receipts: { 'chapter:2': record },
  }
  const source: Task = {
    id,
    novel_id: 'author-clarification-novel',
    branch_id: branch,
    kind: 'workflow',
    status: 'blocked_runtime',
    created: 1,
    cancel: 0,
    error: '作者来源仍待核，未开始正文。',
    payload,
    result,
  }
  const policies = {
    plot_contract_version: result.plot_contract_version,
    flow_source_entailment_version: result.flow_source_entailment_version,
    authored_source_scope_version: AUTHORED_SCOPE_VERSION,
  }
  const baseline = {
    preview_basis_sha256: hash(id + 'basis'),
    story_revision: 12,
    chapter_revision: 0 as const,
    chapter_content_sha256: hash(''),
    manuscript_versions: [],
    manuscript_sha256: hash('manuscripts'),
    plan_sha256: hash(result.plan),
    source_receipt_sha256: hash(record),
    payload_sha256: hash(payload),
    progress_sha256: hash(result),
  }
  const preview: ClarificationPreview = {
    version: AUTHOR_CLARIFICATION_VERSION,
    source_task_id: id,
    novel_id: source.novel_id,
    branch_id: branch,
    chapter_id: id + '-chapter2',
    chapter: 2,
    request_basis: { preview_basis_sha256: baseline.preview_basis_sha256 },
    baseline,
    original_source: entry,
    current_source: entry,
    source_review: { state: 'unverified', contract, bindings, inventory },
    plan: result.plan,
    policies,
    adoption: 'manual',
    repair_round: 1,
    account: {
      id: 'deepseek',
      kind: 'deepseek',
      model: 'offline-mock',
      currency: 'CNY',
      configured: true,
      priced: true,
      input_rate: '2',
      output_rate: '8',
      price_source: 'engineering-fixture',
    },
    budget: {
      provider: 'deepseek',
      source_run_limit: 2,
      source_task_settled: 0.12,
      remaining_run_limit: 1.88,
      effective_remaining_run_limit: 1.86,
      auxiliary_settled: 0.02,
      unknown_reserve: 0,
      foreign_history_calls: [],
      foreign_unknown_calls: [],
    },
    shared_budget: [
      {
        provider: 'deepseek',
        currency: 'CNY',
        limit: '80',
        settled: 0.14,
        reserved_unknown: 0,
        remaining: 79.86,
        calls: [],
      },
    ],
    author_literary_approval: 'unverified',
  }
  const request: ClarificationInput = {
    version: AUTHOR_CLARIFICATION_VERSION,
    client_request_id: 'stable-author-clarification-nonce',
    preview_basis_sha256: baseline.preview_basis_sha256,
    clarification: ADDITION,
    actor: 'author',
    reason: '',
  }
  function child(input: ClarificationInput = request): Task {
    const combined = `${entry.text}\n\n【作者本章补充澄清；与原话分开保存】\n${input.clarification}`
    const receipt = {
      version: AUTHOR_CLARIFICATION_VERSION,
      task_id: id + '-child',
      source_task_id: id,
      novel_id: source.novel_id,
      branch_id: branch,
      chapter: 2,
      chapter_id: preview.chapter_id,
      source_payload_sha256: baseline.payload_sha256,
      source_progress_sha256: baseline.progress_sha256,
      previous_source_receipt: record,
      original_source_entry: entry,
      previous_source_entry: entry,
      source_entry: { ...entry, text: combined, sha256: hash(combined) },
      plan_sha256: baseline.plan_sha256,
      baseline,
      request: input,
      budget: preview.budget,
      account: preview.account,
      policies,
      author_literary_approval: 'unverified',
    }
    const childPayload = {
      ...structuredClone(payload),
      client_request_id: input.client_request_id,
      budget_limit: 1.88,
    }
    const childResult = {
      ...structuredClone(result),
      continued_from: id,
      author_source_clarification_version: AUTHOR_CLARIFICATION_VERSION,
      author_source_clarification: receipt,
    }
    return {
      ...structuredClone(source),
      id: id + '-child',
      status: 'queued',
      created: 2,
      payload: childPayload,
      result: childResult,
    }
  }
  return { source, preview, request, child, record, entry }
}
