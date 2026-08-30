import { describe, it, expect } from 'bun:test'
import { runSimulation, type SimResult } from '../../tools/sim/simulation-runner'
import { runEvalOne } from '../../tools/sim/export-eval-game'
import { STAGES } from '../../src/config/stages'
import type { GameEvent } from '../../src/types'

/**
 * 卡 P0-1（docs/goal-nn-next.md）：eval 遥测矛盾。S2 门判据②（受伤 ≤1.2×锚）与
 * 判据③（存活 ≥80%×锚）直接依赖 deaths/shots 遥测，数据不可信 ⇒ 门在错误数据上判。
 *
 * 独立 re-implementation 口径（区别于 codec golden file，防止两实现共享同一 bug）：
 * 从 `SimResult.events`（零埋点原始事件流）重新统计 击杀/开火/死亡/被命中，
 * 与 `RunTelemetry` / `killCount` 对账。另校验远程分发路径（export-eval-game 的
 * runEvalOne）与本地路径（runSimulation）遥测一致——门判时本地锚与远程批必须同口径。
 */
function recount(events: GameEvent[]): {
  shots: number
  deaths: number
  kills: number
  hits: number
} {
  let shots = 0
  let deaths = 0
  let kills = 0
  let hits = 0
  for (const e of events) {
    if (e.type === 'bullet_fired') {
      if (e.bullet.isPlayer) shots++
    } else if (e.type === 'tank_destroyed') {
      if (e.tank.isPlayer) deaths++
      if (e.by === 'player') kills++
    } else if (e.type === 'player_hit') {
      hits++
    }
  }
  return { shots, deaths, kills, hits }
}

function simOne(stageIdx: number, seed: number, maxTicks = 4000): SimResult {
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

describe('telemetry 与事件流独立重算一致（P0-1）', () => {
  it('stage 0 多 seed：shots/deaths 与 telemetry 全等，kills 与 killCount 守恒', () => {
    for (const seed of [1, 3, 5, 7, 9]) {
      const r = simOne(0, seed)
      const recount0 = recount(r.events)
      expect(r.telemetry).toBeDefined()
      expect(recount0.shots).toBe(r.telemetry!.playerShots)
      expect(recount0.deaths).toBe(r.telemetry!.playerDeaths)
      // killCount = 敌人阵亡总数（含地雷/友军 AoE 殃及 等非玩家死因，KillPipeline
      // recordEnemyKill 统一记账）；事件流 by='player' 只算玩家子弹直接击杀 ⇒
      // killCount ≥ 玩家击杀，且玩家击杀必须可被事件流解释（非空）。
      expect(recount0.kills).toBeLessThanOrEqual(r.finalState.killCount)
      expect(recount0.kills).toBeGreaterThanOrEqual(1) // 被 gate 的上帝局必有击杀
    }
  })

  it('player_hit 事件流与死亡口径兼容（death ≥1 的局 hits ≥ deaths）', () => {
    let foundDeath = false
    for (const seed of [1, 3, 5, 7, 9, 11, 13, 15]) {
      const r = simOne(0, seed)
      const { deaths, hits } = recount(r.events)
      if (deaths > 0) {
        foundDeath = true
        expect(hits).toBeGreaterThanOrEqual(deaths)
      }
    }
    expect(foundDeath).toBe(true) // God-AI 至少一局有死亡，否则本断言空转
  })
})

describe('远程/本地评估遥测口径一致（P0-1 分发对账）', () => {
  it('runEvalOne ≡ runSimulation：outcome/ticks/killCount/playerShots/playerDeaths', () => {
    for (const [stageIdx, seed] of [
      [0, 1],
      [0, 2],
      [3, 1],
    ] as const) {
      const local = simOne(stageIdx, seed)
      const remote = runEvalOne(
        stageIdx,
        STAGES[stageIdx] as never,
        seed,
        'hard',
        4000,
        '{}',
        'god',
      )
      const remoteScorable = remote.scorable as unknown as {
        finalState: { killCount: number }
        telemetry: { playerShots: number; playerDeaths: number }
      }
      expect(remote.outcome).toBe(local.outcome)
      expect(remote.ticks).toBe(local.ticks)
      expect(remote.cleared).toBe(local.cleared) // 全歼口径：分布式/本地必须一致（P0-1）
      expect(remoteScorable.finalState.killCount).toBe(local.finalState.killCount)
      expect(remoteScorable.telemetry.playerShots).toBe(local.telemetry!.playerShots)
      expect(remoteScorable.telemetry.playerDeaths).toBe(local.telemetry!.playerDeaths)
    }
  })
})
