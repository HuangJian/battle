#!/usr/bin/env bun
/**
 * level-gen.ts — Procedural level generator for Battle City.
 *
 * Implements the layered generation pipeline (plan/Automated-Level-Design §Phase 2):
 *   Layer 0: 26×26 empty grid
 *   Layer 1: Base placement ('E' at 12-13, 24-25)
 *   Layer 2: Base defensive walls (classic U-shape, brick/steel)
 *   Layer 3: (implicit) Spawn areas reserved — clusters skip them
 *   Layer 4: Tactical cover clusters (brick/steel, size 3-8)
 *   Layer 5: Environmental terrain clusters (water/forest/ice, size 4-12)
 *   Layer 6: Detail noise (single-cell brick/steel, ~3%)
 *   Layer 7: Enemy formation (20 tanks, difficulty-based distribution)
 *   + Force override protected zones (clear spawns, ensure base)
 *   + A* reachability validation → corridor carving → retry (≤10)
 *
 * Determinism: all randomness flows through a seeded RNG (sub-seed per attempt).
 * Same seed + same difficulty + same theme ⇒ identical stage, always.
 *
 * Usage:
 *   bun tools/level/level-gen.ts --seed 42 --difficulty hard --theme forest
 *   bun tools/level/level-gen.ts --count 10 --difficulty chaos --theme mixed --pretty
 */

import { GRID, BASE_POS, PLAYER_SPAWN, ENEMY_SPAWNS } from '../../src/constants'
import type { StageData, TankKind } from '../../src/types'
import { TileMap } from '../../src/game/TileMap'
import { RNG } from '../../src/utils/RNG'
import { floodFill, type Cell } from '../../src/utils/pathfind'

// ============================================================
// Types
// ============================================================

export type Theme = 'forest' | 'ice' | 'fortress' | 'mixed'

export interface GenOptions {
  seed: number
  difficulty: string
  theme?: Theme
}

export interface ValidationResult {
  valid: boolean
  errors: string[]
}

interface ThemeConfig {
  /** Target fraction of total cells for each terrain type (0..1). */
  brickFraction: number
  steelFraction: number
  waterFraction: number
  forestFraction: number
  iceFraction: number
  /** Probability of single-cell noise per empty cell. */
  noiseFraction: number
}

// ============================================================
// Theme Configurations
// ============================================================

const THEME_CONFIGS: Record<Theme, ThemeConfig> = {
  forest: {
    brickFraction: 0.08,
    steelFraction: 0.02,
    waterFraction: 0.04,
    forestFraction: 0.3, // ≥30% forest
    iceFraction: 0.0,
    noiseFraction: 0.03,
  },
  ice: {
    brickFraction: 0.08,
    steelFraction: 0.03,
    waterFraction: 0.03,
    forestFraction: 0.0,
    iceFraction: 0.25, // ≥25% ice
    noiseFraction: 0.03,
  },
  fortress: {
    brickFraction: 0.1,
    steelFraction: 0.2, // ≥20% steel
    waterFraction: 0.02,
    forestFraction: 0.0,
    iceFraction: 0.0,
    noiseFraction: 0.03,
  },
  mixed: {
    brickFraction: 0.1,
    steelFraction: 0.05,
    waterFraction: 0.06,
    forestFraction: 0.1,
    iceFraction: 0.08,
    noiseFraction: 0.03,
  },
}

// ============================================================
// Reserved cells (spawn areas + base + defense positions)
// ============================================================

/** Cells where clusters must NOT place terrain. */
const RESERVED_CELLS: Set<string> = (() => {
  const set = new Set<string>()
  const add2x2 = (col: number, row: number) => {
    for (let dr = 0; dr <= 1; dr++)
      for (let dc = 0; dc <= 1; dc++) set.add(`${col + dc},${row + dr}`)
  }
  for (const s of ENEMY_SPAWNS) add2x2(s.col, s.row)
  add2x2(PLAYER_SPAWN.col, PLAYER_SPAWN.row)
  add2x2(BASE_POS.col, BASE_POS.row)
  // Base defense positions (classic U-shape)
  for (const [c, r] of [
    [11, 23],
    [12, 23],
    [13, 23],
    [14, 23],
    [11, 24],
    [14, 24],
    [11, 25],
    [14, 25],
  ]) {
    set.add(`${c},${r}`)
  }
  return set
})()

function isReserved(col: number, row: number): boolean {
  return RESERVED_CELLS.has(`${col},${row}`)
}

// ============================================================
// Grid helpers
// ============================================================

type Grid = string[][]

function createEmptyGrid(): Grid {
  return Array.from({ length: GRID }, () => Array.from({ length: GRID }, () => '.'))
}

function gridToTiles(grid: Grid): string[] {
  return grid.map((row) => row.join(''))
}

function tilesToGrid(tiles: string[]): Grid {
  return tiles.map((line) => line.split(''))
}

// ============================================================
// Layer 1: Base placement
// ============================================================

function placeBase(grid: Grid): void {
  grid[24][12] = 'E'
  grid[24][13] = 'E'
  grid[25][12] = 'E'
  grid[25][13] = 'E'
}

// ============================================================
// Layer 2: Base defensive walls (classic U-shape)
// ============================================================

function placeBaseDefense(grid: Grid, rng: RNG, config: ThemeConfig): void {
  const defenseCells: Array<[number, number]> = [
    [11, 23],
    [12, 23],
    [13, 23],
    [14, 23],
    [11, 24],
    [14, 24],
    [11, 25],
    [14, 25],
  ]
  const steelChance = config.steelFraction > 0.15 ? 0.5 : 0.2
  for (const [c, r] of defenseCells) {
    grid[r][c] = rng.next() < steelChance ? 's' : 'b'
  }
}

// ============================================================
// Layer 4-5: Cluster placement
// ============================================================

function findEmptyCell(grid: Grid, rng: RNG): Cell | null {
  const candidates: Cell[] = []
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) {
      if (grid[r][c] === '.' && !isReserved(c, r)) {
        candidates.push({ col: c, row: r })
      }
    }
  }
  if (candidates.length === 0) return null
  return rng.pick(candidates)
}

const DIRS4: ReadonlyArray<readonly [number, number]> = [
  [0, -1],
  [0, 1],
  [-1, 0],
  [1, 0],
]

/**
 * Grow a cluster of `char` terrain from a random seed cell.
 * Uses frontier-based organic growth: randomly picks cells from the frontier
 * to create natural-looking shapes. Returns the number of cells placed.
 */
function growCluster(grid: Grid, rng: RNG, char: string, targetSize: number): number {
  const seed = findEmptyCell(grid, rng)
  if (!seed) return 0

  const cluster: Cell[] = [seed]
  const frontier: Cell[] = []
  const inCluster = new Set<string>([`${seed.col},${seed.row}`])
  const inFrontier = new Set<string>()

  grid[seed.row][seed.col] = char

  const addNeighbors = (cell: Cell) => {
    for (const [dc, dr] of DIRS4) {
      const nc = cell.col + dc
      const nr = cell.row + dr
      if (nc < 0 || nc >= GRID || nr < 0 || nr >= GRID) continue
      const k = `${nc},${nr}`
      if (inCluster.has(k) || inFrontier.has(k)) continue
      if (grid[nr][nc] !== '.' || isReserved(nc, nr)) continue
      frontier.push({ col: nc, row: nr })
      inFrontier.add(k)
    }
  }

  addNeighbors(seed)

  while (cluster.length < targetSize && frontier.length > 0) {
    const idx = rng.int(frontier.length)
    const cell = frontier.splice(idx, 1)[0]
    const k = `${cell.col},${cell.row}`
    inFrontier.delete(k)
    grid[cell.row][cell.col] = char
    cluster.push(cell)
    inCluster.add(k)
    addNeighbors(cell)
  }

  return cluster.length
}

/**
 * Place terrain of a specific type until the target coverage fraction is
 * reached. Each cluster is randomly sized between minCluster and maxCluster.
 */
function placeTerrainType(
  grid: Grid,
  rng: RNG,
  char: string,
  targetFraction: number,
  minCluster: number,
  maxCluster: number,
): number {
  if (targetFraction <= 0) return 0
  const targetCells = Math.floor(GRID * GRID * targetFraction)
  let placed = 0
  let attempts = 0
  const maxAttempts = 60

  while (placed < targetCells && attempts < maxAttempts) {
    attempts++
    const size = minCluster + rng.int(maxCluster - minCluster + 1)
    placed += growCluster(grid, rng, char, size)
  }
  return placed
}

// ============================================================
// Layer 6: Noise
// ============================================================

function placeNoise(grid: Grid, rng: RNG, noiseFraction: number): void {
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) {
      if (grid[r][c] !== '.' || isReserved(c, r)) continue
      if (rng.next() < noiseFraction) {
        grid[r][c] = rng.next() < 0.7 ? 'b' : 's'
      }
    }
  }
}

// ============================================================
// Force overrides (clear spawn areas, ensure base)
// ============================================================

function forceOverrides(grid: Grid): void {
  for (const spawn of ENEMY_SPAWNS) {
    for (let dr = 0; dr <= 1; dr++) {
      for (let dc = 0; dc <= 1; dc++) {
        grid[spawn.row + dr][spawn.col + dc] = '.'
      }
    }
  }
  for (let dr = 0; dr <= 1; dr++) {
    for (let dc = 0; dc <= 1; dc++) {
      grid[PLAYER_SPAWN.row + dr][PLAYER_SPAWN.col + dc] = '.'
    }
  }
  placeBase(grid)
}

// ============================================================
// Layer 7: Enemy formation
// ============================================================

const ENEMY_COUNTS: Record<string, [number, number, number, number]> = {
  // [basic, fast, power, armor] out of 20
  relax: [14, 4, 1, 1],
  classic: [10, 5, 3, 2],
  hard: [7, 5, 4, 4],
  chaos: [4, 6, 5, 5],
}

function generateEnemies(rng: RNG, difficulty: string): TankKind[] {
  const counts = ENEMY_COUNTS[difficulty] ?? ENEMY_COUNTS.classic
  const enemies: TankKind[] = []
  for (let i = 0; i < counts[0]; i++) enemies.push('basic')
  for (let i = 0; i < counts[1]; i++) enemies.push('fast')
  for (let i = 0; i < counts[2]; i++) enemies.push('power')
  for (let i = 0; i < counts[3]; i++) enemies.push('armor')
  for (let i = enemies.length - 1; i > 0; i--) {
    const j = rng.int(i + 1)
    ;[enemies[i], enemies[j]] = [enemies[j], enemies[i]]
  }
  return enemies
}

// ============================================================
// Validation
// ============================================================

/**
 * Validate a generated stage against hard constraints:
 * 1. Base exists at the correct position (12-13, 24-25)
 * 2. All spawn 2×2 areas are clear of blocking terrain
 * 3. All enemy spawns are reachable from the player spawn (floodFill)
 */
export function validateStage(stage: StageData): ValidationResult {
  const errors: string[] = []

  // 1. Base exists at correct position
  const baseCells = [
    { col: 12, row: 24 },
    { col: 13, row: 24 },
    { col: 12, row: 25 },
    { col: 13, row: 25 },
  ]
  for (const cell of baseCells) {
    const ch = stage.tiles[cell.row]?.[cell.col]
    if (ch !== 'E') {
      errors.push(`Base cell (${cell.col},${cell.row}) is '${ch}', expected 'E'`)
    }
  }

  // 2. Spawn areas are clear (brick/steel/water/base block tanks; ice/forest don't)
  const blockingChars = new Set(['b', 's', 'w', 'E'])
  const spawns = [
    ...ENEMY_SPAWNS.map((s) => ({ col: s.col, row: s.row, name: 'enemy' })),
    { col: PLAYER_SPAWN.col, row: PLAYER_SPAWN.row, name: 'player' },
  ]
  for (const spawn of spawns) {
    for (let dr = 0; dr <= 1; dr++) {
      for (let dc = 0; dc <= 1; dc++) {
        const ch = stage.tiles[spawn.row + dr]?.[spawn.col + dc] ?? '.'
        if (blockingChars.has(ch)) {
          errors.push(
            `${spawn.name} spawn (${spawn.col + dc},${spawn.row + dr}) blocked by '${ch}'`,
          )
        }
      }
    }
  }

  // 3. Connectivity: all enemy spawns reachable from player spawn
  const tm = new TileMap()
  tm.loadStage(stage)
  const reachable = floodFill(tm, { col: PLAYER_SPAWN.col, row: PLAYER_SPAWN.row })

  if (reachable.size === 0) {
    errors.push('Player spawn is blocked — floodFill returned empty set')
  } else {
    for (const spawn of ENEMY_SPAWNS) {
      let found = false
      for (let dr = 0; dr <= 1; dr++) {
        for (let dc = 0; dc <= 1; dc++) {
          if (reachable.has(`${spawn.col + dc},${spawn.row + dr}`)) {
            found = true
            break
          }
        }
        if (found) break
      }
      if (!found) {
        errors.push(`Enemy spawn (${spawn.col},${spawn.row}) not reachable from player`)
      }
    }
  }

  return { valid: errors.length === 0, errors }
}

// ============================================================
// Path carving (fallback when validation fails)
// ============================================================

/**
 * Carve a 2-cell-wide L-shaped corridor from a spawn point toward row 12.
 * Vertical: clears cols [from.col, from.col+1] from from.row to row 12.
 * Horizontal: clears rows [12, 13] from from.col to col 12.
 * This guarantees a 2×2 passable corridor for tank traversal.
 */
function carveCorridor(grid: Grid, from: Cell): void {
  const endRow = 12
  for (let r = from.row; r <= endRow; r++) {
    if (grid[r][from.col] !== 'E') grid[r][from.col] = '.'
    if (from.col + 1 < GRID && grid[r][from.col + 1] !== 'E') grid[r][from.col + 1] = '.'
  }
  const minCol = Math.min(from.col, 12)
  const maxCol = Math.max(from.col, 12)
  for (let c = minCol; c <= maxCol; c++) {
    if (grid[endRow][c] !== 'E') grid[endRow][c] = '.'
    if (endRow + 1 < GRID && grid[endRow + 1][c] !== 'E') grid[endRow + 1][c] = '.'
  }
}

/** Check if all enemy spawns are reachable. */
function allSpawnsReachable(reachable: Set<string>): boolean {
  for (const spawn of ENEMY_SPAWNS) {
    let found = false
    for (let dr = 0; dr <= 1; dr++) {
      for (let dc = 0; dc <= 1; dc++) {
        if (reachable.has(`${spawn.col + dc},${spawn.row + dr}`)) {
          found = true
          break
        }
      }
      if (found) break
    }
    if (!found) return false
  }
  return true
}

/** Compute floodFill from player spawn on a grid. */
function computeReachable(grid: Grid): Set<string> {
  const tiles = gridToTiles(grid)
  const tm = new TileMap()
  tm.loadStage({ id: 0, name: '', tiles, enemies: [] })
  return floodFill(tm, { col: PLAYER_SPAWN.col, row: PLAYER_SPAWN.row })
}

/**
 * Carve a 2-cell-wide vertical corridor from the player spawn (8, 24) up to
 * row 12, connecting the player to the horizontal corridor network at row 12.
 * Without this, spawn corridors reaching row 12 still can't connect to the
 * player at row 24 in dense-steel maps (e.g. fortress theme).
 */
function carvePlayerCorridor(grid: Grid): void {
  const col0 = PLAYER_SPAWN.col // 8
  const col1 = PLAYER_SPAWN.col + 1 // 9
  for (let r = 12; r <= PLAYER_SPAWN.row; r++) {
    if (grid[r][col0] !== 'E') grid[r][col0] = '.'
    if (grid[r][col1] !== 'E') grid[r][col1] = '.'
  }
}

/**
 * Carve corridors for unreachable spawns. Returns true if all spawns
 * are reachable after carving.
 */
function fixConnectivity(grid: Grid): boolean {
  let reachable = computeReachable(grid)
  if (reachable.size === 0) return false
  if (allSpawnsReachable(reachable)) return true

  // Carve corridors from unreachable spawns down to row 12
  for (const spawn of ENEMY_SPAWNS) {
    let found = false
    for (let dr = 0; dr <= 1; dr++) {
      for (let dc = 0; dc <= 1; dc++) {
        if (reachable.has(`${spawn.col + dc},${spawn.row + dr}`)) {
          found = true
          break
        }
      }
      if (found) break
    }
    if (!found) {
      carveCorridor(grid, { col: spawn.col, row: spawn.row })
    }
  }

  // Carve a corridor from the player spawn up to row 12 so the player
  // can reach the horizontal corridor network that connects the spawns.
  carvePlayerCorridor(grid)

  reachable = computeReachable(grid)
  return reachable.size > 0 && allSpawnsReachable(reachable)
}

// ============================================================
// Main generation function
// ============================================================

const MAX_RETRIES = 10

/**
 * Generate a single stage deterministically.
 *
 * Uses a sub-seed per retry attempt (seed * 1000 + attempt) so that:
 * - Same input always produces the same output (determinism)
 * - Each retry explores different terrain (variety)
 */
export function generateStage(opts: GenOptions): StageData {
  const theme = opts.theme ?? 'mixed'
  const config = THEME_CONFIGS[theme] ?? THEME_CONFIGS.mixed
  const themeName = theme.charAt(0).toUpperCase() + theme.slice(1)

  let lastStage: StageData | null = null

  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    const rng = new RNG(opts.seed * 1000 + attempt)

    // Layer 0: empty grid
    const grid = createEmptyGrid()

    // Layer 1: base
    placeBase(grid)

    // Layer 2: base defense
    placeBaseDefense(grid, rng, config)

    // Layer 4: tactical cover (brick/steel clusters)
    placeTerrainType(grid, rng, 'b', config.brickFraction, 3, 8)
    placeTerrainType(grid, rng, 's', config.steelFraction, 3, 8)

    // Layer 5: environmental terrain (water/forest/ice clusters)
    placeTerrainType(grid, rng, 'w', config.waterFraction, 4, 12)
    placeTerrainType(grid, rng, 'f', config.forestFraction, 4, 12)
    placeTerrainType(grid, rng, 'i', config.iceFraction, 4, 12)

    // Layer 6: noise
    placeNoise(grid, rng, config.noiseFraction)

    // Force override protected zones
    forceOverrides(grid)

    // Layer 7: enemy formation
    const enemies = generateEnemies(rng, opts.difficulty)

    const stage: StageData = {
      id: 1000 + opts.seed,
      name: `${themeName} ${opts.seed}`,
      tiles: gridToTiles(grid),
      enemies,
    }
    lastStage = stage

    // Validate
    const validation = validateStage(stage)
    if (validation.valid) return stage

    // Try path carving as a fix
    const carvedGrid = tilesToGrid(stage.tiles)
    if (fixConnectivity(carvedGrid)) {
      forceOverrides(carvedGrid)
      const carvedStage: StageData = { ...stage, tiles: gridToTiles(carvedGrid) }
      if (validateStage(carvedStage).valid) return carvedStage
    }
  }

  return lastStage!
}

/** Generate multiple stages with sequential seeds. */
export function generateStages(
  count: number,
  difficulty: string,
  theme?: Theme,
  baseSeed = 1,
): StageData[] {
  const stages: StageData[] = []
  for (let i = 0; i < count; i++) {
    stages.push(generateStage({ seed: baseSeed + i, difficulty, theme }))
  }
  return stages
}

// ============================================================
// Statistics helpers (for testing / reporting)
// ============================================================

export interface StageStats {
  terrainCounts: Record<string, number>
  terrainFractions: Record<string, number>
  totalTerrain: number
  totalCells: number
}

export function computeStats(stage: StageData): StageStats {
  const counts: Record<string, number> = {}
  for (let r = 0; r < GRID; r++) {
    const line = stage.tiles[r] || ''
    for (let c = 0; c < GRID; c++) {
      const ch = line[c] || '.'
      counts[ch] = (counts[ch] ?? 0) + 1
    }
  }
  const totalCells = GRID * GRID
  const fractions: Record<string, number> = {}
  for (const [k, v] of Object.entries(counts)) {
    fractions[k] = v / totalCells
  }
  const totalTerrain = totalCells - (counts['.'] ?? 0)
  return { terrainCounts: counts, terrainFractions: fractions, totalTerrain, totalCells }
}

// ============================================================
// CLI
// ============================================================

if (import.meta.main) {
  function arg(name: string, fallback?: string): string | undefined {
    const i = process.argv.indexOf(`--${name}`)
    return i >= 0 ? process.argv[i + 1] : fallback
  }

  const count = parseInt(arg('count', '1')!, 10)
  const seed = parseInt(arg('seed', '1')!, 10)
  const difficulty = arg('difficulty', 'hard')!
  const theme = (arg('theme', 'mixed') ?? 'mixed') as Theme
  const pretty = process.argv.includes('--pretty')
  const outputFile = arg('output')

  if (count === 1) {
    const stage = generateStage({ seed, difficulty, theme })
    const validation = validateStage(stage)
    const stats = computeStats(stage)
    const output = { stage, validation, stats }
    if (outputFile) {
      Bun.write(outputFile, JSON.stringify([stage], null, pretty ? 2 : 0))
      process.stderr.write(`[level-gen] Wrote 1 stage to ${outputFile}\n`)
    }
    console.log(pretty ? JSON.stringify(output, null, 2) : JSON.stringify(output))
  } else {
    const stages = generateStages(count, difficulty, theme, seed)
    if (outputFile) {
      Bun.write(outputFile, JSON.stringify(stages, null, pretty ? 2 : 0))
      process.stderr.write(`[level-gen] Wrote ${stages.length} stages to ${outputFile}\n`)
    }
    const results = stages.map((s) => {
      const v = validateStage(s)
      const stats = computeStats(s)
      return { id: s.id, name: s.name, valid: v.valid, errors: v.errors, stats }
    })
    console.log(pretty ? JSON.stringify(results, null, 2) : JSON.stringify(results))
  }
}
