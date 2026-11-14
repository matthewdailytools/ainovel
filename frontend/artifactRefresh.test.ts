import { expect, test } from 'vitest'
import type { Artifact } from './api'
import { latestArtifact, refreshArtifactList } from './artifactRefresh'

const base: Artifact = {
  id: 'draft',
  branch_id: 'branch',
  task_id: 'task',
  kind: 'chapter_draft',
  chapter_id: 'chapter',
  base_revision: 0,
  candidate_revision: 2,
  status: 'candidate',
  payload: { text: '已保存的修订 2 😀𠮷', review_passed: false },
  basis: {},
}

test('a late list cannot roll back the saved candidate text or checks', () => {
  const old = { ...base, candidate_revision: 1, payload: { text: '旧修订', review_passed: true } }
  expect(refreshArtifactList([base], [old])).toEqual([base])
  expect(old.payload.text).toBe('旧修订')
})

test('newer saved text is loaded exactly, including its spacing', () => {
  const next = { ...base, candidate_revision: 3, payload: { text: '新修订 \n保留空格。' } }
  expect(latestArtifact(base, next)).toBe(next)
  expect(base.payload.text).toBe('已保存的修订 2 😀𠮷')
})

test.each(['accepted', 'rejected'] as const)('a stale candidate response cannot reopen %s', (status) => {
  const terminal = { ...base, status }
  expect(latestArtifact(terminal, base)).toBe(terminal)
})

test('fresh checks at the same text revision remain readable', () => {
  const checked = { ...base, payload: { ...base.payload, review_passed: true } }
  expect(latestArtifact(base, checked)).toBe(checked)
})

test('list filtering and ordering remain those returned by the server', () => {
  const other = { ...base, id: 'other' }
  expect(refreshArtifactList([base, other], [other])).toEqual([other])
})

test('different artifact identities do not share cached content', () => {
  const other = { ...base, id: 'other', candidate_revision: 0 }
  expect(latestArtifact(base, other)).toBe(other)
})
