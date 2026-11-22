import { useEffect, useState } from 'react'
import { api, type Chapter } from './api'
import type { components } from './api-schema'
import { useLeaveGuard } from './leaveGuard'

export type WritingSetting = components['schemas']['SettingBasis']
type WritingContext = components['schemas']['WritingContext']
type SettingPassage = components['schemas']['SettingPassage']

export function useWritingContext(branchId: string, chapter?: Chapter) {
  const [request, setRequest] = useState(0)
  const [state, setState] = useState<{
    path: string
    revision: number
    request: number
    data?: WritingContext
    error?: string
  }>()
  const path = chapter && `/branches/${branchId}/chapters/${chapter.id}/writing-context`
  const revision = chapter?.revision

  useEffect(() => {
    if (!path || revision === undefined) return
    let active = true
    void api<WritingContext>(path)
      .then((data) => {
        if (!active) return
        if (data.chapter_revision !== revision) throw new Error('保存稿已在别处更新。请重新打开本章。')
        setState({ path, revision, request, data })
      })
      .catch((error: unknown) => {
        if (active)
          setState({
            path,
            revision,
            request,
            error: error instanceof Error ? error.message : '章纲与设定读取失败。',
          })
      })
    return () => {
      active = false
    }
  }, [path, revision, request])
  useEffect(() => {
    const refresh = () => setRequest((value) => value + 1)
    window.addEventListener('focus', refresh)
    return () => window.removeEventListener('focus', refresh)
  }, [])

  const current =
    state?.path === path && state?.revision === revision && state?.request === request ? state : undefined
  return { data: current?.data, error: current?.error, reload: () => setRequest((value) => value + 1) }
}

export function ChapterOutline({
  branchId,
  chapterId,
  context,
  disabled,
}: {
  branchId: string
  chapterId: string
  context: ReturnType<typeof useWritingContext>
  disabled: boolean
}) {
  const [draft, setDraft] = useState<{ text: string; version: string | null }>()
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const outline = context.data?.outline
  useLeaveGuard(!!draft, '未保存的本章章纲')

  async function saveOutline(event: React.FormEvent) {
    event.preventDefault()
    if (!draft || !draft.text.trim() || submitting || disabled) return
    setError('')
    setSubmitting(true)
    try {
      await api(`/branches/${branchId}/chapters/${chapterId}/writing-context/outline`, 'PUT', {
        base_version: draft.version,
        text: draft.text.trim(),
      })
      setDraft(undefined)
      context.reload()
    } catch (error) {
      setError(error instanceof Error ? error.message : '章纲保存失败。')
    } finally {
      setSubmitting(false)
    }
  }

  const source = context.error
    ? '读取失败'
    : !context.data
      ? '读取中'
      : outline?.source === 'author'
        ? '作者章纲'
        : outline?.source === 'frozen_ai'
          ? '冻结 AI 章纲'
          : '暂无章纲'

  return (
    <details className="writing-outline" open>
      <summary>本章章纲 · {source}</summary>
      {context.error && (
        <p role="alert">
          {context.error} <button onClick={context.reload}>重新读取</button>
        </p>
      )}
      {outline && (
        <>
          <p className="small muted" title={outline.version}>
            版本 {outline.version.slice(0, 12)}
          </p>
          {!draft && <p className="writing-outline-text">{outline.text}</p>}
        </>
      )}
      {context.data && !outline && !draft && (
        <p className="small muted">可以直接写作，也可以先记下本章安排。</p>
      )}
      {draft ? (
        <form onSubmit={(event) => void saveOutline(event)}>
          <label>
            本章安排
            <textarea
              value={draft.text}
              onChange={(event) => setDraft({ ...draft, text: event.target.value })}
              placeholder="记下本章目标、阻碍、转折和结果"
              rows={4}
              maxLength={5000}
              required
              disabled={submitting}
            />
          </label>
          {error && <p role="alert">{error}</p>}
          <div className="writing-workbench-actions">
            <button type="submit" disabled={!draft.text.trim() || submitting || disabled}>
              {submitting ? '正在保存章纲…' : '保存章纲'}
            </button>
            <button
              type="button"
              disabled={submitting}
              onClick={() => {
                setDraft(undefined)
                setError('')
                context.reload()
              }}
            >
              取消编辑
            </button>
          </div>
        </form>
      ) : (
        context.data && (
          <button
            type="button"
            disabled={disabled}
            onClick={() => setDraft({ text: outline?.text ?? '', version: outline?.version ?? null })}
          >
            {outline?.source === 'author' ? '编辑章纲' : '记下作者章纲'}
          </button>
        )
      )}
    </details>
  )
}

function SourcePassage({
  branchId,
  chapterId,
  settingId,
  source,
}: {
  branchId: string
  chapterId: string
  settingId: string
  source: NonNullable<WritingSetting['source']>
}) {
  const [passage, setPassage] = useState<SettingPassage>()
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function locate() {
    setLoading(true)
    setError('')
    try {
      const target = `/branches/${branchId}/chapters/${chapterId}/writing-context/settings/${encodeURIComponent(settingId)}/source`
      const result = await api<SettingPassage>(`${target}?revision=${source.revision}&start=${source.start}`)
      if (result.quote !== source.quote) throw new Error('设定原文依据已改变，请刷新后再定位。')
      setPassage(result)
    } catch (error) {
      setError(error instanceof Error ? error.message : '来源原文读取失败。')
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <button type="button" onClick={() => void locate()} disabled={loading || !!passage}>
        {loading ? '正在定位…' : '定位来源原文'}
      </button>
      {error && <p role="alert">{error}</p>}
      {passage && (
        <blockquote aria-label="定位的来源原文">
          {passage.before}
          <mark>{passage.quote}</mark>
          {passage.after}
        </blockquote>
      )}
    </>
  )
}

export function ChapterSettings({
  context,
  branchId,
  chapterId,
}: {
  context: ReturnType<typeof useWritingContext>
  branchId: string
  chapterId: string
}) {
  return (
    <section className="notice writing-workbench-section" aria-label="本章设定依据">
      <div className="section-title">
        <h3>本章设定依据</h3>
        <button type="button" onClick={context.reload}>
          刷新依据
        </button>
      </div>
      {context.error ? (
        <p role="alert">{context.error}</p>
      ) : !context.data ? (
        <p role="status">正在读取设定依据…</p>
      ) : context.data.settings.length === 0 ? (
        <p className="small muted">没有可用的已采用设定。</p>
      ) : (
        context.data.settings.map((setting) => (
          <article className="fact" key={setting.id}>
            <strong>{setting.title}</strong>
            {setting.source && !setting.source_current && <p className="small">原文依据待核对</p>}
            <details>
              <summary>查看设定原文</summary>
              <blockquote>{setting.text}</blockquote>
              {setting.source && (
                <>
                  <p className="small">
                    来源 · 第 {setting.source.position} 章 {setting.source.title} · 修订{' '}
                    {setting.source.revision}
                  </p>
                  <blockquote>{setting.source.quote}</blockquote>
                  <SourcePassage
                    key={`${setting.source.chapter_id}:${setting.source.revision}:${setting.source.start}:${setting.source.end}`}
                    branchId={branchId}
                    chapterId={chapterId}
                    settingId={setting.id}
                    source={setting.source}
                  />
                </>
              )}
            </details>
          </article>
        ))
      )}
    </section>
  )
}
