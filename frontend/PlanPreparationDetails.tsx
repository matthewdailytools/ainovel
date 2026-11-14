const VERSION = 'workflow-plan-preparation/1'
const states: Record<string, string> = {
  interpreting: '正在核对执行含义',
  clarifying: '正在选择本章开放细节',
  checking: '正在核对原安排与固定条件',
  ready: '已冻结执行安排，正文仍须核对',
  needs_review: '执行安排待核对，未据此放行正文',
}
const checks: Record<string, string> = { pass: '模型标保持', fail: '发现不符', unknown: '无法确认' }

function record(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined
}

function text(value: unknown) {
  return typeof value === 'string' ? value : ''
}

function rows(value: unknown) {
  return Array.isArray(value)
    ? value.flatMap((row) => {
        const item = record(row)
        return item ? [item] : []
      })
    : []
}

export function planPreparationNeedsReview(version: unknown, value: unknown, chapter: unknown) {
  if (version !== VERSION || typeof chapter !== 'number' || !Number.isInteger(chapter) || chapter < 1)
    return false
  return record(record(value)?.[String(chapter)])?.status === 'needs_review'
}

export function PlanPreparationDetails({
  mode,
  enabled,
  version,
  value,
  taskStatus,
}: {
  mode?: string
  enabled?: boolean
  version?: string
  value?: unknown
  taskStatus?: string
}) {
  if (mode !== 'automatic' || enabled !== true) return null
  const entries = Object.entries(record(value) || {}).sort(([a], [b]) =>
    a.localeCompare(b, 'zh', { numeric: true }),
  )
  return (
    <details>
      <summary>AI工作章纲执行记录</summary>
      {version === undefined ? (
        <p className="small muted">历史批次未启用写前明确化，继续沿用原运行政策，不自动升级。</p>
      ) : version !== VERSION ? (
        <p role="status">本批次的章纲执行政策无法确认，请核对保存的任务记录。</p>
      ) : (
        <>
          <p className="small muted">
            先明确AI自己的工作安排，再写正文。最多两轮开放选择，原章纲和作者固定条件保留；剧情里的未知不代表必须解谜。
            冻结后的安排及解释用于后续正文复检，不根据已写正文倒改计划。这里的准备通过不等于小说质量通过。
          </p>
          {entries.length === 0 && <p>尚无分章准备记录。</p>}
          {entries.map(([key, raw]) => {
            const row = record(raw)
            if (!row) return <p key={key}>一项分章记录暂无法读取，请核对任务历史。</p>
            const ready = row.status === 'ready'
            const contract = record(row.prepared_contract)
            const complete =
              !!text(row.original_plot) &&
              !!text(row.effective_plot) &&
              /^[a-f0-9]{64}$/.test(text(row.original_plot_sha256)) &&
              /^[a-f0-9]{64}$/.test(text(row.effective_plot_sha256)) &&
              !!contract &&
              Array.isArray(contract.items) &&
              contract.items.length > 0
            const progress = ['interpreting', 'clarifying', 'checking'].includes(text(row.status))
            const status =
              ready && !complete
                ? '记录不完整，暂不能确认准备就绪'
                : progress && taskStatus !== 'running'
                  ? taskStatus === 'queued'
                    ? '等待继续准备'
                    : '准备未在运行，当前记录保留'
                  : states[text(row.status)] || '执行状态待核对'
            return (
              <details key={key}>
                <summary>
                  第{key}章 · {status}
                </summary>
                {text(row.reason) && <p role="status">{text(row.reason)}</p>}
                <h4>原AI章纲</h4>
                <pre className="stream">{text(row.original_plot) || '原文暂无法读取。'}</pre>
                {rows(row.choices).map((choice, index) => (
                  <div key={index}>
                    <h4>开放细节选择 · 第{typeof choice.round === 'number' ? choice.round : index + 1}轮</h4>
                    <pre className="stream">{text(choice.addendum) || '尚无可显示的附注。'}</pre>
                    {text(choice.rationale) && <p>{text(choice.rationale)}</p>}
                  </div>
                ))}
                {text(row.effective_plot) && (
                  <>
                    <h4>本章执行章纲</h4>
                    <pre className="stream">{text(row.effective_plot)}</pre>
                  </>
                )}
                {rows(row.preservation_checks).map((check, index) => {
                  const result = record(check.result)
                  const constraints = record(result?.constraints_check)
                  return (
                    <details key={index}>
                      <summary>
                        保持核对 · {check.round === 0 ? '原章纲' : `第${String(check.round)}轮选择后`}
                      </summary>
                      {text(result?.summary) && <p>{text(result?.summary)}</p>}
                      <ul>
                        {rows(result?.item_checks).map((item, itemIndex) => (
                          <li key={itemIndex}>
                            {text(item.item_id)}：{checks[text(item.result)] || '待核对'}。{text(item.reason)}
                          </li>
                        ))}
                      </ul>
                      <p>
                        作者条件与前后安排：{checks[text(constraints?.result)] || '待核对'}。
                        {text(constraints?.reason)}
                      </p>
                    </details>
                  )
                })}
                <details>
                  <summary>逐轮解释、保持核对与来源</summary>
                  <p className="small muted">以下是AI准备记录，不是已发生事实或作者认可。</p>
                  <pre className="stream">
                    {JSON.stringify(
                      {
                        original_plot_sha256: row.original_plot_sha256,
                        effective_plot_sha256: row.effective_plot_sha256,
                        interpretations: row.interpretations,
                        preservation_checks: row.preservation_checks,
                      },
                      null,
                      2,
                    )}
                  </pre>
                </details>
              </details>
            )
          })}
        </>
      )}
    </details>
  )
}
