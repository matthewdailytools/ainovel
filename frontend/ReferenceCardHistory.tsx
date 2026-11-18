import { useEffect, useState } from 'react'
import { api } from './api'
import type { Notify } from './App'
import type { ArchivedReferenceCard } from './referenceArchive'

type SourceState = 'local' | 'included_excerpt' | 'metadata_only' | 'no_evidence'
export type ReferenceHistoryRow = {
  version: ArchivedReferenceCard
  adopted: boolean
  source_state: SourceState
  source_device: string | null
}
type QueryResult = { key: string; rows?: ReferenceHistoryRow[]; error?: string }

const assessments = { ready: '可复用', incomplete: '未完成', archived: '已归档' }
const sources = {
  local: '本机有可核对的原文',
  included_excerpt: '稿件包附有已选原句',
  metadata_only: '仅有引用地址，原文尚未取得',
  no_evidence: '该修订未携带原文依据',
}
const fieldLabels = {
  observation: '观察',
  interpretation: '解读',
  technique: '可迁移手法',
  conditions: '适用条件',
  exceptions: '须替换',
  evidence: '原文依据',
}

export function canReadEvidence(row: ReferenceHistoryRow) {
  return row.source_state === 'local' || row.source_state === 'included_excerpt'
}

export function referenceVersionLabel(row: ReferenceHistoryRow) {
  return row.version.card?.technique || row.version.card_id
}

export function useReferenceCardHistory(
  branchId: string,
  visible: boolean,
  sessionId?: string,
  revision?: number,
) {
  const [value, setValue] = useState<QueryResult>()
  const [attempt, setAttempt] = useState(0)
  const path = `/branches/${branchId}/reference-cards${sessionId ? '?session_id=' + encodeURIComponent(sessionId) : ''}`
  const key = JSON.stringify([path, revision, attempt])
  useEffect(() => {
    if (!visible) return
    let active = true
    void api<ReferenceHistoryRow[]>(path)
      .then((rows) => {
        if (active) setValue({ key, rows })
      })
      .catch((error: unknown) => {
        if (active) setValue({ key, error: error instanceof Error ? error.message : '手法卡读取失败。' })
      })
    return () => {
      active = false
    }
  }, [key, path, visible])
  return {
    rows: value?.key === key ? value.rows : undefined,
    error: value?.key === key ? value.error : undefined,
    retry: () => setAttempt((current) => current + 1),
  }
}

function CardRevision({
  branchId,
  row,
  notify,
}: {
  branchId: string
  row: ReferenceHistoryRow
  notify: Notify
}) {
  const [proof, setProof] = useState<{ excerpt: string | null; source_state: SourceState }>()
  const [busy, setBusy] = useState(false)
  const { version } = row
  const citation = version.citation
  const state = proof?.source_state ?? row.source_state
  const assessment = assessments[version.assessment as keyof typeof assessments]
  async function readProof() {
    setBusy(true)
    try {
      setProof(await api(`/branches/${branchId}/reference-cards/${version.id}/evidence`))
    } catch (error) {
      notify(error)
    } finally {
      setBusy(false)
    }
  }
  return (
    <details className="fact">
      <summary>
        修订 {version.revision} · {assessment} · {row.adopted ? '当前版本' : '历史版本'}
      </summary>
      {version.goal && <p>学习目标 {version.goal}</p>}
      {version.card && (
        <dl>
          {(['observation', 'interpretation', 'technique', 'conditions', 'exceptions'] as const).map(
            (key) => (
              <div key={key}>
                <dt>{fieldLabels[key]}</dt>
                <dd>{version.card?.[key]}</dd>
              </div>
            ),
          )}
        </dl>
      )}
      {!!version.missing_fields.length && (
        <p>待补 {version.missing_fields.map((key) => fieldLabels[key]).join('、')}</p>
      )}
      {citation && (
        <>
          <p>
            {citation.title} · {sources[state]}
          </p>
          <p>
            已保存选段 UTF-16 {citation.selection_start}–{citation.selection_end}
            {citation.evidence_start === null
              ? ' · 原句在选段内有多个匹配位置'
              : ` · 证据 ${citation.evidence_start}–${citation.evidence_end}`}
          </p>
          {canReadEvidence(row) && !proof && (
            <button type="button" disabled={busy} onClick={() => void readProof()}>
              {busy ? '正在核对原句…' : '查看证据原句'}
            </button>
          )}
          {proof?.excerpt && <blockquote>{proof.excerpt}</blockquote>}
        </>
      )}
      <details>
        <summary>查看来源编号</summary>
        <p className="report">手法卡版本 SHA-256 {version.id}</p>
        {version.parent_id && <p className="report">父版本 {version.parent_id}</p>}
        {row.source_device && <p className="report">来源设备 {row.source_device}</p>}
        {citation && (
          <>
            <p className="report">
              资料编号 {citation.book_id} · 单元 {citation.unit_id}
            </p>
            <p className="report">资料原件 SHA-256 {citation.file_sha256}</p>
            <p className="report">证据原句 SHA-256 {citation.evidence_sha256}</p>
          </>
        )}
      </details>
    </details>
  )
}

export function ReferenceCardHistory({
  branchId,
  sessionId,
  revision,
  notify,
}: {
  branchId: string
  sessionId?: string
  revision?: number
  notify: Notify
}) {
  const [visible, setVisible] = useState(false)
  const { rows, error, retry } = useReferenceCardHistory(branchId, visible, sessionId, revision)
  const cards = new Map<string, ReferenceHistoryRow[]>()
  for (const row of rows ?? []) {
    const key = JSON.stringify([row.version.session_id, row.version.card_id])
    cards.set(key, [...(cards.get(key) ?? []), row])
  }
  return (
    <details className="card" onToggle={(event) => setVisible(event.currentTarget.open)}>
      <summary>已保存手法卡与历史</summary>
      {visible && !rows && !error && <p role="status">正在读取已保存手法卡…</p>}
      {error && (
        <>
          <p role="alert">{error}</p>
          <button type="button" onClick={retry}>
            重试读取手法卡
          </button>
        </>
      )}
      {rows && !rows.length && <p>当前范围还没有保存的手法卡。</p>}
      {[...cards.entries()].map(([key, entries]) => (
        <details className="fact" key={key}>
          <summary>
            {referenceVersionLabel(entries.find((entry) => entry.version.card) ?? entries[0])}
            {' · '}
            {entries.length} 个修订
          </summary>
          {entries.map((item) => (
            <CardRevision key={item.version.id} branchId={branchId} row={item} notify={notify} />
          ))}
        </details>
      ))}
    </details>
  )
}
