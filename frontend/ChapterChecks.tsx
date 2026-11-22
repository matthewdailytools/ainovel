import { useEffect, useRef, useState } from 'react'
import { api, label, type Chapter, type Task } from './api'
import type { components } from './api-schema'
import type { WritingSetting } from './WritingContext'
import { useLeaveGuard } from './leaveGuard'

export const chapterCheckKind = 'chapter_item_check'

type CheckResult = components['schemas']['CheckResult']
type CheckItem = components['schemas']['CheckItem']
type SourceKind = CheckItem['changed_sources'][number]
type Checks = components['schemas']['ChapterChecks']

const sourceLabels: Record<SourceKind, string> = {
  chapter: '本章正文',
  previous: '上一章正文',
  setting: '设定依据',
}
const resultLabels = { pass: '符合要求', fail: '发现问题', unknown: '待核对' }

function ResultEvidence({ report }: { report: CheckResult }) {
  const { chapter, previous, setting } = report.sources
  return (
    <div className="writing-check-result">
      <p className="small">
        第 {chapter.position} 章 · 修订 {chapter.revision}
        {previous && ` / 第 ${previous.position} 章 · 修订 ${previous.revision}`}
      </p>
      <p>{report.reason}</p>
      {report.evidence.map((entry, index) => (
        <figure key={index}>
          <figcaption className="small muted">
            {sourceLabels[entry.source]} · 原文位置 {entry.start}–{entry.end}
          </figcaption>
          <blockquote>{entry.quote}</blockquote>
        </figure>
      ))}
      {setting && (
        <details>
          <summary>当时的设定依据 · {setting.title}</summary>
          <blockquote>{setting.text}</blockquote>
          {setting.source && (
            <>
              <p className="small muted">原文修订 {setting.source.revision}</p>
              <blockquote>{setting.source.quote}</blockquote>
            </>
          )}
        </details>
      )}
    </div>
  )
}

export function ChapterChecks({
  chapter,
  settings,
  saved,
  disabled,
  account,
  modelReady,
  tasks,
  onTask,
}: {
  chapter: Chapter
  settings?: WritingSetting[]
  saved: boolean
  disabled: boolean
  account: string
  modelReady: boolean
  tasks: Task[]
  onTask: (task: Task) => void
}) {
  const [request, setRequest] = useState(0)
  const [data, setData] = useState<{ key: string; checks?: Checks; error?: string }>()
  const [form, setForm] = useState(false)
  const [title, setTitle] = useState('')
  const [condition, setCondition] = useState('')
  const [settingId, setSettingId] = useState('')
  const [includePrevious, setIncludePrevious] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState('')
  const [taskId, setTaskId] = useState('')
  const [pendingCreation, setPendingCreation] = useState('')
  const pendingRequest = useRef<{ signature: string; id: string } | undefined>(undefined)
  const path = `/chapters/${chapter.id}/checks`
  const revision = chapter.revision
  const checkTask = tasks.find((task) => task.id === taskId)
  const status = checkTask?.status
  const taskChanges = tasks
    .filter((task) => task.kind === chapterCheckKind)
    .map((task) => `${task.id}:${task.status}`)
    .sort()
    .join(',')
  const key = `${revision}:${request}:${taskChanges}`
  const current = data?.key === key ? data : undefined
  const checks = current?.checks ?? (!current ? data?.checks : undefined)
  const previous = checks?.previous
  const formLocked = submitting || !!pendingCreation
  const canRun = !disabled && saved && modelReady && !formLocked && !!current?.checks
  useLeaveGuard(
    form && !pendingCreation && !!(title || condition || settingId || includePrevious),
    '未提交的本章校验要求',
  )

  useEffect(() => {
    let active = true
    void api<Checks>(path)
      .then((checks) => {
        if (!active) return
        if (checks.chapter_revision !== revision) throw new Error('保存稿已在别处更新。请重新打开本章。')
        setData({ key, checks })
      })
      .catch((error: unknown) => {
        if (active) setData({ key, error: error instanceof Error ? error.message : '本章校验读取失败。' })
      })
    return () => {
      active = false
    }
  }, [key, path, revision])
  useEffect(() => {
    const refresh = () => setRequest((value) => value + 1)
    window.addEventListener('focus', refresh)
    return () => window.removeEventListener('focus', refresh)
  }, [])

  function resetForm() {
    setForm(false)
    setTitle('')
    setCondition('')
    setSettingId('')
    setIncludePrevious(false)
    setSubmitError('')
  }

  useEffect(() => {
    if (
      !pendingCreation ||
      taskId !== pendingCreation ||
      !status ||
      status === 'queued' ||
      status === 'running'
    )
      return
    setPendingCreation('')
    if (status === 'succeeded') resetForm()
  }, [pendingCreation, status, taskId])

  async function submit(item?: CheckItem) {
    if (!canRun) return
    const setting = settings?.find((entry) => entry.id === settingId && entry.source_current)
    if (!item && (!condition.trim() || (settingId && !setting) || (includePrevious && !previous))) return
    const target = item ? `${path}/${item.id}/recheck` : path
    const prior =
      previous && (item ? item.previous_chapter_id === previous.chapter_id : includePrevious)
        ? previous
        : undefined
    const basis = {
      base_revision: chapter.revision,
      account,
      ...(prior ? { previous_chapter_id: prior.chapter_id, previous_base_revision: prior.revision } : {}),
    }
    const body = item
      ? { ...basis, previous_result_id: item.latest.id }
      : {
          ...basis,
          title: title.trim() || undefined,
          condition: condition.trim(),
          include_previous: includePrevious,
          setting_id: setting?.id,
          expected_setting: setting,
        }
    const signature = JSON.stringify([target, body])
    if (pendingRequest.current?.signature !== signature)
      pendingRequest.current = { signature, id: crypto.randomUUID() }
    setSubmitting(true)
    setSubmitError('')
    try {
      const task = await api<Task>(target, 'POST', { ...body, client_request_id: pendingRequest.current.id })
      pendingRequest.current = undefined
      setTaskId(task.id)
      onTask(task)
      if (!item) setPendingCreation(task.id)
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : '校验任务提交失败。')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className="notice writing-workbench-section" aria-label="本章校验">
      <div className="section-title">
        <h3>本章校验</h3>
        <button type="button" onClick={() => setRequest((value) => value + 1)}>
          刷新校验
        </button>
      </div>
      {!saved && <p className="small">正文保存后可校验，当前结果以保存稿为准。</p>}
      {!modelReady && <p className="small muted">配置可用模型后可运行校验。已有报告可继续查看。</p>}
      {current?.error && <p role="alert">{current.error}</p>}
      {submitError && <p role="alert">{submitError}</p>}
      {checkTask && (
        <p role={checkTask.error ? 'alert' : 'status'} className="small">
          单项校验 · {checkTask.status === 'succeeded' ? '报告已生成' : label(checkTask.status)}
          {checkTask.error && ` · ${checkTask.error}`}
        </p>
      )}
      {!current && <p role="status">{checks ? '正在更新校验项，暂显示上次结果…' : '正在读取校验项…'}</p>}
      {checks && (
        <>
          {checks.items.length === 0 && <p className="small muted">本章尚无校验报告。</p>}
          {checks.items.map((item) => (
            <article className="fact" key={item.id}>
              <strong>{item.title}</strong>
              {item.condition !== item.title && <p>{item.condition}</p>}
              <p className="small">
                {item.status === 'stale'
                  ? `已过期 · ${item.changed_sources.map((source) => sourceLabels[source]).join('、')}有变动`
                  : resultLabels[item.latest.result]}
              </p>
              <p className="small muted">
                范围：本章保存稿{item.include_previous ? '、原相邻前章' : ''}
                {item.setting_id ? '、所选设定' : ''}。复检按模型用量计费，只生成报告。
              </p>
              <details>
                <summary>{item.status === 'stale' ? '过期结果的依据' : '查看校验依据'}</summary>
                <p className="small">原结论 · {resultLabels[item.latest.result]}</p>
                <ResultEvidence report={item.latest} />
              </details>
              <div className="writing-workbench-actions">
                <button type="button" disabled={!canRun} onClick={() => void submit(item)}>
                  复检此项
                </button>
              </div>
              {item.history.length > 0 && (
                <details>
                  <summary>历次校验（{item.history.length}）</summary>
                  {item.history.map((report) => (
                    <details key={report.id}>
                      <summary>
                        修订 {report.base_revision}
                        {report.sources.previous && ` / 前章修订 ${report.sources.previous.revision}`}
                        {' · '}
                        {resultLabels[report.result]}
                      </summary>
                      <ResultEvidence report={report} />
                    </details>
                  ))}
                </details>
              )}
            </article>
          ))}
          {form ? (
            <form
              onSubmit={(event) => {
                event.preventDefault()
                void submit()
              }}
            >
              <label>
                校验名称（可选）
                <input
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  maxLength={100}
                  disabled={formLocked}
                />
              </label>
              <label>
                校验要求
                <textarea
                  value={condition}
                  onChange={(event) => setCondition(event.target.value)}
                  maxLength={2000}
                  rows={3}
                  required
                  disabled={formLocked}
                />
              </label>
              <label>
                设定依据（可选）
                <select
                  value={settingId}
                  onChange={(event) => setSettingId(event.target.value)}
                  disabled={formLocked}
                >
                  <option value="">不引用设定</option>
                  {settings
                    ?.filter((setting) => setting.source_current)
                    .map((setting) => (
                      <option value={setting.id} key={setting.id}>
                        {setting.title}
                      </option>
                    ))}
                </select>
              </label>
              {previous && (
                <label className="check">
                  <input
                    type="checkbox"
                    checked={includePrevious}
                    onChange={(event) => setIncludePrevious(event.target.checked)}
                    disabled={formLocked}
                  />
                  同时核对上一章（{previous.title}）
                </label>
              )}
              <p className="small muted">将发送本章保存稿、校验要求及所选资料给当前模型，按模型用量计费。</p>
              <div className="writing-workbench-actions">
                <button type="submit" disabled={!canRun || !condition.trim()}>
                  运行此项校验
                </button>
                <button type="button" onClick={resetForm} disabled={formLocked}>
                  取消
                </button>
              </div>
            </form>
          ) : (
            <button type="button" onClick={() => setForm(true)}>
              新建校验项
            </button>
          )}
        </>
      )}
    </section>
  )
}
