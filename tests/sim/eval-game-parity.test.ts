import { describe, it, expect } from 'bun:test'
import { runSimulation } from '../../tools/sim/simulation-runner'
import { runEvalOne } from '../../tools/sim/export-eval-game'
import { STAGES } from '../../src/config/stages'

/**
 * v4.0 远程分发等价性（用户指令 2026-08-29：m1-eval 每批都要利用远程 agents）：
 * export-eval-game 的 god 分支必须与本地 runSimulation(policy god) 产出**同一局**——
 * 远程 agents 跑的是 export-eval-game，本地跑的是 runSimulation；两者不等价时，
 * 分发批与本地批不可配对（门判定作废）。
 *
 * 可区分性冒烟（goal-god 伪影教训）：god 与 goal-god 必须产生可区分的结果——
 * 防止"新 policy 静默回落已知策略"再次发生（回落时两者完全一致）。
 */
function parity(stageIdx: number, seed: number, maxTicks = 8000): void {
  const world = { stage: stageIdx, seed }
  const local = runSimulation({
    seed,
    stage: STAGES[stageIdx] as never,
    stageIndex: stageIdx,
    difficulty: 'hard',
    policy: 'god',
    maxTicks,
    collectMetrics: false,
  })
  const remote = runEvalOne(stageIdx, STAGES[stageIdx], seed, 'hard', maxTicks, '{}', 'god')
  expect(remote.outcome).toBe(local.outcome)
  expect(remote.ticks).toBe(local.ticks)
  expect(remote.win).toBe(local.ticks < maxTicks && local.outcome === 'stage_clear')
  void world
}

describe('export-eval-game god 分支与 runSimulation 等价（远程/本地对账）', () => {
  it('stage 0 seed 1/2/3 同局', () => {
    parity(0, 1)
    parity(0, 2)
    parity(0, 3)
  })

  it('stage 5 seed 1 同局（不同地形族）', () => {
    parity(5, 1)
  })
})

describe('policy 可区分性冒烟（回落检测）', () => {
  it('god 与 goal-god 在同一局产生不同行为（非静默回落）', () => {
    const stageIdx = 0
    const seed = 1
    const maxTicks = 3000
    const god = runSimulation({
      seed,
      stage: STAGES[stageIdx] as never,
      stageIndex: stageIdx,
      difficulty: 'hard',
      policy: 'god',
      maxTicks,
      collectMetrics: false,
    })
    const goalGod = runSimulation({
      seed,
      stage: STAGES[stageIdx] as never,
      stageIndex: stageIdx,
      difficulty: 'hard',
      policy: 'goal-god',
      maxTicks,
      collectMetrics: false,
    })
    const distinct =
      god.outcome !== goalGod.outcome ||
      god.ticks !== goalGod.ticks ||
      god.finalState.killCount !== goalGod.finalState.killCount
    expect(distinct).toBe(true)
  })
})
