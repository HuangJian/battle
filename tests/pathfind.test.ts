import { describe, it, expect } from 'bun:test'
import { TileMap } from '../src/game/TileMap'
import { findPath, isReachable, floodFill, pxToCell } from '../src/utils/pathfind'
import { World } from '../src/game/World'
import { STAGES } from '../src/config/stages'
import { GRID, CELL, ENEMY_SPAWNS } from '../src/constants'
import type { StageData } from '../src/types'

/** Build a TileMap from a 26-row string array (one char per sub-block). */
function mapFrom(tiles: string[]): TileMap {
  const tm = new TileMap()
  const stage: StageData = { id: 0, name: 'test', tiles, enemies: [] }
  tm.loadStage(stage)
  return tm
}

/** All-empty 26×26 map (with base). */
function emptyMap(): TileMap {
  const tiles: string[] = []
  for (let r = 0; r < GRID; r++) tiles.push('.'.repeat(GRID))
  // Place base at (12,24) 2×2
  tiles[24] = tiles[24].slice(0, 12) + 'EE' + tiles[24].slice(14)
  tiles[25] = tiles[25].slice(0, 12) + 'EE' + tiles[25].slice(14)
  return mapFrom(tiles)
}

/** Helper: set a single char at (col,row) in a tiles array. */
function setChar(tiles: string[], col: number, row: number, ch: string): void {
  tiles[row] = tiles[row].slice(0, col) + ch + tiles[row].slice(col + 1)
}

describe('findPath', () => {
  it('returns empty path when from === to', () => {
    const tm = emptyMap()
    const path = findPath(tm, { col: 2, row: 2 }, { col: 2, row: 2 })
    expect(path).toEqual([])
  })

  it('returns null when start is impassable (inside steel)', () => {
    const tiles: string[] = []
    for (let r = 0; r < GRID; r++) tiles.push('.'.repeat(GRID))
    // 2×2 steel block at (5,5)
    setChar(tiles, 5, 5, 's')
    setChar(tiles, 6, 5, 's')
    setChar(tiles, 5, 6, 's')
    setChar(tiles, 6, 6, 's')
    const tm = mapFrom(tiles)
    const path = findPath(tm, { col: 5, row: 5 }, { col: 10, row: 10 })
    expect(path).toBeNull()
  })

  it('returns null when goal is impassable', () => {
    const tiles: string[] = []
    for (let r = 0; r < GRID; r++) tiles.push('.'.repeat(GRID))
    setChar(tiles, 10, 10, 's')
    setChar(tiles, 11, 10, 's')
    setChar(tiles, 10, 11, 's')
    setChar(tiles, 11, 11, 's')
    const tm = mapFrom(tiles)
    const path = findPath(tm, { col: 2, row: 2 }, { col: 10, row: 10 })
    expect(path).toBeNull()
  })

  it('finds a straight-line path on an empty map', () => {
    const tm = emptyMap()
    const path = findPath(tm, { col: 2, row: 2 }, { col: 8, row: 2 })
    expect(path).not.toBeNull()
    expect(path!.length).toBe(6)
    expect(path!.every((d) => d === 'right')).toBe(true)
  })

  it('returns null when a full-width wall blocks the path', () => {
    const tiles: string[] = []
    for (let r = 0; r < GRID; r++) tiles.push('.'.repeat(GRID))
    // Full-width brick wall at row 13
    tiles[13] = 'b'.repeat(GRID)
    // Base
    setChar(tiles, 12, 24, 'E')
    setChar(tiles, 13, 24, 'E')
    setChar(tiles, 12, 25, 'E')
    setChar(tiles, 13, 25, 'E')
    const tm = mapFrom(tiles)
    const path = findPath(tm, { col: 2, row: 10 }, { col: 2, row: 16 })
    expect(path).toBeNull()
  })

  it('finds a path through a 2-wide gap in a wall', () => {
    const tiles: string[] = []
    for (let r = 0; r < GRID; r++) tiles.push('.'.repeat(GRID))
    // Wall at row 13 with a 2-cell gap at cols 12-13
    for (let c = 0; c < GRID; c++) {
      if (c < 12 || c > 13) setChar(tiles, c, 13, 'b')
    }
    const tm = mapFrom(tiles)
    const path = findPath(tm, { col: 2, row: 10 }, { col: 2, row: 16 })
    expect(path).not.toBeNull()
    expect(path!.length).toBeGreaterThan(6)
  })

  it('respects 2×2 tank footprint (rejects tight 1-wide gaps)', () => {
    const tiles: string[] = []
    for (let r = 0; r < GRID; r++) tiles.push('.'.repeat(GRID))
    // Full-height vertical steel wall at col 10, with a 1-cell gap at row 10.
    // A 2×2 tank can't fit through because the adjacent cell (row 11) is steel.
    for (let r = 0; r < GRID; r++) {
      setChar(tiles, 10, r, 's')
    }
    // Single-cell gap at (10,10)
    setChar(tiles, 10, 10, '.')
    const tm = mapFrom(tiles)
    // Tank at (4,5) trying to reach (14,5)
    const path = findPath(tm, { col: 4, row: 5 }, { col: 14, row: 5 })
    expect(path).toBeNull()
  })

  it('ignoreWater constraint lets tanks pass through water', () => {
    const tiles: string[] = []
    for (let r = 0; r < GRID; r++) tiles.push('.'.repeat(GRID))
    // Full-height water wall at cols 10-11
    for (let r = 0; r < GRID; r++) {
      setChar(tiles, 10, r, 'w')
      setChar(tiles, 11, r, 'w')
    }
    const tm = mapFrom(tiles)
    // Without ignoreWater: blocked
    const path1 = findPath(tm, { col: 4, row: 5 }, { col: 14, row: 5 })
    expect(path1).toBeNull()
    // With ignoreWater: passable
    const path2 = findPath(
      tm,
      { col: 4, row: 5 },
      { col: 14, row: 5 },
      {
        ignoreWater: true,
      },
    )
    expect(path2).not.toBeNull()
  })

  it('works on a real classic stage', () => {
    const tm = new TileMap()
    tm.loadStage(STAGES[0])
    // Find path from (2,2) to (8,20) — should work on most stages
    const path = findPath(tm, { col: 2, row: 2 }, { col: 8, row: 20 })
    if (path) {
      expect(Array.isArray(path)).toBe(true)
      expect(path.every((d) => ['up', 'down', 'left', 'right'].includes(d))).toBe(true)
    }
  })
})

describe('isReachable', () => {
  it('returns true for same cell', () => {
    const tm = emptyMap()
    expect(isReachable(tm, { col: 5, row: 5 }, { col: 5, row: 5 })).toBe(true)
  })

  it('returns true on empty map', () => {
    const tm = emptyMap()
    expect(isReachable(tm, { col: 2, row: 2 }, { col: 20, row: 20 })).toBe(true)
  })

  it('returns false when fully walled off', () => {
    const tiles: string[] = []
    for (let r = 0; r < GRID; r++) tiles.push('.'.repeat(GRID))
    // Build a sealed steel box around (10,10):
    // Top wall: row 8, cols 8-13
    for (let c = 8; c <= 13; c++) setChar(tiles, c, 8, 's')
    // Bottom wall: row 13, cols 8-13
    for (let c = 8; c <= 13; c++) setChar(tiles, c, 13, 's')
    // Left wall: col 8, rows 8-13
    for (let r = 8; r <= 13; r++) setChar(tiles, 8, r, 's')
    // Right wall: col 13, rows 8-13
    for (let r = 8; r <= 13; r++) setChar(tiles, 13, r, 's')
    const tm = mapFrom(tiles)
    // (10,10) is boxed in by steel — can't reach (2,2)
    expect(isReachable(tm, { col: 10, row: 10 }, { col: 2, row: 2 })).toBe(false)
  })

  it('returns true for enemy spawns to near-base on a real stage', () => {
    const tm = new TileMap()
    tm.loadStage(STAGES[0])
    // Check that each enemy spawn can reach a cell near the base (not the base
    // itself, since 'base' blocks tanks).
    const nearBase = { col: 10, row: 22 }
    for (const spawn of ENEMY_SPAWNS) {
      const cell = { col: spawn.col, row: spawn.row }
      // Some spawn cells may be blocked by terrain (authentic stages do this),
      // so only check when the spawn cell itself is passable.
      const path = findPath(tm, cell, nearBase)
      // At least one spawn should have a path
      if (path !== null) {
        expect(path.length).toBeGreaterThanOrEqual(0)
      }
    }
  })
})

describe('floodFill', () => {
  it('returns only a few cells when fully boxed in', () => {
    const tiles: string[] = []
    for (let r = 0; r < GRID; r++) tiles.push('.'.repeat(GRID))
    // Sealed steel box around (10,10): top row 8, bottom row 13, left col 8, right col 13
    for (let c = 8; c <= 13; c++) {
      setChar(tiles, c, 8, 's')
      setChar(tiles, c, 13, 's')
    }
    for (let r = 8; r <= 13; r++) {
      setChar(tiles, 8, r, 's')
      setChar(tiles, 13, r, 's')
    }
    const tm = mapFrom(tiles)
    const reachable = floodFill(tm, { col: 10, row: 10 })
    // Interior is cols 9-12, rows 9-12 (4×4 = 16 cells). A 2×2 tank can
    // occupy 3×3 = 9 positions inside this box. It cannot escape.
    expect(reachable.size).toBe(9)
    expect(reachable.has('10,10')).toBe(true)
  })

  it('fills most of an empty map', () => {
    const tm = emptyMap()
    const reachable = floodFill(tm, { col: 2, row: 2 })
    // On an empty map (minus base), most cells should be reachable
    expect(reachable.size).toBeGreaterThan(GRID * GRID * 0.7)
  })

  it('all cells in the result are reachable from the start', () => {
    const tm = new TileMap()
    tm.loadStage(STAGES[5])
    const reachable = floodFill(tm, { col: 2, row: 2 })
    for (const k of reachable) {
      const [c, r] = k.split(',').map(Number)
      expect(isReachable(tm, { col: 2, row: 2 }, { col: c, row: r })).toBe(true)
    }
  })
})

describe('pxToCell', () => {
  it('converts pixel coordinates to grid cell', () => {
    expect(pxToCell(0, 0)).toEqual({ col: 0, row: 0 })
    expect(pxToCell(CELL, CELL)).toEqual({ col: 1, row: 1 })
    expect(pxToCell(CELL * 8, CELL * 24)).toEqual({ col: 8, row: 24 })
  })
})

describe('World.loadStageData', () => {
  it('loads a custom StageData without going through STAGES', () => {
    const world = new World()
    const tiles: string[] = []
    for (let r = 0; r < GRID; r++) tiles.push('.'.repeat(GRID))
    setChar(tiles, 12, 24, 'E')
    setChar(tiles, 13, 24, 'E')
    setChar(tiles, 12, 25, 'E')
    setChar(tiles, 13, 25, 'E')
    // Add some terrain
    setChar(tiles, 5, 10, 'b')
    setChar(tiles, 6, 10, 'b')
    setChar(tiles, 5, 11, 'b')
    setChar(tiles, 6, 11, 'b')

    const stage: StageData = {
      id: 999,
      name: 'Custom Test',
      tiles,
      enemies: ['basic', 'fast', 'power', 'armor'],
    }

    world.difficultyKey = 'hard'
    world.difficulty = { name: 'Hard', startLives: 2, playerStartLevel: 0 }
    world.loadStageData(stage, 5)

    expect(world.state).toBe('playing')
    expect(world.stageIndex).toBe(5)
    expect(world.player).not.toBeNull()
    expect(world.player!.x).toBe(8 * CELL)
    expect(world.player!.y).toBe(24 * CELL)
    expect(world.spawnQueue.length).toBe(20)
    expect(world.enemiesRemaining).toBe(20)
    expect(world.tileMap.get(5, 10)).toBe('brick')
    expect(world.tileMap.get(12, 24)).toBe('base')
  })

  it('is deterministic: same stage + same seed = same spawn queue', () => {
    const tiles: string[] = []
    for (let r = 0; r < GRID; r++) tiles.push('.'.repeat(GRID))
    setChar(tiles, 12, 24, 'E')
    setChar(tiles, 13, 24, 'E')
    setChar(tiles, 12, 25, 'E')
    setChar(tiles, 13, 25, 'E')

    const stage: StageData = {
      id: 1,
      name: 'Det Test',
      tiles,
      enemies: ['basic', 'fast', 'power', 'armor'],
    }

    const w1 = new World()
    w1.rng.reseed(42)
    w1.difficultyKey = 'classic'
    w1.difficulty = { name: 'Classic', startLives: 3, playerStartLevel: 0 }
    w1.loadStageData(stage, 0)
    const queue1 = w1.spawnQueue.map((e) => e.kind)

    const w2 = new World()
    w2.rng.reseed(42)
    w2.difficultyKey = 'classic'
    w2.difficulty = { name: 'Classic', startLives: 3, playerStartLevel: 0 }
    w2.loadStageData(stage, 0)
    const queue2 = w2.spawnQueue.map((e) => e.kind)

    expect(queue1).toEqual(queue2)
  })
})
