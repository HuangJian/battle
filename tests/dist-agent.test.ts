import { describe, expect, it } from 'bun:test'
import {
  collectCodeHashEntries,
  computeCodeHashFromFiles,
  packContainer,
  unpackContainer,
  SHARD_FILES,
} from '../tools/agent/sampler-agent'
import { buildPack, PACK_MAGIC } from '../tools/sim/pack-container'

/**
 * BCV2 独立解码器：镜像 nn-training/dist_common.py 的 struct 解析路径重写，
 * 不复用写入端任何内部逻辑（tests/stages.test.ts 的独立重实现惯例）。
 */
function decodeBcv2(buf: Uint8Array): {
  manifest: Record<string, unknown>
  files: Map<string, Uint8Array>
} {
  const frame = Bun.gunzipSync(new Uint8Array(buf))
  const dv = new DataView(frame.buffer, frame.byteOffset, frame.byteLength)
  let off = 0
  const magic = dv.getUint32(off, false)
  off += 4
  expect(magic).toBe(PACK_MAGIC)
  const hlen = dv.getUint32(off, false)
  off += 4
  const header = JSON.parse(new TextDecoder().decode(frame.subarray(off, off + hlen))) as {
    fmt: string
    manifest: Record<string, unknown>
    files: { name: string; len: number }[]
  }
  off += hlen
  expect(header.fmt).toBe('bcv2')
  const files = new Map<string, Uint8Array>()
  for (const spec of header.files) {
    const nlen = dv.getUint16(off, false)
    off += 2
    const name = new TextDecoder().decode(frame.subarray(off, off + nlen))
    off += nlen
    const dlen = Number(dv.getBigUint64(off, false))
    off += 8
    expect(name).toBe(spec.name)
    expect(dlen).toBe(spec.len)
    files.set(name, frame.subarray(off, off + dlen))
    off += dlen
  }
  expect(off).toBe(frame.length)
  return { manifest: header.manifest, files }
}

describe('dist sampler-agent helpers (plan/distributed-rollout.md v3.3)', () => {
  it('container roundtrip preserves manifest + files', () => {
    const report = { stage: 3, seed: 42, wver: 'ab'.repeat(32), games: 1, scoreList: [0.5] }
    const files = Object.fromEntries(
      SHARD_FILES.map((n: string, i: number) => [n, Buffer.from([i, 1, 2, 3]).toString('base64')]),
    )
    const buf = packContainer(report, files)
    const back = unpackContainer(buf)
    expect(back.manifest).toEqual(report)
    expect(back.files).toEqual(files)
  })

  it('codeHash is order-independent and content-sensitive', () => {
    const mk = (relPath: string, content: string) => ({ relPath, content: Buffer.from(content) })
    const a = computeCodeHashFromFiles([mk('b.ts', 'x'), mk('a.ts', 'y')])
    const b = computeCodeHashFromFiles([mk('a.ts', 'y'), mk('b.ts', 'x')])
    expect(a).toBe(b)
    expect(a).toMatch(/^[0-9a-f]{64}$/)
    const c = computeCodeHashFromFiles([mk('b.ts', 'x!'), mk('a.ts', 'y')])
    expect(c).not.toBe(a)
    const d = computeCodeHashFromFiles([mk('b.ts', 'x'), mk('aa.ts', 'y')])
    expect(d).not.toBe(a) // 路径变化也要改变 hash
  })

  it('shard file set matches ppo.py load_shard expectation (10 npy)', () => {
    // v2 schema (AI-No-Items-Warmstart M2) deleted the item head → the shard
    // dropped a_item/lp_item (12 → 10 npy). ppo.py load_shard loads exactly
    // these 10 keys — mirror them here (independent re-statement).
    const expected = [
      'obs.npy',
      'scalars.npy',
      'a_move.npy',
      'a_fire.npy',
      'lp_move.npy',
      'lp_fire.npy',
      'value.npy',
      'reward.npy',
      'done.npy',
      'mask.npy',
    ]
    const names: string[] = [...SHARD_FILES]
    expect(names.length).toBe(expected.length)
    expect([...names].sort()).toEqual([...expected].sort())
  })
})

describe('BCV2 result container v2 (plan/distributed-rollout.md v3.6)', () => {
  const manifest = {
    stage: 7,
    seed: 860001,
    mode: 'rollout',
    wver: 'cd'.repeat(32),
    elapsedSec: 3.2,
    dimLists: { progress: [0.5, 1] },
  }

  it('roundtrip preserves manifest + raw file bytes (12 npy, incl. multi-MB payload)', () => {
    // obs 用 >1MB 伪随机体覆盖 u64 长度路径；其余用定长小体。
    const big = Buffer.alloc(3 * 1024 * 1024 + 7)
    let s = 12345
    for (let i = 0; i < big.length; i++) {
      s = (s * 1103515245 + 12345) & 0x7fffffff
      big[i] = s & 0xff
    }
    const entries = SHARD_FILES.map((n: string, i: number) => ({
      name: n,
      data: i === 0 ? big : Buffer.from([i, 9, 9, 9]),
    }))
    const packed = buildPack(manifest, entries)
    const { manifest: m, files } = decodeBcv2(packed)
    expect(m).toEqual(manifest)
    expect([...files.keys()]).toEqual([...SHARD_FILES])
    expect(Buffer.compare(files.get('obs.npy') as Buffer, big)).toBe(0)
  })

  it('deterministic bytes for identical input (replay-safe)', () => {
    const entries = [{ name: 'value.npy', data: Buffer.from([1, 2, 3]) }]
    expect(Buffer.compare(buildPack(manifest, entries), buildPack(manifest, entries))).toBe(0)
  })

  it('eval case with empty entry list still decodes', () => {
    const packed = buildPack({ ...manifest, mode: 'eval' }, [])
    const { manifest: m, files } = decodeBcv2(packed)
    expect(files.size).toBe(0)
    expect(m.mode).toBe('eval')
  })

  it('v1 legacy helper still decodes its own format (old-agent compat path)', () => {
    const buf = packContainer(
      { stage: 1, seed: 2 },
      { 'value.npy': Buffer.from([7]).toString('base64') },
    )
    const back = unpackContainer(buf)
    expect(back.manifest).toEqual({ stage: 1, seed: 2 })
    expect(back.files['value.npy']).toBe(Buffer.from([7]).toString('base64'))
  })
})

describe('codeHash SSOT manifest (tools/agent/codehash-files.txt)', () => {
  it('collectCodeHashEntries expands dir + file entries with posix relPath', () => {
    const rels = collectCodeHashEntries().map((e) => e.relPath)
    expect(rels.length).toBeGreaterThan(10)
    // 2026-09-01 事故：Python 侧加了这 3 个文件、TS 侧漏同步 → 节点被永久误判 stale。
    for (const need of [
      'tools/agent/restart-guard.ts',
      'src/types.ts',
      'src/game/SimulationCombat.ts',
      'tools/agent/sampler-agent.ts',
    ]) {
      expect(rels).toContain(need)
    }
    expect(rels.some((r) => r.startsWith('src/nn/'))).toBe(true) // 目录条目递归纳入
    expect(rels.every((r) => !r.includes('\\'))).toBe(true) // 全 posix 正斜杠
    expect(new Set(rels).size).toBe(rels.length) // 无重复
  })
})
