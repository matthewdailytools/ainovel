import { useEffect, useRef, useState } from 'react'
import { api, label, type Artifact, type Branch, type Chapter, type Task } from './api'
import type { Notify } from './App'
import { Difference } from './Editor'

type Card = {
  id: string
  observation: string
  interpretation: string
  technique: string
  conditions: string
  exceptions: string
  evidence: string
}
type ReferencePayload = {
  source: {
    title: string
    source: string
    unit_id: string
    file_hash: string
    start: number
    end: number
    quote: string
  }
  goal: string
  cards: Card[]
  selected_ids: string[]
  plot: string
  fixed: string[]
  application_plan: string
  branch_id: string | null
  chapter_id: string | null
  results: Record<string, string>
  feedback: { text: string; candidate_id?: string; revision: number }[]
  history?: { revision: number; cards: Card[]; application_plan: string; results: Record<string, string> }[]
}
export type ReferenceSession = { id: string; revision: number; payload: ReferencePayload }
type Comparison = {
  plot_audit?: {
    status: string
    summary: string
    policy: string
    interpretation_note: string
    items: {
      id: string
      author_evidence: string
      actor: string
      action: string
      object: string
      direction: string
      result: string
      certainty: string
    }[]
    checks: { item_id: string; result: string; observed: string; evidence: string; reason: string }[]
  }
  target?: {
    kind: string
    chapter_id: string
    base_revision: number
    candidate?: { id: string; revision: number }
    content_sha256: string
  }
  rules: { rule_id: string; result: string; evidence: string; reason: string }[]
  concrete_plot_similarities: string[]
  summary: string
  overlap: {
    coverage: string
    matching: string
    hits: { quote: string; source_start: number; candidate_start: number; length: number }[]
  }
}
export function ReferenceComparison({ value }: { value: unknown }) {
  if (!value || typeof value !== 'object') return null
  const data = value as Comparison
  return (
    <section className="notice" aria-label="参考写法对照">
      <h3>参考写法对照 · 等待作者判断</h3>
      <p>{data.summary}</p>
      {data.plot_audit && (
        <details>
          <summary>
            作者情节逐项核对 ·{' '}
            {(
              { model_pass: '模型标通过', failed: '发现不符', unverified: '待核对' } as Record<string, string>
            )[data.plot_audit.status] || '待核对'}
          </summary>
          <p>{data.plot_audit.policy}</p>
          <p>{data.plot_audit.summary}</p>
          <p>{data.plot_audit.interpretation_note}</p>
          {data.plot_audit.items.map((item) => {
            const check = data.plot_audit?.checks.find((row) => row.item_id === item.id)
            return (
              <article className="fact" key={item.id}>
                <strong>
                  {item.id} · AI解释{item.certainty === 'ambiguous' ? '（存在歧义）' : '（仍须核对）'}
                </strong>
                <blockquote>{item.author_evidence}</blockquote>
                <p>
                  {[item.actor, item.action, item.object, item.direction, item.result]
                    .filter(Boolean)
                    .join(' · ')}
                </p>
                {check && (
                  <>
                    <p>本稿观察：{check.observed}</p>
                    <p>
                      {
                        (
                          { pass: '模型标一致', fail: '模型标不符', unknown: '未能核实' } as Record<
                            string,
                            string
                          >
                        )[check.result]
                      }
                      ：{check.reason}
                    </p>
                    <blockquote>{check.evidence}</blockquote>
                  </>
                )}
              </article>
            )
          })}
        </details>
      )}
      {data.target && (
        <p className="small muted">
          检查对象：{data.target.kind} · 原稿基线修订 {data.target.base_revision}
          {data.target.candidate
            ? ` · 候选 ${data.target.candidate.id.slice(0, 8)}／修订 ${data.target.candidate.revision}`
            : ''}{' '}
          · 内容哈希 {data.target.content_sha256.slice(0, 12)}。此报告仅针对该固定内容。
        </p>
      )}
      {data.rules?.map((rule) => (
        <article className="fact" key={rule.rule_id}>
          <strong>
            {rule.rule_id.slice(0, 8)} ·{' '}
            {({ pass: '已体现', fail: '未体现', unknown: '待判断' } as Record<string, string>)[rule.result] ||
              rule.result}
          </strong>
          <p>{rule.reason}</p>
          <blockquote>{rule.evidence}</blockquote>
        </article>
      ))}
      {data.concrete_plot_similarities?.map((text, index) => (
        <p key={index}>具体情节对应提示：{text}</p>
      ))}
      {data.overlap && (
        <details>
          <summary>所选参考范围内的重合检查 · {data.overlap.hits.length}处</summary>
          <p>{data.overlap.coverage}</p>
          <p>{data.overlap.matching}</p>
          {data.overlap.hits.map((hit, index) => (
            <article key={index}>
              <blockquote>{hit.quote}</blockquote>
              <small>
                选段内 UTF-16 {hit.source_start}；候选内 {hit.candidate_start}；长度 {hit.length}
              </small>
            </article>
          ))}
        </details>
      )}
    </section>
  )
}

export function ReferenceWorkspace({
  identifier,
  branch,
  account,
  modelReady,
  notify,
  onBack,
  onWriting,
}: {
  identifier: string
  branch?: Branch
  account: string
  modelReady: boolean
  notify: Notify
  onBack: () => void
  onWriting: () => void
}) {
  const [row, setRow] = useState<ReferenceSession>()
  const [draft, setDraft] = useState<ReferencePayload>()
  const [base, setBase] = useState(-1)
  const [artifacts, setArtifacts] = useState<Record<string, Artifact>>({})
  const [chapters, setChapters] = useState<Chapter[]>([])
  const [task, setTask] = useState<Task>()
  const [tasks, setTasks] = useState<Task[]>([])
  const [historical, setHistorical] = useState<Artifact>()
  const [busy, setBusy] = useState(false)
  const [plotContractCheck, setPlotContractCheck] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [feedback, setFeedback] = useState('')
  const [candidateText, setCandidateText] = useState('')
  const [candidateVersion, setCandidateVersion] = useState(-1)
  const [baseline, setBaseline] = useState('')
  const pending = useRef<{ signature: string; key: string } | null>(null)
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  async function reload() {
    const value = await api<ReferenceSession>(`/reference-sessions/${identifier}`)
    if (!mounted.current) return
    setRow(value)
    setDraft(structuredClone(value.payload))
    setBase(value.revision)
    setMessage('已载入保存版本；修改规则或方案后需保存才参与下一步。')
  }
  useEffect(() => {
    void reload().catch(notify)
  }, [identifier])
  useEffect(() => {
    let active = true
    const poll = async () => {
      try {
        const [value, savedTasks] = await Promise.all([
          api<ReferenceSession>(`/reference-sessions/${identifier}`),
          api<Task[]>(`/reference-sessions/${identifier}/tasks`),
        ])
        if (active) {
          setRow(value)
          setTasks(savedTasks)
          setTask(savedTasks.find((t) => ['queued', 'running'].includes(t.status)))
        }
      } catch (e) {
        if (active) notify(e)
      }
    }
    void poll()
    const timer = setInterval(() => void poll(), 2500)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [identifier, notify])
  const resultsKey = JSON.stringify(row?.payload.results || {})
  useEffect(() => {
    let active = true
    void Promise.all(
      Object.entries(row?.payload.results || {}).map(
        async ([key, id]) => [key, await api<Artifact>(`/artifacts/${id}`)] as const,
      ),
    )
      .then((values) => {
        if (active) setArtifacts(Object.fromEntries(values))
      })
      .catch(notify)
    return () => {
      active = false
    }
  }, [resultsKey, notify])
  useEffect(() => {
    let active = true
    setChapters([])
    if (draft?.branch_id)
      void api<Chapter[]>(`/branches/${draft.branch_id}/chapters`)
        .then((value) => {
          if (active) setChapters(value)
        })
        .catch(notify)
    return () => {
      active = false
    }
  }, [draft?.branch_id, notify])
  const candidate = artifacts.draft
  useEffect(() => {
    let active = true
    if (!candidate) return
    setCandidateText(candidate.payload.text || '')
    setCandidateVersion(candidate.candidate_revision)
    if (candidate.chapter_id)
      void api<{ revision: number; content: string }[]>(`/chapters/${candidate.chapter_id}/history`)
        .then((rows) => {
          if (active) setBaseline(rows.find((r) => r.revision === candidate.base_revision)?.content || '')
        })
        .catch(notify)
    return () => {
      active = false
    }
  }, [candidate?.id, candidate?.candidate_revision, notify])
  const running = !!task && ['queued', 'running'].includes(task.status)
  useEffect(() => {
    if (!running || !task) return
    let active = true
    const poll = async () => {
      try {
        const result = await api<Task>(`/tasks/${task.id}`)
        if (!active) return
        setTask(result)
        if (!['queued', 'running'].includes(result.status)) {
          setMessage(result.error || '本阶段结果已保存；请核对后选择下一步。')
          const fresh = await api<ReferenceSession>(`/reference-sessions/${identifier}`)
          if (active) setRow(fresh)
        }
      } catch (e) {
        if (active) notify(e)
      }
    }
    void poll()
    const timer = setInterval(() => void poll(), 2000)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [running, task?.id, identifier, notify])
  async function perform(fn: () => Promise<void>) {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await fn()
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : '操作失败，输入保留')
      notify(e)
    } finally {
      if (mounted.current) setBusy(false)
    }
  }
  function edit(values: Partial<ReferencePayload>) {
    setDraft((old) => (old ? { ...old, ...values } : old))
    setMessage('表单修改尚未保存。')
  }
  async function save() {
    if (!draft) return
    const { goal, cards, selected_ids, plot, fixed, application_plan, branch_id, chapter_id } = draft
    const value = await api<ReferenceSession>(`/reference-sessions/${identifier}`, 'PATCH', {
      base_revision: base,
      goal,
      cards,
      selected_ids,
      plot,
      fixed: fixed.map((v) => v.trim()).filter(Boolean),
      application_plan,
      branch_id,
      chapter_id,
    })
    if (!mounted.current) return
    setRow(value)
    setDraft(structuredClone(value.payload))
    setBase(value.revision)
    setMessage('已保存采用规则及本次方案。旧结果仍保留在历史中，新动作使用此版本。')
  }
  async function action(name: string, checkSaved = false) {
    if (!row || !draft) return
    const values = {
      action: name,
      base_revision: base,
      account,
      check_saved: checkSaved,
      plot_contract_check: plotContractCheck && (name === 'draft' || name === 'check'),
      candidate_id: name === 'check' && !checkSaved ? candidate?.id : null,
    }
    const signature = JSON.stringify(values)
    if (pending.current?.signature !== signature) pending.current = { signature, key: crypto.randomUUID() }
    const result = await api<Task>(`/reference-sessions/${identifier}/actions`, 'POST', {
      ...values,
      client_request_id: pending.current.key,
    })
    if (!mounted.current) return
    setTask(result)
    pending.current = null
    setMessage('任务已提交；阶段与费用单独记录，生成不会自动采纳。')
  }
  if (!row || !draft)
    return (
      <section className="card">
        <p role="status">正在载入参考会话…</p>
        <button onClick={onBack}>返回资料书库</button>
      </section>
    )
  const stale = row.revision !== base
  const editable = (p: ReferencePayload) =>
    JSON.stringify([
      p.goal,
      p.cards,
      p.selected_ids,
      p.plot,
      p.fixed,
      p.application_plan,
      p.branch_id,
      p.chapter_id,
    ])
  const dirty = editable(draft) !== editable(row.payload)
  const canRun = !busy && !running && !stale && !dirty && modelReady
  return (
    <div className="page-pad reference-workspace">
      <div className="page-heading">
        <div>
          <span className="eyebrow">从参考写法到本书原创正文</span>
          <h1>参考仿写工作区</h1>
          <p>选段 → 拆解写法 → 本书方案 → 试写 → 对照采纳</p>
        </div>
        <button onClick={onBack}>返回资料书库</button>
      </div>
      {error && (
        <p className="notice" role="alert">
          {error}
        </p>
      )}
      {stale && (
        <p className="notice" role="alert">
          会话在其他窗口更新。当前输入保留，请先复制需要的内容，再重新载入。
        </p>
      )}
      <p role="status">{message}</p>
      <section className="card">
        <h2>1. 参考范围与学习目标</h2>
        <p>
          {row.payload.source.title} · {row.payload.source.source}
        </p>
        <small>
          SHA-256 {row.payload.source.file_hash} · UTF-16 {row.payload.source.start}—{row.payload.source.end}
        </small>
        <details>
          <summary>阶段任务与历史规则版本</summary>
          {tasks.map((t) => (
            <article className="fact" key={t.id}>
              <strong>
                {t.payload.action} · {label(t.status)}
              </strong>
              <p>
                {t.error || '阶段记录已保存'} · {t.id}
              </p>
            </article>
          ))}
          {row.payload.history?.map((h) => (
            <article className="fact" key={h.revision}>
              <h3>规则和方案修订 {h.revision}（历史）</h3>
              <p>{h.application_plan || '此版本尚无应用方案'}</p>
              {h.cards.map((c) => (
                <p key={c.id}>{c.technique}</p>
              ))}
              {Object.entries(h.results).map(([stage, id]) => (
                <button
                  key={id}
                  onClick={() =>
                    void perform(async () => {
                      setHistorical(await api<Artifact>(`/artifacts/${id}`))
                    })
                  }
                >
                  查看历史 {stage} 产物
                </button>
              ))}
            </article>
          ))}
          {historical && (
            <section aria-label="历史参考产物">
              <h3>
                历史产物 · {historical.id} · {label(historical.status)}
              </h3>
              <p>仅供核对，不代表当前规则或正文的有效检查。</p>
              <pre>{JSON.stringify(historical.payload, null, 2)}</pre>
            </section>
          )}
        </details>
        <details>
          <summary>查看固定版本选段快照</summary>
          <blockquote>{row.payload.source.quote}</blockquote>
        </details>
        <label>
          想学习什么
          <textarea
            value={draft.goal}
            rows={2}
            maxLength={3000}
            onChange={(e) => edit({ goal: e.target.value })}
          />
        </label>
        <p className="small muted">
          分析发送所选原文；方案和试写使用选定规则及本书上下文；对照阶段发送选段与候选。未配置模型仍可手工整理和写作。
        </p>
        <div className="actions">
          <button disabled={busy || running} onClick={() => void perform(save)}>
            保存本次规则与方案
          </button>
          <button disabled={busy || running} onClick={() => void perform(reload)}>
            重新载入保存版本（替换表单）
          </button>
          <button
            className="primary"
            disabled={!canRun}
            onClick={() => void perform(() => action('analyze'))}
          >
            拆解选段写法
          </button>
        </div>
        {dirty && <p className="small">请先保存表单，再提交模型动作。</p>}
        {!modelReady && <p>模型账户或单价未配置，模型动作暂不可用。</p>}
        {running && (
          <div className="notice">
            <strong>{label(task!.status)}</strong>
            <p>关闭页面不取消本机任务。</p>
            <button
              onClick={() =>
                void perform(async () => {
                  await api(`/tasks/${task!.id}/cancel`, 'POST')
                })
              }
            >
              取消本阶段
            </button>
          </div>
        )}
      </section>
      <section className="card">
        <h2>2. 可编辑拆解卡与本次选用规则</h2>
        {artifacts.analyze && (
          <>
            <p>{String(artifacts.analyze.payload.coverage_note || '')}</p>
            <button
              disabled={busy || running}
              onClick={() => edit({ cards: artifacts.analyze.payload.cards as Card[], selected_ids: [] })}
            >
              载入分析卡供编辑和选择（尚未采用）
            </button>
          </>
        )}
        {!draft.cards.length && <p>尚无拆解卡。可先分析，也可手动整理。</p>}
        {draft.cards.map((card, index) => (
          <article className="fact" key={card.id}>
            <label className="check">
              <input
                type="checkbox"
                checked={draft.selected_ids.includes(card.id)}
                onChange={(e) =>
                  edit({
                    selected_ids: e.target.checked
                      ? [...draft.selected_ids, card.id]
                      : draft.selected_ids.filter((id) => id !== card.id),
                  })
                }
              />
              <span>本次采用：{card.technique}</span>
            </label>
            <details>
              <summary>编辑卡片 {index + 1}：观察、解释、写法与证据</summary>
              {(
                Object.entries({
                  observation: '原文观察',
                  interpretation: '作用解释（分析判断）',
                  technique: '可迁移写法',
                  conditions: '适用条件',
                  exceptions: '例外',
                  evidence: '逐字原文证据',
                }) as [keyof Card, string][]
              ).map(([key, title]) => (
                <label key={key}>
                  {title}
                  <textarea
                    rows={2}
                    maxLength={2000}
                    value={card[key]}
                    onChange={(e) =>
                      edit({
                        cards: draft.cards.map((c) =>
                          c.id === card.id ? { ...c, [key]: e.target.value } : c,
                        ),
                      })
                    }
                  />
                </label>
              ))}
            </details>
          </article>
        ))}
        <button
          disabled={busy || running || draft.cards.length >= 8}
          onClick={() =>
            edit({
              cards: [
                ...draft.cards,
                {
                  id: crypto.randomUUID(),
                  observation: '请填写原文观察',
                  interpretation: '请填写可能作用',
                  technique: '请填写可执行写法',
                  conditions: '请填写适用场景',
                  exceptions: '',
                  evidence: row.payload.source.quote.slice(0, 800),
                },
              ],
            })
          }
        >
          添加手工写法卡
        </button>
      </section>
      <section className="card">
        <h2>3. 本书目标与应用方案</h2>
        <p>
          当前采用目标：{draft.branch_id || '独立资料分析，尚未绑定作品'} · 章节{' '}
          {draft.chapter_id || '尚未选择'}
        </p>
        <button
          disabled={!branch || busy || running}
          onClick={() => edit({ branch_id: branch!.id, chapter_id: null })}
        >
          明确使用顶部当前工作版本：{branch?.name || '先选择作品'}
        </button>
        <label>
          本次目标章节
          <select
            value={draft.chapter_id || ''}
            onChange={(e) => edit({ chapter_id: e.target.value || null })}
          >
            <option value="">请选择章节</option>
            {chapters.map((c) => (
              <option key={c.id} value={c.id}>
                {c.position} · {c.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          本次原创情节与结果
          <textarea
            rows={3}
            value={draft.plot}
            maxLength={12_000}
            onChange={(e) => edit({ plot: e.target.value })}
          />
        </label>
        <label>
          固定条件（每行一条）
          <textarea
            rows={3}
            value={draft.fixed.join('\n')}
            onChange={(e) => edit({ fixed: e.target.value.split('\n') })}
          />
        </label>
        <label>
          <input
            type="checkbox"
            checked={plotContractCheck}
            onChange={(e) => setPlotContractCheck(e.target.checked)}
            disabled={busy || running}
          />
          试写／对照时额外核对情节角色、对象与方向（增加解析与核对调用，仍受预算保护）
        </label>
        <button
          disabled={!canRun || !draft.chapter_id || !draft.selected_ids.length || !draft.plot.trim()}
          onClick={() => void perform(() => action('plan'))}
        >
          生成本书应用方案
        </button>
        {artifacts.plan && (
          <div className="notice">
            <p>{artifacts.plan.payload.text}</p>
            {Array.isArray(artifacts.plan.payload.conflicts) &&
              artifacts.plan.payload.conflicts.map((c, i) => <p key={i}>待取舍：{String(c)}</p>)}
            <button
              disabled={busy || running}
              onClick={() => edit({ application_plan: artifacts.plan.payload.text || '' })}
            >
              载入方案供编辑确认
            </button>
          </div>
        )}
        <label>
          确认的本次应用方案
          <textarea
            rows={5}
            value={draft.application_plan}
            maxLength={12_000}
            onChange={(e) => edit({ application_plan: e.target.value })}
          />
        </label>
        <button
          className="primary"
          disabled={!canRun || !draft.application_plan.trim() || !draft.chapter_id}
          onClick={() => void perform(() => action('draft'))}
        >
          按保存方案原创试写
        </button>
        <button
          disabled={!canRun || !draft.application_plan.trim() || !draft.chapter_id}
          onClick={() => void perform(() => action('check', true))}
        >
          对照检查当前已保存手写稿
        </button>
      </section>
      <section className="card">
        <h2>4. 正文候选、差异与对照采纳</h2>
        {candidate ? (
          <>
            <p>
              {label(candidate.status)} · 来源正文修订 {candidate.base_revision} ·{' '}
              {candidate.payload.review_passed ? '本轮模型条件检查通过，作者质量待审' : '条件检查待确认'}
            </p>
            <label>
              参考试写候选
              <textarea
                rows={10}
                value={candidateText}
                onChange={(e) => setCandidateText(e.target.value)}
                readOnly={candidate.status !== 'candidate'}
              />
            </label>
            <details>
              <summary>对照原稿与候选差异</summary>
              <Difference before={baseline} after={candidateText} beforeLabel="生成时原稿" />
            </details>
            <ReferenceComparison value={candidate.payload.reference_comparison} />
            {!!candidate.payload.previous_reference_comparison && (
              <details>
                <summary>编辑前的对照结果（已失效）</summary>
                <ReferenceComparison value={candidate.payload.previous_reference_comparison} />
              </details>
            )}
            <div className="actions">
              <button
                disabled={
                  busy ||
                  running ||
                  candidate.status !== 'candidate' ||
                  candidateText === candidate.payload.text
                }
                onClick={() =>
                  void perform(async () => {
                    await api(`/artifacts/${candidate.id}`, 'PATCH', {
                      text: candidateText,
                      candidate_revision: candidateVersion,
                    })
                    const value = await api<Artifact>(`/artifacts/${candidate.id}`)
                    setArtifacts((old) => ({ ...old, draft: value }))
                  })
                }
              >
                保存候选编辑（旧检查失效）
              </button>
              <button
                disabled={!canRun || candidateText !== candidate.payload.text}
                onClick={() => void perform(() => action('check'))}
              >
                重新对照候选
              </button>
              <button
                className="primary"
                disabled={
                  busy ||
                  running ||
                  candidate.status !== 'candidate' ||
                  candidateText !== candidate.payload.text
                }
                onClick={() =>
                  void perform(async () => {
                    await api(`/artifacts/${candidate.id}/accept`, 'POST', {
                      idempotency_key: 'reference-' + candidate.id,
                      candidate_revision: candidate.candidate_revision,
                    })
                    setArtifacts((old) => ({ ...old, draft: { ...candidate, status: 'accepted' } }))
                    setMessage('已采纳为新正文修订，原稿历史保留。故事记录可按需整理。')
                  })
                }
              >
                对照后采纳到目标正文
              </button>
              <button
                disabled={busy || running || candidate.status !== 'candidate'}
                onClick={() =>
                  void perform(async () => {
                    const value = await api<Artifact>(`/artifacts/${candidate.id}/reject`, 'POST', {
                      candidate_revision: candidate.candidate_revision,
                    })
                    setArtifacts((old) => ({ ...old, draft: value }))
                    setMessage('已拒绝候选，原稿保持。')
                  })
                }
              >
                拒绝候选
              </button>
            </div>
          </>
        ) : (
          <p>可先采用方案亲自写作，也可试写生成候选。生成不会自动采纳。</p>
        )}
        {artifacts.check && (
          <details>
            <summary>独立对照报告（核对固定对象及版本）</summary>
            <ReferenceComparison value={artifacts.check.payload} />
          </details>
        )}
        <button onClick={onWriting}>回到写作页继续编辑或恢复版本</button>
      </section>
      <section className="card">
        <h2>5. 作者反馈与历史</h2>
        <label>
          本次实际帮助、问题及采纳原因
          <textarea
            rows={3}
            value={feedback}
            maxLength={4000}
            onChange={(e) => setFeedback(e.target.value)}
          />
        </label>
        <button
          disabled={busy || !feedback.trim() || stale}
          onClick={() =>
            void perform(async () => {
              const value = await api<ReferenceSession>(
                `/reference-sessions/${identifier}/feedback`,
                'POST',
                { base_revision: base, text: feedback, candidate_id: candidate?.id || null },
              )
              setRow(value)
              setDraft((old) => (old ? { ...old, feedback: value.payload.feedback } : old))
              setFeedback('')
              setMessage('反馈已记录，不自动改变本书方向或偏好。')
            })
          }
        >
          记录反馈
        </button>
        {row.payload.feedback.map((f, i) => (
          <blockquote key={i}>
            {f.text} · 会话版本 {f.revision}
          </blockquote>
        ))}
        <small>
          会话 {row.id} · 规则和方案修订 {row.revision}；旧任务、原稿和中断输出保留。
        </small>
      </section>
    </div>
  )
}
