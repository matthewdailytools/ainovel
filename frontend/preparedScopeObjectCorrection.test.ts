import { expect, test } from 'vitest'
import { preparedScopeFixture as f } from '../fixtures/prepared-scope-correction'
import { ApiError, type Artifact } from './api'
import { correctionOwner, object, ownerKey } from './sourceCorrection'
import * as old from './preparedScopeCorrection'
import {
  OBJECT_ALIAS_VERSION,
  SCOPE_OBJECT_VERSION,
  SCOPE_OBJECT_BODY_VERSION,
  createScopeOperation,
  readScopePreview,
  readScopeReceipt,
  readScopeBodyPreview,
  readScopeCapsule,
  readScopeTask,
  readScopeReport,
  scopePreviewPath,
  type ScopeCapsule,
} from './preparedScopeObjectCorrection'

const owner = correctionOwner(f.candidate as unknown as Artifact, f.story.id, f.story.branch_id)!
const clone = <T>(value: T): T => structuredClone(value)
const row = (value: unknown): Record<string, unknown> => {
  if (!object(value)) throw Error('Expected record in synthetic test input')
  return value
}
const rows = (value: unknown): unknown[] => {
  if (!Array.isArray(value)) throw Error('Expected array in synthetic test input')
  return value
}
function objectReceipt() {
  const base = clone(f.receipt.payload)
  const observation = base.inventory.inventory.observations[0]
  observation.transfer_object = '测绘记录的抄件'
  return {
    ...base,
    version: SCOPE_OBJECT_VERSION,
    snapshot: { ...base.snapshot, version: SCOPE_OBJECT_VERSION, verify_object_aliases: true },
    object_alias_resolution: {
      version: OBJECT_ALIAS_VERSION,
      state: 'supported',
      input_sha256: 'c'.repeat(64),
      required_item_ids: ['P01'],
      items: [
        {
          item_id: 'P01',
          status: 'supported',
          observation_id: observation.observation_id,
          checks: [
            {
              observation_id: observation.observation_id,
              relation: 'same_transfer_same_instance',
              evidence_source: clone(base.inventory.sources[0]),
              reason: '原句明确这是同一次寄出的同一份材料。',
            },
          ],
          reason: '称谓不同，不改变主体、动作、阶段或物品实例。',
        },
      ],
      note: '仅本次原文关联，不新增本书事实。',
    },
    preservation: {
      ...base.preservation,
      item_checks: base.preservation.item_checks.map((check) => ({ ...check, object_alias_check: 'pass' })),
    },
  }
}
function sourceCapsule(): ScopeCapsule {
  return {
    phase: 'source',
    owner,
    preview: readScopePreview(
      { ...f.preview, version: SCOPE_OBJECT_VERSION, verify_object_aliases: true },
      owner,
      true,
    )!,
    request: {
      candidate_revision: 0,
      preview_basis_sha256: f.preview.basis_sha256,
      account: 'openai_1',
      client_request_id: 'new-scope-request-123',
      item_ids: ['P01'],
      reason: '',
      budget_limit: 1,
      source_identity_context: false,
      verify_object_aliases: true,
    },
  }
}
function bodyCapsule(): ScopeCapsule {
  const receipt = readScopeReceipt(objectReceipt())!
  const preview = readScopeBodyPreview(
    { ...f.bodyPreview, version: SCOPE_OBJECT_BODY_VERSION, verify_object_aliases: true },
    owner,
    f.receipt.id,
    receipt,
  )!
  return {
    phase: 'recheck',
    owner,
    preview,
    request: {
      candidate_revision: 0,
      preview_basis_sha256: f.bodyPreview.basis_sha256,
      account: 'openai_1',
      client_request_id: 'scope-body-native-123',
      receipt_id: f.receipt.id,
      budget_limit: 1,
      source_identity_context: false,
    },
  }
}
function sourceTask() {
  const task = clone(f.sourceQueued)
  return {
    ...task,
    payload: {
      ...task.payload,
      verify_object_aliases: true,
      prepared_source_scope_version: SCOPE_OBJECT_VERSION,
    },
    result: {
      ...task.result,
      prepared_source_scope_version: SCOPE_OBJECT_VERSION,
      prepared_source_scope_snapshot: {
        ...task.result.prepared_source_scope_snapshot,
        version: SCOPE_OBJECT_VERSION,
        verify_object_aliases: true,
      },
    },
  }
}
function bodyTask() {
  const task = clone(f.bodyQueued)
  return {
    ...task,
    result: {
      ...task.result,
      source_corrected_recheck_version: SCOPE_OBJECT_BODY_VERSION,
      source_corrected_recheck_snapshot: {
        ...task.result.source_corrected_recheck_snapshot,
        version: SCOPE_OBJECT_BODY_VERSION,
        verify_object_aliases: true,
      },
    },
  }
}
test('legacy delegates remain byte-shaped and false sends no new key', () => {
  expect(readScopePreview(f.preview, owner, false)).toEqual(old.readScopePreview(f.preview, owner))
  expect(readScopeReceipt(f.receipt.payload)).toEqual(old.readScopeReceipt(f.receipt.payload))
  const capsule = { ...sourceCapsule(), preview: readScopePreview(f.preview, owner)! }
  delete capsule.request.verify_object_aliases
  expect(readScopeCapsule(capsule, ownerKey(owner))).toEqual(old.readScopeCapsule(capsule, ownerKey(owner)))
  expect(readScopeTask(f.sourceQueued, capsule)).toEqual(
    old.readScopeTask(f.sourceQueued, capsule as old.ScopeCapsule),
  )
  expect('verify_object_aliases' in capsule.request).toBe(false)
  expect(scopePreviewPath('a/字%2F', false)).toBe(
    '/artifacts/a%2F%E5%AD%97%252F/prepared-source-scope-preview',
  )
  expect(new URL(scopePreviewPath('id', true), 'http://test').searchParams.get('verify_object_aliases')).toBe(
    'true',
  )
})
test('explicit preview requires the selected policy and keeps version2 distinct', () => {
  const preview = sourceCapsule().preview
  expect(readScopePreview(preview, owner, true)?.version).toBe(SCOPE_OBJECT_VERSION)
  expect(readScopePreview(preview, owner, false)).toBeUndefined()
  expect(readScopePreview(f.preview, owner, true)).toBeUndefined()
  expect(readScopePreview({ ...f.preview, verify_object_aliases: false }, owner, false)).toBeUndefined()
  expect(old.readScopePreview(preview, owner)).toBeUndefined()
})
test('grounded unique material association and full preservation form a readable v2 receipt', () => {
  const receipt = readScopeReceipt(objectReceipt())
  expect(receipt?.version).toBe(SCOPE_OBJECT_VERSION)
  expect(receipt && 'object_alias_resolution' in receipt && receipt.object_alias_resolution.state).toBe(
    'supported',
  )
  expect(receipt?.corrected_contract.items[0].transfer_object).toBe(
    f.receipt.payload.corrected_contract.items[0].transfer_object,
  )
  expect(old.readScopeReceipt(objectReceipt())).toBeUndefined()
})
test.each(['prepared_contract', 'corrected_contract'])(
  'wire %s never tolerates a UI-generated item id',
  (target) => {
    const value = objectReceipt() as unknown as Record<string, unknown>
    const contract = target === 'prepared_contract' ? row(value.snapshot)[target] : value[target]
    row(rows(row(contract).items)[0]).id = 'P01'
    expect(readScopeReceipt(value)).toBeUndefined()
  },
)
const invalid: [string, (value: Record<string, unknown>) => void][] = [
  ['missing opt-in snapshot', (v) => delete row(v.snapshot).verify_object_aliases],
  ['missing association', (v) => delete v.object_alias_resolution],
  [
    'foreign alias policy',
    (v) => {
      row(v.object_alias_resolution).version = 'other/1'
    },
  ],
  [
    'missing required P',
    (v) => {
      row(v.object_alias_resolution).required_item_ids = []
    },
  ],
  [
    'extra P',
    (v) => {
      row(v.object_alias_resolution).required_item_ids = ['P02']
    },
  ],
  [
    'missing comparison',
    (v) => {
      row(rows(row(v.object_alias_resolution).items)[0]).checks = []
    },
  ],
  [
    'unknown promoted',
    (v) => {
      row(rows(row(v.object_alias_resolution).items)[0]).status = 'unknown'
    },
  ],
  [
    'no unique observation',
    (v) => {
      row(rows(row(v.object_alias_resolution).items)[0]).observation_id = ''
    },
  ],
  [
    'ungrounded same-instance',
    (v) => {
      row(rows(row(rows(row(v.object_alias_resolution).items)[0]).checks)[0]).evidence_source = null
    },
  ],
  [
    'foreign quote',
    (v) => {
      const check = row(rows(row(rows(row(v.object_alias_resolution).items)[0]).checks)[0])
      row(check.evidence_source).text = '不是原文'
    },
  ],
  [
    'wrong observation',
    (v) => {
      row(rows(row(rows(row(v.object_alias_resolution).items)[0]).checks)[0]).observation_id = 'O99'
    },
  ],
  [
    'missing preservation association check',
    (v) => {
      delete row(rows(row(v.preservation).item_checks)[0]).object_alias_check
    },
  ],
  [
    'failed preservation promoted',
    (v) => {
      row(rows(row(v.preservation).item_checks)[0]).object_alias_check = 'fail'
    },
  ],
  [
    'unrequired preservation marker',
    (v) => {
      row(rows(row(v.preservation).item_checks)[0]).object_alias_check = 'not_needed'
    },
  ],
  [
    'changed original object',
    (v) => {
      row(rows(row(v.corrected_contract).items)[0]).transfer_object = '另一个物品'
    },
  ],
  [
    'changed historic note',
    (v) => {
      row(v.corrected_contract).note = '删除旧解释'
    },
  ],
]
test.each(invalid)('v2 rejects %s instead of inferring source ready', (_name, mutate) => {
  const value = objectReceipt() as unknown as Record<string, unknown>
  mutate(value)
  expect(readScopeReceipt(value)).toBeUndefined()
})
test('alias unknown remains a source stop with null preservation', () => {
  const value = objectReceipt() as unknown as Record<string, unknown>
  value.state = 'unverified'
  value.preservation = null
  const resolution = row(value.object_alias_resolution)
  resolution.state = 'unverified'
  const item = row(rows(resolution.items)[0])
  item.status = 'unknown'
  item.observation_id = ''
  const check = row(rows(item.checks)[0])
  check.relation = 'unknown'
  check.evidence_source = null
  const proposal = row(rows(row(value.proposal).items)[0])
  proposal.status = 'unknown'
  proposal.observation_ids = []
  expect(readScopeReceipt(value)?.state).toBe('unverified')
  item.status = 'different'
  expect(readScopeReceipt(value)).toBeUndefined()
  check.relation = 'different_transfer_or_instance'
  check.evidence_source = objectReceipt().object_alias_resolution.items[0].checks[0].evidence_source
  expect(readScopeReceipt(value)?.state).toBe('unverified')
  item.status = 'unknown'
  expect(readScopeReceipt(value)).toBeUndefined()
})
test('direct naming needs no extra association but still requires v2 preservation markers', () => {
  const value = objectReceipt()
  value.inventory.inventory.observations[0].transfer_object =
    value.snapshot.prepared_contract.items[0].transfer_object
  value.object_alias_resolution = {
    ...value.object_alias_resolution,
    state: 'not_needed',
    required_item_ids: [],
    items: [],
  }
  value.preservation.item_checks[0].object_alias_check = 'not_needed'
  expect(readScopeReceipt(value)?.state).toBe('ready')
})
test.each(['unknown', 'fail'])(
  'a final independent alias %s remains readable and unverified despite old checks passing',
  (verdict) => {
    const value = objectReceipt()
    value.state = 'unverified'
    value.preservation.item_checks[0].object_alias_check = verdict
    const receipt = readScopeReceipt(value)
    expect(receipt?.state).toBe('unverified')
    expect(readScopeBodyPreview(bodyCapsule().preview, owner, f.receipt.id, receipt!)).toBeUndefined()
  },
)
test('source/body versions cannot mix old receipts or downgrade to v1', () => {
  const receipt = readScopeReceipt(objectReceipt())!
  const preview = bodyCapsule().preview
  expect(readScopeBodyPreview(preview, owner, f.receipt.id, receipt)?.version).toBe(SCOPE_OBJECT_BODY_VERSION)
  expect(readScopeBodyPreview(f.bodyPreview, owner, f.receipt.id, receipt)).toBeUndefined()
  expect(
    readScopeBodyPreview(preview, owner, f.receipt.id, old.readScopeReceipt(f.receipt.payload)!),
  ).toBeUndefined()
  expect(readScopeCapsule(sourceCapsule(), ownerKey(owner))).toBeTruthy()
  expect(readScopeCapsule(bodyCapsule(), ownerKey(owner))).toBeTruthy()
  const missing = sourceCapsule()
  delete missing.request.verify_object_aliases
  expect(readScopeCapsule(missing, ownerKey(owner))).toBeUndefined()
  const mixed = bodyCapsule()
  mixed.request.verify_object_aliases = true
  expect(readScopeCapsule(mixed, ownerKey(owner))).toBeUndefined()
})
test('new tasks bind explicit flag and frozen new source/body versions', () => {
  const source = sourceTask(),
    body = bodyTask()
  expect(readScopeTask(source, sourceCapsule())).toBeTruthy()
  expect(readScopeTask(body, bodyCapsule())).toBeTruthy()
  source.result.prepared_source_scope_snapshot.verify_object_aliases = false
  expect(readScopeTask(source, sourceCapsule())).toBeUndefined()
  body.result.source_corrected_recheck_version = old.SCOPE_BODY_VERSION
  expect(readScopeTask(body, bodyCapsule())).toBeUndefined()
})
test('body report preserves v2 metadata and still requires FLOW2', () => {
  const value = clone(f.report.payload) as unknown as Record<string, unknown>
  value.version = SCOPE_OBJECT_BODY_VERSION
  row(value.plot_audit).source_scope_correction = {
    version: SCOPE_OBJECT_VERSION,
    object_alias_resolution: objectReceipt().object_alias_resolution,
  }
  row(value.review).plot_audit = clone(value.plot_audit)
  expect(readScopeReport(value)?.version).toBe(SCOPE_OBJECT_BODY_VERSION)
  row(row(value.plot_audit).source_scope_correction).version = old.SCOPE_VERSION
  expect(readScopeReport(value)).toBeUndefined()
})
function storage() {
  const values = new Map<string, string>()
  return {
    values,
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => void values.set(key, value),
    removeItem: (key: string) => void values.delete(key),
  }
}
test('new pending request only queries the same opaque nonce through 404/null after refresh', async () => {
  const capsule = sourceCapsule(),
    task = sourceTask(),
    saved = storage(),
    calls: { path: string; method?: string }[] = []
  const nonce = 'aliases/材料%2F\n?#😀'
  capsule.request.client_request_id = nonce
  task.payload.client_request_id = nonce
  let lookup = 0
  const call = async (path: string, method?: string) => {
    calls.push({ path, method })
    if (method === 'POST') throw Error('response lost')
    lookup++
    expect(new URL(path, 'http://test').searchParams.get('client_request_id')).toBe(nonce)
    if (lookup === 1) throw new ApiError('not_found', '暂未找到', 404)
    return {
      source_candidate_id: owner.artifactId,
      client_request_id: nonce,
      task: lookup === 2 ? null : task,
    }
  }
  const first = createScopeOperation(ownerKey(owner), call, saved, () => {})
  await first.start(capsule)
  await first.start(capsule)
  first.dispose()
  const restored = createScopeOperation(ownerKey(owner), call, saved, () => {})
  expect(restored.state.pending?.request.verify_object_aliases).toBe(true)
  expect(restored.releaseKnown()).toBe(false)
  await restored.lookup()
  expect(restored.state.status).toBe('uncertain')
  await restored.lookup()
  expect(restored.state.status).toBe('uncertain')
  await restored.lookup()
  expect(restored.state.task?.id).toBe(task.id)
  expect(calls.filter((call) => call.method === 'POST')).toHaveLength(1)
})
test('old pending request is restored unchanged without adding the new flag', async () => {
  const capsule = sourceCapsule()
  capsule.preview = readScopePreview(f.preview, owner)!
  delete capsule.request.verify_object_aliases
  const saved = storage()
  saved.setItem('ainovel.prepared-scope-correction.1:' + ownerKey(owner), JSON.stringify(capsule))
  const operation = createScopeOperation(
    ownerKey(owner),
    async (_path, method) => {
      expect(method).toBe('GET')
      return {
        source_candidate_id: owner.artifactId,
        client_request_id: capsule.request.client_request_id,
        task: f.sourceQueued,
      }
    },
    saved,
    () => {},
  )
  expect(operation.state.pending).toEqual(capsule)
  await operation.lookup()
  expect(operation.state.status).toBe('confirmed')
  expect(operation.state.pending?.request).not.toHaveProperty('verify_object_aliases')
})
