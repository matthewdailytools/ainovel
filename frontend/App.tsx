import { useCallback, useEffect, useRef, useState } from 'react'
import { api, startSession, type Account, type Branch, type Config, type Novel } from './api'
import { Writing } from './Writing'
import { LibraryPage } from './Library'
import { WorkflowPage } from './Workflow'
import type { SevenStageEntry } from './SevenStageRunPanel'
import { Records } from './Records'
import { Ideation } from './Ideation'
import { ConceptionWorkspace } from './ConceptionWorkspace'
import { OutlineWorkspace } from './OutlineWorkspace'
import { ManuscriptExchange } from './ManuscriptExchange'
import type { ConceptionDestination, ConceptionHandoff } from './conception'
import { confirmLeave } from './leaveGuard'
import type { FeedbackHandoff } from './feedbackRevision'
import { CreationAssistantLayout } from './WorkbenchAssistant'

export type Notify = (error: unknown) => void
const benches = [
  ['project', '立项', 'ideation', 'M1'],
  ['reference', '拆解', 'library', 'M2'],
  ['settings', '设定', 'ideation', 'M3'],
  ['outline', '大纲', 'outline', 'M4'],
  ['writing', '写作', 'writing', 'M5'],
  ['inspection', '校验', 'records', 'M6'],
  ['completion', '完本', 'records', 'M7'],
] as const
type Bench = (typeof benches)[number][0]
type NewWorkStart = 'manual' | 'idea'
const creationStarts = [
  ['manual', '逐台创作'],
  ['idea', '从创意开始'],
  ['relay', '从已完成阶段接力'],
] as const

export function App() {
  const [ready, setReady] = useState(false)
  const [error, setError] = useState('')
  const [config, setConfig] = useState<Config>()
  const [novels, setNovels] = useState<Novel[]>([])
  const [novelId, setNovelId] = useState('')
  const [branches, setBranches] = useState<Branch[]>([])
  const [branchId, setBranchId] = useState('')
  const selection = useRef({ novelId, branchId, generation: 0 })
  if (selection.current.novelId !== novelId || selection.current.branchId !== branchId)
    selection.current = { novelId, branchId, generation: selection.current.generation + 1 }
  const selectionGeneration = selection.current.generation
  const [page, setPage] = useState('writing')
  const navigationGeneration = useRef(0)
  const [bench, setBench] = useState<{ id: Bench; open?: boolean }>()
  const [runEntry, setRunEntry] = useState<SevenStageEntry & { branchId: string }>()
  const [quickConception, setQuickConception] = useState(false)
  const [conceptionHandoff, setConceptionHandoff] = useState<ConceptionHandoff>()
  const [workflowMode, setWorkflowMode] = useState<'assisted' | 'automatic'>('assisted')
  const [feedbackHandoff, setFeedbackHandoff] = useState<FeedbackHandoff>()
  const [outlineHandoff, setOutlineHandoff] = useState<{
    branchId: string
    chapterId: string
    reportId?: string
  }>()
  const [account, setAccount] = useState('openai_1')
  const [title, setTitle] = useState('')
  const [creating, setCreating] = useState(false)
  const [newWorkStart, setNewWorkStart] = useState<NewWorkStart>()
  const createDisclosure = useRef<HTMLDetailsElement>(null)
  const newWorkTitle = useRef<HTMLInputElement>(null)
  const firstWorkTitle = useRef<HTMLInputElement>(null)
  const [settings, setSettings] = useState(false)
  const [dataDir, setDataDir] = useState('')
  const notify: Notify = useCallback((e) => setError(e instanceof Error ? e.message : String(e)), [])
  const refreshConfig = useCallback(async () => setConfig(await api<Config>('/config')), [])
  const refreshBranches = useCallback(async (id: string, select?: string, generation?: number) => {
    const rows = await api<Branch[]>(`/novels/${id}/branches`)
    if (
      selection.current.novelId !== id ||
      (generation !== undefined && selection.current.generation !== generation)
    )
      return
    setBranches(rows)
    setBranchId((current) => select || (rows.some((b) => b.id === current) ? current : rows[0]?.id || ''))
  }, [])
  useEffect(() => {
    let active = true
    void (async () => {
      try {
        const session = await startSession()
        const [rows, configuration] = await Promise.all([api<Novel[]>('/novels'), api<Config>('/config')])
        if (!active) return
        setDataDir(session.data_dir)
        setConfig(configuration)
        setNovels(rows)
        const last = localStorage.getItem('ainovel-novel')
        setNovelId(rows.find((n) => n.id === last)?.id || rows[0]?.id || '')
        setAccount(configuration.accounts.find((a) => a.configured && a.priced)?.id || 'openai_1')
        setReady(true)
      } catch (e) {
        if (active) notify(e)
      }
    })()
    return () => {
      active = false
    }
  }, [notify])
  useEffect(() => {
    if (!novelId) return
    localStorage.setItem('ainovel-novel', novelId)
    let active = true
    void api<Branch[]>(`/novels/${novelId}/branches`)
      .then((rows) => {
        if (!active) return
        setBranches(rows)
        const last = localStorage.getItem('ainovel-branch-' + novelId)
        setBranchId(rows.find((b) => b.id === last)?.id || rows[0]?.id || '')
      })
      .catch(notify)
    return () => {
      active = false
    }
  }, [novelId, notify])
  useEffect(() => {
    if (novelId && branchId) localStorage.setItem('ainovel-branch-' + novelId, branchId)
  }, [novelId, branchId])
  useEffect(() => {
    if (!ready) return
    const timer = setInterval(() => void refreshConfig().catch(notify), 10_000)
    return () => clearInterval(timer)
  }, [ready, refreshConfig, notify])
  function goToPage(id: string) {
    navigationGeneration.current += 1
    setPage(id)
  }
  function navigate(id: string) {
    if (id !== page && confirmLeave()) {
      setBench(undefined)
      setRunEntry(undefined)
      setOutlineHandoff(undefined)
      goToPage(id)
    }
  }
  const novel = novels.find((n) => n.id === novelId)
  const branch = branches.find((b) => b.id === branchId && b.novel_id === novelId)
  useEffect(() => {
    if (!bench || !branch || !benches.some(([id, , destination]) => id === bench.id && destination === page))
      return
    const section = document.getElementById('bench-' + bench.id)
    section?.scrollIntoView({ block: 'start' })
    section?.focus({ preventScroll: true })
  }, [bench, branch?.id, page])
  function showBench(id: Bench, destination: string, open = false) {
    if ((destination !== page || quickConception) && !confirmLeave()) return
    setQuickConception(false)
    setOutlineHandoff(undefined)
    setRunEntry(undefined)
    setBench({ id, open })
    goToPage(destination)
  }
  function openNewWork(start: NewWorkStart) {
    if (creating) return
    setNewWorkStart(start)
    if (!novel && firstWorkTitle.current) {
      firstWorkTitle.current.focus()
      return
    }
    const disclosure = createDisclosure.current
    if (disclosure) disclosure.open = true
    newWorkTitle.current?.focus()
  }
  function chooseCreationStart(start: (typeof creationStarts)[number][0]) {
    if (!branch) {
      if (start !== 'relay') openNewWork(start)
      return
    }
    if (start === 'manual') {
      showBench('project', 'ideation', true)
      return
    }
    if (page !== 'workflow' && !confirmLeave()) return
    setBench(undefined)
    setOutlineHandoff(undefined)
    setRunEntry({ mode: start, branchId: branch.id })
    goToPage('workflow')
  }
  function openConceptionWorkspace() {
    if (!confirmLeave()) return
    setQuickConception(false)
    setRunEntry(undefined)
    goToPage('ideation')
  }
  function openConceptionDestination(destination: ConceptionDestination, handoff?: ConceptionHandoff) {
    if (!confirmLeave()) return
    setConceptionHandoff(handoff)
    if (destination === 'writing') {
      setFeedbackHandoff(undefined)
      goToPage('writing')
    } else {
      setWorkflowMode(destination)
      setRunEntry(undefined)
      goToPage('workflow')
    }
  }
  async function createNovel(event: React.FormEvent) {
    event.preventDefault()
    if (creating || !title.trim()) return
    const focusWasInDisclosure = createDisclosure.current?.contains(document.activeElement)
    setCreating(true)
    const generation = selection.current.generation
    const routeGeneration = navigationGeneration.current
    const submittedTitle = title
    const requestedStart = newWorkStart
    try {
      const result = await api<{ id: string; branch_id: string }>('/novels', 'POST', {
        title: submittedTitle.trim(),
        brief: '',
      })
      setNovels(await api('/novels'))
      const shouldOpen =
        selection.current.generation === generation &&
        (!requestedStart || navigationGeneration.current === routeGeneration) &&
        confirmLeave()
      if (shouldOpen) {
        setBranches([])
        setBranchId('')
        setNovelId(result.id)
        setQuickConception(false)
        setOutlineHandoff(undefined)
        setBench(requestedStart === 'manual' ? { id: 'project', open: true } : undefined)
        setRunEntry(requestedStart === 'idea' ? { mode: 'idea', branchId: result.branch_id } : undefined)
        goToPage(
          requestedStart === 'idea' ? 'workflow' : requestedStart === 'manual' ? 'ideation' : 'writing',
        )
      }
      setTitle((current) => (current === submittedTitle ? '' : current))
      setNewWorkStart(undefined)
      const disclosure = createDisclosure.current
      if (disclosure?.open) {
        const active = document.activeElement
        // Chromium can move focus to the document as soon as the submit button is disabled.
        const returnFocus = disclosure.contains(active) || (focusWasInDisclosure && active === document.body)
        disclosure.open = false
        if (returnFocus && (!shouldOpen || !requestedStart)) disclosure.querySelector('summary')?.focus()
      }
    } catch (e) {
      notify(e)
    } finally {
      setCreating(false)
    }
  }
  const stage =
    benches.find(([id, , destination]) => id === bench?.id && destination === page)?.[3] ??
    benches.find(([, , destination]) => destination === page)?.[3]
  const selectedAccount = config?.accounts.find((a) => a.id === account)
  const selectedConception = conceptionHandoff?.branchId === branch?.id ? conceptionHandoff : undefined
  return (
    <>
      <header className="topbar">
        <a className="brand" href="#main" aria-label="ainovel 工作台">
          <span className="brand-mark">文</span>
          <strong>ainovel</strong>
          <span className="local-label">本机创作</span>
        </a>
        <nav aria-label="主要页面">
          {[
            ['writing', '写作'],
            ['ideation', '小说构思'],
            ['outline', '大纲与台账'],
            ['library', '资料书库'],
            ['workflow', '整书创作'],
            ['records', '故事与审阅'],
          ].map(([id, name]) => (
            <button
              key={id}
              className={page === id ? 'nav active' : 'nav'}
              aria-current={page === id ? 'page' : undefined}
              onClick={() => navigate(id)}
            >
              {name}
            </button>
          ))}
        </nav>
        <button className="budget-button" onClick={() => setSettings(!settings)} aria-expanded={settings}>
          {config?.budget.map((b) => (
            <span key={b.provider}>
              {b.currency === 'USD' ? '$' : '¥'}
              {(b.settled + b.reserved_unknown).toFixed(2)} / {b.limit}
            </span>
          ))}
          <span>账户与预算</span>
        </button>
      </header>
      {error && (
        <div className="alert" role="alert">
          <span>{error}</span>
          <button onClick={() => setError('')}>关闭提示</button>
        </div>
      )}
      {!ready ? (
        <main className="empty" id="main">
          <h1>连接本机工作台</h1>
          <p>{error || '正在读取作品与配置…'}</p>
          <button onClick={() => location.reload()}>重新连接</button>
        </main>
      ) : (
        <>
          {settings && (
            <section className="settings" aria-label="账户与预算">
              <div>
                <h2>模型账户与共享预算</h2>
                <p>
                  两个 OpenAI Key 共用 USD 20；DeepSeek 单独 CNY
                  80。余额为本应用累计用量的保守估算，未知费用继续占用预留。更换 Key、恢复稿件均不清零。
                </p>
                <p className="muted">数据位置：{dataDir}。配置更改后重启本机服务和 Worker。</p>
              </div>
              <div className="account-grid">
                {config?.accounts.map((a) => (
                  <div className="card" key={a.id}>
                    <strong>{a.id}</strong>
                    <span>{a.model}</span>
                    <span>
                      {a.configured ? '密钥已配置' : '密钥未配置'} ·{' '}
                      {a.priced ? '单价已配置' : '缺少单价，禁止调用'}
                    </span>
                    <small>
                      每百万 Token：输入 {a.input_rate}／输出 {a.output_rate} {a.currency}
                    </small>
                    <a href={a.price_source} target="_blank" rel="noreferrer">
                      配置单价依据
                    </a>
                  </div>
                ))}
              </div>
              <details>
                <summary>调用记录与费用预留</summary>
                {config?.budget.map((b) => (
                  <div key={b.provider}>
                    <p>
                      {b.provider}：已结算估算 {b.settled.toFixed(4)}；预留／未知{' '}
                      {b.reserved_unknown.toFixed(4)}；剩余 {b.remaining.toFixed(4)} {b.currency}
                    </p>
                    <div className="table-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th>账户</th>
                            <th>模型</th>
                            <th>状态</th>
                            <th>输入／输出 Token</th>
                            <th>估算／预留</th>
                          </tr>
                        </thead>
                        <tbody>
                          {b.calls.map((c) => (
                            <tr key={c.id}>
                              <td>{c.account}</td>
                              <td>{c.model}</td>
                              <td>{c.status}</td>
                              <td>
                                {c.input_tokens ?? '未知'} / {c.output_tokens ?? '未知'}
                              </td>
                              <td>
                                {((c.charged ?? c.reserved) / 1e6).toFixed(5)} {b.currency}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ))}
              </details>
              <p>{config?.sending_scope}</p>
              <button onClick={() => setSettings(false)}>收起设置</button>
            </section>
          )}
          <div className="workspace-bar">
            <label>
              作品
              <select
                value={novelId}
                onChange={(e) => {
                  if (e.target.value === novelId) return
                  if (!confirmLeave()) {
                    e.target.value = novelId
                    return
                  }
                  setBranches([])
                  setBranchId('')
                  setBench(undefined)
                  setRunEntry(undefined)
                  setNovelId(e.target.value)
                }}
              >
                <option value="" disabled>
                  选择作品
                </option>
                {novels.map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.title}
                  </option>
                ))}
              </select>
            </label>
            <label>
              工作版本
              <select
                value={branchId}
                onChange={(e) => {
                  if (e.target.value === branchId) return
                  if (!confirmLeave()) {
                    e.target.value = branchId
                    return
                  }
                  setBench(undefined)
                  setRunEntry(undefined)
                  setBranchId(e.target.value)
                }}
              >
                <option value="" disabled>
                  选择版本
                </option>
                {branches.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            </label>
            <ManuscriptExchange
              branchId={branch?.id}
              notify={notify}
              onImported={async (id) => {
                setNovels(await api('/novels'))
                if (!confirmLeave()) return
                setBranches([])
                setBranchId('')
                setBench(undefined)
                setRunEntry(undefined)
                setNovelId(id)
                goToPage('writing')
              }}
            />
            <details
              className="new-novel"
              ref={createDisclosure}
              onToggle={(event) => {
                if (!event.currentTarget.open) setNewWorkStart(undefined)
              }}
            >
              <summary>＋ 新建作品</summary>
              <form onSubmit={createNovel}>
                <label>
                  作品名称
                  <input
                    ref={newWorkTitle}
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    required
                    maxLength={200}
                  />
                </label>
                <button className="primary" disabled={creating}>
                  {creating
                    ? '创建中…'
                    : newWorkStart === 'idea'
                      ? '创建并进入自动接力'
                      : newWorkStart === 'manual'
                        ? '创建并进入立项台'
                        : '创建并开始写作'}
                </button>
              </form>
            </details>
            <label className="model-select">
              模型账户
              <select value={account} onChange={(e) => setAccount(e.target.value)}>
                {config?.accounts.map((a: Account) => (
                  <option key={a.id} value={a.id}>
                    {a.id} · {a.model}
                    {!a.configured ? '（未配置）' : ''}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <main id="main">
            {(branch || !novel) && (
              <nav className="creation-starts" aria-label="创作入口">
                {creationStarts.map(([start, name]) => (
                  <button
                    key={start}
                    type="button"
                    disabled={creating || (start === 'relay' && !branch)}
                    onClick={() => chooseCreationStart(start)}
                  >
                    {name}
                  </button>
                ))}
                {!novel && <span>接力需先完成并确认一个阶段。</span>}
              </nav>
            )}
            {branch && (
              <nav className="bench-navigation" aria-label="七台工作流">
                {benches.map(([id, label, destination]) => (
                  <button
                    key={id}
                    type="button"
                    aria-current={bench?.id === id && page === destination ? 'location' : undefined}
                    onClick={() => showBench(id, destination)}
                  >
                    {label}台
                  </button>
                ))}
              </nav>
            )}
            {!novel && page !== 'library' ? (
              <div className="welcome">
                <span className="eyebrow">写下第一句，就可以开始</span>
                <h1>你的故事，由你决定。</h1>
                <p>直接写作，卡住时再找一个方向。大纲、人物卡和模型配置都不是起笔的前提。</p>
                <form onSubmit={createNovel}>
                  <label>
                    第一部作品的名字
                    <input
                      ref={firstWorkTitle}
                      autoFocus
                      value={title}
                      onChange={(e) => setTitle(e.target.value)}
                      placeholder="可以先叫「未命名的故事」"
                      required
                      maxLength={200}
                    />
                  </label>
                  <button className="primary" disabled={creating}>
                    {creating
                      ? '正在创建…'
                      : newWorkStart === 'idea'
                        ? '创建并进入自动接力'
                        : newWorkStart === 'manual'
                          ? '创建并进入立项台'
                          : '打开写作台 →'}
                  </button>
                  {newWorkStart && (
                    <button type="button" disabled={creating} onClick={() => setNewWorkStart(undefined)}>
                      直接写作
                    </button>
                  )}
                </form>
              </div>
            ) : (
              <CreationAssistantLayout
                stage={stage ?? 'M5'}
                branchId={branch?.id ?? ''}
                account={account}
                inline={!branch || !stage || stage === 'M5' || (page === 'ideation' && quickConception)}
              >
                {page === 'ideation' && branch && (
                  <>
                    <nav className="conception-navigation" aria-label="构思方式">
                      <button
                        type="button"
                        aria-pressed={!quickConception}
                        onClick={() => quickConception && confirmLeave() && setQuickConception(false)}
                      >
                        按类别构思
                      </button>
                      <button
                        type="button"
                        aria-pressed={quickConception}
                        onClick={() => !quickConception && confirmLeave() && setQuickConception(true)}
                      >
                        快速构思
                      </button>
                    </nav>
                    {quickConception ? (
                      <Ideation
                        key={branch.id}
                        branch={branch}
                        account={account}
                        modelReady={!!selectedAccount?.configured && !!selectedAccount?.priced}
                        onWriting={() => openConceptionDestination('writing')}
                      />
                    ) : (
                      <div className="page-pad conception-page">
                        <div className="page-heading">
                          <div>
                            <h1>小说构思</h1>
                            <p>
                              {novel?.title} · {branch.name}
                            </p>
                          </div>
                        </div>
                        <ConceptionWorkspace
                          key={branch.id}
                          branch={branch}
                          account={account}
                          modelReady={!!selectedAccount?.configured && !!selectedAccount?.priced}
                          notify={notify}
                          onHandoff={openConceptionDestination}
                          projectEntry={bench?.id === 'project' && bench.open ? bench : undefined}
                        />
                      </div>
                    )}
                  </>
                )}
                {page === 'writing' && novel && branch && (
                  <Writing
                    key={branchId}
                    novel={novel}
                    branch={branch}
                    account={account}
                    modelReady={!!selectedAccount?.configured && !!selectedAccount?.priced}
                    notify={notify}
                    onGenreSettings={() => navigate('records')}
                    feedbackHandoff={feedbackHandoff?.branch_id === branch.id ? feedbackHandoff : undefined}
                    onClearFeedback={() => setFeedbackHandoff(undefined)}
                    conceptionHandoff={selectedConception}
                    onConceptionSettings={openConceptionWorkspace}
                    outlineHandoff={outlineHandoff?.branchId === branch.id ? outlineHandoff : undefined}
                    onOutline={() => navigate('outline')}
                    onInspection={() => {
                      if (!confirmLeave()) return
                      setOutlineHandoff(undefined)
                      setBench({ id: 'inspection' })
                      goToPage('records')
                    }}
                  />
                )}
                {page === 'outline' && branch && (
                  <OutlineWorkspace
                    key={branch.id}
                    branchId={branch.id}
                    account={account}
                    modelReady={!!selectedAccount?.configured && !!selectedAccount?.priced}
                    notify={notify}
                    onWriting={(chapterId, reportId) => {
                      if (!confirmLeave()) return
                      setFeedbackHandoff(undefined)
                      setOutlineHandoff({ branchId: branch.id, chapterId, reportId })
                      goToPage('writing')
                    }}
                  />
                )}
                {page === 'library' && (
                  <LibraryPage
                    novelId={novelId}
                    branch={branch}
                    account={account}
                    modelReady={!!selectedAccount?.configured && !!selectedAccount?.priced}
                    notify={notify}
                    onWriting={() => navigate('writing')}
                  />
                )}
                {page === 'workflow' && novel && (
                  <WorkflowPage
                    key={novel.id}
                    novel={novel}
                    accounts={config?.accounts || []}
                    branchId={branchId}
                    account={account}
                    modelReady={!!selectedAccount?.configured && !!selectedAccount?.priced}
                    notify={notify}
                    initialMode={workflowMode}
                    conceptionHandoff={selectedConception}
                    sevenStageEntry={runEntry?.branchId === branch?.id ? runEntry : undefined}
                    onNewIdeaWork={() => openNewWork('idea')}
                    onConceptionSettings={openConceptionWorkspace}
                    onStarted={async (id) => {
                      await refreshBranches(novel.id, id, selectionGeneration)
                    }}
                    onGenreSettings={() => navigate('records')}
                  />
                )}
                {page === 'records' && branch && (
                  <Records
                    key={branch.id}
                    branch={branch}
                    onSnapshotRestored={(id) => refreshBranches(branch.novel_id, id, selectionGeneration)}
                    account={account}
                    modelReady={!!selectedAccount?.configured && !!selectedAccount?.priced}
                    notify={notify}
                    onReviseFeedback={(feedback) => {
                      if (!confirmLeave()) return
                      setFeedbackHandoff(feedback)
                      goToPage('writing')
                    }}
                  />
                )}
              </CreationAssistantLayout>
            )}
          </main>
        </>
      )}
    </>
  )
}
