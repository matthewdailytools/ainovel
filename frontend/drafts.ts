export type Draft = {
  id: string
  base: number
  text: string
  updated: number
  revisionId?: string
  fromOtherWindow?: boolean
}
const windowId = (() => {
  const fallback = crypto.randomUUID()
  try {
    const id = sessionStorage.getItem('ainovel-draft-window') || fallback
    sessionStorage.setItem('ainovel-draft-window', id)
    return id
  } catch {
    return fallback
  }
})()

export function createDraftOwner(ownerKey: string): () => Promise<string> {
  let owner: Promise<string> | undefined
  return () =>
    (owner ??= new Promise<string>((resolve) => {
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
        // A new identifier is sufficient for writing this window's draft.
      }
      // Duplicated tabs can start with a copy of sessionStorage.
      claim(previous || fresh)
    }))
}

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
export async function readDraft(id: string, owner = windowId): Promise<Draft | undefined> {
  const db = await open()
  return new Promise((resolve, reject) => {
    const request = db.transaction('drafts').objectStore('drafts').getAll()
    request.onsuccess = () => {
      const rows = (request.result as Stored[])
        .filter((row) => row.chapter_id === id || row.id === id)
        .sort((a, b) => b.updated - a.updated)
      const row = rows.find((row) => row.window_id === owner) || rows[0]
      resolve(row ? { ...row, id, fromOtherWindow: row.window_id !== owner } : undefined)
    }
    request.onerror = () => reject(request.error)
  })
}
export async function writeDraft(draft: Draft, owner = windowId): Promise<void> {
  const db = await open()
  return new Promise((resolve, reject) => {
    const transaction = db.transaction('drafts', 'readwrite')
    transaction
      .objectStore('drafts')
      .put({ ...draft, id: draft.id + '@' + owner, chapter_id: draft.id, window_id: owner })
    transaction.oncomplete = () => resolve()
    transaction.onabort = () => reject(transaction.error)
  })
}

export async function discardOwnDraft(id: string, owner = windowId): Promise<void> {
  const db = await open()
  return new Promise((resolve, reject) => {
    const transaction = db.transaction('drafts', 'readwrite')
    transaction.objectStore('drafts').delete(id + '@' + owner)
    transaction.oncomplete = () => resolve()
    transaction.onabort = () => reject(transaction.error)
  })
}
