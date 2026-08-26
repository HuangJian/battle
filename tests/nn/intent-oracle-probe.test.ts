import { describe, expect, it } from 'bun:test'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { START_LIVES } from '../../src/constants'
import { IntentOracleProbe } from '../../src/nn/intent-oracle-probe'
import { INTENT_IDS } from '../../src/ai/intent/vocab'

/**
 * M7① 天花板探针单测：双 God 实例（oracle 全链 + executor 受限链）驱动真实 sim。
 * 断言：不崩、确定性（同 seed 双跑逐字节一致）、意图 trace 合法、oracle 意图确实
 * 提交到 executor override（探针机制生效）。
 */

function run(
  seed: number,
  ticks: number,
): { ok: boolean; state: string; trace: number[]; overrideTicks: number } {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard']
  world.rules = RULES['hard'] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  const probe = new IntentOracleProbe(world, { seed })
  const sim = new Simulation(world, probe)
  world.loadStageData(STAGES[9], 9)
  probe.reset()

  let ok = true
  let overrideTicks = 0
  for (let t = 0; t < ticks; t++) {
    if (world.state !== 'playing') break
    try {
      sim.tick()
      probe.endFrame()
      if (probe['exec']._candidateOverride !== null) overrideTicks++
    } catch {
      ok = false
      break
    }
  }
  return { ok, state: world.state, trace: [...probe.intentTrace], overrideTicks }
}

describe('M7① IntentOracleProbe（天花板探针）', () => {
  it('驱动 S10 真实 sim ≥600 tick 不崩，产出 oracle 意图 trace', () => {
    const r = run(11, 600)
    expect(r.ok).toBe(true)
    expect(r.trace.length).toBeGreaterThan(0)
    for (const i of r.trace) {
      expect(i).toBeGreaterThanOrEqual(0)
      expect(i).toBeLessThan(8)
    }
  })

  it('确定性：同 seed 双跑意图序列与终局一致（零 world.rng 消费）', () => {
    const a = run(42, 900)
    const b = run(42, 900)
    expect(JSON.stringify(a.trace)).toBe(JSON.stringify(b.trace))
    expect(a.state).toBe(b.state)
  })

  it('oracle 意图提交到 executor override（探针机制生效，非全程空链）', () => {
    const r = run(7, 600)
    expect(r.overrideTicks).toBeGreaterThan(0)
  })

  it('oracle 意图分布 ∈ 词表（无未知标签污染 trace）', () => {
    const r = run(3, 600)
    for (const i of r.trace) expect(INTENT_IDS[i] !== undefined).toBe(true)
  })
})
