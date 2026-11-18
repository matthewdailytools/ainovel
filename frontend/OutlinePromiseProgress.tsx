import type { components } from './api-schema'

type PromiseStage = components['schemas']['PromiseStage']
type PromiseState = PromiseStage['status'] | 'stale'

export type OutlinePromise = {
  promise_id: string
  title: string
  plant_chapter_id: string
  opens_chapter_id: string
  closes_chapter_id: string
  state: PromiseState
  fulfillment?: 'author_required'
  fulfillment_met?: boolean
  due: 'open' | 'due' | null
  stages: PromiseStage[]
}

const labels: Record<PromiseState, string> = {
  planned: '计划',
  planted: '已埋设',
  reinforced: '已推进',
  resolved: '已回收',
  retained: '作者确认保留',
  abandoned: '作者确认放弃',
  stale: '证据待复核',
}

export function OutlinePromiseProgress({
  promise,
  chapterName,
  onChapter,
}: {
  promise: OutlinePromise
  chapterName: (id: string) => string
  onChapter?: (id: string) => void
}) {
  const stages = promise.stages.filter(({ status }) => status !== 'planned')
  return (
    <>
      <p>
        {promise.title} · {labels[promise.state]}
        {promise.fulfillment === 'author_required'
          ? promise.fulfillment_met
            ? ' · 必达承诺已兑现'
            : ' · 必达承诺尚未兑现'
          : ''}
        {promise.due === 'open' ? ' · 已进入回收窗口' : promise.due === 'due' ? ' · 回收已到期' : ''}
      </p>
      <p>
        计划埋设于{chapterName(promise.plant_chapter_id)}，从{chapterName(promise.opens_chapter_id)}
        起可回收，最迟
        {onChapter ? (
          <button type="button" onClick={() => onChapter(promise.closes_chapter_id)}>
            {chapterName(promise.closes_chapter_id)}
          </button>
        ) : (
          chapterName(promise.closes_chapter_id)
        )}
        回收。
      </p>
      {stages.length > 0 && (
        <details>
          <summary>查看伏笔阶段与原文</summary>
          {promise.state === 'stale' && <p>以下为固定历史原文，当前伏笔证据待复核。</p>}
          <ol>
            {stages.map((stage, index) => (
              <li key={index}>
                <strong>{labels[stage.status]}</strong>
                {stage.note && <p>{stage.note}</p>}
                {stage.sources?.map((source, position) => (
                  <details key={position}>
                    <summary>
                      {chapterName(source.chapter_id)} · 章稿修订 {source.revision} · 原文位置 {source.start}–
                      {source.end}
                    </summary>
                    <blockquote>{source.quote}</blockquote>
                    <p className="small">
                      章节编号 <code style={{ overflowWrap: 'anywhere' }}>{source.chapter_id}</code>
                    </p>
                  </details>
                ))}
              </li>
            ))}
          </ol>
        </details>
      )}
    </>
  )
}
