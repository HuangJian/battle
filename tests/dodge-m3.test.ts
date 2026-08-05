import { describe, it, expect } from 'bun:test'
import { World, genId } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import {
  dodgeDirectionImpl,
  dodgeCounterFireDirImpl,
  isTerrainPinnedImpl,
} from '../src/ai/god/ThreatAssessor'
import { CELL, BULLET, GRID } from '../src/constants'
import type { Bullet } from '../src/types'

/**
 * §M3 (plan/God-AI-Redesign-v2 M3): dodge-quality features — unit tests.
 *
 * Both features were measured and then REVERTED to OFF (DECISIONS §98):
 * official-shape 35×20 showed no chaos gain for `dodgeCounterFire` (with a
 * deterministic S26 Ice Palace regression 5/20→1/20), and `dodgeClearanceScore`
 * was -0.6pp on hard. They remain as reserved experimental knobs; these tests
 * lock the function-level behavior so a future M3.5 tuning round can reuse them.
 */

function setupWorld(): { world: World; input: GodAIInput } {
  const world = new World()
  world.rng = new RNG(42)
  // Explicit clone (NOT the DEFAULT singleton): mutating input.params below
  // for the clearance-score cases must not leak into the shared
  // DEFAULT_GOD_AI_PARAMS (cross-file module state is shared in bun test —
  // DECISIONS §98). GodAIInput also clones internally since 2026-08-03.
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS })
  const sim = new Simulation(world, new Input())
  world.startGame('classic', 'modern', 0)
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) world.tileMap.grid[r][c] = 'empty'
  }
  for (const r of [24, 25]) {
    for (const c of [12, 13]) world.tileMap.grid[r][c] = 'base'
  }
  void sim
  return { world, input }
}

function makeBullet(x: number, y: number, dir: Bullet['dir'], speed = 4): Bullet {
  return {
    id: genId(),
    x,
    y,
    w: BULLET,
    h: BULLET,
    dir,
    alive: true,
    ownerId: -1,
    ownerKind: 'fast',
    isPlayer: false,
    allegiance: 'enemy',
    speed,
    power: 1,
    damage: 1,
  }
}

function positionPlayer(world: World, x: number, y: number): void {
  const p = world.player!
  p.x = x
  p.y = y
  // Clear spawn timer and shield timer so the AI can think + see threats
  p.spawnTimer = 0
  p.shieldTimer = 0
}

// Player center at (col 8, row 10) = (144, 176)
// player.x = 8 * CELL = 128, center = 128 + 16 = 144
// player.y = 10 * CELL = 160, center = 160 + 16 = 176
const PCX = 8 * CELL + CELL // 144
const PCY = 10 * CELL + CELL // 176

describe('§M3 dodgeCounterFireDirImpl', () => {
  it('returns the facing dir for a dead-on aligned bullet with a clear lane', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()

    // Bullet traveling DOWN, dead-on with the player column (center 136),
    // 40px above the player center.
    const bullet = makeBullet(PCX - BULLET / 2, 128 - BULLET / 2, 'down')
    world.bullets.push(bullet)

    const fireDir = dodgeCounterFireDirImpl(input, bullet, PCX, PCY)
    expect(fireDir).toBe('up')
  })

  it('returns null when the bullet is off-center beyond the align gate', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()

    // Bullet 10px off the player column — the player's 6px bullet would pass
    // beside it (no cancellation).
    const bullet = makeBullet(PCX + 10 - BULLET / 2, 128 - BULLET / 2, 'down')
    world.bullets.push(bullet)

    expect(dodgeCounterFireDirImpl(input, bullet, PCX, PCY)).toBeNull()
  })

  it('returns null when a wall blocks the lane before the bullet', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()

    // Brick between player (row 10) and bullet (row 8): cell (9,10).
    // Player center at (144, 176), bullet at (141, 148). The upward scan
    // passes through cells at col 9 (floor(144/16) = 9).
    world.tileMap.grid[10][9] = 'brick'
    const bullet = makeBullet(PCX - BULLET / 2, 128 - BULLET / 2, 'down')
    world.bullets.push(bullet)

    expect(dodgeCounterFireDirImpl(input, bullet, PCX, PCY)).toBeNull()
  })

  it('does NOT trigger on a horizontal bullet unless aligned on the y-axis', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()

    // Bullet traveling RIGHT, dead-on row (center 168), 40px left of player.
    const bullet = makeBullet(128 - BULLET / 2, PCY - BULLET / 2, 'right')
    world.bullets.push(bullet)

    const fireDir = dodgeCounterFireDirImpl(input, bullet, PCX, PCY)
    expect(fireDir).toBe('left')
  })
})

describe('§M3 params pollution guard (DECISIONS §98)', () => {
  it('constructing with the DEFAULT singleton and mutating input.params does NOT pollute the singleton', () => {
    // Regression guard for the class of bug that flipped the hard/chaos gate
    // (S26 1/20 → 0/20 in the full suite): `new GodAIInput(world)` stores a
    // CLONE now, so `input.params.x = y` (an A/B pattern used across tests)
    // must never touch DEFAULT_GOD_AI_PARAMS — cross-file module state IS
    // shared inside `bun test`.
    const world = new World()
    const input = new GodAIInput(world) // DEFAULT singleton param
    input.params.dodgeClearanceScore = 1
    input.params.dodgeCounterFire = 1
    expect(DEFAULT_GOD_AI_PARAMS.dodgeClearanceScore).toBe(0)
    expect(DEFAULT_GOD_AI_PARAMS.dodgeCounterFire).toBe(0)
  })
})

describe('§M3-revisit isTerrainPinnedImpl (round 3 — terrain-only pinning, DECISIONS §101)', () => {
  // Round-3 semantics: ONLY impassable terrain pins. Timing pressure (a
  // bullet too close to fully clear) NEVER pins — on open ground a partial
  // dodge keeps the player mobile and beats standing to counter-fire
  // (Twin Spires/Bastion/Final Redoubt regressed under round-2's
  // timing-aware gate).

  it('open field, far bullet → NOT pinned', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()
    const p = world.player!
    p.speed = 1

    const bullet = makeBullet(PCX - BULLET / 2, 85 - BULLET / 2, 'down')
    world.bullets.push(bullet)

    expect(isTerrainPinnedImpl(input, p, bullet)).toBe(false)
  })

  it('open field, bullet about to hit (timing-impossible dodge) → NOT pinned (round-3 core)', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()
    const p = world.player!
    p.speed = 1

    // Bullet 30px above — a full 19px lateral clear cannot complete before
    // arrival (7.5 ticks). Round-2 called this pinned and stood to fire;
    // round-3 keeps the (partial) dodge moving — open ground is never pinned.
    const bullet = makeBullet(PCX - BULLET / 2, 135 - BULLET / 2, 'down')
    world.bullets.push(bullet)

    expect(isTerrainPinnedImpl(input, p, bullet)).toBe(false)
  })

  it('both perpendicular sides walled → pinned regardless of timing', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()
    const p = world.player!
    p.speed = 1

    // Wall the cells the tank would move INTO: left → cols 7-8 (col 7 is the
    // new cell), right → cols 9-10 (col 10 is the new cell). Player footprint
    // is cols 8-9 at rows 10-11 (x=128, y=160).
    for (const [c, r] of [
      [7, 10],
      [7, 11],
      [10, 10],
      [10, 11],
    ]) {
      world.tileMap.grid[r][c] = 'brick'
    }
    input._canMoveComputed = 0 // clear per-tick canMoveDir cache

    // Far bullet (feasible timing) — still pinned by the walls.
    const bullet = makeBullet(PCX - BULLET / 2, 65 - BULLET / 2, 'down')
    world.bullets.push(bullet)

    expect(isTerrainPinnedImpl(input, p, bullet)).toBe(true)
  })

  it('crossfire coverage alone does NOT pin — the player keeps dodging', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()
    const p = world.player!
    p.speed = 1

    // Threat: vertical bullet coming down at the player's column.
    const threat = makeBullet(PCX - BULLET / 2, 85 - BULLET / 2, 'down')
    world.bullets.push(threat)
    // Crossfire: another bullet threatens BOTH perpendicular new cells.
    const crossfire = makeBullet(100 - BULLET / 2, PCY - BULLET / 2, 'right')
    world.bullets.push(crossfire)

    expect(isTerrainPinnedImpl(input, p, threat)).toBe(false)
  })

  it('mid-maneuver offset (S26 case) → NOT pinned: open ground stays dodging', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 138, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()
    const p = world.player!
    p.speed = 1

    const bullet = makeBullet(133, 85 - BULLET / 2, 'down')
    world.bullets.push(bullet)

    expect(isTerrainPinnedImpl(input, p, bullet)).toBe(false)
  })
})

describe('§M4 dodgeEmergencyFire — 安全门控单一威胁紧急对枪 (DECISIONS §102)', () => {
  it('close bullet, no crossfire, dodgeCounterFire=1 → fires toward threat (站点对枪)', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()
    input.params.dodgeCounterFire = 1

    // Bullet 5 cells (80px) above player center — within the 5-cell
    // emergency threshold, dodge mathematically infeasible (18+ tick needed).
    const threat = makeBullet(PCX - BULLET / 2, PCY - 80 - BULLET / 2, 'down')
    world.bullets.push(threat)

    const dir = input.getMoveDirection()
    // Player faces up (default), and fireDir is up (toward threat) →
    // _moveDir = null (already facing up, stop to fire).
    expect(dir).toBeNull()
    expect(input.isFiring()).toBe(true)
    expect(input._counterFireTicks).toBe(1)
  })

  it('close bullet, no crossfire, facing away → moves toward threat and fires', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()
    input.params.dodgeCounterFire = 1
    // Set player facing down (away from the threat)
    world.player!.dir = 'down'

    // Bullet 5 cells above
    const threat = makeBullet(PCX - BULLET / 2, PCY - 80 - BULLET / 2, 'down')
    world.bullets.push(threat)

    const dir = input.getMoveDirection()
    // Player faces down, fireDir is up → move up to face + fire.
    expect(dir).toBe('up')
    expect(input.isFiring()).toBe(true)
    expect(input._counterFireTicks).toBe(1)
  })

  it('close bullet WITH crossfire → keeps vertical dodge (does NOT counter-fire)', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()
    input.params.dodgeCounterFire = 1

    // Threat: bullet coming down from above, 5 cells out.
    const threat = makeBullet(PCX - BULLET / 2, PCY - 80 - BULLET / 2, 'down')
    world.bullets.push(threat)

    // Crossfire: another bullet approaching from the left, within 5 cells.
    const crossfire = makeBullet(PCX - 70 - BULLET / 2, PCY - BULLET / 2, 'right')
    world.bullets.push(crossfire)

    const dir = input.getMoveDirection()
    // Must NOT counter-fire (crossfire active) — keep vertical dodge.
    // The dodge direction should be perpendicular to the threat (left/right).
    // When both perpendicular are unsafe, falls back to any passable direction.
    expect(dir).not.toBeNull()
    // Should NOT be threat-facing (up) — that would be emergency fire mode.
    expect(dir).not.toBe('up')
  })

  it('far bullet (>5 cells) → normal vertical dodge, no emergency fire', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()
    input.params.dodgeCounterFire = 1

    // Bullet 8 cells (128px) above — well within normal dodge range.
    const threat = makeBullet(PCX - BULLET / 2, PCY - 140 - BULLET / 2, 'down')
    world.bullets.push(threat)

    const dir = input.getMoveDirection()
    // Normal perpendicular dodge (left/right), not threat-facing.
    expect(dir).not.toBeNull()
    expect(dir === 'up' || dir === 'down').toBe(false)
  })

  it('close bullet, onCooldown=true → normal vertical dodge (no fire)', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()
    input.params.dodgeCounterFire = 1

    // Force cooldown for bulletCap mode: set player's bullet in-flight count to cap.
    // In classic mode, cooldown = in-flight >= cap. With 0★, cap = 1.
    const p = world.player!
    // Create a player bullet that's alive to fill the cap.
    const playerBullet = makeBullet(PCX, 0, 'up', 4)
    playerBullet.isPlayer = true
    playerBullet.ownerId = p.id
    world.bullets.push(playerBullet)

    const threat = makeBullet(PCX - BULLET / 2, PCY - 80 - BULLET / 2, 'down')
    world.bullets.push(threat)

    const dir = input.getMoveDirection()
    // On cooldown (bullet cap reached) → can't fire, must keep perpendicular dodge.
    expect(dir).not.toBeNull()
    expect(dir === 'up' || dir === 'down').toBe(false)
    expect(input.isFiring()).toBe(false)
  })

  it('close bullet, off-center beyond align gate → no emergency fire, perpendicular dodge', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()
    input.params.dodgeCounterFire = 1

    // Bullet 10px off the player column — dodgeCounterFireDirImpl returns null.
    const threat = makeBullet(PCX + 10 - BULLET / 2, PCY - 80 - BULLET / 2, 'down')
    world.bullets.push(threat)

    const dir = input.getMoveDirection()
    expect(dir).not.toBeNull()
    expect(dir === 'up' || dir === 'down').toBe(false)
  })

  it('default (dodgeCounterFire=0) → no emergency fire, byte-identical to M0', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()

    // Close bullet, no crossfire — but dodgeCounterFire=0 by default.
    const threat = makeBullet(PCX - BULLET / 2, PCY - 80 - BULLET / 2, 'down')
    world.bullets.push(threat)

    const dir = input.getMoveDirection()
    expect(dir).not.toBeNull()
    expect(dir === 'up' || dir === 'down').toBe(false)
    expect(input._counterFireTicks).toBe(0)
  })
})

describe('§M3 dodgeCounterFire stays OFF at default (DECISIONS §101)', () => {
  it('default params never trigger counter-fire — _counterFireTicks stays 0', () => {
    // Regression guard: the think() counter-fire branch is gated on
    // dodgeCounterFire > 0. At the shipped default (0) it must never fire,
    // so the M3 wiring (isTerrainPinned + _counterFireTicks) is byte-inert
    // and the gate stays byte-identical to M0.
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()
    // A dead-on bullet that WOULD trigger counter-fire if the param were on.
    const bullet = makeBullet(PCX - BULLET / 2, 60 - BULLET / 2, 'down')
    world.bullets.push(bullet)
    input.getMoveDirection()
    expect(input._counterFireTicks).toBe(0)
    expect(input.params.dodgeCounterFire).toBe(0)
  })
})

describe('§M3 dodgeClearanceScore candidate scoring', () => {
  it('picks the perpendicular side with more bullet clearance', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()
    input.params.dodgeClearanceScore = 1

    // Threat: vertical bullet coming down at the player's column.
    const threat = makeBullet(PCX - BULLET / 2, 3 * CELL, 'down')
    world.bullets.push(threat)

    // Another bullet threatens the LEFT new cell (player would move to
    // x=120): traveling RIGHT at the player's row, 20px left of x=120 →
    // ~5 ticks until it crosses the new cell. The RIGHT side has no bullets
    // (clearance Infinity) → the scored path must pick right.
    const crossfire = makeBullet(100 - BULLET / 2, PCY - BULLET / 2, 'right')
    world.bullets.push(crossfire)

    const dodge = dodgeDirectionImpl(input, threat, PCX, PCY)
    expect(dodge).toBe('right')
  })

  it('falls back to the pinned logic when neither perpendicular is passable', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()
    input.params.dodgeClearanceScore = 1

    // Wall the left and right neighbors of the player's 2×2 footprint.
    for (const [c, r] of [
      [6, 10],
      [6, 11],
      [10, 10],
      [10, 11],
    ]) {
      world.tileMap.grid[r][c] = 'brick'
    }
    input._canMoveComputed = 0 // clear per-tick canMoveDir cache

    const threat = makeBullet(PCX - BULLET / 2, 3 * CELL, 'down')
    world.bullets.push(threat)

    // Neither perpendicular passable → the §83 pinned fallback must produce
    // SOME direction (never null on an open field).
    const dodge = dodgeDirectionImpl(input, threat, PCX, PCY)
    expect(dodge).not.toBeNull()
  })
})

describe('§M9 dodgeHorizonScore commitment scoring (DECISIONS §107)', () => {
  // M9 measured the dominant dodge-death failure mode: COMMITMENT FAILURE —
  // hard 31.8% / chaos 35.0% of dodge-branch deaths are escapable at the
  // start of the window, yet the player oscillates inside the bullet's hit
  // band forever (the binary next-cell isSafeDir + base-closer tie-break
  // never commits to clearing the band; S1 seed2 oscillated 30+ ticks within
  // a 32px band while the bullet closed from 36 ticks away). dodgeHorizonScore
  // scores each perpendicular candidate by its survival horizon (Infinity
  // when the terrain-limited free path clears the band before t_arrive) and
  // COMMITS to the longer-horizon side. 60-seed A/B (chaos): OFF 47.7% vs ON
  // 44.2% (Δ-73, -3.5pp) — survival improved but base-defense/kill efficiency
  // regressed → default OFF (0), reserved as an experimental knob.

  it('defaults to OFF (0) — never active without an explicit A/B override', () => {
    expect(DEFAULT_GOD_AI_PARAMS.dodgeHorizonScore).toBe(0)
  })

  it('commits to the escape side when the base-closer side is walled (legacy picks base-closer)', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()

    // Threat: vertical bullet dead-on the player column, ~30 ticks away.
    const threat = makeBullet(PCX - BULLET / 2, 3 * CELL, 'down')
    world.bullets.push(threat)

    // Legacy (default) path: both perpendiculars passable+safe → the shared
    // base-closer tail picks RIGHT (toward the base at cols 12-13).
    expect(dodgeDirectionImpl(input, threat, PCX, PCY)).toBe('right')

    // Horizon ON: a brick 2 cells right (col 11, rows 10-11) caps the right
    // free path at 16px < the 20px band — the right side can NOT clear the
    // band before the bullet arrives (horizon = t_arrive); the left side
    // escapes (∞) → the committed escape picks LEFT over base-closer RIGHT.
    for (const [c, r] of [
      [11, 10],
      [11, 11],
    ]) {
      world.tileMap.grid[r][c] = 'brick'
    }
    input._canMoveComputed = 0 // clear the per-tick canMoveDir cache
    input.params.dodgeHorizonScore = 1

    expect(dodgeDirectionImpl(input, threat, PCX, PCY)).toBe('left')
  })

  it('counts next-cell crossfire coverage: dodging INTO a lane scores finite', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()
    input.params.dodgeHorizonScore = 1

    const threat = makeBullet(PCX - BULLET / 2, 3 * CELL, 'down')
    world.bullets.push(threat)

    // Crossfire traveling RIGHT along the player's row, 24px left of the LEFT
    // next-cell center (128) → covers it in 2 ticks; the RIGHT side is only
    // threatened by the dodge threat's own lane. Under the M10 escape-margin
    // semantics BOTH perpendiculars are doomed (crossfire hits either side at
    // the same t_arrive, so both margins ≤ 0) → the commitment gate fails and
    // the legacy base-closer tail picks RIGHT — the same safe side the §M3
    // clearance scoring would have chosen. The conservative next-cell counting
    // (a lane covered by another bullet must NOT be dodged into) holds either
    // way.
    const crossfire = makeBullet(120 - BULLET / 2, PCY - BULLET / 2, 'right')
    world.bullets.push(crossfire)

    expect(dodgeDirectionImpl(input, threat, PCX, PCY)).toBe('right')
  })
})

describe('§M10 dodgeHorizon gates (DECISIONS §108)', () => {
  // The ungated M9 horizon commitment traded away base-defense/kill
  // efficiency (S11 seed6: 0 deaths but base destroyed while the player fled
  // 142px away). M10 gates the commitment: only commit when the escape margin
  // is clearly winnable (dodgeHorizonMinMarginTicks) AND the player is not far
  // from the base (dodgeHorizonMaxDistCells); otherwise fall back to the
  // legacy binary path (which returns the base-closer side here).

  // Shared asymmetric setup: brick 2 cells right (col 11) caps the right free
  // path at 16px < the 20px band → left escapes with ~13 ticks margin, right
  // gets hit (-t_arrive). Ungated horizon commits LEFT; the legacy binary
  // path returns the base-closer RIGHT.
  function asymmetricSetup(): { input: GodAIInput; threat: Bullet } {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()
    for (const [c, r] of [
      [11, 10],
      [11, 11],
    ]) {
      world.tileMap.grid[r][c] = 'brick'
    }
    input._canMoveComputed = 0 // clear the per-tick canMoveDir cache
    const threat = makeBullet(PCX - BULLET / 2, 3 * CELL, 'down')
    world.bullets.push(threat)
    input.params.dodgeHorizonScore = 1
    return { input, threat }
  }

  it('time-margin gate: a marginal escape falls back to legacy base-closer', () => {
    const { input, threat } = asymmetricSetup()
    // Best margin ≈ t_arrive(31) − escape(19/1.05≈18) ≈ 13 ticks. A margin
    // gate of 20 (> 13) rejects the commitment → legacy picks base-closer RIGHT.
    input.params.dodgeHorizonMinMarginTicks = 20
    expect(dodgeDirectionImpl(input, threat, PCX, PCY)).toBe('right')
  })

  it('distance gate: a far-from-base escape falls back to legacy base-closer', () => {
    const { input, threat } = asymmetricSetup()
    // Player at (col 8, row 10) is 5+15 = 20 cells from the base center —
    // beyond maxDist=8 → commitment rejected → legacy picks base-closer RIGHT.
    input.params.dodgeHorizonMaxDistCells = 8
    expect(dodgeDirectionImpl(input, threat, PCX, PCY)).toBe('right')
  })

  it('both gates pass → the horizon commitment still applies (escape side)', () => {
    const { input, threat } = asymmetricSetup()
    input.params.dodgeHorizonMinMarginTicks = 8 // best margin ≈ 13 ≥ 8
    input.params.dodgeHorizonMaxDistCells = 30 // player 20 cells away ≤ 30
    expect(dodgeDirectionImpl(input, threat, PCX, PCY)).toBe('left')
  })
})
