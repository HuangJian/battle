/**
 * Phase 2 §6.2 — targetValue (ThreatBudget) + targetValueMode wiring
 * (StrategyPlanner.selectTarget).
 *
 * Geometry notes (learned the hard way):
 *  - createTank(x, y) centers the 32px tank at x+16, y+16 — so x = col*CELL
 *    lands the CENTER in col+1. All helpers here use (col-1)*CELL to put the
 *    center exactly on (col,row).
 *  - Stage 0's base zone: ring bricks at row 23 (cols 12-13) + base cells at
 *    rows 24-25 (cols 12-13). Clearing rows 22-25 cols 10-25 removes the
 *    ring bricks and makes every aligned col-12 enemy csb.
 */
import { describe, expect, it } from 'bun:test'
import { World } from '../src/game/World'
import { STAGES } from '../src/config/stages'
import { CELL, BASE_POS } from '../src/constants'
import { targetValue, enemyDeadline } from '../src/ai/god/ThreatBudget'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { runSimulation } from '../tools/sim/simulation-runner'
import type { Tank } from '../src/types'

function buildWorld(stageIdx: number): World {
  const w = new World()
  w.difficultyKey = 'hard'
  w.loadStageData(STAGES[stageIdx], 0)
  w.playerLevel = 1
  return w
}

function clearBaseZone(w: World): void {
  for (let r = 22; r <= 25; r++) {
    for (let c = 10; c <= 25; c++) w.tileMap.destroy(c, r)
  }
}

/** Clears rows 22-25 cols 10-25 + the full col-12 lane rows 14-21. */
function laneClear(w: World): void {
  clearBaseZone(w)
  for (let r = 14; r <= 21; r++) w.tileMap.destroy(12, r)
}

function placePlayer(w: World, col: number, row: number, dir: 'up' | 'down' | 'left' | 'right'): Tank {
  const p = w.player!
  p.x = (col - 1) * CELL
  p.y = (row - 1) * CELL
  p.dir = dir
  p.lastTurnMs = -9999
  p.lastFire = -9999
  return p
}

function addEnemy(w: World, col: number, row: number, kind: 'basic' | 'fast' | 'power' | 'armor'): Tank {
  const e = w.createTank(kind, (col - 1) * CELL, (row - 1) * CELL, 'up')
  e.spawnTimer = 0
  e.alive = true
  w.tanks.push(e) // register — selectTarget scans world.tanks
  return e
}

describe('targetValue (ThreatBudget §6.2)', () => {
  it('csb enemy (clear shot at the base) beats a far non-threatening enemy', () => {
    const w = buildWorld(0)
    laneClear(w)
    const p = placePlayer(w, 8, 8, 'down')
    const csb = addEnemy(w, BASE_POS.col, 20, 'basic')
    const far = addEnemy(w, 2, 2, 'basic')
    expect(enemyDeadline(w, csb).enemyToShootEta).toBe(0)
    expect(enemyDeadline(w, far).enemyToShootEta).toBeGreaterThan(100)
    const vCsb = targetValue(w, p, csb)
    const vFar = targetValue(w, p, far)
    expect(vCsb).toBeGreaterThan(0)
    expect(vCsb).toBeGreaterThan(vFar)
  })

  it('closer enemy beats a far enemy of the same danger (same csb state)', () => {
    const w = buildWorld(0)
    laneClear(w)
    const p = placePlayer(w, 8, 20, 'down')
    const near = addEnemy(w, BASE_POS.col, 22, 'basic')
    const far = addEnemy(w, BASE_POS.col, 14, 'basic')
    expect(enemyDeadline(w, near).enemyToShootEta).toBe(0)
    expect(enemyDeadline(w, far).enemyToShootEta).toBe(0)
    expect(targetValue(w, p, near)).toBeGreaterThan(targetValue(w, p, far))
  })

  it('value rises as the enemy nears a shoot position (deadline sensitivity)', () => {
    // Real stage-0 terrain (no clearing): (12,20) is cbr — one ring brick
    // (12,23) between it and the base, enemyToShootEta = 1 cadence ≈ 57.
    // (12,12) must WALK to the ring (~157). The closer-to-shooting enemy
    // must carry the higher value.
    const w = buildWorld(0)
    const p = placePlayer(w, 8, 8, 'down')
    const atBreach = addEnemy(w, BASE_POS.col, 20, 'basic')
    const walking = addEnemy(w, BASE_POS.col, 12, 'basic')
    const dlBreach = enemyDeadline(w, atBreach)
    const dlWalking = enemyDeadline(w, walking)
    expect(dlBreach.enemyToShootEta).toBeLessThan(dlWalking.enemyToShootEta)
    expect(targetValue(w, p, atBreach)).toBeGreaterThan(targetValue(w, p, walking))
  })

  it('on-cooldown player raises the value (more interim damage accrues)', () => {
    // baseHp raised to 500 so the damage-prevented cap never flattens the
    // numerator; enemy (12,20) is cbr (e2s ≈ 57.5) on untouched stage 0.
    const w = buildWorld(0)
    w.baseHp = 500
    const p = placePlayer(w, 8, 20, 'down')
    const e = addEnemy(w, BASE_POS.col, 20, 'basic')
    expect(enemyDeadline(w, e).enemyToShootEta).toBeGreaterThan(0)
    const vReady = targetValue(w, p, e)
    p.lastFire = 0
    w.frame = 1
    const vCooldown = targetValue(w, p, e)
    expect(vCooldown).toBeGreaterThan(vReady)
  })

  it('baseHp 0 does not throw and returns a bounded value', () => {
    const w = buildWorld(0)
    laneClear(w)
    const p = placePlayer(w, 8, 8, 'down')
    const e = addEnemy(w, BASE_POS.col, 24, 'basic')
    w.baseHp = 0
    const v = targetValue(w, p, e)
    expect(Number.isFinite(v)).toBe(true)
    expect(v).toBe(0)
  })

  it('pure read-only: same world, same call → identical result, no RNG consumed', () => {
    const w = buildWorld(0)
    laneClear(w)
    const p = placePlayer(w, 8, 8, 'down')
    const e = addEnemy(w, BASE_POS.col, 24, 'basic')
    const rngBefore = JSON.stringify(w.rng.getState())
    const v1 = targetValue(w, p, e)
    const v2 = targetValue(w, p, e)
    expect(v1).toBe(v2)
    expect(JSON.stringify(w.rng.getState())).toBe(rngBefore)
  })
})

/**
 * Synthetic wiring scenario: rows 18-22 of the col-12 lane are cleared but
 * the ring brick (12,23) and base cells stay intact. An enemy centered at
 * (12,17) then has a clear line to the ring brick — cbr with one ring brick
 * between (enemyToShootEta ≈ 60, one cadence to break) — yet is OUTSIDE
 * every threat predicate: row 17 < 18 (no static box), not csb (ring brick
 * blocks), race check off with the player close (player dist 7 + margin 2
 * < breach dist 10), chokepointMode disabled. The player at (15,20) is
 * CLOSER to the harmless (15,17) enemy than to the breach, so mode 0 picks
 * the nearest while mode 1 must divert to the imminent breacher.
 * (Geometry is synthetic — (12,17) sits inside stage-0's brick corridor —
 * but selectTarget is a pure function of (world, params); the ordering
 * logic is the unit under test.)
 */
function buildCbrWorld(): World {
  const w = buildWorld(0)
  for (let r = 18; r <= 22; r++) w.tileMap.destroy(12, r)
  return w
}

describe('targetValueMode wiring (StrategyPlanner.selectTarget)', () => {
  function buildGodAI(w: World, mode: number): GodAIInput {
    return new GodAIInput(w, { ...DEFAULT_GOD_AI_PARAMS, targetValueMode: mode, chokepointMode: 0 })
  }

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
      godAIParams: { ...DEFAULT_GOD_AI_PARAMS, targetValueMode: 0 },
      maxTicks: 9000,
    })
    expect(zero.ticks).toBe(off.ticks)
    expect(zero.outcome).toBe(off.outcome)
  })

  it('selectTarget picks the higher-value target, not merely the nearest', () => {
    const w = buildCbrWorld()
    const breach = addEnemy(w, BASE_POS.col, 17, 'basic') // imminent breach
    const nearSafe = addEnemy(w, 15, 17, 'basic') // closer, harmless
    placePlayer(w, 15, 22, 'down')
    const ai = buildGodAI(w, 1)
    const pc = ai.playerCell()
    // cbr: one ring brick (12,23) between the breach enemy and the base —
    // e2s = 1 cadence (~60), versus ~142 for the walking nearSafe.
    expect(enemyDeadline(w, breach).enemyToShootEta).toBeCloseTo(60.02, 0)
    expect(enemyDeadline(w, nearSafe).enemyToShootEta).toBeGreaterThan(100)
    expect(targetValue(w, w.player!, breach)).toBeGreaterThan(0)
    expect(targetValue(w, w.player!, breach)).toBeGreaterThan(targetValue(w, w.player!, nearSafe))
    const t = ai.selectTarget(pc)
    const bCell = ai.tankCell(breach)
    expect(t).not.toBeNull()
    expect(t!.col).toBe(bCell.col)
    expect(t!.row).toBe(bCell.row)
  })

  it('mode 0 keeps nearest-first on the same field', () => {
    const w = buildCbrWorld()
    const breach = addEnemy(w, BASE_POS.col, 17, 'basic')
    const nearSafe = addEnemy(w, 15, 17, 'basic')
    placePlayer(w, 15, 22, 'down')
    void breach
    const ai = buildGodAI(w, 0)
    const pc = ai.playerCell()
    const t = ai.selectTarget(pc)
    const nsCell = ai.tankCell(nearSafe)
    expect(t).not.toBeNull()
    expect(t!.col).toBe(nsCell.col) // nearest
    expect(t!.row).toBe(nsCell.row)
    void nearSafe
  })

  it('mode 1 changes behavior on the hard field (S34 seed 11)', () => {
    const off = runSimulation({
      seed: 11,
      stage: STAGES[33],
      difficulty: 'hard',
      stageIndex: 0,
      godAIParams: { ...DEFAULT_GOD_AI_PARAMS, targetValueMode: 0 },
      maxTicks: 9000,
    })
    const on = runSimulation({
      seed: 11,
      stage: STAGES[33],
      difficulty: 'hard',
      stageIndex: 0,
      godAIParams: { ...DEFAULT_GOD_AI_PARAMS, targetValueMode: 1 },
      maxTicks: 9000,
    })
    expect(on.ticks === off.ticks && on.outcome === off.outcome).toBe(false)
  })
})
