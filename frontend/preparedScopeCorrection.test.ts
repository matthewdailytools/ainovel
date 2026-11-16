import { expect, test } from 'vitest'
import { preparedScopeFixture as f } from '../fixtures/prepared-scope-correction'
import { ApiError, type Artifact } from './api'
import { correctionOwner, ownerKey, readSourceReceipt, readCorrectedReport } from './sourceCorrection'
import {
  createScopeOperation,
  readScopePreview,
  readScopeReceipt,
  readScopeBodyPreview,
  readScopeReport,
  readScopeTask,
  readScopeCapsule,
  scopeEligibility,
  type ScopeCapsule,
} from './preparedScopeCorrection'
const owner = correctionOwner(f.candidate as unknown as Artifact, f.story.id, f.story.branch_id)!
const sourceCapsule = (): ScopeCapsule => ({
  phase: 'source',
  owner,
  preview: readScopePreview(f.preview, owner)!,
  request: {
    candidate_revision: 0,
    preview_basis_sha256: f.preview.basis_sha256,
    account: 'openai_1',
    client_request_id: 'new-scope-request-123',
    item_ids: ['P01'],
    reason: '',
    budget_limit: 1,
    source_identity_context: false,
  },
})
const bodyCapsule = (): ScopeCapsule =>
  ({
    phase: 'recheck',
    owner,
    preview: structuredClone(f.bodyPreview),
    request: {
      candidate_revision: 0,
      preview_basis_sha256: f.bodyPreview.basis_sha256,
      account: 'openai_1',
      client_request_id: 'scope-body-native-123',
      receipt_id: f.receipt.id,
      budget_limit: 1,
      source_identity_context: false,
    },
  }) as ScopeCapsule
test('official v2 fixture parses both explicit stages and keeps legacy versions distinct', () => {
  expect(readScopePreview(f.preview, owner)).toBeTruthy()
  const receipt = readScopeReceipt(f.receipt.payload)
  expect(receipt).toBeTruthy()
  expect(readScopeBodyPreview(f.bodyPreview, owner, f.receipt.id, receipt!)).toBeTruthy()
  expect(readScopeReport(f.report.payload)).toBeTruthy()
  expect(readSourceReceipt(f.receipt.payload)).toBeUndefined()
  expect(readCorrectedReport(f.report.payload)).toBeUndefined()
  expect(readScopeTask(f.sourceQueued, sourceCapsule())).toBeTruthy()
  expect(readScopeTask(f.sourceDone, sourceCapsule())).toBeTruthy()
  expect(readScopeTask(f.bodyQueued, bodyCapsule())).toBeTruthy()
  expect(readScopeTask(f.bodyDone, bodyCapsule())).toBeTruthy()
  expect(readScopeCapsule(sourceCapsule(), ownerKey(owner))).toBeTruthy()
  expect(readScopeCapsule(bodyCapsule(), ownerKey(owner))).toBeTruthy()
  expect(
    scopeEligibility(f.candidate as unknown as Artifact, f.parent, f.chapter, f.story.branch_id),
  ).toBeUndefined()
})
const clone = <T>(v: T): T => structuredClone(v)
const invalidReceipts: [string, (r: any) => void][] = [
  ['unknown version', (r: any) => (r.version = 'prepared-source-scope-correction/9')],
  ['author origin', (r: any) => (r.source_kind = 'author_plot')],
  ['missing inventory', (r: any) => delete r.inventory],
  ['mixed inventory hash', (r: any) => (r.inventory.inventory.input_sha256 = 'a'.repeat(64))],
  ['unresolved source quote', (r: any) => (r.inventory.sources[0].text = '原文没有此处')],
  ['split UTF16 position', (r: any) => (r.inventory.sources[0].start = 1)],
  ['unsupported source ready', (r: any) => (r.inventory.inventory.observations[0].kind = 'unknown')],
  ['unknown proposal ready', (r: any) => (r.proposal.items[0].status = 'unknown')],
  ['missing preservation', (r: any) => (r.preservation = null)],
  ['insufficient two fields', (r: any) => (r.preservation.item_checks[0].scope_only_fix_sufficient = 'no')],
  ['evidence mismatch', (r: any) => (r.preservation.item_checks[0].evidence = '此句不在原章纲')],
  ['changed actor', (r: any) => (r.corrected_contract.items[0].actor = '另一人')],
  ['changed action', (r: any) => (r.corrected_contract.items[0].action = '偷取')],
  ['changed completed stage', (r: any) => (r.corrected_contract.items[0].transfer_scope = 'planned')],
  ['changed result', (r: any) => (r.corrected_contract.items[0].result = '回执已到')],
  ['changed historic note', (r: any) => (r.corrected_contract.note = '省略旧无源only')],
  ['changed fixed', (r: any) => (r.preservation.fixed_checks[0].evidence = '伪造固定项')],
  ['missing P', (r: any) => (r.proposal.items = [])],
]
test.each(invalidReceipts)('strict receipt rejects %s', (_label, mutate) => {
  const r = clone(f.receipt.payload)
  mutate(r)
  expect(readScopeReceipt(r)).toBeUndefined()
})
test('semantic unknown/null preservation is preserved as unverified, never inferred ready', () => {
  const r = clone(f.receipt.payload) as any
  r.state = 'unverified'
  r.inventory.inventory.observations[0].kind = 'unknown'
  r.inventory.status = 'needs_review'
  r.proposal.items[0].status = 'unknown'
  r.preservation = null
  expect(readScopeReceipt(r)?.state).toBe('unverified')
})
test('unselected P changes and mixed source/body request markers are rejected', () => {
  const r = clone(f.receipt.payload)
  r.snapshot.item_ids = ['P02']
  expect(readScopeReceipt(r)).toBeUndefined()
  const c = sourceCapsule()
  ;(c.request as any).receipt_id = f.receipt.id
  expect(readScopeCapsule(c, ownerKey(owner))).toBeUndefined()
  const b = bodyCapsule()
  ;(b.preview as any).version = 'source-corrected-candidate-recheck/1'
  expect(readScopeCapsule(b, ownerKey(owner))).toBeUndefined()
})
test.each([
  'novel_id',
  'branch_id',
  'source_candidate_id',
  'account',
  'client_request_id',
  'preview_basis_sha256',
] as const)('task request %s must match exactly', (key) => {
  const t = clone(f.sourceQueued) as any
  t.payload[key] = 'foreign'
  expect(readScopeTask(t, sourceCapsule())).toBeUndefined()
})
test('new body report requires FLOW2 with workflow-plan origin', () => {
  const r = clone(f.report.payload) as any
  r.plot_audit.direction_guard.source_entailment.version = 'source-flow-entailment/1'
  expect(readScopeReport(r)).toBeUndefined()
})
function memory() {
  const rows = new Map<string, string>()
  return {
    rows,
    getItem: (key: string) => rows.get(key) ?? null,
    setItem: (key: string, v: string) => void rows.set(key, v),
    removeItem: (key: string) => void rows.delete(key),
  }
}
test('uncertain POST and all-character nonce only query same request through 404/null then same child', async () => {
  const storage = memory(),
    requests: { path: string; method?: string; body?: unknown }[] = []
  let n = 0
  const c = sourceCapsule(),
    nonce = 'scope/字%2F\n?#😀'
  c.request.client_request_id = nonce
  const task = clone(f.sourceQueued) as any
  task.payload.client_request_id = nonce
  const call = async (path: string, method?: string, body?: unknown) => {
    requests.push({ path, method, body })
    if (method === 'POST') throw Error('response lost')
    n++
    expect(new URL(path, 'http://test').searchParams.get('client_request_id')).toBe(nonce)
    if (n === 1) throw new ApiError('not_found', '暂未找到', 404)
    return { source_candidate_id: owner.artifactId, client_request_id: nonce, task: n === 2 ? null : task }
  }
  const first = createScopeOperation(ownerKey(owner), call, storage, () => {})
  await first.start(c)
  expect(first.state.status).toBe('uncertain')
  await first.start(c)
  expect(requests).toHaveLength(1)
  first.dispose()
  const restored = createScopeOperation(ownerKey(owner), call, storage, () => {})
  expect(restored.state.pending?.request.client_request_id).toBe(nonce)
  expect(restored.releaseKnown()).toBe(false)
  await restored.lookup()
  expect(restored.state.status).toBe('uncertain')
  await restored.lookup()
  expect(restored.state.status).toBe('uncertain')
  await restored.lookup()
  expect(restored.state.task?.id).toBe(f.sourceQueued.id)
  expect(requests.filter((r) => r.method === 'POST')).toHaveLength(1)
})
test('storage unavailable prevents POST and dispose filters late response', async () => {
  let calls = 0
  const bad = {
    getItem: () => null,
    setItem: () => {
      throw Error('quota')
    },
    removeItem: () => {},
  }
  const e = createScopeOperation(
    ownerKey(owner),
    async () => {
      calls++
      return f.sourceQueued
    },
    bad,
    () => {},
  )
  await e.start(sourceCapsule())
  expect(calls).toBe(0)
  expect(e.state.status).toBe('unavailable')
  let done!: (v: unknown) => void
  const states: string[] = []
  const live = createScopeOperation(
    ownerKey(owner),
    () =>
      new Promise((r) => {
        done = r
      }),
    memory(),
    (s) => states.push(s.status),
  )
  const pending = live.start(sourceCapsule())
  live.dispose()
  done(f.sourceQueued)
  await pending
  expect(states).toEqual(['sending'])
})
test('explicit known rejection may release while malformed stored capsule locks', async () => {
  const e = createScopeOperation(
    ownerKey(owner),
    async () => {
      throw new ApiError('scope_changed', '依据已变', 409)
    },
    memory(),
    () => {},
  )
  await e.start(sourceCapsule())
  expect(e.state.status).toBe('rejected')
  expect(e.releaseKnown()).toBe(true)
  const storage = memory()
  storage.setItem('ainovel.prepared-scope-correction.1:' + ownerKey(owner), '{}')
  expect(
    createScopeOperation(
      ownerKey(owner),
      async () => undefined,
      storage,
      () => {},
    ).state.status,
  ).toBe('unavailable')
})
test.each([
  'chapter_id',
  'chapter_revision',
  'story_revision',
  'source_text_sha256',
  'original_contract_sha256',
] as const)('source snapshot %s cannot drift from preview owner', (key) => {
  const task = clone(f.sourceQueued) as any
  task.result.prepared_source_scope_snapshot[key] = key.includes('revision') ? 999 : 'foreign'
  expect(readScopeTask(task, sourceCapsule())).toBeUndefined()
})
test('body query is explicit GET with stable nonce and cannot downgrade FLOW2', async () => {
  const c = bodyCapsule(),
    nonce = 'scope/body%2F\n字😀'
  c.request.client_request_id = nonce
  const task = clone(f.bodyQueued) as any
  task.payload.client_request_id = nonce
  const storage = memory()
  storage.setItem('ainovel.prepared-scope-correction.1:' + ownerKey(owner), JSON.stringify(c))
  const e = createScopeOperation(
    ownerKey(owner),
    async (path, method) => {
      expect(method).toBe('GET')
      expect(new URL(path, 'http://test').searchParams.get('client_request_id')).toBe(nonce)
      return task
    },
    storage,
    () => {},
  )
  await e.lookup()
  expect(e.state.task?.id).toBe(task.id)
  task.result.flow_source_entailment_version = 'source-flow-entailment/1'
  expect(readScopeTask(task, c)).toBeUndefined()
})
