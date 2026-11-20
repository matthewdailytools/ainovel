import { afterEach, expect, test, vi } from 'vitest'
import {
  canApplyAssistantSuggestion,
  readAssistantHistory,
  recordAssistantDraftApplication,
  type AssistantLocation,
  type AssistantTurn,
} from './workbenchAssistantModel'

afterEach(() => vi.restoreAllMocks())

test('draft adoption is confined to the exact field, target, revision evidence and submitted value', () => {
  const suggestion = { field: 'title', value: '改过的卷名', reason: '标出本卷冲突' }
  const turn: AssistantTurn = {
    id: 'turn-one',
    sequence: 1,
    question: '润色标题',
    task_id: 'task-one',
    status: 'succeeded',
    result: { answer: '建议保留现有地名。', suggestions: [suggestion], citations: [] },
    action: 'refine',
    target: 'title',
    target_key: 'volume-one',
    context_key: 'revision-two',
    draft_text: '旧城',
    basis_sha256: 'when-sent',
    basis_current: true,
    applied_fields: [],
    created: 0,
  }
  const target = {
    field: 'title',
    label: '卷名',
    objectKey: 'volume-one',
    contextKey: 'revision-two',
    value: '旧城',
  }
  expect(canApplyAssistantSuggestion(turn, suggestion, target, 'when-sent')).toBe(true)
  expect(
    canApplyAssistantSuggestion(turn, suggestion, { ...target, objectKey: 'another-volume' }, 'when-sent'),
  ).toBe(false)
  expect(
    canApplyAssistantSuggestion(turn, suggestion, { ...target, contextKey: 'revision-three' }, 'when-sent'),
  ).toBe(false)
  expect(canApplyAssistantSuggestion(turn, suggestion, { ...target, value: '旧城 ' }, 'when-sent')).toBe(
    false,
  )
  expect(canApplyAssistantSuggestion(turn, suggestion, target, 'after-source-update')).toBe(false)
  expect(canApplyAssistantSuggestion(turn, suggestion, target, undefined)).toBe(false)
})

test('a removed scope still reads saved history and only its own field receipt counts as recorded', async () => {
  const scope: AssistantLocation = {
    branchId: 'work-one',
    stage: 'M4',
    scopeId: 'volume-one',
    draftKey: null,
  }
  const request = vi
    .spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(
      Response.json({
        conversation: {
          id: 'old-conversation',
          stage: 'M4',
          scope_id: 'volume-one',
          draft_key: null,
          subject: '旧卷',
          revision: 2,
          created: 1,
          updated: 3,
        },
        basis: null,
        turns: [],
        next_before: null,
      }),
    )
    .mockResolvedValueOnce(Response.json({ applied_fields: ['title'] }))
    .mockResolvedValueOnce(Response.json({ applied_fields: ['title'] }))
  await expect(readAssistantHistory(scope, 'old-conversation')).resolves.toMatchObject({ basis: null })
  await expect(
    recordAssistantDraftApplication(scope, 'old-conversation', 'old-turn', 'title'),
  ).resolves.toBeUndefined()
  await expect(
    recordAssistantDraftApplication(scope, 'old-conversation', 'old-turn', 'summary'),
  ).rejects.toThrow('无法核对')
  expect(request).toHaveBeenNthCalledWith(
    2,
    '/api/branches/work-one/assistant/conversations/old-conversation/turns/old-turn/apply-receipt',
    expect.objectContaining({ method: 'POST', body: JSON.stringify({ field: 'title' }) }),
  )
})
