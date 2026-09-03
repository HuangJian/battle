import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { buildModelFromText, type ModelLike } from '../../src/nn/infer'

/**
 * 轴 2（torch↔TS）parity golden —— **per-tick 策略头**（plan/Goal-Space-Policy-NN §T6 审计 2026-09-03）。
 *
 * 覆盖缺口：goal-infer / intent-infer 的 golden 校验的是 StudentNet 主干 + 各自专用头
 * （goal_conv/engage、intent/enemy/anchor），**从不触碰 PPOStudent 的 move_head / fire_head
 * / 128 宽 value_head**——而这正是 export-rl-rollout.ts（s5-open20 活路径，kind='student',
 * h64/d8, head_hidden=128 + value_head）在采样的三头。缺它意味着任一侧改这些头或伤害
 * 主干时，TS 运行时可能静默漂移而测试全绿。
 *
 * golden 由 nn-training/models/student.py --golden 生成（PPOStudent + value_head，固定
 * seed、固定随机输入写入 obs/scalars、三头期望 logits + 全权重）。两个规格：
 *   - student-golden.json        h=16/d=2  瘦身（TS 手写循环路径，主干不触发 wasm）
 *   - student-golden-wasm.json   h=64/d=8  生产（与 s5-open20 权重同构，主干走 conv-wasm）
 * TS 端 buildModelFromText（arch.kind='student'，与 export-rl-rollout 同一构建入口）→
 * forward() → move(5)/fire(2)/value(1) 与 golden 对比。
 *
 * 容差口径：沿用 intent golden ≤1e-4 先例（TS 手写循环 vs torch 向量化内核的累加次序差；
 * wasm 卷积段 §312 实测 pooled max|Δ|≈4.8e-6，远在容差内）。
 */
interface StudentGolden {
  h: number
  d: number
  head_hidden: number
  format: string
  version: number
  seed: number
  obs: number[]
  scalars: number[]
  moveLogits: number[]
  fireLogits: number[]
  valueLogits: number[]
  params: Record<string, unknown>
}

const SLIM = JSON.parse(
  readFileSync(join(import.meta.dir, '..', 'fixtures', 'student-golden.json'), 'utf8'),
) as StudentGolden
const PROD = JSON.parse(
  readFileSync(join(import.meta.dir, '..', 'fixtures', 'student-golden-wasm.json'), 'utf8'),
) as StudentGolden

function maxAbsDiff(a: number[], b: Float32Array): number {
  let m = 0
  for (let i = 0; i < a.length; i++) m = Math.max(m, Math.abs(a[i] - b[i]))
  return m
}

function runCase(g: StudentGolden): { move: number; fire: number; value: number } {
  const model = buildModelFromText(
    JSON.stringify({
      arch: { kind: 'student', h: g.h, d: g.d },
      params: g.params,
    }),
  ) as ModelLike & { valueOut: Float32Array }
  const obs = new Uint8Array(g.obs)
  const scalars = new Float32Array(g.scalars)
  model.forward(obs, scalars)
  return {
    move: maxAbsDiff(g.moveLogits, model.moveLogits),
    fire: maxAbsDiff(g.fireLogits, model.fireLogits),
    value: maxAbsDiff(g.valueLogits, model.valueOut),
  }
}

describe('StudentNet TS/Py forward consistency — per-tick RL policy heads (axis 2)', () => {
  it('golden 元信息：format/seed/arch 溯源（两个规格）', () => {
    for (const g of [SLIM, PROD]) {
      expect(g.format).toBe('student-golden')
      expect(g.version).toBe(1)
      expect(g.head_hidden).toBe(128)
      // 三头权重必须都在（断言活路径 PPOStudent 结构）
      expect(g.params['move_head.weight']).toBeDefined()
      expect(g.params['fire_head.weight']).toBeDefined()
      expect(g.params['value_head.weight']).toBeDefined()
    }
    expect(SLIM.h).toBe(16)
    expect(SLIM.d).toBe(2)
    expect(PROD.h).toBe(64)
    expect(PROD.d).toBe(8)
    // 生产规格与 s5-open20 活权重同参数数（42 = stem+8 blocks+fc+3 heads）
    expect(Object.keys(PROD.params).length).toBe(42)
  })

  it('瘦身 h=16/d=2（TS 手写循环路径）：move/fire/value 三头 ≤1e-4', () => {
    const d = runCase(SLIM)
    expect(d.move, `move maxΔ=${d.move}`).toBeLessThanOrEqual(1e-4)
    expect(d.fire, `fire maxΔ=${d.fire}`).toBeLessThanOrEqual(1e-4)
    expect(d.value, `value maxΔ=${d.value}`).toBeLessThanOrEqual(1e-4)
  })

  it('生产 h=64/d=8（conv-wasm 路径，与 s5-open20 同构）：move/fire/value 三头 ≤1e-4', () => {
    const d = runCase(PROD)
    expect(d.move, `move maxΔ=${d.move}`).toBeLessThanOrEqual(1e-4)
    expect(d.fire, `fire maxΔ=${d.fire}`).toBeLessThanOrEqual(1e-4)
    expect(d.value, `value maxΔ=${d.value}`).toBeLessThanOrEqual(1e-4)
  })
})
