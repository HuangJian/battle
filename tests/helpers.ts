import { World } from '../src/game/World'
import { RNG } from '../src/utils/RNG'
import { CELL, GRID, BASE_POS } from '../src/constants'
import type { Direction } from '../src/constants'
import type { Tank, TankKind } from '../src/types'

/**
 * Shared test fixtures (plan/refactor.agy.md §3.4).
 *
 * One canonical implementation per common setup pattern. Semantics are
 * frozen to the dominant historical copy-paste variant — do NOT "improve"
 * them: dozens of tests assert geometry relative to these exact numbers.
 * If a test needs a different setup shape, prefer a local helper over
 * widening these.
 */

/** Options for {@link createTestWorld}. */
export interface TestWorldOptions {
  /** Seed for `world.rng` (default 42 — the historical test seed). */
  rngSeed?: number
}

/** Fresh World with a seeded RNG; no stage loaded (`state` stays 'menu'). */
export function createTestWorld(opts: TestWorldOptions = {}): World {
  const world = new World()
  world.rng = new RNG(opts.rngSeed ?? 42)
  return world
}

/**
 * Wipe every tile to 'empty' and restore the base eagle 2×2 at BASE_POS —
 * the standard "clean slate but win/lose conditions intact" arena used by
 * ~30 simulation tests.
 */
export function clearArena(world: World): void {
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) world.tileMap.grid[r][c] = 'empty'
  }
  for (const r of [24, 25]) {
    for (const c of [BASE_POS.col, BASE_POS.col + 1]) world.tileMap.grid[r][c] = 'base'
  }
}

/**
 * Spawn an enemy directly onto the field (bypassing the spawn queue):
 * `createTank` at cell (col,row), spawn animation skipped, pushed into
 * `world.tanks`. Returns the tank so callers can tweak AI state.
 */
export function placeEnemy(
  world: World,
  col: number,
  row: number,
  kind: TankKind = 'basic',
  dir: Direction = 'down',
): Tank {
  const enemy = world.createTank(kind, col * CELL, row * CELL, dir)
  enemy.spawnTimer = 0
  world.tanks.push(enemy)
  return enemy
}

/**
 * Teleport the live player tank to cell (col,row) with spawn/shield timers
 * cleared. Cell coords → top-left pixel mapping is `col*CELL` (the dominant
 * test convention).
 */
export function positionPlayer(world: World, col: number, row: number, dir?: Direction): void {
  const p = world.player!
  p.x = col * CELL
  p.y = row * CELL
  p.spawnTimer = 0
  p.shieldTimer = 0
  if (dir) p.dir = dir
}
