import type { StageData, TankKind } from '../types'

/**
 * Stage definitions.
 * Tile codes (13×13 grid, each tile = 2×2 sub-blocks):
 *   . = empty    b = brick    s = steel
 *   w = water    f = forest   i = ice
 *   E = base (eagle)
 *
 * Adding a new stage = adding one entry to STAGES.
 */

const ENEMY_POOL: TankKind[] = [
  'basic',
  'basic',
  'basic',
  'basic',
  'fast',
  'fast',
  'fast',
  'power',
  'power',
  'armor',
]

function makeEnemies(seed: number): TankKind[] {
  const kinds: TankKind[] = []
  // Deterministic pseudo-random based on seed
  let s = seed
  const rng = () => {
    s = (s * 1103515245 + 12345) & 0x7fffffff
    return s / 0x7fffffff
  }
  for (let i = 0; i < 20; i++) {
    const r = rng()
    if (i < 4) {
      kinds.push('basic')
    } else if (r < 0.3) {
      kinds.push('basic')
    } else if (r < 0.55) {
      kinds.push('fast')
    } else if (r < 0.8) {
      kinds.push('power')
    } else {
      kinds.push('armor')
    }
  }
  // Mark every 4th enemy as bonus (drops power-up)
  void ENEMY_POOL
  return kinds
}

export const STAGES: StageData[] = [
  {
    id: 1,
    name: 'Outpost',
    tiles: [
      '.............',
      '.............',
      '..bb.....bb..',
      '..bb.....bb..',
      '.............',
      '.....bbb.....',
      '.....b.b.....',
      '.....bbb.....',
      '.............',
      '..bb.....bb..',
      '..bb.....bb..',
      '....bbbbb....',
      '.....bEb.....',
    ],
    enemies: makeEnemies(42),
  },
  {
    id: 2,
    name: 'Waterways',
    tiles: [
      '.............',
      '..bbb...bbb..',
      '..b.b...b.b..',
      '..bbb...bbb..',
      '.....www.....',
      '.....www.....',
      '..bbb...bbb..',
      '..b.b...b.b..',
      '..bbb...bbb..',
      '.....www.....',
      '.............',
      '....bbbbb....',
      '.....bEb.....',
    ],
    enemies: makeEnemies(77),
  },
  {
    id: 3,
    name: 'Steel Fortress',
    tiles: [
      '.............',
      '..sss...sss..',
      '.............',
      '..bbb...bbb..',
      '.............',
      '....f...f....',
      '...ff.ff.ff..',
      '....f...f....',
      '.............',
      '..bbb...bbb..',
      '.............',
      '....bbbbb....',
      '.....bEb.....',
    ],
    enemies: makeEnemies(123),
  },
  {
    id: 4,
    name: 'Crossfire',
    tiles: [
      '.............',
      '....bbb......',
      '....b.b.sss..',
      '....bbb......',
      '..w.......w..',
      '..w.bbbbb.w..',
      '..w.b...b.w..',
      '..w.bbbbb.w..',
      '..w.......w..',
      '......bbb....',
      '..sss.b.b....',
      '......bbb....',
      '.....bEb.....',
    ],
    enemies: makeEnemies(256),
  },
  {
    id: 5,
    name: 'Maze',
    tiles: [
      '.............',
      '.bb.bbb.bb...',
      '.b.....b.bb..',
      '.b.sss.b.....',
      '.b.....b.sss.',
      '.bbbbbbb.b...',
      '.............',
      '...b.bbbbbbb.',
      'sss.b.......b',
      '...b.bbb.bbb.',
      '...b.....b...',
      '...bbbbb.b...',
      '.....bEb.....',
    ],
    enemies: makeEnemies(999),
  },
]

/** Bonus enemy indices (0-based): every 4th enemy drops a power-up */
export function isBonusEnemy(index: number): boolean {
  return index % 4 === 3
}
