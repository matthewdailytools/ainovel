export type Draft = { id: string; base: number; text: string; updated: number; fromOtherWindow?: boolean }
const windowId = sessionStorage.getItem('ainovel-draft-window') || crypto.randomUUID()
sessionStorage.setItem('ainovel-draft-window', windowId)
type Stored = Draft & { chapter_id?: string; window_id?: string }
let connection: Promise<IDBDatabase> | undefined
function open() {
  return (connection ??= new Promise((resolve, reject) => {
    const request = indexedDB.open('ainovel-drafts', 1)
    request.onupgradeneeded = () => request.result.createObjectStore('drafts', { keyPath: 'id' })
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  }))
}
export async function readDraft(id: string): Promise<Draft | undefined> {
  const db = await open()
  return new Promise((resolve, reject) => {
    const request = db.transaction('drafts').objectStore('drafts').getAll()
    request.onsuccess = () => {
      const rows = (request.result as Stored[])
        .filter((row) => row.chapter_id === id || row.id === id)
        .sort((a, b) => b.updated - a.updated)
      const row = rows.find((row) => row.window_id === windowId) || rows[0]
      resolve(row ? { ...row, id, fromOtherWindow: row.window_id !== windowId } : undefined)
    }
    request.onerror = () => reject(request.error)
  })
}
export async function writeDraft(draft: Draft): Promise<void> {
  const db = await open()
  return new Promise((resolve, reject) => {
    const transaction = db.transaction('drafts', 'readwrite')
    transaction
      .objectStore('drafts')
      .put({ ...draft, id: draft.id + '@' + windowId, chapter_id: draft.id, window_id: windowId })
    transaction.oncomplete = () => resolve()
    transaction.onerror = () => reject(transaction.error)
  })
}
