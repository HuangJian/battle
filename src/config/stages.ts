import type { StageData, TankKind } from '../types'
import { LEVELS, ENEMY_FORCES } from './stageData'
import { i18n } from '../i18n'

/**
 * Classic Battle City stages.
 *
 * The authentic level layouts live in `stageData.ts` as 35 stages of 13×13
 * numeric tile codes (the original Famicom maps from
 * github.com/FrontHeads/tanchiki). Each numeric code names a material plus
 * which of the four 2×2 sub-cells it fills, reproducing the original's partial
 * brick & steel pieces.
 *
 * This file decodes every stage into a 26×26 grid of single characters — one
 * character per 16px sub-block — which is exactly what `TileMap.loadStage`
 * consumes. The 26×26 grid is the native terrain resolution of this engine
 * (GRID = 26, CELL = 16), so partial fills render faithfully with no loss.
 *
 * Char legend (one per sub-block):
 *   . = empty    b = brick    s = steel
 *   w = water    f = forest   i = ice
 *   E = base (eagle)
 *
 * Adding a new stage = appending one 13×13 numeric grid to `LEVELS` in
 * `stageData.ts` (and a matching 20-char enemy string to `ENEMY_FORCES`).
 */

// --- Tile codec -------------------------------------------------------------

type Quad = [boolean, boolean, boolean, boolean] // TL, TR, BL, BR
const WHOLE: Quad = [true, true, true, true]
const TOP: Quad = [true, true, false, false]
const BOTTOM: Quad = [false, false, true, true]
const LEFT: Quad = [true, false, true, false]
const RIGHT: Quad = [false, true, false, true]
const BL: Quad = [false, false, true, false]
const BR: Quad = [false, false, false, true]

interface Piece {
  ch: string
  quad: Quad
}

// Codes from the source data (see stageData.ts header).
const CODE: Record<number, Piece | 'base'> = {
  1: { ch: 'b', quad: WHOLE },
  2: { ch: 'b', quad: TOP },
  3: { ch: 'b', quad: RIGHT },
  4: { ch: 'b', quad: BOTTOM },
  5: { ch: 'b', quad: LEFT },
  17: { ch: 'b', quad: BL },
  18: { ch: 'b', quad: BR },
  6: { ch: 's', quad: WHOLE },
  7: { ch: 's', quad: TOP },
  8: { ch: 's', quad: RIGHT },
  9: { ch: 's', quad: BOTTOM },
  10: { ch: 's', quad: LEFT },
  19: { ch: 's', quad: BL },
  20: { ch: 's', quad: BR },
  11: { ch: 'f', quad: WHOLE },
  12: { ch: 'i', quad: WHOLE },
  13: { ch: 'w', quad: WHOLE },
  15: 'base',
}

const KIND_OF: Record<string, TankKind> = {
  a: 'basic',
  b: 'fast',
  c: 'power',
  d: 'armor',
}

const STAGE_NAMES = [
  'Outpost',
  'Waterways',
  'Steel Fortress',
  'Crossfire',
  'Maze',
  'Brickworks',
  'Iron Curtain',
  'Riverbed',
  'Twin Towers',
  'Gauntlet',
  'Fortress',
  'Lattice',
  'Bunker Hill',
  'Steel Web',
  'Citadel',
  'Crossroads',
  'Twin Spires',
  'Gridlock',
  'Frozen Field',
  'Bastion',
  'Checkers',
  'Oasis',
  'Ramparts',
  'Labyrinth',
  'Quarry',
  'Ice Palace',
  'Brick Maze',
  'Thicket',
  'Spider',
  'Concentric',
  'Eagle Nest',
  'Star Fort',
  'Diamond',
  'Battlement',
  'Final Redoubt',
]

/**
 * Chinese names for the classic 35 stages, in the same order as `STAGE_NAMES`.
 * Stage names are presentation-only strings (never gameplay state), so the
 * active language is resolved at display time via `localizedStageName`.
 */
const STAGE_NAMES_ZH = [
  '前哨',
  '水道',
  '钢铁堡垒',
  '交叉火力',
  '迷宫',
  '砖窑',
  '铁幕',
  '河床',
  '双塔',
  '夹道',
  '堡垒',
  '栅格',
  '碉堡山',
  '钢网',
  '要塞',
  '十字路口',
  '双尖塔',
  '网格封锁',
  '冰封原野',
  '棱堡',
  '棋盘',
  '绿洲',
  '壁垒',
  '迷阵',
  '采石场',
  '冰宫',
  '砖迷宫',
  '密林',
  '蛛网',
  '同心圆',
  '鹰巢',
  '星堡',
  '菱形阵',
  '城垛',
  '终极堡垒',
]

/** Decode one 13×13 numeric level into a 26×26 char grid (one char per sub-block). */
function decodeLevel(grid: number[][]): string[] {
  const rows: string[] = []
  for (let r = 0; r < 26; r++) rows.push('.'.repeat(26))
  for (let ty = 0; ty < 13; ty++) {
    for (let tx = 0; tx < 13; tx++) {
      const code = grid[ty]?.[tx] ?? 0
      const piece = CODE[code]
      const cx = tx * 2
      const cy = ty * 2
      if (piece === 'base') {
        // Eagle occupies the 2×2 sub-blocks of this tile (matches BASE_POS).
        for (let dr = 0; dr < 2; dr++) {
          for (let dc = 0; dc < 2; dc++) {
            const sr = cy + dr
            const sc = cx + dc
            const line = rows[sr]
            rows[sr] = line.slice(0, sc) + 'E' + line.slice(sc + 1)
          }
        }
        continue
      }
      if (!piece) continue
      const q = piece.quad
      const set = (sr: number, sc: number, on: boolean) => {
        if (!on) return
        const line = rows[sr]
        rows[sr] = line.slice(0, sc) + piece.ch + line.slice(sc + 1)
      }
      set(cy + 0, cx + 0, q[0])
      set(cy + 0, cx + 1, q[1])
      set(cy + 1, cx + 0, q[2])
      set(cy + 1, cx + 1, q[3])
    }
  }
  return rows
}

/** Build the 20-enemy spawn queue for a stage from its force string. */
function decodeForces(index: number): TankKind[] {
  const forces = ENEMY_FORCES[index % ENEMY_FORCES.length]
  const queue: TankKind[] = []
  for (let i = 0; i < 20; i++) {
    const ch = forces[i] ?? 'a'
    queue.push(KIND_OF[ch] ?? 'basic')
  }
  return queue
}

export const STAGES: StageData[] = LEVELS.map((grid, i) => ({
  id: i + 1,
  name: STAGE_NAMES[i] ?? `Stage ${i + 1}`,
  tiles: decodeLevel(grid),
  enemies: decodeForces(i),
}))

/**
 * Localized stage name for `index`, resolved against the active UI language.
 * `STAGES[i].name` stays the canonical English data (also baked into snapshot /
 * replay metadata); the displayed name follows the locale at call time so it
 * switches live with the LANGUAGE menu without touching the World.
 */
export function localizedStageName(index: number): string {
  if (i18n.locale === 'zh') return STAGE_NAMES_ZH[index] ?? STAGE_NAMES[index] ?? `Stage ${index + 1}`
  return STAGE_NAMES[index] ?? `Stage ${index + 1}`
}

/** Bonus enemy indices (0-based): every 4th enemy drops a power-up */
export function isBonusEnemy(index: number): boolean {
  return index % 4 === 3
}
