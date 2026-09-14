import { describe, expect, it } from 'bun:test'
import { mkdtempSync, readFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { writeRlShard, METRICS_DIM, MASK_DIM, type ShardData } from '../tools/sim/export-rl-rollout'
import { OBS_CHANNELS, BOARD, SCALAR_DIM } from '../src/nn/obs-encoder'

/**
 * obs/scalars 行宽护栏（2026-09-14 x2-start it1 全灭回归）。
 *
 * 背景：obs-encoder 升到 v3（16 通道 / 30 标量）时，只改了编码器，
 * `writeRlShard` 仍按 v2 字面量分配 `[N,14,26,26]` / `[N,19]` ——
 * `obs.set` 在**每一局**终局行越界抛 `RangeError`，整条 RL 采集腿零产出。
 * 与 2026-09-12 的 metrics 行宽事故（本目录 metrics 测试）同类：
 * `bun run typecheck` 看不见（类型无长度），只能由行宽断言覆盖。
 *
 * 这里锁两件事：
 *   1. v3 编码器行宽的 shard 能完整落盘（行内容逐字节回读一致）；
 *   2. 落盘 npy 形状 == 编码器常量（加/减通道必须同步 writer，禁止字面量）。
 */

function npyShape(path: string): number[] {
  const buf = readFileSync(path)
  const hlen = buf.readUInt16LE(8)
  const header = buf.subarray(10, 10 + hlen).toString('latin1')
  const m = header.match(/'shape':\s*\(([^)]*)\)/)
  if (!m) throw new Error(`no shape in npy header: ${path}`)
  return m[1]
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
    .map(Number)
}

function makeShard(n: number): ShardData {
  const obs: Uint8Array[] = []
  const scalars: Float32Array[] = []
  const metrics: number[][] = []
  for (let i = 0; i < n; i++) {
    const o = new Uint8Array(OBS_CHANNELS * BOARD * BOARD)
    o.fill((i * 37 + 11) % 251)
    o[0] = i + 1 // 行标记：回读校验用
    obs.push(o)
    const s = new Float32Array(SCALAR_DIM)
    s.fill(i + 0.5)
    scalars.push(s)
    metrics.push(Array.from({ length: METRICS_DIM }, () => i))
  }
  metrics.push(Array.from({ length: METRICS_DIM }, () => 9))
  return {
    obs,
    scalars,
    aMove: Array.from({ length: n }, () => 1),
    aFire: Array.from({ length: n }, () => 0),
    lpMove: Array.from({ length: n }, () => -0.5),
    lpFire: Array.from({ length: n }, () => -0.25),
    value: Array.from({ length: n }, () => 0.1),
    metrics,
    done: [...Array.from({ length: n - 1 }, () => 0), 1],
    mask: Array.from({ length: n * MASK_DIM }, () => 1),
    n,
  }
}

describe('export-rl-rollout obs/scalars 行宽', () => {
  it('v3 编码器行宽落盘形状 == 编码器常量且内容回读一致', () => {
    const dir = mkdtempSync(join(tmpdir(), 'x2-shard-'))
    const d = makeShard(3)
    expect(() => writeRlShard(dir, d, { schemaMajor: 3 })).not.toThrow()
    for (const f of [
      'obs.npy',
      'scalars.npy',
      'a_move.npy',
      'a_fire.npy',
      'lp_move.npy',
      'lp_fire.npy',
      'value.npy',
      'metrics.npy',
      'done.npy',
      'mask.npy',
      'manifest.json',
    ])
      expect(existsSync(join(dir, f))).toBe(true)
    expect(npyShape(join(dir, 'obs.npy'))).toEqual([3, OBS_CHANNELS, BOARD, BOARD])
    expect(npyShape(join(dir, 'scalars.npy'))).toEqual([3, SCALAR_DIM])
    expect(npyShape(join(dir, 'metrics.npy'))).toEqual([4, METRICS_DIM])
    expect(npyShape(join(dir, 'mask.npy'))).toEqual([3, MASK_DIM])
    // 行内容回读：obs 第 i 行首字节 == i+1（stride 错一位这里即暴露）
    const buf = readFileSync(join(dir, 'obs.npy'))
    const hlen = buf.readUInt16LE(8)
    const raw = buf.subarray(10 + hlen)
    const stride = OBS_CHANNELS * BOARD * BOARD
    expect(raw.length).toBe(3 * stride)
    for (let i = 0; i < 3; i++) expect(raw[i * stride]).toBe(i + 1)
  })
})
