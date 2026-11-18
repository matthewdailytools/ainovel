import { discardOwnDraft, readDraft, writeDraft } from './drafts'
import {
  readLocalSettingsDraftVersion,
  settingsId,
  type SettingsConflictMerge,
  type SettingsVersion,
  type SettingsVersionRecord,
} from './settingsSyncModel'

export type LocalSettingsDraftSource =
  | { kind: 'history'; id: string; projectId: string | null }
  | { kind: 'merge'; context: SettingsConflictMerge; base: SettingsVersion }
export type LocalSettingsDraft = {
  version: SettingsVersion
  categoryId: string
  source?: LocalSettingsDraftSource
}

const ownerKey = 'ainovel-settings-draft-window'
let owner: Promise<string> | undefined

function draftOwner() {
  return (owner ??= new Promise<string>((resolve) => {
    const fresh = crypto.randomUUID()
    if (!navigator.locks) {
      resolve(fresh)
      return
    }
    function claim(id: string) {
      void navigator.locks
        .request(`${ownerKey}-${id}`, { ifAvailable: true }, (lock) => {
          if (!lock) return claim(fresh)
          try {
            sessionStorage.setItem(ownerKey, id)
          } catch {
            // Drafts remain available as recovery copies without a saved window identifier.
          }
          resolve(id)
          return new Promise<void>((release) => {
            function close(event: PageTransitionEvent) {
              if (event.persisted) return
              window.removeEventListener('pagehide', close)
              release()
            }
            window.addEventListener('pagehide', close)
          })
        })
        .catch(() => resolve(fresh))
    }
    let previous: string | null = null
    try {
      previous = sessionStorage.getItem(ownerKey)
    } catch {
      // An independent editor still has its own draft.
    }
    // A duplicated tab can share sessionStorage with a live editor until it claims a new owner.
    claim(previous || fresh)
  }))
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('无法识别本机设定草稿。')
  return value as Record<string, unknown>
}

function receiptId(value: unknown) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9._:-]{1,128}$/u.test(value))
    throw new Error('无法识别设定草稿的冲突来源。')
  return value
}

function side(raw: unknown): SettingsVersionRecord {
  const row = record(raw)
  const source = row.source === undefined ? undefined : record(row.source)
  if (source && (typeof source.sequence !== 'number' || !Number.isSafeInteger(source.sequence)))
    throw new Error('无法识别设定草稿的冲突来源。')
  return {
    version: readLocalSettingsDraftVersion(row.version),
    ...(source && typeof source.sequence === 'number'
      ? {
          source: {
            device_id: receiptId(source.device_id),
            change_id: receiptId(source.change_id),
            sequence: source.sequence,
          },
        }
      : {}),
    ...(typeof row.recordedDevice === 'string' ? { recordedDevice: row.recordedDevice } : {}),
    ...(row.fromPackage === true ? { fromPackage: true } : {}),
  }
}

function draftSource(raw: unknown): LocalSettingsDraftSource | undefined {
  if (raw === undefined) return undefined
  const row = record(raw)
  if (row.kind === 'history')
    return {
      kind: 'history',
      id: settingsId(row.id),
      projectId: row.projectId === null ? null : settingsId(row.projectId),
    }
  if (row.kind === 'merge') {
    const context = record(row.context)
    return {
      kind: 'merge',
      base: readLocalSettingsDraftVersion(row.base),
      context: {
        conflictId: receiptId(context.conflictId),
        localRevisionId: settingsId(context.localRevisionId),
        current: side(context.current),
        incoming: side(context.incoming),
      },
    }
  }
  throw new Error('无法识别设定草稿的恢复来源。')
}

const draftKey = (branchId: string) => `offline/settings/${branchId}`

export async function readSettingsDraft(branchId: string) {
  const saved = await readDraft(draftKey(branchId), await draftOwner())
  if (!saved) return undefined
  const row = record(JSON.parse(saved.text))
  if (row.format !== 1) throw new Error('无法识别本机设定草稿格式。')
  return {
    draft: {
      version: readLocalSettingsDraftVersion(row.version),
      categoryId: settingsId(row.categoryId),
      source: draftSource(row.source),
    } satisfies LocalSettingsDraft,
    foreign: saved.fromOtherWindow === true,
  }
}

export async function writeSettingsDraft(branchId: string, draft: LocalSettingsDraft) {
  await writeDraft(
    { id: draftKey(branchId), text: JSON.stringify({ format: 1, ...draft }), base: 0, updated: Date.now() },
    await draftOwner(),
  )
}

export async function discardSettingsDraft(branchId: string) {
  await discardOwnDraft(draftKey(branchId), await draftOwner())
}
