import { describe, expect, it } from 'vitest'
import type { Task } from './api'
import {
  canPreviewRestart,
  OPEN_DETAILS_VERSION,
  readPendingRestart,
  readRestartChild,
  readRestartPreview,
  readSourceRolesCreation,
  RESTART_VERSION,
  SOURCE_ROLES_CREATION_VERSION,
  SOURCE_ROLES_VERSION,
  sourceRolesAnchorsMatch,
  canonical,
  type PendingRestart,
  type RestartPreview,
} from './planRestart'

function fixture() {
  const creation = {
    version: SOURCE_ROLES_CREATION_VERSION,
    task_id: 'fresh-origin',
    novel_id: 'novel',
    branch_id: 'branch',
    guidance_version: SOURCE_ROLES_VERSION,
    source_payload_sha256: 'c'.repeat(64),
  }
  const source: Task = {
    id: creation.task_id,
    novel_id: creation.novel_id,
    branch_id: creation.branch_id,
    kind: 'workflow',
    status: 'blocked_runtime',
    created: 1,
    cancel: 0,
    error: '',
    payload: {
      mode: 'automatic',
      account: 'deepseek',
      plot_contract_check: true,
      budget_limit: 1,
      workflow_plan_source_roles_version: SOURCE_ROLES_VERSION,
      workflow_plan_source_roles_creation: structuredClone(creation),
    },
    result: {
      next_chapter: 5,
      expected_story_revision: 12,
      workflow_plan_preparation_version: 'workflow-plan-preparation/1',
      workflow_plan_open_details_version: OPEN_DETAILS_VERSION,
      plot_contract_version: 'author-plot-audit/2',
      workflow_plan_source_roles_version: SOURCE_ROLES_VERSION,
      workflow_plan_source_roles_creation: structuredClone(creation),
      plan: { chapters: [{ number: 5, plot: '许可仅带副本；原件留铺。' }] },
      plot_preparations: {
        '5': {
          chapter_id: 'chapter',
          original_plot: '许可仅带副本；原件留铺。',
          original_plot_sha256: 'a'.repeat(64),
          workflow_plan_source_roles_version: SOURCE_ROLES_VERSION,
          status: 'interpreting',
          round: 0,
          phases: { 'interpret:0': {} },
        },
      },
    },
  }
  const preview: RestartPreview = {
    version: RESTART_VERSION,
    workflow_plan_open_details_version: OPEN_DETAILS_VERSION,
    workflow_plan_source_roles_version: SOURCE_ROLES_VERSION,
    task_id: source.id,
    branch_id: source.branch_id,
    chapter_id: 'chapter',
    request_basis: {
      chapter: 5,
      base_story_revision: 12,
      base_progress_sha256: 'b'.repeat(64),
      workflow_plan_source_roles_version: SOURCE_ROLES_VERSION,
    },
    original_plot: '许可仅带副本；原件留铺。',
    original_plot_sha256: 'a'.repeat(64),
    old_preparation_sha256: 'd'.repeat(64),
    known_invalid_steps: {
      call_ids: ['one', 'two', 'three'],
      fingerprints: ['1'.repeat(64), '2'.repeat(64), '3'.repeat(64)],
    },
    budget: {
      provider: 'deepseek',
      source_run_limit: 1,
      source_task_settled: 0.1,
      remaining_run_limit: 0.9,
      unknown_reserve: 0,
    },
    policy: '原政策保留',
  }
  const request = {
    ...preview.request_basis,
    reason: '保留原来源、规划与费用',
    client_request_id: 'explicit-fresh-new-generation',
  }
  const pending: PendingRestart = { source, preview, request }
  const child: Task = {
    ...structuredClone(source),
    id: 'new-child',
    status: 'queued',
    payload: {
      ...structuredClone(source.payload),
      client_request_id: request.client_request_id,
      budget_limit: 0.9,
    } as Task['payload'],
    result: {
      ...structuredClone(source.result),
      continued_from: source.id,
      plot_preparations: {},
      plan_preparation_restart: {
        version: RESTART_VERSION,
        workflow_plan_source_roles_version: SOURCE_ROLES_VERSION,
        source_task_id: source.id,
        request,
        previous_preparation_sha256: preview.old_preparation_sha256,
        known_invalid_steps: preview.known_invalid_steps,
        budget: preview.budget,
      },
    },
  }
  return { source, preview, pending, child, creation }
}

describe('fresh creation 来源与显式重核代际分开核对', () => {
  it('原始fresh无需restart回执，已知creation允许读取正式preview并保存原nonce', () => {
    const { source, preview, pending, creation } = fixture()
    expect(readSourceRolesCreation(source)).toEqual(creation)
    expect(sourceRolesAnchorsMatch(source)).toBe(true)
    expect(canPreviewRestart(source)).toBe(true)
    expect(readRestartPreview(preview, source)).toEqual(preview)
    expect(readPendingRestart(pending, canonical([source.novel_id, source.branch_id, source.id]))).toEqual(
      pending,
    )
  })
  it('本次child同时保留原creation与current restart，不能把origin换成新task', () => {
    const { child, pending } = fixture()
    expect(readRestartChild(child, pending)).toEqual(child)
    child.payload.workflow_plan_source_roles_creation = {
      ...(child.payload.workflow_plan_source_roles_creation as Record<string, unknown>),
      task_id: child.id,
    }
    child.result.workflow_plan_source_roles_creation = child.payload.workflow_plan_source_roles_creation
    expect(readRestartChild(child, pending)).toBeUndefined()
  })
  it('合法continuation可继承历史restart回执，正式preview仍负责验证整条来源链', () => {
    const { source, preview, creation } = fixture()
    source.id = 'continued-source'
    source.result.continued_from = 'immediate-parent'
    source.result.plan_preparation_restart = {
      version: RESTART_VERSION,
      workflow_plan_source_roles_version: SOURCE_ROLES_VERSION,
      source_task_id: 'historical-restart-parent',
      request: { workflow_plan_source_roles_version: SOURCE_ROLES_VERSION },
    }
    preview.task_id = source.id
    expect(readSourceRolesCreation(source)).toEqual(creation)
    expect(sourceRolesAnchorsMatch(source)).toBe(true)
    expect(readRestartPreview(preview, source)).toEqual(preview)
  })
  it('没有historical receipt的明确continued_from来源也可送正式preview', () => {
    const { source } = fixture()
    source.id = 'continued-source'
    source.result.continued_from = 'fresh-origin'
    expect(sourceRolesAnchorsMatch(source)).toBe(true)
    expect(canPreviewRestart(source)).toBe(true)
  })
  for (const damage of [
    'one-sided',
    'origin',
    'branch',
    'version',
    'guidance',
    'hash',
    'extra',
    'null',
  ] as const) {
    it('拒绝混合或不明creation：' + damage, () => {
      const { source } = fixture()
      const raw = source.payload.workflow_plan_source_roles_creation as Record<string, unknown>
      if (damage === 'one-sided') delete source.result.workflow_plan_source_roles_creation
      else if (damage === 'origin')
        source.result.workflow_plan_source_roles_creation = { ...raw, task_id: 'another-origin' }
      else if (damage === 'null')
        source.payload.workflow_plan_source_roles_creation =
          source.result.workflow_plan_source_roles_creation = null
      else {
        if (damage === 'branch') raw.branch_id = 'another-branch'
        if (damage === 'version') raw.version = 'future'
        if (damage === 'guidance') raw.guidance_version = 'future'
        if (damage === 'hash') raw.source_payload_sha256 = 'unknown'
        if (damage === 'extra') raw.account = 'other-account'
        source.result.workflow_plan_source_roles_creation = structuredClone(raw)
      }
      expect(readSourceRolesCreation(source)).toBeUndefined()
      expect(sourceRolesAnchorsMatch(source)).toBe(false)
      expect(canPreviewRestart(source)).toBe(false)
    })
  }
  for (const damage of [
    'no-parent',
    'self-parent',
    'fresh-has-receipt',
    'receipt-role',
    'receipt-null',
    'receipt-self-source',
  ] as const) {
    it('拒绝不自洽的fresh或继承代际：' + damage, () => {
      const { source } = fixture()
      if (damage === 'fresh-has-receipt')
        source.result.plan_preparation_restart = { version: RESTART_VERSION }
      else {
        source.id = 'continued-source'
        if (damage !== 'no-parent')
          source.result.continued_from = damage === 'self-parent' ? source.id : 'parent'
        if (damage === 'receipt-role')
          source.result.plan_preparation_restart = {
            version: RESTART_VERSION,
            source_task_id: 'parent',
            workflow_plan_source_roles_version: SOURCE_ROLES_VERSION,
            request: {},
          }
        if (damage === 'receipt-null') source.result.plan_preparation_restart = null
        if (damage === 'receipt-self-source')
          source.result.plan_preparation_restart = {
            version: RESTART_VERSION,
            source_task_id: source.id,
            workflow_plan_source_roles_version: SOURCE_ROLES_VERSION,
            request: { workflow_plan_source_roles_version: SOURCE_ROLES_VERSION },
          }
      }
      expect(sourceRolesAnchorsMatch(source)).toBe(false)
      expect(canPreviewRestart(source)).toBe(false)
    })
  }
  it('creation不能在请求中冒充client配置，旧nonce仍是原精确字段集', () => {
    const { source, pending } = fixture()
    const capsule = structuredClone(pending) as unknown as { request: Record<string, unknown> }
    capsule.request.workflow_plan_source_roles_creation = source.payload.workflow_plan_source_roles_creation
    expect(
      readPendingRestart(capsule, canonical([source.novel_id, source.branch_id, source.id])),
    ).toBeUndefined()
  })
})
