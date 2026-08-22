import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { STAGES } from '../src/config/stages'
import { CELL, BASE_POS } from '../src/constants'
import type { Bullet } from '../src/types'
import {
  contractStandingHold,
  enemyBulletOnRay,
  ownBulletOnRay,
} from '../src/ai/god/ActionContract'
import { runSimulation } from '../tools/sim/simulation-runner'

/**
 * Phase 2 §6.1 (plan/God-AI-Hard-Breakthrough-Implementation.md):
 * 冷却期有效等待 / 无产出提交 contract tests.
 *
 * A defense branch may commit a standing no-fire hold (moveDir=null,
 * fire=false while on cooldown) only when the hold has valid waiting value:
 * enemy bullet on the held ray / own bullet resolving on the line / standing
 * shot beats the enemy's damage deadline (killSlack > 0). mode=0 → the gate
 * never runs (byte-identical).
 */

function buildWorld(stageIdx: number): World {
  const w = new World()
  w.difficultyKey = 'hard'
  w.loadStageData(STAGES[stageIdx], stageIdx)
  w.playerLevel = 1
  return w
}

/** Clear the base approach zone (rows 22-25, cols 10-25) for deterministic geometry. */
function clearBaseZone(w: World) {
  for (let r = 22; r <= 25; r++) for (let c = 10; c <= 25; c++) w.tileMap.destroy(c, r)
}

function placePlayer(w: World, col: number, row: number, dir: 'up' | 'down' | 'left' | 'right') {
  const p = w.player!
  p.x = col * CELL
  p.y = row * CELL
  p.dir = dir
  p.lastTurnMs = -9999
  p.lastFire = -9999
  return p
}

function addEnemy(w: World, col: number, row: number, kind: string = 'basic') {
  const e = w.createTank(kind as never, col * CELL, row * CELL, 'down')
  e.spawnTimer = 0
  e.alive = true
  return e
}

function addBullet(
  w: World,
  x: number,
  y: number,
  dir: 'up' | 'down' | 'left' | 'right',
  enemy = true,
  ownerId = 0,
) {
  const b: Bullet = {
    id: w.bulletSeq++,
    x,
    y,
    w: 6,
    h: 6,
    dir,
    alive: true,
    ownerId,
    ownerKind: enemy ? 'basic' : 'player',
    isPlayer: !enemy,
    allegiance: enemy ? 'enemy' : 'player',
    speed: 2,
    power: 1,
    damage: 1,
  }
  w.addBullet(b)
  return b
}

function baseCtx(over: Partial<Parameters<typeof contractStandingHold>[0]> = {}) {
  const w = buildWorld(0)
  clearBaseZone(w)
  const p = placePlayer(w, 8, 8, 'down')
  const threat = addEnemy(w, 8, 10, 'basic')
  return {
    world: w,
    player: p,
    threat,
    enemyBulletOnRay: false,
    ownBulletOnRay: false,
    ...over,
  } as Parameters<typeof contractStandingHold>[0]
}

describe('contractStandingHold (§6.1 valid waiting value)', () => {
  it('no threat → invalid (standing has no value)', () => {
    const v = contractStandingHold(baseCtx({ threat: null }))
    expect(v.valid).toBe(false)
    expect(v.reason).toContain('no threat')
  })

  it('killSlack > 0 → valid: the held shot lands before the deadline', () => {
    // Player (8,8) aligned with (8,10); enemy far from the base → deadline
    // large (walk + breach + window), killEta small (2-cell flight).
    const v = contractStandingHold(baseCtx({}))
    expect(v.valid).toBe(true)
    expect(v.reason).toContain('killSlack')
  })

  it('killSlack < 0 → invalid: csb enemy, player far on the line, on cooldown', () => {
    // Power enemy on the base row (csb after zone clear) at (8,24); player
    // at (8,0) — 24 cells of flight, just fired (full re-arm pending). The
    // standing shot cannot beat ~2 power cadences of damage window.
    const w = buildWorld(0)
    clearBaseZone(w)
    const p = placePlayer(w, 8, 0, 'down')
    p.lastFire = 0 // fired at t0 → on cooldown
    w.frame = 1
    const threat = addEnemy(w, 8, 24, 'power')
    const v = contractStandingHold({
      world: w,
      player: p,
      threat,
      enemyBulletOnRay: false,
      ownBulletOnRay: false,
    })
    expect(v.valid).toBe(false)
    expect(v.reason).toContain('killSlack')
  })

  it('enemy bullet on the held ray → valid even when the shot is hopeless', () => {
    const w = buildWorld(0)
    clearBaseZone(w)
    const p = placePlayer(w, 8, 4, 'down')
    const threat = addEnemy(w, 8, 24, 'basic')
    const v = contractStandingHold({
      world: w,
      player: p,
      threat,
      enemyBulletOnRay: true,
      ownBulletOnRay: false,
    })
    expect(v.valid).toBe(true)
    expect(v.reason).toContain('interception')
  })

  it('own bullet resolving on the threat line → valid', () => {
    const v = contractStandingHold(baseCtx({ ownBulletOnRay: true }))
    expect(v.valid).toBe(true)
    expect(v.reason).toContain('own bullet')
  })

  it('pure: consumes no World RNG', () => {
    const c = baseCtx({})
    const before = c.world.rng.getState()
    contractStandingHold(c)
    expect(c.world.rng.getState()).toBe(before)
  })
})

describe('enemyBulletOnRay / ownBulletOnRay (interception segment, protocol §5.1)', () => {
  // placePlayer(col,row) puts the 32px tank's CENTER at (col+1,row+1) — the
  // bullet helpers aim x = col*CELL + 13 so the 6px bullet centers on the
  // player's column. All bullets below are enemy bullets (speed 2 px/tick,
  // player bullet is faster — chase shots can catch).

  it('head-on: bullet above an up-facing player, heading down → true', () => {
    const w = buildWorld(0)
    clearBaseZone(w)
    const p = placePlayer(w, 8, 20, 'up')
    addBullet(w, 8 * CELL + 13, 14 * CELL, 'down', true)
    expect(enemyBulletOnRay(w, p, 'up')).toBe(true)
  })

  it('§5.1 counterexample: bullet BEHIND the player → false', () => {
    const w = buildWorld(0)
    clearBaseZone(w)
    // Up-facing player; bullet already BELOW it (between player and base,
    // wrong side of the muzzle) — the up-ray can never meet it.
    const p = placePlayer(w, 8, 20, 'up')
    addBullet(w, 8 * CELL + 13, 22 * CELL, 'down', true)
    expect(enemyBulletOnRay(w, p, 'up')).toBe(false)
  })

  it('§5.1 positive: bullet between player and base, in front of a down-facing muzzle → true', () => {
    const w = buildWorld(0)
    clearBaseZone(w)
    const p = placePlayer(w, 8, 18, 'down')
    addBullet(w, 8 * CELL + 13, 20 * CELL, 'down', true)
    expect(enemyBulletOnRay(w, p, 'down')).toBe(true)
  })

  it('§5.1 counterexample: bullet already past the base (horizontal) → false', () => {
    const w = buildWorld(0)
    clearBaseZone(w)
    // Player on the base row facing right; a right-moving shell is EAST of
    // the base zone (crossed it already) — receding, nothing to intercept.
    const p = placePlayer(w, 4, 24, 'right')
    addBullet(w, 15 * CELL, 24 * CELL + 13, 'right', true)
    expect(enemyBulletOnRay(w, p, 'right')).toBe(false)
  })

  it('§5.1 counterexample: bullet at/past the base near edge (vertical) → false', () => {
    const w = buildWorld(0)
    clearBaseZone(w)
    const p = placePlayer(w, 8, 18, 'down')
    addBullet(w, 8 * CELL + 13, BASE_POS.row * CELL, 'down', true) // y = baseTop
    expect(enemyBulletOnRay(w, p, 'down')).toBe(false)
  })

  it('bullet heading AWAY from the base → false', () => {
    const w = buildWorld(0)
    clearBaseZone(w)
    const p = placePlayer(w, 8, 20, 'up')
    addBullet(w, 8 * CELL + 13, 14 * CELL, 'up', true)
    expect(enemyBulletOnRay(w, p, 'up')).toBe(false)
  })

  it('bullet on a different column → false', () => {
    const w = buildWorld(0)
    clearBaseZone(w)
    const p = placePlayer(w, 8, 20, 'up')
    addBullet(w, 12 * CELL + 13, 14 * CELL, 'down', true)
    expect(enemyBulletOnRay(w, p, 'up')).toBe(false)
  })

  it('horizontal symmetry: in-front chase toward the base → true', () => {
    const w = buildWorld(0)
    clearBaseZone(w)
    // Player west of the shell, both heading east toward the base column.
    const p = placePlayer(w, 4, 25, 'right')
    addBullet(w, 7 * CELL, 25 * CELL + 13, 'right', true)
    expect(enemyBulletOnRay(w, p, 'right')).toBe(true)
  })

  it('horizontal symmetry: same shell behind the muzzle → false', () => {
    const w = buildWorld(0)
    clearBaseZone(w)
    const p = placePlayer(w, 4, 25, 'left')
    addBullet(w, 7 * CELL, 25 * CELL + 13, 'right', true)
    expect(enemyBulletOnRay(w, p, 'left')).toBe(false)
  })

  it('review P1: player east of the base firing left, shell crossing from the base-west → false', () => {
    const w = buildWorld(0)
    clearBaseZone(w)
    // The shell is on the FAR side of the base from the player — the player's
    // shot dies on the base (baseRight=224) before it can reach the crossing
    // point (~164). Old code returned true (interception impossible).
    const p = placePlayer(w, 16, 20, 'left')
    addBullet(w, 6 * CELL + 13, 20 * CELL + 13, 'right', true)
    expect(enemyBulletOnRay(w, p, 'left')).toBe(false)
  })

  it('review P1 mirror: player west of the base firing right, shell crossing from the base-east → false', () => {
    const w = buildWorld(0)
    clearBaseZone(w)
    const p = placePlayer(w, 4, 20, 'right')
    addBullet(w, 19 * CELL + 13, 20 * CELL + 13, 'left', true)
    expect(enemyBulletOnRay(w, p, 'right')).toBe(false)
  })

  it('review P1: same-side chase toward the base stays interceptable → true', () => {
    const w = buildWorld(0)
    clearBaseZone(w)
    // Player east of the base; shell between player and base moving west —
    // same side, catch resolves at ~236, still outside the base. The P1 gates
    // must not over-reject the legal case.
    const p = placePlayer(w, 16, 20, 'left')
    addBullet(w, 15 * CELL + 13, 20 * CELL + 13, 'left', true)
    expect(enemyBulletOnRay(w, p, 'left')).toBe(true)
  })

  it('own bullet on the ray → true (direction-agnostic, resolving-shot check)', () => {
    const w = buildWorld(0)
    clearBaseZone(w)
    const p = placePlayer(w, 8, 8, 'down')
    addBullet(w, 8 * CELL + 13, 6 * CELL, 'down', false, p.id)
    expect(ownBulletOnRay(w, p, true)).toBe(true)
    addBullet(w, 8 * CELL + 13, 4 * CELL, 'down', false, p.id + 999)
    expect(ownBulletOnRay(w, p, true)).toBe(true) // the ray check only
  })
})

describe('mode gating (byte-identical default, active only when > 0)', () => {
  it('actionContractMode 0 and undefined params → identical runs', () => {
    const a = runSimulation({
      seed: 11,
      stage: STAGES[33],
      difficulty: 'hard',
      stageIndex: 0,
      godAIParams: { ...DEFAULT_GOD_AI_PARAMS, actionContractMode: 0 },
      maxTicks: 8000,
    })
    const b = runSimulation({
      seed: 11,
      stage: STAGES[33],
      difficulty: 'hard',
      stageIndex: 0,
      godAIParams: { ...DEFAULT_GOD_AI_PARAMS },
      maxTicks: 8000,
    })
    expect(a.outcome).toBe(b.outcome)
    expect(a.ticks).toBe(b.ticks)
    expect(a.finalState).toEqual(b.finalState)
  })

  it('mode 1 actually changes behavior on the M0 no-output stage (S34 seed 11)', () => {
    const off = runSimulation({
      seed: 11,
      stage: STAGES[33],
      difficulty: 'hard',
      stageIndex: 0,
      godAIParams: { ...DEFAULT_GOD_AI_PARAMS, actionContractMode: 0 },
      maxTicks: 8000,
    })
    const on = runSimulation({
      seed: 11,
      stage: STAGES[33],
      difficulty: 'hard',
      stageIndex: 0,
      godAIParams: { ...DEFAULT_GOD_AI_PARAMS, actionContractMode: 1 },
      maxTicks: 8000,
    })
    expect(on.ticks).not.toBe(off.ticks)
  })
})
