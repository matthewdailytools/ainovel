export const inspectionExcerptTransferReason =
  '旧体检保留了外部资料原句。请在本机查看该记录，含原句的历史不能通过稿件包或云同步发送。'
const dependentCompletionReason = '该完本记录引用了含外部资料原句的旧体检，保留本机并暂缓同步。'

function object(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined
}

function readObject(value: unknown): Record<string, unknown> | undefined {
  if (typeof value !== 'string') return undefined
  try {
    return object(JSON.parse(value))
  } catch {
    return undefined
  }
}

export function hasInspectionReferenceQuote(report: unknown) {
  const items = object(report)?.items
  return (
    Array.isArray(items) &&
    items.some((value) => {
      const item = object(value)
      if (item?.check_id !== '08') return false
      const basis = object(item.basis)
      return [basis?.card_references, basis?.planned_card_references].some(
        (cards) =>
          Array.isArray(cards) &&
          cards.some((card) => {
            const quote = object(object(card)?.source)?.quote
            return typeof quote === 'string' && quote.length > 0
          }),
      )
    })
  )
}

export function hasProjectedInspectionReferenceQuote(document: unknown) {
  const evidence = object(document)
  return evidence?.kind === 'inspection' && hasInspectionReferenceQuote(readObject(evidence.body))
}

export function workflowTransferRestrictions(
  objects: Iterable<readonly [string, unknown]>,
  previouslyBlocked: ReadonlySet<string> = new Set(),
) {
  const inspectionIds = new Set(previouslyBlocked)
  const deferred = new Map<string, string>()
  const completions = new Map<string, string>()
  for (const [id, value] of objects) {
    const payload = object(value)
    if (payload?.kind === 'workflow-inspection') {
      const entry = readObject(payload.entry_json)
      if (hasInspectionReferenceQuote(entry?.report)) {
        if (typeof entry?.id === 'string') inspectionIds.add(entry.id)
        deferred.set(id, inspectionExcerptTransferReason)
      }
    } else if (payload?.kind === 'workflow-completion') {
      const report = object(readObject(payload.entry_json)?.manifest)?.inspection_id
      if (typeof report === 'string') completions.set(id, report)
    } else if (payload?.kind === 'evidence' && hasProjectedInspectionReferenceQuote(payload.document)) {
      const report = object(payload.document)?.id
      if (typeof report === 'string') inspectionIds.add(report)
      deferred.set(id, inspectionExcerptTransferReason)
    }
  }
  for (const [id, report] of completions) {
    if (inspectionIds.has(report)) deferred.set(id, dependentCompletionReason)
  }
  return { inspectionIds, deferred }
}
