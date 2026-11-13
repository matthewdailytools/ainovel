import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { expect, test } from 'vitest'
import type { Artifact, Task } from './api'
import {
  characterFieldRepairEnabled,
  characterRepairAccess,
  currentRepairTasks,
  readCharacterRepairBasis,
} from './characterFieldRepair'
import {
  CharacterFieldRepair,
  CharacterFieldRepairOption,
  FrozenCharacterFieldRepairPolicy,
} from './CharacterRepairDetails'

const basis = {
  version: 'character-field-repair/1',
  root_artifact_id: 'root',
  parent_artifact_id: 'root',
  round: 1,
  input_sha256: 'a'.repeat(64),
  diff: [
    {
      record_id: 'person',
      field: 'notes',
      before: '断言已经确认',
      after: '仍待核对',
      source_ids: ['CHR0001'],
      reason: '保留来源中的未知。',
    },
  ],
}
function chain(current = 'child') {
  return {
    root: {
      version: 'character-field-repair/1',
      root_artifact_id: 'root',
      max_rounds: 2,
      current_artifact_id: current,
      rounds: [
        {
          round: 1,
          parent_artifact_id: 'root',
          ...(current === 'root' ? {} : { child_artifact_id: 'child' }),
          status: current === 'root' ? 'stopped' : 'candidate_created',
        },
      ],
    },
  }
}
function task(id = 'task', journal: unknown = chain()): Task {
  return {
    id,
    novel_id: 'novel',
    branch_id: 'branch',
    created: 1,
    kind: 'workflow',
    status: 'needs_review',
    cancel: 0,
    error: '',
    result: { character_field_repairs: journal },
    payload: {
      account: 'test',
      adoption: 'automatic',
      structured_maintenance: true,
      character_field_repair: true,
    },
  }
}
function artifact(id = 'child'): Pick<Artifact, 'id' | 'status' | 'basis'> {
  return { id, status: 'candidate', basis: id === 'root' ? {} : { character_field_repair: basis } }
}

test('有限修订必须明确勾选、自动采纳且维护结构；旧值及字符串不构成授权', () => {
  for (const value of [undefined, false, 'true', 1])
    expect(characterFieldRepairEnabled(value, 'automatic', true)).toBe(false)
  expect(characterFieldRepairEnabled(true, 'manual', true)).toBe(false)
  expect(characterFieldRepairEnabled(true, 'automatic', false)).toBe(false)
  expect(characterFieldRepairEnabled(true, 'automatic', true)).toBe(true)
  for (const [adoption, maintenance] of [
    ['manual', true],
    ['automatic', false],
  ] as const) {
    const html = renderToStaticMarkup(
      createElement(CharacterFieldRepairOption, {
        checked: true,
        adoption,
        maintenance,
        busy: false,
        onChange: () => undefined,
      }),
    )
    expect(html).toContain('disabled=""')
    expect(html).not.toContain('checked=""')
    expect(html).toContain('最多修订2轮')
    expect(html).toContain('增加调用费用')
  }
  const html = renderToStaticMarkup(
    createElement(CharacterFieldRepairOption, {
      checked: false,
      adoption: 'automatic',
      maintenance: true,
      busy: false,
      onChange: () => undefined,
    }),
  )
  expect(html).not.toContain('checked=""')
  expect(html).not.toContain('disabled=""')
})

test('冻结政策只读取任务值，缺失按关闭，不受当前表单影响', () => {
  for (const enabled of [undefined, false, 'true']) {
    const html = renderToStaticMarkup(createElement(FrozenCharacterFieldRepairPolicy, { enabled }))
    expect(html).toContain('未启用')
    expect(html).not.toContain('已启用')
  }
  expect(renderToStaticMarkup(createElement(FrozenCharacterFieldRepairPolicy, { enabled: true }))).toContain(
    '已启用，最多2轮',
  )
})

test('只读历史父候选，当前未采用子候选保持人工核对入口', () => {
  expect(characterRepairAccess(artifact('root'), 'branch', [task()], true).reason).toContain('历史父候选')
  expect(characterRepairAccess(artifact(), 'branch', [task()], true).readOnly).toBe(false)
  expect(characterRepairAccess(artifact(), 'branch', [task()], false).readOnly).toBe(true)
  expect(characterRepairAccess(artifact('root'), 'branch', [task('manual', {})], true).readOnly).toBe(false)
})

test('两轮链按顺序定位：原稿与中间父稿只读，第二轮当前候选可核对', () => {
  const owner = task('two-rounds', {
    root: {
      version: 'character-field-repair/1',
      root_artifact_id: 'root',
      max_rounds: 2,
      current_artifact_id: 'child2',
      rounds: [
        { round: 1, parent_artifact_id: 'root', child_artifact_id: 'child' },
        { round: 2, parent_artifact_id: 'child', child_artifact_id: 'child2' },
      ],
    },
  })
  expect(characterRepairAccess(artifact('root'), 'branch', [owner], true).readOnly).toBe(true)
  expect(characterRepairAccess(artifact(), 'branch', [owner], true).readOnly).toBe(true)
  const current = {
    ...artifact('child2'),
    basis: {
      character_field_repair: {
        ...basis,
        round: 2,
        parent_artifact_id: 'child',
      },
    },
  }
  expect(characterRepairAccess(current, 'branch', [owner], true)).toMatchObject({
    readOnly: false,
    rootId: 'root',
    parentId: 'child',
    currentId: 'child2',
  })
})

test('cannot_repair无子候选仍为当前父候选，不因存在修订记录而封锁作者采用', () => {
  expect(
    characterRepairAccess(artifact('root'), 'branch', [task('stopped', chain('root'))], true).readOnly,
  ).toBe(false)
})

test('子候选采用后整条链只读，可由回执或产物状态确认', () => {
  const completed = task()
  completed.result.structure_history = [{ artifact_id: 'child', policy: 'author_confirmed', created: 1 }]
  for (const selected of [artifact('root'), artifact()]) {
    expect(characterRepairAccess(selected, 'branch', [completed], true).readOnly).toBe(true)
    expect(characterRepairAccess(selected, 'branch', [completed], true).reason).toContain('整条候选链只读')
  }
  expect(
    characterRepairAccess({ ...artifact(), status: 'accepted' }, 'branch', [task()], true).readOnly,
  ).toBe(true)
})

test('当前非continued任务决定候选链，旧任务副本不造成错误解锁或多归属', () => {
  const old = task('old', chain('root'))
  old.status = 'continued'
  expect(characterRepairAccess(artifact('root'), 'branch', [old, task('new')], true).readOnly).toBe(true)
  expect(characterRepairAccess(artifact(), 'branch', [old, task('new')], true).readOnly).toBe(false)
  expect(characterRepairAccess(artifact(), 'branch', [task('first'), task('second')], true).readOnly).toBe(
    true,
  )
  const foreign = task()
  foreign.branch_id = 'other'
  expect(characterRepairAccess(artifact(), 'branch', [foreign], true).readOnly).toBe(true)
})

test('最近任务列表漏创建者时沿continued_to回到当前工作流，不仅依赖创建task', async () => {
  const old = task('old', chain('root'))
  old.status = 'continued'
  old.result.continued_to = 'new'
  const current = task('new')
  current.result.continued_from = 'old'
  const fetched: string[] = []
  const rows = await currentRepairTasks({ task_id: 'old' }, 'branch', [current], async (id) => {
    fetched.push(id)
    return old
  })
  expect(fetched).toEqual(['old'])
  expect(characterRepairAccess(artifact('root'), 'branch', rows, true).readOnly).toBe(true)
})

test('续跑跨分支、循环或缺少回链时拒绝确认，不对任意task放行', async () => {
  const old = task('old')
  old.status = 'continued'
  old.result.continued_to = 'new'
  const current = task('new')
  await expect(
    currentRepairTasks({ task_id: 'old' }, 'branch', [old, current], async () => current),
  ).rejects.toThrow('续跑任务来源无法核对')
  current.result.continued_from = 'old'
  current.result.continued_to = 'old'
  await expect(
    currentRepairTasks({ task_id: 'old' }, 'branch', [old, current], async () => current),
  ).rejects.toThrow('批次链无法确认')
  current.branch_id = 'foreign'
  await expect(currentRepairTasks({ task_id: 'new' }, 'branch', [], async () => current)).rejects.toThrow(
    '不属于当前工作版本',
  )
})

test.each([
  { ...basis, version: 'unknown' },
  { ...basis, round: 3 },
  { ...basis, input_sha256: 'wrong' },
  { ...basis, diff: [] },
  { ...basis, diff: [basis.diff[0], basis.diff[0]] },
  { ...basis, diff: [{ ...basis.diff[0], field: 'title' }] },
  { ...basis, diff: [{ ...basis.diff[0], source_ids: [] }] },
])('异常修订元数据不解锁历史操作 %s', (value) => {
  expect(readCharacterRepairBasis(value)).toBeUndefined()
  expect(
    characterRepairAccess(
      { ...artifact(), basis: { character_field_repair: value } },
      'branch',
      [task()],
      true,
    ).readOnly,
  ).toBe(true)
})

test('字段差异、理由和来源编号安全展示，保留原候选链及中文换行', () => {
  const unsafe = '<script>测试😀𠀀</script>\n长中文差异'
  const value = {
    ...basis,
    diff: [{ ...basis.diff[0], before: unsafe, after: `${unsafe}修订`, reason: unsafe }],
  }
  const html = renderToStaticMarkup(
    createElement(CharacterFieldRepair, {
      value,
      access: characterRepairAccess(artifact(), 'branch', [task()], true),
      currentId: 'child',
      busy: false,
      nameFor: () => '人物甲',
      onSelect: () => undefined,
    }),
  )
  for (const text of [
    '人物甲 · 说明',
    '修订前候选',
    '修订后候选',
    'CHR0001',
    '查看原候选',
    '查看上一轮候选',
    '查看当前修订候选',
    '第1轮／最多2轮',
  ])
    expect(html).toContain(text)
  expect(html).toContain('&lt;script&gt;测试😀𠀀&lt;/script&gt;')
  expect(html).not.toContain('<script>')
})
