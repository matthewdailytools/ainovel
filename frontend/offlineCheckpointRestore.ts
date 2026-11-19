import type { RestoreAffectedReport } from './ChapterRestorePreview'
import type { LocalChapter, LocalRevision, ManuscriptPack } from './offlineStore'
import {
  outlineCardKey,
  prepareOutlineVersion,
  type OutlineArchivePending,
  type OutlineVersion,
} from './outlineSyncModel'
import {
  lengthTargetKey,
  type PortableCheckpoint,
  type PortableCheckpointEntry,
  type PortableCheckpointLengthTarget,
  type PortableRestoreState,
} from './portableCheckpoint'
import { prepareSettingsVersion, type SettingsEvidence } from './settingsSyncModel'
import {
  allStoryEvidence,
  buildStoryVersion,
  compactJSON,
  orderStoryHeads,
  type StoryNativeRevision,
  type StoryVersion,
} from './storySyncModel'

export type LocalCheckpointChapterChange = {
  id: string
  title: string
  action: 'restore' | 'archive'
  currentRevision: number | null
  targetRevision: number | null
}
export type LocalCheckpointPreview = {
  basis: string
  changes: LocalCheckpointChapterChange[]
  blockers: string[]
  projectChanged: boolean
  settingsChanged: boolean
  outlineChanged: boolean
  storyRecordCount: number
  factCount: number
  lengthTargetCount: number
  affectedReports: RestoreAffectedReport[]
  affectedSettingUses: { chapterTitle: string; title: string; versionId: string }[]
  sceneSpansForReview: number
}
export type LocalCheckpointReceipt = {
  recoveryCheckpointId: string
  chapters: LocalChapter[]
  storyRevision: number
}

type Captured = {
  pack: ManuscriptPack
  adoptedOutlineId: string | null
  deferredOutline?: OutlineArchivePending
  reportAliases?: ReadonlySet<string>
}
type ChapterWrite = { chapter: LocalChapter; revision?: LocalRevision }
export type StoryCheckpointWrite = { version: StoryVersion; history: StoryNativeRevision }
type Prep = { id: () => string; created: number }

function versions<T extends { id: string }>(history?: { versions: T[] }) {
  return new Map(history?.versions.map((version) => [version.id, version]))
}

function identify(pack: ManuscriptPack) {
  return {
    project_id: pack.project_history?.adopted_revision_id ?? null,
    settings_id: pack.settings_history?.adopted_revision_id ?? null,
    outline_id: pack.outline_history?.adopted_revision_id ?? null,
  }
}

export function checkpointRestoreFingerprint(
  { pack, adoptedOutlineId, deferredOutline, reportAliases }: Captured,
  source: PortableCheckpointEntry,
) {
  const current = chapterHeads(pack)
  return compactJSON([
    'ainovel-offline-restore-preview/1',
    pack.branch,
    pack.chapters.map((row) => [row, current.get(row.id)?.id]),
    identify(pack),
    adoptedOutlineId,
    deferredOutline ?? null,
    pack.outline_history?.required_story_records ?? [],
    pack.story_history?.adopted_heads ?? {},
    pack.setting_use_history?.adopted_heads ?? {},
    pack.setting_use_history?.native_bindings ?? {},
    pack.workflow_history?.inspections.map(({ id }) => id).sort() ?? [],
    pack.workflow_history?.completions.map(({ id }) => id).sort() ?? [],
    [...(reportAliases ?? [])].sort(),
    pack.reference_history?.adopted_heads ?? [],
    pack.restore_state ?? null,
    source.sha256,
  ])
}

export function chapterHeads({ chapters, revisions }: Pick<ManuscriptPack, 'chapters' | 'revisions'>) {
  const directory = new Map(chapters.map((row) => [row.id, row.revision]))
  return new Map(
    revisions
      .filter((row) => directory.get(row.chapter_id) === row.revision)
      .map((row) => [row.chapter_id, row]),
  )
}

function historyHeads(pack: ManuscriptPack) {
  const versionsById = versions(pack.story_history)
  const history = pack.story_history?.native_history ?? {}
  return new Map(
    Object.entries(pack.story_history?.adopted_heads ?? {}).map(([id, version]) => [
      id,
      {
        version: versionsById.get(version)!,
        native: history[id].at(-1)!,
      },
    ]),
  )
}

export function captureRecoveryCheckpoint(
  { pack, adoptedOutlineId }: Captured,
  checkpointId: string,
  basis: string,
  created: number,
): PortableCheckpoint {
  const current = chapterHeads(pack)
  const heads = historyHeads(pack)
  const outline = identify(pack)
  const outlineVersion = versions(pack.outline_history).get(outline.outline_id ?? '')
  const volumes = new Set(outlineVersion?.plan.volumes.map(({ id }) => id))
  const reports: PortableCheckpoint['reports'] = {}
  const inspection = pack.workflow_history?.inspections.at(-1)
  const completion = pack.workflow_history?.completions.at(-1)
  if (inspection) reports.workflow_inspection = inspection.id
  if (completion) reports.book_completion = completion.id
  return {
    version: 'ainovel-portable-checkpoint/1',
    id: checkpointId,
    branch_id: pack.branch.id,
    label: '恢复前检查点',
    scope: 'book',
    scope_id: null,
    purpose: 'pre_restore',
    created,
    source_manifest_sha256: basis,
    story_revision: pack.branch.story_revision,
    scoped_chapter_ids: pack.chapters.filter(({ archived }) => !archived).map(({ id }) => id),
    chapters: pack.chapters.map(({ branch_id: _branch, ...row }) => ({
      ...row,
      revision_id: current.get(row.id)!.id,
      sha256: current.get(row.id)!.sha256,
    })),
    shared: outline,
    reports,
    restore_sources: {
      version: 'ainovel-portable-restore-sources/1',
      outline_live: adoptedOutlineId !== null && adoptedOutlineId === outline.outline_id,
      story_heads: Object.fromEntries(
        [...heads].map(([id, { native }]) => [
          id,
          { revision: native.revision, version_id: native.version_id },
        ]),
      ),
      facts: pack.restore_state?.facts ?? [],
      length_targets: (pack.restore_state?.length_targets ?? []).filter(
        ({ scope, scope_id }) => scope !== 'volume' || volumes.has(scope_id!),
      ),
      scene_spans_for_review: 0,
    },
  }
}

function volumeIds(outline: OutlineVersion | undefined, id: string | null) {
  return (
    outline?.plan.volumes
      .find((volume) => volume.id === id)
      ?.units.flatMap(({ chapters }) => chapters.map(({ chapter_id }) => chapter_id)) ?? []
  )
}

function windowIds(window: OutlineVersion['plan']['promises'][number]) {
  return [window.plant_chapter_id, window.opens_chapter_id, window.closes_chapter_id]
}

function activeImpacts(
  { pack, reportAliases }: Captured,
  changed: ReadonlySet<string>,
  shared: boolean,
  lengthChanges: ReadonlySet<string>,
) {
  const current = chapterHeads(pack)
  const active = new Map(pack.chapters.filter(({ archived }) => !archived).map((row) => [row.id, row]))
  const currentSignature = (link: { id: string; revision_id: string; sha256: string }) => {
    const row = active.get(link.id)
    const head = current.get(link.id)
    return Boolean(
      row &&
      !row.stale &&
      head &&
      head.sha256 === link.sha256 &&
      (head.id === link.revision_id || reportAliases?.has(`${link.revision_id}:${head.id}`)),
    )
  }
  const reports: RestoreAffectedReport[] = []
  const inspected = new Set<string>()
  for (const { id, report, chapter_bindings } of [...(pack.workflow_history?.inspections ?? [])].sort(
    (a, b) => b.created - a.created || a.id.localeCompare(b.id),
  )) {
    const scope = `${report.scope}:${report.scope_id ?? ''}`
    if (
      inspected.has(scope) ||
      (report.scope === 'book' && active.size !== chapter_bindings.length) ||
      !chapter_bindings.every(currentSignature)
    )
      continue
    const direct = chapter_bindings.some((row) => changed.has(row.id))
    const lengthChanged = lengthChanges.has(lengthTargetKey(report))
    const check_ids = report.items.flatMap((item) =>
      ['pending', 'outdated'].includes(item.status) ||
      !(
        shared ||
        direct ||
        (lengthChanged && item.check_id === '09') ||
        item.author_review?.evidence.some((row) => changed.has(row.chapter_id))
      )
        ? []
        : [item.check_id],
    )
    if (check_ids.length) {
      inspected.add(scope)
      reports.push({
        id,
        kind: 'workflow_inspection',
        scope: report.scope,
        scope_id: report.scope_id,
        check_ids,
      })
    }
  }
  for (const { id, chapter_bindings } of pack.workflow_history?.completions ?? [])
    if (
      active.size === chapter_bindings.length &&
      chapter_bindings.every(currentSignature) &&
      (shared || lengthChanges.has('book:') || chapter_bindings.some((row) => changed.has(row.id)))
    )
      reports.push({ id, kind: 'book_completion', scope: 'book', scope_id: null, check_ids: [] })
  return reports
}

function ownedStory(story: StoryVersion, touched: ReadonlySet<string>, localPromises: ReadonlySet<string>) {
  const evidence = allStoryEvidence(story)
  return (
    evidence.every(({ chapter_id }) => touched.has(chapter_id)) &&
    (evidence.length > 0 || localPromises.has(story.record_id))
  )
}

function restoreLengthTargets(
  source: PortableCheckpoint,
  current: PortableCheckpointLengthTarget[],
  touched: ReadonlySet<string>,
) {
  const inScope = ({ scope, scope_id }: PortableCheckpointLengthTarget) =>
    source.scope === 'book' ||
    (scope === 'chapter' && touched.has(scope_id!)) ||
    (scope === 'volume' && scope_id === source.scope_id)
  const previous = new Map(current.map((row) => [lengthTargetKey(row), row]))
  const selected = new Map(
    (source.restore_sources?.length_targets ?? []).filter(inScope).map((row) => [lengthTargetKey(row), row]),
  )
  const status = new Map(previous)
  for (const [key, row] of previous)
    if (inScope(row) && !selected.has(key)) status.set(key, { ...row, targets: null })
  for (const [key, row] of selected) status.set(key, row)
  const changed = new Set(
    [...status].flatMap(([key, row]) =>
      compactJSON(row) !== compactJSON(previous.get(key) ?? null) ? [key] : [],
    ),
  )
  return { values: [...status.values()], changed }
}

export function planCheckpointRestore(captured: Captured, source: PortableCheckpoint) {
  const { pack, adoptedOutlineId } = captured
  const current = identify(pack)
  const outlines = versions(pack.outline_history)
  const oldOutline = source.shared.outline_id ? outlines.get(source.shared.outline_id) : undefined
  const currentOutline = adoptedOutlineId ? outlines.get(adoptedOutlineId) : undefined
  const active = pack.chapters.filter(({ archived }) => !archived).map(({ id }) => id)
  const wanted = source.scoped_chapter_ids
  const scoped = source.scope === 'book' ? active : volumeIds(currentOutline, source.scope_id)
  const outside = active.filter((id) => !scoped.includes(id))
  const whole = source.scope === 'book' || outside.length === 0
  const touched = new Set([...wanted, ...scoped])
  const blockers: string[] = []
  if (!source.restore_sources) blockers.push('这份归档缺少可执行恢复的正式依据。')
  if (source.shared.outline_id && source.restore_sources?.outline_live !== true)
    blockers.push('归档中的大纲仍待核对，请在桌面另存后审阅。')
  if (current.outline_id && current.outline_id !== adoptedOutlineId)
    blockers.push('本机已有待核对的大纲依据，请先完成当前大纲确认。')
  const adoptedSettings = versions(pack.settings_history).get(current.settings_id ?? '')
  if (
    (adoptedSettings && adoptedSettings.project_version_id !== current.project_id) ||
    (currentOutline &&
      (currentOutline.project_version_id !== current.project_id ||
        (currentOutline.settings_version_id && currentOutline.settings_version_id !== current.settings_id)))
  )
    blockers.push('本机的立项、设定或大纲依据已变化。请先核对，以便保存完整的恢复前检查点。')
  if (
    currentOutline?.plan.promises.some(
      ({ promise_id }) => !Object.hasOwn(pack.story_history?.adopted_heads ?? {}, promise_id),
    )
  )
    blockers.push('本机大纲含仅记录初始计划的伏笔。请先确认该伏笔的故事记录，以便保留完整恢复点。')
  if (
    whole &&
    ((current.project_id && !source.shared.project_id) ||
      (current.settings_id && !source.shared.settings_id) ||
      (current.outline_id && !source.shared.outline_id))
  )
    blockers.push('归档缺少当前立项、设定或大纲的正式旧版。')
  if (source.scope === 'volume') {
    if (
      !scoped.length ||
      scoped.some((id) => !active.includes(id)) ||
      wanted.some((id) => outside.includes(id))
    )
      blockers.push('当前卷的有效章节范围已变化，请在桌面从归档另存。')
    if (
      !whole &&
      (current.project_id !== source.shared.project_id || current.settings_id !== source.shared.settings_id)
    )
      blockers.push('立项或设定与其他卷共用且已更改，请在桌面从归档另存。')
    if (
      !whole &&
      [...(oldOutline?.plan.promises ?? []), ...(currentOutline?.plan.promises ?? [])].some((window) => {
        const ids = windowIds(window)
        return ids.some((id) => touched.has(id)) && !ids.every((id) => touched.has(id))
      })
    )
      blockers.push('伏笔窗口跨越本卷边界，请在桌面从归档另存。')
  }
  const old = new Map(source.chapters.map((row) => [row.id, row]))
  const directory = new Map(pack.chapters.map((row) => [row.id, row]))
  const currentChapters = chapterHeads(pack)
  const changes: LocalCheckpointChapterChange[] = []
  for (const id of wanted) {
    const before = old.get(id)!
    const now = directory.get(id)
    if (!now) {
      blockers.push('缺少归档中的章节或本机历史。')
      continue
    }
    if (
      before.revision_id !== currentChapters.get(id)?.id ||
      before.title !== now.title ||
      now.archived ||
      now.stale !== before.stale ||
      (whole && now.position !== before.position)
    )
      changes.push({
        id,
        title: before.title,
        action: 'restore',
        currentRevision: now.revision,
        targetRevision: before.revision,
      })
  }
  for (const id of scoped)
    if (!wanted.includes(id)) {
      const chapter = directory.get(id)!
      changes.push({
        id,
        title: chapter.title,
        action: 'archive',
        currentRevision: chapter.revision,
        targetRevision: null,
      })
    }
  const projectChanged = whole && source.shared.project_id !== current.project_id
  const changedIds = new Set(changes.map(({ id }) => id))
  const oldSettings = versions(pack.settings_history).get(source.shared.settings_id ?? '')
  const sourceAddresses = new Map(
    [...(pack.source_revisions ?? []), ...pack.revisions].map((row) => [row.id, row]),
  )
  const reboundSettings =
    oldSettings &&
    Object.values(oldSettings.origins).some(({ evidence }) => {
      if (!evidence || !changedIds.has(evidence.chapter_id) || !wanted.includes(evidence.chapter_id))
        return false
      const chapter = old.get(evidence.chapter_id)!
      const original = sourceAddresses.get(evidence.revision_id)
      return original?.revision === chapter.revision && original.sha256 === chapter.sha256
    })
  const settingsChanged =
    whole && (source.shared.settings_id !== current.settings_id || projectChanged || Boolean(reboundSettings))
  const outlineChanged =
    (whole
      ? source.shared.outline_id !== adoptedOutlineId
      : !!oldOutline && oldOutline.id !== adoptedOutlineId) ||
    (settingsChanged && !!oldOutline?.settings_version_id) ||
    (projectChanged && !!oldOutline)
  const headVersions = versions(pack.story_history)
  const currentStory = historyHeads(pack)
  const priorStory = new Map(
    Object.entries(source.restore_sources?.story_heads ?? {}).map(([id, prior]) => [
      id,
      headVersions.get(prior.version_id)!,
    ]),
  )
  const localPromises = new Set(
    [...(oldOutline?.plan.promises ?? []), ...(currentOutline?.plan.promises ?? [])]
      .filter((window) => windowIds(window).every((id) => touched.has(id)))
      .map(({ promise_id }) => promise_id),
  )
  const records = new Set<string>()
  for (const [id, row] of currentStory) {
    const prior = priorStory.get(id)
    if (
      whole ||
      (ownedStory(row.version, touched, localPromises) &&
        (!prior || ownedStory(prior, touched, localPromises)))
    )
      records.add(id)
  }
  if (whole) for (const id of priorStory.keys()) records.add(id)
  const selectedHeads = new Map([...currentStory].map(([id, row]) => [id, row.version]))
  for (const id of records) {
    const prior = priorStory.get(id)
    if (prior) selectedHeads.set(id, prior)
  }
  try {
    orderStoryHeads(selectedHeads)
  } catch {
    blockers.push('卷内和卷外的故事记录合并后含缺失或循环依赖，请在桌面从归档另存。')
  }
  const storyRecordCount = [...records].filter((id) => {
    const prior = priorStory.get(id)
    const now = currentStory.get(id)?.version
    return prior?.id !== now?.id
      ? !!prior || !now?.archived
      : !!prior && allStoryEvidence(prior).some(({ chapter_id }) => changedIds.has(chapter_id))
  }).length
  const allBasesChange = projectChanged || settingsChanged || outlineChanged
  const capturedChanges = new Set([
    ...changedIds,
    ...(!whole && wanted.join() !== scoped.join() ? outside : []),
  ])
  const factSources = (source.restore_sources?.facts ?? []).filter(({ evidence }) =>
    wanted.includes(evidence.chapter_id),
  )
  const lengthTargets = restoreLengthTargets(source, pack.restore_state?.length_targets ?? [], touched)
  const affectedReports = activeImpacts(
    captured,
    capturedChanges,
    allBasesChange || storyRecordCount > 0,
    lengthTargets.changed,
  )
  const diagnoses = versions(pack.settings_history).get(current.settings_id ?? '')
  const proofIndex = new Map(pack.setting_use_history?.versions.map(({ version }) => [version.id, version]))
  const affectedSettingUses: LocalCheckpointPreview['affectedSettingUses'] = []
  for (const id of Object.values(pack.setting_use_history?.adopted_heads ?? {})) {
    const proof = proofIndex.get(id)
    if (
      !proof ||
      proof.action !== 'confirmed' ||
      proof.outline_version_id !== adoptedOutlineId ||
      proof.settings_version_id !== current.settings_id
    )
      continue
    const chapter = directory.get(proof.evidence.chapter_id)
    const currentRevision = chapter && currentChapters.get(chapter.id)
    if (
      !chapter ||
      chapter.archived ||
      chapter.stale ||
      !currentRevision ||
      currentRevision.sha256 !== proof.chapter_sha256 ||
      !(allBasesChange || capturedChanges.has(chapter.id))
    )
      continue
    if (
      currentRevision.id !== proof.evidence.revision_id &&
      pack.setting_use_history?.native_bindings?.[proof.evidence.revision_id] !== currentRevision.id
    )
      continue
    const item = diagnoses?.categories.flatMap((entry) => entry.items).find(({ id }) => id === proof.item_id)
    affectedSettingUses.push({
      chapterTitle: chapter.title,
      title: item?.title ?? proof.item_id,
      versionId: proof.id,
    })
  }
  return {
    preview: {
      changes,
      blockers,
      projectChanged,
      settingsChanged,
      outlineChanged,
      storyRecordCount,
      factCount: factSources.length,
      lengthTargetCount: lengthTargets.changed.size,
      affectedReports,
      affectedSettingUses,
      sceneSpansForReview: source.restore_sources?.scene_spans_for_review ?? 0,
    },
    whole,
    wanted,
    scoped,
    outside,
    old,
    directory,
    currentChapters,
    currentStory,
    priorStory,
    records,
    oldOutline,
    currentOutline,
    current,
    touched,
    factSources,
    lengthTargets,
  }
}

export async function prepareCheckpointWrites(
  captured: Captured,
  source: PortableCheckpoint,
  plan: ReturnType<typeof planCheckpointRestore>,
  { id, created }: Prep,
) {
  const { pack } = captured
  const { preview, current, wanted, scoped, outside, whole, old, directory, currentChapters } = plan
  const native = new Map(pack.revisions.map((row) => [row.id, row]))
  const actual = new Map<string, LocalRevision>()
  const changes = new Set(preview.changes.map((row) => row.id))
  const active = pack.chapters.filter(({ archived }) => !archived).map((row) => row.id)
  const first = active.findIndex((id) => scoped.includes(id))
  const order = whole
    ? wanted
    : [
        ...active.slice(0, first).filter((id) => outside.includes(id)),
        ...wanted,
        ...active.slice(first).filter((id) => outside.includes(id)),
      ]
  const positions = new Map(order.map((id, index) => [id, whole ? old.get(id)!.position : index + 1]))
  const reorder = source.scope === 'book' || wanted.join() !== scoped.join()
  const writes: ChapterWrite[] = []
  for (const chapter of directory.values()) {
    const position = reorder ? (positions.get(chapter.id) ?? chapter.position) : chapter.position
    const capturedChapter = wanted.includes(chapter.id) ? old.get(chapter.id) : undefined
    const head = currentChapters.get(chapter.id)!
    if (capturedChapter) {
      const original = native.get(capturedChapter.revision_id)!
      const revision = changes.has(chapter.id)
        ? {
            ...original,
            id: id(),
            revision: head.revision + 1,
            parent_id: head.id,
            created,
            cause: '从跨端归档恢复',
          }
        : undefined
      if (revision || position !== chapter.position)
        writes.push({
          chapter: {
            ...chapter,
            position,
            title: capturedChapter.title,
            archived: 0,
            stale: capturedChapter.stale,
            revision: revision?.revision ?? chapter.revision,
          },
          revision,
        })
      actual.set(chapter.id, revision ?? head)
    } else if (scoped.includes(chapter.id)) {
      writes.push({ chapter: { ...chapter, archived: 1 } })
    } else if (chapter.position !== position) {
      writes.push({ chapter: { ...chapter, position } })
    }
  }
  const projects = versions(pack.project_history)
  const project =
    preview.projectChanged && source.shared.project_id
      ? {
          ...projects.get(source.shared.project_id)!,
          id: id(),
          parent_id: current.project_id,
        }
      : undefined
  const historicalSettings = versions(pack.settings_history).get(source.shared.settings_id ?? '')
  const originalRevisions = new Map(
    [...(pack.source_revisions ?? []), ...pack.revisions].map((row) => [row.id, row]),
  )
  const rebindProof = (evidence: SettingsEvidence, nativeId = evidence.revision_id) => {
    const before = old.get(evidence.chapter_id)
    const revision = actual.get(evidence.chapter_id)
    const original = native.get(nativeId) ?? originalRevisions.get(evidence.revision_id)
    if (before && revision && original?.revision === before.revision && original.sha256 === revision.sha256)
      return { ...evidence, revision_id: revision.id }
    return evidence
  }
  const settings =
    preview.settingsChanged && historicalSettings
      ? await prepareSettingsVersion({
          ...historicalSettings,
          id: id(),
          parent_id: current.settings_id,
          project_version_id: project?.id ?? current.project_id,
          origins: Object.fromEntries(
            Object.entries(historicalSettings.origins).map(([key, value]) => [
              key,
              {
                ...value,
                project_version_id:
                  project && value.project_version_id === source.shared.project_id
                    ? project.id
                    : value.project_version_id,
                evidence: value.evidence && rebindProof(value.evidence),
              },
            ]),
          ),
        })
      : undefined
  const story = await prepareStoryWrites(captured, source, plan, rebindProof, created)
  const outline =
    plan.oldOutline && (preview.outlineChanged || (settings && plan.oldOutline.settings_version_id))
      ? prepareOutlineVersion(
          buildReboundOutline(
            plan,
            id(),
            project?.id ?? current.project_id,
            settings?.id ?? current.settings_id,
          ),
        )
      : undefined
  const restoreState: PortableRestoreState = {
    facts: [
      ...(pack.restore_state?.facts ?? []).filter(({ evidence }) => !plan.touched.has(evidence.chapter_id)),
      ...plan.factSources.map((fact) => ({ ...fact, evidence: rebindProof(fact.evidence) })),
    ],
    length_targets: plan.lengthTargets.values,
  }
  return { writes, project, settings, story, outline, restoreState }
}

function buildReboundOutline(
  plan: ReturnType<typeof planCheckpointRestore>,
  id: string,
  projectId: string | null,
  settingsId: string | null,
) {
  const saved = plan.oldOutline!
  const current = plan.currentOutline
  const oldScope = saved.plan.volumes.find((volume) =>
    volume.units.some(({ chapters }) => chapters.some(({ chapter_id }) => plan.wanted.includes(chapter_id))),
  )
  const chosen =
    plan.whole || !current
      ? saved.plan
      : {
          ...current.plan,
          volumes: current.plan.volumes.map((volume) => (volume.id === oldScope?.id ? oldScope : volume)),
          promises: [
            ...current.plan.promises.filter(
              (window) => !windowIds(window).every((id) => plan.touched.has(id)),
            ),
            ...saved.plan.promises.filter((window) => windowIds(window).every((id) => plan.touched.has(id))),
          ],
        }
  const reference = new Map(
    [...(current?.reference_cards ?? []), ...saved.reference_cards].map((row) => [outlineCardKey(row), row]),
  )
  const cards = new Set(
    chosen.volumes.flatMap(({ units }) =>
      units.flatMap(({ chapters }) =>
        chapters.flatMap(({ reference_cards }) => (reference_cards ?? []).map(outlineCardKey)),
      ),
    ),
  )
  const seeds = { ...(current?.promise_sources ?? {}), ...saved.promise_sources }
  return {
    ...saved,
    id,
    parent_id: plan.current.outline_id,
    project_version_id: projectId,
    settings_version_id:
      saved.settings_version_id || (!plan.whole && current?.settings_version_id) ? settingsId : null,
    plan: chosen,
    reference_cards: [...reference].filter(([key]) => cards.has(key)).map(([, row]) => row),
    promise_sources: Object.fromEntries(
      chosen.promises.flatMap(({ promise_id }) =>
        Object.hasOwn(seeds, promise_id) ? [[promise_id, seeds[promise_id]]] : [],
      ),
    ),
  }
}

async function prepareStoryWrites(
  { pack }: Captured,
  source: PortableCheckpoint,
  plan: ReturnType<typeof planCheckpointRestore>,
  rebind: (evidence: SettingsEvidence, nativeId: string) => SettingsEvidence,
  created: number,
) {
  const selected = new Map([...plan.currentStory].map(([id, row]) => [id, row.version]))
  const sources = source.restore_sources?.story_heads ?? {}
  const nativeHistory = pack.story_history?.native_history ?? {}
  for (const id of plan.records) {
    const before = plan.priorStory.get(id)
    const now = selected.get(id)
    if (before) selected.set(id, before)
    else if (now) selected.set(id, { ...now, archived: true })
  }
  const written = new Map<string, StoryCheckpointWrite>()
  for (const selectedVersion of orderStoryHeads(selected)) {
    const id = selectedVersion.record_id
    if (!plan.records.has(id)) continue
    const prior = plan.priorStory.get(id)
    const current = plan.currentStory.get(id)
    const basis = prior
      ? nativeHistory[id].find((row) => row.revision === sources[id].revision)!
      : current!.native
    const rebound = (evidence: SettingsEvidence, nativeId: string) =>
      prior ? rebind(evidence, nativeId) : evidence
    const sourceProof = selectedVersion.evidence
    const record = sourceProof.record.map((proof, index) => rebound(proof, basis.evidence.record[index]))
    const stages = sourceProof.stages.map((group, index) =>
      group.map((proof, item) => rebound(proof, basis.evidence.stages[index][item])),
    )
    const dependencies = { ...selectedVersion.dependencies }
    const nativeDependencies = { ...basis.dependencies }
    for (const [parent, versionId] of Object.entries(dependencies)) {
      const parentBeforeId = prior ? sources[parent]?.version_id : plan.currentStory.get(parent)?.version.id
      const parentAfter = written.get(parent)
      if (parentAfter && parentBeforeId === versionId) {
        dependencies[parent] = parentAfter.version.id
        nativeDependencies[parent] = parentAfter.history.revision
      }
    }
    if (
      current?.version.id === selectedVersion.id &&
      current.version.archived === selectedVersion.archived &&
      compactJSON({ record, stages, dependencies }) ===
        compactJSON({ ...sourceProof, dependencies: selectedVersion.dependencies })
    )
      continue
    const { id: _versionId, ...draft } = selectedVersion
    const version = await buildStoryVersion(pack.branch.id, {
      ...draft,
      parent_id: current?.version.id ?? null,
      evidence: { record, stages },
      dependencies,
    })
    const pickNative = (proof: SettingsEvidence, original: SettingsEvidence, nativeId: string) =>
      proof.revision_id === original.revision_id ? nativeId : proof.revision_id
    written.set(id, {
      version,
      history: {
        revision: (current?.native.revision ?? -1) + 1,
        version_id: version.id,
        created,
        dependencies: nativeDependencies,
        evidence: {
          record: record.map((proof, index) =>
            pickNative(proof, sourceProof.record[index], basis.evidence.record[index]),
          ),
          stages: stages.map((group, index) =>
            group.map((proof, item) =>
              pickNative(proof, sourceProof.stages[index][item], basis.evidence.stages[index][item]),
            ),
          ),
        },
      },
    })
  }
  return [...written.values()]
}
