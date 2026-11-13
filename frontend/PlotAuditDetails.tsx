import { useEffect, useState } from 'react'
import { plotAuditState, readPlotAudit } from './plotAudit'

export function PlotAuditOption({
  checked,
  disabled,
  onChange,
}: {
  checked: boolean
  disabled?: boolean
  onChange: (value: boolean) => void
}) {
  return (
    <div>
      <label className="check">
        <input
          type="checkbox"
          checked={checked}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
        />
        额外逐项核对本次情节
      </label>
      <p className="small muted">
        通常增加解析和对照两步模型调用；物品或信息流向可能再增加一次，格式或引用修正也可能增加调用，每轮复检均增加费用。歧义或未知保留待核对，模型解释不形成作者新条件，手动采用仍由作者决定。
      </p>
    </div>
  )
}

export function FrozenPlotAuditPolicy({ enabled, mode }: { enabled: unknown; mode?: string }) {
  return (
    <p className="small muted">
      本任务冻结的情节逐项核对：{enabled === true ? '已开启（额外模型费用）' : '未开启'}。
      {enabled === true &&
        (mode === 'automatic'
          ? '核对来源是AI章纲，不是作者原话或新增硬条件。'
          : mode === 'assisted'
            ? '核对来源是作者提供的本章情节。'
            : '核对来源仅为本次原始指令。')}
    </p>
  )
}

const statuses = {
  model_pass: '模型标一致，仍须作者核对',
  failed: '发现不符',
  unverified: '待核对（含歧义或未知）',
  historical: '历史报告已失效，不能作为当前稿结论',
  legacy: '历史报告：版本或内容绑定未核实',
  pending: '正在核对报告对应的正文内容',
}
const checkLabels = { pass: '模型标一致', fail: '模型标不符', unknown: '未能核实' }

export function PlotAuditDetails({
  value,
  candidateRevision,
  candidateText,
  candidateSha256,
  historical = false,
  savedChapter,
  expectSavedChapter = false,
  baseRevision,
  targetStatus,
  targetMessage,
  expectedSha256,
  reportSavedChapter,
}: {
  value: unknown
  candidateRevision?: number
  candidateText?: string
  candidateSha256?: string
  historical?: boolean
  savedChapter?: { id: string; revision: number; content: string }
  expectSavedChapter?: boolean
  baseRevision?: number | null
  targetStatus?: 'ready' | 'historical' | 'loading' | 'unavailable'
  targetMessage?: string
  expectedSha256?: string
  reportSavedChapter?: { chapter_id: string; revision: number; content_sha256: string }
}) {
  const audit = readPlotAudit(value)
  const boundText = savedChapter?.content ?? candidateText
  const [hashed, setHashed] = useState<{ text: string; sha256?: string; error?: boolean }>()
  useEffect(() => {
    let active = true
    if (boundText !== undefined && audit?.binding && candidateSha256 === undefined) {
      if (!globalThis.crypto?.subtle) setHashed({ text: boundText, error: true })
      else
        void globalThis.crypto.subtle
          .digest('SHA-256', new TextEncoder().encode(boundText))
          .then((buffer) => {
            if (active)
              setHashed({
                text: boundText,
                sha256: [...new Uint8Array(buffer)]
                  .map((value) => value.toString(16).padStart(2, '0'))
                  .join(''),
              })
          })
          .catch(() => {
            if (active) setHashed({ text: boundText, error: true })
          })
    }
    return () => {
      active = false
    }
  }, [boundText, candidateSha256, audit?.binding?.candidate_sha256])
  if (value === undefined || value === null) return null
  if (!audit)
    return (
      <section aria-label="情节逐项核对">
        <p role="status">情节核对报告格式无法核对，不能视为一致；原始报告仍保留。</p>
        <details>
          <summary>查看原始情节核对报告</summary>
          <pre className="report">{JSON.stringify(value, null, 2)}</pre>
        </details>
      </section>
    )
  const currentHash = candidateSha256 ?? (hashed && hashed.text === boundText ? hashed.sha256 : undefined)
  const state = plotAuditState(audit, {
    revision: candidateRevision,
    sha256: currentHash,
    edited: historical,
    hashPending: boundText !== undefined && !currentHash,
    expectSavedChapter,
    savedChapter: savedChapter && {
      id: savedChapter.id,
      revision: savedChapter.revision,
      sha256: currentHash,
    },
    baseRevision,
    targetStatus,
    expectedSha256,
    reportSavedChapter,
  })
  const savedTarget = audit.target ?? reportSavedChapter
  const workflowPlan = audit.binding?.source.kind === 'workflow_plan'
  const sourceLabel = workflowPlan
    ? 'AI章纲摘录（不是作者原话或新增硬条件）'
    : audit.binding?.source.kind === 'author_instruction'
      ? '本次原始指令摘录'
      : audit.binding?.source.kind === 'author_plot'
        ? '作者情节摘录'
        : '历史来源摘录（来源类型未核实）'
  return (
    <section aria-label="情节逐项核对" style={{ overflowWrap: 'anywhere' }}>
      <details>
        <summary>
          {workflowPlan ? 'AI章纲逐项核对' : '情节逐项核对'} · {statuses[state]}
        </summary>
        <p role="status">{statuses[state]}。模型辅助核对不等于作者认可或文学质量通过。</p>
        {targetMessage && <p role="status">{targetMessage}</p>}
        {hashed?.text === boundText && hashed?.error && (
          <p>正文内容哈希暂不可核对，报告不能视为当前有效结论。</p>
        )}
        <p>{audit.policy}</p>
        <p>{audit.summary}</p>
        <p>{audit.interpretation_note}</p>
        {workflowPlan && <p>以下是对AI生成章纲的解释和核对，不能升级为作者原话硬条件。</p>}
        {audit.binding && (
          <p className="small muted">
            版本：{audit.binding.version}；报告正文修订{audit.binding.candidate_revision}； 来源文本SHA-256：
            {audit.binding.source.text_sha256}；正文SHA-256：{audit.binding.candidate_sha256}。
          </p>
        )}
        {savedTarget && (
          <p className="small muted">
            检查对象：保存稿 {savedTarget.chapter_id} · 修订{savedTarget.revision} · SHA-256：
            {savedTarget.content_sha256}。
          </p>
        )}
        {audit.items.map((item) => {
          const check = audit.checks.find((row) => row.item_id === item.id)!
          return (
            <article className="fact" key={item.id}>
              <strong>
                {item.id} · AI解释{item.certainty === 'ambiguous' ? '（存在歧义）' : '（仍须核对）'}
              </strong>
              <p>{sourceLabel}</p>
              <blockquote>{item.author_evidence}</blockquote>
              <p>
                {[item.actor, item.action, item.object, item.direction, item.result]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
              <p>本稿观察：{check.observed}</p>
              <p>
                {checkLabels[check.result]}：{check.reason}
              </p>
              <blockquote>{check.evidence}</blockquote>
              {check.repair && <p>修订建议：{check.repair}</p>}
            </article>
          )
        })}
        {audit.findings.map((finding, index) => (
          <article className="fact" key={index}>
            <strong>
              第{finding.chapter}章 · {finding.severity === 'blocker' ? '严重问题' : '建议'}
            </strong>
            <p>{finding.reason}</p>
            <blockquote>{finding.quote}</blockquote>
            {finding.repair && <p>修订建议：{finding.repair}</p>}
          </article>
        ))}
        <details>
          <summary>查看原始核对记录与附加来源</summary>
          <pre className="report">{JSON.stringify(value, null, 2)}</pre>
        </details>
      </details>
    </section>
  )
}
