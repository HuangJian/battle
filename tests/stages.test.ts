import { describe, it, expect } from 'bun:test'
import { LEVELS, ENEMY_FORCES } from '../src/config/stageData'
import { STAGES, isBonusEnemy } from '../src/config/stages'
import type { TankKind } from '../src/types'

// Tile codec mirror (kept local so the test does not depend on the codec impl).
const WHOLE = [1, 1, 1, 1] as const
const TOP = [1, 1, 0, 0] as const
const BOTTOM = [0, 0, 1, 1] as const
const LEFT = [1, 0, 1, 0] as const
const RIGHT = [0, 1, 0, 1] as const
const BL = [0, 0, 1, 0] as const
const BR = [0, 0, 0, 1] as const

const CODE: Record<number, [string, readonly number[]] | 'base'> = {
  1: ['b', WHOLE],
  2: ['b', TOP],
  3: ['b', RIGHT],
  4: ['b', BOTTOM],
  5: ['b', LEFT],
  17: ['b', BL],
  18: ['b', BR],
  6: ['s', WHOLE],
  7: ['s', TOP],
  8: ['s', RIGHT],
  9: ['s', BOTTOM],
  10: ['s', LEFT],
  19: ['s', BL],
  20: ['s', BR],
  11: ['f', WHOLE],
  12: ['i', WHOLE],
  13: ['w', WHOLE],
  15: 'base',
}

const KIND_OF: Record<string, TankKind> = {
  a: 'basic',
  b: 'fast',
  c: 'power',
  d: 'armor',
}

/** Independent re-implementation of the decode used to cross-check STAGES. */
function decodeLevel(grid: number[][]): string[] {
  const rows: string[] = Array.from({ length: 26 }, () => '.'.repeat(26))
  for (let ty = 0; ty < 13; ty++) {
    for (let tx = 0; tx < 13; tx++) {
      const piece = CODE[grid[ty]?.[tx] ?? 0]
      const cx = tx * 2
      const cy = ty * 2
      if (piece === 'base') {
        for (let dr = 0; dr < 2; dr++) {
          for (let dc = 0; dc < 2; dc++) {
            const sr = cy + dr
            const sc = cx + dc
            rows[sr] = rows[sr].slice(0, sc) + 'E' + rows[sr].slice(sc + 1)
          }
        }
        continue
      }
      if (!piece) continue
      const [ch, q] = piece
      const set = (sr: number, sc: number, on: boolean) => {
        if (!on) return
        rows[sr] = rows[sr].slice(0, sc) + ch + rows[sr].slice(sc + 1)
      }
      set(cy + 0, cx + 0, q[0] === 1)
      set(cy + 0, cx + 1, q[1] === 1)
      set(cy + 1, cx + 0, q[2] === 1)
      set(cy + 1, cx + 1, q[3] === 1)
    }
  }
  return rows
}

function decodeForces(index: number): TankKind[] {
  const forces = ENEMY_FORCES[index % ENEMY_FORCES.length]
  const queue: TankKind[] = []
  for (let i = 0; i < 20; i++) {
    const ch = forces[i] ?? 'a'
    queue.push(KIND_OF[ch] ?? 'basic')
  }
  return queue
}

const EXPECTED_BASE_CELLS = [
  [24, 12],
  [24, 13],
  [25, 12],
  [25, 13],
]

describe('classic stage data (stageData.ts)', () => {
  it('has 35 levels and 35 enemy force strings', () => {
    expect(LEVELS.length).toBe(35)
    expect(ENEMY_FORCES.length).toBe(35)
  })

  it('every level is a 13x13 numeric grid', () => {
    for (let i = 0; i < LEVELS.length; i++) {
      expect(LEVELS[i].length, `level ${i} rows`).toBe(13)
      for (const row of LEVELS[i]) {
        expect(row.length, `level ${i} cols`).toBe(13)
      }
    }
  })

  it('every enemy force string has exactly 20 chars', () => {
    for (let i = 0; i < ENEMY_FORCES.length; i++) {
      expect(ENEMY_FORCES[i].length, `forces ${i}`).toBe(20)
    }
  })

  it('enemy force letters are within the known set', () => {
    for (const forces of ENEMY_FORCES) {
      for (const ch of forces) {
        expect(['a', 'b', 'c', 'd']).toContain(ch)
      }
    }
  })
})

describe('decoded stages (stages.ts)', () => {
  it('produces 35 stages', () => {
    expect(STAGES.length).toBe(35)
  })

  it('every stage decodes to a 26x26 tile grid', () => {
    for (const stage of STAGES) {
      expect(stage.tiles.length, `stage ${stage.id} rows`).toBe(26)
      for (const row of stage.tiles) {
        expect(row.length, `stage ${stage.id} cols`).toBe(26)
      }
    }
  })

  it('every stage has exactly 20 enemies', () => {
    for (const stage of STAGES) {
      expect(stage.enemies.length, `stage ${stage.id}`).toBe(20)
    }
  })

  it('every stage places the base eagle at rows 24-25 / cols 12-13', () => {
    for (const stage of STAGES) {
      const base: number[][] = []
      for (let r = 0; r < 26; r++) {
        for (let c = 0; c < 26; c++) {
          if (stage.tiles[r][c] === 'E') base.push([r, c])
        }
      }
      expect(base, `stage ${stage.id} base`).toEqual(EXPECTED_BASE_CELLS)
    }
  })

  it('only uses known terrain characters', () => {
    const allowed = new Set(['.', 'b', 's', 'w', 'f', 'i', 'E'])
    for (const stage of STAGES) {
      for (const row of stage.tiles) {
        for (const ch of row) {
          expect(allowed.has(ch), `stage ${stage.id} char "${ch}"`).toBe(true)
        }
      }
    }
  })

  it('matches an independent re-implementation of the codec', () => {
    for (let i = 0; i < LEVELS.length; i++) {
      const expected = decodeLevel(LEVELS[i])
      expect(STAGES[i].tiles, `stage ${i + 1} tiles`).toEqual(expected)
      expect(STAGES[i].enemies, `stage ${i + 1} enemies`).toEqual(decodeForces(i))
    }
  })

  it('assigns sequential ids and non-empty names', () => {
    STAGES.forEach((stage, i) => {
      expect(stage.id).toBe(i + 1)
      expect(stage.name.length).toBeGreaterThan(0)
    })
  })
})

describe('isBonusEnemy', () => {
  it('flags every 4th enemy (0-based index % 4 === 3)', () => {
    expect(isBonusEnemy(3)).toBe(true)
    expect(isBonusEnemy(7)).toBe(true)
    expect(isBonusEnemy(0)).toBe(false)
    expect(isBonusEnemy(1)).toBe(false)
    expect(isBonusEnemy(2)).toBe(false)
  })
})
