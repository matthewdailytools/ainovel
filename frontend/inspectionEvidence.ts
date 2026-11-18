import type { components } from './api-schema'

export type InspectionExcerpt = components['schemas']['Source']
export type SoftDeviation = { id: string; ruleTitle: string; source: InspectionExcerpt }
export const softDeviationPolicy = 'soft-deviation-review/1'
export const maxAuthorEvidence = 30
export const maxSoftDeviations = 1000

// A textarea counts a CRLF as one unit. Restore the UTF-16 offset in the saved text.
export function storedTextOffset(content: string, textareaOffset: number) {
  let original = 0
  for (let normalized = 0; normalized < textareaOffset && original < content.length; normalized += 1) {
    original += content.slice(original, original + 2) === '\r\n' ? 2 : 1
  }
  return original
}

type ScopedInspectionFinding = { scope: 'chapter' | 'volume'; scope_id: string; report_id: string }

export function scopedInspectionFindings(basis: Record<string, unknown>): ScopedInspectionFinding[] {
  if (!Array.isArray(basis.scoped_findings)) return []
  const findings: ScopedInspectionFinding[] = []
  for (const value of basis.scoped_findings as unknown[]) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) continue
    const { scope, scope_id, report_id } = value as Record<string, unknown>
    if (
      (scope === 'chapter' || scope === 'volume') &&
      typeof scope_id === 'string' &&
      scope_id &&
      typeof report_id === 'string' &&
      report_id
    )
      findings.push({ scope, scope_id, report_id })
  }
  return findings
}

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
