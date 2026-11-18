import { useEffect, useState } from 'react'
import { api, type Chapter } from './api'

type PublishedChapter = { chapter_id: string; published_at: string }
type MetricKind = 'adjacent_follow' | 'first_chapter_retention' | 'chapter_completion' | 'aggregate_ratio'
type Metric = {
  kind: MetricKind
  chapter_id: string
  target_chapter_id: string | null
  numerator: number | null
  denominator: number | null
  rate: number | null
  excluded_incomplete: number
  excluded_prior_or_tied: number
}
type ReadingReport = {
  mode: 'events' | 'aggregate'
  source: string
  release_version: string
  timezone: string
  extracted_at: string
  observed_from: string
  observed_until: string
  read_definition: string
  completion_definition: string | null
  cohort: { started_from: string; started_before: string; channel: string | null } | null
  window_hours: number | null
  event_rows: number | null
  distinct_readers: number | null
  event_channels: string[]
  chapters: PublishedChapter[]
  counts: { chapter_id: string; unique_readers: number | null }[]
  native_metrics: {
    name: string
    definition: string
    value: number
    unit: string
    chapter_id: string | null
  }[]
  metrics: Metric[]
  notes: string[]
}
type ImportedReport = { id: string; report: ReadingReport }
type ChapterOption = Pick<Chapter, 'id' | 'title' | 'position'>

const eventsKinds = ['adjacent_follow', 'first_chapter_retention', 'chapter_completion'] as const
const metricLabels = {
  adjacent_follow: '相邻章追读率',
  first_chapter_retention: '首章至目标章留存',
  chapter_completion: '单章完读率',
  aggregate_ratio: '各章独立读人数比值',
}
const percent = new Intl.NumberFormat('zh-CN', { style: 'percent', maximumFractionDigits: 2 })
const number = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 6 })
const missing = '无可计算数据'

function downloadTemplate(mode: ReadingReport['mode'], chapters: ChapterOption[]) {
  const ordered = [...chapters].sort((left, right) => left.position - right.position)
  const template = {
    format: 'ainovel-reading/1',
    mode,
    source: '',
    release_version: '',
    timezone: 'Asia/Shanghai',
    observed_from: '',
    observed_until: '',
    extracted_at: '',
    chapters: ordered.map(({ id }) => ({ chapter_id: id, published_at: '' })),
    read_definition: '',
    ...(mode === 'events'
      ? { window_hours: 168, completion_definition: null, events: [] }
      : { counts: ordered.map(({ id }) => ({ chapter_id: id, unique_readers: null })), native_metrics: [] }),
  }
  const url = URL.createObjectURL(new Blob([JSON.stringify(template, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `ainovel-reading-${mode}.json`
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function ReportDetails({ report, chapters }: { report: ReadingReport; chapters: ChapterOption[] }) {
  const [kind, setKind] = useState<MetricKind>(
    report.mode === 'events' ? 'adjacent_follow' : 'aggregate_ratio',
  )
  const titleById = new Map(chapters.map(({ id, title }) => [id, title]))
  const counts = new Map(report.counts.map(({ chapter_id, unique_readers }) => [chapter_id, unique_readers]))
  const chapterTitle = (id: string) => titleById.get(id) ?? id
  const rows = report.metrics.filter((metric) => metric.kind === kind)
  const showCoverage = report.mode === 'events'
  const comparable = report.metrics.filter(
    (metric) => metric.kind === 'adjacent_follow' && metric.rate !== null,
  )
  const lowest = comparable.reduce<Metric | undefined>((low, row) => {
    if (row.rate !== null && (low?.rate == null || row.rate < low.rate)) return row
    return low
  }, undefined)
  const hasDifference = lowest && comparable.some((row) => row.rate !== lowest.rate)
  return (
    <section aria-label="追读报告">
      <h3>
        {report.mode === 'events' ? '读者事件报告' : '独立读人数汇总'} · {report.release_version}
      </h3>
      <p>
        来源 {report.source} · 时区 {report.timezone} · 提取于{' '}
        <time dateTime={report.extracted_at}>{report.extracted_at}</time>
      </p>
      <p>
        观察范围 <time dateTime={report.observed_from}>{report.observed_from}</time> 至{' '}
        <time dateTime={report.observed_until}>{report.observed_until}</time>
        {report.window_hours !== null && ` · 跟踪窗口 ${report.window_hours} 小时`}
      </p>
      {showCoverage && (
        <p>
          {number.format(report.event_rows ?? 0)} 条事件 · {number.format(report.distinct_readers ?? 0)}{' '}
          个读者键
          {report.event_channels.length > 0 && ` · 渠道 ${report.event_channels.join('、')}`}
        </p>
      )}
      {report.cohort && (
        <p>
          首读批次 <time dateTime={report.cohort.started_from}>{report.cohort.started_from}</time> 至{' '}
          <time dateTime={report.cohort.started_before}>{report.cohort.started_before}</time>（截止不含）
          {report.cohort.channel && ` · 渠道 ${report.cohort.channel}`}
        </p>
      )}
      <details>
        <summary>发布时间与事件定义</summary>
        <p>有效阅读为「{report.read_definition}」。</p>
        {showCoverage && (
          <p>
            完读定义为{report.completion_definition ? `「${report.completion_definition}」` : '来源未提供'}。
          </p>
        )}
        <ul>
          {report.chapters.map((chapter) => (
            <li key={chapter.chapter_id}>
              {chapterTitle(chapter.chapter_id)} ·{' '}
              <time dateTime={chapter.published_at}>{chapter.published_at}</time>
              {report.mode === 'aggregate' && ` · 独立读人数 ${counts.get(chapter.chapter_id) ?? '未提供'}`}
            </li>
          ))}
        </ul>
      </details>
      {showCoverage && (
        <label>
          查看指标
          <select value={kind} onChange={(event) => setKind(event.target.value as MetricKind)}>
            {eventsKinds.map((key) => (
              <option value={key} key={key}>
                {metricLabels[key]}
              </option>
            ))}
          </select>
        </label>
      )}
      {rows.length > 0 ? (
        <div className="table-scroll" tabIndex={0} role="region" aria-label={metricLabels[kind]}>
          <table>
            <caption>{metricLabels[kind]}</caption>
            <thead>
              <tr>
                <th scope="col">章节</th>
                <th scope="col">分子</th>
                <th scope="col">分母</th>
                <th scope="col">结果</th>
                {showCoverage && <th scope="col">观察窗口未满</th>}
                {showCoverage && kind !== 'chapter_completion' && <th scope="col">已先读或同时读到目标章</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.chapter_id}/${row.target_chapter_id ?? ''}`}>
                  <th scope="row">
                    {chapterTitle(row.chapter_id)}
                    {row.target_chapter_id && ` → ${chapterTitle(row.target_chapter_id)}`}
                  </th>
                  <td>{row.numerator === null ? '未提供' : number.format(row.numerator)}</td>
                  <td>{row.denominator === null ? '未提供' : number.format(row.denominator)}</td>
                  <td>
                    {row.rate === null
                      ? missing
                      : showCoverage
                        ? percent.format(row.rate)
                        : `${number.format(row.rate)} 倍`}
                  </td>
                  {showCoverage && <td>{number.format(row.excluded_incomplete)}</td>}
                  {showCoverage && kind !== 'chapter_completion' && (
                    <td>{number.format(row.excluded_prior_or_tied)}</td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p role="status">{missing}</p>
      )}
      {report.native_metrics.length > 0 && (
        <details>
          <summary>平台原生指标</summary>
          <dl>
            {report.native_metrics.map((metric, index) => (
              <div key={index}>
                <dt>
                  {metric.name}
                  {metric.chapter_id && ` · ${chapterTitle(metric.chapter_id)}`}
                </dt>
                <dd>
                  {number.format(metric.value)} {metric.unit} · {metric.definition}
                </dd>
              </div>
            ))}
          </dl>
        </details>
      )}
      {hasDifference && lowest.target_chapter_id && lowest.rate !== null && (
        <p className="notice">
          待验证线索 · {chapterTitle(lowest.chapter_id)} → {chapterTitle(lowest.target_chapter_id)}
          的追读率在本次可计算章对中最低，为 {percent.format(lowest.rate)}（{lowest.numerator}/
          {lowest.denominator} 人）。可对照两章的衔接、更新间隔、推荐来源与可读范围。
        </p>
      )}
      <details>
        <summary>覆盖范围与解释</summary>
        <ul>
          {report.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      </details>
    </section>
  )
}

export function ReadingMetricsPanel({ branchId, chapters }: { branchId: string; chapters: ChapterOption[] }) {
  const [latest, setLatest] = useState<ImportedReport | null>()
  const [file, setFile] = useState<File>()
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [reload, setReload] = useState(0)
  const [status, setStatus] = useState('')
  const [loadError, setLoadError] = useState('')
  const [importError, setImportError] = useState('')
  const route = `/branches/${branchId}/reading-metrics`
  useEffect(() => {
    let active = true
    setLoading(true)
    setLoadError('')
    void api<{ latest: ImportedReport | null }>(route)
      .then((payload) => {
        if (active) setLatest(payload.latest)
      })
      .catch((error: unknown) => {
        if (active) setLoadError(error instanceof Error ? error.message : '报告读取失败。')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [route, reload])

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!file || loading || submitting) return
    const form = event.currentTarget
    const upload = new FormData()
    upload.set('file', file)
    setStatus('')
    setImportError('')
    setSubmitting(true)
    try {
      setLatest(await api<ImportedReport>(`${route}/import`, 'POST', upload))
      setFile(undefined)
      form.reset()
      setLoadError('')
      setStatus('追读报告已导入。')
    } catch (error) {
      setImportError(error instanceof Error ? error.message : '文件导入失败。')
    } finally {
      setSubmitting(false)
    }
  }
  return (
    <section className="card" aria-label="追读数据" style={{ overflowWrap: 'anywhere' }}>
      <h2>追读数据</h2>
      <details>
        <summary>文件口径与导入模板</summary>
        <p>
          UTF-8 JSON，最大 8
          MiB。模板预填当前工作版本的章节编号。请按发布顺序核对章列表，填入数据来源、发布版本、含时区偏移的发布时间和观察时间，例如
          <code>2026-11-01T00:00:00+08:00</code>，并写明有效阅读定义。导入后保存统计报告。
        </p>
        <ul>
          <li>
            事件文件的每条记录包含 chapter_id、匿名 reader_key、channel、at 和 kind。kind 使用 read、start 或
            finish。
          </li>
          <li>
            事件窗口默认 168 小时，可改 window_hours。限定首读批次时，填写 cohort 的 started_from 和
            started_before，也可填写 channel。若导出包含完读事件，请填写 completion_definition。
          </li>
          <li>
            汇总文件的 counts 每条包含 chapter_id 和 unique_readers。空缺使用 null。原生指标可填
            name、definition、value 和 unit。
          </li>
        </ul>
        <div className="actions">
          <button
            type="button"
            disabled={!chapters.length}
            onClick={() => downloadTemplate('events', chapters)}
          >
            下载事件模板
          </button>
          <button
            type="button"
            disabled={!chapters.length}
            onClick={() => downloadTemplate('aggregate', chapters)}
          >
            下载汇总模板
          </button>
        </div>
      </details>
      <form onSubmit={(event) => void submit(event)}>
        <label>
          追读数据文件
          <input
            type="file"
            accept=".json,application/json"
            disabled={submitting}
            onChange={(event) => {
              setFile(event.currentTarget.files?.[0])
              setImportError('')
              setStatus('')
            }}
          />
        </label>
        <button type="submit" disabled={!file || loading || submitting}>
          {submitting ? '正在导入…' : '导入并计算'}
        </button>
      </form>
      {loading && <p role="status">正在读取追读报告…</p>}
      {loadError && (
        <p role="alert">
          {loadError}{' '}
          <button type="button" disabled={submitting} onClick={() => setReload(reload + 1)}>
            重新读取
          </button>
        </p>
      )}
      {importError && <p role="alert">{importError}</p>}
      {status && <p role="status">{status}</p>}
      {latest && <ReportDetails key={latest.id} report={latest.report} chapters={chapters} />}
      {latest === null && !loading && <p className="muted">暂无追读报告。导入发布数据后可查看统计。</p>}
    </section>
  )
}
