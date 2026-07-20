import type { TerrainType, StageData } from '../types'
import { GRID, CELL } from '../constants'

/**
 * TileMap — 26×26 grid of sub-blocks.
 * Each sub-block is CELL (16px) wide.
 * A "tile" in stage data = 2×2 sub-blocks.
 */
export class TileMap {
  /** grid[row][col] — terrain type per sub-block */
  grid: TerrainType[][]

  constructor() {
    this.grid = []
    for (let r = 0; r < GRID; r++) {
      this.grid.push(Array.from({ length: GRID }, () => 'empty' as TerrainType))
    }
  }

  loadStage(stage: StageData): void {
    for (let r = 0; r < GRID; r++) {
      for (let c = 0; c < GRID; c++) {
        this.grid[r][c] = 'empty'
      }
    }

    const tiles = stage.tiles
    for (let tr = 0; tr < 13; tr++) {
      const line = tiles[tr] || ''
      for (let tc = 0; tc < 13; tc++) {
        const ch = line[tc] || '.'
        const type = this.charToTerrain(ch)
        // Each tile maps to 2×2 sub-blocks
        for (let dr = 0; dr < 2; dr++) {
          for (let dc = 0; dc < 2; dc++) {
            const sr = tr * 2 + dr
            const sc = tc * 2 + dc
            if (sr < GRID && sc < GRID) {
              this.grid[sr][sc] = type
            }
          }
        }
      }
    }
  }

  private charToTerrain(ch: string): TerrainType {
    switch (ch) {
      case 'b':
        return 'brick'
      case 's':
        return 'steel'
      case 'w':
        return 'water'
      case 'f':
        return 'forest'
      case 'i':
        return 'ice'
      case 'E':
        return 'base'
      default:
        return 'empty'
    }
  }

  get(col: number, row: number): TerrainType {
    if (col < 0 || col >= GRID || row < 0 || row >= GRID) return 'steel'
    return this.grid[row][col]
  }

  set(col: number, row: number, type: TerrainType): void {
    if (col >= 0 && col < GRID && row >= 0 && row < GRID) {
      this.grid[row][col] = type
    }
  }

  /** Destroy a single sub-block (set to empty) */
  destroy(col: number, row: number): void {
    if (col >= 0 && col < GRID && row >= 0 && row < GRID) {
      this.grid[row][col] = 'empty'
    }
  }

  /** Check if terrain blocks tank movement */
  static blocksTank(type: TerrainType): boolean {
    return type === 'brick' || type === 'steel' || type === 'water' || type === 'base'
  }

  /** Check if terrain blocks bullets */
  static blocksBullet(type: TerrainType): boolean {
    return type === 'brick' || type === 'steel' || type === 'base'
  }

  /** Check if terrain is destructible by bullets */
  static isDestructible(type: TerrainType): boolean {
    return type === 'brick' || type === 'base'
  }

  /** Check if terrain is steel (destructible only by power 2+ bullets) */
  static isSteel(type: TerrainType): boolean {
    return type === 'steel'
  }

  /** Pixel-to-grid conversion */
  static pxToCell(px: number): number {
    return Math.floor(px / CELL)
  }

  /** Check if the base is destroyed */
  isBaseDestroyed(): boolean {
    for (let r = 0; r < GRID; r++) {
      for (let c = 0; c < GRID; c++) {
        if (this.grid[r][c] === 'base') return false
      }
    }
    return true
  }

  /** Find base position in pixels */
  getBasePos(): { x: number; y: number } | null {
    for (let r = 0; r < GRID; r++) {
      for (let c = 0; c < GRID; c++) {
        if (this.grid[r][c] === 'base') {
          return { x: c * CELL, y: r * CELL }
        }
      }
    }
    return null
  }
}
