import { describe, it, expect } from 'bun:test'
import { TileMap } from '../../src/game/TileMap'
import { GRID } from '../../src/constants'
import { findPath } from '../../src/utils/pathfind'
import type { StageData } from '../../src/types'

function mapFrom(tiles: string[]): TileMap {
  const tm = new TileMap()
  const stage: StageData = { id: 0, name: 'hard-block', tiles, enemies: [] }
  tm.loadStage(stage)
  return tm
}

function emptyTiles(): string[] {
  const tiles: string[] = []
  for (let r = 0; r < GRID; r++) tiles.push('.'.repeat(GRID))
  return tiles
}

function setChar(tiles: string[], col: number, row: number, ch: string): void {
  tiles[row] = tiles[row].slice(0, col) + ch + tiles[row].slice(col + 1)
}

/** Walk a path and report whether any 2×2 footprint overlaps a hard cell. */
function pathTouches(
  from: { col: number; row: number },
  path: readonly string[],
  hard: readonly { col: number; row: number }[],
): boolean {
  const key = (c: number, r: number) => r * GRID + c
  const hardKeys = new Set(hard.map((h) => key(h.col, h.row)))
  let col = from.col
  let row = from.row
  for (const d of path) {
    if (d === 'up') row--
    else if (d === 'down') row++
    else if (d === 'left') col--
    else col++
    for (let dr = 0; dr <= 1; dr++) {
      for (let dc = 0; dc <= 1; dc++) {
        if (hardKeys.has(key(col + dc, row + dr))) return true
      }
    }
  }
  return false
}

describe('findPath hardBlockedCells', () => {
  it('cannot cross a steel wall through a hard-blocked gap even in dig mode', () => {
    const tiles = emptyTiles()
    // Steel wall at row 13 with an otherwise-open 2-wide gap.
    for (let c = 0; c < GRID; c++) setChar(tiles, c, 13, 's')
    setChar(tiles, 12, 13, '.')
    setChar(tiles, 13, 13, '.')
    const tm = mapFrom(tiles)

    const open = findPath(tm, { col: 2, row: 10 }, { col: 2, row: 16 }, { breakBrick: true })
    expect(open).not.toBeNull()

    const blocked = findPath(
      tm,
      { col: 2, row: 10 },
      { col: 2, row: 16 },
      {
        breakBrick: true,
        hardBlockedCells: [
          { col: 12, row: 13 },
          { col: 13, row: 13 },
        ],
      },
    )
    expect(blocked).toBeNull()
  })

  it('checks the whole 2×2 footprint, so a hard cell beside the route blocks it', () => {
    const hard = [{ col: 10, row: 2 }]
    const tm = mapFrom(emptyTiles())
    const path = findPath(
      tm,
      { col: 2, row: 2 },
      { col: 18, row: 2 },
      {
        breakBrick: true,
        hardBlockedCells: hard,
      },
    )
    expect(path).not.toBeNull()
    expect(pathTouches({ col: 2, row: 2 }, path!, hard)).toBe(false)
    expect(path!.length).toBeGreaterThan(16) // detoured above/below row 2
  })

  it('leaves a path untouched when the hard cells are off-route', () => {
    const tm = mapFrom(emptyTiles())
    const baseline = findPath(tm, { col: 2, row: 2 }, { col: 18, row: 2 })
    const withHard = findPath(
      tm,
      { col: 2, row: 2 },
      { col: 18, row: 2 },
      {
        hardBlockedCells: [{ col: 10, row: 20 }],
      },
    )
    expect(withHard).toEqual(baseline)
  })
})

describe('findPath brickCost', () => {
  it('lexicographic brickCost prefers a long brick-free detour over one brick', () => {
    const tiles = emptyTiles()
    // Brick wall at col 10 with a 2-row gap far below (rows 10-11).
    for (let r = 0; r < GRID; r++) setChar(tiles, 10, r, 'b')
    setChar(tiles, 10, 10, '.')
    setChar(tiles, 10, 11, '.')
    const tm = mapFrom(tiles)

    const classic = findPath(tm, { col: 4, row: 2 }, { col: 16, row: 2 }, { breakBrick: true })
    expect(classic).not.toBeNull()
    // Classic brick=5 prefers the short route straight through the wall.
    expect(classic!.every((d) => d === 'right')).toBe(true)

    const lexicographic = findPath(
      tm,
      { col: 4, row: 2 },
      { col: 16, row: 2 },
      {
        breakBrick: true,
        brickCost: GRID * GRID + 1,
      },
    )
    expect(lexicographic).not.toBeNull()
    // The detour must go down to the row 5-6 gap and back up.
    expect(lexicographic).toContain('down')
    expect(lexicographic).toContain('up')
    expect(lexicographic!.length).toBeGreaterThan(classic!.length)
  })
})
