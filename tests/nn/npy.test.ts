/**
 * npy.test.ts — regression test for the hand-written raw `.npy` writer.
 *
 * Locks the contract that `numpy.load` (and the Python trainer) depend on:
 * the stored `hlen` MUST include the 64-byte-alignment padding, so a reader
 * lands exactly at the data. A bug that stores only the unpadded dict length
 * (off by up to 48 bytes) makes numpy.shape resolution fail.
 */
import { describe, it, expect } from 'bun:test'
import { writeNpy } from '../../src/nn/npy'
import { readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

function headerLenOf(buf: Buffer): number {
  // v1 header length is uint16 LE at offset 8; v2 is uint32 LE.
  if (buf[6] === 1) return buf.readUInt16LE(8)
  return buf.readUInt32LE(8)
}

describe('npy.ts writer', () => {
  it('produces a numpy-aligned header whose hlen includes padding', () => {
    const shape = [3, 14, 26, 26]
    const data = new Uint8Array(3 * 14 * 26 * 26)
    for (let i = 0; i < data.length; i++) data[i] = i & 0xff
    const p = join(tmpdir(), `npy-test-${Date.now()}.npy`)
    writeNpy(p, data, shape, 'u1')
    const buf = readFileSync(p)

    expect(buf.slice(0, 6).toString('latin1')).toBe('\x93NUMPY')
    const hlen = headerLenOf(buf)
    const headerDict = buf.slice(10, 10 + hlen).toString('latin1')
    expect(headerDict.startsWith("{'descr': '<u1'")).toBe(true)
    expect(headerDict.includes("'fortran_order': False")).toBe(true)
    expect(headerDict).toContain(`'shape': (${shape.join(', ')})`)

    const dataStart = 10 + hlen
    // data must start exactly at 10+hlen and be exactly data.length bytes
    expect(buf.length - dataStart).toBe(data.length)
    expect(buf[dataStart]).toBe(data[0])
    expect(buf[dataStart + data.length - 1]).toBe(data[data.length - 1])

    // numpy convention: total prefix (10 + hlen) is 64-byte aligned
    expect((10 + hlen) % 64).toBe(0)

    // hlen MUST equal dict length + pad (not just dict length)
    const dictLen = headerDict.length
    const pad = (64 - ((10 + dictLen) % 64)) % 64
    expect(hlen).toBe(dictLen + pad)
  })

  it('round-trips a float32 scalar shard with correct byte length', () => {
    const shape = [5, 24]
    const data = new Float32Array(5 * 24)
    for (let i = 0; i < data.length; i++) data[i] = i * 0.5
    const p = join(tmpdir(), `npy-test-f4-${Date.now()}.npy`)
    writeNpy(p, data, shape, 'f4')
    const buf = readFileSync(p)
    expect(buf.slice(0, 6).toString('latin1')).toBe('\x93NUMPY')
    const hlen = headerLenOf(buf)
    const dataStart = 10 + hlen
    expect(buf.length - dataStart).toBe(data.length * 4)
  })
})
