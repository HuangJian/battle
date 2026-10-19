import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { RNG } from '../src/utils/RNG'
import { STAGES } from '../src/config/stages'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../src/config/rules'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { turnWillCommit } from '../src/ai/god/turn-gate'
import { TICK_MS, TURN_SENTINEL_MS, type Direction } from '../src/constants'

/**
 * A2 — committed-facing fire gate (plan/godai-post-a1-retune.plan.md §3.1).
 *
 * A1 (`SimulationCombat.tryFire` adjudicates the pending turn before deriving
 * the bullet direction) killed the God AI's implicit snap-aim: a "turn + fire
 * in the same tick" request inside the turn-cooldown window now emits a bullet
 * along the OLD facing — a wasted shot in the wrong lane. hard win rate fell
 * 77.7% → 58.7%.
 *
 * `turnWillCommit` is the missing predicate ("will this tick's shot fly along
 * the requested direction?") and `GodAIInput.isFiring()` gates every
 * candidate's fire decision on it. These tests pin:
 *
 *   1. the predicate against all three `applyTurnCooldown` paths (accept /
 *      accept-expired / reject-revert) — including the "turn back onto
 *      prevMoveDir" case that still commits;
 *   2. the end-to-end invariant: with the gate ON, the God AI never emits a
 *      player bullet whose direction differs from the direction it requested
 *      that tick (i.e. no shot is ever spent on a rejected turn);
 *   3. the gate is NOT vacuous: with it OFF the same runs DO contain such
 *      off-axis shots (the A1-era wasted bullets).
 */

// ---------------------------------------------------------------- helpers

/** Minimal World-shaped stub for the pure predicate (no stage, no tanks). */
function worldWith(cdMs: number, frame: number): World {
  return { rules: { turnCooldownMs: cdMs }, frame } as unknown as World
}

function tankWith(
  dir: Direction,
  prevMoveDir: Direction,
  lastTurnMs: number,
): Parameters<typeof turnWillCommit>[1] {
  return { dir, prevMoveDir, lastTurnMs } as unknown as Parameters<typeof turnWillCommit>[1]
}

/** Drive one stage with the God AI for `ticks` and count off-axis shots. */
function countOffAxisShots(stageIdx: number, seed: number, gate: number, ticks: number): number {
  const world = new World()
  world.seed = seed
  world.rng = new RNG(seed)
  world.difficulty = DIFFICULTIES['hard']
  world.rules = RULES['hard'] ?? DEFAULT_RULES
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS, turnCommitFireGate: gate })
  const sim = new Simulation(world, input)
  world.loadStageData(STAGES[stageIdx], stageIdx)
  world.state = 'playing'
  input.reset()

  let offAxis = 0
  for (let t = 0; t < ticks; t++) {
    sim.tick()
    input.endFrame()
    const p = world.player
    const events = world.consumeEvents()
    if (!p) continue
    for (const e of events) {
      if (e.type !== 'bullet_fired') continue
      if (e.bullet.ownerId !== p.id) continue
      // `lastActionDir` is the direction REQUESTED this tick (PlayerSystem sets
      // it before firing); `bullet.dir` is the COMMITTED facing after A1's
      // adjudication. They diverge exactly on a rejected turn.
      if (p.lastActionDir && e.bullet.dir !== p.lastActionDir) offAxis++
    }
    if (world.state !== 'playing') break
  }
  return offAxis
}

// ---------------------------------------------------------------- predicate

describe('turnWillCommit — exact against applyTurnCooldown paths', () => {
  const CD = 200

  it('no turn requested (dir === committed facing) always commits', () => {
    // Cooldown active but no turn: facing unchanged ⇒ the shot is aligned.
    const w = worldWith(CD, 100)
    const p = tankWith('up', 'up', 100 * TICK_MS)
    expect(turnWillCommit(w, p, 'up')).toBe(true)
    expect(turnWillCommit(w, p, null)).toBe(true)
  })

  it('turn accepted once the cooldown expires', () => {
    const p = tankWith('up', 'up', 100 * TICK_MS)
    // 5 ticks later — 83ms < 200ms ⇒ rejected.
    expect(turnWillCommit(worldWith(CD, 105), p, 'left')).toBe(false)
    // 20 ticks later — 333ms > 200ms ⇒ accepted.
    expect(turnWillCommit(worldWith(CD, 120), p, 'left')).toBe(true)
  })

  it('turn back onto prevMoveDir still commits (the revert lands on it)', () => {
    // dir = 'left' accepted, prevMoveDir still 'up'... the rejection path
    // writes `tank.dir = tank.prevMoveDir`, so requesting prevMoveDir succeeds
    // even inside the cooldown window.
    const p = tankWith('left', 'up', 100 * TICK_MS)
    expect(turnWillCommit(worldWith(CD, 105), p, 'up')).toBe(true)
    // ...while a genuinely new direction does not.
    expect(turnWillCommit(worldWith(CD, 105), p, 'right')).toBe(false)
  })

  it('never-turned tank (sentinel) and zero cooldown always commit', () => {
    expect(turnWillCommit(worldWith(CD, 1), tankWith('up', 'up', TURN_SENTINEL_MS), 'left')).toBe(
      true,
    )
    expect(turnWillCommit(worldWith(0, 1), tankWith('up', 'up', 0), 'left')).toBe(true)
  })

  it('a missing tank is inert (no crash, fire allowed)', () => {
    expect(turnWillCommit(worldWith(CD, 1), null, 'left')).toBe(true)
    expect(turnWillCommit(worldWith(CD, 1), undefined, 'left')).toBe(true)
  })
})

// ---------------------------------------------------------------- end to end

describe('A2 fire gate — no bullet is spent on a rejected turn', () => {
  // Riverbed (8) / Crossroads (16) / Frozen Field (19) are the A1 regression
  // leaders — most turning while engaging, so the gate has real work to do.
  const CASES: Array<[number, number]> = [
    [7, 42],
    [15, 42],
    [18, 42],
  ]
  const TICKS = 3000

  it('gate ON: every player bullet flies along the tick’s requested direction', () => {
    for (const [stage, seed] of CASES) {
      expect(countOffAxisShots(stage, seed, 1, TICKS)).toBe(0)
    }
  })

  it('gate OFF (A1-era): the same runs DO spend shots on rejected turns', () => {
    let total = 0
    for (const [stage, seed] of CASES) total += countOffAxisShots(stage, seed, 0, TICKS)
    expect(total).toBeGreaterThan(0)
  })
})
