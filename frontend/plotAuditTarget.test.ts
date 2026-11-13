import { createHash } from 'node:crypto'
import { expect, test, vi } from 'vitest'
import {
  comparisonPlotTarget,
  createPlotAuditTargetLoader,
  loadPlotAuditTarget,
  readComparisonTarget,
  validatePlotAuditTarget,
  type PlotAuditTarget,
  type PlotAuditTargetState,
} from './plotAuditTarget'
import { plotAuditState, readPlotAudit } from './plotAudit'

const text = '沈砚把旧尺还给老陈😀𠮷。'
const sha = (text: string) => createHash('sha256').update(text).digest('hex')
const audit = {
  version: 'author-plot-audit/1',
  source: { kind: 'author_plot', text_sha256: 'a'.repeat(64) },
  candidate_sha256: sha(text),
  candidate_revision: 0,
  status: 'model_pass',
  summary: '还尺方向一致',
  policy: '模型辅助核对',
  interpretation_note: '',
  items: [
    {
      id: 'P01',
      author_evidence: text,
      actor: '沈砚',
      action: '还',
      object: '尺',
      direction: '沈砚→老陈',
      result: '归还',
      certainty: 'explicit',
    },
  ],
  checks: [
    { item_id: 'P01', result: 'pass', observed: '沈砚还尺', evidence: text, reason: '方向一致', repair: '' },
  ],
  findings: [],
}
const saved: PlotAuditTarget = {
  kind: 'saved_chapter',
  chapterId: 'chapter-1',
  revision: 1,
  sha256: sha(text),
}
const candidate: PlotAuditTarget = {
  kind: 'candidate',
  candidateId: 'candidate-1',
  chapterId: 'chapter-1',
  revision: 1,
  sha256: sha(text),
}
const chapter = { id: 'chapter-1', branch_id: 'branch-a', revision: 1, content: text, archived: 0 }
const body = {
  id: 'candidate-1',
  branch_id: 'branch-a',
  chapter_id: 'chapter-1',
  kind: 'chapter_draft',
  status: 'candidate',
  candidate_revision: 1,
  payload: { text },
}

function state(value: unknown, target: PlotAuditTarget, current: PlotAuditTargetState) {
  return plotAuditState(readPlotAudit(value)!, {
    revision: current.candidateRevision,
    sha256: sha(current.savedChapter?.content ?? current.candidateText ?? ''),
    savedChapter: current.savedChapter && {
      ...current.savedChapter,
      sha256: sha(current.savedChapter.content),
    },
    expectSavedChapter: target.kind === 'saved_chapter',
    reportSavedChapter:
      target.kind === 'saved_chapter'
        ? { chapter_id: target.chapterId, revision: target.revision, content_sha256: target.sha256 }
        : undefined,
    targetStatus: current.status,
    expectedSha256: target.sha256,
  })
}

test('保存稿独立检查读取真正当前章，保存稿变化后旧报告历史化', async () => {
  const value = {
    ...audit,
    target: { kind: 'saved_chapter', chapter_id: 'chapter-1', revision: 1, content_sha256: sha(text) },
  }
  const report = {
    plot_audit: value,
    target: {
      kind: 'saved_manuscript',
      chapter_id: 'chapter-1',
      base_revision: 1,
      content_sha256: sha(text),
      candidate: null,
    },
  }
  expect(comparisonPlotTarget(report)).toEqual(saved)
  const reader = vi.fn().mockResolvedValue(chapter)
  const fresh = await loadPlotAuditTarget(saved, 'branch-a', reader)
  expect(reader).toHaveBeenCalledWith('/chapters/chapter-1')
  expect(state(value, saved, fresh)).toBe('model_pass')
  const changed = await loadPlotAuditTarget(saved, 'branch-a', async () => ({
    ...chapter,
    revision: 2,
    content: '作者后来改稿。',
  }))
  expect(state(value, saved, changed)).toBe('historical')
  const sameRevisionChangedText = await loadPlotAuditTarget(saved, 'branch-a', async () => ({
    ...chapter,
    content: '即使修订未递增也不能用报告hash冒充当前内容。',
  }))
  expect(state(value, saved, sameRevisionChangedText)).toBe('historical')
})

test('候选rev1检查按被检候选实际修订确认，不能用报告artifact的rev0，rev2后历史化', async () => {
  const value = { ...audit, candidate_revision: 1 }
  const report = {
    _candidate_revision: 0,
    plot_audit: value,
    target: {
      kind: 'candidate',
      chapter_id: 'chapter-1',
      base_revision: 1,
      content_sha256: sha(text),
      candidate: { id: 'candidate-1', revision: 1 },
    },
  }
  expect(comparisonPlotTarget(report)).toEqual(candidate)
  const reader = vi.fn().mockResolvedValue(body)
  const fresh = await loadPlotAuditTarget(candidate, 'branch-a', reader)
  expect(reader).toHaveBeenCalledWith('/artifacts/candidate-1')
  expect(fresh.candidateRevision).toBe(1)
  expect(state(value, candidate, fresh)).toBe('model_pass')
  const edited = await loadPlotAuditTarget(candidate, 'branch-a', async () => ({
    ...body,
    candidate_revision: 2,
  }))
  expect(state(value, candidate, edited)).toBe('historical')
})

test('v2范围限定报告继续绑定真实保存稿或被检候选，兼容既有target读取', async () => {
  const value = {
    ...audit,
    version: 'author-plot-audit/2',
    items: audit.items.map((item) => ({ ...item, recipient_scope: 'only', scope_condition: '' })),
  }
  const savedReport = {
    ...value,
    target: { kind: 'saved_chapter', chapter_id: 'chapter-1', revision: 1, content_sha256: sha(text) },
  }
  expect(comparisonPlotTarget({ plot_audit: savedReport })).toEqual(saved)
  expect(state(savedReport, saved, await loadPlotAuditTarget(saved, 'branch-a', async () => chapter))).toBe(
    'model_pass',
  )
  const candidateReport = { ...value, candidate_revision: 1 }
  expect(
    state(candidateReport, candidate, await loadPlotAuditTarget(candidate, 'branch-a', async () => body)),
  ).toBe('model_pass')
})

test('仅audit.target也可定位保存稿，冲突或缺失的对象元数据保守处理', () => {
  expect(
    comparisonPlotTarget({
      plot_audit: {
        ...audit,
        target: { kind: 'saved_chapter', chapter_id: 'chapter-1', revision: 1, content_sha256: sha(text) },
      },
    }),
  ).toEqual(saved)
  expect(comparisonPlotTarget({ plot_audit: audit })).toBeUndefined()
  expect(comparisonPlotTarget({ plot_audit: audit, target: { kind: 'candidate' } })).toBeUndefined()
  expect(
    comparisonPlotTarget({
      plot_audit: audit,
      target: {
        kind: 'candidate',
        chapter_id: 'chapter-1',
        base_revision: 1,
        content_sha256: 'b'.repeat(64),
        candidate: { id: 'candidate-1', revision: 0 },
      },
    }),
  ).toBeUndefined()
  expect(
    readComparisonTarget({
      kind: 'saved_manuscript',
      chapter_id: 'chapter-1',
      base_revision: 1,
      content_sha256: sha(text),
      candidate: { id: 'candidate-1', revision: 1 },
    }),
  ).toBeUndefined()
})

test('非法嵌套枚举对象不触发隐式字符串转换或渲染崩溃', () => {
  const malformed = {
    kind: { toString: null },
    chapter_id: 'chapter-1',
    base_revision: 1,
    content_sha256: sha(text),
  }
  expect(readComparisonTarget(malformed)).toBeUndefined()
  expect(comparisonPlotTarget({ plot_audit: audit, target: malformed })).toBeUndefined()
  expect(() => validatePlotAuditTarget(candidate, 'branch-a', { ...body, kind: { toString: null } })).toThrow(
    '被检候选的章节、修订或正文无法确认',
  )
})

test.each([
  { ...chapter, branch_id: 'other-branch' },
  { ...chapter, id: 'other-chapter' },
  { ...chapter, content: undefined },
])('保存稿返回对象必须符合分支和身份（%#）', async (reply) => {
  await expect(loadPlotAuditTarget(saved, 'branch-a', async () => reply)).rejects.toThrow()
})

test.each([
  { ...body, branch_id: 'other-branch' },
  { ...body, id: 'other-candidate' },
  { ...body, chapter_id: 'other-chapter' },
  { ...body, kind: 'reference_comparison' },
])('候选返回对象必须符合分支、身份与章节（%#）', async (reply) => {
  await expect(loadPlotAuditTarget(candidate, 'branch-a', async () => reply)).rejects.toThrow()
})

function deferred() {
  let resolve: (value: unknown) => void = () => undefined
  let reject: (error: Error) => void = () => undefined
  const promise = new Promise<unknown>((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

test.each(['success', 'failure'] as const)('切换后旧请求%s不覆盖当前被检对象', async (oldResult) => {
  const old = deferred(),
    fresh = deferred()
  const read = vi.fn().mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise)
  const publish = vi.fn()
  const loader = createPlotAuditTargetLoader(read, publish)
  const oldLoad = loader.load(saved, 'branch-a')
  const freshLoad = loader.load(candidate, 'branch-a')
  fresh.resolve(body)
  await freshLoad
  if (oldResult === 'success') old.resolve(chapter)
  else old.reject(new Error('旧对象读取失败'))
  await oldLoad
  expect(publish).toHaveBeenCalledTimes(1)
  expect(publish.mock.calls[0][0]).toMatchObject({
    status: 'ready',
    candidateRevision: 1,
    candidateText: text,
  })
})

test('读取失败清除有效结论，恢复可重读；卸载后的晚响应不发布', async () => {
  const pending = deferred()
  const read = vi
    .fn()
    .mockResolvedValueOnce(chapter)
    .mockRejectedValueOnce(new Error('读取失败'))
    .mockReturnValueOnce(pending.promise)
  const publish = vi.fn()
  const loader = createPlotAuditTargetLoader(read, publish)
  await loader.load(saved, 'branch-a')
  await loader.load(saved, 'branch-a')
  expect(publish.mock.calls[1][0]).toEqual({ status: 'unavailable', message: '读取失败' })
  expect(plotAuditState(readPlotAudit(audit)!, { targetStatus: 'unavailable' })).toBe('unverified')
  expect(plotAuditState(readPlotAudit(audit)!, { targetStatus: 'loading' })).toBe('pending')
  const last = loader.load(saved, 'branch-a')
  loader.invalidate()
  pending.resolve(chapter)
  await last
  expect(publish).toHaveBeenCalledTimes(2)
})

test('生成候选直接使用当前可靠对象及本地编辑内容，不能把报告对象当成候选', () => {
  const target = { ...candidate, kind: 'generated_candidate' as const }
  expect(validatePlotAuditTarget(target, 'branch-a', body).candidateRevision).toBe(1)
  const changed = validatePlotAuditTarget(target, 'branch-a', body, '尚未保存的作者编辑')
  expect(state({ ...audit, candidate_revision: 1 }, target, changed)).toBe('historical')
  expect(() =>
    validatePlotAuditTarget(target, 'branch-a', { ...body, kind: 'reference_comparison' }),
  ).toThrow()
})
