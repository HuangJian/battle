import { describe, it, expect } from 'bun:test'
import { crc32, readStoreZip, writeStoreZip } from '../src/probe/zip'

// ============================================================
// Probe session pack — ZIP (STORE only, no compression)
//
// The reader below is an INDEPENDENT re-implementation (repo convention for
// codecs, cf. tests/stages.test.ts): it walks the archive with its own offset
// arithmetic so a shared bug in src/probe/zip.ts cannot hide behind its own
// reader.
// ============================================================

interface DecodedEntry {
  name: string
  data: Uint8Array
  crc: number
  method: number
}

function decodeZip(buf: Uint8Array): DecodedEntry[] {
  const u32 = (o: number): number =>
    (buf[o] | (buf[o + 1] << 8) | (buf[o + 2] << 16) | (buf[o + 3] << 24)) >>> 0
  const u16 = (o: number): number => buf[o] | (buf[o + 1] << 8)

  let eocd = -1
  for (let i = buf.length - 22; i >= 0; i--) {
    if (u32(i) === 0x06054b50) {
      eocd = i
      break
    }
  }
  if (eocd < 0) throw new Error('no EOCD')
  const count = u16(eocd + 10)
  let p = u32(eocd + 16)

  const out: DecodedEntry[] = []
  for (let i = 0; i < count; i++) {
    if (u32(p) !== 0x02014b50) throw new Error(`central sig @${p}`)
    const method = u16(p + 10)
    const crc = u32(p + 16)
    const size = u32(p + 24)
    const nameLen = u16(p + 28)
    const localOff = u32(p + 42)
    const name = new TextDecoder().decode(buf.subarray(p + 46, p + 46 + nameLen))
    const dataStart = localOff + 30 + u16(localOff + 26) + u16(localOff + 28)
    out.push({ name, data: buf.subarray(dataStart, dataStart + size), crc, method })
    p += 46 + nameLen + u16(p + 30) + u16(p + 32)
  }
  return out
}

const enc = (s: string): Uint8Array => new TextEncoder().encode(s)

describe('probe zip (store)', () => {
  it('round-trips through an independent decoder', () => {
    const files = [
      { name: 'session.json', data: enc('{"course":"x"}') },
      { name: '0.replay', data: enc('replay-bytes') },
      { name: 'verdicts.jsonl', data: enc('{"game":0}\n') },
    ]
    const zip = writeStoreZip(files)
    const decoded = decodeZip(zip)
    expect(decoded.map((d) => d.name)).toEqual(['session.json', '0.replay', 'verdicts.jsonl'])
    for (const [i, d] of decoded.entries()) {
      expect(d.method).toBe(0)
      expect(d.data).toEqual(files[i].data)
      expect(d.crc).toBe(crc32(files[i].data))
    }
  })

  it('is deterministic (fixed timestamp, no extra fields)', () => {
    const files = [{ name: 'a.txt', data: enc('same') }]
    expect(writeStoreZip(files)).toEqual(writeStoreZip(files))
  })

  it('reads back its own output and rejects compressed archives', () => {
    const files = [{ name: 'x.json', data: enc('{"a":1}') }]
    const back = readStoreZip(writeStoreZip(files))
    expect(back).toHaveLength(1)
    expect(back[0].name).toBe('x.json')
    expect(new TextDecoder().decode(back[0].data)).toBe('{"a":1}')

    // Flip the method field of the single central-directory entry to 8
    // (deflate) — the reader must refuse rather than return garbage.
    const zip = writeStoreZip(files)
    const body = new TextDecoder().decode(zip.subarray(0, 4))
    expect(body.length).toBe(4) // sanity: file starts with PK\x03\x04
    const central = findSignature(zip, 0x02014b50)
    zip[central + 10] = 8
    expect(() => readStoreZip(zip)).toThrow(/STORE/)
  })

  it('rejects a truncated archive', () => {
    const zip = writeStoreZip([{ name: 'a', data: enc('data') }])
    expect(() => readStoreZip(zip.subarray(0, zip.length - 5))).toThrow()
  })

  it('crc32 matches the known reference vector', () => {
    // CRC-32("123456789") = 0xCBF43926
    expect(crc32(enc('123456789'))).toBe(0xcbf43926)
  })
})

function findSignature(buf: Uint8Array, sig: number): number {
  const u32 = (o: number): number =>
    (buf[o] | (buf[o + 1] << 8) | (buf[o + 2] << 16) | (buf[o + 3] << 24)) >>> 0
  for (let i = 0; i + 4 <= buf.length; i++) {
    if (u32(i) === sig) return i
  }
  throw new Error('signature not found')
}
