import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { buildMetricsRow, METRICS_DIM, type Telemetry } from '../tools/sim/export-rl-rollout'
import { seedWorld } from './helpers'

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
    puSpawnStar: 0,
    baseWallTotal: 8,
    baseWallIntact: 8,
    basePressureSum: 0,
    basePressureSamples: 0,
    cellsVisited: new Set<number>(),
    firstKillTick: undefined,
    clearTick: undefined,
    enemyHits: 0,
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
    expect(nz[METRICS_DIM - 1]).toBe(123)
    expect(sentinel[METRICS_DIM - 1]).toBe(-1)
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
    expect(names[names.length - 1]).toBe('clearTick')
  })
})
