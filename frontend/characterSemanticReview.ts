export const CHARACTER_REVIEW_VERSION = 'character-field-entailment/2'
export type CharacterReviewResult = 'supported' | 'unknown' | 'contradicted'
export type CharacterSemanticReview = {
  version: typeof CHARACTER_REVIEW_VERSION | 'character-field-entailment/1'
  candidate_revision: number
  input_sha256: string
  status: 'supported' | 'needs_review'
  results: {
    record_id: string
    field: 'notes' | 'profile'
    result: CharacterReviewResult
    reason: string
    evidence: {
      chapter_id: string
      revision: number
      start: number
      end: number
      quote: string
    }[]
  }[]
  reviewer: { account: string; provider: string; model: string }
  created: number
}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}
function revision(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
}

export function readCharacterSemanticReview(value: unknown): CharacterSemanticReview | undefined {
  if (
    !object(value) ||
    (value.version !== CHARACTER_REVIEW_VERSION && value.version !== 'character-field-entailment/1') ||
    !revision(value.candidate_revision) ||
    typeof value.input_sha256 !== 'string' ||
    !value.input_sha256 ||
    (value.status !== 'supported' && value.status !== 'needs_review') ||
    !Array.isArray(value.results) ||
    !object(value.reviewer) ||
    typeof value.reviewer.account !== 'string' ||
    typeof value.reviewer.provider !== 'string' ||
    typeof value.reviewer.model !== 'string' ||
    typeof value.created !== 'number' ||
    !Number.isFinite(value.created) ||
    value.created < 0
  )
    return undefined
  const results: CharacterSemanticReview['results'] = []
  for (const item of value.results) {
    if (
      !object(item) ||
      typeof item.record_id !== 'string' ||
      !item.record_id ||
      (item.field !== 'notes' && item.field !== 'profile') ||
      (item.result !== 'supported' && item.result !== 'unknown' && item.result !== 'contradicted') ||
      typeof item.reason !== 'string' ||
      !Array.isArray(item.evidence)
    )
      return undefined
    const evidence: CharacterSemanticReview['results'][number]['evidence'] = []
    for (const source of item.evidence) {
      if (
        !object(source) ||
        typeof source.chapter_id !== 'string' ||
        !source.chapter_id ||
        !revision(source.revision) ||
        !revision(source.start) ||
        !revision(source.end) ||
        source.end < source.start ||
        typeof source.quote !== 'string'
      )
        return undefined
      evidence.push({
        chapter_id: source.chapter_id,
        revision: source.revision,
        start: source.start,
        end: source.end,
        quote: source.quote,
      })
    }
    results.push({
      record_id: item.record_id,
      field: item.field,
      result: item.result,
      reason: item.reason,
      evidence,
    })
  }
  return {
    version: value.version,
    candidate_revision: value.candidate_revision,
    input_sha256: value.input_sha256,
    status: value.status,
    results,
    reviewer: {
      account: String(value.reviewer.account),
      provider: String(value.reviewer.provider),
      model: String(value.reviewer.model),
    },
    created: value.created,
  }
}

type ReviewState =
  'missing' | 'invalid' | 'legacy' | 'historical' | 'stale' | 'needs_review' | 'supported' | 'not_required'

export function characterReviewState(value: unknown, candidateRevision: number, sourceStale: boolean) {
  const report = readCharacterSemanticReview(value)
  const state: ReviewState =
    value === undefined || value === null
      ? 'missing'
      : !report
        ? 'invalid'
        : report.version !== CHARACTER_REVIEW_VERSION
          ? 'legacy'
          : report.candidate_revision !== candidateRevision
            ? 'historical'
            : sourceStale
              ? 'stale'
              : report.status === 'needs_review' || report.results.some((item) => item.result !== 'supported')
                ? 'needs_review'
                : report.results.length
                  ? 'supported'
                  : 'not_required'
  return { report, state }
}

export function extractionSourcesStale(
  manifest: { id: string; revision: number }[],
  chapters: { id: string; revision: number; stale: number }[],
) {
  return manifest.some((source) => {
    const current = chapters.find((chapter) => chapter.id === source.id)
    return !current || current.revision !== source.revision || !!current.stale
  })
}

// A refresh may finish after a new selection, another refresh, or unmounting.
export function createExtractionRequestGuard() {
  let latest = 0
  return {
    begin() {
      const request = ++latest
      return () => request === latest
    },
    invalidate() {
      latest += 1
    },
  }
}
