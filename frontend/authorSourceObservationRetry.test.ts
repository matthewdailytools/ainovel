import { expect, test } from 'vitest'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { observationRetryFixture } from '../fixtures/author-source-observation-retry'
import { AuthorSourceObservationRetryReceipt } from './AuthorSourceObservationRetryPanel'
import { canonical, object } from './planRestart'
import {
  AUTHOR_OBSERVATION_RETRY_VERSION as V,
  OBSERVATION_EXECUTION_VERSION as NEW,
  EXECUTION_KEY,
  RETRY_KEY,
  observationRetryUnavailableReason,
  observationRetryPath,
  readObservationRetryPreview,
  readPendingObservationRetry,
  readObservationRetryChild,
  readObservationRetryLookup,
} from './authorSourceObservationRetry'
const row = (v: unknown) => {
  const r = object(v)
  if (!r) throw Error('Bad fixture')
  return r
}

test('started slot is not inferred unknown: only full returned settled GET proof enables new execution', () => {
  const f = observationRetryFixture(),
    pending = { source: f.source, preview: f.preview, request: f.request }
  expect(observationRetryUnavailableReason(f.source)).toBe('')
  expect(readObservationRetryPreview(f.preview, f.source)).toEqual(f.preview)
  expect(
    readPendingObservationRetry(pending, canonical([f.source.novel_id, f.source.branch_id, f.source.id])),
  ).toEqual(pending)
  expect(readObservationRetryChild(f.child(), pending)).toEqual(f.child())
  const url = new URL(observationRetryPath('钟/a', f.request.client_request_id), 'http://offline')
  expect(url.searchParams.get('client_request_id')).toBe(f.request.client_request_id)
  expect(url.pathname).toContain('%E9%92%9F%2Fa')
})
test.each([
  'new-execution',
  'retry-marker',
  'retry-receipt',
  'continued',
  'running',
  'cancel',
  'candidate',
  'source1',
  'response',
])('old source/history cannot arm from %s', (kind) => {
  const f = observationRetryFixture(),
    r = row(f.source.result)
  if (kind === 'new-execution') r[EXECUTION_KEY] = NEW
  if (kind === 'retry-marker') r[RETRY_KEY] = V
  if (kind === 'retry-receipt') r.author_source_observation_retry = { version: V }
  if (kind === 'continued') r.continued_to = 'existing'
  if (kind === 'running') f.source.status = 'running'
  if (kind === 'cancel') f.source.cancel = 1
  if (kind === 'candidate') r.waiting_artifact = 'draft'
  if (kind === 'source1') r.authored_source_scope_version = 'authored-source-scope/1'
  if (kind === 'response') Object.assign(f.record.phases.observe, { response: {} })
  expect(observationRetryUnavailableReason(f.source)).not.toBe('')
  expect(readObservationRetryPreview(f.preview, f.source)).toBeUndefined()
})
test.each([
  'policy',
  'execution',
  'source',
  'plan',
  'adoption',
  'budget',
  'unknown',
  'proof-owner',
  'proof-attempt',
  'proof-two',
  'proof-empty-errors',
  'proof-raw',
  'proof-dispatch',
  'proof-calls',
  'proof-stop',
  'call-null-charge',
  'call-provider',
  'call-owner',
  'call-unsettled',
  'call-incomplete',
  'eligible',
  'baseline',
])('preview rejects %s', (kind) => {
  const f = observationRetryFixture(),
    v = row(structuredClone(f.preview)),
    proof = row(v.known_returned_proof)
  if (kind === 'policy') v.source_policy = 'authored-source-scope/3'
  if (kind === 'execution') v.new_execution_version = 'future'
  if (kind === 'source') row(v.current_source).text += '改剧情'
  if (kind === 'plan') v.plan = {}
  if (kind === 'adoption') v.adoption = 'manual'
  if (kind === 'budget') row(v.budget).remaining_run_limit = -1
  if (kind === 'unknown') row(v.budget).unknown_reserve = 1
  if (kind === 'proof-owner') proof.owner_task_id = 'other'
  if (kind === 'proof-attempt') proof.attempt = 'other'
  if (kind === 'proof-two') row(v).known_returned_proof = { ...proof, steps: [] }
  if (kind === 'proof-empty-errors') proof.errors = [[], [], []]
  if (kind === 'proof-raw') row((proof.steps as unknown[])[0]).raw_sha256 = 'bad'
  if (kind === 'proof-dispatch') row((proof.steps as unknown[])[0]).dispatch_event_id = 102
  if (kind === 'proof-calls') proof.call_ids = ['same', 'same', 'same']
  if (kind === 'proof-stop') proof.stopped_event_id = 1
  if (kind === 'call-null-charge') row((proof.calls as unknown[])[0]).charged = null
  if (kind === 'call-provider')
    Object.assign(row((proof.calls as unknown[])[0]), { provider: 'openai', currency: 'USD' })
  if (kind === 'call-owner') row((proof.calls as unknown[])[0]).task_id = 'foreign'
  if (kind === 'call-unsettled') row((proof.calls as unknown[])[0]).status = 'unknown'
  if (kind === 'call-incomplete') row((proof.calls as unknown[])[0]).has_complete_step = false
  if (kind === 'eligible') v.eligible = false
  if (kind === 'baseline') row(v.baseline).progress_sha256 = 'bad'
  expect(readObservationRetryPreview(v, f.source)).toBeUndefined()
})
test.each(['owner', 'extra-field', 'nonce', 'empty', 'long', 'basis'])('pending rejects %s', (kind) => {
  const f = observationRetryFixture(),
    pending = { source: f.source, preview: f.preview, request: row(structuredClone(f.request)) }
  let owner = canonical([f.source.novel_id, f.source.branch_id, f.source.id])
  if (kind === 'owner') owner = 'other'
  if (kind === 'extra-field') pending.request.clarification = '不能补情节'
  if (kind === 'nonce') pending.request.client_request_id = 'short'
  if (kind === 'empty') pending.request.reason = ' '
  if (kind === 'long') pending.request.reason = '𠮷'.repeat(2001)
  if (kind === 'basis') pending.request.preview_basis_sha256 = 'b'.repeat(64)
  expect(readPendingObservationRetry(pending, owner)).toBeUndefined()
})
test.each([
  'account',
  'budget',
  'plan',
  'nonce',
  'source',
  'old-receipt',
  'proof',
  'adoption',
  'execution',
  'execution-owner',
  'request',
  'policy',
  'literary',
  'baseline',
  'cancel',
])('child rejects %s', (kind) => {
  const f = observationRetryFixture(),
    value = f.child(),
    r = row(value.result),
    receipt = row(r.author_source_observation_retry)
  if (kind === 'account') value.payload.account = 'foreign'
  if (kind === 'budget') value.payload.budget_limit = 999
  if (kind === 'plan') r.plan = {}
  if (kind === 'nonce') row(value.payload).client_request_id = 'other'
  if (kind === 'source') row(receipt.source_entry).text += '改原话'
  if (kind === 'old-receipt') receipt.previous_source_receipt = {}
  if (kind === 'proof') receipt.known_returned_proof = {}
  if (kind === 'adoption') receipt.adoption = 'manual'
  if (kind === 'execution') r[EXECUTION_KEY] = 'future'
  if (kind === 'execution-owner') row(r.authored_source_observation_execution_snapshot).task_id = 'foreign'
  if (kind === 'request') row(receipt.request).reason = 'other'
  if (kind === 'policy') r.authored_source_scope_version = 'authored-source-scope/3'
  if (kind === 'literary') receipt.author_literary_approval = 'passed'
  if (kind === 'baseline') receipt.baseline = {}
  if (kind === 'cancel') Object.assign(value, { cancel: '0' })
  expect(
    readObservationRetryChild(value, { source: f.source, preview: f.preview, request: f.request }),
  ).toBeUndefined()
})
test('null/mismatched nonce and repeated historical receipt never become a new attempt', () => {
  const f = observationRetryFixture(),
    p = { source: f.source, preview: f.preview, request: f.request }
  expect(
    readObservationRetryLookup(
      { source_task_id: f.source.id, client_request_id: f.request.client_request_id, task: null },
      p,
    ),
  ).toBeNull()
  expect(
    readObservationRetryLookup(
      { source_task_id: 'foreign', client_request_id: f.request.client_request_id, task: f.child() },
      p,
    ),
  ).toBeUndefined()
  const child = f.child()
  child.status = 'production_complete'
  child.result.next_chapter = 13
  expect(readObservationRetryChild(child, p)).toEqual(child)
  expect(observationRetryUnavailableReason(child)).not.toBe('')
  const html = renderToStaticMarkup(createElement(AuthorSourceObservationRetryReceipt, { task: child }))
  expect(html).toContain('三次返回')
  expect(html).toContain('只读')
  expect(html).not.toContain('<button')
})

test('three observation charges do not erase earlier legitimate source charges', () => {
  const f = observationRetryFixture()
  f.preview.budget.source_task_settled = 0.5
  f.preview.budget.remaining_run_limit = 1.5
  f.preview.budget.effective_remaining_run_limit = 1.48
  expect(readObservationRetryPreview(f.preview, f.source)).toEqual(f.preview)
})

test('observation charges cannot exceed the reported parent settled ledger', () => {
  const f = observationRetryFixture()
  f.preview.budget.source_task_settled = 0.1
  f.preview.budget.remaining_run_limit = 1.9
  f.preview.budget.effective_remaining_run_limit = 1.88
  expect(readObservationRetryPreview(f.preview, f.source)).toBeUndefined()
})
test('inherited historical receipt escapes original text and exposes no create action', () => {
  const f = observationRetryFixture(),
    child = f.child(),
    r = row(child.result),
    receipt = row(r.author_source_observation_retry)
  row(receipt.source_entry).text = '<script>alert("fixture-only")</script>'
  child.id = 'history-descendant'
  child.result.continued_from = 'older-child'
  const html = renderToStaticMarkup(createElement(AuthorSourceObservationRetryReceipt, { task: child }))
  expect(html).toContain('&lt;script&gt;')
  expect(html).not.toContain('<script>')
  expect(html).not.toContain('<button')
  expect(observationRetryUnavailableReason(child)).not.toBe('')
})

test('receipt keeps original repair round while later chapters may start a new round', () => {
  const f = observationRetryFixture()
  f.source.result.repair_round = 1
  f.preview.repair_round = 1
  const next = f.child()
  next.result.next_chapter = 3
  next.result.repair_round = 0
  expect(
    readObservationRetryChild(next, { source: f.source, preview: f.preview, request: f.request }),
  ).toEqual(next)
  next.result.next_chapter = f.preview.chapter
  expect(
    readObservationRetryChild(next, { source: f.source, preview: f.preview, request: f.request }),
  ).toBeUndefined()
})
