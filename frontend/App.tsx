import { useCallback, useEffect, useRef, useState } from 'react'
import { api, startSession, type Account, type Branch, type Config, type Novel } from './api'
import { Writing } from './Writing'
import { LibraryPage } from './Library'
import { WorkflowPage } from './Workflow'
import { Records } from './Records'

export type Notify = (error: unknown) => void
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
  const [account, setAccount] = useState('openai_1')
  const [title, setTitle] = useState('')
  const [creating, setCreating] = useState(false)
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
  async function createNovel(event: React.FormEvent) {
    event.preventDefault()
    if (creating || !title.trim()) return
    setCreating(true)
    try {
      const result = await api<{ id: string }>('/novels', 'POST', { title: title.trim(), brief: '' })
      setNovels(await api('/novels'))
      setBranches([])
      setBranchId('')
      setNovelId(result.id)
      setPage('writing')
      setTitle('')
    } catch (e) {
      notify(e)
    } finally {
      setCreating(false)
    }
  }
  const novel = novels.find((n) => n.id === novelId)
  const branch = branches.find((b) => b.id === branchId && b.novel_id === novelId)
  const selectedAccount = config?.accounts.find((a) => a.id === account)
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
            ['library', '资料书库'],
            ['workflow', '整书创作'],
            ['records', '故事与审阅'],
          ].map(([id, name]) => (
            <button
              key={id}
              className={page === id ? 'nav active' : 'nav'}
              aria-current={page === id ? 'page' : undefined}
              onClick={() => setPage(id)}
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
                  setBranches([])
                  setBranchId('')
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
              <select value={branchId} onChange={(e) => setBranchId(e.target.value)}>
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
            <details className="new-novel">
              <summary>＋ 新建作品</summary>
              <form onSubmit={createNovel}>
                <label>
                  作品名称
                  <input value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={200} />
                </label>
                <button className="primary" disabled={creating}>
                  {creating ? '创建中…' : '创建并开始写作'}
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
            {!novel && page !== 'library' ? (
              <div className="welcome">
                <span className="eyebrow">写下第一句，就可以开始</span>
                <h1>你的故事，由你决定。</h1>
                <p>直接写作，卡住时再找一个方向。大纲、人物卡和模型配置都不是起笔的前提。</p>
                <form onSubmit={createNovel}>
                  <label>
                    第一部作品的名字
                    <input
                      autoFocus
                      value={title}
                      onChange={(e) => setTitle(e.target.value)}
                      placeholder="可以先叫「未命名的故事」"
                      required
                      maxLength={200}
                    />
                  </label>
                  <button className="primary" disabled={creating}>
                    {creating ? '正在创建…' : '打开写作台 →'}
                  </button>
                </form>
                <div className="welcome-steps">
                  <span>01 亲自写作</span>
                  <span>02 按情节补写</span>
                  <span>03 从粗纲到整书</span>
                </div>
              </div>
            ) : (
              <>
                {page === 'writing' && novel && branch && (
                  <Writing
                    key={branchId}
                    novel={novel}
                    branch={branch}
                    account={account}
                    modelReady={!!selectedAccount?.configured && !!selectedAccount?.priced}
                    notify={notify}
                    onGenreSettings={() => setPage('records')}
                  />
                )}
                {page === 'library' && (
                  <LibraryPage
                    novelId={novelId}
                    branch={branch}
                    account={account}
                    modelReady={!!selectedAccount?.configured && !!selectedAccount?.priced}
                    notify={notify}
                    onWriting={() => setPage('writing')}
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
                    onStarted={async (id) => {
                      await refreshBranches(novel.id, id, selectionGeneration)
                    }}
                    onGenreSettings={() => setPage('records')}
                  />
                )}
                {page === 'records' && branch && (
                  <Records
                    key={branch.id}
                    branch={branch}
                    account={account}
                    modelReady={!!selectedAccount?.configured && !!selectedAccount?.priced}
                    notify={notify}
                  />
                )}
              </>
            )}
          </main>
        </>
      )}
    </>
  )
}
