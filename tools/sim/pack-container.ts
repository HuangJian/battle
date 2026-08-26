/**
 * pack-container.ts — 分布式结果容器 v2（BCV2）写入端（plan/distributed-rollout.md v3.6）。
 *
 * 为什么不再是 v1 的 gzip(JSON {manifest, files:{name:base64}})：
 *   v1 的 base64 编码、MB 级 JSON 拼装与 gzip 全部发生在 agent 主线程——workers 开多后
 *   各局完成体在这条串行打包点上排队，成为整节点吞吐瓶颈（生产实测 8 workers 时每个
 *   rollout 子进程仅 ~50% CPU）。v2 把打包下沉到 exporter 子进程（--pack <path>），
 *   与仿真并行执行；同时去掉 base64（线体体积 -25%）与 JSON 内嵌大块二进制。
 *
 * 格式（TS 写 / Python 读，双语契约，Python 端 nn-training/dist_common.unpack_container）：
 *
 *   container := gzip(frame)
 *   frame     := magic u32 BE = 0x42435632 ('B''C''V''2')
 *              | headerLen u32 BE | headerJSON utf8
 *              | entry*                                  （顺序 = header.files[] 顺序）
 *   entry     := nameLen u16 BE | name utf8 | dataLen u64 BE | data bytes
 *   headerJSON := { fmt:'bcv2', manifest:object, files:[{name:string,len:number}] }
 *
 * 兼容性：coordinator 解码端按 magic 自动识别 v2/v1——新 trainer + 旧 agent（v1 包）
 * 直接可用；反向（旧 trainer + 新 agent）不兼容，部署时两侧同 commit 即可（既有惯例）。
 */
import { gzipSync, gunzipSync } from 'node:zlib'

export const PACK_MAGIC = 0x42435632

export interface PackEntry {
  name: string
  data: Buffer
}

export interface PackHeader {
  fmt: 'bcv2'
  manifest: Record<string, unknown>
  files: Array<{ name: string; len: number }>
}

/** 解包 BCV2 容器（m1-eval --dist-nodes 消费端；与 Python dist_common.unpack_container 对等）。 */
export function unpackContainer(buf: Buffer): {
  manifest: Record<string, unknown>
  entries: Map<string, Buffer>
} {
  const frame = gunzipSync(buf)
  if (frame.length < 8) throw new Error('pack too small')
  const dv = new DataView(frame.buffer, frame.byteOffset, frame.byteLength)
  const magic = dv.getUint32(0, false)
  if (magic !== PACK_MAGIC) {
    // 兼容 v1：gzip(JSON {manifest, files:{name:base64}})。
    const v1 = JSON.parse(frame.toString('utf8')) as {
      manifest?: Record<string, unknown>
      files?: Record<string, string>
    }
    if (!v1.manifest) throw new Error('unrecognized pack magic')
    const entries = new Map<string, Buffer>()
    for (const [k, b64] of Object.entries(v1.files ?? {}))
      entries.set(k, Buffer.from(b64, 'base64'))
    return { manifest: v1.manifest, entries }
  }
  const headerLen = dv.getUint32(4, false)
  const headerJson = frame.toString('utf8', 8, 8 + headerLen)
  const header = JSON.parse(headerJson) as PackHeader
  let off = 8 + headerLen
  const entries = new Map<string, Buffer>()
  for (let fi = 0; fi < header.files.length; fi++) {
    const nameLen = dv.getUint16(off, false)
    const name = frame.toString('utf8', off + 2, off + 2 + nameLen)
    const dataLen = Number(dv.getBigUint64(off + 2 + nameLen, false))
    entries.set(
      name,
      Buffer.from(frame.subarray(off + 2 + nameLen + 8, off + 2 + nameLen + 8 + dataLen)),
    )
    off += 2 + nameLen + 8 + dataLen
  }
  return { manifest: header.manifest, entries }
}

/** 组装 BCV2 容器：单次分配 frame 缓冲 → gzip。manifest/entries 均为纯数据。 */
export function buildPack(manifest: Record<string, unknown>, entries: PackEntry[]): Buffer {
  const header = Buffer.from(
    JSON.stringify({
      fmt: 'bcv2',
      manifest,
      files: entries.map((e) => ({ name: e.name, len: e.data.length })),
    }),
    'utf8',
  )
  let frameLen = 8 + header.length
  const nameBufs: Buffer[] = []
  for (const e of entries) {
    const nameBuf = Buffer.from(e.name, 'utf8')
    if (nameBuf.length > 0xffff) throw new Error(`entry name too long: ${e.name}`)
    if (e.data.length > 0xffffffffff) throw new Error(`entry too large: ${e.name}`)
    nameBufs.push(nameBuf)
    frameLen += 2 + nameBuf.length + 8 + e.data.length
  }
  const frame = Buffer.alloc(frameLen)
  const dv = new DataView(frame.buffer, frame.byteOffset, frame.byteLength)
  let off = 0
  dv.setUint32(off, PACK_MAGIC, false)
  dv.setUint32(off + 4, header.length, false)
  off += 8
  header.copy(frame, off)
  off += header.length
  for (let i = 0; i < entries.length; i++) {
    const nameBuf = nameBufs[i]
    const data = entries[i].data
    dv.setUint16(off, nameBuf.length, false)
    nameBuf.copy(frame, off + 2)
    dv.setBigUint64(off + 2 + nameBuf.length, BigInt(data.length), false)
    data.copy(frame, off + 2 + nameBuf.length + 8)
    off += 2 + nameBuf.length + 8 + data.length
  }
  return gzipSync(frame)
}
