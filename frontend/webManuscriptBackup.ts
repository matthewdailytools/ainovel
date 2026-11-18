import type { ManuscriptPack } from './offlineStore'

const heading = 'ainovel-web-backup/1'
const ending = 'sha256 '
export const manuscriptBackupMediaType = 'application/x-ainovel-web-backup'
const maxFrameCharacters = 128 * 1024 * 1024
const maxFrames = 250_000
const digestPattern = /^[a-f0-9]{64}$/

const roots = ['format', 'novel', 'branch', 'chapters', 'revisions', 'source_revisions', 'evidence']
const histories = {
  project_history: ['adopted_revision_id', 'versions'],
  settings_history: ['adopted_revision_id', 'versions', 'source_devices'],
  outline_history: ['adopted_revision_id', 'versions', 'source_devices', 'required_story_records'],
  workflow_history: ['inspections', 'completions'],
  story_history: ['adopted_heads', 'versions', 'source_devices', 'native_history'],
  setting_use_history: ['adopted_heads', 'versions', 'native_bindings'],
  reference_history: ['adopted_heads', 'versions', 'source_devices', 'excerpts'],
}

type Digest = (text: string) => Promise<string>
type Frame = [path: string, kind: 'value' | 'list' | 'item', value: unknown]

function invalid(): never {
  throw new Error('独立备份的格式、分段或校验值无效，未导入作品。')
}

function* partition(pack: ManuscriptPack): Iterable<[string, unknown]> {
  for (const [name, value] of Object.entries(pack)) {
    if (value === undefined) continue
    if (Object.hasOwn(histories, name)) {
      for (const [key, field] of Object.entries(value)) {
        if (field !== undefined) yield [`${name}.${key}`, field]
      }
    } else {
      yield [name, value]
    }
  }
}

function* frames(pack: ManuscriptPack): Iterable<Frame> {
  for (const [path, value] of partition(pack)) {
    if (Array.isArray(value)) {
      yield [path, 'list', null]
      for (const item of value) yield [path, 'item', item]
    } else {
      yield [path, 'value', value]
    }
  }
}

export async function encodeWebManuscriptBackup(pack: ManuscriptPack, digest: Digest) {
  const records: BlobPart[] = [`${heading}\n`]
  const hashes: string[] = []
  for (const frame of frames(pack)) {
    const json = JSON.stringify(frame)
    if (json.length > maxFrameCharacters || hashes.length === maxFrames)
      throw new Error('此作品的单项记录或记录数量超过独立备份上限。已保存的作品和历史仍可使用。')
    const hash = await digest(json)
    hashes.push(hash)
    records.push(`${hash} ${json}\n`)
  }
  records.push(`${ending}${await digest(hashes.join(''))}\n`)
  return new Blob(records, { type: manuscriptBackupMediaType })
}

async function* lines(file: Blob) {
  const reader = file
    .stream()
    .pipeThrough(new TextDecoderStream('utf-8', { fatal: true }))
    .getReader()
  let partial: string[] = []
  let length = 0
  let finished = false
  try {
    for (;;) {
      const read = await reader.read().catch(invalid)
      if (read.done) {
        finished = true
        break
      }
      const chunk = read.value
      let offset = 0
      for (;;) {
        const end = chunk.indexOf('\n', offset)
        const piece = chunk.slice(offset, end < 0 ? undefined : end)
        length += piece.length
        if (length > maxFrameCharacters + 65) invalid()
        partial.push(piece)
        if (end < 0) break
        yield partial.join('')
        offset = end + 1
        partial = []
        length = 0
      }
    }
    if (length) invalid()
  } finally {
    if (!finished) void reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}

function locate(path: string, pack: Record<string, unknown>) {
  const [name, field, ...extra] = path.split('.')
  if (!field && !extra.length && path === name && roots.includes(name)) return { row: pack, key: name }
  if (
    !extra.length &&
    Object.hasOwn(histories, name) &&
    histories[name as keyof typeof histories].includes(field)
  ) {
    if (!Object.hasOwn(pack, name)) pack[name] = Object.create(null)
    return { row: pack[name] as Record<string, unknown>, key: field }
  }
  return invalid()
}

function addFrame(pack: Record<string, unknown>, data: unknown) {
  if (!Array.isArray(data) || data.length !== 3 || typeof data[0] !== 'string') invalid()
  const { row, key } = locate(data[0], pack)
  const [, kind, value] = data as Frame
  if (kind === 'item') {
    if (!Array.isArray(row[key])) invalid()
    row[key].push(value)
  } else {
    if (Object.hasOwn(row, key) || (kind !== 'value' && !(kind === 'list' && value === null))) invalid()
    row[key] = kind === 'list' ? [] : value
  }
}

export async function decodeWebManuscriptBackup(file: Blob, digest: Digest): Promise<unknown> {
  const pack: Record<string, unknown> = Object.create(null)
  const hashes: string[] = []
  let opened = false
  let sealed = false
  for await (const line of lines(file)) {
    if (!opened) {
      if (line !== heading) invalid()
      opened = true
    } else if (sealed) {
      invalid()
    } else if (line.startsWith(ending)) {
      if (line.slice(ending.length) !== (await digest(hashes.join('')))) invalid()
      sealed = true
    } else {
      const hash = line.slice(0, 64)
      const json = line.slice(65)
      if (
        hashes.length === maxFrames ||
        !digestPattern.test(hash) ||
        line[64] !== ' ' ||
        (await digest(json)) !== hash
      )
        invalid()
      try {
        addFrame(pack, JSON.parse(json))
      } catch {
        invalid()
      }
      hashes.push(hash)
    }
  }
  if (!sealed) invalid()
  return pack
}
