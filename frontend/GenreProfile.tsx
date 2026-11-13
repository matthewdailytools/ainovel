import { useEffect, useRef, useState } from 'react'
import { api, type GenreCatalog, type GenreProfile, type GenreTopic } from './api'
import type { Notify } from './App'
import {
  emptyGenreDraft,
  genreDraftFromAdopted,
  readerDefinition,
  updateGenreDraft,
  type GenreDraft,
} from './genreDraft'

const families: Record<string, string> = {
  fantasy: '幻想与世界',
  mystery: '悬疑与冒险',
  society: '社会与生活',
  relationship: '人物与关系',
  competition: '竞技与成长',
  expression: '表达与体验',
}
function topicList(title: string, topics: GenreTopic[]) {
  if (!topics.length) return null
  return (
    <details>
      <summary>
        {title} · {topics.length} 项
      </summary>
      <ul className="genre-topic-list">
        {topics.map((topic) => (
          <li key={topic.id}>
            <strong>{topic.title}</strong>
            <p>{topic.items.join('；')}</p>
          </li>
        ))}
      </ul>
    </details>
  )
}

export function GenreProfileDetails({ profile }: { profile: GenreProfile }) {
  const sections: { title: string; items: string[] }[] = [
    { title: '阅读承诺', items: profile.reading_promises },
    { title: '叙事重点', items: profile.focus },
    { title: '相关审查提示', items: profile.checks },
    { title: '作者例外与边界', items: profile.author_exceptions },
  ]
  return (
    <div className="genre-profile-details">
      {!profile.configured && <p>尚未采用频道与题材配置，可以继续手写或使用局部辅助。</p>}
      {profile.author_notes && (
        <div>
          <h4>作者补充承诺</h4>
          <p className="genre-author-notes">{profile.author_notes}</p>
        </div>
      )}
      {sections.map(({ title, items }) => {
        return items.length ? (
          <div key={title}>
            <h4>{title}</h4>
            <ul>
              {items.map((value, i) => (
                <li key={i}>{value}</li>
              ))}
            </ul>
          </div>
        ) : null
      })}
      {!!profile.mechanisms.length && <p>附加机制：{profile.mechanisms.join('、')}</p>}
      {!!profile.conflicts.length && (
        <div className="notice" role="status">
          <strong>组合与例外提示</strong>
          <ul>
            {profile.conflicts.map((value, i) => (
              <li key={i}>{value}</li>
            ))}
          </ul>
        </div>
      )}
      {topicList('当前组合适用的设计主题', profile.required_topics)}
      {topicList('其他可选主题，当前暂不展开', profile.optional_topics)}
      <p className="small muted">
        题材预设提供阅读方向和相关检查提示。作者明确要求、固定情节与已采用事实优先；不要求每章展开所有主题。
      </p>
      <p className="small muted">
        目录 {profile.catalog_version}
        {profile.source_story_revision != null && ` · 故事修订 ${profile.source_story_revision}`}
        {profile.source_artifact_id && ` · 来源 ${profile.source_artifact_id.slice(0, 8)}`}
      </p>
    </div>
  )
}

export function GenreProfileNotice({
  branchId,
  onSettings,
  scope = '写作辅助',
  onResolved,
}: {
  branchId: string
  onSettings: () => void
  scope?: string
  onResolved?: (profile: GenreProfile | undefined) => void
}) {
  const [loaded, setLoaded] = useState<{ branchId: string; profile: GenreProfile }>()
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    let active = true
    setError('')
    onResolved?.(undefined)
    if (!branchId) return
    void api<GenreProfile>(`/branches/${branchId}/genre-profile`)
      .then((profile) => {
        if (active) {
          setLoaded({ branchId, profile })
          onResolved?.(profile)
        }
      })
      .catch((e: unknown) => {
        if (active) setError(e instanceof Error ? e.message : '读取题材配置失败')
      })
    return () => {
      active = false
    }
  }, [branchId, retry, onResolved])
  const profile = loaded?.branchId === branchId ? loaded.profile : undefined
  return (
    <details className="genre-profile-notice">
      <summary>
        本书题材与阅读取向
        {error
          ? ' · 读取失败，请刷新确认'
          : profile?.configured
            ? ` · ${profile.audience} · ${[profile.primary_genre, ...profile.secondary_genres].filter(Boolean).join('＋') || '自定方向'}`
            : profile
              ? ' · 尚未设置'
              : ' · 正在读取'}
      </summary>
      <p className="small">{scope}使用本工作版本已采用的配置。未保存的配置草稿不参与生成。</p>
      {error ? <p role="alert">{error}</p> : profile && <GenreProfileDetails profile={profile} />}
      <div className="actions">
        <button type="button" onClick={onSettings}>
          设置频道、题材与作者例外
        </button>
        <button type="button" onClick={() => setRetry((v) => v + 1)}>
          刷新采用配置
        </button>
      </div>
    </details>
  )
}

export function GenreSettings({
  branchId,
  notify,
  onSaved,
}: {
  branchId: string
  notify: Notify
  onSaved: () => Promise<void>
}) {
  const [catalog, setCatalog] = useState<GenreCatalog>()
  const [adopted, setAdopted] = useState<GenreProfile>()
  const [draft, setDraft] = useState<GenreDraft>(emptyGenreDraft)
  const [preview, setPreview] = useState<GenreProfile>()
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [reload, setReload] = useState(0)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    let active = true
    setLoading(true)
    setError('')
    void Promise.all([
      api<GenreCatalog>('/genre-catalog'),
      api<GenreProfile>(`/branches/${branchId}/genre-profile`),
    ])
      .then(([c, p]) => {
        if (!active) return
        setCatalog(c)
        setAdopted(p)
        setDraft(genreDraftFromAdopted(p, c))
        setPreview(undefined)
        setStatus('表单已载入当前采用配置；修改后需保存才生效。')
      })
      .catch((e: unknown) => {
        if (active) setError(e instanceof Error ? e.message : '读取配置失败')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
      alive.current = false
    }
  }, [branchId, reload])

  function change(values: Partial<GenreDraft>) {
    setDraft((d) => updateGenreDraft(d, values))
    setPreview(undefined)
    setStatus('配置草稿尚未保存，当前采用配置保持。')
  }
  async function perform(save: boolean) {
    if (busy || loading) return
    setBusy(true)
    setError('')
    try {
      let baseline = adopted
      if (save) {
        const current = await api<GenreProfile>(`/branches/${branchId}/genre-profile`)
        if (
          !adopted ||
          current.source_artifact_id !== adopted.source_artifact_id ||
          current.catalog_version !== adopted.catalog_version
        )
          throw new Error('题材配置已改变，表单保留；请先复制需要的内容，再重新载入当前采用配置。')
        baseline = current
      }
      const values = readerDefinition(draft, baseline?.story_revision)
      if (save) {
        await api(`/branches/${branchId}/definitions`, 'POST', values)
        if (alive.current) setStatus('配置已写入，正在读取采用结果…')
        const p = await api<GenreProfile>(`/branches/${branchId}/genre-profile`)
        if (!alive.current) return
        setAdopted(p)
        setPreview(undefined)
        if (catalog) setDraft(genreDraftFromAdopted(p, catalog))
        setStatus('已保存并采用新配置。后续新任务将使用它；已有正文保持，旧任务或候选需核对版本影响。')
        await onSaved()
      } else {
        const p = await api<GenreProfile>('/genre-profile/preview', 'POST', values)
        if (alive.current) {
          setPreview(p)
          setStatus('这是配置草稿的装配预览，尚未保存；没有调用模型。')
        }
      }
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e.message : '处理配置失败，输入已保留')
      notify(e)
    } finally {
      if (alive.current) setBusy(false)
    }
  }
  const title = adopted?.configured
    ? `${adopted.audience} · ${[adopted.primary_genre, ...adopted.secondary_genres].filter(Boolean).join('＋') || '自定方向'}`
    : '尚未采用，可直接写作'
  return (
    <section className="card genre-settings" id="genre-settings">
      <h2>频道、36题材与作者例外</h2>
      <p>从同一题材选择不同阅读重点，组合主辅题材及附加机制。女频不自动增加恋爱，男频不自动增加后宫。</p>
      <p className="genre-adopted">
        <strong>当前采用：</strong>
        {loading ? '正在读取…' : !adopted && error ? '尚未读取，请重试' : title}
      </p>
      {adopted && (
        <details>
          <summary>查看当前采用的承诺、重点与检查</summary>
          <GenreProfileDetails profile={adopted} />
        </details>
      )}
      {error && (
        <p role="alert" className="notice">
          {error} 表单输入保持。
        </p>
      )}
      {loading ? (
        <p role="status">正在读取题材目录与本版本配置…</p>
      ) : catalog && adopted ? (
        <details>
          <summary>编辑本书配置（保存后生效）</summary>
          <form
            onSubmit={(event) => {
              event.preventDefault()
              void perform(true)
            }}
          >
            <fieldset disabled={busy}>
              <legend>本次配置草稿</legend>
              <div className="form-grid">
                <label>
                  阅读取向
                  <select
                    value={draft.audience}
                    onChange={(e) => change({ audience: e.target.value as GenreDraft['audience'] })}
                  >
                    {(['不限', '男频', '女频'] as const).map((v) => (
                      <option key={v}>{v}</option>
                    ))}
                  </select>
                </label>
                <label>
                  主题材
                  <select
                    value={draft.primary}
                    onChange={(e) =>
                      change({
                        primary: e.target.value,
                        secondary: draft.secondary.filter((id) => id !== e.target.value),
                      })
                    }
                  >
                    <option value="">暂不选择题材</option>
                    {[...new Set(catalog.genres.map((g) => g.family))].map((family) => (
                      <optgroup key={family} label={families[family] || family}>
                        {catalog.genres
                          .filter((g) => g.family === family)
                          .map((g) => (
                            <option key={g.id} value={g.id}>
                              {g.name}
                            </option>
                          ))}
                      </optgroup>
                    ))}
                    <option value="__custom__">自定义主题材</option>
                  </select>
                </label>
              </div>
              {draft.primary === '__custom__' && (
                <label>
                  自定义主题材
                  <input
                    value={draft.customPrimary}
                    onChange={(e) => change({ customPrimary: e.target.value })}
                    maxLength={200}
                    required
                  />
                </label>
              )}
              <details>
                <summary>辅题材 · 已选 {draft.secondary.length} 项</summary>
                <div className="genre-choices">
                  {catalog.genres
                    .filter((g) => g.id !== draft.primary)
                    .map((g) => (
                      <label className="check" key={g.id}>
                        <input
                          type="checkbox"
                          checked={draft.secondary.includes(g.id)}
                          disabled={draft.secondary.length >= 9 && !draft.secondary.includes(g.id)}
                          onChange={(e) =>
                            change({
                              secondary: e.target.checked
                                ? [...draft.secondary, g.id]
                                : draft.secondary.filter((id) => id !== g.id),
                            })
                          }
                        />
                        <span>{g.name}</span>
                      </label>
                    ))}
                </div>
              </details>
              <label>
                其他自定义辅题材（顿号分隔）
                <input
                  value={draft.customSecondary}
                  onChange={(e) => change({ customSecondary: e.target.value })}
                  maxLength={2000}
                  placeholder="保留已有自定义题材，也可补充目录外方向"
                />
              </label>
              <details>
                <summary>附加机制 · 已选 {draft.mechanisms.length} 项</summary>
                <div className="genre-choices">
                  {catalog.mechanisms.map((m) => (
                    <label className="check" key={m.id}>
                      <input
                        type="checkbox"
                        checked={draft.mechanisms.includes(m.id)}
                        disabled={draft.mechanisms.length >= 10 && !draft.mechanisms.includes(m.id)}
                        onChange={(e) =>
                          change({
                            mechanisms: e.target.checked
                              ? [...draft.mechanisms, m.id]
                              : draft.mechanisms.filter((id) => id !== m.id),
                          })
                        }
                      />
                      <span>{m.name}</span>
                    </label>
                  ))}
                </div>
              </details>
              <label>
                其他自定义机制（顿号分隔）
                <input
                  value={draft.customMechanisms}
                  onChange={(e) => change({ customMechanisms: e.target.value })}
                  maxLength={2000}
                />
              </label>
              <label>
                作者例外与明确边界（每行一条）
                <textarea
                  rows={4}
                  value={draft.exceptions}
                  onChange={(e) => change({ exceptions: e.target.value })}
                  maxLength={20_000}
                  placeholder="例如：无CP，不增加恋爱主线；主角不建立后宫；交易必须失败且不战斗"
                />
              </label>
              <label>
                本书阅读承诺与补充说明
                <textarea
                  rows={4}
                  value={draft.notes}
                  onChange={(e) => change({ notes: e.target.value })}
                  maxLength={20_000}
                  placeholder="你希望读者获得什么？明确例外会优先于频道预设。"
                />
              </label>
              <p className="small muted">
                36个题材均有男频／女频配置；主辅题材合计最多10项，机制最多10项。旧的自定义名称会保留。
              </p>
              <div className="actions">
                <button type="button" onClick={() => void perform(false)}>
                  预览装配结果（不调用模型）
                </button>
                <button className="primary" type="submit">
                  {busy ? '正在处理…' : '保存并采用题材配置'}
                </button>
              </div>
            </fieldset>
          </form>
        </details>
      ) : null}
      <p role="status">{status}</p>
      {preview && (
        <div className="genre-preview">
          <h3>配置草稿预览 · 尚未保存</h3>
          <GenreProfileDetails profile={preview} />
        </div>
      )}
      <button type="button" disabled={busy || loading} onClick={() => setReload((v) => v + 1)}>
        重新载入当前采用配置（替换本表单）
      </button>
    </section>
  )
}
