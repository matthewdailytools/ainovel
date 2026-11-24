import { useEffect, useRef } from 'react'
import type { Account, Config } from './api'

export function AppSettings({
  config,
  dataDir,
  account,
  onSelectAccount,
  onClose,
}: {
  config: Config
  dataDir: string
  account?: Account
  onSelectAccount: (id: string) => void
  onClose: () => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    if (dialog.current && !dialog.current.open) dialog.current.showModal()
  }, [])

  return (
    <dialog
      id="app-settings"
      ref={dialog}
      className="app-settings-dialog"
      aria-labelledby="app-settings-title"
      onClose={onClose}
    >
      <header className="app-settings-header">
        <h2 id="app-settings-title">设置</h2>
        <button type="button" onClick={() => dialog.current?.close()}>
          关闭设置
        </button>
      </header>
      <div className="app-settings-body">
        <section className="app-settings-section" aria-labelledby="app-settings-model-heading">
          <h3 id="app-settings-model-heading">模型与预算</h3>
          <label>
            模型账户
            <select
              value={account?.id || ''}
              onChange={(event) => onSelectAccount(event.currentTarget.value)}
              aria-describedby="app-settings-account-status"
              disabled={config.accounts.length === 0}
            >
              <option value="" disabled>
                {config.accounts.length ? '请选择模型账户' : '暂无模型账户'}
              </option>
              {config.accounts.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.id} · {option.model}
                  {!option.configured ? '（未配置）' : !option.priced ? '（缺少单价）' : ''}
                </option>
              ))}
            </select>
          </label>
          <p id="app-settings-account-status" className="muted">
            {account?.configured && account.priced
              ? '各工作区发起的新任务使用此账户。'
              : '配置模型密钥和单价后可发起模型任务。'}
          </p>
          <p>同一服务商的模型账户共用预算。余额按本应用累计用量估算，未知费用继续占用预留。</p>
          {config.budget.length ? (
            <ul className="app-settings-budgets">
              {config.budget.map((budget) => (
                <li key={budget.provider}>
                  <strong>{budget.provider}</strong> · 剩余 {budget.remaining.toFixed(4)} / {budget.limit}{' '}
                  {budget.currency}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">暂无服务商预算。</p>
          )}
          {config.accounts.length > 0 && (
            <details>
              <summary>账户配置与单价</summary>
              <div className="account-grid">
                {config.accounts.map((option) => (
                  <div className="card" key={option.id}>
                    <strong>{option.id}</strong>
                    <span>{option.model}</span>
                    <span>
                      {option.configured ? '密钥已配置' : '密钥未配置'} ·{' '}
                      {option.priced ? '单价已配置' : '缺少单价，禁止调用'}
                    </span>
                    <small>
                      每百万 Token：输入 {option.input_rate}／输出 {option.output_rate} {option.currency}
                    </small>
                    {option.price_source && (
                      <a href={option.price_source} target="_blank" rel="noreferrer">
                        配置单价依据
                      </a>
                    )}
                  </div>
                ))}
              </div>
            </details>
          )}
          {config.budget.length > 0 && (
            <details>
              <summary>调用记录与费用预留</summary>
              {config.budget.map((budget) => (
                <div key={budget.provider}>
                  <p>
                    {budget.provider}：已结算估算 {budget.settled.toFixed(4)}；预留／未知{' '}
                    {budget.reserved_unknown.toFixed(4)}；剩余 {budget.remaining.toFixed(4)} {budget.currency}
                  </p>
                  {budget.calls.length ? (
                    <div className="table-scroll">
                      <table aria-label={`${budget.provider} 调用记录`}>
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
                          {budget.calls.map((call) => (
                            <tr key={call.id}>
                              <td>{call.account}</td>
                              <td>{call.model}</td>
                              <td>{call.status}</td>
                              <td>
                                {call.input_tokens ?? '未知'} / {call.output_tokens ?? '未知'}
                              </td>
                              <td>
                                {((call.charged ?? call.reserved) / 1e6).toFixed(5)} {budget.currency}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p className="muted">暂无调用记录。</p>
                  )}
                </div>
              ))}
            </details>
          )}
          {config.sending_scope && (
            <details>
              <summary>默认发送范围</summary>
              <p className="muted">{config.sending_scope}</p>
            </details>
          )}
        </section>
        <section className="app-settings-section" aria-labelledby="app-settings-local-heading">
          <h3 id="app-settings-local-heading">本机数据</h3>
          <dl>
            <dt>数据位置</dt>
            <dd className="app-settings-data-path">{dataDir}</dd>
          </dl>
          <p className="muted">修改本机配置文件后，重启本机服务和 Worker。</p>
        </section>
      </div>
    </dialog>
  )
}
