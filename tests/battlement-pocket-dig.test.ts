// Bug-repro: Battlement hard seed 1 — player pinned in the spawn pocket
// because break-through fire is suppressed by a FALSE baseWall flag.
//
// Mechanism (probe-verified): at (9,22) facing up, canMoveOrBreak(up) is
// true ((9,21) is an ordinary brick — dig path returns 'up'), but the HUNT
// fire branch runs scanAheadImpl which merges BOTH offset lines: the col-10
// line passes through open (10,21)/(10,20) and hits (10,19) — a brick within
// baseWallScanRadius=5 of the base with dc=2, so the loose rectangle flags
// it baseWall=true even though it is NOT a ring cell (dr=5 > ring extent).
// shouldFireBreakThrough rejects → _fire stays false → the pocket ceiling is
// never broken → the player never leaves the spawn pocket → base_destroyed.
//
// Fix: baseWallExactRing=1 (SHIPPED default) — scanAheadImpl flags base
// walls by the EXACT ring predicate (SimulationCombat.isBaseProtectionCell),
// so the far ordinary brick no longer poisons the dual-offset merge and the
// player digs out. classic restores 0 (radius 3 — false positive impossible).
//
// Repro: seed 1, hard, Battlement (STAGES[33]). Before the fix the player
// spends ~100% of alive ticks inside the pocket zone (cols 7-11 × rows 21-25)
// and zero of its break-through attempts fire. After the fix it must leave.
import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../src/config/rules'
import { STAGES } from '../src/config/stages'

function runSeed(
  seed: number,
  maxTicks = 36000,
): { outcome: string; pocketPct: number; minRow: number } {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard'] ?? DIFFICULTIES['classic']
  world.rules = RULES['hard'] ?? DEFAULT_RULES
  const input = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS)
  const sim = new Simulation(world, input)
  world.loadStageData(STAGES[33], 0)
  input.reset()

  let pocket = 0
  let total = 0
  let minRow = 26
  let outcome = 'max_ticks'
  for (let tick = 1; tick <= maxTicks; tick++) {
    sim.tick()
    input.endFrame()
    const p = world.player
    if (p && p.alive) {
      const col = Math.round(p.x / 16)
      const row = Math.round(p.y / 16)
      total++
      // Spawn pocket: cols 7-11 × rows 21-25 (seed 1 never leaves it).
      if (col >= 7 && col <= 11 && row >= 21 && row <= 25) pocket++
      if (row < minRow) minRow = row
    }
    if (world.state === 'stageclear' || world.state === 'gameover' || world.state === 'victory') {
      outcome = world.state
      break
    }
  }
  return { outcome, pocketPct: total > 0 ? pocket / total : 1, minRow }
}

describe('Battlement hard — spawn-pocket dig (D4 regression)', () => {
  it('seed 1 must leave the spawn pocket instead of standing fireless at (9,22)', () => {
    const r = runSeed(1)
    // Before the fix: pocket 100%, player never digs up (stays rows 22-24).
    // After the fix: break-through fire works, the player digs out upward
    // (reaches rows above the pocket zone) and roams.
    expect(r.pocketPct).toBeLessThan(0.95)
    expect(r.minRow).toBeLessThan(21)
  })

  it('seed 2 also escapes the pocket', () => {
    const r = runSeed(2)
    expect(r.pocketPct).toBeLessThan(0.95)
  })
})
