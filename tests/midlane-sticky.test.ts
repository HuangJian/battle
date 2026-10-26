import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { RNG } from '../src/utils/RNG'
import { RULES } from '../src/config/rules'
import { DIFFICULTIES } from '../src/config/difficulty'
import { STAGES } from '../src/config/stages'
import { START_LIVES } from '../src/constants'

// ================================================================
// §164: mid-lane drill sticky (midLaneStickyTicks) — S8 Riverbed
// drill reproduction.
//
// Root cause (hard forensics, 37/38 S8 losses = base_destroyed): an
// enemy parked in the base column above the base (top-center spawn,
// rows 0-4, above the water band) fires down col 12-13 repeatedly.
// Each bullet chews 1-2 bricks and dies, so laneThreatImpl flickers
// ON only ~10-60 ticks per shot with 70-130 tick gaps. midLaneDefense
// releases in the gap and the player wanders away — never reaching
// the lane point. When the ring bricks finally fall, the next bullet
// has a clear ~71-tick lane to the base while the player is 6+ cells
// away (bullet 4px/tick vs player 1px/tick) — the base dies.
//
// Fix: midLaneStickyTicks — once a lane drill bullet is seen, keep
// the candidate engaged for N ticks so the player commits to walking
// to the lane point and holds through the whole drill.
// ================================================================

function runRiverbed(
  seed: number,
  params: Partial<typeof DEFAULT_GOD_AI_PARAMS>,
): {
  outcome: string
  terminalTick: number
} {
  const difficulty = 'hard'
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty]
  world.rules = RULES[difficulty]
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const input = new GodAIInput(
    world,
    {
      ...DEFAULT_GOD_AI_PARAMS,
      // A24b 纪元：本用例测自己的机制，与「残敌狩猎」无关。
      lateHuntMode: 0,
      // A23 纪元（2026-10-26）：本用例测的是自己的机制，与「转身开火」无关 ⇒ 固定回 A23 前口径。
      turnToFireMode: 0,
      ...params,
    },
    godRng,
  )
  const sim = new Simulation(world, input)
  world.loadStageData(STAGES[7], 7)
  input.reset()

  let outcome = ''
  let terminalTick = -1
  for (let t = 0; t < 36000; t++) {
    sim.tick()
    input.endFrame()
    sim.input2?.endFrame()
    world.consumeEvents()
    if (world.state === 'stageclear' || world.state === 'gameover' || world.state === 'victory') {
      outcome = world.state
      terminalTick = t
      break
    }
  }
  return { outcome, terminalTick }
}

describe('§164 S8 Riverbed drill sticky (midLaneStickyTicks) — 开火准入纪元（2026-10-20）', () => {
  // ⚠ 本用例在「开火策略准入」纪元（DECISIONS §2026-10-20-godai-mechfix-fire-admission）
  // 被改写：t2a 在冷却期不再提交 ⇒ 玩家不再蹲车道，seed 10 的钻砖死亡**不再复现**；
  // 而 sticky=90 会把玩家钉在原地，反而把它从 clear 变 gameover。
  // 新纪元 60-seed 配对 sweep（A = 旧默认 90）：0 → SUITE 0.5781（+0.0127）· 30 → 0.5747 ·
  // 60 → 0.5686 · 90 → 0.5654 ⇒ 单调下降 ⇒ 默认改为 0。
  const SEED = 10

  it('shipped default (sticky=0): seed 10 clears — the drill death is gone in this epoch', () => {
    const r = runRiverbed(SEED, {})
    expect(r.outcome).toBe('stageclear')
  })

  it('sticky=90 loses this seed — documents why the default moved back to 0', () => {
    const r = runRiverbed(SEED, { midLaneStickyTicks: 90 })
    expect(r.outcome).toBe('gameover')
    expect(r.terminalTick).toBeLessThan(10000)
  })
})
