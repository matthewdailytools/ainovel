import type { DefinitionInput, GenreCatalog, GenreProfile } from './api'

export type GenreDraft = {
  audience: GenreProfile['audience']
  primary: string
  customPrimary: string
  secondary: string[]
  customSecondary: string
  mechanisms: string[]
  customMechanisms: string
  exceptions: string
  notes: string
  originalGenres: string[]
  originalMechanisms: string[]
  genresChanged: boolean
  mechanismsChanged: boolean
}
export const emptyGenreDraft: GenreDraft = {
  audience: '不限',
  primary: '',
  customPrimary: '',
  secondary: [],
  customSecondary: '',
  mechanisms: [],
  customMechanisms: '',
  exceptions: '',
  notes: '',
  originalGenres: [],
  originalMechanisms: [],
  genresChanged: false,
  mechanismsChanged: false,
}
const separate = (value: string) => [
  ...new Set(
    value
      .split(/[、，,\n]/u)
      .map((v) => v.trim())
      .filter(Boolean),
  ),
]

export function genreDraftFromAdopted(profile: GenreProfile, catalog: GenreCatalog): GenreDraft {
  const raw = profile.reader_definition
  const first = raw?.genres?.[0]
  const known = catalog.genres.find(
    (g) => g.id === first || g.name === first || g.name === profile.primary_genre,
  )
  const customPrimary = !known ? first || profile.primary_genre || '' : ''
  return {
    audience: profile.audience,
    primary: known?.id || (customPrimary ? '__custom__' : ''),
    customPrimary,
    secondary: profile.genre_ids.filter((id) => id !== known?.id),
    customSecondary: profile.custom_genres.filter((v) => v !== customPrimary).join('、'),
    mechanisms: profile.mechanism_ids,
    customMechanisms: profile.custom_mechanisms.join('、'),
    exceptions: profile.author_exceptions.join('\n'),
    notes: raw?.text || profile.author_notes,
    originalGenres: raw?.genres || [profile.primary_genre || '', ...profile.secondary_genres].filter(Boolean),
    originalMechanisms: raw?.mechanisms || profile.mechanisms,
    genresChanged: false,
    mechanismsChanged: false,
  }
}

export function updateGenreDraft(draft: GenreDraft, values: Partial<GenreDraft>): GenreDraft {
  return {
    ...draft,
    ...values,
    genresChanged:
      draft.genresChanged ||
      ['primary', 'customPrimary', 'secondary', 'customSecondary'].some((k) => k in values),
    mechanismsChanged: draft.mechanismsChanged || ['mechanisms', 'customMechanisms'].some((k) => k in values),
  }
}

export function readerDefinition(draft: GenreDraft, storyRevision: number | undefined): DefinitionInput {
  const primary = draft.primary === '__custom__' ? draft.customPrimary.trim() : draft.primary
  if (draft.genresChanged && !primary && (draft.secondary.length || separate(draft.customSecondary).length))
    throw new Error('请先选择主题材，或清空辅题材后保持不设置。')
  const genres = draft.genresChanged
    ? [...new Set([primary, ...draft.secondary, ...separate(draft.customSecondary)].filter(Boolean))]
    : draft.originalGenres
  const mechanisms = draft.mechanismsChanged
    ? [...new Set([...draft.mechanisms, ...separate(draft.customMechanisms)])]
    : draft.originalMechanisms
  if (genres.length > 10 || mechanisms.length > 10)
    throw new Error('主辅题材合计最多10项，附加机制最多10项。')
  if (storyRevision === undefined) throw new Error('当前故事版本尚未读取，请重试。')
  return {
    kind: 'reader',
    audience: draft.audience,
    genres,
    mechanisms,
    author_exceptions: [
      ...new Set(
        draft.exceptions
          .split('\n')
          .map((v) => v.trim())
          .filter(Boolean),
      ),
    ],
    text: draft.notes.trim() || '本书采用所选主辅题材与机制；作者明确例外优先，阅读取向按结构化配置。',
    base_story_revision: storyRevision,
  }
}
