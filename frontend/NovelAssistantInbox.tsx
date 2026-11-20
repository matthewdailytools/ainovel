import { useEffect, useId, useRef, useState } from 'react'
import {
  novelAssistantStages,
  novelAssistantValueText,
  readNovelAssistantDeliveries,
  readNovelAssistantDelivery,
  type NovelAssistantDelivery,
} from './novelAssistantModel'
import { useDeliveryTarget, type CreationBench } from './WorkbenchAssistant'
import './novelAssistantInbox.css'

export type DeliveryLocation = {
  id: string
  stage: CreationBench
  scopeId: string | null
  field: string
}

export function NovelAssistantInbox({
  branchId,
  stage,
  revision,
  selected,
  onViewSource,
}: {
  branchId: string
  stage: CreationBench
  revision: number
  selected?: DeliveryLocation & { request: number }
  onViewSource: (turnId: string) => void
}) {
  const headingId = useId()
  const [rows, setRows] = useState<NovelAssistantDelivery[]>()
  const [opened, setOpened] = useState(false)
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState('')
  const [failure, setFailure] = useState('')
  const [retry, setRetry] = useState(0)
  const [comparison, setComparison] = useState<{ id: string; draft: string }>()
  const findTarget = useDeliveryTarget()
  const currentTarget = useRef(findTarget)
  const alive = useRef(false)
  const focusedRequest = useRef<number | undefined>(undefined)
  const selectedCard = useRef<HTMLElement>(null)
  currentTarget.current = findTarget

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])
  useEffect(() => {
    let active = true
    void readNovelAssistantDeliveries(branchId, stage)
      .then((deliveries) => {
        if (active) setRows(deliveries)
      })
      .catch((error: unknown) => {
        if (active) setFailure(error instanceof Error ? error.message : '无法读取这台的作品对话建议。')
      })
    return () => {
      active = false
    }
  }, [branchId, stage, revision, retry])
  useEffect(() => {
    if (selected && focusedRequest.current !== selected.request) setOpened(true)
  }, [selected])
  useEffect(() => {
    if (!opened || !selected || focusedRequest.current === selected.request || !selectedCard.current) return
    focusedRequest.current = selected.request
    selectedCard.current.scrollIntoView({ block: 'nearest' })
    selectedCard.current.focus({ preventScroll: true })
  }, [opened, rows, selected])

  async function copy(row: NovelAssistantDelivery) {
    setNotice('')
    setFailure('')
    try {
      await navigator.clipboard.writeText(novelAssistantValueText(row.value))
      if (alive.current) setNotice('已复制建议，可粘贴到目标编辑器。')
    } catch {
      if (alive.current) setFailure('无法写入剪贴板，请选中建议文字后复制。')
    }
  }

  async function load(row: NovelAssistantDelivery, confirmed?: { draft: string }) {
    if (busy) return
    setBusy(row.id)
    setNotice('')
    setFailure('')
    try {
      const fresh = await readNovelAssistantDelivery(branchId, row.id)
      if (!alive.current) return
      setRows((previous) => previous?.map((entry) => (entry.id === row.id ? fresh : entry)))
      if (!fresh.basis_current) {
        setComparison(undefined)
        setFailure('目标依据已变化。请从原对话重新选择去向并预览。')
        return
      }
      const target = currentTarget.current?.(fresh)
      if (!target?.apply || typeof target.value !== 'string' || typeof fresh.value !== 'string') {
        setComparison(undefined)
        setNotice('请先打开这一对象的字段编辑区，再回来比较草稿。')
        return
      }
      if (!confirmed || confirmed.draft !== target.value) {
        setComparison({ id: row.id, draft: target.value })
        if (confirmed) setNotice('当前编辑草稿已变化，请重新比较。')
        return
      }
      if (!target.apply(fresh.value)) {
        setComparison(undefined)
        setFailure('目标当前不可编辑，请打开并核对目标字段后重试。')
        return
      }
      setComparison(undefined)
      setNotice('已载入当前编辑草稿，可在本台继续编辑和保存。')
      const element = target.element?.current
      if (element) {
        for (let parent = element.parentElement; parent; parent = parent.parentElement) {
          if (parent instanceof HTMLDetailsElement) parent.open = true
        }
        element.focus()
      }
    } catch (error) {
      if (alive.current) setFailure(error instanceof Error ? error.message : '暂时无法核对目标草稿。')
    } finally {
      if (alive.current) setBusy('')
    }
  }

  if (!rows?.length && !selected && !failure) return null
  return (
    <section className="novel-assistant-inbox" aria-labelledby={headingId}>
      <details open={opened} onToggle={(event) => setOpened(event.currentTarget.open)}>
        <summary id={headingId}>作品对话建议{rows?.length ? ` · ${rows.length} 条` : ''}</summary>
        {failure && (
          <p role="alert">
            {failure}{' '}
            <button
              type="button"
              onClick={() => {
                setFailure('')
                setRetry((value) => value + 1)
              }}
            >
              刷新建议
            </button>
          </p>
        )}
        {notice && <p role="status">{notice}</p>}
        {rows === undefined && !failure && <p role="status">正在读取本台收到的建议…</p>}
        {rows && !rows.length && <p>本台没有收到建议。</p>}
        {rows?.map((row) => {
          const checked = comparison?.id === row.id ? comparison : undefined
          return (
            <article key={row.id} tabIndex={-1} ref={selected?.id === row.id ? selectedCard : undefined}>
              <h3>
                {row.target_label} · {row.field_label}
              </h3>
              <p className="novel-assistant-inbox-status">
                {row.basis_current ? '已送达 · 待处理' : '目标依据已更新 · 请重新核对'}
              </p>
              <blockquote>{novelAssistantValueText(row.value)}</blockquote>
              {row.basis_current && stage !== 'M5' && (
                <details className="novel-assistant-inbox-basis">
                  <summary>查看送达时的内容</summary>
                  <blockquote>{novelAssistantValueText(row.before) || '原字段为空'}</blockquote>
                </details>
              )}
              {checked && (
                <div className="novel-assistant-inbox-compare">
                  <b>当前编辑草稿</b>
                  <blockquote>{checked.draft || '当前字段为空'}</blockquote>
                  <button disabled={!!busy} type="button" onClick={() => void load(row, checked)}>
                    载入编辑草稿
                  </button>
                  <button type="button" onClick={() => setComparison(undefined)}>
                    取消
                  </button>
                </div>
              )}
              <div className="actions">
                {row.basis_current && stage !== 'M5' && !checked && (
                  <button type="button" disabled={!!busy} onClick={() => void load(row)}>
                    {busy === row.id ? '正在核对…' : '与编辑草稿比较'}
                  </button>
                )}
                <button type="button" onClick={() => void copy(row)}>
                  复制建议
                </button>
                <button type="button" onClick={() => onViewSource(row.turn_id)}>
                  回到原对话
                </button>
              </div>
            </article>
          )
        })}
      </details>
      {selected && rows?.length && !rows.some((row) => row.id === selected.id) ? (
        <p role="status">未在{novelAssistantStages[stage]}找到这条建议。</p>
      ) : null}
    </section>
  )
}
