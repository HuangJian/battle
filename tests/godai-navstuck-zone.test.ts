// §168: navStuckZone — the shipped P0.3 nav-stuck escape (navStuckTicks)
// is defeated by sub-pixel center-cell jitter: playerCell() flips between
// two adjacent cells every few ticks, and the exact-cell comparison resets
// `_navStuckTicks` back to 1 before it can ever reach the 180-tick
// threshold (S34 Battlement seed 8: pinned at (5,6) ~1200 ticks, nst never
// exceeded 6, base died at t1764 with 1 kill). The ±1 zone check (the §152
// aggNavStuckTicks pattern) keeps the counter accumulating through jitter.
//
// Repro: headless S34 seed 8 (deterministic, mirrors simulation-runner
// wiring). Metric: the longest continuous dwell inside any ±1-cell zone
// without a kill. Unfixed (navStuckZone 0): ~1200 ticks. Fixed: the P0.3
// escape fires at navStuckTicks (180) and moves the player out.
import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import type { GodAIParams } from '../src/ai/god/params'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES } from '../src/config/rules'
import { CELL } from '../src/constants'
import { RNG } from '../src/utils/RNG'
import { STAGES } from '../src/config/stages'

/** Longest ±1-zone dwell without a kill during the run. When `debug` is a
 * tick threshold, prints any dwell exceeding it with its anchor cell. */
function maxZoneDwell(
  params: GodAIParams,
  stageIdx: number,
  seed: number,
  maxTicks: number,
  debug = Infinity,
): number {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard']
  world.rules = RULES['hard']
  world.playerLevel = world.difficulty.playerStartLevel ?? 0
  world.lives = world.difficulty.startLives ?? 3
  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const input = new GodAIInput(world, params, godRng)
  const sim = new Simulation(world, input)
  world.loadStageData(STAGES[stageIdx], stageIdx)
  input.reset()

  let anchorCol = -99
  let anchorRow = -99
  let dwell = 0
  let best = 0
  for (let tick = 0; tick < maxTicks; tick++) {
    sim.tick()
    const p = world.player
    if (p && p.alive && p.spawnTimer <= 0) {
      const col = Math.floor((p.x + p.w / 2) / CELL)
      const row = Math.floor((p.y + p.h / 2) / CELL)
      if (Math.abs(col - anchorCol) <= 1 && Math.abs(row - anchorRow) <= 1) {
        dwell++
      } else {
        anchorCol = col
        anchorRow = row
        dwell = 1
      }
      if (dwell > best) best = dwell
      if (dwell > debug && dwell % 100 === 0) {
        console.log(`  dwell=${dwell} anchor=(${anchorCol},${anchorRow}) t=${tick} seed=${seed}`)
      }
    } else {
      anchorCol = -99
      anchorRow = -99
      dwell = 0
    }
    input.endFrame()
    for (const e of world.consumeEvents()) {
      // A kill is progress — the P0.3 counter resets on kills, so does ours.
      if (e.type === 'tank_destroyed' && e.by === 'player') {
        dwell = 0
        anchorCol = -99
        anchorRow = -99
      }
    }
    if (world.state === 'gameover' || world.state === 'stageclear' || world.state === 'victory')
      break
  }
  return best
}

describe('§168 navStuckZone — center-cell jitter must not defeat the nav-stuck escape', () => {
  // S34 Battlement, seed 8: the traced pin (1200 ticks at (5,6), 0 kills).
  const STAGE = 33
  const SEED = 8
  // Zone detection + escape suppression window. NOT shipped: the 60-seed
  // paired A/B was net+5 but z=0.30 (flip churn 146/141) — the knob stays
  // default 0 (byte-identical). These tests keep the repro evidence and
  // guard the mechanism for a future revival (see DECISIONS §168).
  const ON = {
    ...DEFAULT_GOD_AI_PARAMS,
    navStuckZone: 1,
    navStuckSuppressTicks: 60,
    baseConnectClearMode: 0,
  }

  it('OFF (navStuckZone 0) reproduces the long pin (repro leg)', () => {
    const dwell = maxZoneDwell({ ...DEFAULT_GOD_AI_PARAMS, navStuckZone: 0, baseConnectClearMode: 0, pixelStuckDirectMoveTicks: 0 }, STAGE, SEED, 2400)
    // Unfixed behavior: the pin runs far past the 180-tick escape threshold.
    // §187 target blacklist (targetBlacklistStuckTicks=240) provides partial
    // relief even when navStuckZone=0, reducing the pin from >600 to ~564.
    expect(dwell).toBeGreaterThan(500)
  })

  it('ON (navStuckZone 1 + window) bounds the dwell — the escape fires', () => {
    const dwell = maxZoneDwell(ON, STAGE, SEED, 2400, 400)
    // The escape fires at navStuckTicks=180 and moves the player out of the
    // zone. Generous bound (well above 180, well below the 1200-tick pin):
    // a few extra ticks can pass before HUNT next evaluates after the
    // threshold is crossed, and re-pins are allowed as long as each one is
    // bounded.
    expect(dwell).toBeLessThan(500)
  })

  it('ON bounds the dwell across several Battlement losing seeds', () => {
    // Other ping-pong losses flagged by the oscillation probe (S34 s20/s10/s2).
    for (const seed of [20, 10, 2]) {
      const dwell = maxZoneDwell(ON, STAGE, seed, 3000)
      expect(dwell).toBeLessThan(600)
    }
  })

  it('zone-only (no window) also shortens the traced pin', () => {
    // Zone detection alone breaks the 1200-tick pin (traced: escape fires at
    // t748) even though the A/B showed no win-rate significance — the
    // mechanism works, the win-rate lever lies elsewhere.
    const dwell = maxZoneDwell(
      { ...DEFAULT_GOD_AI_PARAMS, navStuckZone: 1, baseConnectClearMode: 0 },
      STAGE,
      SEED,
      2400,
    )
    expect(dwell).toBeLessThan(1000)
  })
})
