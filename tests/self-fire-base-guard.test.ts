import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { CLASSIC_MODEL_PARAMS } from '../src/ai/god/params'
import { shotReachesBaseImpl, enemyInShotCorridorImpl } from '../src/ai/god/FireControl'
import { CELL } from '../src/constants'
import { clearArena, placeEnemy } from './helpers'

/**
 * §121 t2a/aggressive 停射自毁守卫 (self-fire base guard) — unit tests.
 *
 * Background (DECISIONS §120): 32 self-kill runs across hard/chaos 120-seed —
 * t2a 81% — all straight-line shots into the base through broken ring gaps.
 * Root cause: the scan's dual ±8px offset lines catch an enemy up to ~25px
 * off the bullet's 6px center path; §74 then allows fire ("enemy closer than
 * the base wall") but the bullet misses the off-line enemy and continues
 * into the base (hard S6 s43: killer shot at x=200, enemy body x∈[206,238],
 * bullet [197,203] passed beside it into the eagle).
 *
 * This file tests the two helpers (shotReachesBaseImpl — the center-line
 * terrain walk; enemyInShotCorridorImpl — mode-2 leniency) and the
 * ENGAGE/AGGRO candidate end-to-end behavior with selfFireBaseGuard on.
 *
 * Geometry: default base at cols 12-13 / rows 24-25. Terrain is cleared
 * (all 'empty') except the base cells and whatever the test places.
 */

function setupWorld(
  params: Partial<typeof DEFAULT_GOD_AI_PARAMS> = {},
  difficulty = 'classic',
): {
  world: World
  input: GodAIInput
} {
  const world = new World()
  world.rng = new RNG(42)
  // Explicit clone (NOT the DEFAULT singleton) — mutating input.params must
  // not leak into DEFAULT_GOD_AI_PARAMS (DECISIONS §98).
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS, ...params })
  const sim = new Simulation(world, new Input())
  // 'hard' = POOL combat model. §115: on 'classic' (instant), reset() restores
  // selfFireBaseGuard→0 from CLASSIC_MODEL_PARAMS, so shipped-default tests
  // must run in a pool world (where the §121 default 2 actually applies).
  world.startGame(difficulty, 'modern', 0)
  clearArena(world)
  void sim
  input.reset()
  return { world, input }
}

function positionPlayer(world: World, x: number, y: number): void {
  const p = world.player!
  p.x = x
  p.y = y
  p.hp = 100
  p.spawnTimer = 0
  p.shieldTimer = 0
  p.level = 0
  world.playerLevel = 0
}

// ============================================================
// shotReachesBaseImpl — the center-line terrain walk
// ============================================================

describe('shotReachesBaseImpl', () => {
  it('clear column above the base, firing down → true (would hit the base)', () => {
    const { input } = setupWorld()
    // Player at column 12 (x=192..224 center 208), row 20, firing down —
    // the eagle at rows 24-25 is straight ahead with no ring wall.
    expect(shotReachesBaseImpl(input, 13 * CELL, 20 * CELL, 'down')).toBe(true)
  })

  it('intact ring brick stops the bullet → false (safe to fire)', () => {
    const { world, input } = setupWorld()
    // Ring top edge (row 23) across cols 11-14 — restore the brick.
    for (const c of [11, 12, 13, 14]) world.tileMap.grid[23][c] = 'brick'
    expect(shotReachesBaseImpl(input, 13 * CELL, 20 * CELL, 'down')).toBe(false)
  })

  it('ring gap (destroyed ring cell) lets the bullet reach the base → true', () => {
    const { world, input } = setupWorld()
    for (const c of [11, 12, 13, 14]) world.tileMap.grid[23][c] = 'brick'
    world.tileMap.grid[23][13] = 'empty' // gap in the ring above the eagle
    expect(shotReachesBaseImpl(input, 13 * CELL, 20 * CELL, 'down')).toBe(true)
  })

  it('ring steel stops the bullet → false', () => {
    const { world, input } = setupWorld()
    for (const c of [11, 12, 13, 14]) world.tileMap.grid[23][c] = 'steel'
    expect(shotReachesBaseImpl(input, 13 * CELL, 20 * CELL, 'down')).toBe(false)
  })

  it('non-ring brick in the line does NOT stop (bullets plow through) → true', () => {
    const { world, input } = setupWorld()
    world.tileMap.grid[21][13] = 'brick' // ordinary brick, not a ring cell
    expect(shotReachesBaseImpl(input, 13 * CELL, 20 * CELL, 'down')).toBe(true)
  })

  it('non-ring steel blocks at level < 3 → false', () => {
    const { world, input } = setupWorld()
    world.tileMap.grid[21][13] = 'steel'
    positionPlayer(world, 13 * CELL, 20 * CELL) // level 0
    expect(shotReachesBaseImpl(input, 13 * CELL, 20 * CELL, 'down')).toBe(false)
  })

  it('non-ring steel is pierced at level >= 3 → true', () => {
    const { world, input } = setupWorld()
    world.tileMap.grid[21][13] = 'steel'
    world.player!.level = 3
    world.playerLevel = 3
    expect(shotReachesBaseImpl(input, 13 * CELL, 20 * CELL, 'down')).toBe(true)
  })

  it('side fire into the base row band → true (right from the base row)', () => {
    const { input } = setupWorld()
    // Player at row 24 (y=384..416 center 400), col 8, firing right —
    // the eagle cols 12-13 are straight ahead.
    expect(shotReachesBaseImpl(input, 8 * CELL, 24 * CELL, 'right')).toBe(true)
  })

  it('center 3px outside the eagle span still reaches (6px box edge-graze) → true', () => {
    const { world, input } = setupWorld()
    // The §121 residual (hard S16 s82): center x=224 (col 14, the ring
    // right-edge column) — the box [221,227] overlaps the eagle [192,224).
    // Destroy the ring right-edge cells so nothing stops the bullet.
    world.tileMap.grid[24][14] = 'empty'
    world.tileMap.grid[25][14] = 'empty'
    expect(shotReachesBaseImpl(input, 14 * CELL, 11 * CELL, 'down')).toBe(true)
  })

  it('center 3px outside the eagle span BUT intact ring edge stops it → false', () => {
    const { world, input } = setupWorld()
    // Ring right-edge (col 14, rows 24-25) intact → the bullet dies on it
    // before reaching the eagle — safe to fire.
    world.tileMap.grid[24][14] = 'steel'
    world.tileMap.grid[25][14] = 'steel'
    expect(shotReachesBaseImpl(input, 14 * CELL, 11 * CELL, 'down')).toBe(false)
  })

  it('side fire from a row above the base band → false (never reaches)', () => {
    const { input } = setupWorld()
    expect(shotReachesBaseImpl(input, 8 * CELL, 20 * CELL, 'right')).toBe(false)
  })

  it('firing UP from below the base cannot reach it → false', () => {
    const { input } = setupWorld()
    expect(shotReachesBaseImpl(input, 13 * CELL, 22 * CELL, 'up')).toBe(false)
  })

  it('no base on the stage → false', () => {
    const { world, input } = setupWorld()
    for (const r of [24, 25]) for (const c of [12, 13]) world.tileMap.grid[r][c] = 'empty'
    world.tileMap.rebuildBaseCache()
    input.reset() // re-caches input.hasBase from the (now) base-less TileMap
    expect(shotReachesBaseImpl(input, 13 * CELL, 20 * CELL, 'down')).toBe(false)
  })
})

// ============================================================
// enemyInShotCorridorImpl — mode-2 leniency
// ============================================================

describe('enemyInShotCorridorImpl', () => {
  it('enemy body overlapping the bullet column between player and base → true', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 13 * CELL, 20 * CELL)
    placeEnemy(world, 13, 22) // center-aligned, 2 cells ahead of the player
    expect(enemyInShotCorridorImpl(input, 13 * CELL, 20 * CELL, 'down')).toBe(true)
  })

  it('enemy 22px off the center line (the §120 killer geometry) → false', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 12 * CELL + 8, 20 * CELL) // center x = 200
    placeEnemy(world, 13, 22) // enemy body x∈[208,240] — center 224?? no
    // Exact §120 geometry: player center x=200, enemy center x=222 (22px off).
    // Place the enemy at pixel x=206..238: world.createTank takes cell coords,
    // so position it manually for the 22px offset.
    const e = placeEnemy(world, 13, 22)
    e.x = 206 // body [206,238], center 222 — 22px off the player's 200 line
    expect(enemyInShotCorridorImpl(input, 200, 20 * CELL, 'down')).toBe(false)
  })

  it('enemy behind the player (not in front) → false', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 13 * CELL, 20 * CELL)
    placeEnemy(world, 13, 18) // above the player — not in front of a down-shot
    expect(enemyInShotCorridorImpl(input, 13 * CELL, 20 * CELL, 'down')).toBe(false)
  })

  it('enemy beyond the base far edge → false (bullet hits the base first)', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 13 * CELL, 20 * CELL)
    const e = placeEnemy(world, 13, 25)
    e.y = 26 * CELL // center below the base bottom — beyond the eagle
    expect(enemyInShotCorridorImpl(input, 13 * CELL, 20 * CELL, 'down')).toBe(false)
  })
})

// ============================================================
// Shipped-default invariants (DECISIONS §121)
// ============================================================

describe('§121 shipped defaults', () => {
  it('DEFAULT_GOD_AI_PARAMS ships mode 2 (lenient — the A/B winner)', () => {
    expect(DEFAULT_GOD_AI_PARAMS.selfFireBaseGuard).toBe(2)
  })

  it('CLASSIC_MODEL_PARAMS restores 0 (classic stays byte-identical)', () => {
    expect(CLASSIC_MODEL_PARAMS.selfFireBaseGuard).toBe(0)
  })

  it('the shipped default (2) blocks the §120 killer geometry (enemy 22px off-line)', () => {
    // POOL world ('hard') — on classic the §115 restore sets guard=0, which is
    // the byte-identical path; the shipped 2 only exists in pool games.
    const { world, input } = t2aSceneForDefault()
    // No explicit param — DEFAULT (guard=2) applies (not restored on pool).
    world.player!.level = 0
    world.playerLevel = 0
    for (const t of world.tanks) if (!t.isPlayer) t.x = 214 // 22px off the 208 line
    input.getMoveDirection()
    expect(input._fire).toBe(false)
  })
})

function t2aSceneForDefault(): { world: World; input: GodAIInput } {
  const { world, input } = setupWorld({}, 'hard')
  positionPlayer(world, 12 * CELL, 20 * CELL) // center 208, firing down
  for (const c of [11, 12, 13, 14]) world.tileMap.grid[23][c] = 'brick'
  world.tileMap.grid[23][13] = 'empty' // ring gap above the eagle
  const e = placeEnemy(world, 13, 22)
  e.x = 200
  return { world, input }
}

// ============================================================
// ENGAGE (T2a) candidate — end-to-end via think()
// ============================================================
// Scene: the §120 killer geometry — player above the base on the base column
// (ring gap broken), an enemy ALIGNED-but-off-line (22px, screened from the
// scan's offset lines), so the old code fires through the gap into the base.
// With the guard ON, T2a must decline (fall through to navigate).

describe('ENGAGE (T2a) self-fire guard (end-to-end)', () => {
  function t2aScene(): { world: World; input: GodAIInput } {
    const { world, input } = setupWorld()
    // Player above the base on column 13 (tank corner 12*CELL → center 208),
    // firing down. NOTE: positionPlayer sets the tank CORNER; the candidate
    // uses center = corner + 16, so 12*CELL gives center x=208.
    positionPlayer(world, 12 * CELL, 20 * CELL)
    // Ring top edge restored, but with a gap directly above the eagle.
    for (const c of [11, 12, 13, 14]) world.tileMap.grid[23][c] = 'brick'
    world.tileMap.grid[23][13] = 'empty'
    // Enemy screened off the 6px center path but inside the scan's ±8px
    // offset tolerance: body x∈[200,232] (center 216, 8px off the 208 line)
    // placed 2 cells below the player.
    const e = placeEnemy(world, 13, 22)
    e.x = 200 // body [200,232] — center 216, offset 8px from player's 208
    return { world, input }
  }

  it('guard OFF (selfFireBaseGuard=0) → T2a fires down the gap (the pre-§121 bug)', () => {
    const { input } = t2aScene()
    input.params.selfFireBaseGuard = 0
    input.getMoveDirection()
    expect(input._lastBranch).toBe('t2a')
    expect(input._fire).toBe(true)
  })

  it('guard mode 1 (strict) → T2a declined, falls through to navigate', () => {
    const { world, input } = t2aScene()
    input.params.selfFireBaseGuard = 1
    world.player!.level = 0
    world.playerLevel = 0
    input.getMoveDirection()
    expect(input._lastBranch).not.toBe('t2a')
    expect(input._fire).toBe(false)
  })

  it('guard mode 2 (lenient) → still allows when an enemy body overlaps the corridor', () => {
    const { world, input } = t2aScene()
    input.params.selfFireBaseGuard = 2
    world.player!.level = 0
    world.playerLevel = 0
    input.getMoveDirection()
    // Enemy at x∈[200,232], player line 208 — the 6px bullet column [205,211]
    // DOES overlap the enemy body → mode 2 lets it fire (bullet hits enemy first).
    expect(input._lastBranch).toBe('t2a')
  })

  it('guard mode 2 → blocked when the enemy is beyond the ±19px corridor band', () => {
    const { world, input } = t2aScene()
    input.params.selfFireBaseGuard = 2
    world.player!.level = 0
    world.playerLevel = 0
    // Push the enemy to the §120 killer geometry: body [214,246] (center 230,
    // 22px off the player's 208 line) — outside the 19px corridor band, so the
    // bullet would pass beside it into the base → suppressed.
    for (const t of world.tanks) if (!t.isPlayer) t.x = 214
    input.getMoveDirection()
    expect(input._lastBranch).not.toBe('t2a')
    expect(input._fire).toBe(false)
  })

  it('guard mode 1 → intact ring (no gap) does NOT block T2a (bullet stops at the wall)', () => {
    const { world, input } = t2aScene()
    input.params.selfFireBaseGuard = 1
    world.tileMap.grid[23][13] = 'brick' // restore the gap — ring fully intact
    input.getMoveDirection()
    // The bullet would stop at the ring brick — no base risk — fire is kept.
    expect(input._lastBranch).toBe('t2a')
  })
})

// ============================================================
// AGGRO candidate — same guard in the freeze-window stop-and-aim
// ============================================================

describe('AGGRO self-fire guard (end-to-end)', () => {
  it('guard ON: aggressive stop-and-aim declines a through-gap base shot', () => {
    const { world, input } = setupWorld({ selfFireBaseGuard: 1 })
    // Freeze window → aggressive mode.
    world.freezeTimer = 120
    // Player above the base on column 13 (corner 12*CELL → center 208), ring
    // gap above the eagle.
    positionPlayer(world, 12 * CELL, 20 * CELL)
    for (const c of [11, 12, 13, 14]) world.tileMap.grid[23][c] = 'brick'
    world.tileMap.grid[23][13] = 'empty'
    // Enemy screened off the 6px path: body [200,232] (center 216, 8px off).
    const e = placeEnemy(world, 13, 22)
    e.x = 200
    input.getMoveDirection()
    // The stop-and-aim is declined; the aggressive navigate fall-through may
    // still commit branch='aggressive' (it repositions), but it must NOT fire
    // — shouldFireInDir now carries the same §121 base-ray guard.
    expect(input._fire).toBe(false)
  })

  it('guard OFF: aggressive stop-and-aim still fires (pre-§121 baseline)', () => {
    const { world, input } = setupWorld({ selfFireBaseGuard: 0 })
    world.freezeTimer = 120
    positionPlayer(world, 13 * CELL, 20 * CELL)
    for (const c of [11, 12, 13, 14]) world.tileMap.grid[23][c] = 'brick'
    world.tileMap.grid[23][13] = 'empty'
    const e = placeEnemy(world, 13, 22)
    e.x = 200
    input.getMoveDirection()
    expect(input._lastBranch).toBe('aggressive')
  })
})
