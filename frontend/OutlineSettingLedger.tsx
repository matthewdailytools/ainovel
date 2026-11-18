export type SettingLedgerLocation = {
  chapter_id: string
  chapter_title: string
  planned: 'current' | 'outdated' | null
  evidence: {
    method: 'author' | 'rule'
    status: 'current' | 'outdated' | 'unavailable'
    quote: string | null
    revision_id: string | null
    report_id?: string | null
  } | null
  source_unavailable?: boolean
}

export type SettingLedgerItem = {
  setting: { id: string; title: string; category_title?: string }
  chapters: SettingLedgerLocation[]
}

function evidenceLabel(row: SettingLedgerLocation) {
  const proof = row.evidence
  if (!proof) return row.source_unavailable ? '对账报告未在此端取得' : '待正文证据'
  if (proof.status === 'unavailable') return '原稿来源待补齐'
  if (proof.status === 'outdated') return '历史正文证据 · 待复核'
  return proof.method === 'author' ? '作者已确认正文使用' : '已有正文对账证据'
}

export function OutlineSettingLedger({
  items,
  onChapter,
}: {
  items: SettingLedgerItem[]
  onChapter?: (id: string, reportId?: string) => void
}) {
  if (!items.length) return <p>当前没有可用于章纲的已采用设定。</p>
  return (
    <div className="records">
      {items.map(({ setting, chapters }) => (
        <section key={setting.id} aria-label={`${setting.title} · ${setting.category_title ?? '世界设定'}`}>
          <h4>
            {setting.title} · {setting.category_title ?? '世界设定'}
          </h4>
          {!chapters.some(({ planned }) => planned) && <p>未安排</p>}
          {!!chapters.length && (
            <ul>
              {chapters.map((row) => (
                <li key={row.chapter_id}>
                  {onChapter ? (
                    <button
                      type="button"
                      onClick={() => onChapter(row.chapter_id, row.evidence?.report_id ?? undefined)}
                    >
                      {row.chapter_title}
                    </button>
                  ) : (
                    row.chapter_title
                  )}{' '}
                  ·{' '}
                  {row.planned === 'current'
                    ? '已计划'
                    : row.planned === 'outdated'
                      ? '计划待复核'
                      : '旧章引用'}{' '}
                  · {evidenceLabel(row)}
                  {row.evidence?.quote && (
                    <details>
                      <summary>查看正文证据</summary>
                      <blockquote>{row.evidence.quote}</blockquote>
                      {row.evidence.revision_id && (
                        <p style={{ overflowWrap: 'anywhere' }}>章稿修订 {row.evidence.revision_id}</p>
                      )}
                    </details>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </div>
  )
}
