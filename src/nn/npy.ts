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

import { closeSync, mkdirSync, openSync, writeFileSync, writeSync } from 'fs'
import { dirname } from 'path'
import { OBS_CHANNELS, BOARD, SCALAR_DIM, SCHEMA_FINGERPRINT } from './obs-encoder'
import { POLICY_EXTRA_DIM } from './policy-extra'

export type NpyDtype = 'u1' | 'u2' | 'i1' | 'f4' | 'f8'

const DESCR: Record<NpyDtype, string> = {
  u1: '<u1',
  u2: '<u2',
  i1: '<i1',
  f4: '<f4',
  f8: '<f8',
}

/**
 * Serialize one array to raw `.npy` bytes (in memory). Same byte layout as
 * `writeNpy` — callers that don't want a temp file (e.g. the distributed BC
 * exporter packing shards straight into a BCV2 container) use this directly.
 */
/**
 * `.npy` 头（magic + version + hlen + 64 字节对齐的 dict）。`npyBytes` 与
 * `writeNpyFile` 共用这一份 —— 两条路径落盘的字节必须完全相同。
 */
export function npyHeader(shape: number[], dtype: NpyDtype): Buffer {
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
  return Buffer.concat([magic, version, lenBuf, headerBytes, padBuf])
}

/**
 * Serialize one array to raw `.npy` bytes (in memory).
 */
export function npyBytes(view: ArrayBufferView, shape: number[], dtype: NpyDtype): Buffer {
  const dataBuf = Buffer.from(view.buffer, view.byteOffset, view.byteLength)
  return Buffer.concat([npyHeader(shape, dtype), dataBuf])
}

/**
 * 直接落盘的 `.npy` 写（头 + 数据两次 `write`，**不做整块拷贝**）。
 *
 * 为什么要有它：`npyBytes` 的 `Buffer.concat` 把整块数据再拷一份。35×20 语料的 obs 是
 * **3.97 GB** —— 白烧一次 3.97 GB 的内存带宽与峰值内存（每 shard 两份 obs）。本函数落盘
 * 字节与 `writeFileSync(path, npyBytes(...))` **逐字节相同**（同一个 `npyHeader`）。
 * 两段 `writeSync` 而非 `writevSync`：后者在部分运行时/平台上不保证全量写，头部半截落盘
 * 比多一次 syscall 贵得多。
 */
export function writeNpyFile(
  path: string,
  view: ArrayBufferView,
  shape: number[],
  dtype: NpyDtype,
): void {
  const header = npyHeader(shape, dtype)
  const data =
    view instanceof Uint8Array
      ? view
      : new Uint8Array(view.buffer, view.byteOffset, view.byteLength)
  const fd = openSync(path, 'w')
  try {
    writeAll(fd, header)
    writeAll(fd, data)
  } finally {
    closeSync(fd)
  }
}

/** 写完一段（`writeSync` 对普通文件是全量写；短写仍循环兜底，绝不静默截断）。 */
function writeAll(fd: number, buf: Uint8Array): void {
  let off = 0
  while (off < buf.length) {
    off += writeSync(fd, buf, off, buf.length - off)
  }
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
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, npyBytes(view, shape, dtype))
}

/** Per-shard arrays (flat, row-major). */
export interface ShardArrays {
  obs: Uint8Array // N * OBS_CHANNELS * 26 * 26
  scalars: Float32Array // N * SCALAR_DIM
  /** v4（plan/policy-spatial-head.plan.md S0-b）：POLICY_EXTRA(9) float32（独立张量）。 */
  extra: Float32Array // N * 9
  actions: Uint8Array // N * 2   [move, fire]  (v2: item head removed)
  masks: Uint8Array // N * 7  [move5, fire2], 1 = valid
  conditions: Uint8Array // N * 1 (uint8 category)
}

/** Write one replay's decision-tick samples as a shard of npy + manifest. */
export function writeShard(dir: string, a: ShardArrays, manifest: unknown): void {
  mkdirSync(dir, { recursive: true })
  const N = a.conditions.length
  if (N === 0) return
  // v3（obs-schema-v3.plan.md v4.0 §3.5-3）：shape 从 schema 常量派生，勿写死
  // ——major bump 曾三次漏改这里（14/19 硬编码）。
  writeNpy(`${dir}/obs.npy`, a.obs, [N, OBS_CHANNELS, BOARD, BOARD], 'u1')
  writeNpy(`${dir}/scalars.npy`, a.scalars, [N, SCALAR_DIM], 'f4')
  // v4：POLICY_EXTRA(9)。
  writeNpy(`${dir}/extra.npy`, a.extra, [N, POLICY_EXTRA_DIM], 'f4')
  writeNpy(`${dir}/actions.npy`, a.actions, [N, 2], 'u1')
  writeNpy(`${dir}/masks.npy`, a.masks, [N, 7], 'u1')
  writeNpy(`${dir}/conditions.npy`, a.conditions, [N], 'u1')
  // schema 自述（obs spec §3.5-3）：指纹随 shard 落盘，加载方一眼确认语料 schema。
  const stamped =
    manifest !== null && typeof manifest === 'object' && !Array.isArray(manifest)
      ? { ...(manifest as Record<string, unknown>), schemaFingerprint: SCHEMA_FINGERPRINT }
      : manifest
  writeFileSync(`${dir}/manifest.json`, JSON.stringify(stamped, null, 2))
}
