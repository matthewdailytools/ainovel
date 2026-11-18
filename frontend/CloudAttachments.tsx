import { useEffect, useRef, useState } from 'react'

type Attachment = { name: string; sha256: string; size: number; sealed: number }
type Listing = { files: Attachment[]; chunk_bytes: number; max_file_bytes: number }
type Upload = Attachment & { chunk_bytes: number; received: number[] }
const fileLimit = 64 * 1024 * 1024
const chunkLimit = 512 * 1024

function fileMetadata(value: unknown): value is Attachment {
  if (!value || typeof value !== 'object') return false
  const file = value as Attachment
  return (
    typeof file.name === 'string' &&
    file.name.trim().length > 0 &&
    file.name.length <= 400 &&
    !/[\\/\u0000-\u001f]/u.test(file.name) &&
    typeof file.sha256 === 'string' &&
    /^[a-f0-9]{64}$/.test(file.sha256) &&
    Number.isSafeInteger(file.size) &&
    file.size >= 1 &&
    file.size <= fileLimit &&
    (file.sealed === 0 || file.sealed === 1)
  )
}

async function hash(buffer: ArrayBuffer) {
  const bytes = new Uint8Array(await crypto.subtle.digest('SHA-256', buffer))
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

export function CloudAttachments({
  workId,
  service,
  token,
  ledgerId,
  inactive,
  onBusy,
}: {
  workId: string
  service: string
  token: string
  ledgerId?: string | null
  inactive?: boolean
  onBusy: (busy: boolean) => void
}) {
  const [listing, setListing] = useState<Listing>()
  const [selected, setSelected] = useState<File>()
  const [partial, setPartial] = useState<string>()
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const active = useRef<AbortController | undefined>(undefined)
  useEffect(() => () => active.current?.abort(), [])
  const ready = !inactive && !!ledgerId && !!service.trim() && /^[A-Za-z0-9_-]{32,128}$/.test(token.trim())
  async function request(path = '', method = 'GET', body?: Blob | object) {
    if (!ledgerId) throw new Error('请先核对作品的云端连接。')
    const origin = new URL(service.trim())
    if (
      !(
        origin.protocol === 'https:' ||
        (origin.protocol === 'http:' && ['127.0.0.1', '[::1]'].includes(origin.hostname))
      ) ||
      origin.username ||
      origin.password ||
      origin.pathname !== '/' ||
      origin.search ||
      origin.hash
    )
      throw new Error('请使用 HTTPS 或回环 HTTP 的同步服务地址。')
    const sign = body instanceof Blob
    const response = await fetch(
      `${origin.origin}/api/sync/works/${encodeURIComponent(workId)}/attachments${path}`,
      {
        method,
        credentials: 'omit',
        redirect: 'error',
        cache: 'no-store',
        headers: {
          Authorization: `Bearer ${token.trim()}`,
          'X-Ainovel-Ledger': ledgerId,
          ...(body ? { 'Content-Type': sign ? 'application/octet-stream' : 'application/json' } : {}),
        },
        ...(body ? { body: sign ? body : JSON.stringify(body) } : {}),
        signal: active.current
          ? AbortSignal.any([active.current.signal, AbortSignal.timeout(90_000)])
          : AbortSignal.timeout(90_000),
      },
    )
    if (!response.ok) {
      const data = await response.json()
      throw new Error(data.message || `附件操作失败（HTTP ${response.status}）。`)
    }
    return response
  }
  async function run(operation: () => Promise<void>) {
    if (busy || !ready) return
    active.current = new AbortController()
    setBusy(true)
    onBusy(true)
    setMessage('')
    setError('')
    try {
      await operation()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '附件操作未完成。')
    } finally {
      active.current = undefined
      setBusy(false)
      onBusy(false)
    }
  }
  async function refresh() {
    const current = (await (await request()).json()) as Listing | null
    if (
      !current ||
      current.chunk_bytes !== chunkLimit ||
      !Number.isSafeInteger(current.max_file_bytes) ||
      current.max_file_bytes < 1 ||
      current.max_file_bytes > fileLimit ||
      !Array.isArray(current.files) ||
      current.files.length > 256 ||
      current.files.some((file) => !fileMetadata(file) || file.sealed !== 1)
    )
      throw new Error('云附件清单无法核对。')
    setListing(current)
    return current
  }
  async function upload() {
    if (!selected) return
    const latest = listing ?? (await refresh())
    if (selected.size < 1 || selected.size > latest.max_file_bytes)
      throw new Error('附件为空或超过服务的单文件上限。')
    const digest = await hash(await selected.arrayBuffer())
    const state = (await (
      await request('', 'POST', {
        name: selected.name,
        sha256: digest,
        size: selected.size,
      })
    ).json()) as Upload | null
    const chunks = Math.ceil(selected.size / latest.chunk_bytes)
    if (
      !state ||
      !fileMetadata(state) ||
      state.sha256 !== digest ||
      state.size !== selected.size ||
      state.chunk_bytes !== latest.chunk_bytes ||
      !Array.isArray(state.received) ||
      state.received.length > chunks ||
      new Set(state.received).size !== state.received.length ||
      state.received.some((slot) => !Number.isSafeInteger(slot) || slot < 0 || slot >= chunks) ||
      (state.sealed === 1 && state.received.length !== chunks)
    )
      throw new Error('云附件分片回执与本机文件不一致。')
    setPartial(digest)
    const received = new Set(state.received)
    for (let slot = 0; !state.sealed && slot < chunks; slot++) {
      if (!received.has(slot)) {
        await request(
          `/${digest}/parts/${slot}`,
          'POST',
          selected.slice(slot * state.chunk_bytes, (slot + 1) * state.chunk_bytes),
        )
      }
      setMessage(`附件分片 ${slot + 1}/${chunks}`)
    }
    const sealed: unknown = await (await request(`/${digest}/seal`, 'POST')).json()
    if (
      !fileMetadata(sealed) ||
      sealed.sealed !== 1 ||
      sealed.sha256 !== digest ||
      sealed.size !== selected.size
    )
      throw new Error('云附件封存回执与本机文件不一致。')
    setPartial(undefined)
    setMessage('附件已通过校验，可在其他设备下载。')
    await refresh()
  }
  async function download(file: Attachment) {
    const stream = (await request(`/${file.sha256}/content`)).body
    if (!stream) throw new Error('云附件响应没有正文。')
    const reader = stream.getReader()
    const data = new Uint8Array(file.size)
    let offset = 0
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      if (offset + value.length > data.length) {
        await reader.cancel()
        throw new Error('下载大小超出云附件清单。')
      }
      data.set(value, offset)
      offset += value.length
    }
    if (offset !== file.size || (await hash(data.buffer)) !== file.sha256) {
      throw new Error('下载内容校验失败。')
    }
    const href = URL.createObjectURL(new Blob([data], { type: 'application/octet-stream' }))
    const link = document.createElement('a')
    link.href = href
    link.download = file.name
    link.click()
    setTimeout(() => URL.revokeObjectURL(href), 1000)
  }
  return (
    <details>
      <summary>选定云附件</summary>
      <p>只上传此处选中的文件。附件与章稿分别下载，未完成的分片可续传。</p>
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      <fieldset disabled={!ready || busy}>
        <legend>附件</legend>
        <button
          type="button"
          onClick={() =>
            void run(async () => {
              await refresh()
            })
          }
        >
          读取云附件
        </button>
        <label>
          选择云附件
          <input type="file" onChange={(event) => setSelected(event.target.files?.[0])} />
        </label>
        <button type="button" disabled={!selected} onClick={() => void run(upload)}>
          上传选中文件
        </button>
        {partial && (
          <button
            type="button"
            onClick={() =>
              void run(async () => {
                await request('/' + partial, 'DELETE')
                setPartial(undefined)
                setMessage('已清理未完成上传。')
              })
            }
          >
            清理未完成分片
          </button>
        )}
        {listing && (
          <ul>
            {listing.files.map((file) => (
              <li key={file.sha256}>
                <button type="button" onClick={() => void run(() => download(file))}>
                  下载 {file.name}
                </button>{' '}
                · {file.size.toLocaleString()} 字节
              </li>
            ))}
          </ul>
        )}
      </fieldset>
    </details>
  )
}
