import { useEffect, useState } from 'react'
import { api, type Branch, type Chapter, type Fact } from './api'
import type { Notify } from './App'
import { GenreSettings } from './GenreProfile'
import { AuthorIntentPanel } from './AuthorIntent'
import { FinalizationPanel } from './FinalizationPanel'
import { StoryGraph } from './StoryGraph'

type Completion = {
  chapter_count: number
  expected: number
  chars: number
  missing_or_stale: number[]
  length_failures: number[]
  author_finalized: boolean
  production_complete: boolean
  accepted: boolean
  assessments: { category: string; result: string; note: string; minutes: string }[]
  model_report: unknown
  note: string
}
type Feedback = {
  id: string
  title: string
  source_revision: number
  category: string
  text: string
  source_quote: string
}
export function Records({
  branch,
  account,
  modelReady,
  notify,
}: {
  branch: Branch
  account: string
  modelReady: boolean
  notify: Notify
}) {
  const [facts, setFacts] = useState<Fact[]>([])
  const [chapters, setChapters] = useState<Chapter[]>([])
  const [chapterId, setChapterId] = useState('')
  const [query, setQuery] = useState('')
  const [at, setAt] = useState(100)
  const [feedback, setFeedback] = useState<Feedback[]>([])
  const [category, setCategory] = useState('事实错误')
  const [feedbackText, setFeedbackText] = useState('')
  const [quote, setQuote] = useState('')
  const [source, setSource] = useState('')
  const [rawHits, setRawHits] = useState<(Chapter & { snippet: string; start: number; end: number })[]>([])
  const [completion, setCompletion] = useState<Completion>()
  const [assessment, setAssessment] = useState('creative')
  const [result, setResult] = useState('unverified')
  const [assessmentNote, setAssessmentNote] = useState('')
  const [minutes, setMinutes] = useState<Record<string, string>>({})
  const [backups, setBackups] = useState<{ id: string; bytes: number }[]>([])
  const [backupResult, setBackupResult] = useState('')
  const [busy, setBusy] = useState(false)
  const [factFields, setFactFields] = useState({
    subject: '',
    kind: 'event',
    statement: '',
    truth: 'fact',
    story_time: '',
    evidence: '',
  })
  const [definition, setDefinition] = useState({
    kind: 'world',
    text: '',
  })
  const [definitions, setDefinitions] = useState<
    {
      id: string
      kind: string
      payload: { text: string; audience?: string; genres?: string[]; mechanisms?: string[] }
    }[]
  >([])
  const [storyRevision, setStoryRevision] = useState(0)
  const keys = ['准备', '查找', '提示调整', '等待', '核查', '返修', '整理保存', '基线总计']
  async function refresh() {
    const [f, c, b, a, z, context] = await Promise.all([
      api<Fact[]>(`/branches/${branch.id}/facts`),
      api<Chapter[]>(`/branches/${branch.id}/chapters`),
      api<Feedback[]>(`/branches/${branch.id}/feedback`),
      api<Completion>(`/branches/${branch.id}/completion`),
      api<{ id: string; bytes: number }[]>('/backups'),
      api<{ branch: Branch; definitions: typeof definitions }>(`/branches/${branch.id}/context`),
    ])
    setFacts(f)
    setChapters(c)
    setChapterId((current) => current || c[0]?.id || '')
    setFeedback(b)
    setCompletion(a)
    setBackups(z)
    setDefinitions(context.definitions)
    setStoryRevision(context.branch.story_revision)
  }
  useEffect(() => {
    void refresh().catch(notify)
  }, [branch.id])
  async function perform(fn: () => Promise<unknown>) {
    if (busy) return
    setBusy(true)
    try {
      await fn()
    } catch (e) {
      notify(e)
    } finally {
      setBusy(false)
    }
  }
  const displayed = facts.filter(
    (f) => f.position <= at && (!query || `${f.subject}${f.statement}${f.story_time}`.includes(query)),
  )
  return (
    <div className="page-pad records-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">事实有出处，判断有记录</span>
          <h1>故事与审阅</h1>
          <p>{branch.name} · 事实、人物认知、承诺与作者判断分别保留。</p>
        </div>
        <button onClick={() => void perform(refresh)} disabled={busy}>
          刷新当前版本
        </button>
      </div>
      <AuthorIntentPanel
        branchId={branch.id}
        novelId={branch.novel_id}
        account={account}
        modelReady={modelReady}
        notify={notify}
        onSaved={refresh}
      />
      <GenreSettings key={branch.id} branchId={branch.id} notify={notify} onSaved={refresh} />
      <StoryGraph key={'story-' + branch.id} branchId={branch.id} notify={notify} onSaved={refresh} />
      <div className="records-grid">
        <section className="card">
          <h2>故事状态与影响</h2>
          <div className="form-grid">
            <label>
              人物、事项或时点关键词
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="例如：周铎、伤势、第三天"
              />
            </label>
            <label>
              查看至第几章
              <input
                type="number"
                min={1}
                max={100}
                value={at}
                onChange={(e) => setAt(Number(e.target.value))}
              />
            </label>
          </div>
          <p className="small muted">
            按章序与文字关键词筛选，不按真实故事时间推算状态。未知时点保持未知；失效记录保留出处，不进入后续生成上下文。
          </p>
          <button
            disabled={!query || busy}
            onClick={() =>
              void perform(async () => {
                const result = await api<{ hits: typeof rawHits }>(
                  `/branches/${branch.id}/search?q=${encodeURIComponent(query)}&before=${at + 1}`,
                )
                setRawHits(result.hits)
              })
            }
          >
            在指定范围搜索正文原文
          </button>
          {rawHits.map((hit) => (
            <article className="fact" key={hit.id}>
              <strong>
                第 {hit.position} 章 · {hit.title}／修订 {hit.revision}
              </strong>
              <p>{hit.snippet}</p>
              <small>
                UTF-16 {hit.start}—{hit.end} · {hit.stale ? '受前文影响，待复检' : '当前版本'}
              </small>
              <button
                onClick={() =>
                  void perform(async () => {
                    const history = await api<{ revision: number; content: string }[]>(
                      `/chapters/${hit.id}/history`,
                    )
                    setSource(
                      history.find((r) => r.revision === hit.revision)?.content ||
                        '来源修订不可用，请检查数据。',
                    )
                  })
                }
              >
                查看命中时的来源版本
              </button>
            </article>
          ))}
          {displayed.length ? (
            displayed.map((f) => (
              <article className={'fact ' + (f.stale ? 'stale' : '')} key={f.id}>
                <div className="section-title">
                  <strong>
                    {f.subject} · {f.kind}
                  </strong>
                  <span className="badge">
                    {f.stale ? '已失效，待复检' : '有效'} · {f.truth}
                  </span>
                </div>
                <p>{f.statement}</p>
                <small>
                  第 {f.position} 章／修订 {f.source_revision} · {f.story_time}
                </small>
                <blockquote>{f.evidence}</blockquote>
                <div className="actions">
                  <button
                    onClick={() =>
                      void perform(async () => {
                        const rows = await api<{ revision: number; content: string }[]>(
                          `/chapters/${f.chapter_id}/history`,
                        )
                        const original = rows.find((r) => r.revision === f.source_revision)
                        if (!original) throw new Error('来源修订不存在，请检查数据完整性。')
                        setSource(
                          `第 ${f.position} 章 · 修订 ${f.source_revision}\nUTF-16 ${f.evidence_start}—${f.evidence_end}\n\n${original.content}`,
                        )
                      })
                    }
                  >
                    查看来源修订
                  </button>
                  <button
                    disabled={!!f.stale || busy}
                    onClick={() =>
                      void perform(async () => {
                        await api(`/facts/${f.id}`, 'DELETE')
                        await refresh()
                      })
                    }
                  >
                    标记此记录无效
                  </button>
                </div>
              </article>
            ))
          ) : (
            <p className="empty-note">暂无匹配记录。可以手动录入，或在写作页生成事实候选后采纳。</p>
          )}
          <details>
            <summary>手动录入／更正一条事实</summary>
            <form
              onSubmit={(event) => {
                event.preventDefault()
                void perform(async () => {
                  const c = await api<Chapter>(`/chapters/${chapterId}`)
                  await api('/facts', 'POST', {
                    ...factFields,
                    chapter_id: c.id,
                    source_revision: c.revision,
                  })
                  await refresh()
                })
              }}
            >
              <label>
                来源章节
                <select value={chapterId} onChange={(e) => setChapterId(e.target.value)}>
                  {chapters.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.position} · {c.title}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                主体
                <input
                  value={factFields.subject}
                  onChange={(e) => setFactFields({ ...factFields, subject: e.target.value })}
                  required
                />
              </label>
              <label>
                类型
                <select
                  value={factFields.kind}
                  onChange={(e) => setFactFields({ ...factFields, kind: e.target.value })}
                >
                  {[
                    ['event', '事件'],
                    ['knowledge', '人物认知'],
                    ['capability', '能力'],
                    ['resource', '资源'],
                    ['relationship', '关系'],
                    ['promise', '伏笔与承诺'],
                    ['world', '世界事实'],
                  ].map(([v, t]) => (
                    <option key={v} value={v}>
                      {t}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                事实／误信／未知／计划
                <select
                  value={factFields.truth}
                  onChange={(e) => setFactFields({ ...factFields, truth: e.target.value })}
                >
                  {[
                    ['fact', '事实'],
                    ['belief', '人物相信'],
                    ['uncertain', '未确定'],
                    ['plan', '未来计划'],
                  ].map(([v, t]) => (
                    <option key={v} value={v}>
                      {t}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                故事时点
                <input
                  value={factFields.story_time}
                  onChange={(e) => setFactFields({ ...factFields, story_time: e.target.value })}
                  placeholder="未知时填写「未知」"
                  required
                />
              </label>
              <label>
                陈述
                <textarea
                  value={factFields.statement}
                  onChange={(e) => setFactFields({ ...factFields, statement: e.target.value })}
                  required
                />
              </label>
              <label>
                逐字原文依据
                <textarea
                  value={factFields.evidence}
                  onChange={(e) => setFactFields({ ...factFields, evidence: e.target.value })}
                  required
                />
              </label>
              <button className="primary" disabled={busy || !chapterId}>
                保存事实与来源
              </button>
            </form>
          </details>
        </section>
        <section className="card">
          <h2>作者反馈与定向修订</h2>
          <form
            onSubmit={(event) => {
              event.preventDefault()
              void perform(async () => {
                await api('/feedback', 'POST', {
                  chapter_id: chapterId,
                  category,
                  text: feedbackText,
                  source_quote: quote,
                })
                setFeedbackText('')
                setQuote('')
                await refresh()
              })
            }}
          >
            <label>
              对应章节
              <select value={chapterId} onChange={(e) => setChapterId(e.target.value)}>
                {chapters.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.position} · {c.title}
                  </option>
                ))}
              </select>
            </label>
            <label>
              反馈性质
              <select value={category} onChange={(e) => setCategory(e.target.value)}>
                {['事实错误', '理解问题', '审美偏好', '待判断'].map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            </label>
            <label>
              原文引用（可选）
              <textarea value={quote} onChange={(e) => setQuote(e.target.value)} rows={2} />
            </label>
            <label>
              反馈内容
              <textarea
                value={feedbackText}
                onChange={(e) => setFeedbackText(e.target.value)}
                rows={4}
                required
              />
            </label>
            <button className="primary" disabled={busy || !chapterId}>
              记录反馈
            </button>
          </form>
          <p className="small muted">
            反馈不会自动改动作者方向。复制修订要求后，可在写作页按作者选定的范围生成候选。
          </p>
          {feedback.map((f) => (
            <article className="fact" key={f.id}>
              <strong>
                {f.category} · {f.title}／修订 {f.source_revision}
              </strong>
              <blockquote>{f.source_quote}</blockquote>
              <p>{f.text}</p>
              <button
                onClick={() =>
                  void perform(async () => {
                    await navigator.clipboard.writeText(
                      `依据反馈定向修订，保留作者原定情节与结果。反馈性质：${f.category}\n原文：${f.source_quote}\n要求：${f.text}`,
                    )
                    setSource('修订要求已复制，可在写作页粘贴并选择作用范围。')
                  })
                }
              >
                复制定向修订要求
              </button>
            </article>
          ))}
        </section>
      </div>
      <section className="card">
        <h2>其他已采用设定</h2>
        <p>
          按需维护人物、世界与粗纲。创作约定和题材在上方独立维护。新整书版本可继承当前已采用设定，已有正文与已发生事实不会被复制为另一版本的生成结果。
        </p>
        <details>
          <summary>手动编写或调整设定</summary>
          <form
            onSubmit={(event) => {
              event.preventDefault()
              void perform(async () => {
                await api(`/branches/${branch.id}/definitions`, 'POST', {
                  ...definition,
                  base_story_revision: storyRevision,
                })
                setDefinition({ ...definition, text: '' })
                await refresh()
              })
            }}
          >
            <div className="form-grid">
              <label>
                设定类型
                <select
                  value={definition.kind}
                  onChange={(e) => setDefinition({ ...definition, kind: e.target.value })}
                >
                  {[
                    ['world', '世界与社会规则'],
                    ['characters', '人物档案与关系'],
                    ['outline', '粗纲、多线与伏笔安排'],
                  ].map(([v, t]) => (
                    <option key={v} value={v}>
                      {t}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <label>
              设定正文与适用例外
              <textarea
                rows={5}
                value={definition.text}
                onChange={(e) => setDefinition({ ...definition, text: e.target.value })}
                required
                maxLength={20_000}
              />
            </label>
            <button className="primary" disabled={busy}>
              保存并采用此版本
            </button>
          </form>
        </details>
        {definitions.map((d) => (
          <details key={d.id}>
            <summary>
              {(
                {
                  intent: '创作意图',
                  reader: '读者与题材',
                  world: '世界',
                  characters: '人物',
                  outline: '粗纲',
                } as Record<string, string>
              )[d.kind] || d.kind}{' '}
              · 已采用{d.payload.audience ? ' · ' + d.payload.audience : ''} {d.payload.genres?.join('、')}
            </summary>
            <pre>{d.payload.text}</pre>
          </details>
        ))}
      </section>
      {source && (
        <section className="card source-review">
          <div className="section-title">
            <h2>来源与定位</h2>
            <button onClick={() => setSource('')}>关闭来源</button>
          </div>
          <pre>{source}</pre>
        </section>
      )}
      <FinalizationPanel
        branchId={branch.id}
        refreshVersion={storyRevision}
        notify={notify}
        onFinalized={refresh}
      />
      <section className="card acceptance">
        <div className="section-title">
          <h2>整书验收</h2>
          <span className="badge">
            {completion?.accepted
              ? '作者验收已通过'
              : completion?.author_finalized
                ? '作者已确认定稿，工具验收另行记录'
                : completion?.production_complete
                  ? '生成完成，待作者验收'
                  : '尚未满足整书完成条件'}
          </span>
        </div>
        {completion && (
          <>
            <p>
              {completion.chapter_count} / {completion.expected} 章 · {completion.chars}{' '}
              字符。空稿或受影响章节：{completion.missing_or_stale.join('、') || '无'}；字数未达配置：
              {completion.length_failures.join('、') || '无'}。
            </p>
            <p>{completion.note}</p>
            <details>
              <summary>模型完结检查与覆盖范围</summary>
              <pre>{JSON.stringify(completion.model_report, null, 2)}</pre>
            </details>
            <div className="assessment-list">
              {completion.assessments
                .filter((a) => ['engineering', 'creative', 'benefit'].includes(a.category))
                .map((a) => (
                  <div className="notice" key={a.category}>
                    <strong>
                      {
                        (
                          {
                            engineering: '工程可靠性',
                            creative: '创作质量',
                            benefit: '实际使用收益',
                          } as Record<string, string>
                        )[a.category]
                      }{' '}
                      · {a.result}
                    </strong>
                    <p>{a.note}</p>
                    <small>{a.minutes}</small>
                  </div>
                ))}
            </div>
          </>
        )}
        <form
          onSubmit={(event) => {
            event.preventDefault()
            void perform(async () => {
              await api('/assessments', 'POST', {
                branch_id: branch.id,
                category: assessment,
                result,
                note: assessmentNote,
                minutes: Object.fromEntries(
                  Object.entries(minutes)
                    .filter(([, v]) => v !== '')
                    .map(([k, v]) => [k, Number(v)]),
                ),
              })
              setAssessmentNote('')
              await refresh()
            })
          }}
        >
          <div className="form-grid">
            <label>
              本次验收层
              <select value={assessment} onChange={(e) => setAssessment(e.target.value)}>
                <option value="engineering">工程可靠性</option>
                <option value="creative">创作质量</option>
                <option value="benefit">实际使用收益</option>
              </select>
            </label>
            <label>
              作者判定
              <select value={result} onChange={(e) => setResult(e.target.value)}>
                <option value="unverified">未验证</option>
                <option value="failed">未通过</option>
                <option value="passed">通过</option>
              </select>
            </label>
          </div>
          <label>
            实际证据、判断与剩余问题
            <textarea
              value={assessmentNote}
              onChange={(e) => setAssessmentNote(e.target.value)}
              required
              rows={4}
              placeholder="写明具体段落、固定条件、严重矛盾、对照任务和额外投入；模型评分只作辅助。"
            />
          </label>
          <details>
            <summary>记录工具总投入与基线（分钟）</summary>
            <div className="form-grid">
              {keys.map((k) => (
                <label key={k}>
                  {k}
                  <input
                    type="number"
                    min={0}
                    step="0.1"
                    value={minutes[k] || ''}
                    onChange={(e) => setMinutes({ ...minutes, [k]: e.target.value })}
                  />
                </label>
              ))}
            </div>
            <p>
              总投入包含准备、查找、提示调整、等待、核查、返修、整理保存。对照任务应同难度、交替顺序，并注明学习效应。
            </p>
          </details>
          <button className="primary" disabled={busy}>
            将验收绑定到当前故事修订
          </button>
        </form>
      </section>
      <section className="card">
        <div className="section-title">
          <h2>备份与恢复演练</h2>
          <button
            className="primary"
            disabled={busy}
            onClick={() =>
              void perform(async () => {
                const b = await api<{ id: string }>('/backups', 'POST')
                const check = await api(`/backups/${b.id}/verify`, 'POST')
                setBackupResult(JSON.stringify(check, null, 2))
                await refresh()
              })
            }
          >
            创建备份并实际演练恢复
          </button>
        </div>
        <p>
          备份包含正文、历史、资料原件和费用证据。恢复演练在隔离目录进行，正式替换需停止服务后使用命令行；当前费用账本始终保留。
        </p>
        {backups.slice(0, 5).map((b) => (
          <div className="catalog-row" key={b.id}>
            <span>
              {b.id} · {(b.bytes / 1024).toFixed(1)} KiB
            </span>
            <button
              disabled={busy}
              onClick={() =>
                void perform(async () => {
                  setBackupResult(JSON.stringify(await api(`/backups/${b.id}/verify`, 'POST'), null, 2))
                })
              }
            >
              演练恢复此备份
            </button>
          </div>
        ))}
        {backupResult && <pre role="status">{backupResult}</pre>}
      </section>
    </div>
  )
}
