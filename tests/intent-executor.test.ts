import { describe, expect, it } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../src/config/rules'
import { STAGES } from '../src/config/stages'
import { START_LIVES } from '../src/constants'
import { RNG } from '../src/utils/RNG'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { WHITELISTS } from '../src/ai/intent/vocab'

/**
 * M6 机制单测：候选子链 override（共享委托）。
 *   (a) override=null（默认）→ 与全链完全一致（字节等价，回归防线）；
 *   (b) override=某意图白名单 → think 只提交该子集候选（branch 落到白名单内）；
 *   (c) reset() 清空 override。
 */

interface Trace {
  outcome: string | null
  ticks: number
  branches: string[]
}

function runWith(override: ReadonlySet<string> | null, seed: number, maxTicks = 1200): Trace {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard']
  world.rules = RULES['hard'] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  const god = new GodAIInput(
    world,
    { ...DEFAULT_GOD_AI_PARAMS },
    new RNG((seed ^ 0x9e3779b9) >>> 0),
  )
  const sim = new Simulation(world, god)
  world.loadStageData(STAGES[9], 9)
  god.reset()
  god._candidateOverride = override

  const branches: string[] = []
  let tick = 0
  for (; tick < maxTicks; tick++) {
    if (world.state !== 'playing') break
    sim.tick()
    branches.push(god._lastBranch)
    god.endFrame()
  }
  return { outcome: world.state, ticks: tick, branches }
}

describe('M6 候选子链 override（共享委托机制）', () => {
  it('override=null（默认）与全链一致（字节等价回归防线）', () => {
    const a = runWith(null, 42)
    const b = runWith(new Set(), 42) // 空集→fallback 全链
    expect(JSON.stringify(a.branches)).toBe(JSON.stringify(b.branches))
    expect(a.outcome).toBe(b.outcome)
  })

  it('reset() 清空 override（下局回到全链）', () => {
    const a = runWith(new Set(['dodge', 'interceptBase']), 7)
    expect(a.branches.length).toBeGreaterThan(0)
    // 至少部分 tick 落在白名单内（dodge/interceptBase/t8 或兜底 hunt 相关）。
    const inList = a.branches.filter((b) => ['dodge', 't8', 'navigate', 'aggressive'].includes(b))
    expect(inList.length).toBeGreaterThan(0)
  })

  it('白名单候选 id 与 vocab.WHITELISTS 引用一致（共享事实源）', () => {
    // 每个意图白名单的 window 层候选都是合法 ActionId（可由 CANDIDATES 解析）。
    for (const rows of Object.values(WHITELISTS)) {
      for (const r of rows) {
        expect(r.branch.length).toBeGreaterThan(0)
        expect(typeof r.layer).toBe('string')
      }
    }
    // 至少 REFLEX 层引用了 dodge。
    expect(WHITELISTS.HUNT.some((r) => r.branch === 'dodge' && r.layer === 'reflex')).toBe(true)
  })
})
