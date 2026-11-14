import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { Notify } from './App'
import { confirmLeave, useLeaveGuard } from './leaveGuard'

type Segment = { title: string; start: number; end: number; same_title?: string[]; same_content?: string[] }
type Mapping = Segment & {
  action: 'append' | 'replace' | 'skip'
  chapter_id: string | null
  base_revision: number | null
  allow_duplicate: boolean
}
type Receipt = {
  id: string
  story_revision: number
  results: { action: string; title: string; revision?: number; start: number; end: number }[]
  note: string
}
type Preview = {
  id: string
  filename: string
  encoding: string
  raw_hash: string
  base_story_revision: number
  length_utf16: number
  coverage: string
  segments: Segment[]
  existing: { id: string; title: string; revision: number; position: number }[]
  receipt: Receipt | null
  draft: { revision: number; mappings: Mapping[] } | null
}
type History = { id: string; filename: string; created: number; committed: number }
const mapping = (s: Segment): Mapping => ({
  ...s,
  action: 'append',
  chapter_id: null,
  base_revision: null,
  allow_duplicate: false,
})
function mappingSignature(rows: Mapping[]) {
  return JSON.stringify(
    rows.map(({ title, start, end, action, chapter_id, base_revision, allow_duplicate }) => ({
      title,
      start,
      end,
      action,
      chapter_id,
      base_revision,
      allow_duplicate,
    })),
  )
}
const actions = { append: '追加', replace: '替换', skip: '跳过' }

export function ManuscriptImport({
  branchId,
  notify,
  onImported,
  onClose,
}: {
  branchId: string
  notify: Notify
  onImported: () => Promise<unknown>
  onClose: () => void
}) {
  const [encoding, setEncoding] = useState('utf-8-sig')
  const [preview, setPreview] = useState<Preview>()
  const [mappings, setMappings] = useState<Mapping[]>([])
  const [baseline, setBaseline] = useState(mappingSignature([]))
  const dirty = mappingSignature(mappings) !== baseline
  useLeaveGuard(dirty, '导入章界与映射')
  const [history, setHistory] = useState<History[]>([])
  const [selected, setSelected] = useState(0)
  const [content, setContent] = useState('')
  const [splitAt, setSplitAt] = useState<number>()
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [receipt, setReceipt] = useState<Receipt>()
  const pending = useRef<{ signature: string; key: string } | null>(null)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    void api<History[]>(`/branches/${branchId}/import-previews`)
      .then((rows) => {
        if (alive.current) setHistory(rows)
      })
      .catch(notify)
    return () => {
      alive.current = false
    }
  }, [branchId, notify])
  async function perform(fn: () => Promise<void>) {
    if (busy) return
    setBusy(true)
    try {
      await fn()
    } catch (e) {
      notify(e)
      if (alive.current) setMessage(e instanceof Error ? e.message : '操作失败，映射保留。')
    } finally {
      if (alive.current) setBusy(false)
    }
  }
  function load(row: Preview) {
    setPreview(row)
    const values = row.draft?.mappings || row.segments.map(mapping)
    setMappings(values)
    setBaseline(mappingSignature(values))
    setReceipt(row.receipt || undefined)
    setSelected(0)
    setContent('')
    pending.current = null
    setMessage('请核对编码、章界和每段操作。确认前不改变稿件。')
  }
  const current = mappings[selected]
  useEffect(() => {
    let active = true
    setContent('')
    setSplitAt(undefined)
    if (preview && current)
      void api<{ content: string }>(`/branches/${branchId}/import-previews/${preview.id}/read`, 'POST', {
        title: current.title || '预览段',
        start: current.start,
        end: current.end,
      })
        .then((row) => {
          if (active) setContent(row.content)
        })
        .catch(notify)
    return () => {
      active = false
    }
  }, [preview?.id, selected, current?.start, current?.end, branchId, notify])
  function edit(values: Partial<Mapping>) {
    setMappings((old) => old.map((m, i) => (i === selected ? { ...m, ...values } : m)))
  }
  return (
    <section className="card manuscript-import" aria-label="长篇拆章导入">
      <h2>长篇拆章导入</h2>
      <button
        disabled={busy}
        onClick={() => {
          if (!dirty || confirmLeave()) onClose()
        }}
      >
        关闭导入工作区
      </button>
      <p>预览 → 调整章界 → 追加／替换／跳过 → 确认回执。原文件与替换前修订保留，不调用模型。</p>
      <p role="status">{message}</p>
      <label>
        文件编码
        <select value={encoding} onChange={(e) => setEncoding(e.target.value)}>
          <option value="utf-8-sig">UTF-8（含BOM）</option>
          <option value="utf-16">UTF-16（含BOM）</option>
          <option value="gb18030">GB18030／GBK</option>
        </select>
      </label>
      <label>
        选择长篇TXT或Markdown（20MB内）
        <input
          type="file"
          accept=".txt,.md"
          disabled={busy}
          onChange={(e) => {
            const file = e.target.files?.[0]
            e.target.value = ''
            if (file && (!dirty || confirmLeave()))
              void perform(async () => {
                const form = new FormData()
                form.append('file', file)
                form.append('encoding', encoding)
                const row = await api<Preview>(`/branches/${branchId}/import-previews`, 'POST', form)
                if (alive.current) {
                  load(row)
                  setHistory(await api(`/branches/${branchId}/import-previews`))
                }
              })
          }}
        />
      </label>
      <details>
        <summary>已保存预览与导入回执</summary>
        {history.map((row) => (
          <button
            key={row.id}
            disabled={busy}
            onClick={() => {
              if (!dirty || confirmLeave())
                void perform(async () => load(await api(`/branches/${branchId}/import-previews/${row.id}`)))
            }}
          >
            {row.filename} · {row.committed ? '已导入' : '未确认'} ·{' '}
            {new Date(row.created * 1000).toLocaleString()}
          </button>
        ))}
      </details>
      {preview && (
        <>
          <p>
            {preview.filename} · {preview.encoding} · {mappings.length}段 · {preview.length_utf16}文本位置
          </p>
          <p>{preview.coverage}</p>
          <small>原文件SHA-256：{preview.raw_hash}</small>
        </>
      )}
      {preview && !receipt && current && (
        <>
          <label>
            选择预览段
            <select value={selected} onChange={(e) => setSelected(Number(e.target.value))}>
              {mappings.map((m, i) => (
                <option key={i} value={i}>
                  {i + 1} · {m.title} · {actions[m.action]}
                </option>
              ))}
            </select>
          </label>
          <label>
            章节标题
            <input value={current.title} maxLength={200} onChange={(e) => edit({ title: e.target.value })} />
          </label>
          <div className="form-grid">
            <label>
              本段开始位置
              <input
                type="number"
                value={current.start}
                onChange={(e) => {
                  const value = Number(e.target.value)
                  setMappings((old) =>
                    old.map((m, i) =>
                      i === selected ? { ...m, start: value } : i === selected - 1 ? { ...m, end: value } : m,
                    ),
                  )
                }}
              />
            </label>
            <label>
              本段结束位置
              <input
                type="number"
                value={current.end}
                onChange={(e) => {
                  const value = Number(e.target.value)
                  setMappings((old) =>
                    old.map((m, i) =>
                      i === selected ? { ...m, end: value } : i === selected + 1 ? { ...m, start: value } : m,
                    ),
                  )
                }}
              />
            </label>
          </div>
          <label>
            本段原文（可将光标放到新的章界）
            <textarea
              rows={12}
              readOnly
              value={content}
              onSelect={(e) => setSplitAt(current.start + e.currentTarget.selectionStart)}
            />
          </label>
          <div className="actions">
            <button
              disabled={splitAt === undefined || splitAt <= current.start || splitAt >= current.end}
              onClick={() => {
                if (splitAt === undefined) return
                setMappings((old) =>
                  old.flatMap((m, i) =>
                    i === selected
                      ? [
                          { ...m, end: splitAt },
                          mapping({ title: m.title + '（续）', start: splitAt, end: m.end }),
                        ]
                      : [m],
                  ),
                )
              }}
            >
              在光标处拆成两段
            </button>
            <button
              disabled={selected === mappings.length - 1}
              onClick={() =>
                setMappings((old) =>
                  old.flatMap((m, i) =>
                    i === selected ? [{ ...m, end: old[i + 1].end }] : i === selected + 1 ? [] : [m],
                  ),
                )
              }
            >
              合并下一段（沿用本段映射）
            </button>
          </div>
          {current.same_title?.length || current.same_content?.length ? (
            <p className="notice">
              原建议段匹配：同名 {current.same_title?.length || 0} 章，相同正文{' '}
              {current.same_content?.length || 0} 章。章界改变后以确认时校验为准，不按同名自动替换。
            </p>
          ) : null}
          <label>
            本段导入方式
            <select
              value={current.action}
              onChange={(e) =>
                edit({ action: e.target.value as Mapping['action'], chapter_id: null, base_revision: null })
              }
            >
              <option value="append">追加新章节</option>
              <option value="replace">替换指定章节（保留历史）</option>
              <option value="skip">明确跳过此段</option>
            </select>
          </label>
          {current.action === 'replace' && (
            <label>
              替换目标章节
              <select
                value={current.chapter_id || ''}
                onChange={(e) => {
                  const target = preview.existing.find((c) => c.id === e.target.value)
                  edit({ chapter_id: target?.id || null, base_revision: target?.revision ?? null })
                }}
              >
                <option value="">请明确选择替换目标</option>
                {preview.existing.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.position} · {c.title} · 修订{c.revision}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="check">
            <input
              type="checkbox"
              checked={current.allow_duplicate}
              onChange={(e) => edit({ allow_duplicate: e.target.checked })}
            />
            明确允许本段与已有或本批正文重复
          </label>
          <p>
            本次范围：追加 {mappings.filter((m) => m.action === 'append').length} 段；替换{' '}
            {mappings.filter((m) => m.action === 'replace').length} 段；跳过{' '}
            {mappings.filter((m) => m.action === 'skip').length} 段。所有段须连续覆盖原文。
          </p>
          <button
            disabled={busy}
            onClick={() =>
              void perform(async () => {
                const values = mappings.map(
                  ({ title, start, end, action, chapter_id, base_revision, allow_duplicate }) => ({
                    title,
                    start,
                    end,
                    action,
                    chapter_id,
                    base_revision,
                    allow_duplicate,
                  }),
                )
                const value = await api<Preview>(
                  `/branches/${branchId}/import-previews/${preview.id}/draft`,
                  'PATCH',
                  { base_revision: preview.draft?.revision || 0, mappings: values },
                )
                if (alive.current) {
                  setPreview(value)
                  setBaseline(mappingSignature(values))
                  setMessage('章界和映射草稿已保存，刷新后可从预览历史恢复。')
                }
              })
            }
          >
            保存章界与映射草稿
          </button>
          <button
            className="primary"
            disabled={
              busy ||
              mappings.every((m) => m.action === 'skip') ||
              mappings.some((m) => m.action === 'replace' && !m.chapter_id)
            }
            onClick={() =>
              void perform(async () => {
                const values = {
                  base_story_revision: preview.base_story_revision,
                  draft_revision: preview.draft?.revision || 0,
                  mappings: mappings.map(
                    ({ title, start, end, action, chapter_id, base_revision, allow_duplicate }) => ({
                      title,
                      start,
                      end,
                      action,
                      chapter_id,
                      base_revision,
                      allow_duplicate,
                    }),
                  ),
                }
                const signature = JSON.stringify(values)
                if (pending.current?.signature !== signature)
                  pending.current = { signature, key: crypto.randomUUID() }
                const result = await api<Receipt>(
                  `/branches/${branchId}/import-previews/${preview.id}/confirm`,
                  'POST',
                  { ...values, client_request_id: pending.current.key },
                )
                if (!alive.current) return
                setReceipt(result)
                setBaseline(mappingSignature(values.mappings))
                setMessage(
                  '导入已完成，回执保留。目录已更新，当前编辑器输入仍保留，请从目录选择要继续的章节。',
                )
                await onImported()
                setHistory(await api(`/branches/${branchId}/import-previews`))
              })
            }
          >
            确认以上范围并导入（替换保留原稿）
          </button>
        </>
      )}
      {receipt && (
        <section className="notice" aria-label="长篇导入回执">
          <h3>导入回执</h3>
          <p>{receipt.note}</p>
          <p>
            故事修订 {receipt.story_revision} · 回执 {receipt.id}
          </p>
          <ol>
            {receipt.results.map((r, i) => (
              <li key={i}>
                {actions[r.action as keyof typeof actions] || r.action} · {r.title} · {r.start}—{r.end}
                {r.revision !== undefined ? ` · 正文修订${r.revision}` : ''}
              </li>
            ))}
          </ol>
        </section>
      )}
    </section>
  )
}
