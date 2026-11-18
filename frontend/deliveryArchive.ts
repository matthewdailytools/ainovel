import { deliveryFiles, type ArchivedCompletion } from './workflowArchive'

const encoder = new TextEncoder()
const crcTable = Uint32Array.from({ length: 256 }, (_, byte) => {
  for (let bit = 0; bit < 8; bit++) byte = (byte >>> 1) ^ (byte & 1 ? 0xedb88320 : 0)
  return byte
})

function crc32(bytes: Uint8Array) {
  let crc = 0xffffffff
  for (const byte of bytes) crc = (crc >>> 8) ^ crcTable[(crc ^ byte) & 255]
  return (crc ^ 0xffffffff) >>> 0
}

// Five known UTF-8 files in the ZIP32 stored format. The portable package limit
// bounds all offsets and lengths well below the ZIP64 threshold.
export function createDeliveryZip(record: ArchivedCompletion): Blob {
  const files = deliveryFiles.map((name) => [name, record.materials[name]] as const)
  const contents = [...files, ['manifest.json', JSON.stringify(record.manifest)] as const]
  const localParts: BlobPart[] = []
  const directory: BlobPart[] = []
  let offset = 0
  let directorySize = 0
  for (const [filename, content] of contents) {
    const name = encoder.encode(filename)
    const data = encoder.encode(content)
    const crc = crc32(data)
    const local = new DataView(new ArrayBuffer(30))
    local.setUint32(0, 0x04034b50, true)
    local.setUint16(4, 20, true)
    local.setUint16(6, 0x0800, true)
    local.setUint16(12, 0x0021, true)
    local.setUint32(14, crc, true)
    local.setUint32(18, data.length, true)
    local.setUint32(22, data.length, true)
    local.setUint16(26, name.length, true)
    localParts.push(local.buffer, name, data)

    const header = new DataView(new ArrayBuffer(46))
    header.setUint32(0, 0x02014b50, true)
    header.setUint16(4, 20, true)
    header.setUint16(6, 20, true)
    header.setUint16(8, 0x0800, true)
    header.setUint16(14, 0x0021, true)
    header.setUint32(16, crc, true)
    header.setUint32(20, data.length, true)
    header.setUint32(24, data.length, true)
    header.setUint16(28, name.length, true)
    header.setUint32(42, offset, true)
    directory.push(header.buffer, name)
    directorySize += header.byteLength + name.length
    offset += local.byteLength + name.length + data.length
  }
  const end = new DataView(new ArrayBuffer(22))
  end.setUint32(0, 0x06054b50, true)
  end.setUint16(8, contents.length, true)
  end.setUint16(10, contents.length, true)
  end.setUint32(12, directorySize, true)
  end.setUint32(16, offset, true)
  return new Blob([...localParts, ...directory, end.buffer], { type: 'application/zip' })
}
