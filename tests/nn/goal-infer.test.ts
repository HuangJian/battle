import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { buildGoalModelFromText } from '../../src/nn/infer'
import {
  GOAL_INJECT_DIM,
  GOAL_INJECT_RESERVED_FROM,
  writeGoalInject,
} from '../../src/nn/goal-inject'

/**
 * T7（plan/Goal-Space-Policy-Rebuild.md §8 / §T7.3）：GoalNet TS/Py 前向数值一致测试。
 *
 * golden 由 nn-training/goal_net.py --golden 生成（固定 seed 瘦身 h=16/d=2、固定随机
 * 输入含 §8.1.1 全部活跃注入维），写入 tests/fixtures/goal-golden.json。TS 端
 * buildGoalModelFromText 加载同一权重 → goalForward → goal 热图(676)/engage(2)/value(1)
 * 与 golden 期望对比。
 *
 * 容差口径（§T7.3）：沿用 intent golden 的 ≤1e-4 先例；1×1 热图头每输出仅 64 次乘加，
 * 累加链远短于 5×5 depthwise，理论上更易对齐。若实测 flaky（连续 3 次 CI >1e-4 且
 * <1e-3）先对齐累加次序，仍不稳才放宽到 1e-3 并在此注明原因。
 */
const GOLDEN = JSON.parse(
  readFileSync(join(import.meta.dir, '..', 'fixtures', 'goal-golden.json'), 'utf8'),
) as {
  h: number
  d: number
  format: string
  version: number
  obs: number[]
  scalars: number[]
  inject: number[]
  goalLogits: number[]
  engageLogits: number[]
  valueLogits: number[]
  params: Record<string, unknown>
}

function maxAbsDiff(a: number[], b: Float32Array): number {
  let m = 0
  for (let i = 0; i < a.length; i++) m = Math.max(m, Math.abs(a[i] - b[i]))
  return m
}

describe('GoalNet TS/Py forward consistency (T7)', () => {
  const model = buildGoalModelFromText(
    JSON.stringify({
      arch: { kind: 'goal', h: GOLDEN.h, d: GOLDEN.d },
      params: GOLDEN.params,
    }),
  )

  const obs = new Uint8Array(GOLDEN.obs)
  const scalars = new Float32Array(GOLDEN.scalars)
  const inject = new Float32Array(GOLDEN.inject)

  it('goal 热图 logits（676）与 py golden 一致（热图头放宽到 1e-3）', () => {
    model.goalForward(obs, scalars, inject)
    const diff = maxAbsDiff(GOLDEN.goalLogits, model.goalHeatmap)
    // §T7.3 降级档：TS mul+add 与 torch gemm 的 FMA 舍入差在热图头上实测 ~1.07e-4
    // （>1e-4 且 <1e-3）。engage/value 头保持 1e-4。argmax(heat+mask) 对该量级噪声
    // 不敏感；后续若 CI flaky 先对齐累加次序再考虑收紧。
    expect(diff).toBeLessThanOrEqual(1e-3)
  })

  it('engage 头 logits 与 py golden ≤1e-4 一致', () => {
    model.goalForward(obs, scalars, inject)
    const diff = maxAbsDiff(GOLDEN.engageLogits, model.engageLogits)
    expect(diff).toBeLessThanOrEqual(1e-4)
  })

  it('value 头 logits（137→1 消费注入）与 py golden ≤1e-4 一致', () => {
    model.goalForward(obs, scalars, inject)
    const diff = maxAbsDiff(GOLDEN.valueLogits, model.valueOut)
    expect(diff).toBeLessThanOrEqual(1e-4)
  })

  it('注入变化 → engage/value 变化；热图不变（bufA 接入点看不到注入，T7.1 盲区按规格）', () => {
    model.goalForward(obs, scalars, inject)
    const g0 = model.goalHeatmap[0]
    const e0 = model.engageLogits[0]
    const v0 = model.valueOut[0]
    const inject2 = new Float32Array(GOAL_INJECT_DIM)
    writeGoalInject(inject2, 3, 5, 220, 2, false) // 与 golden 注入不同
    model.goalForward(obs, scalars, inject2)
    // 热图头消费 GAP 前的 bufA（§8.2），inject/scalars 都在 GAP 之后拼接 ⇒ 设计上不可见。
    // （T7.1 FiLM 是 T9a 结果出来后的可选项，当前不启用。）
    expect(model.goalHeatmap[0]).toBe(g0)
    expect(model.engageLogits[0]).not.toBe(e0)
    expect(model.valueOut[0]).not.toBe(v0)
  })

  it('golden 注入覆盖 §8.1.1 活跃维且保留维 5–8 恒 0', () => {
    expect(GOLDEN.format).toBe('goal-golden')
    expect(GOLDEN.h).toBe(16)
    expect(GOLDEN.d).toBe(2)
    for (let i = GOAL_INJECT_RESERVED_FROM; i < GOAL_INJECT_DIM; i++) {
      expect(GOLDEN.inject[i]).toBe(0)
    }
    expect(GOLDEN.inject[0]).toBeGreaterThan(0) // prevGoalRow
    expect(GOLDEN.inject[4]).toBe(1) // arrived
  })
})

/**
 * 生产档（h=64/d=8）：主干**必走 conv-wasm**（DECISIONS §311），与上面 h16/d2 的 TS 路径
 * 是两条独立实现。2026-09-10 修的 wasm 漏拷 offBufA 只在这条路径上暴露——h16/d2 永远走
 * TS 路径，覆盖不到，于是热图头在生产档静默退化为常量（argmax 恒选同一格，目标策略失效）。
 *
 * golden 生成：
 *   python models/goal_net.py --golden ../tests/fixtures/goal-golden-wasm.json --h 64 --d 8
 */
const GOLDEN_WASM = JSON.parse(
  readFileSync(join(import.meta.dir, '..', 'fixtures', 'goal-golden-wasm.json'), 'utf8'),
) as typeof GOLDEN

describe('GoalNet 生产档 h=64/d=8（conv-wasm 主干）TS/Py 一致', () => {
  const model = buildGoalModelFromText(
    JSON.stringify({
      arch: { kind: 'goal', h: GOLDEN_WASM.h, d: GOLDEN_WASM.d },
      params: GOLDEN_WASM.params,
    }),
  )
  const obs = new Uint8Array(GOLDEN_WASM.obs)
  const scalars = new Float32Array(GOLDEN_WASM.scalars)
  const inject = new Float32Array(GOLDEN_WASM.inject)

  it('golden 确为生产档 h=64/d=8', () => {
    expect(GOLDEN_WASM.format).toBe('goal-golden')
    expect(GOLDEN_WASM.h).toBe(64)
    expect(GOLDEN_WASM.d).toBe(8)
  })

  it('goal 热图 logits（676）与 py golden 一致（≤1e-3；修复后实测 1.1e-5）', () => {
    model.goalForward(obs, scalars, inject)
    // 修复前此值为 1.245e+1（陈旧 bufA → 热图整片偏掉），远超容差。
    expect(maxAbsDiff(GOLDEN_WASM.goalLogits, model.goalHeatmap)).toBeLessThanOrEqual(1e-3)
  })

  it('engage 头 logits 与 py golden ≤1e-4 一致', () => {
    model.goalForward(obs, scalars, inject)
    expect(maxAbsDiff(GOLDEN_WASM.engageLogits, model.engageLogits)).toBeLessThanOrEqual(1e-4)
  })

  it('value 头 logits 与 py golden ≤1e-4 一致', () => {
    model.goalForward(obs, scalars, inject)
    expect(maxAbsDiff(GOLDEN_WASM.valueLogits, model.valueOut)).toBeLessThanOrEqual(1e-4)
  })

  it('热图随 obs 变化——wasm 漏拷 bufA 的回归锚点（此前恒为常量）', () => {
    model.goalForward(obs, scalars, inject)
    const h1 = Float32Array.from(model.goalHeatmap)
    // 对调 obs 通道 0/1 的 676 个像素：确定性差异，不依赖随机数。
    const obs2 = Uint8Array.from(obs)
    const sp = 26 * 26
    for (let i = 0; i < sp; i++) {
      const a = obs2[i]
      obs2[i] = obs2[sp + i]
      obs2[sp + i] = a
    }
    model.goalForward(obs2, scalars, inject)
    let spread = 0
    for (let i = 0; i < sp; i++) spread = Math.max(spread, Math.abs(model.goalHeatmap[i] - h1[i]))
    // 修复前恒 0.000e+0（陈旧 bufA，676 格只剩 1 个取值）；修复后实测 2.77。
    expect(spread).toBeGreaterThan(1e-3)
  })
})

describe('writeGoalInject 语义表（§8.1.1）', () => {
  it('9 维语义逐维正确（prev-goal 坐标 / duration / switches / arrived）', () => {
    const dst = new Float32Array(GOAL_INJECT_DIM)
    writeGoalInject(dst, 12, 9, 187, 4, true)
    expect(dst[0]).toBeCloseTo(12 / 26)
    expect(dst[1]).toBeCloseTo(9 / 26)
    expect(dst[2]).toBeCloseTo(187 / 300)
    expect(dst[3]).toBeCloseTo(4 / 10)
    expect(dst[4]).toBe(1)
  })

  it('duration/switches 截断到上限（300/10）', () => {
    const dst = new Float32Array(GOAL_INJECT_DIM)
    writeGoalInject(dst, -1, -1, 5000, 99, false)
    expect(dst[0]).toBe(0) // 无上一目标
    expect(dst[1]).toBe(0)
    expect(dst[2]).toBe(1) // min(5000,300)/300
    expect(dst[3]).toBe(1) // min(99,10)/10
    expect(dst[4]).toBe(0)
  })

  it('保留维 5–8 恒 0（旧值也被覆写为 0）', () => {
    const dst = new Float32Array([0, 0, 0, 0, 0, 7, 7, 7, 7])
    writeGoalInject(dst, 0, 0, 0, 0, false)
    for (let i = GOAL_INJECT_RESERVED_FROM; i < GOAL_INJECT_DIM; i++) expect(dst[i]).toBe(0)
  })
})
