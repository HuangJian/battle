/**
 * P′-3 / E2′ byte-identical 门（plan/nn-hierarchical-options.plan.md §9.6）。
 *
 * 断言：对 8 类意图中的每一个，
 *   E1  = `_candidateOverride = candidateOverrideForIntent(intent)`（现役子链）
 *   E2′ = `_candidateOverride = {'nnOption'}` + `_optionCmd.option = intent`
 *        （NN_OPTION 委托到同一份 `candidateOverrideForIntent` 子链）
 * 在同 seed 的两局里逐拍动作（_moveDir/_fire）、终局 World 全字段、AI RNG 状态全等。
 *
 * 白名单（计划允许差异）：branchCounts / _lastBranch / trace / telemetry /
 * _candidateOverride —— 本用例不比较这些。
 */
import { describe, expect, it } from 'bun:test'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { START_LIVES } from '../../src/constants'
import { DEFAULT_GOD_AI_PARAMS, GodAIInput } from '../../src/ai/GodAIInput'
import type { NNOptionCommand } from '../../src/ai/god/NNOption'
import { RNG } from '../../src/utils/RNG'
import { INTENT_IDS, candidateOverrideForIntent, type IntentId } from '../../src/ai/intent/vocab'
import { cloneWorld } from '../../src/snapshot/WorldSerializer'
import { canonicalizeIds } from '../support/canonicalize-ids'
import { executorSeed } from '../../tools/sim/simulation-runner'

function makeRun(seed: number, intent: IntentId, mode: 'e1' | 'e2') {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard']
  world.rules = RULES['hard'] ?? DEFAULT_RULES
  world.playerLevel = 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS }, new RNG(executorSeed(seed)))
  const sim = new Simulation(world, input)
  world.loadStageData(STAGES[9], 9)
  input.reset()
  if (mode === 'e1') {
    input._candidateOverride = candidateOverrideForIntent(intent)
  } else {
    const cmd: NNOptionCommand = {
      option: intent,
      targetKind: 'none',
      target: null,
      pathMode: 'direct',
      param: 0,
    }
    input._optionCmd = cmd
    input._candidateOverride = new Set(['nnOption'])
  }
  return { world, input, sim }
}

describe('P′-3 E2′ nn-option delegation parity', () => {
  for (const intent of INTENT_IDS) {
    it(`${intent}: E2′ 委托 ≡ E1 子集（300 拍逐拍动作 + 终局世界 + RNG）`, () => {
      const seed = 20261128 + INTENT_IDS.indexOf(intent)
      const e1 = makeRun(seed, intent, 'e1')
      const e2 = makeRun(seed, intent, 'e2')
      for (let t = 0; t < 300; t++) {
        if (e1.world.state !== 'playing' || e2.world.state !== 'playing') break
        e1.sim.tick()
        e1.input.endFrame()
        e2.sim.tick()
        e2.input.endFrame()
        expect(e2.input._moveDir).toBe(e1.input._moveDir)
        expect(e2.input._fire).toBe(e1.input._fire)
      }
      expect(e2.world.state).toBe(e1.world.state)
      expect(e2.input.rng.getState()).toBe(e1.input.rng.getState())
      expect(canonicalizeIds(cloneWorld(e2.world))).toEqual(canonicalizeIds(cloneWorld(e1.world)))
    })
  }
})
