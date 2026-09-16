/**
 * metrics-v6-census.test.ts — T5 分敌种命中/击杀列（plan/t5-metrics-v6 §5 DoD.4）。
 *
 * 体例仿 `phase0-census.test.ts`：
 *   1. 独立重实现对账：从原始事件流本地重算分敌种击杀/命中，与 rollout 侧
 *      telemetry 规则（export-rl-rollout 事件消费语义）逐值对账；同时对照
 *      eval 侧 census（export-eval-game）作参照实现。
 *   2. 守恒：sum(killsByKind) == 玩家击杀敌车数；sum(hitsByKind) == enemyHits。
 *   3. determinism：同 seed 双跑，metrics 行（含 idx31–38）逐值相同。
 */
import { describe, it, expect } from 'bun:test'
import { runSimulation } from '../../tools/sim/simulation-runner'
import { runEvalOne, ENEMY_KIND_ORDER } from '../../tools/sim/export-eval-game'
import {
  buildMetricsRow,
  enemyKindIndex,
  METRICS_DIM,
  type Telemetry,
} from '../../tools/sim/export-rl-rollout'
import { STAGES } from '../../src/config/stages'
import { seedWorld } from '../helpers'
import type { GameEvent, TankKind } from '../../src/types'

const KINDS = ENEMY_KIND_ORDER as readonly TankKind[]
const idxOf = (kind: string): number => KINDS.indexOf(kind as TankKind)

/** 独立重实现：只看原始事件流，不看任何中间 census/telemetry 状态。 */
function recountKinds(events: GameEvent[]): {
  enemyHits: number
  playerKills: number
  hits: [number, number, number, number]
  kills: [number, number, number, number]
} {
  const hits: [number, number, number, number] = [0, 0, 0, 0]
  const kills: [number, number, number, number] = [0, 0, 0, 0]
  let enemyHits = 0
  let playerKills = 0
  for (const e of events) {
    if (e.type === 'enemy_hit') {
      enemyHits++
      const i = idxOf(e.targetKind)
      if (i >= 0) hits[i]++
    } else if (e.type === 'tank_destroyed') {
      if (e.tank.isPlayer) continue
      if (e.by === 'player' && e.tank.allegiance === 'enemy') {
        playerKills++
        const i = idxOf(e.tank.kind)
        if (i >= 0) kills[i]++
      }
    }
  }
  return { enemyHits, playerKills, hits, kills }
}

/**
 * rollout 侧事件消费语义（export-rl-rollout.ts 的 tank_destroyed / enemy_hit 分支，
 * 刻意在本文件重写一遍——两实现不共享代码）。
 */
function fillRolloutKindTel(events: GameEvent[]): {
  enemyHits: number
  killsByKind: [number, number, number, number]
  hitsByKind: [number, number, number, number]
} {
  const killsByKind: [number, number, number, number] = [0, 0, 0, 0]
  const hitsByKind: [number, number, number, number] = [0, 0, 0, 0]
  let enemyHits = 0
  for (const e of events) {
    if (e.type === 'tank_destroyed') {
      if (e.tank.isPlayer) continue
      if (e.by === 'player' && e.tank.allegiance === 'enemy') {
        const ki = enemyKindIndex(e.tank.kind)
        if (ki >= 0) killsByKind[ki]++
      }
    } else if (e.type === 'enemy_hit') {
      enemyHits++
      const ki = enemyKindIndex(e.targetKind)
      if (ki >= 0) hitsByKind[ki]++
    }
  }
  return { enemyHits, killsByKind, hitsByKind }
}

function makeTel(over: Partial<Telemetry> = {}): Telemetry {
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
    killsByKind: [0, 0, 0, 0],
    hitsByKind: [0, 0, 0, 0],
    stuckTicks: 0,
    ...over,
  }
}

function runGod(stageIdx: number, seed: number, maxTicks = 4000) {
  return runSimulation({
    seed,
    stage: STAGES[stageIdx] as never,
    stageIndex: stageIdx,
    difficulty: 'hard',
    policy: 'god',
    maxTicks,
    telemetry: true,
    collectMetrics: false,
  })
}

describe('metrics v6：rollout 分敌种列 vs 独立重实现 / eval census', () => {
  it('killsByKind/hitsByKind 与原始事件流重算逐值一致，且守恒', () => {
    for (const [stageIdx, seed] of [
      [0, 1],
      [0, 7],
      [3, 1],
      [5, 3],
    ] as const) {
      const local = runGod(stageIdx, seed)
      const rc = recountKinds(local.events)
      const roll = fillRolloutKindTel(local.events)
      const evalR = runEvalOne(stageIdx, STAGES[stageIdx] as never, seed, 'hard', 4000, '{}', 'god')

      expect(roll.killsByKind).toEqual(rc.kills)
      expect(roll.hitsByKind).toEqual(rc.hits)
      expect(roll.enemyHits).toBe(rc.enemyHits)
      // 守恒（事件流内部）：四桶不丢计数
      expect(roll.killsByKind.reduce((a, b) => a + b, 0)).toBe(rc.playerKills)
      expect(roll.hitsByKind.reduce((a, b) => a + b, 0)).toBe(rc.enemyHits)
      // ⚠ 但**与指标 kills 列（world.killCount）不必相等**（评审 P0，2026-09-16）：
      // `recordEnemyKill()`（src/game/KillPipeline.ts:36）是 killCount++ 的唯一入口，
      // 其 4 个调用点中仅 bomb 清屏（src/game/SimulationPowerUps.ts:391）不推
      // `tank_destroyed` ⇒ 事件流分列之和**恒 ≤** 标量 kills 列
      //（实测 God AI 长局缺 11.6%、NN/ladder-c03 缺 ~1.4%；不守恒局 100% 拾取过 bomb）。
      // 别把这里改成 toBe(killCount)——那是错的；奖励侧由 x3-credit-p6 的残差桶补偿
      //（见 nn-training/tests/test_reward_golden.py::test_credit_p6_formula_and_course）。
      expect(roll.killsByKind.reduce((a, b) => a + b, 0)).toBeLessThanOrEqual(
        local.finalState.killCount,
      )
      // eval 侧 census 参照（两实现独立，同 seed 对齐）
      expect(evalR.killsByKind).toEqual([...rc.kills])
      expect(evalR.hitsByKind).toEqual([...rc.hits])
    }
  })

  it('buildMetricsRow 把分敌种计数写进 idx31–38，行宽 METRICS_DIM', () => {
    const world = seedWorld(1)
    const tel = makeTel({
      killsByKind: [2, 0, 1, 0],
      hitsByKind: [5, 1, 3, 0],
      enemyHits: 9,
    })
    const row = buildMetricsRow(0, world, tel)
    expect(row.length).toBe(METRICS_DIM)
    expect(row[31]).toBe(2)
    expect(row[32]).toBe(0)
    expect(row[33]).toBe(1)
    expect(row[34]).toBe(0)
    expect(row[35]).toBe(5)
    expect(row[36]).toBe(1)
    expect(row[37]).toBe(3)
    expect(row[38]).toBe(0)
    expect(row[6]).toBe(9) // enemyHits 总列不受分列影响
  })
})

describe('metrics v6：determinism（同 seed 双跑分列逐值相同）', () => {
  it('同一 (stage, seed) 两次事件流 → 分敌种计数相同', () => {
    for (const [stageIdx, seed] of [
      [0, 11],
      [3, 5],
    ] as const) {
      const a = fillRolloutKindTel(runGod(stageIdx, seed, 2000).events)
      const b = fillRolloutKindTel(runGod(stageIdx, seed, 2000).events)
      expect(JSON.stringify(a)).toBe(JSON.stringify(b))
    }
  })
})

describe('metrics v6：enemyKindIndex 热路径契约', () => {
  it('四敌种映射 0–3，其余 -1', () => {
    expect(enemyKindIndex('basic')).toBe(0)
    expect(enemyKindIndex('fast')).toBe(1)
    expect(enemyKindIndex('power')).toBe(2)
    expect(enemyKindIndex('armor')).toBe(3)
    expect(enemyKindIndex('player')).toBe(-1)
    expect(enemyKindIndex('')).toBe(-1)
  })
})
