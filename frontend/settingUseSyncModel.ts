import { eligibleOutlineSettings, outlineChapterPlans, type OutlineVersion } from './outlineSyncModel'
import {
  parseSettingsEvidence,
  settingsId,
  type SettingsEvidence,
  type SettingsVersion,
} from './settingsSyncModel'
import { compactJSON, verifyStoryEvidence, type StoryChapterRevision } from './storySyncModel'

const format = 'ainovel-manuscript-sync/1' as const
type Hash = (text: string) => Promise<string>

export type SettingUseVersion = {
  id: string
  parent_id: string | null
  outline_version_id: string
  settings_version_id: string
  item_id: string
  evidence: SettingsEvidence
  chapter_sha256: string
  action: 'confirmed' | 'withdrawn'
  actor: 'author'
}
export type SettingUseDraft = Omit<SettingUseVersion, 'id'>
export type SettingUseEntry = { version: SettingUseVersion; created: number; source_device?: string }
export type SettingUseHistory = {
  adopted_heads: Record<string, string>
  versions: SettingUseEntry[]
  native_bindings?: Record<string, string>
}
export type SettingUseVersionObject = {
  format: typeof format
  kind: 'setting-use-version'
  branch_id: string
  version: SettingUseVersion
}
export type SettingUseHeadObject = {
  format: typeof format
  kind: 'setting-use-head'
  branch_id: string
  slot_id: string
  revision_id: string
}
export type SettingUseSyncObject = SettingUseVersionObject | SettingUseHeadObject
export type SettingUseLookups = {
  use: (id: string) => SettingUseVersion | undefined
  revision: (id: string) => StoryChapterRevision | undefined
  settings: (id: string) => SettingsVersion | undefined
  outline: (id: string) => OutlineVersion | undefined
}

export type SettingUseStatus = 'current' | 'outdated' | 'superseded' | 'withdrawn' | 'missing'
export type SettingUseRecord = {
  entry: SettingUseEntry
  status: SettingUseStatus
  title: string
  quote: string | null
  canWithdraw: boolean
}

function invalid(message: string): never {
  throw new Error(message)
}

function object(value: unknown, allowed?: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) invalid('设定使用证据须为结构化对象。')
  const row = value as Record<string, unknown>
  if (allowed && Object.keys(row).some((field) => !allowed.includes(field)))
    invalid('设定使用证据含无法识别的字段。')
  return row
}

function digest(value: unknown): string {
  if (typeof value !== 'string' || value.length !== 64 || /[^a-f0-9]/u.test(value))
    invalid('设定使用证据的摘要或版本编号无效。')
  return value
}

const draftFields = [
  'parent_id',
  'outline_version_id',
  'settings_version_id',
  'item_id',
  'evidence',
  'chapter_sha256',
  'action',
  'actor',
]

function draft(row: Record<string, unknown>): SettingUseDraft {
  if ((row.action !== 'confirmed' && row.action !== 'withdrawn') || row.actor !== 'author')
    invalid('设定使用证据需要作者明确确认或撤回。')
  const parent_id = row.parent_id === null ? null : digest(row.parent_id)
  if (row.action === 'withdrawn' && parent_id === null) invalid('撤回证据缺少上一份确认。')
  return {
    parent_id,
    outline_version_id: settingsId(row.outline_version_id),
    settings_version_id: settingsId(row.settings_version_id),
    item_id: settingsId(row.item_id),
    evidence: parseSettingsEvidence(row.evidence),
    chapter_sha256: digest(row.chapter_sha256),
    action: row.action,
    actor: row.actor,
  }
}

export function settingUseSlotKey(chapterId: string, settingsId: string, itemId: string) {
  return compactJSON([chapterId, settingsId, itemId])
}

export function settingUseSlot(
  branchId: string,
  chapterId: string,
  settingsVersionId: string,
  itemId: string,
  hash: Hash,
) {
  return hash(compactJSON([branchId, chapterId, settingsVersionId, itemId].map(settingsId)))
}

export function settingUseVersionSlot(branchId: string, version: SettingUseVersion, hash: Hash) {
  return settingUseSlot(
    branchId,
    version.evidence.chapter_id,
    version.settings_version_id,
    version.item_id,
    hash,
  )
}

export async function buildSettingUseVersion(
  branchId: string,
  input: SettingUseDraft,
  hash: Hash,
): Promise<SettingUseVersion> {
  const value = draft(object(input, draftFields))
  return { id: await hash(compactJSON([settingsId(branchId), value])), ...value }
}

export async function validateSettingUseVersion(branchId: string, input: unknown, hash: Hash) {
  const row = object(input, ['id', ...draftFields])
  const id = digest(row.id)
  const version = await buildSettingUseVersion(branchId, draft(row), hash)
  if (id !== version.id || id === version.parent_id) invalid('设定使用版本与摘要或工作版本不匹配。')
  return version
}

export function plannedSettingUseItems(
  settings: SettingsVersion | undefined,
  outline: OutlineVersion | undefined,
  chapterId: string,
) {
  if (
    !settings ||
    !outline ||
    outline.settings_version_id !== settings.id ||
    outline.project_version_id !== settings.project_version_id
  )
    return []
  const selected = outlineChapterPlans(outline.plan).find(
    ({ chapter_id }) => chapter_id === chapterId,
  )?.setting_ids
  if (!selected?.length) return []
  return eligibleOutlineSettings(settings).filter(
    ({ id, category_title }) => category_title !== undefined && selected.includes(id),
  )
}

export async function validateSettingUseVersionSources(
  version: SettingUseVersion,
  lookup: SettingUseLookups,
) {
  const settings = lookup.settings(version.settings_version_id)
  const outline = lookup.outline(version.outline_version_id)
  if (
    !settings ||
    !outline ||
    settings.id !== version.settings_version_id ||
    outline.id !== version.outline_version_id
  )
    invalid('设定使用证据缺少采用时的设定或大纲。')
  const item = plannedSettingUseItems(settings, outline, version.evidence.chapter_id).find(
    ({ id }) => id === version.item_id,
  )
  if (!item) invalid('请选择章纲挂载的正式非世界设定。')
  if (version.parent_id) {
    const parent = lookup.use(version.parent_id)
    if (
      !parent ||
      parent.id !== version.parent_id ||
      parent.evidence.chapter_id !== version.evidence.chapter_id ||
      parent.settings_version_id !== version.settings_version_id ||
      parent.item_id !== version.item_id
    )
      invalid('设定使用证据的父版本缺失或归属不同。')
    if (
      version.action === 'withdrawn' &&
      (parent.action !== 'confirmed' ||
        parent.outline_version_id !== version.outline_version_id ||
        parent.chapter_sha256 !== version.chapter_sha256 ||
        compactJSON(parent.evidence) !== compactJSON(version.evidence))
    )
      invalid('撤回版本须保留上一确认的原稿与引用。')
  }
  const original = lookup.revision(version.evidence.revision_id)
  if (!original || original.sha256 !== version.chapter_sha256)
    invalid('找不到确切的正文修订或全文校验值不符。')
  const quote = await verifyStoryEvidence(version.evidence, original)
  if (!quote.replace(/[\p{White_Space}\x1c-\x1f]/gu, '')) invalid('请选择含正文的有效选区。')
  return { title: item.title, quote }
}

export async function validateSettingUseSyncObject(
  branchId: string,
  objectId: string,
  value: unknown,
  hash: Hash,
): Promise<SettingUseSyncObject> {
  const immutable = objectId.startsWith('setting-use-version:')
  const row = object(value, [
    'format',
    'kind',
    'branch_id',
    ...(immutable ? ['version'] : ['slot_id', 'revision_id']),
  ])
  if (row.format !== format || row.branch_id !== settingsId(branchId))
    invalid('设定使用证据与当前工作版本不符。')
  if (immutable && row.kind === 'setting-use-version') {
    const version = await validateSettingUseVersion(branchId, row.version, hash)
    if (objectId !== `setting-use-version:${version.id}`) invalid('设定使用来源编号不一致。')
    return { format, kind: row.kind, branch_id: branchId, version }
  }
  if (!immutable && row.kind === 'setting-use-head') {
    const slot_id = digest(row.slot_id)
    if (objectId !== `setting-use:${slot_id}`) invalid('设定使用采用头的归属不一致。')
    return { format, kind: row.kind, branch_id: branchId, slot_id, revision_id: digest(row.revision_id) }
  }
  return invalid('同步设定使用证据需要正式修订，撤回须形成新版本。')
}

export function settingUseObjectSignature(value: SettingUseSyncObject | null) {
  return !value
    ? 'null'
    : value.kind === 'setting-use-head'
      ? JSON.stringify([value.kind, value.branch_id, value.slot_id, value.revision_id])
      : JSON.stringify([value.kind, value.branch_id, value.version.id])
}

export async function validateSettingUseHistory(
  branchId: string,
  input: unknown,
  lookup: Omit<SettingUseLookups, 'use'> & {
    nativeRevision: (id: string) => (StoryChapterRevision & { revision: number }) | undefined
  },
  hash: Hash,
): Promise<SettingUseHistory> {
  const row = object(input, ['adopted_heads', 'versions', 'native_bindings'])
  const heads = Object.entries(object(row.adopted_heads))
  if (
    !Array.isArray(row.versions) ||
    !row.versions.length ||
    row.versions.length > 20000 ||
    heads.length > row.versions.length
  )
    invalid('设定使用历史的记录数量不符。')
  const versions: SettingUseEntry[] = []
  const byId = new Map<string, SettingUseVersion>()
  for (const source of row.versions) {
    const entry = object(source, ['version', 'created', 'source_device'])
    const version = await validateSettingUseVersion(branchId, entry.version, hash)
    if (
      byId.has(version.id) ||
      typeof entry.created !== 'number' ||
      !Number.isFinite(entry.created) ||
      entry.created < 0
    )
      invalid('设定使用历史存在重复或无效的版本。')
    if (
      entry.source_device !== undefined &&
      (typeof entry.source_device !== 'string' ||
        !entry.source_device ||
        [...entry.source_device].length > 128 ||
        /\p{Surrogate}/u.test(entry.source_device))
    )
      invalid('设定使用历史的源设备编号无效。')
    byId.set(version.id, version)
    versions.push({
      version,
      created: entry.created,
      ...(entry.source_device !== undefined ? { source_device: entry.source_device } : {}),
    })
  }
  for (const { version } of versions)
    await validateSettingUseVersionSources(version, { ...lookup, use: (id) => byId.get(id) })
  for (const [slot, id] of heads) {
    const selected = byId.get(digest(id))
    if (!selected || digest(slot) !== (await settingUseVersionSlot(branchId, selected, hash)))
      invalid('设定使用采用头缺少对应该章和设定的原始版本。')
  }
  const bindings = row.native_bindings === undefined ? [] : Object.entries(object(row.native_bindings))
  const sources = new Set(versions.map(({ version }) => version.evidence.revision_id))
  if (bindings.length > sources.size) invalid('正文采用关系超过设定证据的来源数量。')
  const native_bindings = Object.fromEntries(
    bindings.map(([source, native]) => {
      const sourceId = settingsId(source)
      const nativeId = settingsId(native)
      const original = lookup.revision(sourceId)
      const adopted = lookup.nativeRevision(nativeId)
      const previousNative = lookup.nativeRevision(sourceId)
      if (
        !sources.has(sourceId) ||
        !original ||
        !adopted ||
        sourceId === nativeId ||
        (!!previousNative && adopted.revision <= previousNative.revision) ||
        original.chapter_id !== adopted.chapter_id ||
        original.sha256 !== adopted.sha256 ||
        original.content !== adopted.content
      )
        invalid('设定使用证据的正文采用关系不属于同一已保存原稿。')
      return [sourceId, nativeId]
    }),
  )
  return {
    adopted_heads: Object.fromEntries(heads.map(([slot, id]) => [slot, digest(id)])),
    versions,
    ...(bindings.length ? { native_bindings } : {}),
  }
}
