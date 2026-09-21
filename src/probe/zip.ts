// ================================================================
// ZIP — minimal STORE-only reader/writer (no compression, no deps)
//
// The probe session pack is ONE download containing session.json + N
// `<game>.replay` + verdicts.jsonl (human-opening-probe.plan §1.2). Adding a
// zip dependency would violate MANIFEST §14 (new dep ⇒ justify), so this is
// the ~150-line store-only subset we actually need.
//
// Deterministic by construction: no compression, no data descriptors, a FIXED
// DOS timestamp (1980-01-01 00:00) and no extra fields — the same inputs always
// produce the same bytes, so packs can be byte-compared.
//
// Writer is used by the browser (session pack) and the reader by
// `tools/probe/annotate.ts` (input = an extracted/cloned pack) + tests.
// Pure module: no DOM, no fs.
// ================================================================

export interface ZipEntry {
  name: string
  data: Uint8Array
}

/** DOS date for 1980-01-01 (the earliest representable date). */
const DOS_DATE = 0x0021
const DOS_TIME = 0

const LOCAL_SIG = 0x04034b50
const CENTRAL_SIG = 0x02014b50
const EOCD_SIG = 0x06054b50

const CRC_TABLE: Uint32Array = (() => {
  const table = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    table[n] = c >>> 0
  }
  return table
})()

/** Standard CRC-32 (the ZIP polynomial), returned as an unsigned 32-bit int. */
export function crc32(bytes: Uint8Array): number {
  let c = 0xffffffff
  for (let i = 0; i < bytes.length; i++) {
    c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8)
  }
  return (c ^ 0xffffffff) >>> 0
}

function u16(v: number): number[] {
  return [v & 0xff, (v >>> 8) & 0xff]
}

function u32(v: number): number[] {
  return [v & 0xff, (v >>> 8) & 0xff, (v >>> 16) & 0xff, (v >>> 24) & 0xff]
}

const encoder = new TextEncoder()

/** Zip a set of files with the STORE method. Deterministic (fixed timestamp). */
export function writeStoreZip(entries: readonly ZipEntry[]): Uint8Array {
  const chunks: number[][] = []
  const central: number[][] = []
  let offset = 0

  for (const entry of entries) {
    const nameBytes = encoder.encode(entry.name)
    const crc = crc32(entry.data)
    const size = entry.data.length
    if (nameBytes.length > 0xffff) throw new Error(`zip: 文件名过长 ${entry.name}`)
    if (size > 0xffffffff) throw new Error(`zip: 单文件超过 4GiB ${entry.name}`)

    const local = [
      ...u32(LOCAL_SIG),
      ...u16(20), // version needed
      ...u16(0), // flags
      ...u16(0), // method: store
      ...u16(DOS_TIME),
      ...u16(DOS_DATE),
      ...u32(crc),
      ...u32(size), // compressed size == size (store)
      ...u32(size),
      ...u16(nameBytes.length),
      ...u16(0), // extra length
      ...nameBytes,
    ]
    chunks.push(local)
    chunks.push(Array.from(entry.data))

    central.push([
      ...u32(CENTRAL_SIG),
      ...u16(20), // version made by
      ...u16(20), // version needed
      ...u16(0), // flags
      ...u16(0), // method
      ...u16(DOS_TIME),
      ...u16(DOS_DATE),
      ...u32(crc),
      ...u32(size),
      ...u32(size),
      ...u16(nameBytes.length),
      ...u16(0), // extra
      ...u16(0), // comment
      ...u16(0), // disk number start
      ...u16(0), // internal attrs
      ...u32(0), // external attrs
      ...u32(offset), // local header offset
      ...nameBytes,
    ])

    offset += local.length + size
  }

  const centralStart = offset
  let centralSize = 0
  for (const c of central) {
    chunks.push(c)
    centralSize += c.length
  }

  chunks.push([
    ...u32(EOCD_SIG),
    ...u16(0), // this disk
    ...u16(0), // disk with central dir
    ...u16(entries.length),
    ...u16(entries.length),
    ...u32(centralSize),
    ...u32(centralStart),
    ...u16(0), // comment length
  ])

  let total = 0
  for (const c of chunks) total += c.length
  const out = new Uint8Array(total)
  let p = 0
  for (const c of chunks) {
    out.set(c, p)
    p += c.length
  }
  return out
}

function readU16(b: Uint8Array, off: number): number {
  return b[off] | (b[off + 1] << 8)
}

function readU32(b: Uint8Array, off: number): number {
  return (b[off] | (b[off + 1] << 8) | (b[off + 2] << 16) | (b[off + 3] << 24)) >>> 0
}

const decoder = new TextDecoder()

/**
 * Read a STORE-method ZIP. Throws on compressed entries or a malformed archive
 * — loud, never a partial result.
 */
export function readStoreZip(data: Uint8Array): ZipEntry[] {
  // Locate the End Of Central Directory record (scan back over the comment).
  let eocd = -1
  for (let i = data.length - 22; i >= 0 && i >= data.length - 22 - 0xffff; i--) {
    if (readU32(data, i) === EOCD_SIG) {
      eocd = i
      break
    }
  }
  if (eocd < 0) throw new Error('zip: 找不到 EOCD')
  const count = readU16(data, eocd + 10)
  let p = readU32(data, eocd + 16)

  const out: ZipEntry[] = []
  for (let i = 0; i < count; i++) {
    if (readU32(data, p) !== CENTRAL_SIG) throw new Error(`zip: 中央目录第 ${i} 项签名错误`)
    const method = readU16(data, p + 10)
    const size = readU32(data, p + 24)
    const nameLen = readU16(data, p + 28)
    const extraLen = readU16(data, p + 30)
    const commentLen = readU16(data, p + 32)
    const localOff = readU32(data, p + 42)
    const name = decoder.decode(data.subarray(p + 46, p + 46 + nameLen))
    if (method !== 0) throw new Error(`zip: ${name} 不是 STORE 压缩（method=${method}）`)

    if (readU32(data, localOff) !== LOCAL_SIG) throw new Error(`zip: ${name} 本地头签名错误`)
    const localNameLen = readU16(data, localOff + 26)
    const localExtraLen = readU16(data, localOff + 28)
    const dataStart = localOff + 30 + localNameLen + localExtraLen
    const bytes = data.subarray(dataStart, dataStart + size)
    if (bytes.length !== size) throw new Error(`zip: ${name} 数据长度不足`)

    out.push({ name, data: bytes })
    p += 46 + nameLen + extraLen + commentLen
  }
  return out
}
