#!/usr/bin/env bun
/**
 * opening-dose.ts — S0.5 tick-0 reachability probe for the hybrid opening.
 *
 * Answers one narrow question before spending eval budget: at stage load, can
 * the deterministic controller actually do anything?
 *   - P1↔P2 open corridor? (ring hard-blocked)
 *   - open lane to any enemy spawn?
 *   - if not, how many bricks does the cheapest legal dig need
 *     (central window off / best single 2×2 window)?
 *
 * This is a reachability census, not a win-rate judgment. It does not run the
 * simulation and does not touch evaluation thresholds.
 *
 * Usage:
 *   bun tools/diag/opening-dose.ts
 *   bun tools/diag/opening-dose.ts --stages 20,8,28
 *   bun tools/diag/opening-dose.ts --stages all
 */
import { World } from '../../src/game/World'
import { STAGES } from '../../src/config/stages'
import { findPath } from '../../src/utils/pathfind'
import { RING_CELLS } from '../../src/utils/base-ring'
import { computePlayer2SpawnCol } from '../../src/utils/helpers'
import { BASE_POS, CELL, GRID, type Direction } from '../../src/constants'
import { arg } from '../lib/cli'
import { parseStageSpec, StageSpecError } from '../lib/stage-spec'
import { DIR_DX, DIR_DY, dirIdx } from '../../src/utils/direction'
import type { TileMap } from '../../src/game/TileMap'
import type { Cell } from '../../src/utils/pathfind'

const spec = arg('stages') ?? '20'
let stages: number[]
try {
  stages = parseStageSpec(spec, STAGES.length)
} catch (e) {
  console.error(e instanceof StageSpecError ? e.message : `opening-dose: bad --stages: ${spec}`)
  process.exit(1)
}

const ring: Cell[] = RING_CELLS.map((c) => ({ col: c.col, row: c.row }))
const ringKeys = new Set(RING_CELLS.map((c) => c.row * GRID + c.col))
const BRICK_COST = GRID * GRID + 1

interface Dig {
  bricks: number
  pathLength: number
}

function cellKey(col: number, row: number): number {
  return row * GRID + col
}

function countBricks(tm: TileMap, from: Cell, path: readonly Direction[]): number {
  const seen = new Set<number>()
  let col = from.col
  let row = from.row
  for (const d of path) {
    const di = dirIdx(d)
    col += DIR_DX[di]
    row += DIR_DY[di]
    if (col < 0 || row < 0 || col + 1 >= GRID || row + 1 >= GRID) continue
    for (let dr = 0; dr <= 1; dr++) {
      for (let dc = 0; dc <= 1; dc++) {
        if (tm.grid[row + dr][col + dc] === 'brick') seen.add(cellKey(col + dc, row + dr))
      }
    }
  }
  return seen.size
}

function hardCells(tm: TileMap, windowRow: number | null): Cell[] {
  const hard = ring.slice()
  for (let row = 0; row < GRID; row++) {
    if (windowRow !== null && (row === windowRow || row === windowRow + 1)) continue
    for (let col = BASE_POS.col; col <= BASE_POS.col + 1; col++) {
      if (ringKeys.has(cellKey(col, row))) continue
      if (tm.grid[row][col] === 'brick') hard.push({ col, row })
    }
  }
  return hard
}

function digPlan(tm: TileMap, from: Cell, to: Cell, windowRow: number | null): Dig | null {
  const path = findPath(tm, from, to, {
    breakBrick: true,
    hardBlockedCells: hardCells(tm, windowRow),
    brickCost: BRICK_COST,
  })
  if (!path) return null
  return { bricks: countBricks(tm, from, path), pathLength: path.length }
}

function openPath(tm: TileMap, from: Cell, to: Cell): boolean {
  return findPath(tm, from, to, { hardBlockedCells: ring }) !== null
}

function fmt(n: number | null): string {
  return n === null ? '-' : String(n)
}

let p1p2Open = 0
let anyEnemyOpen = 0
let digReachable = 0

console.log('stage  p1p2   enemyOpen  enemyDigNoWin  enemyDigBestWin(bricks,row)')
for (const idx of stages) {
  const world = new World()
  world.loadStageData(STAGES[idx], idx)
  const tm = world.tileMap
  const spawn = world.playerSpawnPoint
  const from: Cell = { col: spawn.col, row: spawn.row }
  const p2: Cell = { col: computePlayer2SpawnCol(spawn.col), row: spawn.row }

  const p1p2 = openPath(tm, from, p2)
  const enemy = world.enemySpawnPoints.map((p) => ({
    col: Math.floor(p.x / CELL),
    row: Math.floor(p.y / CELL),
  }))
  const openEnemy = enemy.filter((e) => openPath(tm, from, e)).length

  // Controller phase 2 only digs when NO enemy spawn is open. Report the
  // cheapest legal enemy dig (same lexicographic objective as the controller).
  let enemyNoWindow: Dig | null = null
  let enemyBest: Dig | null = null
  let enemyBestRow: number | null = null
  for (const e of enemy) {
    const d = digPlan(tm, from, e, null)
    if (
      d &&
      (!enemyNoWindow ||
        d.bricks < enemyNoWindow.bricks ||
        (d.bricks === enemyNoWindow.bricks && d.pathLength < enemyNoWindow.pathLength))
    ) {
      enemyNoWindow = d
    }
    for (let row = 0; row <= GRID - 2; row++) {
      const w = digPlan(tm, from, e, row)
      if (!w) continue
      if (
        !enemyBest ||
        w.bricks < enemyBest.bricks ||
        (w.bricks === enemyBest.bricks && w.pathLength < enemyBest.pathLength)
      ) {
        enemyBest = w
        enemyBestRow = row
      }
    }
  }

  if (p1p2) p1p2Open++
  if (openEnemy > 0) anyEnemyOpen++
  if (enemyNoWindow || enemyBest) digReachable++

  console.log(
    `S${String(idx + 1).padEnd(4)}  ${p1p2 ? 'open ' : 'blocked'}  ${openEnemy}/${enemy.length}      ` +
      `${fmt(enemyNoWindow ? enemyNoWindow.bricks : null).padEnd(14)} ` +
      `${enemyBest ? `${enemyBest.bricks},${enemyBestRow}` : '-'}`,
  )
}

console.log(
  `\n${stages.length} stages: p1p2 open ${p1p2Open}, any enemy open ${anyEnemyOpen}, ` +
    `dig reachable ${digReachable}`,
)
