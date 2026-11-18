import type { ReactNode } from 'react'
import type { InspectionExcerpt } from './inspectionEvidence'

export const closureFields = [
  ['ending', '主冲突与终局'],
  ['characters', '关键人物与关系'],
  ['promises', '伏笔与读者承诺'],
] as const
export type ClosureKey = (typeof closureFields)[number][0]
export type ClosureDecision = 'pending' | 'verified' | 'author_decision'
export type AuthorDecisions = Record<ClosureKey, ClosureDecision> & { reading: 'pending' | 'verified' }
export type Closure = { note: string; sources: InspectionExcerpt[] }
export type CompletionReview = Record<ClosureKey, Closure> & {
  read_note: string
  author_decisions?: AuthorDecisions | null
}

const decisionLabels: Record<ClosureDecision, string> = {
  pending: '待核对',
  verified: '已核对正文与依据',
  author_decision: '作者已裁定',
}

export function CompletionReviewEvidence({
  review,
  titles,
  openOriginal,
}: {
  review: CompletionReview
  titles: ReadonlyMap<string, string>
  openOriginal?: (evidence: InspectionExcerpt, description: string) => ReactNode
}) {
  const decisions = review.author_decisions
  return (
    <div>
      {decisions ? <h4>已通读当前稿件</h4> : <h4>原通读记录</h4>}
      <p style={{ overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}>{review.read_note}</p>
      {closureFields.map(([id, title]) => (
        <section key={id} aria-label={`${title}的原裁定`} style={{ overflowWrap: 'anywhere' }}>
          <h4>
            {title}
            {decisions && ` · ${decisionLabels[decisions[id]]}`}
          </h4>
          <p style={{ whiteSpace: 'pre-wrap' }}>{review[id].note}</p>
          {review[id].sources.map((evidence, index) => (
            <blockquote key={`${evidence.chapter_id}/${evidence.start}/${index}`}>
              {evidence.quote}
              <footer>
                {titles.get(evidence.chapter_id) ?? evidence.chapter_id} · 修订 {evidence.revision} · 位置{' '}
                {evidence.start}–{evidence.end}
              </footer>
              {openOriginal?.(evidence, `${title}原文 ${index + 1}`)}
            </blockquote>
          ))}
        </section>
      ))}
    </div>
  )
}
