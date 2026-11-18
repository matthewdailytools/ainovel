import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { LocalEvidence, LocalRevision } from './offlineStore'

export function PortableEvidence({
  docs,
  chapters,
  readSource,
}: {
  docs: LocalEvidence[]
  chapters: { id: string; revision: number }[]
  readSource: (id: string) => Promise<LocalRevision | undefined>
}) {
  const [preview, setPreview] = useState<{ documentId: string; source?: LocalRevision; failure?: string }>()
  const newestRequest = useRef(0)
  if (!docs.length) return null
  const known = new Map(chapters.map((row) => [row.id, row.revision]))
  async function showSource(doc: LocalEvidence, link: LocalEvidence['chapters'][number]) {
    if (!link.revision_id) return
    const request = ++newestRequest.current
    setPreview(undefined)
    try {
      const record = await readSource(link.revision_id)
      if (
        !record ||
        record.id !== link.revision_id ||
        record.chapter_id !== link.id ||
        record.revision !== link.revision ||
        record.sha256 !== link.sha256
      )
        throw new Error('缺少哈希一致的原章稿修订。')
      if (request === newestRequest.current) setPreview({ documentId: doc.id, source: record })
    } catch (error) {
      if (request === newestRequest.current)
        setPreview({
          documentId: doc.id,
          failure: error instanceof Error ? error.message : '来源稿件暂时无法读取。',
        })
    }
  }
  return (
    <section aria-label="随稿依据">
      <h3>随稿依据</h3>
      {docs.map((document) => (
        <details key={document.id}>
          <summary>{document.title}</summary>
          <p>
            {document.chapters.some((chapter) => known.get(chapter.id) !== chapter.revision)
              ? '已有章稿改版，以下为来源版本的只读记录。'
              : '依据快照 · 只读'}
          </p>
          <pre className="report">{document.body}</pre>
          {document.chapters.some((link) => link.revision_id) && (
            <ul aria-label="来源章稿修订">
              {document.chapters.map(
                (link) =>
                  link.revision_id && (
                    <li key={`${link.id}/${link.revision_id}`}>
                      <button type="button" onClick={() => void showSource(document, link)}>
                        查看来源修订 {link.revision} · {link.revision_id.slice(0, 8)}
                      </button>
                    </li>
                  ),
              )}
            </ul>
          )}
          {preview?.documentId === document.id && preview.failure && <p role="alert">{preview.failure}</p>}
          {preview?.documentId === document.id && preview.source && (
            <section aria-label="来源章稿正文">
              <p>
                来源修订 <code>{preview.source.id}</code> · SHA-256 <code>{preview.source.sha256}</code>
              </p>
              <pre className="report">{preview.source.content || '空稿'}</pre>
            </section>
          )}
        </details>
      ))}
    </section>
  )
}

export function LocalPortableEvidence({
  branchId,
  chapters,
}: {
  branchId: string
  chapters: { id: string; revision: number }[]
}) {
  const [docs, setDocs] = useState<LocalEvidence[]>([])
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    void api<LocalEvidence[]>(`/branches/${branchId}/manuscript-evidence`)
      .then((value) => {
        if (active) setDocs(value)
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : '随稿依据暂时无法读取。')
      })
    return () => {
      active = false
    }
  }, [branchId])
  return error ? (
    <p role="alert">{error}</p>
  ) : (
    <PortableEvidence
      docs={docs}
      chapters={chapters}
      readSource={(id) => api<LocalRevision>(`/branches/${branchId}/manuscript-sources/${id}`)}
    />
  )
}
