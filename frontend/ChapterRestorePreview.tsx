import { Difference } from './Editor'

type RestoreVersion = { revision: number; content: string }
type RestoreSource = { chapter_id: string; revision: number; start: number; end: number; quote: string }
type RestoreReference = {
  kind: 'outline' | 'setting' | 'story_record' | 'fact' | 'scene_time' | 'reference_card'
  id: string
  title: string
  source: RestoreSource | null
}
type RestorePromise = {
  id: string
  title: string
  status: string
  sources: readonly RestoreSource[]
  window: {
    plant_chapter_id?: string | null
    opens_chapter_id?: string | null
    closes_chapter_id?: string | null
  } | null
}

export type RestoreAffectedReport = {
  id: string
  kind: 'workflow_inspection' | 'book_completion' | 'author_finalization' | 'setting_review'
  scope: 'chapter' | 'volume' | 'book'
  scope_id: string | null
  check_ids: readonly string[]
}

export type ChapterRestorePreviewProps = {
  current: RestoreVersion
  target: RestoreVersion
  references: readonly RestoreReference[]
  promises: readonly RestorePromise[]
  affected_reports: readonly RestoreAffectedReport[]
}

const referenceNames = {
  outline: '大纲',
  setting: '设定',
  story_record: '台账记录',
  fact: '事实',
  scene_time: '场景时间',
  reference_card: '手法卡',
}
const promiseStatuses: Record<string, string> = {
  planned: '计划',
  planted: '已埋设',
  reinforced: '已推进',
  resolved: '已回收',
  retained: '作者确认保留',
  abandoned: '作者确认放弃',
  stale: '证据待复核',
}
const reportNames = {
  workflow_inspection: '体检单',
  book_completion: '完本记录',
  author_finalization: '作者定稿',
  setting_review: '设定校验',
}
const scopes = { chapter: '章', volume: '卷', book: '全书' }

export function RestoreReportList({
  reports,
  scopeNames,
}: {
  reports: readonly RestoreAffectedReport[]
  scopeNames?: ReadonlyMap<string, string>
}) {
  return (
    <section aria-label="受影响报告">
      <h4>受影响报告</h4>
      {reports.length ? (
        <ul style={{ overflowWrap: 'anywhere' }}>
          {reports.map((report) => (
            <li key={`${report.kind}/${report.id}`}>
              {reportNames[report.kind]} · {scopes[report.scope]}
              {report.scope_id && (
                <>
                  {scopeNames?.get(report.scope_id) && ` · ${scopeNames.get(report.scope_id)}`}
                  {' · 范围 '}
                  <code>{report.scope_id}</code>
                </>
              )}
              {' · 报告 '}
              <code>{report.id}</code>
              {report.check_ids.length > 0 && <> · 检查项 {report.check_ids.join('、')}</>}
            </li>
          ))}
        </ul>
      ) : (
        <p>暂无受影响的正式报告。</p>
      )}
    </section>
  )
}

function FrozenSource({ source }: { source: RestoreSource }) {
  return (
    <blockquote>
      <div className="small">
        章 <code>{source.chapter_id}</code> · 修订 {source.revision} · 位置 {source.start}–{source.end}
      </div>
      <pre>{source.quote}</pre>
    </blockquote>
  )
}

export function ChapterRestorePreview({
  current,
  target,
  references,
  promises,
  affected_reports,
}: ChapterRestorePreviewProps) {
  const currentLabel = `当前稿 · 修订 ${current.revision}`
  const targetLabel = `历史稿 · 修订 ${target.revision}`
  return (
    <section aria-label="章节恢复预览" style={{ minWidth: 0 }}>
      <h4>正文差异</h4>
      <p className="small">
        {currentLabel} / {targetLabel}
      </p>
      <Difference
        before={current.content}
        after={target.content}
        beforeLabel={currentLabel}
        afterLabel={targetLabel}
      />
      <p>提交恢复将保存为新章稿修订，原稿仍可在版本历史中查看。</p>
      <section aria-label="引用与伏笔影响" style={{ overflowWrap: 'anywhere' }}>
        <h4>引用与伏笔影响</h4>
        {references.length > 0 && (
          <ul>
            {references.map((reference) => (
              <li
                key={`${reference.kind}/${reference.id}/${reference.source?.start ?? ''}/${reference.source?.end ?? ''}`}
              >
                {referenceNames[reference.kind]} · {reference.title} · <code>{reference.id}</code>
                {reference.source && <FrozenSource source={reference.source} />}
              </li>
            ))}
          </ul>
        )}
        {promises.length > 0 && (
          <ul>
            {promises.map((promise) => (
              <li key={promise.id}>
                伏笔 · {promise.title} · {promiseStatuses[promise.status] ?? promise.status} ·{' '}
                <code>{promise.id}</code>
                {promise.window && (
                  <p className="small">
                    计划埋点 {promise.window.plant_chapter_id ?? '待定'} · 窗口{' '}
                    {promise.window.opens_chapter_id ?? '待定'}
                    {' 至 '}
                    {promise.window.closes_chapter_id ?? '待定'}
                  </p>
                )}
                {promise.sources.map((source) => (
                  <FrozenSource
                    source={source}
                    key={`${source.chapter_id}/${source.revision}/${source.start}/${source.end}`}
                  />
                ))}
              </li>
            ))}
          </ul>
        )}
        {references.length === 0 && promises.length === 0 && <p>暂无关联引用或伏笔。</p>}
      </section>
      <RestoreReportList reports={affected_reports} />
    </section>
  )
}
