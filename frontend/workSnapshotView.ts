import type { components } from './api-schema'
import type { RestoreAffectedReport } from './ChapterRestorePreview'

export type WorkSnapshotScope = 'book' | 'volume' | 'chapter'
export type WorkSnapshot = {
  id: string
  label: string
  scope: WorkSnapshotScope
  scope_id: string | null
  purpose: string
  created: number
  story_revision: number
}

export type WorkSnapshotPreview = {
  snapshot: WorkSnapshot
  basis_sha256: string
  changes: {
    chapter_id: string
    title: string
    action: 'restore' | 'archive' | 'keep'
    snapshot_revision: number | null
    current_revision: number | null
  }[]
  in_place_blockers: string[]
  shared_changes: { definitions: string[]; conception: boolean; outline: boolean }
  affected_reports: RestoreAffectedReport[]
  affected_setting_uses: components['schemas']['SnapshotSettingUseImpact'][]
  notes: string[]
}

const definitionNames: Record<string, string> = {
  intent: '作者约定',
  reader: '题材设定',
  world: '世界规则',
  characters: '人物设定',
  outline: '旧版规划',
  project_brief: '立项',
}

export function snapshotSharedChanges({
  definitions,
  conception,
  outline,
}: WorkSnapshotPreview['shared_changes']) {
  return [
    ...definitions.map((kind) => definitionNames[kind] || kind),
    ...(conception ? ['分类构思与设定'] : []),
    ...(outline ? ['四级大纲'] : []),
  ]
}
