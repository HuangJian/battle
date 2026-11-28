/**
 * P′-3 E2′ 整局 byte-identical：IntentOptionProbe（E2′ nn-option 委托）
 * ≡ IntentOracleProbe（E1 白名单子链）。同 seed 的 800 拍轨迹、意图 trace、
 * 终局 World 全字段必须一致（计划 §9.6 第一门的整局版本）。
 */
import { describe, expect, it } from 'bun:test'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { START_LIVES } from '../../src/constants'
import { IntentOracleProbe } from '../../src/nn/intent-oracle-probe'
import { IntentOptionProbe } from '../../src/nn/intent-option-probe'
import type { InputLike } from '../../src/game/Input'
import { cloneWorld } from '../../src/snapshot/WorldSerializer'
import { canonicalizeIds } from '../support/canonicalize-ids'

function makeRun(seed: number, mode: 'e1' | 'e2') {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard']
  world.rules = RULES['hard'] ?? DEFAULT_RULES
  world.playerLevel = 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  const probe: InputLike & { intentTrace: number[] } =
    mode === 'e1'
      ? new IntentOracleProbe(world, { seed, replanEvery: 30 })
      : new IntentOptionProbe(world, { seed, replanEvery: 30 })
  const sim = new Simulation(world, probe)
  world.loadStageData(STAGES[9], 9)
  probe.reset()
  return { world, sim, probe }
}

describe('P′-3 E2′ probe parity（E1 ≡ nn-option 委托）', () => {
  it('同 seed 800 拍：意图 trace + 终局 World 逐字段一致', () => {
    const seed = 20261201
    const e1 = makeRun(seed, 'e1')
    const e2 = makeRun(seed, 'e2')
    for (let t = 0; t < 800; t++) {
      if (e1.world.state !== 'playing' || e2.world.state !== 'playing') break
      e1.sim.tick()
      e1.probe.endFrame()
      e2.sim.tick()
      e2.probe.endFrame()
    }
    expect(JSON.stringify(e2.probe.intentTrace)).toBe(JSON.stringify(e1.probe.intentTrace))
    expect(e2.probe.intentTrace.length).toBeGreaterThan(0)
    expect(e2.world.state).toBe(e1.world.state)
    expect(e2.world.frame).toBe(e1.world.frame)
    expect(canonicalizeIds(cloneWorld(e2.world))).toEqual(canonicalizeIds(cloneWorld(e1.world)))
  })
})
