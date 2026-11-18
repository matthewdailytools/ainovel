import { createDraftOwner, discardOwnDraft, readDraft, writeDraft } from './drafts'
import { readLocalProjectDraft, type ProjectDraft } from './projectSyncModel'

const draftOwner = createDraftOwner('ainovel-project-draft-window')
const draftKey = (branchId: string) => `offline/project/${branchId}`

export async function readProjectDraft(branchId: string) {
  const saved = await readDraft(draftKey(branchId), await draftOwner())
  if (!saved) return undefined
  return {
    draft: readLocalProjectDraft(JSON.parse(saved.text)),
    foreign: saved.fromOtherWindow === true,
  }
}

export async function writeProjectDraft(branchId: string, draft: ProjectDraft) {
  await writeDraft(
    { id: draftKey(branchId), text: JSON.stringify({ format: 1, ...draft }), base: 0, updated: Date.now() },
    await draftOwner(),
  )
}

export async function discardProjectDraft(branchId: string) {
  await discardOwnDraft(draftKey(branchId), await draftOwner())
}
