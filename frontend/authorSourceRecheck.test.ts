import { expect, test } from 'vitest'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { AuthorSourceRecheckReceipt } from './AuthorSourceRecheckPanel'
import { sourceRecheckFixture } from '../fixtures/author-source-recheck'
import { canonical, object } from './planRestart'
import {
  AUTHOR_SOURCE_RECHECK_VERSION,
  AUTHORED_SOURCE_POLICY_VERSION,
  readPendingSourceRecheck,
  readSourceRecheckChild,
  readSourceRecheckLookup,
  readSourceRecheckPreview,
  sourceRecheckPath,
  sourceRecheckPreviewPath,
  sourceRecheckUnavailableReason,
} from './authorSourceRecheck'
const row = (value: unknown) => {
  const found = object(value)
  if (!found) throw Error('Invalid synthetic object')
  return found
}

test('known completed stop binds identical original source, fees and only the new policy', () => {
  const { source, preview, request, child } = sourceRecheckFixture(),
    pending = { source, preview, request }
  expect(readSourceRecheckPreview(preview, source)).toEqual(preview)
  expect(
    readPendingSourceRecheck(pending, canonical([source.novel_id, source.branch_id, source.id])),
  ).toEqual(pending)
  expect(readSourceRecheckChild(child(), pending)).toEqual(child())
  expect(
    readSourceRecheckLookup(
      { source_task_id: source.id, client_request_id: request.client_request_id, task: null },
      pending,
    ),
  ).toBeNull()
  expect(
    readSourceRecheckLookup(
      { source_task_id: source.id, client_request_id: request.client_request_id, task: child() },
      pending,
    ),
  ).toEqual(child())
  expect(Object.keys(request).sort()).toEqual([
    'client_request_id',
    'preview_basis_sha256',
    'reason',
    'version',
  ])
  expect(sourceRecheckPreviewPath('字/a%2F')).toBe('/tasks/%E5%AD%97%2Fa%252F/author-source-recheck-preview')
  const url = new URL(sourceRecheckPath(source.id, request.client_request_id), 'http://offline')
  expect(url.searchParams.get('client_request_id')).toBe(request.client_request_id)
})
test.each([
  'new-policy',
  'future-policy',
  'candidate',
  'continued',
  'active',
  'unknown-dispatch',
  'ready-source',
])('no shortcut from %s', (change) => {
  const { source, preview, record } = sourceRecheckFixture()
  if (change === 'new-policy')
    row(source.result).authored_source_scope_version = AUTHORED_SOURCE_POLICY_VERSION
  if (change === 'future-policy') row(source.result).authored_source_scope_version = 'future'
  if (change === 'candidate') source.result.waiting_artifact = 'candidate'
  if (change === 'continued') source.result.continued_to = 'existing-child'
  if (change === 'active') source.status = 'running'
  if (change === 'unknown-dispatch') record.phases.observe.status = 'started'
  if (change === 'ready-source') record.state = 'ready'
  expect(sourceRecheckUnavailableReason(source)).not.toBe('')
  expect(readSourceRecheckPreview(preview, source)).toBeUndefined()
})
test.each([
  'version',
  'old-policy',
  'new-policy',
  'source-unchanged',
  'starts-child',
  'eligible',
  'source',
  'proof-missing',
  'proof-owner',
  'proof-call',
  'proof-step',
  'proof-hash',
  'unknown-budget',
])('preview rejects %s', (change) => {
  const { source, preview } = sourceRecheckFixture(),
    value = row(structuredClone(preview)),
    proof = row(row(value.budget).source_steps)
  if (change === 'version') value.version = 'future'
  if (change === 'old-policy') value.old_policy = AUTHORED_SOURCE_POLICY_VERSION
  if (change === 'new-policy') value.new_policy = 'future'
  if (change === 'source-unchanged') value.source_unchanged = false
  if (change === 'starts-child') value.starts_child = false
  if (change === 'eligible') value.eligible = false
  if (change === 'source') row(value.current_source).text += '新情节'
  if (change === 'proof-missing') delete row(value.budget).source_steps
  if (change === 'proof-owner') proof.owner_task_ids = ['other']
  if (change === 'proof-call') proof.call_ids = []
  if (change === 'proof-step') proof.step_fingerprints = []
  if (change === 'proof-hash') proof.sha256 = 'bad'
  if (change === 'unknown-budget') row(value.budget).unknown_reserve = 1
  expect(readSourceRecheckPreview(value, source)).toBeUndefined()
})
test.each(['clarification', 'actor', 'account', 'nonce', 'basis', 'empty-reason', 'long-reason'])(
  'pending rejects extra or changed %s',
  (change) => {
    const fixture = sourceRecheckFixture(),
      pending = { source: fixture.source, preview: fixture.preview, request: row(fixture.request) }
    if (['clarification', 'actor', 'account'].includes(change)) pending.request[change] = 'extra'
    if (change === 'nonce') pending.request.client_request_id = 'tiny'
    if (change === 'basis') pending.request.preview_basis_sha256 = 'b'.repeat(64)
    if (change === 'empty-reason') pending.request.reason = '  '
    if (change === 'long-reason') pending.request.reason = '𠮷'.repeat(2001)
    expect(
      readPendingSourceRecheck(
        pending,
        canonical([fixture.source.novel_id, fixture.source.branch_id, fixture.source.id]),
      ),
    ).toBeUndefined()
  },
)
test.each([
  'source',
  'previous-source',
  'original-source',
  'old-receipt',
  'old-policy',
  'new-policy',
  'policies',
  'previous-policies',
  'request',
  'budget',
  'account',
  'plan',
  'literary',
  'cancel',
])('child rejects %s without accepting a guessed continuation', (change) => {
  const { source, preview, request, child } = sourceRecheckFixture(),
    value = child(),
    receipt = row(row(value.result).author_source_recheck)
  if (change === 'source') row(receipt.source_entry).text += '新写情节'
  if (change === 'previous-source') receipt.previous_source_entry = {}
  if (change === 'original-source') receipt.original_source_entry = {}
  if (change === 'old-receipt') receipt.previous_source_receipt = {}
  if (change === 'old-policy') receipt.old_policy = AUTHORED_SOURCE_POLICY_VERSION
  if (change === 'new-policy') row(value.result).authored_source_scope_version = 'authored-source-scope/1'
  if (change === 'policies') receipt.policies = preview.policies
  if (change === 'previous-policies') receipt.previous_policies = {}
  if (change === 'request') receipt.request = { ...request, reason: 'different' }
  if (change === 'budget') value.payload.budget_limit = 100
  if (change === 'account') value.payload.account = 'other'
  if (change === 'plan') value.result.plan = {}
  if (change === 'literary') receipt.author_literary_approval = 'pass'
  if (change === 'cancel') Object.assign(value, { cancel: '0' })
  expect(readSourceRecheckChild(value, { source, preview, request })).toBeUndefined()
})
test('lookup never changes owner or opaque nonce, and accepts progress only from the frozen receipt', () => {
  const { source, preview, request, child } = sourceRecheckFixture(),
    pending = { source, preview, request },
    value = child()
  value.status = 'needs_review'
  value.result.next_chapter = 3
  value.result.repair_round = 2
  expect(readSourceRecheckChild(value, pending)).toEqual(value)
  expect(
    readSourceRecheckLookup(
      { source_task_id: 'other', client_request_id: request.client_request_id, task: value },
      pending,
    ),
  ).toBeUndefined()
  expect(
    readSourceRecheckLookup(
      { source_task_id: source.id, client_request_id: request.client_request_id + 'x', task: value },
      pending,
    ),
  ).toBeUndefined()
  expect(AUTHOR_SOURCE_RECHECK_VERSION).toBe('author-source-recheck/1')
})
test.each(['blocked_creative', 'segment_complete', 'production_complete'])(
  'a %s child remains discoverable through its original creation receipt',
  (status) => {
    const { source, preview, request, child } = sourceRecheckFixture(),
      value = child()
    value.status = status
    expect(readSourceRecheckChild(value, { source, preview, request })).toEqual(value)
  },
)
test.each(['ready', 'unverified', 'future'])(
  '新来源%s的观察、/2绑定与完整保持只读展示，不把未知变通过',
  (state) => {
    const value = sourceRecheckFixture().child()
    row(value.result).authored_source_scope_receipts = {
      'chapter:2': {
        version: state === 'future' ? 'future' : AUTHORED_SOURCE_POLICY_VERSION,
        state: state === 'future' ? 'ready' : state,
        bindings: { version: AUTHORED_SOURCE_POLICY_VERSION },
        phases: {
          preserve: {
            status: 'completed',
            response: {
              source_checks: [
                {
                  source_id: 'SCOPE0001',
                  classification_result: state === 'ready' ? 'pass' : 'unknown',
                  result: 'pass',
                },
              ],
              summary: '<script>只读</script>',
            },
          },
        },
      },
    }
    const html = renderToStaticMarkup(createElement(AuthorSourceRecheckReceipt, { task: value }))
    expect(html).toContain('本任务新版来源观察、全部情节解释与独立保持')
    expect(html).toContain('classification_result')
    expect(html).toContain('&lt;script&gt;')
    expect(html).not.toContain('<script>')
    expect(html.includes('新版来源模型核对就绪')).toBe(state === 'ready')
    expect(html).toContain('作者文学认可')
  },
)
