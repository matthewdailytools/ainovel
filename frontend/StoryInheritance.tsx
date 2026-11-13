import { useEffect, useState } from 'react'
import { api } from './api'
import type { components } from './api-schema'
import type { Notify } from './App'

type RecordPayload =
  | components['schemas']['Character']
  | components['schemas']['Event']
  | components['schemas']['State']
  | components['schemas']['Promise']
  | components['schemas']['Relationship']
type Item = {
  id: string
  title: string
  kind: RecordPayload['kind']
  record: RecordPayload
  eligible: boolean
  reason: string
  required_ids: string[]
}
type Preview = { source_branch_id: string; source_story_revision: number; coverage: string; items: Item[] }
export type InheritanceSelection = {
  source_branch_id: string
  source_story_revision: number
  selected_ids: string[]
}
const kinds = {
  character: '人物档案',
  event: '事件计划',
  state: '状态计划',
  promise: '伏笔计划',
  relationship: '关系计划',
}

function description(p: RecordPayload) {
  switch (p.kind) {
    case 'character':
      return `别名：${p.aliases?.join('、') || '无'}\n档案：${p.profile || '未填写'}`
    case 'event':
      return `计划：${p.story_time || '未知时间'} · ${p.thread || '主线'} · 顺序${p.story_order ?? '未知'}`
    case 'state':
      return `计划${p.key}：${p.value}（${p.audience === 'character' ? '人物' : p.audience === 'reader' ? '读者' : '作者'}视角）`
    case 'promise':
      return `计划阶段：${p.stages.map((s) => s.note || '尚未埋设').join('；')}`
    case 'relationship':
      return `计划关系：${p.relation}`
  }
}

export function StoryInheritance({
  branchId,
  busy,
  notify,
  onChange,
}: {
  branchId: string
  busy: boolean
  notify: Notify
  onChange: (value: InheritanceSelection | undefined) => void
}) {
  const [preview, setPreview] = useState<Preview>()
  const [selected, setSelected] = useState<string[]>([])
  const [epoch, setEpoch] = useState(0)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    setPreview(undefined)
    setSelected([])
    setError('')
    onChange(undefined)
    void api<Preview>(`/branches/${branchId}/story-inheritance`)
      .then((value) => {
        if (!active) return
        setPreview(value)
        onChange({
          source_branch_id: value.source_branch_id,
          source_story_revision: value.source_story_revision,
          selected_ids: [],
        })
      })
      .catch((e) => {
        if (active) {
          setError('继承目录读取失败，可重新读取；未勾选时仍可启动独立批次。')
          notify(e)
        }
      })
    return () => {
      active = false
    }
  }, [branchId, epoch, onChange, notify])
  function select(ids: string[]) {
    if (!preview) return
    setSelected(ids)
    onChange({
      source_branch_id: preview.source_branch_id,
      source_story_revision: preview.source_story_revision,
      selected_ids: ids,
    })
  }
  const names = new Map(preview?.items.map((i) => [i.id, i.title]))
  const eligible = preview?.items.filter((i) => i.eligible) || []
  return (
    <details className="notice">
      <summary>新版本继承人物与结构计划 · 已选{selected.length}项</summary>
      <p>
        默认不继承。勾选表示你确认将这些档案或计划作为新版本设定；不表示旧版经历已经发生。原稿、实际状态和来源选区不会复制。
      </p>
      <button type="button" disabled={busy} onClick={() => setEpoch((e) => e + 1)}>
        重新读取目录并清空勾选
      </button>
      {error && <p role="alert">{error}</p>}
      {!preview && !error && <p role="status">正在读取来源工作版本…</p>}
      {preview && (
        <>
          <p className="small">
            来源故事修订{preview.source_story_revision} · 可选{eligible.length}
            项；人物和计划将获得本版独立身份。
          </p>
          {!eligible.length && <p>没有可继承的有效人物或计划；仍可直接从简报开始。</p>}
          {eligible.map((item) => (
            <article className="fact" key={item.id}>
              <label className="check">
                <input
                  type="checkbox"
                  disabled={busy || (!selected.includes(item.id) && selected.length >= 300)}
                  checked={selected.includes(item.id)}
                  onChange={(e) =>
                    select(
                      e.target.checked ? [...selected, item.id] : selected.filter((id) => id !== item.id),
                    )
                  }
                />
                继承{kinds[item.kind]}：{item.title}
              </label>
              <details>
                <summary>核对本版设定内容</summary>
                <pre>
                  {description(item.record)}
                  {item.record.notes ? '\n说明：' + item.record.notes : ''}
                  {item.record.volume ? '\n卷别：' + item.record.volume : ''}
                </pre>
              </details>
              {!!item.required_ids.length && (
                <p className="small">
                  需一并明确勾选：{item.required_ids.map((id) => names.get(id) || '缺失关联').join('、')}
                </p>
              )}
            </article>
          ))}
          {!!preview.items.filter((i) => !i.eligible).length && (
            <details>
              <summary>查看不能继承的历史或失效记录</summary>
              {preview.items
                .filter((i) => !i.eligible)
                .map((i) => (
                  <p key={i.id}>
                    {i.title}：{i.reason}
                  </p>
                ))}
            </details>
          )}
        </>
      )}
    </details>
  )
}
