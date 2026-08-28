import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import {
  avgTicksPerGame,
  baseIntegrityOf,
  gamesOf,
  healthVerdict,
  klEff,
  metricsOf,
  type IterEvent,
} from '../../tools/diag/rl-hourly-inspect'
import { runOne } from '../../tools/sim/export-intent-rollout'
import { STAGES } from '../../src/config/stages'

/**
 * HTML 巡检报告指标单元测试（rl-hourly-inspect.ts + export-intent-rollout.ts dims）。
 *
 * 保证「迭代健康指标」表（winRate/score_mean/baseIntegrity/entropy/KL/局均ticks）与
 * 「本段各关表现」表（score/击杀/win/ticks）的取值来源正确：
 *  - IterEvent 派生函数：gamesOf / avgTicksPerGame / baseIntegrityOf / klEff / healthVerdict；
 *  - metricsOf：从 per-tick RL 与意图 RL 两种 manifest 提取 score/kills/win/ticks；
 *  - export-intent-rollout 的 v7 dims：progress/baseIntegrity/baseSafety 与局内实际状态一致。
 */

const WEIGHTS = (() => {
  try {
    return readFileSync('tmp/intent-rl/weights.json', 'utf8')
  } catch {
    // 训练产物缺失（tmp/ 为临时区，产物随训练运行存在）——依赖它的用例整体跳过。
    return null
  }
})()

function iterEvent(partial: Partial<IterEvent>): IterEvent {
  return {
    iter: 1,
    time: '2026-08-27 00:00:00',
    winRate: 0.5,
    outcomes: { stage_clear: 1, base_destroyed: 1 },
    score_mean: null,
    entropy: null,
    kl: null,
    ticks: null,
    ...partial,
  }
}

describe('HTML 报告指标（迭代健康指标表）', () => {
  it('gamesOf = outcomes 计数之和（不硬编码轮局数）', () => {
    expect(gamesOf(iterEvent({}))).toBe(2)
    expect(gamesOf(iterEvent({ outcomes: { timeout: 5 } }))).toBe(5)
    expect(gamesOf(iterEvent({ outcomes: {} }))).toBe(0)
  })

  it('avgTicksPerGame = ticks / games；缺数据返回 null 而非 0', () => {
    expect(avgTicksPerGame(iterEvent({ ticks: 6000 }))).toBe(3000)
    expect(avgTicksPerGame(iterEvent({ ticks: 0 }))).toBe(null)
    expect(avgTicksPerGame(iterEvent({ outcomes: {}, ticks: 5000 }))).toBe(null)
  })

  it('baseIntegrityOf 读 dim_means.baseIntegrity（意图 RL 写入的守家维度）', () => {
    expect(baseIntegrityOf(iterEvent({ dim_means: { baseIntegrity: 0.87 } }))).toBe(0.87)
    expect(baseIntegrityOf(iterEvent({}))).toBe(null)
    expect(baseIntegrityOf(iterEvent({ dim_means: { baseIntegrity: null } }))).toBe(null)
  })

  it('klEff 优先轮内累计 kl_cum（熔断判据口径），旧行回落 kl', () => {
    expect(klEff(iterEvent({ kl_cum: 0.15, kl: 0.03 }))).toBe(0.15)
    expect(klEff(iterEvent({ kl: 0.04 }))).toBe(0.04)
    expect(klEff(iterEvent({}))).toBe(null)
  })

  it('healthVerdict：健康/观察/异常三档判定', () => {
    // 健康：熵 > 0.8、KL 无连续超标、局均 ticks 正常。
    expect(healthVerdict([iterEvent({ entropy: 1.2, kl: 0.04, ticks: 8000 })])).toBe('健康')
    // 观察：熵 < 0.8 或 KL 连续 2。
    expect(healthVerdict([iterEvent({ entropy: 0.7, kl: 0.04, ticks: 8000 })])).toBe('观察')
    // 异常：熵 ≤ 0.6 或 KL 连续 3 或局均 ticks < 1000。
    expect(healthVerdict([iterEvent({ entropy: 0.5, kl: 0.04, ticks: 8000 })])).toBe('异常')
    expect(
      healthVerdict([
        iterEvent({ entropy: 1.0, kl: 0.2, ticks: 8000 }),
        iterEvent({ entropy: 1.0, kl: 0.2, ticks: 8000 }),
        iterEvent({ entropy: 1.0, kl: 0.2, ticks: 8000 }),
      ]),
    ).toBe('异常')
  })
})

describe('HTML 报告指标（本段各关表现表 — metricsOf）', () => {
  it('per-tick RL manifest（dims.progress/baseIntegrity + scoreList）正确提取', () => {
    const m = metricsOf({
      report: {
        stages: [3],
        seeds: [7],
        scoreList: [0.82],
        outcomes: { stage_clear: 1 },
        totalTicks: 9200,
      },
      manifest: {
        progress: { value: 0.6, raw: 12 },
        baseIntegrity: { value: 1, raw: 8 },
        clearSpeed: { value: 0.7, raw: 9200 },
      },
    } as never)
    expect(m.stage).toBe(3)
    expect(m.seed).toBe(7)
    expect(m.score).toBe(0.82)
    expect(m.win).toBe(true)
    expect(m.kills).toBe(12)
    expect(m.ticks).toBe(9200)
  })

  it('意图 RL manifest（无 scoreList/dims，用 score/kills 标量）正确提取', () => {
    const m = metricsOf({
      report: {
        score: 0.315,
        kills: 8,
        totalTicks: 3000,
        outcomes: { timeout: 1 },
      } as never,
      manifest: { progress: { value: 0.4, raw: 8 } } as never,
    } as never)
    expect(m.score).toBe(0.315)
    expect(m.kills).toBe(8)
    expect(m.ticks).toBe(3000)
    expect(m.win).toBe(false)
  })

  it('缺 dims/score 时兜底：score -1、kills 回落 r.kills（不崩）', () => {
    const m = metricsOf({
      report: { outcomes: { base_destroyed: 1 }, totalTicks: 5000 } as never,
      manifest: null,
    } as never)
    expect(m.score).toBe(-1)
    expect(m.kills).toBe(-1)
    expect(m.ticks).toBe(5000)
    expect(m.win).toBe(false)
  })
})

describe('export-intent-rollout v7 dims 正确性（HTML 报告数据源）', () => {
  // 依赖 tmp/intent-rl/weights.json（训练产物）；缺失时跳过（确定性测试不依赖外部产物）。
  const itW = WEIGHTS !== null ? it : it.skip
  itW('baseIntegrity：基地失守=0；存活 ∈ [0.55, 1]（0.55+0.45·完好墙比，含 telemetry）', () => {
    for (const seed of [1, 2]) {
      const r = runOne(0, STAGES[0], seed, 'hard', 4000, WEIGHTS!, 30)
      if (r.outcome === 'base_destroyed') {
        expect(r.dims.baseIntegrity.value).toBe(0)
      } else {
        const v = r.dims.baseIntegrity.value
        expect(v).toBeGreaterThanOrEqual(0.55)
        expect(v).toBeLessThanOrEqual(1)
      }
    }
  })

  itW('progress = clamp01(kills / enemyTotal)（π 定义，God-AI 评分同口径）', () => {
    const enemyTotal = STAGES[0].enemyCount ?? 20
    for (const seed of [3, 4]) {
      const r = runOne(0, STAGES[0], seed, 'hard', 4000, WEIGHTS!, 30)
      expect(r.dims.progress.value).toBeCloseTo(Math.min(1, r.kills / enemyTotal), 5)
    }
  })

  itW('任何局的 dims 值域合法：progress/baseIntegrity/baseSafety ∈ [0,1]', () => {
    const r = runOne(0, STAGES[0], 99, 'hard', 3000, WEIGHTS!, 30)
    for (const k of ['progress', 'baseIntegrity', 'baseSafety'] as const) {
      const v = r.dims[k]?.value
      if (v === null) continue // 某些维度缺数据可合法为 null
      expect(v).toBeGreaterThanOrEqual(0)
      expect(v).toBeLessThanOrEqual(1)
    }
  })
})
