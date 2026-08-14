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

function runSeed10(params: Partial<typeof DEFAULT_GOD_AI_PARAMS>): {
  outcome: string
  terminalTick: number
} {
  const seed = 10
  const difficulty = 'hard'
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty]
  world.rules = RULES[difficulty]
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS, ...params }, godRng)
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

describe('§164 S8 Riverbed drill sticky (midLaneStickyTicks)', () => {
  it('baseline (sticky=0): seed 10 loses — reproduces the drill base death', () => {
    // Pin sticky OFF explicitly: the shipped default is now 90 (hard/chaos),
    // which is exactly the fix under test. At 0 the drill chews the ring, the
    // fatal bullet wins the race — this documents the bug the sticky fixes.
    const r = runSeed10({ midLaneStickyTicks: 0 })
    expect(r.outcome).toBe('gameover')
    expect(r.terminalTick).toBeLessThan(10000)
  })

  it('sticky=90 (shipped default): seed 10 clears — the player commits to the lane point', () => {
    const r = runSeed10({ midLaneStickyTicks: 90 })
    expect(r.outcome).toBe('stageclear')
  })
})
