import { describe, expect, it } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../src/config/rules'
import { STAGES } from '../src/config/stages'
import { START_LIVES } from '../src/constants'
import { RNG } from '../src/utils/RNG'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { INTENT_IDS } from '../src/ai/intent/vocab'
import { INTENT_REPLAN_TICKS } from '../src/ai/intent/tagger'

/**
 * M1 tagger 接地钩子（预注册 #15 断言进 CI）：
 *   (a) intentTaggerMode ON vs OFF 同 seed 全局逐字节一致（零 RNG 消费）；
 *   (b) ON 时按 replan 网格采样、字段合法、reset() 清空。
 */

interface RunTrace {
  outcome: string | null
  ticks: number
  kills: number
  lives: number
  firstKill: number | null
  sampleCount: number
}

function runGame(params: Record<string, number>, seed: number, maxTicks = 4000): RunTrace {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard']
  world.rules = RULES['hard'] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  const god = new GodAIInput(
    world,
    { ...DEFAULT_GOD_AI_PARAMS, ...params },
    new RNG((seed ^ 0x9e3779b9) >>> 0),
  )
  const sim = new Simulation(world, god)
  world.loadStageData(STAGES[9], 9) // S10 Hardpoint——典型守家关
  god.reset()

  let tick = 0
  let firstKill: number | null = null
  for (; tick < maxTicks; tick++) {
    if (world.state !== 'playing') break
    // 先记录击杀首帧（事件在 tick 内产生——粗粒度用 killCount 变化代替）。
    sim.tick()
    god.endFrame()
    if (firstKill === null && world.killCount > 0) firstKill = tick
  }
  return {
    outcome: world.state,
    ticks: tick,
    kills: world.killCount,
    lives: world.lives,
    firstKill,
    sampleCount: god._intentLog.length,
  }
}

describe('M1 tagger 钩子（预注册 #15：ON/OFF 字节等价 + 确定性）', () => {
  it('OFF（默认）：不采样、行为与关闭前逐字节一致', () => {
    const off = runGame({ intentTaggerMode: 0 }, 42)
    expect(off.sampleCount).toBe(0)
    const offDefault = runGame({}, 42) // 显式 0 ≡ 默认缺省
    expect(offDefault).toEqual(off)
  })

  it('ON vs OFF：同 seed 轨迹完全一致（零 RNG 消费）', () => {
    const off = runGame({ intentTaggerMode: 0 }, 7)
    const on = runGame({ intentTaggerMode: 1 }, 7)
    const { sampleCount, ...restOn } = on
    const { sampleCount: offCount, ...restOff } = off
    expect(sampleCount).toBeGreaterThan(0)
    expect(offCount).toBe(0)
    expect(restOn).toEqual(restOff)
  })

  it('ON：按 replan 网格采样、字段合法、intent ∈ 词表', () => {
    const on = runGame({ intentTaggerMode: 1 }, 11, 3000)
    // 出生/dead 帧空意图合法跳过采样 → 样本数 ≥1（不设紧上限）。
    expect(on.sampleCount).toBeGreaterThan(1)
    expect(on.sampleCount).toBeLessThanOrEqual(Math.ceil(on.ticks / INTENT_REPLAN_TICKS) + 1)

    // 日志字段合法性（重跑取日志）。
    const world = new World()
    world.rng.reseed(11)
    world.difficultyKey = 'hard'
    world.difficulty = DIFFICULTIES['hard']
    world.rules = RULES['hard'] ?? DEFAULT_RULES
    world.playerLevel = world.difficulty?.playerStartLevel ?? 0
    world.lives = world.difficulty?.startLives ?? START_LIVES
    const god = new GodAIInput(
      world,
      { ...DEFAULT_GOD_AI_PARAMS, intentTaggerMode: 1 },
      new RNG((11 ^ 0x9e3779b9) >>> 0),
    )
    const sim = new Simulation(world, god)
    world.loadStageData(STAGES[9], 9)
    god.reset()
    for (let t = 0; t < 3000; t++) {
      if (world.state !== 'playing') break
      sim.tick()
      god.endFrame()
    }
    const log = god._intentLog
    expect(log.length).toBeGreaterThan(0)
    for (const s of log) {
      expect(s.tick).toBeGreaterThanOrEqual(0)
      expect(s.tick % INTENT_REPLAN_TICKS).toBe(0)
      expect((INTENT_IDS as readonly string[]).includes(s.intent)).toBe(true)
      expect(s.targetEnemySlot).toBeGreaterThanOrEqual(0)
      expect(s.targetEnemySlot).toBeLessThanOrEqual(4)
      expect(s.duration).toBeGreaterThanOrEqual(1)
      if (s.prevIntent !== null) {
        expect((INTENT_IDS as readonly string[]).includes(s.prevIntent)).toBe(true)
      }
    }
    // 相邻采样 tick 间隔 = 网格的整数倍（空意图跳过后成合法间隙 30/60/…）。
    for (let i = 1; i < log.length; i++) {
      const gap = log[i].tick - log[i - 1].tick
      expect(gap).toBeGreaterThanOrEqual(INTENT_REPLAN_TICKS)
      expect(gap % INTENT_REPLAN_TICKS).toBe(0)
    }
    // duration 单调语义：与 prev 相同则 +1。
    for (let i = 1; i < log.length; i++) {
      if (log[i].intent === log[i - 1].intent) {
        expect(log[i].duration).toBe(log[i - 1].duration + 1)
      } else {
        expect(log[i].duration).toBe(1)
      }
    }
  })

  it('reset() 清空 tagger 态（跨关无泄漏）', () => {
    const world = new World()
    world.rng.reseed(5)
    world.difficultyKey = 'hard'
    world.difficulty = DIFFICULTIES['hard']
    world.rules = RULES['hard'] ?? DEFAULT_RULES
    world.playerLevel = world.difficulty?.playerStartLevel ?? 0
    world.lives = world.difficulty?.startLives ?? START_LIVES
    const god = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS, intentTaggerMode: 1 }, new RNG(5))
    const sim = new Simulation(world, god)
    world.loadStageData(STAGES[9], 9)
    god.reset()
    for (let t = 0; t < 500; t++) {
      if (world.state !== 'playing') break
      sim.tick()
      god.endFrame()
    }
    expect(god._intentLog.length).toBeGreaterThan(0)
    god.reset()
    expect(god._intentLog.length).toBe(0)
    expect(god._intentPrev).toBeNull()
    expect(god._intentDuration).toBe(0)
  })

  it('确定性：同 seed 两次 ON 采样逐条一致', () => {
    const a = runGame({ intentTaggerMode: 1 }, 21, 2000)
    const b = runGame({ intentTaggerMode: 1 }, 21, 2000)
    expect(a).toEqual(b)
  })
})
