import { beforeEach, expect, test, vi } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import {
  createLocalProject,
  confirmLocalReferenceCard,
  exportLocalManuscript,
  importLocalManuscript,
  manuscriptHash,
  readLocalReferenceHistory,
} from './offlineStore'
import { compactJSON } from './storySyncModel'

beforeEach(() => vi.stubGlobal('indexedDB', new IDBFactory()))

test('交换包恢复手法卡地址，只有选定精确修订才再次附带原句', async () => {
  const { novel, branch } = await createLocalProject('手法卡合成样例')
  const pack = await exportLocalManuscript(novel.id)
  const quote = '𠮷字藏在门背。'
  const draft = {
    parent_id: null,
    session_id: 'session',
    card_id: 'card',
    revision: 1,
    assessment: 'ready',
    card: {
      observation: '字藏在门背。',
      interpretation: '读者晚于人物看到线索。',
      technique: '使用回头时显现的物件。',
      conditions: '角色有自然理由回头。',
      exceptions: '替换门与刻字。',
    },
    citation: {
      book_id: 'book',
      unit_id: 'unit',
      file_sha256: 'e'.repeat(64),
      title: '自编材料',
      selection_start: 12,
      selection_end: 12 + quote.length,
      selection_sha256: await manuscriptHash(quote),
      evidence_sha256: await manuscriptHash(quote),
      evidence_start: 12,
      evidence_end: 12 + quote.length,
    },
    goal: '拆解线索披露时机。',
    missing_fields: [],
  }
  const id = await manuscriptHash(compactJSON([branch.id, draft]))
  const history = {
    adopted_heads: [{ session_id: draft.session_id, card_id: draft.card_id, revision_id: id }],
    versions: [{ ...draft, id }],
    source_devices: { [id]: 'desktop-m2' },
    excerpts: { [id]: quote },
  }
  vi.stubGlobal('indexedDB', new IDBFactory())
  const fileWith = (reference_history: unknown) => new Blob([JSON.stringify({ ...pack, reference_history })])
  await expect(
    importLocalManuscript(fileWith({ ...history, excerpts: { [id]: quote.replace('藏', '印') } })),
  ).rejects.toThrow('原文与引用校验值不一致')
  await importLocalManuscript(fileWith(history))
  expect(await readLocalReferenceHistory(novel.id)).toEqual(history)
  expect((await exportLocalManuscript(novel.id)).reference_history).not.toHaveProperty('excerpts')
  expect((await exportLocalManuscript(novel.id, [id])).reference_history).toEqual(history)
  await expect(exportLocalManuscript(novel.id, ['not-in-work'])).rejects.toThrow('不属于当前作品')
  const authored = await confirmLocalReferenceCard(novel.id, id, {
    ...draft.card,
    technique: '改为三次查看刻痕。',
  })
  expect(authored.parent_id).toBe(id)
  expect(authored.citation).toEqual(draft.citation)
  await expect(confirmLocalReferenceCard(novel.id, id, draft.card)).rejects.toMatchObject({
    code: 'conflict',
  })
  expect((await readLocalReferenceHistory(novel.id))?.adopted_heads[0].revision_id).toBe(authored.id)
})
