import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { START_LIVES } from '../../src/constants'
import { IntentExecutor } from '../../src/nn/intent-executor'
import { INTENT_IDS } from '../../src/ai/intent/vocab'

/**
 * M6 IntentExecutor 机制测试：子链共享委托 + 承诺期 + 确定性。
 * 用 golden 权重（固定前向）驱动真实 sim。
 */
const GOLDEN = JSON.parse(
  readFileSync(join(import.meta.dir, '..', 'fixtures', 'intent-golden.json'), 'utf8'),
) as { h: number; d: number; params: Record<string, unknown> }
const WEIGHTS_TEXT = JSON.stringify({
  arch: { kind: 'intent', h: GOLDEN.h, d: GOLDEN.d },
  params: GOLDEN.params,
})

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
  const exec = new IntentExecutor(world, { weightsText: WEIGHTS_TEXT })
  const sim = new Simulation(world, exec)
  world.loadStageData(STAGES[9], 9)
  exec.reset()

  let ok = true
  let overrideTicks = 0
  for (let t = 0; t < ticks; t++) {
    if (world.state !== 'playing') break
    try {
      sim.tick()
      exec.endFrame()
      if (exec['god']._candidateOverride !== null) overrideTicks++
    } catch {
      ok = false
      break
    }
  }
  return { ok, state: world.state, trace: [...exec.intentTrace], overrideTicks }
}

describe('M6 IntentExecutor（子链共享委托）', () => {
  it('驱动 S10 真实 sim ≥600 tick 不崩，产出意图 trace', () => {
    const r = run(11, 600)
    expect(r.ok).toBe(true)
    expect(r.trace.length).toBeGreaterThan(0)
    for (const i of r.trace) {
      expect(i).toBeGreaterThanOrEqual(0)
      expect(i).toBeLessThan(8)
    }
  })

  it('确定性：同 seed 双跑轨迹与意图序列一致', () => {
    const a = run(42, 900)
    const b = run(42, 900)
    expect(JSON.stringify(a.trace)).toBe(JSON.stringify(b.trace))
    expect(a.state).toBe(b.state)
  })

  it('子链 override 在 replan 后生效（窗口内 God-AI 候选受限）', () => {
    const r = run(7, 600)
    // replan=30，≥600 tick 应有 ≥20 个 override 帧；至少 1 次生效。
    expect(r.overrideTicks).toBeGreaterThan(0)
  })

  it('意图 id 均 ∈ 词表', () => {
    const r = run(3, 600)
    for (const i of r.trace) expect(INTENT_IDS[i] !== undefined).toBe(true)
  })
})
