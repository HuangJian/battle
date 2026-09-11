/** evalboard-stats.test.ts ↔ tools/training/evalboard/stats.ts（§2.4 唯一口径）。 */
import { describe, expect, it } from 'bun:test'
import {
  deriveMetrics,
  flipRate,
  mddPaired,
  mddUnpaired,
  windowAgg,
} from '../tools/training/evalboard/stats'
import type { EvalGameRow } from '../tools/training/evalboard/store'

function row(over: Partial<EvalGameRow> = {}): EvalGameRow {
  return {
    schema: 1,
    ts: '2026-09-10T00:00:00.000Z',
    source: 'B',
    batch_id: 'b1',
    batch_unit: { idx: 0, of: 2 },
    run_id: 'run1',
    course: 'c',
    iter: 1,
    ckpt_path: 'w',
    ckpt_sha16: 'c'.repeat(16),
    init_sha16: 'i'.repeat(16),
    wver: 'v',
    policy: 'nn',
    rung: 'c4l1',
    probe_key: 'k',
    seedSpace: 'eval860k',
    stage_id: 's',
    stage_name: 's',
    seed: 860001,
    seed_segment: 0,
    engine: { git_commit: 'g', dist_codehash: 'd', engine_epoch: 'e' },
    outcome: 'stage_clear',
    win: true,
    cleared: true,
    ticks: 1000,
    kills: 4,
    enemyTotal: 4,
    playerHits: 2,
    playerDamageTaken: 1,
    playerShots: 50,
    enemyHits: 10,
    powerUpsCollected: 0,
    stuckTicks: 5,
    firstKillTick: 200,
    playerDeaths: 0,
    cellsVisited: 30,
    playerLevel: 1,
    puSpawnBomb: 0,
    puSpawnTank: 0,
    puSpawnFreeze: 0,
    puSpawnShield: 0,
    puSpawnStar: 0,
    puGotBomb: 0,
    puGotTank: 0,
    puGotFreeze: 0,
    puGotShield: 0,
    score: 1,
    metrics_version: 1,
    greedy: true,
    node_id: 'self',
    elapsedSec: 1,
    phase: 'rollout',
    milestone: false,
    ...over,
  }
}

describe('MDD 是函数不是常量 (§2.4)', () => {
  it('配对表值：100局 flips=40% → 12.4pp；flips=20% → 8.8pp；400局 → 6.2pp', () => {
    expect(mddPaired(100, 40)).toBeCloseTo(0.124, 3)
    expect(mddPaired(100, 20)).toBeCloseTo(0.088, 3)
    expect(mddPaired(400, 160)).toBeCloseTo(0.062, 3)
    expect(mddPaired(1600, 640)).toBeCloseTo(0.031, 2)
  })
  it('非配对：n 越大 MDD 越小', () => {
    const a = mddUnpaired(100, 0.5, 100, 0.5)
    const b = mddUnpaired(400, 0.5, 400, 0.5)
    expect(a).toBeGreaterThan(b)
    expect(Number.isNaN(mddUnpaired(0, 0.5, 100, 0.5))).toBe(true)
  })
})

describe('deriveMetrics (§5.1)', () => {
  it('wins=0 ⇒ lifePrice=+∞；空集胜局中位 N/A', () => {
    const m = deriveMetrics([row({ win: false, outcome: 'gameover', playerHits: 5 })])
    expect(m.lifePrice).toBe(Infinity)
    expect(m.winDmgMedian).toBeNull()
    expect(m.winTickMedian).toBeNull()
    expect(m.winRate).toBe(0)
  })
  it('正常聚合', () => {
    const m = deriveMetrics([
      row({ win: true, playerHits: 2 }),
      row({ win: false, outcome: 'gameover', playerHits: 3 }),
    ])
    expect(m.winRate).toBe(0.5)
    expect(m.lifePrice).toBe(5)
    expect(m.deathRate).toBe(0.5)
    expect(m.accuracy).toBeCloseTo(20 / 100, 5)
  })
})

describe('R2 二级指标', () => {
  it('meanKills / meanPowerUps / winTickMean / winHpLeftMean（L0 maxHp = 263）', () => {
    const m = deriveMetrics([
      row({
        win: true,
        kills: 4,
        powerUpsCollected: 2,
        ticks: 1000,
        playerLevel: 0,
        playerDamageTaken: 10,
      }),
      row({
        win: false,
        outcome: 'gameover',
        kills: 2,
        powerUpsCollected: 0,
        ticks: 800,
        playerLevel: 0,
        playerDamageTaken: 5,
      }),
    ])
    expect(m.meanKills).toBe(3)
    expect(m.meanPowerUps).toBe(1)
    expect(m.winTickMean).toBe(1000)
    expect(m.winHpLeftMean).toBe(263 - 10)
  })
  it('空集 ⇒ 0/null；无胜局 ⇒ winTickMean/winHpLeftMean 均 null', () => {
    const empty = deriveMetrics([])
    expect(empty.meanKills).toBe(0)
    expect(empty.meanPowerUps).toBe(0)
    expect(empty.winTickMean).toBeNull()
    expect(empty.winHpLeftMean).toBeNull()
    const noWin = deriveMetrics([row({ win: false, outcome: 'gameover' })])
    expect(noWin.winTickMean).toBeNull()
    expect(noWin.winHpLeftMean).toBeNull()
  })
  it('maxHp 随星位增长（L1 > L0）', () => {
    const l0 = deriveMetrics([row({ win: true, playerLevel: 0, playerDamageTaken: 0 })])
    const l1 = deriveMetrics([row({ win: true, playerLevel: 1, playerDamageTaken: 0 })])
    expect(l1.winHpLeftMean!).toBeGreaterThan(l0.winHpLeftMean!)
  })
})

describe('flipRate', () => {
  it('同 seed 对齐计数', () => {
    const a = new Map([
      [1, true],
      [2, false],
      [3, true],
    ])
    const b = new Map([
      [1, false],
      [2, false],
      [3, true],
    ])
    const f = flipRate(a, b)
    expect(f).toEqual({ flips: 1, n: 3, rate: 1 / 3, delta: -1 / 3 })
  })
})

describe('windowAgg (§5.4)', () => {
  it('不足 4 批标 partial；满 4 批不标；byCkpt 分桶留痕', () => {
    const b1 = [row({ ckpt_sha16: 'a'.repeat(16) })]
    const w1 = windowAgg('c4l1', [b1, b1])
    expect(w1.partial).toBe(true)
    expect(w1.batches).toBe(2)
    const w4 = windowAgg('c4l1', [b1, b1, b1, b1])
    expect(w4.partial).toBe(false)
    expect(w4.n).toBe(4)
    expect(w4.byCkpt['a'.repeat(16)].n).toBe(4)
    expect(w4.latest).not.toBeNull()
  })
})
