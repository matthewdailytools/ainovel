import type { Artifact } from './api'

export function SettingReviewReport({ report }: { report: Artifact }) {
  const rule = report.basis.rule as { title: string; detail: string; id: string; revision: number }
  const evidence = report.payload.evidence as { text: string; start: number; end: number } | null
  const ruleId = `setting-rule-${report.id}`
  const passageId = `setting-passage-${report.id}`
  const result = report.payload.result
  return (
    <section aria-label="设定对账结果">
      <h3>设定对账</h3>
      <p role="status">
        {report.basis.current === true ? '当前版本' : '已过期 · 历史依据仍可查'} ·{' '}
        {result === 'pass' ? '相符' : result === 'fail' ? '冲突' : '待核对'}
      </p>
      <p>{String(report.payload.reason)}</p>
      <p>
        <a href={`#${ruleId}`}>查看对应设定</a>
        {evidence && (
          <>
            {' '}
            · <a href={`#${passageId}`}>查看对应正文段落</a>
          </>
        )}
      </p>
      <section id={ruleId} tabIndex={-1} aria-label="设定依据">
        <h4>
          {rule.title} · 设定修订 {rule.revision}
        </h4>
        <p>{rule.detail}</p>
      </section>
      {evidence && (
        <section id={passageId} tabIndex={-1} aria-label="正文依据">
          <h4>
            章稿修订 {report.base_revision} · 位置 {evidence.start}–{evidence.end}
          </h4>
          <blockquote>{evidence.text}</blockquote>
        </section>
      )}
    </section>
  )
}
