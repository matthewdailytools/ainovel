export const CONDITIONAL_REVIEW_VERSION = 'conditional-book-check/1'

function object(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

function nonempty(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

export function hasNotTriggeredCheck(value: unknown): boolean {
  return (
    object(value) &&
    Array.isArray(value.checks) &&
    value.checks.some((check) => object(check) && check.result === 'not_triggered')
  )
}

export function notTriggeredLabel(check: Record<string, unknown>, review: unknown): string {
  return object(review) &&
    review.conditional_policy_version === CONDITIONAL_REVIEW_VERSION &&
    nonempty(check.condition) &&
    Array.isArray(review.conditional_book_conditions) &&
    review.conditional_book_conditions.every(nonempty) &&
    review.conditional_book_conditions.includes(check.condition) &&
    check.scope === 'book' &&
    check.applicability === 'not_triggered' &&
    check.chapter_obligation === 'not_required' &&
    nonempty(check.explanation)
    ? '本章未触发，整书待核对'
    : '未触发政策或依据无法核对，仍待核对'
}

export function ConditionalReviewNotice({ value }: { value: unknown }) {
  if (!object(value) || !Array.isArray(value.checks)) return null
  const checks = value.checks.filter((check) => object(check) && check.result === 'not_triggered')
  if (!checks.length) return null
  return (
    <section className="notice" aria-label="本章未触发的全书约定" style={{ overflowWrap: 'anywhere' }}>
      <p>本章未触发的约定不表示已经兑现；整书仍须逐项核对。</p>
      {checks.map((check, index) =>
        object(check) ? (
          <article key={index}>
            <strong>{notTriggeredLabel(check, value)}</strong>
            <p>{typeof check.condition === 'string' ? check.condition : '约定原话无法核对'}</p>
            {typeof check.explanation === 'string' && <p>{check.explanation}</p>}
          </article>
        ) : null,
      )}
    </section>
  )
}
