import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  buildMetricsRow,
  METRICS_DIM,
  PICKUP_DIST_SENTINEL,
  type Telemetry,
} from '../tools/sim/export-rl-rollout'
import { seedWorld, positionPlayer, makePowerUp } from './helpers'

/**
 * metrics 行宽护栏（2026-09-12 P0 回归）。
 *
 * 背景：给指标向量加第 31 列 `clearTick` 时只改了 `buildMetricsRow` 的行、没改
 * `METRICS_DIM`（仍是 30），`writeRlShard` 的 `metrics.set(row, i * METRICS_DIM)`
 * 于是在**每一局**的终局行越界抛 `RangeError` —— 整条 RL 采集腿零产出，而
 * `bun run typecheck` 看不见（`number[]` 无长度类型）、也没有任何断言覆盖行宽。
 *
 * 这里锁两件事：
 *   1. 行宽 == `METRICS_DIM`（加/删列必须同步常量）；
 *   2. `METRICS_DIM` == Python `rl/reward_library.py::METRICS` 的条目数（跨语言 SSOT，
 *      manifest 的 metrics_version 与之绑定）。
 */

function makeTelemetry(over: Partial<Telemetry> = {}): Telemetry {
  return {
    enemyTotal: 20,
    startLives: 1,
    playerDeaths: 0,
    playerHits: 0,
    playerDamageTaken: 0,
    playerShots: 0,
    powerUpsSpawned: 0,
    powerUpsCollected: 0,
    starsCollected: 0,
    puSpawnBomb: 0,
    puSpawnTank: 0,
    puSpawnFreeze: 0,
    puSpawnShield: 0,
    puGotBomb: 0,
    puGotTank: 0,
    puGotFreeze: 0,
    puGotShield: 0,
    puGotOther: 0,
    puSpawnStar: 0,
    baseWallTotal: 8,
    baseWallIntact: 8,
    basePressureSum: 0,
    basePressureSamples: 0,
    cellsVisited: new Set<number>(),
    firstKillTick: undefined,
    clearTick: undefined,
    enemyHits: 0,
    killsByKind: [0, 0, 0, 0],
    hitsByKind: [0, 0, 0, 0],
    stuckTicks: 0,
    ...over,
  }
}

describe('export-rl-rollout metrics 行宽', () => {
  it('buildMetricsRow 行宽 == METRICS_DIM', () => {
    const world = seedWorld(1)
    const row = buildMetricsRow(0, world, makeTelemetry())
    expect(row.length).toBe(METRICS_DIM)
    expect(row.every((v) => Number.isFinite(v))).toBe(true)
  })

  it('未清场 / 已清场两种哨兵都写进第 30 列，且不影响行宽', () => {
    const world = seedWorld(1)
    const nz = buildMetricsRow(7, world, makeTelemetry({ clearTick: 123 }))
    const sentinel = buildMetricsRow(7, world, makeTelemetry())
    expect(nz.length).toBe(METRICS_DIM)
    expect(sentinel.length).toBe(METRICS_DIM)
    // clearTick 固定 idx30（v5）；v6 在尾部追加分敌种列，不再假设「最后一列」
    expect(nz[30]).toBe(123)
    expect(sentinel[30]).toBe(-1)
  })

  it('METRICS_DIM 与 Python METRICS 列数一致（跨语言 SSOT）', () => {
    const pyPath = join(import.meta.dir, '..', 'nn-training', 'rl', 'reward_library.py')
    const py = readFileSync(pyPath, 'utf8')
    const block = py.match(/METRICS: tuple\[str, \.\.\.\] = \(([\s\S]*?)\n\)/)
    expect(block).not.toBeNull()
    const names = [...(block as RegExpMatchArray)[1].matchAll(/"([A-Za-z0-9_]+)"/g)].map(
      (m) => m[1],
    )
    // 列名唯一且与 TS 侧行宽同长
    expect(new Set(names).size).toBe(names.length)
    expect(names.length).toBe(METRICS_DIM)
    expect(names[30]).toBe('clearTick')
    expect(names[31]).toBe('killsBasic')
    expect(names[38]).toBe('hitsArmor')
    expect(names[39]).toBe('puGotOther')
    expect(names[40]).toBe('pickupDist')
  })

  it('分敌种击杀/命中写入 idx31–38（metrics v6）', () => {
    const world = seedWorld(1)
    const row = buildMetricsRow(
      0,
      world,
      makeTelemetry({ killsByKind: [1, 2, 3, 4], hitsByKind: [5, 6, 7, 8] }),
    )
    expect(row.length).toBe(METRICS_DIM)
    expect(row.slice(31, 39)).toEqual([1, 2, 3, 4, 5, 6, 7, 8])
  })

  it('pickupDist 写入 idx40（metrics v7）：独立重算 vs buildMetricsRow 逐位一致', () => {
    // 独立重实现（不看生产 helper）：中心格曼哈顿，跳过非存活拾取。
    const indepPickupDist = (w: ReturnType<typeof seedWorld>): number => {
      const p = w.player
      if (!p || !p.alive) return PICKUP_DIST_SENTINEL
      const pc = { col: Math.floor((p.x + p.w / 2) / 16), row: Math.floor((p.y + p.h / 2) / 16) }
      let best = PICKUP_DIST_SENTINEL
      for (const pu of w.powerUps) {
        if (!pu.alive) continue
        const d =
          Math.abs(Math.floor((pu.x + pu.w / 2) / 16) - pc.col) +
          Math.abs(Math.floor((pu.y + pu.h / 2) / 16) - pc.row)
        if (best < 0 || d < best) best = d
      }
      return best
    }
    // 带玩家的 world（startGame 才会建玩家坦克）。
    const withPlayer = (seed: number): ReturnType<typeof seedWorld> => {
      const w = seedWorld(seed)
      w.startGame('hard', 'modern', 0)
      return w
    }

    // 多拾取局：取最近者（含炸弹型道具在场）
    const w1 = withPlayer(1)
    positionPlayer(w1, 8, 8)
    w1.powerUps.push(
      makePowerUp(12, 8, 'bomb'), // 距 4 格
      makePowerUp(8, 20, 'star'), // 距 12 格
      makePowerUp(5, 5, 'tank'), // 距 6 格
    )
    const r1 = buildMetricsRow(0, w1, makeTelemetry())
    expect(r1[40]).toBe(4)
    expect(r1[40]).toBe(indepPickupDist(w1))

    // 同格 = 0；非存活拾取被跳过
    const w2 = withPlayer(2)
    positionPlayer(w2, 5, 5)
    w2.powerUps.push(makePowerUp(5, 5, 'freeze'), makePowerUp(5, 9, 'shield', { alive: false }))
    const r2 = buildMetricsRow(0, w2, makeTelemetry())
    expect(r2[40]).toBe(0)
    expect(r2[40]).toBe(indepPickupDist(w2))

    // 无存活拾取 → 哨兵 -1
    const w3 = withPlayer(3)
    positionPlayer(w3, 3, 3)
    w3.powerUps.push(makePowerUp(3, 9, 'star', { alive: false }))
    const r3 = buildMetricsRow(0, w3, makeTelemetry())
    expect(r3[40]).toBe(PICKUP_DIST_SENTINEL)
    expect(r3[40]).toBe(indepPickupDist(w3))

    // 玩家不在场（阵亡）→ 哨兵 -1
    const w4 = withPlayer(4)
    positionPlayer(w4, 2, 2)
    w4.player!.alive = false
    w4.powerUps.push(makePowerUp(2, 2, 'star'))
    const r4 = buildMetricsRow(0, w4, makeTelemetry())
    expect(r4[40]).toBe(PICKUP_DIST_SENTINEL)
    expect(r4[40]).toBe(indepPickupDist(w4))
  })

  it('puGotOther 写入 idx39（metrics v7）：四桶外拾取的残差记账', () => {
    const world = seedWorld(1)
    const row = buildMetricsRow(
      0,
      world,
      makeTelemetry({ puGotBomb: 1, puGotTank: 2, puGotOther: 3, starsCollected: 4 }),
    )
    expect(row.length).toBe(METRICS_DIM)
    expect(row[25]).toBe(1) // puGotBomb
    expect(row[26]).toBe(2) // puGotTank
    expect(row[39]).toBe(3) // puGotOther
    expect(row[10]).toBe(4) // starsCollected
  })
})
