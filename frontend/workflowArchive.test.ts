import { createHash } from 'node:crypto'
import { expect, test } from 'vitest'
import { validateWorkflowHistory } from './workflowArchive'

const hash = (value: string) => createHash('sha256').update(value).digest('hex')

test('deterministic character conflict archive stays red and binds both original selections', async () => {
  const revisions = new Map(
    ['😀沈砚左手骨折。', '隔雾看去，沈砚左手完好。'].map((content, index) => {
      const id = `original-${index}`
      return [id, { id, chapter_id: `chapter-${index}`, revision: 2, sha256: hash(content), content }]
    }),
  )
  const chapters = [...revisions.values()].map(({ chapter_id, revision, sha256 }, index) => ({
    id: chapter_id,
    title: `第${index + 1}章`,
    position: index + 1,
    revision,
    sha256,
    chars: 12,
    stale: false,
  }))
  const chapter_bindings = [...revisions.values()].map(({ chapter_id, revision, sha256, id }) => ({
    id: chapter_id,
    revision,
    revision_id: id,
    sha256,
  }))
  const group = {
    character_id: 'shen',
    category: 'injury',
    key: '左手伤势',
    audience: 'author',
  }
  const sides = [...revisions.values()].map(({ chapter_id, revision, content }, index) => ({
    record_id: `state-${index}`,
    revision: 0,
    title: '左手',
    value: index ? '完好' : '骨折',
    event_id: 'crossing',
    sources: [{ chapter_id, revision, start: 0, end: content.length, quote: content }],
  }))
  const records = sides.map(({ record_id, ...side }) => ({
    id: record_id,
    ...group,
    ...side,
    kind: 'state',
    truth: 'actual',
    invalid_reasons: [],
  }))
  const items = Array.from({ length: 10 }, (_, index) => ({
    check_id: String(index + 1).padStart(2, '0'),
    title: '核对正文',
    summary: '',
    status: index === 2 ? 'red' : 'pending',
    can_mark_not_applicable: false,
    basis_sha256: hash(String(index)),
    basis:
      index === 2
        ? { records, state_conflicts: [{ ...group, story_order: null, event_id: 'crossing', sides }] }
        : {},
  }))
  const report = {
    version: 'm6-inspection/1',
    scope: 'book',
    scope_id: null,
    chapters,
    items,
    basis_sha256: hash(JSON.stringify(['book', null, items.map((item) => item.basis_sha256)])),
  }
  const history = {
    inspections: [{ id: 'inspection', created: 1, chapter_bindings, report }],
    completions: [],
  }
  const read = () =>
    validateWorkflowHistory(history, 'work', revisions, async (value) => hash(value), new Map())
  expect((await read()).inspections[0].report.items[2].status).toBe('red')

  items[2].status = 'green'
  await expect(read()).rejects.toThrow('须保留红项')
  items[2].status = 'red'
  sides[0].sources[0].start = 1
  await expect(read()).rejects.toThrow('第 03 项冲突引文与原稿选区不一致')
})
