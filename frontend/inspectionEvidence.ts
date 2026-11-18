import type { components } from './api-schema'

export type InspectionExcerpt = components['schemas']['Source']
export type SoftDeviation = { id: string; ruleTitle: string; source: InspectionExcerpt }
export const softDeviationPolicy = 'soft-deviation-review/1'

export function softDeviations(basis: Record<string, unknown>): SoftDeviation[] | undefined {
  if (basis.soft_deviation_policy !== softDeviationPolicy) return []
  if (!Array.isArray(basis.reports)) return
  const reports = basis.reports as ({
    result?: string
    report_id?: string
    chapter_id?: string
    revision?: number
    rule?: { title?: string; setting_policy?: { strength?: string } }
    evidence?: { start?: number; end?: number; text?: string }
  } | null)[]
  const deviations: SoftDeviation[] = []
  const ids = new Set<string>()
  for (const report of reports) {
    if (!report || typeof report !== 'object' || Array.isArray(report)) return
    if (report.result !== 'fail') continue
    const { rule, evidence } = report
    if (
      rule?.setting_policy?.strength !== 'soft' ||
      typeof report.report_id !== 'string' ||
      ids.has(report.report_id) ||
      typeof report.chapter_id !== 'string' ||
      typeof report.revision !== 'number' ||
      !Number.isSafeInteger(report.revision) ||
      report.revision < 0 ||
      typeof evidence?.start !== 'number' ||
      typeof evidence.end !== 'number' ||
      !Number.isSafeInteger(evidence.start) ||
      !Number.isSafeInteger(evidence.end) ||
      evidence.start < 0 ||
      evidence.end <= evidence.start ||
      typeof evidence.text !== 'string' ||
      !evidence.text.trim() ||
      evidence.text.length !== evidence.end - evidence.start
    )
      return
    ids.add(report.report_id)
    deviations.push({
      id: report.report_id,
      ruleTitle: rule.title ?? '软设定',
      source: {
        chapter_id: report.chapter_id,
        revision: report.revision,
        start: evidence.start,
        end: evidence.end,
        quote: evidence.text,
      },
    })
  }
  return deviations
}

export function coversInspectionExcerpt(excerpt: InspectionExcerpt, source: InspectionExcerpt) {
  return (
    excerpt.chapter_id === source.chapter_id &&
    excerpt.revision === source.revision &&
    excerpt.start <= source.start &&
    excerpt.end >= source.end &&
    excerpt.quote.slice(source.start - excerpt.start, source.end - excerpt.start) === source.quote
  )
}
