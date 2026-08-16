import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { STAGES } from '../src/config/stages'
import type { Tank } from '../src/types'
import { CELL } from '../src/constants'
import { RNG } from '../src/utils/RNG'
import { runSimulation } from '../tools/sim/simulation-runner'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'

/**
 * Phase 2 §6.3 (plan/God-AI-Hard-Breakthrough-Implementation.md §6.3):
 * short-term action intent. selectTarget commits a hunt target only for a
 * lease (intentLeaseTicks); revalidation releases on target death, lease
 * expiry, player stall (no move + no fire), deadline tightening, a clearly
 * worse new threat, or target flight. Default intentMode 0 = byte-identical.
 *
 * Geometry convention (same as godai-target-value.test.ts): tanks are
 * center-aligned via (col−1)*CELL; the AI works in corner-cell space
 * (floor(x/16)), so assertions go through ai.tankCell(tank). The threat
 * predicates (static box |col−12|≤3 && row≥18, P4 race) are kept off by
 * keeping enemies' corner cells at row ≤ 17 and the player close to the base.
 */

function buildWorld(): World {
  const w = new World()
  pinSeed(w)
  w.difficultyKey = 'hard'
  w.loadStageData(STAGES[0], 0)
  w.playerLevel = 1
  return w
}

/** Pin the World RNG (default is Date.now()): createTank draws speed jitter
 * from world.rng, so unseeded worlds make deadline math nondeterministic
 * across runs. Seed 2 keeps the A/C deadline gap (11.2 ticks) comfortably
 * under the INTENT_THREAT_DELTA release boundary (15). */
function pinSeed(w: World) {
  w.seed = 2
  w.rng = new RNG(2)
}

/** Open arena below row 14: destroy ring + corridor bricks. */
function openArena(w: World) {
  for (let r = 14; r <= 25; r++) for (let c = 8; c <= 20; c++) w.tileMap.destroy(c, r)
}

function placePlayer(w: World, col: number, row: number, dir: 'up' | 'down' | 'left' | 'right') {
  const p = w.player!
  p.x = (col - 1) * CELL
  p.y = (row - 1) * CELL
  p.dir = dir
  p.lastTurnMs = -9999
  p.lastFire = -9999
  p.fireCooldown = 0
  return p
}

function addEnemy(w: World, col: number, row: number): Tank {
  const e = w.createTank('basic', (col - 1) * CELL, (row - 1) * CELL, 'up')
  e.spawnTimer = 0
  e.alive = true
  w.tanks.push(e)
  return e
}

function buildAI(w: World, mode: number): GodAIInput {
  return new GodAIInput(w, { ...DEFAULT_GOD_AI_PARAMS, intentMode: mode })
}

/** Invalidate the within-tick memos and rebuild the enemy snapshot (same
 * pattern as tests/godai-hunt-commit.test.ts). */
function refresh(ai: GodAIInput, w: World): void {
  ai._baseUnderThreatCache = null
  ai._selTargetValid = false
  ai._enemies.length = 0
  for (const t of w.tanks) {
    if (t.alive && t.spawnTimer <= 0) ai._enemies.push(t)
  }
}

describe('intentMode wiring (Phase 2 §6.3)', () => {
  it('mode 0 === no param (byte-identical, S34 seed 1)', () => {
    const off = runSimulation({
      seed: 1,
      stage: STAGES[33],
      difficulty: 'hard',
      stageIndex: 0,
      godAIParams: { ...DEFAULT_GOD_AI_PARAMS },
      maxTicks: 9000,
    })
    const zero = runSimulation({
      seed: 1,
      stage: STAGES[33],
      difficulty: 'hard',
      stageIndex: 0,
      godAIParams: { ...DEFAULT_GOD_AI_PARAMS, intentMode: 0 },
      maxTicks: 9000,
    })
    expect(zero.ticks).toBe(off.ticks)
    expect(zero.outcome).toBe(off.outcome)
  })

  it('holds the committed target within the lease even when a closer enemy appears', () => {
    const w = buildWorld()
    openArena(w)
    const a = addEnemy(w, 15, 17) // committed: nearest (dist 5)
    addEnemy(w, 10, 12) // farther + non-urgent (outside csb/cbr band)
    placePlayer(w, 15, 22, 'down')
    const ai = buildAI(w, 1)
    const pc = ai.playerCell()
    const t1 = ai.selectTarget(pc)
    expect(t1).not.toBeNull()
    expect(t1!.col).toBe(ai.tankCell(a).col)
    expect(t1!.row).toBe(ai.tankCell(a).row)
    const c = addEnemy(w, 15, 18) // NEW closer (dist 4), harmless
    void c
    refresh(ai, w)
    const t2 = ai.selectTarget(pc)
    expect(t2!.col).toBe(ai.tankCell(a).col) // intent still holds
    expect(t2!.row).toBe(ai.tankCell(a).row)
  })

  it('mode 0 re-picks the new nearest enemy on the same field', () => {
    const w = buildWorld()
    openArena(w)
    const a = addEnemy(w, 15, 17)
    addEnemy(w, 10, 12)
    placePlayer(w, 15, 22, 'down')
    const ai = buildAI(w, 0)
    const pc = ai.playerCell()
    ai.selectTarget(pc)
    const c = addEnemy(w, 15, 18)
    refresh(ai, w)
    const t = ai.selectTarget(pc)
    expect(t).not.toBeNull()
    expect(t!.col).toBe(ai.tankCell(c).col) // nearest-first (no intent)
    expect(t!.row).toBe(ai.tankCell(c).row)
    void a
  })

  it('releases on lease expiry and re-picks', () => {
    const w = buildWorld()
    openArena(w)
    const a = addEnemy(w, 15, 17)
    addEnemy(w, 10, 12)
    const c = addEnemy(w, 15, 18)
    const p = placePlayer(w, 15, 22, 'down')
    const ai = buildAI(w, 1)
    const pc = ai.playerCell()
    const t1 = ai.selectTarget(pc)
    expect(t1!.col).toBe(ai.tankCell(a).col)
    // Advance past the lease; keep the stall check inert by marking a recent
    // shot (fireCooldown > 0) so the release is purely lease-driven.
    w.frame += DEFAULT_GOD_AI_PARAMS.intentLeaseTicks + 1
    p.fireCooldown = 50
    refresh(ai, w)
    const t2 = ai.selectTarget(pc)
    expect(t2!.col).toBe(ai.tankCell(c).col)
    expect(t2!.row).toBe(ai.tankCell(c).row)
  })

  it('releases when the committed target dies', () => {
    const w = buildWorld()
    openArena(w)
    const a = addEnemy(w, 15, 17)
    addEnemy(w, 10, 12)
    const c = addEnemy(w, 15, 18)
    placePlayer(w, 15, 22, 'down')
    const ai = buildAI(w, 1)
    const pc = ai.playerCell()
    ai.selectTarget(pc)
    a.alive = false // target killed
    refresh(ai, w)
    const t = ai.selectTarget(pc)
    expect(t!.col).toBe(ai.tankCell(c).col)
  })

  it('releases when a clearly more urgent threat appears (new-threat revalidation)', () => {
    const w = buildWorld()
    // Keep the ring brick (12,23): breach B at (12,17) has e2s ≈ 60 (cbr),
    // a tight damage deadline; committed A has a huge walk deadline.
    for (let r = 18; r <= 22; r++) w.tileMap.destroy(12, r)
    const a = addEnemy(w, 15, 17)
    placePlayer(w, 15, 22, 'down')
    const ai = buildAI(w, 1)
    const pc = ai.playerCell()
    const t1 = ai.selectTarget(pc)
    expect(t1!.col).toBe(ai.tankCell(a).col)
    const b = addEnemy(w, 12, 17) // imminent breacher — deadline ≪ A's
    const c = addEnemy(w, 15, 18) // closer harmless — the release re-picks it
    void b
    void c
    refresh(ai, w)
    const t2 = ai.selectTarget(pc)
    expect(t2).not.toBeNull()
    expect(t2!.col).toBe(ai.tankCell(c).col) // released, re-picked nearest
  })

  it('releases when the committed target flees beyond expectedProgress', () => {
    const w = buildWorld()
    openArena(w)
    const a = addEnemy(w, 15, 17)
    addEnemy(w, 10, 12)
    const c = addEnemy(w, 15, 18)
    placePlayer(w, 15, 22, 'down')
    const ai = buildAI(w, 1)
    const pc = ai.playerCell()
    ai.selectTarget(pc)
    a.x = (25 - 1) * CELL // fled across the field (dist 15 > 5 + 2)
    refresh(ai, w)
    const t = ai.selectTarget(pc)
    expect(t!.col).toBe(ai.tankCell(c).col)
  })

  it('is deterministic: same world + params → same target sequence', () => {
    const run = () => {
      const w = buildWorld()
      openArena(w)
      const a = addEnemy(w, 15, 17)
      const b = addEnemy(w, 10, 12)
      const c = addEnemy(w, 15, 18)
      placePlayer(w, 15, 22, 'down')
      const ai = buildAI(w, 1)
      const pc = ai.playerCell()
      const seq: string[] = []
      for (let i = 0; i < 3; i++) {
        const t = ai.selectTarget(pc)!
        seq.push(t.col + ':' + t.row)
        if (i === 0) {
          a.alive = false
          b.x = (3 - 1) * CELL
          c.y = (12 - 1) * CELL
          refresh(ai, w)
        }
      }
      return seq
    }
    expect(run()).toEqual(run())
  })
})
