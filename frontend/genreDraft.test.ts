import { describe, expect, it } from 'vitest'
import type { GenreCatalog, GenreProfile } from './api'
import { emptyGenreDraft, genreDraftFromAdopted, readerDefinition, updateGenreDraft } from './genreDraft'

const catalog: GenreCatalog = {
  version: 'test',
  audiences: [],
  mechanisms: [],
  genres: [
    { id: 'xianxia', name: '仙侠 · 修仙修真', title: '仙侠', family: 'fantasy', description: '' },
    { id: 'mystery', name: '悬疑 · 推理 · 解谜', title: '悬疑', family: 'mystery', description: '' },
  ],
}
const legacy: GenreProfile = {
  catalog_version: 'test',
  audience: '女频',
  configured: true,
  primary_genre: '作者自己的方向',
  secondary_genres: ['仙侠 · 修仙修真', '目录外副题材', '悬疑 · 推理 · 解谜'],
  genre_ids: ['xianxia', 'mystery'],
  custom_genres: ['作者自己的方向', '目录外副题材'],
  mechanisms: ['重生', '作者自己的机制'],
  mechanism_ids: ['plugin-2'],
  custom_mechanisms: ['作者自己的机制'],
  author_exceptions: ['无CP'],
  author_notes: '主角自主成长。',
  reading_promises: [],
  focus: [],
  checks: [],
  required_topics: [],
  optional_topics: [],
  conflicts: [],
  priority: [],
  story_revision: 7,
  reader_definition: {
    audience: '女频',
    genres: ['作者自己的方向', '修仙', '目录外副题材', '悬疑'],
    mechanisms: ['作者自己的机制', '重生'],
    author_exceptions: ['无CP'],
    text: '主角自主成长。',
  },
}

describe('reader configuration preserves author choices', () => {
  it('retains legacy names, custom primary and mixed secondary order when changing only channel or notes', () => {
    const loaded = genreDraftFromAdopted(legacy, catalog)
    expect(loaded.primary).toBe('__custom__')
    expect(loaded.customPrimary).toBe('作者自己的方向')
    const edited = updateGenreDraft(loaded, { audience: '男频', notes: '保留无CP，不增加后宫。' })
    const request = readerDefinition(edited, 7)
    expect(request.genres).toEqual(legacy.reader_definition?.genres)
    expect(request.mechanisms).toEqual(legacy.reader_definition?.mechanisms)
    expect(request.author_exceptions).toEqual(['无CP'])
    expect(request.base_story_revision).toBe(7)
  })
  it('applies an explicit new primary while preserving custom secondary and exceptions', () => {
    const draft = updateGenreDraft(genreDraftFromAdopted(legacy, catalog), {
      primary: 'mystery',
      secondary: ['xianxia'],
      exceptions: '无CP\n交易失败，不战斗\n无CP',
    })
    const request = readerDefinition(draft, 7)
    expect(request.genres).toEqual(['mystery', 'xianxia', '目录外副题材'])
    expect(request.author_exceptions).toEqual(['无CP', '交易失败，不战斗'])
  })
  it('keeps an unconfigured book writable without promoting a secondary or guessing a story version', () => {
    expect(readerDefinition(emptyGenreDraft, 0).genres).toEqual([])
    expect(() => readerDefinition(emptyGenreDraft, undefined)).toThrow('故事版本')
    const secondaryOnly = updateGenreDraft(emptyGenreDraft, { secondary: ['mystery'] })
    expect(() => readerDefinition(secondaryOnly, 0)).toThrow('主题材')
  })
})
