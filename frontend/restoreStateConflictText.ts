import type { RestoreStateVersion } from './restoreStateSyncModel'

type FactSet = Extract<RestoreStateVersion, { kind: 'fact-set-version' }>['version']
type LengthTarget = Extract<RestoreStateVersion, { kind: 'length-target-version' }>['version']

const truthNames = { fact: '事实', belief: '人物认知', uncertain: '待核对', plan: '计划' }
const scopeNames = { chapter: '章', volume: '卷', book: '全书' }

export function describeFactSet(version: FactSet) {
  return [
    `章节 ${version.chapter_id} · 章稿修订 ${version.chapter_revision_id}`,
    version.facts.length ? `正式事实 ${version.facts.length} 项` : '本修订没有有效事实',
    ...version.facts.map(({ subject, statement, truth, story_time, evidence }) =>
      [
        `${subject} · ${truthNames[truth]} · ${statement}`,
        [story_time, `原文选区 UTF-16 ${evidence.start}–${evidence.end}`].filter(Boolean).join(' · '),
      ].join('\n'),
    ),
  ].join('\n')
}

export function describeLengthTarget(version: LengthTarget) {
  const { targets } = version
  return [
    [scopeNames[version.scope], version.scope_id].filter(Boolean).join(' · '),
    ...(version.outline_version_id ? [`采用大纲 ${version.outline_version_id}`] : []),
    ...(targets === null
      ? ['已清空显式篇幅目标']
      : [
          ['最少有效字符', targets.min_chars],
          ['最多有效字符', targets.max_chars],
          ['目标章数', targets.expected_chapters],
        ].flatMap(([label, value]) => (value === null ? [] : [`${label} ${value}`]))),
  ].join('\n')
}
