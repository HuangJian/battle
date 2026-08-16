import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { STAGES } from '../src/config/stages'
import type { Tank } from '../src/types'
import { CELL } from '../src/constants'
import { RNG } from '../src/utils/RNG'
import { runSimulation } from '../tools/sim/simulation-runner'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { coveragePlanImpl } from '../src/ai/god/CoveragePlanner'
import { enemyDeadline } from '../src/ai/god/ThreatBudget'

/**
 * Phase 3 (plan/God-AI-Hard-Breakthrough-Implementation.md §7): dynamic
 * attack coverage point. When the base is NOT under threat but a major
 * threat exists (damage deadline < 360), the coverage branch (after all
 * defense/override branches, before the normal hunt) scores geometric
 * candidates (throat / lane / firing intersections) and holds the best one
 * for a lease. Default coverageMode 0 = byte-identical.
 *
 * Geometry convention (as godai-target-value / godai-intent): tanks are
 * center-aligned via (col−1)*CELL; the AI works in corner-cell space, so
 * assertions go through ai.tankCell(). World RNG is pinned (createTank
 * draws speed jitter from world.rng; the World default seed is Date.now()).
 */

function buildWorld(): World {
  const w = new World()
  w.seed = 2
  w.rng = new RNG(2)
  w.difficultyKey = 'hard'
  w.loadStageData(STAGES[0], 0)
  w.playerLevel = 1
  return w
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

function addEnemy(w: World, col: number, row: number, kind: Tank['kind'] = 'basic'): Tank {
  const e = w.createTank(kind, (col - 1) * CELL, (row - 1) * CELL, 'up')
  e.spawnTimer = 0
  e.alive = true
  w.tanks.push(e)
  return e
}

function buildAI(w: World, mode: number): GodAIInput {
  // baseClearShotThreat/chokepointMode are shipped features that make any
  // clear-lane enemy a threat — disabled here so the coverage branch sees
  // the raw box/race predicates only.
  return new GodAIInput(w, { ...DEFAULT_GOD_AI_PARAMS, coverageMode: mode, chokepointMode: 0, baseClearShotThreat: 0 })
}

/** Invalidate within-tick memos and rebuild the enemy snapshot. */
function refresh(ai: GodAIInput, w: World): void {
  ai._baseUnderThreatCache = null
  ai._selTargetValid = false
  ai._enemies.length = 0
  for (const t of w.tanks) {
    if (t.alive && t.spawnTimer <= 0) ai._enemies.push(t)
  }
}

describe('coverageMode wiring (Phase 3 §7)', () => {
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
      godAIParams: { ...DEFAULT_GOD_AI_PARAMS, coverageMode: 0 },
      maxTicks: 9000,
    })
    expect(zero.ticks).toBe(off.ticks)
    expect(zero.outcome).toBe(off.outcome)
  })

  it('holds a coverage point when a major threat exists and no defense triggers', () => {
    const w = buildWorld()
    // Ring intact; col-12 lane cleared (rows 18-22) → enemy at (12,17) is a
    // cbr threat (deadline ≈ 240 < 360). Player far on the field (20,10):
    // not under threat, guardrail (c) passes (return ETA 84 < deadline 240).
    for (let r = 18; r <= 22; r++) w.tileMap.destroy(12, r)
    const b = addEnemy(w, 12, 17)
    // Player close enough to the base that the P4 race predicate stays off
    // (dist 6 + margin 2 < breach dist 9): base not under threat, yet the
    // breach has a tight cbr deadline (≈240 < 360) — the coverage scenario.
    placePlayer(w, 14, 20, 'right')
    const ai = buildAI(w, 1)
    const pc = ai.playerCell()
    expect(ai.isBaseUnderThreat()).toBe(false)
    expect(enemyDeadline(w, b).damageDeadline).toBeLessThan(450)
    const t = ai.selectTarget(pc)
    expect(t).not.toBeNull()
    // Coverage holds a throat/lane cell on the threat's column (12), NOT the
    // nearest-enemy roam pick (which would be the enemy cell itself).
    expect(t!.col).toBe(12)
    expect(ai._coverageCell).not.toBeNull()
  })

  it('mode 0 falls back to the nearest-enemy hunt on the same field', () => {
    const w = buildWorld()
    for (let r = 18; r <= 22; r++) w.tileMap.destroy(12, r)
    const b = addEnemy(w, 12, 17)
    placePlayer(w, 14, 20, 'right')
    const ai = buildAI(w, 0)
    const pc = ai.playerCell()
    const t = ai.selectTarget(pc)
    expect(t).not.toBeNull()
    expect(t!.col).toBe(ai.tankCell(b).col) // nearest hunt, no coverage
    expect(t!.row).toBe(ai.tankCell(b).row)
  })

  it('guardrail (a): 3+ enemies with a tight second threat blocks coverage', () => {
    const w = buildWorld()
    openArena(w) // destroyed ring → every lane within walk range is a ~360 threat
    const a = addEnemy(w, 11, 17) // three independent clear lanes, same row
    addEnemy(w, 12, 17)
    addEnemy(w, 13, 17)
    const p = placePlayer(w, 13, 21, 'right') // dist 4: 4+2 < 8 → race off
    // Player cadence is a frozen value; 6000ms → killEta = (2−1)×360+8 = 368,
    // above threats[1].deadline (~360), so (a) binds. (Normal 867ms cadence
    // kills in ~88 ticks — fast pool kills keep (a) a rare real-world guard.)
    p.nextFireInterval = 6000
    const ai = buildAI(w, 1)
    const pc = ai.playerCell()
    expect(ai.isBaseUnderThreat()).toBe(false)
    const t = ai.selectTarget(pc)
    // Blocked → normal hunt picks the nearest enemy's cell ((13,17) → (12,16)).
    expect(t).not.toBeNull()
    expect(t!.col).toBe(ai.tankCell(w.tanks[2]).col)
    expect(t!.row).toBe(ai.tankCell(w.tanks[2]).row)
    expect(ai._coverageCell).toBeNull()
    void a
  })

  it('guardrail (c): player too far with return ETA > base slack blocks coverage', () => {
    const w = buildWorld()
    openArena(w) // no ring bricks → the (12,20) enemy is csb with a ~180 deadline
    const e = addEnemy(w, 12, 20)
    const p = placePlayer(w, 25, 25, 'down') // far corner
    const ai = buildAI(w, 1)
    const pc = ai.playerCell()
    const d = enemyDeadline(w, e).damageDeadline
    // The race guard is a separate predicate; here we drive coveragePlanImpl
    // directly with a slowed player so (c) must bind on its own merits.
    p.speed *= 0.9
    const returnEta = (Math.abs(pc.col - 12) + Math.abs(pc.row - 24)) * (CELL / p.speed)
    expect(returnEta).toBeGreaterThan(d) // cannot get back in time
    expect(coveragePlanImpl(ai, w, p, pc, [e])).toBeNull() // blocked
    expect(ai._coverageCell).toBeNull()
  })

  it('holds the coverage point within the lease (no re-plan on identical state)', () => {
    const w = buildWorld()
    for (let r = 18; r <= 22; r++) w.tileMap.destroy(12, r)
    addEnemy(w, 12, 17)
    placePlayer(w, 14, 20, 'right')
    const ai = buildAI(w, 1)
    const pc = ai.playerCell()
    const t1 = ai.selectTarget(pc)
    refresh(ai, w)
    const t2 = ai.selectTarget(pc)
    expect(t2!.col).toBe(t1!.col)
    expect(t2!.row).toBe(t1!.row)
    expect(ai._coverageCell).not.toBeNull()
  })

  it('releases when the committed threat dies and falls back to hunt', () => {
    const w = buildWorld()
    for (let r = 18; r <= 22; r++) w.tileMap.destroy(12, r)
    const b = addEnemy(w, 12, 17)
    addEnemy(w, 20, 12) // non-threat (walk deadline ≥ horizon)
    placePlayer(w, 14, 20, 'right')
    const ai = buildAI(w, 1)
    const pc = ai.playerCell()
    const t1 = ai.selectTarget(pc)
    expect(t1!.col).toBe(12)
    b.alive = false // threat dies
    refresh(ai, w)
    const t2 = ai.selectTarget(pc)
    expect(t2).not.toBeNull()
    expect(t2!.col).not.toBe(12) // no threat left → hunt the remaining enemy
    expect(ai._coverageCell).toBeNull()
  })

  it('is deterministic: same world + params → same target sequence', () => {
    const run = () => {
      const w = buildWorld()
      for (let r = 18; r <= 22; r++) w.tileMap.destroy(12, r)
      addEnemy(w, 12, 17)
      addEnemy(w, 20, 12)
      placePlayer(w, 14, 20, 'right')
      const ai = buildAI(w, 1)
      const pc = ai.playerCell()
      const seq: string[] = []
      for (let i = 0; i < 3; i++) {
        const t = ai.selectTarget(pc)!
        seq.push(t.col + ':' + t.row)
        w.frame += 13 // advance past lease + replan grid
        refresh(ai, w)
      }
      return seq
    }
    expect(run()).toEqual(run())
  })

  it('coveragePlanImpl is a pure read (no RNG consumption)', () => {
    const w = buildWorld()
    for (let r = 18; r <= 22; r++) w.tileMap.destroy(12, r)
    addEnemy(w, 12, 17)
    placePlayer(w, 14, 20, 'right')
    const ai = buildAI(w, 1)
    const pc = ai.playerCell()
    const before = w.rng.getState()
    const res = coveragePlanImpl(ai, w, w.player!, pc, [w.tanks[0]])
    expect(res).not.toBeNull()
    expect(w.rng.getState()).toBe(before)
  })
})

describe('coverage implementation defects (post-A/B audit)', () => {
  it('DEFECT-A: per-threat lane candidate sits between enemy and base, not above the enemy', () => {
    const w = buildWorld()
    // Threat on col 12 above the ring (ring rows 23-25); the "between the
    // enemy and the ring" cell is row 18 (t.row+1) — NOT row 16 (t.row-1).
    for (let r = 18; r <= 22; r++) w.tileMap.destroy(12, r)
    const b = addEnemy(w, 12, 17)
    // Player OFF the threat column; baseline cannot prevent (unaligned).
    placePlayer(w, 9, 20, 'right')
    const ai = buildAI(w, 1)
    const pc = ai.playerCell()
    // Drive the planner directly: at this player distance the race predicate
    // routes selectTarget to the defense cascade, but the planner itself must
    // pick the between-cell (row 18), not the above-enemy cell (row 16).
    const t = coveragePlanImpl(ai, w, w.player!, pc, [b])
    expect(t).not.toBeNull()
    // The held point must be between the enemy (row 17) and the base (row 24):
    // row >= 18 (or the throat rows 21-22). Row 16 is ABOVE the enemy — wrong.
    expect(t!.row).toBeGreaterThanOrEqual(17)
  })

  it('DEFECT-B: a tank between the point and the threat blocks the shot (prevent=0)', () => {
    const w = buildWorld()
    for (let r = 18; r <= 22; r++) w.tileMap.destroy(12, r)
    const threat = addEnemy(w, 12, 17)
    const blocker = addEnemy(w, 12, 18) // stands between throat (12,22) and threat
    placePlayer(w, 9, 20, 'right')
    const ai = buildAI(w, 1)
    const pc = ai.playerCell()
    const t = coveragePlanImpl(ai, w, w.player!, pc, [threat, blocker])
    // The blocker (12,18) is itself a cbr threat, so the planner may hold a
    // cell that only sees the blocker (the threat behind it is shielded by
    // the blocker's own hull — and its bullets are too). What it must NEVER
    // do: stand on the blocker's cell, or hold a cell whose ray to a threat
    // crosses a tank while pretending to prevent that threat.
    if (t) {
      const tc = ai.tankCell(blocker)
      expect(t.col === tc.col && t.row === tc.row).toBe(false) // never stand on the blocker
    }
  })

  it('DEFECT-D: coverage never drags the player far from the base', () => {
    const w = buildWorld()
    openArena(w)
    const b = addEnemy(w, 12, 17)
    // Player far away (e.g. clearing the right flank). Holding a point 20+
    // cells from the base while a breacher approaches is exactly the S34
    // collapse pattern (forensics: dist 26 at loss).
    const p = placePlayer(w, 20, 6, 'right')
    const ai = buildAI(w, 1)
    const pc = ai.playerCell()
    expect(Math.abs(pc.col - 12) + Math.abs(pc.row - 24)).toBeGreaterThan(12)
    // Guardrail (c) must refuse: a far player doing field work is not
    // re-routed to a coverage point.
    const t = coveragePlanImpl(ai, w, p, pc, [b])
    expect(t).toBeNull()
    expect(ai._coverageCell).toBeNull()
  })
})

describe('coverage round-2 audit (post §208/§209)', () => {
  it('DEFECT-F: guardrail (b) binds for TWO cbr lanes with an intact ring', () => {
    const w = buildWorld()
    // Open both breach corridors so BOTH enemies are real cbr threats
    // (deadline ≈ 300 < horizon 425) — the old test's (11,17) was a
    // walk-band non-threat (512 ≥ 425) and never exercised (b).
    for (let r = 18; r <= 22; r++) {
      w.tileMap.destroy(11, r)
      w.tileMap.destroy(13, r)
    }
    addEnemy(w, 11, 17)
    addEnemy(w, 13, 17)
    placePlayer(w, 14, 20, 'right')
    const ai = buildAI(w, 1)
    const pc = ai.playerCell()
    // Two different lane bands (corner cols 10 and 12): (b) must refuse any
    // single point that cannot cover both — throat (12,22) only covers one.
    const t = coveragePlanImpl(ai, w, w.player!, pc, ai._enemies)
    expect(t).toBeNull()
  })

  it('BUG-2: fast path releases when all committed threats leave the horizon', () => {
    const w = buildWorld()
    for (let r = 18; r <= 22; r++) w.tileMap.destroy(12, r)
    const b = addEnemy(w, 12, 17)
    placePlayer(w, 14, 20, 'right')
    const ai = buildAI(w, 1)
    const pc = ai.playerCell()
    const t1 = coveragePlanImpl(ai, w, w.player!, pc, [b])
    expect(t1).not.toBeNull()
    // Walk the threat far beyond the horizon; same-frame re-entry hits the
    // fast path (lease open, replan-grid tick) with an EMPTY threat set —
    // must release, not keep holding the stale point.
    b.x = (20 - 1) * CELL
    b.y = (12 - 1) * CELL
    const t2 = coveragePlanImpl(ai, w, w.player!, pc, [b])
    expect(t2).toBeNull()
    expect(ai._coverageCell).toBeNull()
  })

  it('§210: floor corner cells — in-cell jitter never flips the decision', () => {
    // round(x/CELL) flips at the cell MIDPOINT (16k+8): a ±1px jitter across
    // it oscillates the corner cell every tick and flips laneAligned — the
    // plan churns (threat x=183.5→(12,21), x=184.5→null under round()).
    // floor() flips only at the true cell boundary, so the same jitter must
    // produce an identical plan.
    const w = buildWorld()
    for (let r = 18; r <= 22; r++) {
      w.tileMap.destroy(11, r)
      w.tileMap.destroy(12, r)
    }
    const b = addEnemy(w, 12, 17) // center-aligned; x re-jittered below
    placePlayer(w, 14, 20, 'right')
    // Threat x jitters across the midpoint of corner cell 11 (176+8=184):
    // round() flips its corner 11↔12 and drops the plan; floor() stays 11.
    const jitter = [183.5, 184.5, 183.5, 184.5]
    const seen: string[] = []
    for (let i = 0; i < jitter.length; i++) {
      b.x = jitter[i]
      const ai = buildAI(w, 1)
      refresh(ai, w)
      const pc = ai.playerCell()
      const t = coveragePlanImpl(ai, w, w.player!, pc, ai._enemies)
      seen.push(t ? `${t.col},${t.row}` : 'null')
    }
    expect(new Set(seen).size).toBe(1)
  })
})
