import { useEffect, useRef, useState } from 'react'
import { ApiError, api, type Branch, type DefinitionInput } from './api'
import type { components } from './api-schema'
import type { Notify } from './App'
import { AuthorIntentPanel } from './AuthorIntent'
import { GenreSettings } from './GenreProfile'
import { useLeaveGuard } from './leaveGuard'
import { settingLabels, type SettingKind } from './workspaceSettingsKinds'

type WrittenSetting = Exclude<SettingKind, 'intent' | 'reader'>

type DefinitionSummary = components['schemas']['DefinitionSummary']

type AdoptedContext = {
  branch: Branch
  definitions: { id: string; kind: SettingKind; payload: { text: string } }[]
}

export function WorkspaceSettings({
  branch,
  kind,
  account,
  modelReady,
  notify,
  onSaved,
}: {
  branch: Branch
  kind: SettingKind
  account: string
  modelReady: boolean
  notify: Notify
  onSaved: () => void
}) {
  const target = branch.id + ':' + kind
  return (
    <div className="workspace-setting-editor">
      {kind === 'intent' ? (
        <AuthorIntentPanel
          key={target}
          branchId={branch.id}
          novelId={branch.novel_id}
          account={account}
          modelReady={modelReady}
          notify={notify}
          onSaved={async () => onSaved()}
          inWorkspace
        />
      ) : kind === 'reader' ? (
        <GenreSettings
          key={target}
          branchId={branch.id}
          notify={notify}
          onSaved={async () => onSaved()}
          inWorkspace
        />
      ) : (
        <WrittenSettingEditor key={target} branchId={branch.id} kind={kind} onSaved={onSaved} />
      )}
    </div>
  )
}

function WrittenSettingEditor({
  branchId,
  kind,
  onSaved,
}: {
  branchId: string
  kind: WrittenSetting
  onSaved: () => void
}) {
  const path = `/branches/${branchId}/definitions`
  const title = settingLabels[kind]
  const [adopted, setAdopted] = useState<DefinitionSummary>()
  const [draft, setDraft] = useState('')
  const [latest, setLatest] = useState<DefinitionSummary>()
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const alive = useRef(false)
  const inFlight = useRef(false)
  const editor = useRef<HTMLTextAreaElement>(null)
  const dirty = adopted !== undefined && draft !== adopted.text
  useLeaveGuard(dirty || saving, `${title}草稿`, 'workspace-setting')

  useEffect(() => {
    alive.current = true
    let active = true
    setLoading(true)
    setError('')
    void api<DefinitionSummary>(`${path}/${kind}`)
      .then((current) => {
        if (!active) return
        setAdopted(current)
        setDraft(current.text)
      })
      .catch((failure: unknown) => {
        if (active) setError(failure instanceof Error ? failure.message : '读取设定失败。')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
      alive.current = false
    }
  }, [path, kind, retry])

  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (!adopted || !dirty || !draft.trim() || latest || inFlight.current) return
    inFlight.current = true
    setSaving(true)
    setError('')
    try {
      const current = await api<DefinitionSummary>(`${path}/${kind}`)
      if (!alive.current) return
      if (current.artifact_id !== adopted.artifact_id) {
        setLatest(current)
        return
      }
      const input: DefinitionInput = {
        kind,
        text: draft,
        audience: '不限',
        base_story_revision: current.story_revision,
      }
      const result = await api<AdoptedContext>(path, 'POST', input)
      if (!alive.current) return
      const definition = result.definitions.find((row) => row.kind === kind)
      if (!definition) throw new Error('设定已提交，未能读取采用版本，请重新核对。')
      setAdopted({
        story_revision: result.branch.story_revision,
        artifact_id: definition.id,
        text: definition.payload.text,
      })
      setDraft(definition.payload.text)
      onSaved()
    } catch (failure) {
      if (!alive.current) return
      if (failure instanceof ApiError && failure.status === 409) {
        try {
          const current = await api<DefinitionSummary>(`${path}/${kind}`)
          if (alive.current && current.artifact_id !== adopted.artifact_id) {
            setLatest(current)
            return
          }
        } catch {
          // Leave the original write error and draft available for retry.
        }
      }
      if (alive.current) setError(failure instanceof Error ? failure.message : '保存设定失败。')
    } finally {
      inFlight.current = false
      if (alive.current) setSaving(false)
    }
  }

  function resolve(useLatest: boolean) {
    if (!latest) return
    setAdopted(latest)
    if (useLatest) setDraft(latest.text)
    setLatest(undefined)
    editor.current?.focus()
  }

  return (
    <section className="card" aria-labelledby="workspace-setting-title">
      <h2 id="workspace-setting-title">{title}</h2>
      {loading && <p role="status">正在读取采用版本…</p>}
      {error && (
        <p role="alert">
          {error}
          {adopted && ' 当前草稿已保留。'}
        </p>
      )}
      {!adopted && !loading && error && (
        <button type="button" onClick={() => setRetry((value) => value + 1)}>
          重新读取设定
        </button>
      )}
      {adopted && (
        <form onSubmit={(event) => void save(event)}>
          <p className="small muted" role="status">
            {saving
              ? '正在保存…'
              : dirty
                ? '草稿尚未保存'
                : adopted.artifact_id
                  ? '已保存并采用'
                  : '尚无已采用内容'}
          </p>
          {latest && (
            <section className="notice" aria-labelledby="workspace-setting-conflict">
              <h3 id="workspace-setting-conflict">采用版本已在别处更新</h3>
              <p role="alert">请核对最新采用内容，当前草稿已保留。</p>
              <details>
                <summary>查看最新采用内容</summary>
                <pre>{latest.text || '暂无采用内容。'}</pre>
              </details>
              <div className="actions">
                <button type="button" onClick={() => resolve(false)}>
                  保留草稿，按最新版本继续编辑
                </button>
                <button type="button" onClick={() => resolve(true)}>
                  使用最新版本，放弃当前草稿
                </button>
              </div>
            </section>
          )}
          <label>
            {title}正文
            <textarea
              ref={editor}
              rows={18}
              value={draft}
              readOnly={saving}
              maxLength={20_000}
              onChange={(event) => setDraft(event.target.value)}
            />
          </label>
          <button className="primary" disabled={!dirty || !draft.trim() || saving || !!latest}>
            保存并采用
          </button>
        </form>
      )}
    </section>
  )
}
