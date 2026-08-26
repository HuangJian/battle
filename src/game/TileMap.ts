import type { TerrainType, StageData } from '../types'
import { GRID, CELL } from '../constants'

/**
 * TileMap — 26×26 grid of sub-blocks.
 * Each sub-block is CELL (16px) wide.
 * Stage data is already a 26×26 grid (one char per sub-block), so each char
 * maps 1:1 to a sub-block.
 */
export class TileMap {
  /** grid[row][col] — terrain type per sub-block */
  grid: TerrainType[][]

  /**
   * Monotonic terrain revision counter (perf §127). Bumped on EVERY terrain
   * mutation (loadStage / set / destroy / destroyAllBaseCells) and on
   * snapshot restore (restoreWorld writes the grid directly, bypassing
   * set/destroy). Lets simulation-side cross-tick caches that depend on the
   * terrain (the §127 replan cache) invalidate EXACTLY when the terrain
   * changes, instead of bounding staleness with a safety timer — turning a
   * bounded-stale cache into a strict pure memo (byte-identical).
   * Renderer-only consumers should keep using `dirty`/`dirtyCells`.
   */
  revision = 0

  /**
   * Set to true when terrain changes; renderer checks this to invalidate its cache.
   *
   * ── Registered exception to "presentation never mutates World" (§2.5) ──
   * The RENDERER is the consumer of this flag: GameRendererTerrain's
   * updateTerrainCache clears `dirty` (and drains `dirtyCells`) after
   * rebuilding its cache. This is a presentation-side consumption protocol,
   * not a simulation write: who sets it — TileMap mutations + snapshot
   * restore; who consumes it — the renderer, once per repaint, in
   * PresentationLayer.render → updateTerrainCache; order constraint — the
   * clear must happen only AFTER the rebuild that observed it. Do not add a
   * second consumer without restating this contract at both ends.
   */
  dirty = true

  /**
   * Indices (row * GRID + col) of individual cells that changed since the last
   * incremental terrain-cache redraw. Lets the renderer redraw only the
   * affected cells instead of the whole 26×26 field. Cleared after consumption.
   */
  dirtyCells: number[] = []

  /** Cached positions of all base cells (usually 4 cells at rows 24-25, cols 12-13). */
  private baseCells: Array<{ c: number; r: number }> = []
  /** Cached base position in pixels (first base cell found). */
  private basePos: { x: number; y: number } | null = null
  /** Whether any base cell is still intact. */
  private baseAlive = false
  /** Whether the stage contains any water (boat power-up only drops on water stages). */
  private waterPresent = false

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
    for (let r = 0; r < GRID; r++) {
      const line = tiles[r] || ''
      for (let c = 0; c < GRID; c++) {
        const ch = line[c] || '.'
        this.grid[r][c] = this.charToTerrain(ch)
      }
    }

    this.rebuildBaseCache()
    this.dirty = true
    this.dirtyCells.length = 0
    this.revision++ // §127: full terrain reset
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
      const prev = this.grid[row][col]
      if (prev === type) return
      this.grid[row][col] = type
      // Water changes require re-scanning the water cell list → full rebuild.
      if (prev === 'water' || type === 'water') {
        this.dirty = true
      } else {
        this.dirtyCells.push(row * GRID + col)
      }
      this.revision++ // §127: terrain changed
    }
  }

  /** Destroy a single sub-block (set to empty) */
  destroy(col: number, row: number): void {
    if (col >= 0 && col < GRID && row >= 0 && row < GRID) {
      const type = this.grid[row][col]
      if (type === 'empty') return // nothing to destroy — no change
      this.grid[row][col] = 'empty'
      this.revision++ // §127: terrain changed
      if (type === 'base') {
        // Check if any cached base cells remain intact (O(4) instead of O(676))
        this.baseAlive = false
        for (const cell of this.baseCells) {
          if (this.grid[cell.r][cell.c] === 'base') {
            this.baseAlive = true
            break
          }
        }
        // Base destruction changes global ruin rendering → full rebuild.
        this.dirty = true
      } else {
        // Incremental redraw: only this one cell changed (no full rebuild).
        this.dirtyCells.push(row * GRID + col)
      }
    }
  }

  /** Destroy all base cells at once (when any base cell is hit). O(4) instead of O(676). */
  destroyAllBaseCells(): void {
    for (const cell of this.baseCells) {
      this.grid[cell.r][cell.c] = 'empty'
      this.dirtyCells.push(cell.r * GRID + cell.c)
    }
    this.baseAlive = false
    this.dirty = true
    this.revision++ // §127: terrain changed
  }

  /**
   * Mark the base cells dirty so the renderer repaints the (damaged) crystal
   * after a non-fatal hit. Used by Simulation.damageBase — only the visible
   * damage overlay changes; the base stays intact.
   */
  markBaseDamaged(): void {
    for (const cell of this.baseCells) {
      this.dirtyCells.push(cell.r * GRID + cell.c)
    }
  }

  /** Rebuild cached base state from the grid. Called after loadStage and snapshot restore. */
  rebuildBaseCache(): void {
    this.baseCells = []
    this.basePos = null
    this.baseAlive = false
    this.waterPresent = false
    for (let r = 0; r < GRID; r++) {
      for (let c = 0; c < GRID; c++) {
        const t = this.grid[r][c]
        if (t === 'base') {
          this.baseAlive = true
          this.baseCells.push({ c, r })
          if (!this.basePos) {
            this.basePos = { x: c * CELL, y: r * CELL }
          }
        } else if (t === 'water') {
          this.waterPresent = true
        }
      }
    }
  }

  /** True if the current stage has any water. Water is static during a stage,
   *  so this is cached at loadStage/snapshot-restore (rebuildBaseCache). */
  hasWater(): boolean {
    return this.waterPresent
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

  /** Check if the base is destroyed — O(1) via cached state.
   *  Gap B (plan/God-AI-Curriculum §3.5 影响 3): when the stage has no base at
   *  all, returns false — a non-existent base is not "destroyed". Without this
   *  guard, no-base stages immediately trigger gameover on the first tick. */
  isBaseDestroyed(): boolean {
    return this.basePos !== null && !this.baseAlive
  }

  /** Find base position in pixels — O(1) via cached state */
  getBasePos(): { x: number; y: number } | null {
    return this.basePos
  }

  /**
   * Whether the current stage contains a base at all (plan/God-AI-Curriculum §3
   * Gap B). Curriculum "no-base" stages omit the `E` tile entirely; the God AI
   * and enemy AI use this to skip all base-defense logic instead of guarding a
   * ghost base at the hardcoded `BASE_POS`.
   */
  hasBase(): boolean {
    return this.basePos !== null
  }

  /**
   * Returns true if (c, r) is the TOP-LEFT cell of a contiguous base block.
   * The base is drawn as a single crystal spanning its 2×2 area, so only this
   * cell triggers the full-block draw. A cell qualifies when it is 'base' and
   * has no 'base' neighbour to its left or above.
   */
  isBaseTopLeft(c: number, r: number): boolean {
    if (this.grid[r]?.[c] !== 'base') return false
    const left = c > 0 ? this.grid[r][c - 1] : 'empty'
    const up = r > 0 ? this.grid[r - 1][c] : 'empty'
    return left !== 'base' && up !== 'base'
  }
}
