/**
 * P′-0：GodAIInput 决策状态 capture/restore 契约。
 *
 * 分支 rollout 的正确性前提：同一 tick 的 WorldSnapshot + GodAIDecisionState
 * 恢复后，继续跑 K 拍必须与「未回档」的 K 拍逐字段一致；且同一份 capture
 * 可重复 restore（分支之间不得串味）。
 */
import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { DEFAULT_GOD_AI_PARAMS, GodAIInput } from '../src/ai/GodAIInput'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../src/config/rules'
import { STAGES } from '../src/config/stages'
import { RNG } from '../src/utils/RNG'
import { cloneWorld, restoreWorld } from '../src/snapshot/WorldSerializer'
import { executorSeed } from '../tools/sim/simulation-runner'
import { canonicalizeIds } from './support/canonicalize-ids'

function makeRun(
  seed: number,
  stageIndex = 0,
): {
  world: World
  input: GodAIInput
  sim: Simulation
} {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard'] ?? DIFFICULTIES['classic']
  world.rules = RULES['hard'] ?? DEFAULT_RULES
  world.playerLevel = 0
  world.lives = 3
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS }, new RNG(executorSeed(seed)))
  const sim = new Simulation(world, input)
  world.loadStageData(STAGES[stageIndex], stageIndex)
  input.reset()
  return { world, input, sim }
}

function step(run: ReturnType<typeof makeRun>, n: number): void {
  for (let i = 0; i < n; i++) {
    run.sim.tick()
    run.input.endFrame()
  }
}

describe('GodAIInput decision state capture/restore', () => {
  it('restore 后续写一致：World 快照与决策状态逐字段相等', () => {
    const run = makeRun(20261128, 0)
    step(run, 120)
    const snap = cloneWorld(run.world)
    const state = run.input.captureDecisionState()

    step(run, 80)
    const expectedWorld = cloneWorld(run.world)
    const expectedState = run.input.captureDecisionState()

    restoreWorld(run.world, snap)
    run.input.restoreDecisionState(state)
    step(run, 80)

    expect(canonicalizeIds(cloneWorld(run.world))).toEqual(canonicalizeIds(expectedWorld))
    expect(canonicalizeIds(run.input.captureDecisionState())).toEqual(
      canonicalizeIds(expectedState),
    )
  })

  it('同一份 capture 可重复 restore（分支之间不串味）', () => {
    const run = makeRun(20261129, 0)
    step(run, 60)
    const snap = cloneWorld(run.world)
    const state = run.input.captureDecisionState()

    step(run, 60)
    const expectedWorld = cloneWorld(run.world)
    const expectedState = run.input.captureDecisionState()

    // 第一次回档后故意再跑 10 拍（制造「现场被改脏」），第二次必须仍回到同一终点。
    restoreWorld(run.world, snap)
    run.input.restoreDecisionState(state)
    step(run, 10)

    restoreWorld(run.world, snap)
    run.input.restoreDecisionState(state)
    step(run, 60)

    expect(canonicalizeIds(cloneWorld(run.world))).toEqual(canonicalizeIds(expectedWorld))
    expect(canonicalizeIds(run.input.captureDecisionState())).toEqual(
      canonicalizeIds(expectedState),
    )
  })

  it('版本/字段缺失 fail-closed', () => {
    const run = makeRun(20261130, 0)
    expect(() => run.input.restoreDecisionState({ version: 2 } as never)).toThrow(
      /unsupported version/,
    )
    expect(() =>
      run.input.restoreDecisionState({ version: 1, rngState: 0, fields: {} } as never),
    ).toThrow(/missing field/)
  })
})
