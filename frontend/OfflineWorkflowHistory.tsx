import { useEffect, useRef, useState } from 'react'
import { createDeliveryZip } from './deliveryArchive'
import {
  manuscriptHash,
  readLocalSourceRevision,
  readLocalWorkflowHistory,
  type LocalRevision,
} from './offlineStore'
import { inspectionSettingUses, verifyInspectionSettingUse } from './workflowArchive'

const inspectionLabels = {
  green: '绿 · 已核对',
  yellow: '黄 · 待处理',
  red: '红 · 有冲突',
  pending: '待检查',
  not_applicable: '经核对不适用',
  outdated: '原记录已过期',
}
const scopeNames = { book: '全书体检', volume: '卷体检', chapter: '章体检' }
const date = (stamp: number) => new Date(stamp * 1000).toLocaleString('zh-CN')

export function OfflineWorkflowHistory({
  projectId,
  refreshToken,
  download,
}: {
  projectId: string
  refreshToken: number
  download: (filename: string, blob: Blob) => void
}) {
  const [history, setHistory] = useState<Awaited<ReturnType<typeof readLocalWorkflowHistory>>>()
  const [selection, setSelection] = useState('')
  const [failure, setFailure] = useState('')
  const [source, setSource] = useState<LocalRevision>()
  const [excerpt, setExcerpt] = useState<{ start: number; end: number; quote: string }>()
  const [sourceFailure, setSourceFailure] = useState('')
  const request = useRef(0)

  useEffect(() => {
    let active = true
    void readLocalWorkflowHistory(projectId).then(
      (loaded) => {
        if (active) {
          setHistory(loaded)
          setFailure('')
        }
      },
      (error: unknown) => {
        if (active) setFailure(error instanceof Error ? error.message : '无法读取历史报告。')
      },
    )
    return () => {
      active = false
      request.current++
    }
  }, [projectId, refreshToken])

  const inspections = history?.inspections ?? []
  const completions = history?.completions ?? []
  const options = [
    ...inspections.map(({ id, report, created }) => ({
      id,
      created,
      title: `${scopeNames[report.scope]}${report.scope_id ? ` · ${report.scope_id}` : ''}`,
    })),
    ...completions.map(({ id, created }) => ({ id, created, title: '完本交付' })),
  ].sort((left, right) => right.created - left.created || left.id.localeCompare(right.id))
  const identifier = options.find(({ id }) => id === selection)?.id ?? options[0]?.id
  const inspection = inspections.find(({ id }) => id === identifier)
  const completion = completions.find(({ id }) => id === identifier)
  const record = inspection ?? completion
  const devices = history?.sourceDevices
  const device = identifier && devices && Object.hasOwn(devices, identifier) ? devices[identifier] : undefined

  async function showOriginal(
    binding: NonNullable<typeof record>['chapter_bindings'][number],
    settingUse?: ReturnType<typeof inspectionSettingUses>[number],
  ) {
    const token = ++request.current
    setSource(undefined)
    setExcerpt(undefined)
    setSourceFailure('')
    try {
      const version = await readLocalSourceRevision(projectId, binding.revision_id)
      if (!version || version.chapter_id !== binding.id || version.sha256 !== binding.sha256)
        throw new Error('缺少与这份报告对应的原章稿修订。')
      const quote = settingUse
        ? await verifyInspectionSettingUse(settingUse, version, manuscriptHash)
        : undefined
      if (token === request.current) {
        setSource(version)
        if (settingUse && quote)
          setExcerpt({ start: settingUse.evidence.start, end: settingUse.evidence.end, quote })
      }
    } catch (error) {
      if (token === request.current)
        setSourceFailure(error instanceof Error ? error.message : '无法读取来源正文。')
    }
  }

  if (failure) return <p role="alert">{failure}</p>
  if (!record) return null
  return (
    <details aria-label="历次体检与完本交付">
      <summary>历次体检与完本交付</summary>
      <p className="small">
        {device ? `源自同步设备 ${device}` : '源自稿件交换包'}。当前稿件可在工作台复检。
      </p>
      <label>
        选择历史报告
        <select
          value={identifier}
          onChange={(event) => {
            request.current++
            setSelection(event.target.value)
            setSource(undefined)
            setExcerpt(undefined)
            setSourceFailure('')
          }}
        >
          {options.map(({ id, title, created }) => (
            <option key={id} value={id}>
              {title} · {date(created)} · {id.slice(0, 8)}
            </option>
          ))}
        </select>
      </label>
      {inspection && (
        <ol aria-label="十处对账">
          {inspection.report.items.map((item) => (
            <li key={item.check_id}>
              <details>
                <summary>
                  {item.title} · {inspectionLabels[item.status]}
                </summary>
                <p>{item.summary}</p>
                {item.check_id === '08' &&
                  inspectionSettingUses(item.basis).map((use) => (
                    <p key={use.version_id}>
                      <button
                        type="button"
                        onClick={() =>
                          void showOriginal(
                            {
                              id: use.evidence.chapter_id,
                              revision_id: use.evidence.revision_id,
                              revision: use.revision,
                              sha256: use.chapter_sha256,
                            },
                            use,
                          )
                        }
                      >
                        查看「{use.title}」原文 · 修订 {use.revision} · 位置 {use.evidence.start}–
                        {use.evidence.end}
                      </button>
                    </p>
                  ))}
                {item.author_review && (
                  <section aria-label="原作者核对">
                    <p>{item.author_review.basis_note}</p>
                    <p>{item.author_review.reason}</p>
                    {!!item.author_review.evidence.length && (
                      <ul aria-label="核对原文引用">
                        {item.author_review.evidence.map((source, index) => (
                          <li key={index}>
                            <blockquote>{source.quote}</blockquote>
                            <p className="small">
                              章节 {source.chapter_id} · 修订 {source.revision} · 选区 {source.start}–
                              {source.end}
                            </p>
                          </li>
                        ))}
                      </ul>
                    )}
                  </section>
                )}
              </details>
            </li>
          ))}
        </ol>
      )}
      {completion && (
        <section aria-label="历史完本清单">
          <ul>
            {completion.checks.map((check) => (
              <li key={check.id}>{check.title} · 原判定通过</li>
            ))}
          </ul>
          <p>
            来源体检 {completion.manifest.inspection_id.slice(0, 8)} ·{' '}
            {completion.manifest.spoiler_scope === 'opening' ? '简介仅含开篇' : '简介涉及全书'}
          </p>
          <button
            type="button"
            onClick={() => download('ainovel-delivery.zip', createDeliveryZip(completion))}
          >
            下载历史交付 ZIP
          </button>
        </section>
      )}
      <ul aria-label="历史报告原章稿">
        {record.chapter_bindings.map((binding) => (
          <li key={binding.id}>
            <button type="button" onClick={() => void showOriginal(binding)}>
              查看来源修订 {binding.revision} · {binding.revision_id.slice(0, 8)}
            </button>
          </li>
        ))}
      </ul>
      {sourceFailure && <p role="alert">{sourceFailure}</p>}
      {source && (
        <section aria-label="历史报告引用正文">
          <p>
            来源修订 {source.id} · SHA-256 {source.sha256}
          </p>
          <pre className="report">
            {excerpt ? (
              <>
                {source.content.slice(0, excerpt.start).slice(-240)}
                <mark>{excerpt.quote}</mark>
                {source.content.slice(excerpt.end, excerpt.end + 240)}
              </>
            ) : (
              source.content || '空稿'
            )}
          </pre>
        </section>
      )}
    </details>
  )
}
