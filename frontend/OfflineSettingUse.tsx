import { useEffect, useState } from 'react'
import {
  confirmLocalSettingUse,
  readLocalBoundChapterSource,
  readLocalOutlineSettingEvidence,
  readLocalSettingUse,
  readLocalWorkflowHistory,
  withdrawLocalSettingUse,
  type LocalRevision,
} from './offlineStore'
import { captureStoryEvidence } from './storySyncModel'
import type { SettingUseRecord, SettingUseStatus } from './settingUseSyncModel'
import { eligibleOutlineSettings, outlineChapterPlans } from './outlineSyncModel'
import {
  OutlineSettingLedger,
  type SettingLedgerItem,
  type SettingLedgerLocation,
} from './OutlineSettingLedger'
import type { OfflineOutlineData } from './OfflineOutline'

export type SavedSelection = { revisionId: string; start: number; end: number; text: string }
const labels: Record<SettingUseStatus, string> = {
  current: '当前正文证据',
  outdated: '待按当前正文或设定重新核对',
  superseded: '已由新证据接替',
  withdrawn: '已撤回',
  missing: '原稿来源待补齐',
}

function Proof({ proof }: { proof: SettingUseRecord }) {
  const { version, source_device } = proof.entry
  return (
    <>
      <p style={{ overflowWrap: 'anywhere' }}>
        大纲 {version.outline_version_id} · 设定采用版 {version.settings_version_id} · 原稿{' '}
        {version.evidence.revision_id} · {version.evidence.start}–{version.evidence.end}
      </p>
      {source_device && <p>来源设备 {source_device}</p>}
      {proof.quote !== null && <blockquote>{proof.quote}</blockquote>}
    </>
  )
}

export function OfflineSettingUse({
  projectId,
  saved,
  selection,
  dirty,
  disabled,
  refreshToken,
  onUpdated,
}: {
  projectId: string
  saved: LocalRevision
  selection?: SavedSelection
  dirty: boolean
  disabled: boolean
  refreshToken: number
  onUpdated: () => void
}) {
  const [open, setOpen] = useState(false)
  const [view, setView] = useState<Awaited<ReturnType<typeof readLocalSettingUse>>>()
  const [itemId, setItemId] = useState('')
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const [reload, refresh] = useState(0)
  const chapterId = saved.chapter_id
  useEffect(() => {
    if (!open) return
    let active = true
    void readLocalSettingUse(projectId, chapterId).then(
      (value) => {
        if (active) setView(value)
      },
      (failure: Error) => {
        if (active) setError(failure.message)
      },
    )
    return () => {
      active = false
    }
  }, [open, projectId, chapterId, saved.id, refreshToken, reload])

  const basis = view?.basis
  const option = view?.options.find(({ id }) => id === itemId)
  const canUseSelection =
    !!selection &&
    !dirty &&
    selection.revisionId === saved.id &&
    basis?.revision.id === saved.id &&
    selection.end > selection.start &&
    !!selection.text.trim() &&
    saved.content.slice(selection.start, selection.end) === selection.text

  async function perform(action: () => Promise<unknown>, message: string) {
    setBusy(true)
    setError('')
    setStatus('')
    try {
      await action()
      setStatus(message)
      onUpdated()
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '未能保存设定使用证据。')
      refresh((value) => value + 1)
    } finally {
      setBusy(false)
    }
  }

  function confirm() {
    if (!basis || !option || !selection || !canUseSelection || busy || disabled) return
    void perform(
      async () =>
        confirmLocalSettingUse(projectId, chapterId, {
          outline_version_id: basis.outlineVersionId,
          settings_version_id: basis.settingsVersionId,
          item_id: option.id,
          parent_id: option.headId,
          chapter_sha256: basis.revision.sha256,
          evidence: await captureStoryEvidence(saved, selection.start, selection.end),
        }),
      '已确认当前正文使用该设定。',
    )
  }

  function withdraw(id: string) {
    if (busy || disabled || !window.confirm('撤回这条正文使用证据？原稿和历史证据会保留。')) return
    void perform(() => withdrawLocalSettingUse(projectId, chapterId, id), '已撤回正文使用证据。')
  }

  return (
    <details aria-label="离线设定正文证据" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>设定的正文使用证据</summary>
      {error && <p role="alert">{error}</p>}
      {status && <p role="status">{status}</p>}
      {!view && !error && open && <p role="status">正在读取本章已保存的证据…</p>}
      {!!view?.options.length && (
        <fieldset disabled={busy || disabled}>
          <legend>确认保存稿中的设定使用</legend>
          <label>
            本章引用的设定
            <select value={option?.id ?? ''} onChange={(event) => setItemId(event.target.value)}>
              <option value="">请选择设定</option>
              {view.options.map(({ id, title, category }) => (
                <option key={id} value={id}>
                  {title} · {category}
                </option>
              ))}
            </select>
          </label>
          {canUseSelection ? (
            <blockquote>{selection.text}</blockquote>
          ) : (
            <p>请先保存本章，再在正文编辑区选择原句。</p>
          )}
          <button type="button" disabled={!option || !canUseSelection} onClick={confirm}>
            确认该选区为正文证据
          </button>
        </fieldset>
      )}
      {view && !view.options.length && <p>当前章稿和大纲暂无可确认的非世界设定引用。</p>}
      {!!view?.records.length && (
        <ul>
          {view.records.map((proof) => (
            <li key={proof.entry.version.id}>
              <details>
                <summary>
                  {proof.title} · {labels[proof.status]}
                </summary>
                <Proof proof={proof} />
                {proof.canWithdraw && (
                  <button
                    type="button"
                    disabled={busy || disabled}
                    onClick={() => withdraw(proof.entry.version.id)}
                  >
                    撤回这条正文证据
                  </button>
                )}
              </details>
            </li>
          ))}
        </ul>
      )}
    </details>
  )
}

export function OfflineOutlineSettingLedger({
  projectId,
  data,
  refreshToken,
}: {
  projectId: string
  data: OfflineOutlineData
  refreshToken: number
}) {
  const [open, setOpen] = useState(false)
  const [rows, setRows] = useState<SettingLedgerItem[]>()
  const [error, setError] = useState('')
  useEffect(() => {
    if (!open) return
    let active = true
    void readSettingLedger(projectId, data).then(
      (value) => {
        if (active) {
          setRows(value)
          setError('')
        }
      },
      (failure: Error) => {
        if (active) setError(failure.message)
      },
    )
    return () => {
      active = false
    }
  }, [open, projectId, data, refreshToken])
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>设定台账</summary>
      {error && <p role="alert">{error}</p>}
      {open && !rows && !error && <p role="status">正在读取当前大纲和正文证据…</p>}
      {rows && <OutlineSettingLedger items={rows} />}
    </details>
  )
}

type FrozenRuleReview = {
  report_id: string
  chapter_id: string
  revision: number
  result: 'pass' | 'fail' | 'unknown'
  rule: { id: string; confirmation_id: string }
  evidence: { start: number; end: number; text: string } | null
}

function ruleReviews(basis: Record<string, unknown>): FrozenRuleReview[] {
  if (!Array.isArray(basis.reports)) return []
  return basis.reports.filter(
    (review): review is FrozenRuleReview =>
      review &&
      typeof review.report_id === 'string' &&
      typeof review.chapter_id === 'string' &&
      Number.isSafeInteger(review.revision) &&
      typeof review.rule?.id === 'string' &&
      typeof review.rule?.confirmation_id === 'string' &&
      ['pass', 'fail', 'unknown'].includes(review.result) &&
      (review.evidence == null ||
        (Number.isSafeInteger(review.evidence.start) &&
          Number.isSafeInteger(review.evidence.end) &&
          typeof review.evidence.text === 'string')),
  )
}

async function readSettingLedger(projectId: string, data: OfflineOutlineData): Promise<SettingLedgerItem[]> {
  const active = data.settings?.project_version_id === data.projectVersionId ? data.settings : null
  const entries = new Map(
    eligibleOutlineSettings(active).map((setting) => [
      setting.id,
      { setting, chapters: new Map<string, SettingLedgerLocation>() },
    ]),
  )
  if (!entries.size) return []
  const chapterTitles = new Map(data.chapters.map(({ id, title }) => [id, title]))
  const outline = data.current?.version
  const current =
    outline?.project_version_id === data.projectVersionId && outline?.settings_version_id === active?.id
  function location(
    settingId: string,
    chapterId: string,
    chapterTitle = chapterTitles.get(chapterId) ?? chapterId,
  ) {
    const chapters = entries.get(settingId)?.chapters
    if (!chapters) return undefined
    const row: SettingLedgerLocation = chapters.get(chapterId) ?? {
      chapter_id: chapterId,
      chapter_title: chapterTitle,
      planned: null,
      evidence: null,
    }
    chapters.set(chapterId, row)
    return row
  }
  for (const plan of outline ? outlineChapterPlans(outline.plan) : []) {
    for (const id of plan.setting_ids) {
      const row = location(id, plan.chapter_id)
      if (row) {
        row.planned = current ? 'current' : 'outdated'
        row.source_unavailable = !entries.get(id)?.setting.category_title
      }
    }
  }
  const [authorEvidence, history] = await Promise.all([
    readLocalOutlineSettingEvidence(projectId),
    readLocalWorkflowHistory(projectId),
  ])
  for (const proof of authorEvidence) {
    const { item_id, evidence, outline_version_id, settings_version_id } = proof.entry.version
    if (!entries.get(item_id)?.setting.category_title) continue
    const row = location(item_id, evidence.chapter_id, proof.chapterTitle)
    if (!row) continue
    const currentProof =
      row.planned === 'current' &&
      proof.status === 'current' &&
      outline?.id === outline_version_id &&
      active?.id === settings_version_id
    row.evidence = {
      method: 'author',
      status: proof.quote ? (currentProof ? 'current' : 'outdated') : 'unavailable',
      quote: proof.quote,
      revision_id: evidence.revision_id,
    }
  }
  const known = new Set<string>()
  const passages = new Map<
    string,
    {
      review: FrozenRuleReview
      first: boolean
      binding: (typeof history.inspections)[number]['chapter_bindings'][number] | undefined
      chapterTitle: string | undefined
    }
  >()
  for (const { report, chapter_bindings } of history.inspections.sort(
    (a, b) => b.created - a.created || a.id.localeCompare(b.id),
  )) {
    const rules = report.items.find((item) => item.check_id === '05')
    if (!rules) continue
    for (const review of ruleReviews(rules.basis)) {
      const setup = entries.get(review.rule.id)?.setting
      if (!setup || setup.category_title) continue
      const key = JSON.stringify([review.rule.id, review.chapter_id])
      const first = !known.has(key)
      known.add(key)
      const planned = entries.get(review.rule.id)?.chapters.get(review.chapter_id)
      if (planned) delete planned.source_unavailable
      if (review.result !== 'pass' || !review.evidence || passages.has(key)) continue
      passages.set(key, {
        review,
        first,
        binding: chapter_bindings.find(
          ({ id, revision }) => id === review.chapter_id && revision === review.revision,
        ),
        chapterTitle: report.chapters.find(({ id }) => id === review.chapter_id)?.title,
      })
    }
  }
  await Promise.all(
    [...passages.values()].map(async ({ review, first, binding, chapterTitle }) => {
      const row = location(review.rule.id, review.chapter_id, chapterTitle)!
      const evidence = review.evidence!
      const bound = binding ? await readLocalBoundChapterSource(projectId, binding) : undefined
      const quote =
        bound &&
        evidence.start >= 0 &&
        evidence.end > evidence.start &&
        evidence.end <= bound.source.content.length &&
        bound.source.content.slice(evidence.start, evidence.end) === evidence.text &&
        evidence.text.trim() &&
        !/\p{Surrogate}/u.test(evidence.text)
          ? evidence.text
          : null
      row.evidence = {
        method: 'rule',
        status: quote
          ? first && row.planned === 'current' && active?.id === review.rule.confirmation_id && bound?.current
            ? 'current'
            : 'outdated'
          : 'unavailable',
        quote,
        revision_id: binding?.revision_id ?? null,
        report_id: review.report_id,
      }
    }),
  )
  return [...entries.values()].map(({ setting, chapters }) => ({ setting, chapters: [...chapters.values()] }))
}
