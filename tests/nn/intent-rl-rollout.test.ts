import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { runOne, sampleCat } from '../../tools/sim/export-intent-rollout'
import { STAGES } from '../../src/config/stages'
import {
  INTENT_REWARD,
  SWITCH_COST,
  GAMMA_TICK,
  shapingStep,
  shapingMult,
  INTENT_SHAPING_MULT,
} from '../../src/nn/intent-rl-reward'
import { INTENT_IDS } from '../../src/ai/intent/vocab'

/**
 * M8 rollout collector 确定性 + 语义测试（export-intent-rollout.ts）。
 *
 * 确定性（AGENTS §2.3 / §7）：同 (stage, seed) 双跑 → 意图步 shard 逐字节一致
 * （obs/scalars/inject/a/lp/value/reward/done/mask/dt 全字段）。
 * 语义：ESCAPE 永不被采（死类掩码）；dt ≡ replan 窗口；reward 有界（终局+塑形对账）；
 * 意图分布跨多类（B′ 冷启动可学）；Σreward 终局对账 ≈ 终局值 + 有界塑形项。
 */
// 固定 golden 权重（h16/d2 瘦身，P3-4 锁定 TS/Py 一致）——与训练产物
// (tmp/intent-rl/weights.json) **解耦**：训练状态（如 M8 期 HUNT 坍缩）不得让
// collector 单测 flaky。
const GOLDEN = JSON.parse(
  readFileSync(join(import.meta.dir, '..', 'fixtures', 'intent-golden.json'), 'utf8'),
) as { h: number; d: number; params: Record<string, unknown> }
const WEIGHTS = JSON.stringify({
  arch: { kind: 'intent', h: GOLDEN.h, d: GOLDEN.d },
  params: GOLDEN.params,
})

function serialize(r: ReturnType<typeof runOne>): string {
  const s = r.shard.steps
  const flat = s.map((x) => [
    x.a,
    x.lp.toFixed(6),
    x.value.toFixed(6),
    x.reward.toFixed(6),
    x.done,
    x.dt,
    [...x.inject].map((v) => v.toFixed(4)).join(','),
    [...x.obs].join(''),
    [...x.scalars].map((v) => v.toFixed(4)).join(','),
  ])
  return JSON.stringify({ outcome: r.outcome, ticks: r.ticks, wins: r.win, kills: r.kills, flat })
}

describe('intent rollout collector (M8)', () => {
  it('同 (stage,seed) 双跑逐字段一致（确定性）', () => {
    const a = runOne(0, STAGES[0], 12345, 'hard', 2000, WEIGHTS, 30)
    const b = runOne(0, STAGES[0], 12345, 'hard', 2000, WEIGHTS, 30)
    expect(serialize(a)).toBe(serialize(b))
    expect(a.shard.n).toBeGreaterThan(0)
    expect(a.shard.steps[0].dt).toBeGreaterThan(0)
  })

  it('不同 seed 产生不同采样轨迹（采样 RNG 生效）', () => {
    const a = runOne(0, STAGES[0], 111, 'hard', 1500, WEIGHTS, 30)
    const b = runOne(0, STAGES[0], 999, 'hard', 1500, WEIGHTS, 30)
    expect(serialize(a)).not.toBe(serialize(b))
  })

  it('ESCAPE 永不被采（死类掩码）且注入 one-hot 合法', () => {
    const r = runOne(0, STAGES[0], 7, 'hard', 1500, WEIGHTS, 30)
    for (let i = 0; i < r.shard.steps.length; i++) {
      const s = r.shard.steps[i]
      expect(s.a).not.toBe(7) // ESCAPE idx 7
      expect(s.a).toBeGreaterThanOrEqual(0)
      expect(s.a).toBeLessThan(7)
      // inject: one-hot(8) + duration(1)。首窗 zero-vector（预注册 #11 prev=-1），
      // 其后恰好一个 1。
      const ohSum = s.inject.slice(0, 8).reduce((a, b) => a + b, 0)
      expect(ohSum).toBe(i === 0 ? 0 : 1)
      expect(s.inject[8]).toBeGreaterThanOrEqual(0)
      expect(s.inject[8]).toBeLessThanOrEqual(1)
    }
  })

  it('dt 与 replan cadence 一致（均为 30 的倍数；末窗可短于 30）', () => {
    const r = runOne(0, STAGES[0], 42, 'hard', 2000, WEIGHTS, 30)
    expect(r.shard.n).toBeGreaterThanOrEqual(2)
    for (let i = 0; i < r.shard.steps.length - 1; i++) {
      // 玩家出生/阵亡重生的窗口会跳过 replan 帧 → dt 是 30 的倍数（可 >30）。
      expect(r.shard.steps[i].dt % 30).toBe(0)
    }
    expect(r.shard.steps[r.shard.steps.length - 1].dt).toBeLessThanOrEqual(30)
  })

  it('sampleCat 掩码采样跨多类且 ESCAPE 恒屏蔽', () => {
    // 直接测采样器（不依赖可玩权重）：近均匀 logits → 采样分布跨 ≥3 类；
    // ESCAPE（idx7）被掩码恒 -inf → 永不出现。
    const logits = new Float32Array([0.1, -0.1, 0.2, -0.2, 0.05, 0.15, 0, -1])
    const mask = [1, 1, 1, 1, 1, 1, 1, 0]
    let s = 12345
    const rng = () => {
      s = (s * 1103515245 + 12345) & 0x7fffffff
      return s / 0x7fffffff
    }
    const seen = new Set<number>()
    for (let i = 0; i < 400; i++) {
      const { idx } = sampleCat(logits, mask, rng)
      seen.add(idx)
      expect(idx).not.toBe(7) // ESCAPE 死类掩码
    }
    expect(seen.size).toBeGreaterThanOrEqual(3)
  })

  it('potential shaping telescoping（P1-8k3）：闭合循环累积恒 0、总量有界', () => {
    // 闭合循环：Φ 回到初始值 → Σ shaping ≡ 0（防 farming）。
    const seq = [-0.9, -0.4, -0.1, -0.7, -0.3, -0.9]
    let sum = 0
    for (let i = 0; i < seq.length - 1; i++) sum += shapingStep(seq[i], seq[i + 1])
    expect(Math.abs(sum)).toBeLessThanOrEqual(1e-9)
    // 任意序列：Σ shaping = Φ_T − Φ_0 ∈ [−1, 1]（Φ∈[−1,0]）。
    const mono = [-1, -0.5, 0, -0.5, -1]
    let sum2 = 0
    for (let i = 0; i < mono.length - 1; i++) sum2 += shapingStep(mono[i], mono[i + 1])
    expect(Math.abs(sum2)).toBeLessThanOrEqual(1.0 + 1e-9)
  })

  it('reward 有界且终局对账（Σr = 终局 + 有界塑形项 + 密集分量 + 切换成本）', () => {
    const r = runOne(0, STAGES[0], 5, 'hard', 2000, WEIGHTS, 30)
    const sum = r.shard.steps.reduce((a, s) => a + s.reward, 0)
    const term =
      r.outcome === 'stage_clear'
        ? INTENT_REWARD.CLEAR_STAGE
        : r.outcome === 'base_destroyed'
          ? INTENT_REWARD.BASE_DESTROYED
          : r.outcome === 'lives_exhausted'
            ? INTENT_REWARD.LIVES_EXHAUSTED
            : INTENT_REWARD.TIMEOUT
    // 密集分量（击杀为主）+ 加权塑形（Σ mult_t·ΔΦ，保守界 ≤ 60）+ 切换成本。
    // 宽松界只抓结构性错误。
    const denseBound = r.kills * INTENT_REWARD.KILL + 60.0 + r.shard.steps.length * SWITCH_COST + 10
    expect(Math.abs(sum - term)).toBeLessThanOrEqual(denseBound)
    // 逐 reward 有界（击杀 4 / 清砖 0.5 / 拾取 2 / 终局 ±50 + 塑形 ±1）。
    for (const s of r.shard.steps) {
      expect(s.reward).toBeGreaterThan(-60)
      expect(s.reward).toBeLessThan(60)
    }
  })

  it('γ 换算前提：γ_step=γ_tick^Δt ∈ (0,1]', () => {
    expect(GAMMA_TICK).toBeGreaterThan(0)
    expect(GAMMA_TICK).toBeLessThan(1)
  })

  it('shaping 意图加权完整覆盖 8 类且防守类放大（2026-08-27 坍缩修复）', () => {
    // INTENT_IDS 顺序与 shapingMult 索引一一对应。
    expect(INTENT_IDS.length).toBe(8)
    for (let i = 0; i < INTENT_IDS.length; i++) {
      const id = INTENT_IDS[i]
      expect(INTENT_SHAPING_MULT[id]).toBe(shapingMult(i))
      expect(shapingMult(i)).toBeGreaterThan(0)
    }
    // 防守类（回防/据守/走廊）放大 > 进攻基线 HUNT。
    expect(shapingMult(INTENT_IDS.indexOf('RETURN_DEFENSE'))).toBeGreaterThan(1.0)
    expect(shapingMult(INTENT_IDS.indexOf('HOLD_LANE'))).toBeGreaterThan(1.0)
    expect(shapingMult(INTENT_IDS.indexOf('HUNT'))).toBe(1.0)
    // 越界索引安全回退。
    expect(shapingMult(99)).toBe(1.0)
    expect(shapingMult(-1)).toBe(1.0)
  })
})
