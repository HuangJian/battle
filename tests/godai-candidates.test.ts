import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { STAGES } from '../src/config/stages'
import type { Tank } from '../src/types'
import { CELL } from '../src/constants'
import { RNG } from '../src/utils/RNG'
import { runSimulation } from '../tools/sim/simulation-runner'
import { DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import {
  evaluateUnifiedCandidates,
  makeCandidateVerdict,
  clearLaneFireDir,
  fireRayBlocked,
} from '../src/ai/god/ActionCandidates'
import { enemyDeadline } from '../src/ai/god/ThreatBudget'

/**
 * M4 unified action candidates (plan/God-AI-Hard-Open-Test-Protocol.md §7).
 *
 * Geometry convention (same as godai-intent / godai-target-value tests):
 * tanks placed center-aligned via (col−1)*CELL, so addEnemy(col,row) has
 * tankCenterCell = (col,row); ThreatBudget center-floor space throughout.
 * The base ring spans cols 11..14 at row 23 and cols 11/14 at rows 24/25.
 */

function buildWorld(): World {
  const w = new World()
  w.seed = 7
  w.rng = new RNG(7)
  w.difficultyKey = 'hard'
  w.loadStageData(STAGES[0], 0)
  w.playerLevel = 1
  return w
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

function addEnemy(w: World, col: number, row: number, kind: 'basic' | 'fast' | 'power' | 'armor' = 'basic'): Tank {
  const e = w.createTank(kind, (col - 1) * CELL, (row - 1) * CELL, 'up')
  e.spawnTimer = 0
  e.alive = true
  w.tanks.push(e)
  return e
}

function enemies(w: World): Tank[] {
  return w.tanks.filter((t) => t.alive && t.spawnTimer <= 0 && !t.isPlayer)
}

const ANCHOR = { col: 12, row: 22 }

function evaluate(w: World, hunt: Tank | null = null) {
  return evaluateUnifiedCandidates(
    w,
    w.player!,
    enemies(w),
    hunt,
    ANCHOR.col,
    ANCHOR.row,
    makeCandidateVerdict(),
  )
}

describe('M4 unified action candidates (protocol §7)', () => {
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
      godAIParams: { ...DEFAULT_GOD_AI_PARAMS, candidateMode: 0 },
      maxTicks: 9000,
    })
    expect(zero.ticks).toBe(off.ticks)
    expect(zero.outcome).toBe(off.outcome)
  })

  it('standing kill-current wins when aligned, facing, ray clear, enemy on cooldown', () => {
    const w = buildWorld()
    // Enemy on the base column with the corridor below it cleared → csb.
    for (let r = 17; r <= 23; r++) w.tileMap.destroy(12, r)
    // fast (HP 150) + player level 3 (damage 168) ⇒ one-shot kill: a standing
    // kill is flight-only, so it can beat the csb deadline minus the safety
    // margin. A basic (HP 250) always needs a 49.5-tick re-arm — no standing
    // slack is possible against a ready csb, which is the S30s26 finding.
    w.playerLevel = 3
    const e = addEnemy(w, 12, 16, 'fast')
    e.lastFire = 0 // enemy just fired — its next shot is a full cadence away
    placePlayer(w, 12, 21, 'up') // same column, facing the enemy, ray rows 17..20 clear
    const v = evaluate(w)
    expect(v.kind).toBe('killCurrent')
    expect(v.threatId).toBe(e.id)
    expect(v.firstOutputTick).toBe(0) // standing — output immediate
    expect(v.standingShot).toBe(true) // a standing commit is never re-derived
    expect(v.killSlack).toBeGreaterThan(0)
    expect(v.fireClear).toBe(true)
    expect(v.reason).toContain('standing')
  })

  it('approach kill-current commits with standingShot=false (never re-derived)', () => {
    const w = buildWorld()
    // cbr threat at (12,21) (ring brick (12,23) intact, lane row 22 clear);
    // the player is 2 cells diagonal at (11,22) — an APPROACH: zero ray, so
    // the geometric killSlack is real (movement + one turn window + flight
    // all beat the cbr deadline ≈ 110). firstOutputTick is the arrival — a
    // misclassification would treat this as a standing hold.
    w.playerLevel = 3
    for (let r = 22; r <= 22; r++) w.tileMap.destroy(12, r)
    const e = addEnemy(w, 12, 21, 'fast')
    e.lastFire = 0 // on cooldown — generous cbr deadline
    placePlayer(w, 11, 22, 'right')
    const v = evaluate(w, e)
    expect(v.kind).toBe('killCurrent')
    expect(v.standingShot).toBe(false)
    expect(v.firstOutputTick).toBeGreaterThan(0)
    expect(v.killSlack).toBeGreaterThan(0)
    expect(v.reason).toContain('approach')
  })

  it('base eagle on the ray vetoes every fire candidate (S30s27 base form)', () => {
    const w = buildWorld()
    // csb enemy at (14,24); the player holds (11,24) facing right — the ray
    // passes THROUGH the 2×2 eagle ((12,24)) before reaching the enemy.
    // fireRayBlocked treats the base as a blocker: no standing shot, no
    // aligned approach, no clear-lane (firstBrickOnRay reports 'base').
    w.playerLevel = 3
    const e = addEnemy(w, 14, 24, 'fast')
    e.lastFire = 0
    placePlayer(w, 11, 24, 'right')
    const v = evaluate(w)
    expect(v.kind).toBeNull()
    expect(v.reason).toContain('rayHit=base')
    expect(v.reason).toContain('blockedRay=true')
  })

  it('own-ring guard: a standing shot crossing an intact ring cell is vetoed (S30s27)', () => {
    const w = buildWorld()
    // Enemy csb along row 24 from the right: clear cols 13..15 on row 24
    // (including the ring cell (14,24)), but KEEP the ring brick (11,24) —
    // the player's ray to the enemy crosses it.
    for (let c = 13; c <= 16; c++) w.tileMap.destroy(c, 24)
    w.tileMap.destroy(9, 24)
    w.tileMap.destroy(10, 24)
    const e = addEnemy(w, 17, 24)
    expect(enemyDeadline(w, e).directThreat).toBe(true) // precondition: csb
    e.lastFire = 0 // generous window — the veto must come from the ring, not timing
    placePlayer(w, 8, 24, 'right')
    const v = evaluate(w)
    // The standing shot's ray crosses intact ring (11,24) → kill-current
    // standing is invalid; every other candidate's window is closed (csb
    // deadline ≈ one cadence) → the layer declines with the ring evidence.
    expect(v.kind).toBeNull()
    expect(v.reason).toContain('fireClear=false')
    expect(w.tileMap.get(11, 24)).toBe('brick') // the ring cell is intact — the guard was about a real brick
  })

  it('clear-lane: a non-ring brick on the aligned ray to a cbr threat is the commit', () => {
    const w = buildWorld()
    // cbr threat: enemy on col 12 with a clear line to the ring brick
    // (12,23) — destroy rows 13..22 on the column, then restore ONE brick at
    // (12,12) between the player (above) and the enemy. The enemy→ring line
    // (rows 18..22) stays clear, so it is cbr with the ring block charged.
    for (let r = 11; r <= 22; r++) w.tileMap.destroy(12, r)
    w.tileMap.set(12, 12, 'brick')
    const e = addEnemy(w, 12, 17)
    expect(enemyDeadline(w, e).directThreat).toBe(true) // precondition: cbr
    e.lastFire = 0 // breach deadline = fireReady + 1 breach cycle — generous
    placePlayer(w, 12, 10, 'down') // aligned, facing down, brick 2 cells ahead
    // Sanity: the approach kill/intercept cannot beat the cbr deadline from
    // 7 cells away, so clear-lane is the only open window.
    expect(clearLaneFireDir(w, w.player!, e)).toBe('down')
    const v = evaluate(w)
    expect(v.kind).toBe('clearLane')
    expect(v.threatId).toBe(e.id)
    expect(v.killSlack).toBeGreaterThan(0)
    expect(v.reason).toContain('clear-lane brick(12,12)')
  })

  it('clear-lane never targets a ring brick', () => {
    const w = buildWorld()
    for (let r = 11; r <= 22; r++) w.tileMap.destroy(12, r)
    // The ONLY brick on the player→enemy ray is the ring cell (12,23)... no —
    // put the enemy inside the ring approach: ray from row 10 to row 24
    // crosses the intact ring (12,23).
    const e = addEnemy(w, 12, 25 - 1 + 1) // (12,25)?? keep simple below
    void e
    // Direct case: player above, enemy below the ring, ring intact on the ray.
    for (let r = 18; r <= 22; r++) w.tileMap.destroy(12, r)
    const e2 = addEnemy(w, 12, 24) // in the ring pocket, csb via col 12 once (12,23) is breached
    e2.lastFire = 0
    placePlayer(w, 12, 10, 'down')
    // (12,23) ring brick is the first blocker → clearLaneFireDir returns null
    // (never clear the own ring), and the verdict cannot be clearLane.
    expect(clearLaneFireDir(w, w.player!, e2)).toBeNull()
    const v = evaluate(w)
    expect(v.kind).not.toBe('clearLane')
  })

  it('return-defense: windows closed locally, but arrival at the anchor beats every earliest', () => {
    const w = buildWorld()
    // cbr threat on cooldown 6 cells away; the player is 3 cells from the
    // anchor — kill/intercept ETAs overshoot the deadline, arrival does not.
    for (let r = 18; r <= 22; r++) w.tileMap.destroy(12, r)
    const e = addEnemy(w, 12, 17)
    expect(enemyDeadline(w, e).directThreat).toBe(true)
    e.lastFire = 0
    placePlayer(w, 10, 21, 'down') // NOT aligned with the enemy (clear-lane n/a)
    const v = evaluate(w, null)
    expect(v.kind).toBe('returnDefense')
    expect(v.threatId).toBe(-1)
    expect(v.reason).toContain('return-defense')
  })

  it('declines when there is no direct (csb/cbr) threat', () => {
    const w = buildWorld()
    const e = addEnemy(w, 5, 5) // far away, walk branch — not csb/cbr
    expect(enemyDeadline(w, e).directThreat).toBe(false)
    placePlayer(w, 15, 22, 'down')
    const v = evaluate(w)
    expect(v.kind).toBeNull()
    expect(v.reason).toContain('no direct')
  })

  it('review P1: second threat more urgent than the standing kill → commit refused', () => {
    const w = buildWorld()
    w.playerLevel = 3
    // Killable first target A (same setup as the standing kill-current test):
    // fast at (12,16) on cooldown, corridor below cleared → csb, standing kill
    // eta ≈ flight-only ≈ 20t, deadline ≈ cadence → positive slack alone.
    for (let r = 17; r <= 23; r++) w.tileMap.destroy(12, r)
    const A = addEnemy(w, 12, 16, 'fast')
    A.lastFire = 0
    // Second threat B: fast at (12,22), fire-ready — its csb deadline (a few
    // ticks of flight) passes BEFORE the kill on A lands (~20t). B is the
    // urgent threat; the standing kill-current on A (hunt target) must be
    // refused — committing it leaves B in an irreversible window (§7.2 (c)).
    const B = addEnemy(w, 12, 22, 'fast')
    B.lastFire = -9999
    placePlayer(w, 12, 21, 'up') // aligned with A (16), facing up
    const v = evaluate(w, A)
    expect(v.kind).not.toBe('killCurrent')
    expect(v.reason).toContain('secondThreat')
  })

  it('is deterministic: same world → identical verdict', () => {
    const run = () => {
      const w = buildWorld()
      for (let r = 18; r <= 23; r++) w.tileMap.destroy(12, r)
      const e = addEnemy(w, 12, 16)
      e.lastFire = 0
      placePlayer(w, 12, 21, 'up')
      return evaluate(w)
    }
    const a = run()
    const b = run()
    expect(b).toEqual(a)
  })

  it('same-center-cell player+enemy: ray walks terminate (no infinite loop)', () => {
    // Review P2 (hot-path/correctness): the old loop walked away from the
    // target cell forever when both center cells coincide (step = −1 on a
    // zero-length ray). The same-cell guard must return immediately.
    const w = buildWorld()
    const e = addEnemy(w, 12, 21)
    e.lastFire = 0
    placePlayer(w, 12, 21, 'up')
    expect(fireRayBlocked(w, w.player!, e)).toBe(false)
    expect(clearLaneFireDir(w, w.player!, e)).toBeNull()
    // Same-cell on the clear-lane evaluation path must also terminate
    // (the aligned ray is zero-length → rayHit 'none', kill gates closed
    // by the standing assessment, layer declines without hanging).
    const v = evaluate(w, e)
    expect(v.kind).not.toBe('clearLane')
  })
})
