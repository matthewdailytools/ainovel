import type { Task } from '../frontend/api'
import { clarificationFixture } from './author-source-clarification'
import { object } from '../frontend/planRestart'
import {
  AUTHOR_SOURCE_RECHECK_VERSION,
  AUTHORED_SOURCE_POLICY_VERSION,
  type SourceRecheckInput,
  type SourceRecheckPreview,
} from '../frontend/authorSourceRecheck'

/** Anonymous boundary fixture; actual API/Worker cases use a separate disposable server. */
export function sourceRecheckFixture(
  id = 'author-source-recheck-parent',
  branch = 'author-source-recheck-branch',
) {
  const old = clarificationFixture(id, branch)
  const preview: SourceRecheckPreview = {
    ...old.preview,
    version: AUTHOR_SOURCE_RECHECK_VERSION,
    old_policy: 'authored-source-scope/1',
    new_policy: AUTHORED_SOURCE_POLICY_VERSION,
    source_unchanged: true,
    starts_child: true,
    eligible: true,
    budget: {
      ...old.preview.budget,
      source_steps: {
        owner_task_ids: [id],
        call_ids: ['offline-complete-call-1'],
        step_fingerprints: ['offline-complete-step-1'],
        sha256: 'c'.repeat(64),
      },
    },
  }
  const request: SourceRecheckInput = {
    version: AUTHOR_SOURCE_RECHECK_VERSION,
    client_request_id: 'source-nonce+/字%2F?=same',
    preview_basis_sha256: preview.request_basis.preview_basis_sha256,
    reason: '用新版核对原话分类，不补剧情。',
  }
  function child(input = request): Task {
    const base = old.child({ ...old.request, client_request_id: input.client_request_id })
    const policies = { ...preview.policies, authored_source_scope_version: AUTHORED_SOURCE_POLICY_VERSION }
    const result = object(base.result)!
    delete result.author_source_clarification_version
    delete result.author_source_clarification
    result.authored_source_scope_version = AUTHORED_SOURCE_POLICY_VERSION
    result.author_source_recheck_version = AUTHOR_SOURCE_RECHECK_VERSION
    result.author_source_recheck = structuredClone({
      version: AUTHOR_SOURCE_RECHECK_VERSION,
      task_id: base.id,
      source_task_id: id,
      novel_id: old.source.novel_id,
      branch_id: branch,
      chapter: preview.chapter,
      chapter_id: preview.chapter_id,
      source_payload_sha256: preview.baseline.payload_sha256,
      source_progress_sha256: preview.baseline.progress_sha256,
      previous_source_receipt: old.record,
      original_source_entry: preview.original_source,
      previous_source_entry: preview.current_source,
      source_entry: preview.current_source,
      plan_sha256: preview.baseline.plan_sha256,
      baseline: preview.baseline,
      request: input,
      budget: preview.budget,
      account: preview.account,
      previous_policies: preview.policies,
      policies,
      old_policy: preview.old_policy,
      new_policy: preview.new_policy,
      source_unchanged: true,
      starts_child: true,
      author_literary_approval: 'unverified',
    })
    return base
  }
  return { ...old, preview, request, child }
}
