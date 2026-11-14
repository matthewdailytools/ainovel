import { useEffect, useRef, useState } from 'react'
import { api, type Artifact, type Chapter, type Task } from './api'
import { PlotAuditDetails } from './PlotAuditDetails'
import { SourceIdentityOption, SOURCE_IDENTITY_VERSION } from './sourceIdentity'

const VERSION = 'candidate-recheck/1'
const PLOT_VERSION = 'author-plot-audit/2'
const PREPARATION_VERSION = 'workflow-plan-preparation/1'

function object(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}
function revision(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
}
function nonempty(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}
function hash(value: unknown): value is string {
  return typeof value === 'string' && /^[a-f0-9]{64}$/u.test(value)
}

export function candidateRecheckSourceMode(task: Task): 'assisted' | 'automatic' | undefined {
  if (task.kind !== 'candidate_recheck') return undefined
  const snapshot = task.result.candidate_recheck_snapshot
  if (!object(snapshot)) return undefined
  if (snapshot.source_kind === 'author_plot') return 'assisted'
  if (snapshot.source_kind === undefined && object(snapshot.prepared_contract)) return 'automatic'
  return undefined
}

export type CandidateRecheckReportData = {
  version: 'candidate-recheck/1'
  target: {
    kind: 'candidate'
    artifact_id: string
    revision: number
    chapter_id: string
    base_revision: number
    content_sha256: string
  }
  source_workflow_id: string
  review: Record<string, unknown> & { checks: unknown[]; findings: unknown[]; summary: string }
  plot_audit: unknown
  review_passed: boolean
  memory_generated: boolean
  note: string
  previous_candidate?: Record<string, unknown>
}

export function readCandidateRecheck(value: unknown): CandidateRecheckReportData | undefined {
  if (
    !object(value) ||
    value.version !== VERSION ||
    !object(value.target) ||
    value.target.kind !== 'candidate' ||
    !nonempty(value.target.artifact_id) ||
    !revision(value.target.revision) ||
    !nonempty(value.target.chapter_id) ||
    !revision(value.target.base_revision) ||
    !hash(value.target.content_sha256) ||
    !nonempty(value.source_workflow_id) ||
    !object(value.review) ||
    !Array.isArray(value.review.checks) ||
    !Array.isArray(value.review.findings) ||
    typeof value.review.summary !== 'string' ||
    !object(value.plot_audit) ||
    typeof value.review_passed !== 'boolean' ||
    typeof value.memory_generated !== 'boolean' ||
    typeof value.note !== 'string' ||
    (value.previous_candidate !== undefined && !object(value.previous_candidate))
  )
    return undefined
  return {
    version: VERSION,
    target: {
      kind: 'candidate',
      artifact_id: value.target.artifact_id,
      revision: value.target.revision,
      chapter_id: value.target.chapter_id,
      base_revision: value.target.base_revision,
      content_sha256: value.target.content_sha256,
    },
    source_workflow_id: value.source_workflow_id,
    review: {
      ...value.review,
      checks: value.review.checks,
      findings: value.review.findings,
      summary: value.review.summary,
    },
    plot_audit: value.plot_audit,
    review_passed: value.review_passed,
    memory_generated: value.memory_generated,
    note: value.note,
    ...(object(value.previous_candidate) ? { previous_candidate: value.previous_candidate } : {}),
  }
}

export function candidateRecheckEligibility(
  artifact: Artifact,
  source: Task | undefined,
  context: {
    branchId: string
    chapter?: Pick<Chapter, 'id' | 'revision'>
    unsaved?: boolean
    disabled?: boolean
    modelReady?: boolean
  },
): string | undefined {
  if (context.disabled) return '请等待当前操作或模型任务结束。'
  if (context.modelReady === false) return '所选模型账户尚不可用。'
  if (context.unsaved) return '请先保存候选编辑及当前原稿，再重新核对。'
  if (
    artifact.kind !== 'chapter_draft' ||
    artifact.status !== 'candidate' ||
    !revision(artifact.candidate_revision) ||
    typeof artifact.payload.text !== 'string'
  )
    return '仅未采纳的整章候选可重新核对。'
  if (
    !context.chapter ||
    artifact.branch_id !== context.branchId ||
    artifact.chapter_id !== context.chapter.id ||
    artifact.base_revision !== context.chapter.revision
  )
    return '候选与当前工作版本、章节或原稿修订不一致，请先核对。'
  if (!source) return '正在核对候选所属的整书任务…'
  const authorAudit = artifact.payload.plot_audit ?? artifact.payload.previous_plot_audit
  const sourceReady =
    source.payload.mode === 'automatic'
      ? source.result.workflow_plan_preparation_version === PREPARATION_VERSION
      : source.payload.mode === 'assisted' &&
        object(authorAudit) &&
        authorAudit.version === PLOT_VERSION &&
        authorAudit.source_kind === 'author_plot' &&
        object(authorAudit.source) &&
        authorAudit.source.kind === 'author_plot' &&
        hash(authorAudit.source.text_sha256) &&
        Array.isArray(authorAudit.items) &&
        authorAudit.items.length > 0
  if (
    !artifact.task_id ||
    source.id !== artifact.task_id ||
    source.kind !== 'workflow' ||
    source.branch_id !== context.branchId ||
    source.status !== 'needs_review' ||
    !sourceReady ||
    source.payload.plot_contract_check !== true ||
    source.result.waiting_artifact !== artifact.id ||
    !!source.result.continued_to ||
    source.result.plot_contract_version !== PLOT_VERSION
  )
    return '当前候选缺少原作者情节解释或完整写前章纲，或不是该任务的当前待审稿；旧契约不会自动升级。'
  return undefined
}

export function parseRecheckBudget(value: string): { value?: number; error?: string } {
  if (!value.trim()) return {}
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed <= 0)
    return { error: '本次复检预算须为大于0的金额，或留空沿用账户累计预算保护。' }
  return { value: parsed }
}

export function recheckRequestSignature(
  artifact: Artifact,
  account: string,
  budgetLimit?: number,
  sourceIdentityContext = false,
) {
  return JSON.stringify([
    artifact.id,
    artifact.candidate_revision,
    artifact.payload.text,
    artifact.branch_id,
    artifact.chapter_id,
    artifact.base_revision,
    artifact.task_id,
    account,
    budgetLimit ?? null,
    ...(sourceIdentityContext ? [SOURCE_IDENTITY_VERSION] : []),
  ])
}

const nonces = new Map<string, string>()
export function recheckNonce(
  signature: string,
  storage?: Pick<Storage, 'getItem' | 'setItem'>,
  makeUuid: () => string = () => crypto.randomUUID(),
) {
  const key = 'ainovel.candidate-recheck.1:' + signature
  const cached = nonces.get(key)
  if (cached) return cached
  let nonce: string | null = null
  try {
    // An empty cache entry acknowledges an earlier receipt. Do not revive a
    // stale storage entry if removing it was blocked by the browser.
    if (cached === undefined) nonce = storage?.getItem(key) || null
  } catch {
    // The in-memory entry still protects retries if browser storage is denied.
  }
  if (!nonce || !/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/iu.test(nonce)) nonce = makeUuid()
  nonces.set(key, nonce)
  try {
    storage?.setItem(key, nonce)
  } catch {
    // Never replace an already assigned nonce after an ambiguous POST result.
  }
  return nonce
}

export function acknowledgeRecheckNonce(
  signature: string,
  expectedNonce: string,
  storage?: Pick<Storage, 'getItem' | 'removeItem'>,
) {
  const key = 'ainovel.candidate-recheck.1:' + signature
  const current = nonces.get(key)
  let acknowledged = false
  if (current === expectedNonce) {
    nonces.set(key, '')
    acknowledged = true
  }
  try {
    if (storage?.getItem(key) === expectedNonce) {
      if (current === undefined) nonces.set(key, '')
      acknowledged = true
      storage.removeItem(key)
    }
  } catch {
    // The empty memory entry prevents reuse of this acknowledged nonce even
    // when removing the matching persistent entry is unavailable.
  }
  return acknowledged
}

export function readCandidateRecheckReceipt(
  value: unknown,
  expected: {
    artifact: Artifact
    account: string
    clientRequestId: string
    budgetLimit?: number
    sourceIdentityContext?: boolean
  },
): Task | undefined {
  const { artifact } = expected
  if (
    !object(value) ||
    !nonempty(value.id) ||
    value.kind !== 'candidate_recheck' ||
    value.branch_id !== artifact.branch_id ||
    !nonempty(value.novel_id) ||
    !nonempty(value.status) ||
    typeof value.created !== 'number' ||
    !Number.isFinite(value.created) ||
    (value.cancel !== 0 && value.cancel !== 1) ||
    typeof value.error !== 'string' ||
    !object(value.payload) ||
    value.payload.branch_id !== artifact.branch_id ||
    value.payload.chapter_id !== artifact.chapter_id ||
    value.payload.base_revision !== artifact.base_revision ||
    value.payload.source_candidate_id !== artifact.id ||
    value.payload.candidate_revision !== artifact.candidate_revision ||
    value.payload.account !== expected.account ||
    value.payload.client_request_id !== expected.clientRequestId ||
    (value.payload.budget_limit ?? undefined) !== expected.budgetLimit ||
    (value.payload.source_identity_context ?? false) !== (expected.sourceIdentityContext ?? false) ||
    !object(value.result) ||
    value.result.candidate_recheck_version !== VERSION ||
    !object(value.result.candidate_recheck_snapshot) ||
    value.result.candidate_recheck_snapshot.artifact_id !== artifact.id ||
    value.result.candidate_recheck_snapshot.candidate_revision !== artifact.candidate_revision ||
    value.result.candidate_recheck_snapshot.source_workflow_id !== artifact.task_id ||
    (expected.sourceIdentityContext === true &&
      (value.result.source_identity_context_version !== SOURCE_IDENTITY_VERSION ||
        value.result.candidate_recheck_snapshot.source_identity_context_version !== SOURCE_IDENTITY_VERSION))
  )
    return undefined
  // The response was returned successfully by the 202 endpoint; runtime checks
  // above bind its server-owned task receipt to this exact submitted request.
  return value as Task
}

export type RecheckTargetState = {
  status: 'ready' | 'historical' | 'loading' | 'unavailable'
  candidate?: Artifact
  sha256?: string
  message?: string
}
async function sha256(text: string) {
  if (!globalThis.crypto?.subtle) throw new Error('正文哈希暂不可核对，报告不能视为当前有效结论。')
  const buffer = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return [...new Uint8Array(buffer)].map((value) => value.toString(16).padStart(2, '0')).join('')
}

export async function loadCandidateRecheckTarget(
  report: CandidateRecheckReportData,
  branchId: string,
  read: (path: string) => Promise<unknown>,
  digest: (text: string) => Promise<string> = sha256,
): Promise<RecheckTargetState> {
  const value = await read(`/artifacts/${encodeURIComponent(report.target.artifact_id)}`)
  if (
    !object(value) ||
    value.id !== report.target.artifact_id ||
    value.branch_id !== branchId ||
    value.task_id !== report.source_workflow_id ||
    value.kind !== 'chapter_draft' ||
    value.chapter_id !== report.target.chapter_id ||
    !revision(value.candidate_revision) ||
    !revision(value.base_revision) ||
    typeof value.status !== 'string' ||
    !object(value.payload) ||
    typeof value.payload.text !== 'string' ||
    !object(value.basis)
  )
    throw new Error('目标候选的身份、工作版本、章节或正文无法核对。')
  // Only return the API object after validating every field used for target
  // identity, revision, hashing and navigation. Other fields remain read-only.
  const candidate = value as Artifact
  const actualHash = await digest(candidate.payload.text!)
  if (!hash(actualHash)) throw new Error('目标候选哈希无法核对。')
  const current =
    candidate.candidate_revision === report.target.revision &&
    candidate.base_revision === report.target.base_revision &&
    actualHash === report.target.content_sha256 &&
    candidate.status !== 'rejected'
  return {
    status: current ? 'ready' : 'historical',
    candidate,
    sha256: actualHash,
    message: current
      ? '已从目标候选读取并核对修订与正文哈希；此报告不代表已采纳的保存稿。'
      : '目标候选已变化或被拒绝，以下报告是历史记录，不能作为当前候选结论。',
  }
}

export function createCandidateRecheckTargetLoader(
  read: (path: string) => Promise<unknown>,
  publish: (state: RecheckTargetState) => void,
  digest?: (text: string) => Promise<string>,
) {
  let sequence = 0
  return {
    async load(report: CandidateRecheckReportData, branchId: string) {
      const request = ++sequence
      try {
        const state = await loadCandidateRecheckTarget(report, branchId, read, digest)
        if (request === sequence) publish(state)
      } catch (error) {
        if (request === sequence)
          publish({
            status: 'unavailable',
            message:
              error instanceof Error ? error.message : '目标候选读取失败，报告仍保留但不能判为当前有效。',
          })
      }
    },
    invalidate() {
      sequence += 1
    },
  }
}

export function CandidateRecheckAction({
  artifact,
  chapter,
  branchId,
  account,
  modelReady,
  busy = false,
  disabled,
  unsaved,
  perform,
  onStarted,
}: {
  artifact: Artifact
  chapter?: Chapter
  branchId: string
  account: string
  modelReady: boolean
  busy?: boolean
  disabled: boolean
  unsaved: boolean
  perform: (fn: () => Promise<unknown>) => Promise<void>
  onStarted: (task: Task) => void
}) {
  const [budget, setBudget] = useState('')
  const [sourceIdentityContext, setSourceIdentityContext] = useState(false)
  const parsedBudget = parseRecheckBudget(budget)
  const signature = recheckRequestSignature(artifact, account, parsedBudget.value, sourceIdentityContext)
  const [source, setSource] = useState<{ key: string; task?: Task; error?: string }>()
  const sourceKey = JSON.stringify([
    artifact.id,
    artifact.candidate_revision,
    artifact.status,
    artifact.task_id,
    artifact.base_revision,
    branchId,
    chapter?.id,
    chapter?.revision,
  ])
  const state = useRef({
    signature,
    sourceKey,
    disabled,
    unsaved,
    modelReady,
    budgetError: parsedBudget.error,
  })
  state.current = { signature, sourceKey, disabled, unsaved, modelReady, budgetError: parsedBudget.error }
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])
  useEffect(() => {
    let active = true
    if (!artifact.task_id) {
      setSource({ key: sourceKey, error: '候选所属任务无法确认，不能重新核对。' })
      return
    }
    void api<Task>(`/tasks/${encodeURIComponent(artifact.task_id)}`)
      .then((task) => {
        if (active) setSource({ key: sourceKey, task })
      })
      .catch(() => {
        if (active) setSource({ key: sourceKey, error: '候选所属任务读取失败，请重新打开候选。' })
      })
    return () => {
      active = false
    }
  }, [sourceKey])
  const task = source?.key === sourceKey ? source.task : undefined
  const reason =
    parsedBudget.error ||
    (source?.key === sourceKey && source.error) ||
    candidateRecheckEligibility(artifact, task, {
      branchId,
      chapter,
      disabled: disabled || busy,
      unsaved,
      modelReady,
    })
  return (
    <section aria-label="重新核对候选">
      <h4>保存后重新核对</h4>
      <p className="small muted">
        按原任务冻结的本章情节来源和约定，重新检查已保存候选。会产生额外模型费用；不改候选正文，不自动采纳或继续整书任务。旧报告和本次独立报告均保留。
      </p>
      <SourceIdentityOption
        checked={sourceIdentityContext}
        disabled={disabled || busy}
        onChange={setSourceIdentityContext}
      />
      <details>
        <summary>本次复检费用设置</summary>
        <label>
          本次复检预算上限（账户币种，可留空）
          <input
            type="number"
            step="any"
            min="0"
            value={budget}
            disabled={disabled || busy}
            onChange={(event) => setBudget(event.target.value)}
          />
        </label>
        <p className="small muted">留空时仍受账户累计预算保护。未知结果不会自动重发模型调用。</p>
      </details>
      {reason && <p role="status">{reason}</p>}
      <button
        type="button"
        disabled={!!reason}
        onClick={() => {
          if (reason || !artifact.task_id) return
          const expected = { signature, sourceKey }
          const stillCurrent = () =>
            alive.current &&
            state.current.signature === expected.signature &&
            state.current.sourceKey === expected.sourceKey &&
            !state.current.disabled &&
            !state.current.budgetError &&
            !state.current.unsaved &&
            state.current.modelReady
          void perform(async () => {
            const currentSource = await api<Task>(`/tasks/${encodeURIComponent(artifact.task_id!)}`)
            if (!stillCurrent()) return
            const error = candidateRecheckEligibility(artifact, currentSource, {
              branchId,
              chapter,
              unsaved: state.current.unsaved,
              modelReady: state.current.modelReady,
            })
            setSource({ key: sourceKey, task: currentSource })
            if (error) throw new Error(error)
            let storage: Storage | undefined
            try {
              storage = window.sessionStorage
            } catch {
              // The nonce helper keeps its in-memory retry identity.
            }
            const requestHash = await sha256(signature)
            if (!stillCurrent()) return
            const nonce = recheckNonce(requestHash, storage)
            const response = await api<unknown>(
              `/artifacts/${encodeURIComponent(artifact.id)}/recheck`,
              'POST',
              {
                candidate_revision: artifact.candidate_revision,
                account,
                client_request_id: nonce,
                ...(sourceIdentityContext ? { source_identity_context: true } : {}),
                ...(parsedBudget.value === undefined ? {} : { budget_limit: parsedBudget.value }),
              },
            )
            const started = readCandidateRecheckReceipt(response, {
              artifact,
              account,
              clientRequestId: nonce,
              budgetLimit: parsedBudget.value,
              sourceIdentityContext,
            })
            if (!started) throw new Error('复检任务回执无法核对，已保留本次请求标识；请核查任务后重试。')
            acknowledgeRecheckNonce(requestHash, nonce, storage)
            if (stillCurrent()) onStarted(started)
          })
        }}
      >
        重新核对候选（额外模型费用）
      </button>
    </section>
  )
}

export function CandidateRecheckReport({
  artifact,
  branchId,
  onTarget,
  disabled = false,
}: {
  artifact: Artifact
  branchId: string
  onTarget: (candidate: Artifact) => void
  disabled?: boolean
}) {
  const report = readCandidateRecheck(artifact.payload)
  const key = JSON.stringify([artifact.id, branchId, report?.target])
  const [loaded, setLoaded] = useState<{ key: string; state: RecheckTargetState }>()
  useEffect(() => {
    if (!report) return
    const loader = createCandidateRecheckTargetLoader(api, (state) => setLoaded({ key, state }))
    const load = () => void loader.load(report, branchId)
    load()
    const timer = setInterval(load, 2000)
    return () => {
      loader.invalidate()
      clearInterval(timer)
    }
  }, [key])
  if (!report)
    return (
      <section aria-label="候选复检报告">
        <p role="status">候选复检报告格式无法核对，仅保留原始记录，不能作为通过结论。</p>
        <details>
          <summary>查看原始复检记录</summary>
          <pre className="report">{JSON.stringify(artifact.payload, null, 2)}</pre>
        </details>
      </section>
    )
  const current: RecheckTargetState = loaded?.key === key ? loaded.state : { status: 'loading' }
  const text = (value: unknown) => (typeof value === 'string' ? value : '')
  return (
    <section aria-label="候选复检报告" style={{ overflowWrap: 'anywhere' }}>
      <h3>候选复检报告 · 只读</h3>
      <p>
        {current.status === 'ready'
          ? report.review_passed
            ? '模型标复检通过，仍须作者核对。'
            : '本轮复检仍有未通过或未能核实的项目。'
          : current.status === 'historical'
            ? '历史报告：目标候选已变化，不能作为当前结论。'
            : '目标候选尚未核实，不能作为当前通过结论。'}
      </p>
      <p role="status">{current.message || '正在读取目标候选并核对正文哈希…'}</p>
      <p className="small muted">
        本报告不生成新正文，也不自动采纳或继续整书。模型复检不等于作者文学认可。采纳仍须回到目标候选。
      </p>
      <p>{report.note}</p>
      {report.previous_candidate && (
        <details>
          <summary>复检前候选与检查记录</summary>
          <p>
            以下是本轮复检前候选修订 {report.target.revision}{' '}
            的完整内容和检查记录，仅为历史依据，不能作为当前候选结论；其中嵌套的旧报告可能来自更早检查。
          </p>
          <pre className="report">{JSON.stringify(report.previous_candidate, null, 2)}</pre>
        </details>
      )}
      <details>
        <summary>复检对象与版本</summary>
        <p className="small muted">
          目标候选 {report.target.artifact_id} · 候选修订 {report.target.revision} · 章节{' '}
          {report.target.chapter_id} · 原稿基线修订 {report.target.base_revision}。
        </p>
        <p className="small muted">目标正文SHA-256：{report.target.content_sha256}</p>
        <p className="small muted">
          来源工作流：{report.source_workflow_id} · 报告版本：{report.version}
        </p>
      </details>
      <button
        type="button"
        disabled={disabled || !current.candidate}
        onClick={() => {
          if (current.candidate) onTarget(current.candidate)
        }}
      >
        查看目标候选
      </button>
      <details>
        <summary>查看本轮普通审稿与依据</summary>
        <p>{report.review.summary}</p>
        {report.review.checks.map((check, index) =>
          object(check) ? (
            <article className="fact" key={index}>
              <p>{text(check.condition)}</p>
              <p>
                {check.result === 'pass' ? '模型标一致' : check.result === 'fail' ? '模型标不符' : '未能核实'}
              </p>
              <blockquote>{text(check.evidence)}</blockquote>
            </article>
          ) : null,
        )}
        {report.review.findings.map((finding, index) =>
          object(finding) ? (
            <article className="fact" key={index}>
              <strong>{finding.severity === 'blocker' ? '阻断意见' : '审稿建议'}</strong>
              <blockquote>{text(finding.quote)}</blockquote>
              <p>{text(finding.reason)}</p>
              <p>{text(finding.repair)}</p>
            </article>
          ) : null,
        )}
      </details>
      <PlotAuditDetails
        value={report.plot_audit}
        candidateRevision={current.candidate?.candidate_revision}
        candidateSha256={current.sha256}
        targetStatus={current.status}
        targetMessage={current.message}
        expectedSha256={report.target.content_sha256}
        historical={current.status === 'historical'}
      />
      <p className="small muted">
        {report.memory_generated
          ? '本轮全部检查通过后生成了记忆候选；不表示正文已采纳。'
          : '本轮没有生成新的记忆候选。'}
      </p>
      <details>
        <summary>查看本次独立复检原始记录</summary>
        <pre className="report">{JSON.stringify(artifact.payload, null, 2)}</pre>
      </details>
    </section>
  )
}

export type CandidateHistoryPage = {
  items: { id: number | string; kind: string; payload: Record<string, unknown>; created: number }[]
  next_offset: number | null
}

export function readCandidateHistoryPage(
  value: unknown,
  artifactId: string,
  offset = 0,
): CandidateHistoryPage | undefined {
  if (
    !object(value) ||
    value.artifact_id !== artifactId ||
    !revision(offset) ||
    !Array.isArray(value.items) ||
    value.items.length > 20 ||
    (value.next_offset !== null && (!revision(value.next_offset) || value.next_offset <= offset))
  )
    return undefined
  const items: CandidateHistoryPage['items'] = []
  for (const item of value.items) {
    if (
      !object(item) ||
      (!nonempty(item.id) && !revision(item.id)) ||
      (item.kind !== 'candidate_edited' && item.kind !== 'candidate_rechecked') ||
      typeof item.created !== 'number' ||
      !Number.isFinite(item.created) ||
      !object(item.payload) ||
      item.payload.artifact_id !== artifactId ||
      (item.payload.previous_revision !== undefined && !revision(item.payload.previous_revision)) ||
      (item.payload.previous_content_sha256 !== undefined && !hash(item.payload.previous_content_sha256)) ||
      (item.payload.previous_candidate !== undefined && !object(item.payload.previous_candidate))
    )
      return undefined
    items.push({ id: item.id, kind: item.kind, payload: item.payload, created: item.created })
  }
  return { items, next_offset: value.next_offset }
}

export function CandidateHistory({ artifactId }: { artifactId: string }) {
  const [page, setPage] = useState<{ id: string; value: CandidateHistoryPage }>()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const sequence = useRef(0)
  const currentId = useRef(artifactId)
  currentId.current = artifactId
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    setError('')
    setLoading(false)
    return () => {
      alive.current = false
      sequence.current += 1
    }
  }, [artifactId])
  const currentPage = page?.id === artifactId ? page.value : undefined
  async function load(offset: number) {
    const id = artifactId
    const request = ++sequence.current
    const matches = () => alive.current && currentId.current === id && sequence.current === request
    setLoading(true)
    setError('')
    try {
      const response = await api<unknown>(`/artifacts/${encodeURIComponent(id)}/history?offset=${offset}`)
      if (!matches()) return
      const next = readCandidateHistoryPage(response, id, offset)
      if (!next) throw new Error('候选历史的归属或分页信息无法核对，未展示为当前候选记录。')
      setPage((previous) => ({
        id,
        value: {
          items:
            offset > 0 && previous?.id === id
              ? [
                  ...previous.value.items,
                  ...next.items.filter((item) => !previous.value.items.some((old) => old.id === item.id)),
                ]
              : next.items,
          next_offset: next.next_offset,
        },
      }))
    } catch (failure) {
      if (matches()) setError(failure instanceof Error ? failure.message : '历史记录读取失败。')
    } finally {
      if (matches()) setLoading(false)
    }
  }
  return (
    <details
      onToggle={(event) => {
        if (event.currentTarget.open && !currentPage && !loading) void load(0)
      }}
    >
      <summary>候选编辑与复检历史</summary>
      <p className="small muted">
        以下仅为该候选的历史内容和检查依据，不代表当前结论，不会恢复、采纳或继续任务。旧事件未保留完整快照时会明确说明。
      </p>
      {loading && <p role="status">正在读取候选历史…</p>}
      {error && <p role="status">{error}</p>}
      {currentPage?.items.length === 0 && <p>尚无可读取的候选编辑或复检历史。</p>}
      {currentPage?.items.map((item) => (
        <details key={item.id}>
          <summary>
            {item.kind === 'candidate_edited'
              ? '候选编辑记录'
              : item.kind === 'candidate_rechecked'
                ? '候选复检记录'
                : '候选历史记录'}{' '}
            · {new Date(item.created * 1000).toLocaleString('zh-CN')}
          </summary>
          {!object(item.payload.previous_candidate) && <p>此旧事件未单独保留完整候选快照。</p>}
          {revision(item.payload.previous_revision) && (
            <p>以下保留的是该次编辑或复检前的候选修订 {item.payload.previous_revision}，不是当前稿。</p>
          )}
          <pre className="report">{JSON.stringify(item.payload, null, 2)}</pre>
        </details>
      ))}
      <div className="actions">
        <button type="button" disabled={loading} onClick={() => void load(0)}>
          刷新候选历史
        </button>
        {currentPage?.next_offset !== null && currentPage?.next_offset !== undefined && (
          <button type="button" disabled={loading} onClick={() => void load(currentPage.next_offset!)}>
            查看更早的记录
          </button>
        )}
      </div>
    </details>
  )
}
