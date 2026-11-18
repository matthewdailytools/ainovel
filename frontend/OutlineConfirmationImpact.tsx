import { RestoreReportList, type RestoreAffectedReport } from './ChapterRestorePreview'

const chapterFields = {
  placement: '卷、单元或章序',
  goal: '目标',
  obstacle: '阻碍',
  turn: '转折',
  result: '结果',
  settings: '引用设定',
  reference_cards: '手法卡',
}
const outlineFields = {
  summary: '总纲',
  organization: '卷纲或单元纲',
  settings: '采用设定版本',
  promises: '伏笔窗口',
}
const actions = { added: '新增', removed: '移除', changed: '修改' }

export type OutlineConfirmationImpact = {
  draft_id: string
  base_revision: number
  confirmed_id: string | null
  confirmed_revision: number | null
  basis_sha256: string
  outline_fields: (keyof typeof outlineFields)[]
  chapter_changes: {
    chapter_id: string
    title: string
    action: keyof typeof actions
    fields: (keyof typeof chapterFields)[]
  }[]
  frozen_candidates: {
    id: string
    kind: 'chapter_draft' | 'text_patch'
    chapter_id: string
    title: string
  }[]
  affected_reports: RestoreAffectedReport[]
}

export function OutlineImpactDetails({
  impact,
  scopeNames,
}: {
  impact: OutlineConfirmationImpact
  scopeNames: ReadonlyMap<string, string>
}) {
  return (
    <>
      <p>
        {impact.confirmed_revision == null
          ? '首次确认大纲。'
          : `当前确认版为修订 ${impact.confirmed_revision}。`}{' '}
        待确认草稿为修订 {impact.base_revision}。
      </p>
      {!!impact.outline_fields.length && (
        <p>大纲范围变化 · {impact.outline_fields.map((field) => outlineFields[field]).join('、')}</p>
      )}
      <section aria-label="章纲内容直接变化">
        <h4>章纲内容直接变化</h4>
        {impact.chapter_changes.length ? (
          <ul>
            {impact.chapter_changes.map((chapter) => (
              <li key={chapter.chapter_id}>
                {chapter.title} · {actions[chapter.action]}
                {chapter.fields.length > 0 &&
                  ` · ${chapter.fields.map((field) => chapterFields[field]).join('、')}`}
              </li>
            ))}
          </ul>
        ) : (
          <p>本次没有章纲内容变化。</p>
        )}
      </section>
      <section aria-label="旧大纲候选">
        <h4>旧大纲候选</h4>
        {impact.frozen_candidates.length ? (
          <>
            <p>采用新大纲后，以下候选仍可查阅，采纳前需按新大纲重新生成。</p>
            <ul style={{ overflowWrap: 'anywhere' }}>
              {impact.frozen_candidates.map((candidate) => (
                <li key={candidate.id}>
                  {candidate.title} · {candidate.kind === 'chapter_draft' ? '正文候选' : '局部改写'} ·{' '}
                  <code>{candidate.id}</code>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p>当前没有受本次确认影响的待采纳候选。</p>
        )}
      </section>
      <RestoreReportList reports={impact.affected_reports} scopeNames={scopeNames} />
    </>
  )
}
