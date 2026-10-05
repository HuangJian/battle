/**
 * spatial-tower.test.ts — S0′-3：空间塔 torch↔TS 一致性（plan/policy-spatial-head.plan.md §4 Step 1）。
 *
 * golden 由 `nn-training/tools/spatial-tower-parity.py` 生成（固定 seed：
 * Conv2d(64→8,1×1) + ReLU + adaptive_avg_pool2d(4) + Linear(128→112)，随机 bufA）。
 * TS 侧走 `src/nn/spatial-tower.ts` 的循环实现，逐值对账 ≤1e-4（与 goal-infer 容差同规；
 * 数学等价、只有累加次序导致的 ~1e-6 舍入差）。
 *
 * 边界冻结（§3.5）：断言分区边界表 = PyTorch `adaptive_avg_pool2d(4)` 的等价式
 * （相邻块重叠：只有 index 6/19 两格被重复计算；13 不重复）。
 */
import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  SPATIAL_POOL_BOUNDS,
  SPATIAL_TOWER_C,
  SPATIAL_TOWER_FC_OUT,
  SPATIAL_TOWER_FEAT,
  SPATIAL_TOWER_IN_CH,
  SPATIAL_TOWER_POOL,
  spatialTowerForward,
} from '../../src/nn/spatial-tower'

interface GoldenParam {
  shape: number[]
  data: string
}

interface TowerGolden {
  format: string
  version: number
  inCh: number
  board: number
  c: number
  pool: number
  fcOut: number
  projW: GoldenParam
  projB: GoldenParam
  fcW: GoldenParam
  fcB: GoldenParam
  bufA: GoldenParam
  regionVec: number[]
  expected: number[]
}

function b64ToF32(b64: string): Float32Array {
  const bin = atob(b64)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  return new Float32Array(bytes.buffer, bytes.byteOffset, bytes.byteLength >> 2)
}

const GOLDEN = JSON.parse(
  readFileSync(join(import.meta.dir, '..', 'fixtures', 'spatial-tower-golden.json'), 'utf8'),
) as TowerGolden

describe('spatial tower：常量与分区边界（冻结值）', () => {
  it('C=8 / pool=4 / fcOut=112 / 128 维特征', () => {
    expect(SPATIAL_TOWER_C).toBe(8)
    expect(SPATIAL_TOWER_POOL).toBe(4)
    expect(SPATIAL_TOWER_FC_OUT).toBe(112)
    expect(SPATIAL_TOWER_FEAT).toBe(128)
  })

  it('分区边界 = adaptive_avg_pool2d(4) 等价式 [0,7)/[6,13)/[13,20)/[19,26)', () => {
    expect([...SPATIAL_POOL_BOUNDS].map((b) => [...b])).toEqual([
      [0, 7],
      [6, 13],
      [13, 20],
      [19, 26],
    ])
    // 重叠只有 index 6 与 19（7×4 = 28 = 26 + 2）
    const seen = new Set<number>()
    let dup = 0
    for (const [lo, hi] of SPATIAL_POOL_BOUNDS) {
      for (let i = lo; i < hi; i++) {
        if (seen.has(i)) dup++
        seen.add(i)
      }
    }
    expect(dup).toBe(2)
    expect(Math.max(...seen)).toBe(25)
  })
})

describe('spatial tower：torch↔TS 逐值对账（S0′-3 golden）', () => {
  it('golden 元信息：format/架构溯源', () => {
    expect(GOLDEN.format).toBe('spatial-tower-golden')
    expect(GOLDEN.version).toBe(1)
    expect(GOLDEN.inCh).toBe(SPATIAL_TOWER_IN_CH)
    expect(GOLDEN.c).toBe(SPATIAL_TOWER_C)
    expect(GOLDEN.fcOut).toBe(SPATIAL_TOWER_FC_OUT)
    expect(GOLDEN.bufA.shape).toEqual([64, 26, 26])
  })

  it('1×1+ReLU+4×4 池 + FC 全链 ≤1e-4', () => {
    const projW = b64ToF32(GOLDEN.projW.data)
    const projB = b64ToF32(GOLDEN.projB.data)
    const fcW = b64ToF32(GOLDEN.fcW.data)
    const fcB = b64ToF32(GOLDEN.fcB.data)
    const bufA = b64ToF32(GOLDEN.bufA.data)
    const zBuf = new Float32Array(SPATIAL_TOWER_C * 26 * 26)
    const vecBuf = new Float32Array(SPATIAL_TOWER_FEAT)
    const out = new Float32Array(SPATIAL_TOWER_FC_OUT)
    spatialTowerForward(bufA, projW, projB, fcW, fcB, zBuf, vecBuf, out)
    let maxDelta = 0
    for (let i = 0; i < SPATIAL_TOWER_FC_OUT; i++) {
      maxDelta = Math.max(maxDelta, Math.abs(out[i] - GOLDEN.expected[i]))
    }
    expect(maxDelta, `fc maxΔ=${maxDelta}`).toBeLessThanOrEqual(1e-4)
  })
})
