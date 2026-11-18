import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { components } from './api-schema'
import { RestoreReportList } from './ChapterRestorePreview'
import { settingAdoptionLabels } from './conception'
import { ConceptionQuote } from './ConceptionItems'

type History = components['schemas']['SettingItemHistory']
type ItemState = components['schemas']['SettingItemState']
export type SettingRestoreBasis = Omit<components['schemas']['RestoreSettingItem'], 'idempotency_key'>
type SettingRestorePreview = components['schemas']['SettingRestorePreview']

function provenanceLabel(provenance: ItemState['provenance']) {
  if (provenance.origin === 'author') return '作者设定'
  if (provenance.origin === 'ai') return '来自 AI 草案'
  const label = provenance.origin === 'archive' ? '稿件包恢复' : '同步设定'
  const origin = provenance.source_origin
  if (origin === 'author') return `${label} · 源端作者设定`
  if (origin === 'ai') return `${label} · 源端 AI 草案`
  return label
}

function ItemVersion({ title, value }: { title: string; value: ItemState | null }) {
  return (
    <section aria-label={title} style={{ overflowWrap: 'anywhere' }}>
      <h5>{title}</h5>
      {value ? (
        <>
          <p>
            <strong>{value.item.title}</strong>
          </p>
          <p className="small">
            {value.category_title}（{value.category_id}） · 第 {value.item_number} 项 · 条目编号{' '}
            {value.item.id}
          </p>
          <p style={{ whiteSpace: 'pre-wrap' }}>{value.item.detail || '尚无设定内容。'}</p>
          {value.item.adoption_state && (
            <p className="small">采用状态 {settingAdoptionLabels[value.item.adoption_state]}</p>
          )}
          {value.item.setting_policy && (
            <p className="small">
              {value.item.setting_policy.strength === 'hard' ? '硬规定' : '可调整设定'}
              {' · 适用范围 '}
              {value.item.setting_policy.scope || '未填写'}
              {' · 例外 '}
              {value.item.setting_policy.exceptions || '未填写'}
            </p>
          )}
          {!!value.item.links?.length && <p className="small">关联编号 {value.item.links.join('、')}</p>}
          <p className="small">
            {provenanceLabel(value.provenance)}
            {value.provenance.author_edited ? ' · 经作者编辑' : ''}
            {value.provenance.artifact_id ? ` · 草案编号 ${value.provenance.artifact_id}` : ''}
            {value.provenance.project_confirmation_id
              ? ` · 立项依据 ${value.provenance.project_revision === null || value.provenance.project_revision === undefined ? value.provenance.project_confirmation_id : `修订 ${value.provenance.project_revision}`}`
              : ''}
          </p>
          {value.provenance.source_revision_id && (
            <p className="small">
              源端修订 {value.provenance.source_revision_id}
              {value.provenance.source_device_id
                ? ` · ${value.provenance.origin === 'archive' ? '稿件包记录设备' : '设备'} ${value.provenance.source_device_id}`
                : ''}
              {value.provenance.source_artifact_id
                ? ` · 草案编号 ${value.provenance.source_artifact_id}`
                : ''}
            </p>
          )}
          {value.provenance.evidence && (
            <ConceptionQuote
              source={value.provenance.evidence}
              current={value.provenance.evidence_current}
              edited={value.provenance.author_edited}
            />
          )}
        </>
      ) : (
        <p className="small">此修订没有该条目。</p>
      )}
    </section>
  )
}

function sameItem(left: ItemState | null, right: ItemState | null) {
  return (
    !!left &&
    !!right &&
    left.category_id === right.category_id &&
    JSON.stringify([left.item, left.provenance]) === JSON.stringify([right.item, right.provenance])
  )
}

function SettingRestoreImpact({ preview }: { preview: SettingRestorePreview }) {
  const scopeNames = new Map(
    preview.outlines.flatMap(({ chapters }) =>
      chapters.flatMap(({ chapter_id, title, volume_id, volume_title }) => [
        [chapter_id, title] as const,
        [volume_id, volume_title] as const,
      ]),
    ),
  )
  return (
    <section aria-label="设定恢复影响" style={{ overflowWrap: 'anywhere' }}>
      <h4>设定恢复影响</h4>
      <p>恢复会创建新的设定草稿。请在设定台核对并重新确认后采用。</p>
      {preview.current_confirmation_id && (
        <p>
          设定确认依据 <code>{preview.current_confirmation_id}</code> ·{' '}
          {preview.confirmation_current ? '恢复后需重新确认' : '当前已待重新确认'}
        </p>
      )}
      {preview.outlines.map((outline) => (
        <details key={outline.id} open>
          <summary>
            {outline.confirmed ? '已确认大纲' : '保存的大纲草稿'} · 修订 {outline.revision} ·{' '}
            {outline.chapters.filter((chapter) => chapter.references_item).length} 章直接引用
          </summary>
          <p>
            大纲 <code>{outline.id}</code>
            {outline.setting_confirmation_id && (
              <>
                {' · 绑定整版设定 '}
                <code>{outline.setting_confirmation_id}</code>
                {outline.setting_basis_current ? ' · 恢复后需重新核对' : ' · 该设定依据已待复核'}
              </>
            )}
          </p>
          <ul>
            {outline.chapters.map((chapter) => (
              <li key={chapter.chapter_id}>
                {chapter.volume_title}（<code>{chapter.volume_id}</code>） · {chapter.unit_title}（
                <code>{chapter.unit_id}</code>） · {chapter.title}（<code>{chapter.chapter_id}</code>
                ，稿件修订 {chapter.chapter_revision}） ·{' '}
                {chapter.references_item ? '直接引用本条设定' : '共用整版设定'}
              </li>
            ))}
          </ul>
        </details>
      ))}
      <RestoreReportList reports={preview.affected_reports} scopeNames={scopeNames} />
    </section>
  )
}

export function ConceptionItemHistory({
  path,
  itemId,
  revision,
  storyRevision,
  dirty,
  busy,
  onRefresh,
  onRestore,
}: {
  path: string
  itemId: string
  revision: number
  storyRevision: number
  dirty: boolean
  busy: boolean
  onRefresh: () => Promise<void>
  onRestore: (basis: SettingRestoreBasis) => void
}) {
  const historyPath = `${path}/items/${encodeURIComponent(itemId)}/history`
  const [history, setHistory] = useState<History>()
  const [selectedId, setSelectedId] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [impact, setImpact] = useState<SettingRestorePreview>()
  const [checking, setChecking] = useState(false)
  const request = useRef(0)
  const impactRequest = useRef(0)

  function resetImpact() {
    impactRequest.current++
    setImpact(undefined)
    setChecking(false)
  }

  useEffect(() => {
    resetImpact()
    const attempt = ++request.current
    setLoading(true)
    setError('')
    void api<History>(historyPath)
      .then((next) => {
        if (attempt !== request.current) return
        setHistory(next)
        setSelectedId(
          next.entries.find((entry) => entry.state && !sameItem(next.current, entry.state))?.workspace_id ||
            next.entries[0]?.workspace_id ||
            '',
        )
      })
      .catch((failure: Error) => {
        if (attempt === request.current) setError(failure.message)
      })
      .finally(() => {
        if (attempt === request.current) setLoading(false)
      })
    return () => {
      request.current++
      impactRequest.current++
    }
  }, [historyPath, reload, revision, storyRevision])

  async function refresh() {
    try {
      await onRefresh()
      setReload((number) => number + 1)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '暂无法重新读取设定历史。')
    }
  }

  async function loadOlder() {
    if (!history?.next_before_revision) return
    const attempt = ++request.current
    setLoading(true)
    setError('')
    try {
      const next = await api<History>(`${historyPath}?before_revision=${history.next_before_revision}`)
      if (attempt !== request.current) return
      if (
        next.base_revision !== history.base_revision ||
        next.base_story_revision !== history.base_story_revision
      ) {
        setError('保存版已变化，请重新读取设定和历史后比较。')
        return
      }
      setHistory({ ...next, entries: [...history.entries, ...next.entries] })
    } catch (failure) {
      if (attempt === request.current)
        setError(failure instanceof Error ? failure.message : '暂无法读取更早修订。')
    } finally {
      if (attempt === request.current) setLoading(false)
    }
  }

  const selected = history?.entries.find((entry) => entry.workspace_id === selectedId)
  const stale =
    !!history && (history.base_revision !== revision || history.base_story_revision !== storyRevision)
  const unchanged = !!history && !!selected && sameItem(history.current, selected.state)
  const blocked = busy || loading || dirty || stale || !selected?.state || unchanged
  const currentImpact =
    !blocked && impact?.source_workspace_id === selected?.workspace_id ? impact : undefined

  async function checkImpact() {
    if (!selected || !history || blocked) return
    const attempt = ++impactRequest.current
    setImpact(undefined)
    setChecking(true)
    setError('')
    try {
      const next = await api<SettingRestorePreview>(
        `${historyPath}/${encodeURIComponent(selected.workspace_id)}/restore-preview`,
      )
      if (attempt !== impactRequest.current) return
      if (
        next.item_id !== itemId ||
        next.source_workspace_id !== selected.workspace_id ||
        next.base_revision !== history.base_revision ||
        next.base_story_revision !== history.base_story_revision
      )
        throw new Error('设定基准已有变化，请重新读取设定和历史。')
      setImpact(next)
    } catch (failure) {
      if (attempt === impactRequest.current)
        setError(failure instanceof Error ? failure.message : '暂无法读取设定恢复影响。')
    } finally {
      if (attempt === impactRequest.current) setChecking(false)
    }
  }
  return (
    <section aria-label="条目版本对照">
      {error && <p role="alert">{error}</p>}
      {loading && <p role="status">正在读取条目历史</p>}
      {stale && <p className="small">设定基准已有变化，请重新读取后对照。</p>}
      <button type="button" disabled={loading || busy} onClick={() => void refresh()}>
        重新读取设定和历史
      </button>
      {history && (
        <>
          <label>
            比较修订
            <select
              value={selectedId}
              onChange={(event) => {
                resetImpact()
                setSelectedId(event.target.value)
              }}
            >
              {history.entries.map((entry) => (
                <option key={entry.workspace_id} value={entry.workspace_id}>
                  修订 {entry.revision} · {entry.state?.item.title || '已删除'}
                  {entry.restored_from ? ' · 由历史恢复' : ''}
                </option>
              ))}
              {!history.entries.length && <option value="">没有可比较的修订</option>}
            </select>
          </label>
          {history.next_before_revision && (
            <button type="button" disabled={loading || stale || busy} onClick={() => void loadOlder()}>
              读取更早修订
            </button>
          )}
          {selected && (
            <>
              <div
                style={{
                  display: 'grid',
                  gap: '16px',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 16rem), 1fr))',
                }}
              >
                <ItemVersion title={`当前保存版 · 修订 ${history.base_revision}`} value={history.current} />
                <ItemVersion title={`历史版本 · 修订 ${selected.revision}`} value={selected.state} />
              </div>
              {selected.restored_from && <p className="small">恢复来源版本编号 {selected.restored_from}</p>}
              {dirty ? (
                <p className="small">请先保存或放弃本窗口的未保存编辑。</p>
              ) : unchanged ? (
                <p className="small">此历史版本与当前条目一致。</p>
              ) : null}
              <button type="button" disabled={blocked || checking} onClick={() => void checkImpact()}>
                {checking ? '正在预览设定恢复影响…' : '预览设定恢复影响'}
              </button>
              {currentImpact && <SettingRestoreImpact preview={currentImpact} />}
              <button
                type="button"
                disabled={!currentImpact}
                onClick={() => {
                  if (!currentImpact) return
                  const { base_revision, base_story_revision, source_workspace_id, basis_sha256 } =
                    currentImpact
                  resetImpact()
                  onRestore({ base_revision, base_story_revision, source_workspace_id, basis_sha256 })
                }}
              >
                恢复所选版本为设定草稿
              </button>
            </>
          )}
        </>
      )}
    </section>
  )
}
