/**
 * central-breach.ts — pure central-breach risk predicate.
 *
 * A stage has central-breach risk when the central band (cols 11–13, rows
 * 0–22) has no steel, the default center spawn column is used, and the top
 * approach is open. Terrain only — deterministic, no RNG.
 */
import type { World } from '../game/World'
import { ENEMY_SPAWNS } from '../constants'

const BREACH_BAND_COL_MIN = 11
const BREACH_BAND_COL_MAX = 13
const BREACH_BAND_ROW_MAX = 22
const BREACH_OPEN_ROW_MAX = 9
const BREACH_OPEN_MIN_CELLS = 4

export function detectCentralBreachRisk(world: World): boolean {
  const tm = world.tileMap
  // Condition 1: no steel in the central band.
  for (let row = 0; row <= BREACH_BAND_ROW_MAX; row++) {
    for (let col = BREACH_BAND_COL_MIN; col <= BREACH_BAND_COL_MAX; col++) {
      if (tm.get(col, row) === 'steel') return false
    }
  }
  // Condition 2: an enemy spawn point at col 12±1.
  let centerSpawn = false
  for (let i = 0; i < ENEMY_SPAWNS.length; i++) {
    if (Math.abs(ENEMY_SPAWNS[i].col - 12) <= 1) {
      centerSpawn = true
      break
    }
  }
  if (!centerSpawn) return false
  // Condition 3: col 12 must have an open approach from the top.
  let openCells = 0
  for (let row = 0; row <= BREACH_OPEN_ROW_MAX; row++) {
    if (tm.get(12, row) === 'empty') openCells++
  }
  return openCells >= BREACH_OPEN_MIN_CELLS
}
