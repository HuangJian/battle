import { describe, expect, it } from 'bun:test'
import { mkdtempSync, readFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { writeGoalShard, type Step as GoalStep } from '../tools/sim/export-goal-rollout'
import { writeIntentShard, type Step as IntentStep } from '../tools/sim/export-intent-rollout'
import { writeCfShard, type CfGameResult } from '../tools/sim/export-counterfactual-goals'
import { flushShard, type Accumulator } from '../tools/replay/export-observations'
import { shardSampleStrides } from '../tools/sim/student-accuracy'
import { OBS_CHANNELS, BOARD, SCALAR_DIM } from '../src/nn/obs-encoder'
import { GOAL_INJECT_DIM } from '../src/nn/goal-inject'

/**
 * 其余 shard writer 行宽护栏（2026-09-14 x2-start it1 全灭事故的同类残留）。
 *
 * 背景：与 `export-rl-rollout` 同因 —— obs-encoder 升 v3（16 通道 / 30 标量）
 * 后，goal / intent / counterfactual / observations 四个 writer 仍按 v2 字面量
 * `14/19` 分配 obs/scalars 缓冲，`TypedArray.set` 在终局行越界抛 `RangeError`。
 * RL writer 已由 `tests/export-rl-rollout-obs-shape.test.ts` 覆盖；这里锁剩下四家：
 *   1. 编码器行宽（16ch/30 标量）的输入能完整落盘；
 *   2. 落盘 obs.npy/scalars.npy 形状 == 编码器常量。
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

function v3obs(seed: number): Uint8Array {
  const o = new Uint8Array(OBS_CHANNELS * BOARD * BOARD)
  o.fill(seed % 251)
  return o
}

function v3scalars(seed: number): Float32Array {
  const s = new Float32Array(SCALAR_DIM)
  s.fill(seed + 0.25)
  return s
}

describe('shard writer 行宽（v3 编码器行）', () => {
  it('writeGoalShard: 落盘形状 == 编码器常量', () => {
    const dir = mkdtempSync(join(tmpdir(), 'x2-goal-'))
    const steps: GoalStep[] = [0, 1].map((i) => ({
      obs: v3obs(i + 1),
      scalars: v3scalars(i + 1),
      inject: new Float32Array(GOAL_INJECT_DIM).fill(0.5),
      a: i,
      lp: -0.5,
      value: 0.1,
      reward: 0.2,
      done: i,
      mask: new Uint8Array([1, 1, 1, 0]),
      dt: 10,
      engage: 1,
    }))
    expect(() => writeGoalShard(dir, { steps, n: 2 }, {})).not.toThrow()
    expect(npyShape(join(dir, 'obs.npy'))).toEqual([2, OBS_CHANNELS, BOARD, BOARD])
    expect(npyShape(join(dir, 'scalars.npy'))).toEqual([2, SCALAR_DIM])
  })

  it('writeIntentShard: 落盘形状 == 编码器常量', () => {
    const dir = mkdtempSync(join(tmpdir(), 'x2-intent-'))
    const steps: IntentStep[] = [0, 1].map((i) => ({
      obs: v3obs(i + 3),
      scalars: v3scalars(i + 3),
      inject: new Float32Array(9).fill(0.5),
      a: i,
      lp: -0.5,
      value: 0.1,
      reward: 0.2,
      done: i,
      dt: 10,
    }))
    expect(() => writeIntentShard(dir, { steps, n: 2 }, {})).not.toThrow()
    expect(npyShape(join(dir, 'obs.npy'))).toEqual([2, OBS_CHANNELS, BOARD, BOARD])
    expect(npyShape(join(dir, 'scalars.npy'))).toEqual([2, SCALAR_DIM])
  })

  it('writeCfShard: 落盘形状 == 编码器常量', () => {
    const dir = mkdtempSync(join(tmpdir(), 'x2-cf-'))
    const res: CfGameResult = {
      decisions: [0, 1, 2].map((i) => ({
        tick: i * 10,
        candidates: [i],
        srcs: [0],
        ks: [1],
        scoresW: [[0.5]],
        engageW: [1],
      })),
      obs: [v3obs(7), v3obs(8), v3obs(9)],
      scalars: [v3scalars(7), v3scalars(8), v3scalars(9)],
      injects: [0, 1, 2].map(() => new Float32Array(GOAL_INJECT_DIM).fill(0.5)),
      windows: [1],
      outcome: 'timeout',
      ticks: 30,
      truncated: 0,
      totalCandidates: 3,
    }
    expect(() => writeCfShard(dir, res, 4, {})).not.toThrow()
    expect(existsSync(join(dir, 'obs.npy'))).toBe(true)
    expect(npyShape(join(dir, 'obs.npy'))).toEqual([3, OBS_CHANNELS, BOARD, BOARD])
    expect(npyShape(join(dir, 'scalars.npy'))).toEqual([3, SCALAR_DIM])
  })

  it('flushShard(observations): 落盘形状 == 编码器常量', () => {
    const dir = mkdtempSync(join(tmpdir(), 'x2-obs-'))
    const acc: Accumulator = {
      obs: [v3obs(11), v3obs(12)],
      scalars: [v3scalars(11), v3scalars(12)],
      actions: [1, 0, 2, 1],
      masks: Array.from({ length: 2 * 7 }, () => 1),
      conditions: [0, 0],
      rewards: [0.1, 0.2],
      nTurn: 0,
      nFire: 0,
      nItem: 0,
      nItemEvents: 0,
      nSub: 0,
      nFilteredItem: 0,
      nSamples: 2,
    }
    expect(() => flushShard(acc, dir, 's', { conditionBreakdown: {} })).not.toThrow()
    expect(npyShape(join(dir, 'obs.npy'))).toEqual([2, OBS_CHANNELS, BOARD, BOARD])
    expect(npyShape(join(dir, 'scalars.npy'))).toEqual([2, SCALAR_DIM])
  })
})

describe('student-accuracy 读 shard 步长', () => {
  it('步长从 npy 形状派生：v3 与 legacy v2 均正确，非法形状响亮拒绝', () => {
    // v3（当前编码器）
    expect(shardSampleStrides([5, 16, 26, 26], [5, 30])).toEqual({
      obsStride: 16 * 26 * 26,
      scStride: 30,
    })
    // legacy v2 shard 照读（旧语料不强制重导）
    expect(shardSampleStrides([5, 14, 26, 26], [5, 19])).toEqual({
      obsStride: 14 * 26 * 26,
      scStride: 19,
    })
    // 非法形状：rank 错 / 行数对不上，一律抛（禁静默错读）
    expect(() => shardSampleStrides([5, 16, 26], [5, 30])).toThrow()
    expect(() => shardSampleStrides([5, 16, 26, 26], [4, 30])).toThrow()
    expect(() => shardSampleStrides([5, 0, 26, 26], [5, 30])).toThrow()
  })
})
