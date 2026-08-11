import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { STAGES } from '../src/config/stages'
import { aabb } from '../src/utils/helpers'
import { CELL, TANK, FIELD } from '../src/constants'

/**
 * Spawn relocation (user req: 任何作战单位出生时，如果出生点有敌人或者队友，
 * 无条件将其移动至周围的空格子，避免互相卡住).
 *
 * A combat unit must never be born on top of terrain or another tank — two
 * overlapping spawns jam BOTH units (neither can drive off the shared
 * footprint). `World.findFreeSpawnCell` (applied inside `createTank`) resolves
 * the requested cell to the nearest free 32-aligned cell. This test guards that
 * invariant for players, enemies and allies alike.
 */

/** Load stage terrain only (no auto-spawned player), so we control occupancy. */
function loadTerrainOnly(world: World, stageIndex = 0): void {
  world.tileMap.loadStage(STAGES[stageIndex])
  world.playerSpawnPoint = { col: 8, row: 24 }
}

/** The true nearest free 32-aligned cell — reference implementation for assertions. */
function trueNearestFree(world: World, x: number, y: number): { x: number; y: number } | null {
  const step = TANK
  const maxX = FIELD - TANK
  const maxY = FIELD - TANK
  let best: { x: number; y: number } | null = null
  let bestD = Infinity
  for (let gy = 0; gy <= maxY; gy += step) {
    for (let gx = 0; gx <= maxX; gx += step) {
      if (world.rectHitsTerrain(gx, gy, TANK, TANK)) continue
      let occupied = false
      for (const t of world.allTanks) {
        if (t.alive && aabb(gx, gy, TANK, TANK, t.x, t.y, t.w, t.h)) {
          occupied = true
          break
        }
      }
      if (occupied) continue
      const d = (gx - x) * (gx - x) + (gy - y) * (gy - y)
      if (d < bestD) {
        bestD = d
        best = { x: gx, y: gy }
      }
    }
  }
  return best
}

describe('Spawn relocation — never born on top of another tank', () => {
  it('returns the requested cell unchanged when it is already free', () => {
    const world = new World()
    loadTerrainOnly(world)
    // (0,0) is the classic enemy spawn point — terrain-free on stage 0.
    const cell = world.findFreeSpawnCell(0, 0)
    expect(cell).toEqual({ x: 0, y: 0 })
  })

  it('relocates off an occupied cell to a free cell (no terrain, no overlap)', () => {
    const world = new World()
    loadTerrainOnly(world)
    // Occupy the player spawn cell with a live enemy.
    const occupant = world.createTank('basic', 8 * CELL, 24 * CELL, 'down')
    world.tanks.push(occupant)
    expect(world.findFreeSpawnCell(8 * CELL, 24 * CELL)).not.toEqual({
      x: 8 * CELL,
      y: 24 * CELL,
    })
    const cell = world.findFreeSpawnCell(8 * CELL, 24 * CELL)
    // Not overlapping terrain.
    expect(world.rectHitsTerrain(cell.x, cell.y, TANK, TANK)).toBe(false)
    // Not overlapping any live tank (including the occupant).
    for (const t of world.allTanks) {
      if (!t.alive) continue
      expect(aabb(cell.x, cell.y, TANK, TANK, t.x, t.y, t.w, t.h)).toBe(false)
    }
  })

  it('relocates to the NEAREST free cell', () => {
    const world = new World()
    loadTerrainOnly(world)
    const occupant = world.createTank('basic', 8 * CELL, 24 * CELL, 'down')
    world.tanks.push(occupant)
    const want = trueNearestFree(world, 8 * CELL, 24 * CELL)!
    expect(want).toBeDefined()
    expect(world.findFreeSpawnCell(8 * CELL, 24 * CELL)).toEqual(want)
  })

  it('is deterministic (same world state ⇒ same resolved cell, no RNG)', () => {
    const world = new World()
    loadTerrainOnly(world)
    const occupant = world.createTank('basic', 8 * CELL, 24 * CELL, 'down')
    world.tanks.push(occupant)
    const a = world.findFreeSpawnCell(8 * CELL, 24 * CELL)
    const b = world.findFreeSpawnCell(8 * CELL, 24 * CELL)
    expect(b).toEqual(a)
  })

  it('player respawn relocates when an enemy sits on the spawn point', () => {
    const world = new World()
    loadTerrainOnly(world)
    // An enemy camped exactly on the player spawn point.
    const camper = world.createTank('basic', 8 * CELL, 24 * CELL, 'down')
    world.tanks.push(camper)
    // Respawn the player — must NOT be born on top of the camper.
    world.spawnPlayer()
    expect(world.player).not.toBeNull()
    // The player must have been moved off the camper's exact cell (a relocation
    // may keep the same column and only shift one row, so assert the full
    // position differs rather than a single coordinate).
    expect(world.player!.x === camper.x && world.player!.y === camper.y).toBe(false)
    // No overlap with the camper (the core "don't get stuck" invariant).
    expect(aabb(world.player!.x, world.player!.y, TANK, TANK, camper.x, camper.y, camper.w, camper.h)).toBe(false)
    expect(world.rectHitsTerrain(world.player!.x, world.player!.y, TANK, TANK)).toBe(false)
    for (const t of world.allTanks) {
      if (t === world.player || !t.alive) continue
      expect(aabb(world.player!.x, world.player!.y, TANK, TANK, t.x, t.y, t.w, t.h)).toBe(false)
    }
  })
})
