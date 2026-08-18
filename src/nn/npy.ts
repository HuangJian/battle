/**
 * npy.ts — hand-written raw `.npy` writer (plan §2.2).
 *
 * No external dependency. Produces STANDARD NumPy 1.0 files that
 * `numpy.load` (Python side) reads byte-for-byte. The Python trainer
 * (`nn-training/npyio.py`) consumes these shards.
 *
 * Layout (must match npyio.read_npy):
 *   magic   \x93NUMPY            (6 bytes)
 *   version 0x01 0x00            (2 bytes)
 *   hdr_len uint16 LE            (2 bytes)
 *   header  dict repr + spaces   (64-byte aligned total prefix)
 *   raw     C-order bytes
 *
 * We use C-order (fortran_order: False) — the plan's "fortran 序" referred to
 * the deterministic layout, which C-order already guarantees for our fixed
 * shapes. numpy.load does not require fortran order.
 */

import { writeFileSync, mkdirSync } from 'fs'
import { dirname } from 'path'

export type NpyDtype = 'u1' | 'i1' | 'f4' | 'f8'

const DESCR: Record<NpyDtype, string> = {
  u1: '<u1',
  i1: '<i1',
  f4: '<f4',
  f8: '<f8',
}

/**
 * Write a raw `.npy` file. `view` is the typed-array view of the data
 * (e.g. Uint8Array for obs, Float32Array for scalars). `shape` is the logical
 * tensor shape (e.g. [N, 14, 26, 26]).
 */
export function writeNpy(
  path: string,
  view: ArrayBufferView,
  shape: number[],
  dtype: NpyDtype,
): void {
  // A 1-D shape like [N] must serialize as "(N,)" — Python parses "(N)" as a
  // bare integer, not a tuple, and numpy.load rejects it. Multi-dim is fine
  // either way, so always add the trailing comma for the single-dim case.
  const shapeStr = shape.length === 1 ? `(${shape[0]},)` : `(${shape.join(', ')})`
  const header = `{'descr': '${DESCR[dtype]}', 'fortran_order': False, 'shape': ${shapeStr}, }`
  const headerBytes = Buffer.from(header, 'latin1')
  // Data offset = 6 (magic) + 2 (version) + 2 (len) + len(header) + pad
  // must be a multiple of 64 (numpy alignment).
  const pad = (64 - ((10 + headerBytes.length) % 64)) % 64
  const magic = Buffer.from([0x93, 0x4e, 0x55, 0x4d, 0x50, 0x59]) // \x93NUMPY
  const version = Buffer.from([0x01, 0x00])
  const lenBuf = Buffer.alloc(2)
  // IMPORTANT: hlen must count the PADDED header (dict + spaces) so a reader
  // lands exactly at the data. numpy.load reads `hlen` bytes then the array;
  // storing only the unpadded dict length would start 48 bytes early and break
  // shape resolution.
  lenBuf.writeUInt16LE(headerBytes.length + pad, 0)
  const padBuf = Buffer.alloc(pad, 0x20) // spaces, like numpy
  const dataBuf = Buffer.from(view.buffer, view.byteOffset, view.byteLength)
  const out = Buffer.concat([magic, version, lenBuf, headerBytes, padBuf, dataBuf])
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, out)
}

/** Per-shard arrays (flat, row-major). */
export interface ShardArrays {
  obs: Uint8Array // N * 14 * 26 * 26
  scalars: Float32Array // N * 24
  actions: Uint8Array // N * 3   [move, fire, item]
  masks: Uint8Array // N * 10  [move5, fire2, item3], 1 = valid
  conditions: Uint8Array // N * 1 (uint8 category)
}

/** Write one replay's decision-tick samples as a shard of npy + manifest. */
export function writeShard(dir: string, a: ShardArrays, manifest: unknown): void {
  mkdirSync(dir, { recursive: true })
  const N = a.conditions.length
  if (N === 0) return
  writeNpy(`${dir}/obs.npy`, a.obs, [N, 14, 26, 26], 'u1')
  writeNpy(`${dir}/scalars.npy`, a.scalars, [N, 24], 'f4')
  writeNpy(`${dir}/actions.npy`, a.actions, [N, 3], 'u1')
  writeNpy(`${dir}/masks.npy`, a.masks, [N, 10], 'u1')
  writeNpy(`${dir}/conditions.npy`, a.conditions, [N], 'u1')
  writeFileSync(`${dir}/manifest.json`, JSON.stringify(manifest, null, 2))
}
