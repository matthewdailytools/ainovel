import { useEffect, useState } from 'react'
import { api, type Chapter } from './api'
import type { components } from './api-schema'

type PublishedChapter = { chapter_id: string; published_at: string; opens_at?: string | null }
type Diagnosis = components['schemas']['ReadingDiagnosis']
type AccessExclusions = { excluded_ineligible?: number | null; excluded_access_unknown?: number | null }
type ChannelFollow = components['schemas']['ChannelFollow'] & AccessExclusions
type MetricKind = 'adjacent_follow' | 'first_chapter_retention' | 'chapter_completion' | 'aggregate_ratio'
type AccessMode = 'shared_opening' | 'shared_opening_assumed' | 'reader_eligibility' | 'unknown'
type Metric = AccessExclusions & {
  kind: MetricKind
  chapter_id: string
  target_chapter_id: string | null
  numerator: number | null
  denominator: number | null
  rate: number | null
  excluded_incomplete: number
  excluded_prior_or_tied: number
  channel_breakdown?: ChannelFollow[] | null
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
  access_mode?: AccessMode | null
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
  diagnostics?: Diagnosis[]
}
type ImportedReport = { id: string; report: ReadingReport }
type HistoryEntry = Pick<ReadingReport, 'mode' | 'release_version' | 'extracted_at'> & { id: string }
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
const changeName = { access: '可读范围', recommendation: '推荐', revision: '发布稿修订' }
const accessLabel: Record<AccessMode, string> = {
  shared_opening: '来源声明共同开放',
  shared_opening_assumed: '按共同开放假设统计，读者资格未观测',
  reader_eligibility: '按读者资格统计，资格未知和明确无权分别排除',
  unknown: '读者资格覆盖未知，章间追读与留存无可计算数据',
}

function eligibilityCells(row: AccessExclusions) {
  return (
    <>
      <td>{row.excluded_ineligible == null ? '未记录' : number.format(row.excluded_ineligible)}</td>
      <td>{row.excluded_access_unknown == null ? '未记录' : number.format(row.excluded_access_unknown)}</td>
    </>
  )
}

function ChannelBreakdown({ title, rows }: { title: string; rows?: ChannelFollow[] | null }) {
  if (!rows) return <p>该报告未保存渠道细分。</p>
  const showEligibility = rows.some((row) => row.excluded_access_unknown != null)
  return (
    <>
      <p>按来源章首次有效阅读的渠道归属。同一首读时刻出现多个渠道的读者合计一次。</p>
      <div className="table-scroll" tabIndex={0} role="region" aria-label={title}>
        <table>
          <caption>{title}</caption>
          <thead>
            <tr>
              <th scope="col">首读渠道</th>
              <th scope="col">分子</th>
              <th scope="col">分母</th>
              <th scope="col">追读率</th>
              <th scope="col">观察窗口未满</th>
              <th scope="col">已先读或同时读到目标章</th>
              {showEligibility && <th scope="col">明确无权</th>}
              {showEligibility && <th scope="col">资格未知</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={JSON.stringify(row.channel)}>
                <th scope="row">{row.channel ?? '同刻多个首读渠道'}</th>
                <td>{number.format(row.numerator)}</td>
                <td>{number.format(row.denominator)}</td>
                <td>{row.rate === null ? missing : percent.format(row.rate)}</td>
                <td>{number.format(row.excluded_incomplete)}</td>
                <td>{number.format(row.excluded_prior_or_tied)}</td>
                {showEligibility && eligibilityCells(row)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

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
    chapters: ordered.map(({ id }) => ({ chapter_id: id, published_at: '', opens_at: null })),
    publication_changes: [],
    read_definition: '',
    ...(mode === 'events'
      ? {
          window_hours: 168,
          completion_definition: null,
          access_mode: 'unknown',
          reader_access: null,
          events: [],
        }
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
  const showEligibility = rows.some((row) => row.excluded_access_unknown != null)
  const channelRows = rows.flatMap(({ chapter_id, target_chapter_id, channel_breakdown }) =>
    target_chapter_id && channel_breakdown?.length
      ? [{ from: chapter_id, to: target_chapter_id, channels: channel_breakdown }]
      : [],
  )
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
        <>
          <p>
            {number.format(report.event_rows ?? 0)} 条事件 · {number.format(report.distinct_readers ?? 0)}{' '}
            个读者键
            {report.event_channels.length > 0 && ` · 渠道 ${report.event_channels.join('、')}`}
          </p>
          <p>{report.access_mode ? accessLabel[report.access_mode] : '此历史报告未记录读者资格口径。'}</p>
        </>
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
              {chapter.opens_at && (
                <>
                  {' · 开放于 '}
                  <time dateTime={chapter.opens_at}>{chapter.opens_at}</time>
                </>
              )}
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
                {showEligibility && <th scope="col">明确无权</th>}
                {showEligibility && <th scope="col">资格未知</th>}
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
                  {showEligibility && eligibilityCells(row)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p role="status">{missing}</p>
      )}
      {!!channelRows.length && (
        <details>
          <summary>按章对查看首读渠道</summary>
          {channelRows.map(({ from, to, channels }) => (
            <ChannelBreakdown
              key={from + '/' + to}
              title={`${chapterTitle(from)} → ${chapterTitle(to)} · 渠道资格`}
              rows={channels}
            />
          ))}
        </details>
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
      {!!report.diagnostics?.length && (
        <section aria-label="追读待验证线索">
          <h4>待验证线索</h4>
          <ul>
            {report.diagnostics.map((item) => (
              <li key={`${item.chapter_id}/${item.target_chapter_id}`}>
                <strong>
                  {chapterTitle(item.chapter_id)} → {chapterTitle(item.target_chapter_id)}
                </strong>
                <p>
                  追读 {percent.format(item.rate)}（{item.numerator}/{item.denominator} 人）。同口径的{' '}
                  {item.baseline_pairs} 组相邻章中位数为 {percent.format(item.baseline_rate)}。
                </p>
                <p>
                  两章发布相隔 {number.format(item.publication_gap_hours)} 小时，本次可计算章对的中位间隔为{' '}
                  {number.format(item.median_publication_gap_hours)} 小时。
                </p>
                <ChannelBreakdown
                  title={`${chapterTitle(item.chapter_id)} → ${chapterTitle(item.target_chapter_id)} · 首读渠道分解`}
                  rows={item.channel_breakdown}
                />
                {item.changes.length ? (
                  <ul>
                    {item.changes.map((change, index) => (
                      <li key={index}>
                        {chapterTitle(change.chapter_id)} · {changeName[change.kind]} ·{' '}
                        <time dateTime={change.at}>{change.at}</time> · {change.note}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p>来源未提供这两章的可读范围、推荐或稿件变化记录。</p>
                )}
              </li>
            ))}
          </ul>
        </section>
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
  const [history, setHistory] = useState<HistoryEntry[]>([])
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
    void Promise.all([api<{ latest: ImportedReport | null }>(route), api<HistoryEntry[]>(`${route}/history`)])
      .then(([payload, reports]) => {
        if (active) {
          setLatest(payload.latest)
          setHistory(reports)
        }
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
      const imported = await api<ImportedReport>(`${route}/import`, 'POST', upload)
      const { release_version, mode, extracted_at } = imported.report
      setLatest(imported)
      setHistory((current) => [{ id: imported.id, release_version, mode, extracted_at }, ...current])
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
  async function selectReport(id: string) {
    if (!id || id === latest?.id) return
    setLoading(true)
    setLoadError('')
    try {
      setLatest(await api<ImportedReport>(`${route}/history/${encodeURIComponent(id)}`))
    } catch (failure) {
      setLoadError(failure instanceof Error ? failure.message : '历史报告读取失败。')
    } finally {
      setLoading(false)
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
            章节开放晚于发布时，填写 opens_at。按读者限制访问时，使用 reader_eligibility 作为 access_mode， 在
            reader_access 中逐条填写 chapter_id、匿名 reader_key 和带时区的 available_from。值为 null 表示
            观察期内明确无权，缺少该读者和章节的记录表示资格未知。时间值表示从该时点起在本次观察期内持续可读。
          </li>
          <li>
            数据源能确认共同开放时，可使用 shared_opening 并将 reader_access 留空。资料不足时保留模板的
            unknown 口径。访问变化说明不能用于推断逐个读者的资格。
          </li>
          <li>
            汇总文件的 counts 每条包含 chapter_id 和 unique_readers。空缺使用 null。原生指标可填
            name、definition、value 和 unit。
          </li>
          <li>
            已知发布变化可选填 publication_changes，每条含 chapter_id、at、note，kind 使用
            access、recommendation 或 revision。
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
      {history.length > 1 && (
        <label>
          发布报告
          <select
            value={latest?.id ?? ''}
            disabled={loading || submitting}
            onChange={(event) => void selectReport(event.target.value)}
          >
            {history.map((entry) => (
              <option value={entry.id} key={entry.id}>
                {entry.release_version} · {entry.mode === 'events' ? '事件' : '汇总'} · {entry.extracted_at}
              </option>
            ))}
          </select>
        </label>
      )}
      {latest && <ReportDetails key={latest.id} report={latest.report} chapters={chapters} />}
      {latest === null && !loading && <p className="muted">暂无追读报告。导入发布数据后可查看统计。</p>}
    </section>
  )
}
