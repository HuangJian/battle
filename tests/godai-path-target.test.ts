import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { CLASSIC_MODEL_PARAMS, GUARD_GOD_AI_PARAMS } from '../src/ai/god/params'
import { RNG } from '../src/utils/RNG'
import { CELL, GRID } from '../src/constants'
import type { Tank } from '../src/types'

/**
 * §171: path-aware target selection (路径长度感知目标选择) — unit tests.
 *
 * Root cause (divergence probe, DECISIONS §171): the normal hunt branch
 * scores enemies by Manhattan distance; on maze stages the Manhattan-nearest
 * enemy is often wall-separated (true path cost 6.3× larger in losses than
 * wins). pathTargetMode=1 scores by TRUE travel cost (corridor length, dig
 * + penalty, unreachable + penalty).
 *
 * Geometry: player spawns at (8,24). A full-height brick wall on cols 10-11
 * splits the field. Enemy A sits wall-separated on the right (Manhattan
 * nearer), enemy B open on the left (path nearer). OFF picks A; ON scores
 * by true travel cost (corridor length; dig + 1000 penalty) and picks B.
 * Isolation: chokepointMode=0 + baseGuardAnchorMode=0, enemies high up so
 * no base threat, enemiesRemaining=20 (canHunt false → normal hunt branch).
 */

function setupWorld(params: Partial<typeof DEFAULT_GOD_AI_PARAMS> = {}): {
  world: World
  input: GodAIInput
} {
  const world = new World()
  world.rng = new RNG(42)
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS, ...params })
  const sim = new Simulation(world, new Input())
  world.startGame('hard', 'modern', 0)
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) world.tileMap.grid[r][c] = 'empty'
  }
  for (const r of [24, 25]) {
    for (const c of [12, 13]) world.tileMap.grid[r][c] = 'base'
  }
  void sim
  world.enemiesRemaining = 20 // keep canHunt false → normal hunt branch
  input.hasBase = world.tileMap.hasBase()
  input.reset()
  return { world, input }
}

/** Full-height brick wall on cols 10-11 — no corridor around it. */
function buildWall(world: World): void {
  for (let r = 0; r < GRID; r++) {
    world.tileMap.grid[r][10] = 'brick'
    world.tileMap.grid[r][11] = 'brick'
  }
  world.tileMap.revision++
}

/** Open a 2×2 gate at rows 12-13 so the right side becomes corridor-reachable. */
function openGate(world: World): void {
  for (const r of [12, 13]) {
    world.tileMap.grid[r][10] = 'empty'
    world.tileMap.grid[r][11] = 'empty'
  }
  world.tileMap.revision++
}

/** Low horizontal wall on rows 21-22 cols 4-11 — passable only via the
 * left gap (cols 0-3); the base pinches cols 12-13 at rows 24-25, so the
 * only route above the wall goes around the left end. */
function buildLowWall(world: World): void {
  for (const r of [21, 22]) {
    for (let c = 4; c <= 11; c++) world.tileMap.grid[r][c] = 'brick'
  }
  world.tileMap.revision++
}

function placeEnemy(world: World, col: number, row: number): Tank {
  const enemy = world.createTank('basic', col * CELL, row * CELL, 'down')
  enemy.alive = true
  enemy.spawnTimer = 0
  world.tanks.push(enemy)
  return enemy
}

function refresh(input: GodAIInput, world: World): void {
  input._baseUnderThreatCache = null
  input._selTargetValid = false
  input._enemies.length = 0
  for (const t of world.tanks) {
    if (t.alive && t.spawnTimer <= 0) input._enemies.push(t)
  }
}

function cellOf(input: GodAIInput, t: Tank): { col: number; row: number } {
  const c = input.tankCell(t)
  return { col: c.col, row: c.row } // copy — tankCell shares a buffer
}

const ISOLATED = { chokepointMode: 0, baseGuardAnchorMode: 0 }

describe('§171: path-aware target selection (路径长度感知目标选择)', () => {
  it('OFF (default 0): the wall-separated Manhattan-nearest enemy is picked', () => {
    const { world, input } = setupWorld(ISOLATED)
    buildWall(world)
    const a = placeEnemy(world, 14, 6) // wall-separated, manhattan 24
    placeEnemy(world, 2, 2) // open side, manhattan 28
    refresh(input, world)
    const t = input.selectTarget(input.playerCell())
    expect(t).not.toBeNull()
    expect(t!.col).toBe(cellOf(input, a).col)
    expect(t!.row).toBe(cellOf(input, a).row)
    // The path-cost memo is untouched when the knob is off.
    for (let s = 0; s < 8; s++) expect(input._pathCostEId[s]).toBe(-1)
  })

  it('ON: the corridor-reachable enemy beats the wall-separated Manhattan pick', () => {
    const { world, input } = setupWorld({ ...ISOLATED, pathTargetMode: 1 })
    buildWall(world)
    const a = placeEnemy(world, 14, 6) // dig-only → cost ≥ 100
    const b = placeEnemy(world, 2, 2) // corridor cost 28
    refresh(input, world)
    const t = input.selectTarget(input.playerCell())
    expect(t!.col).toBe(cellOf(input, b).col)
    expect(t!.row).toBe(cellOf(input, b).row)
    // Both enemies landed in the memo.
    const filled: number[] = []
    for (let s = 0; s < 8; s++) if (input._pathCostEId[s] >= 0) filled.push(input._pathCostEId[s])
    expect(filled).toContain(a.id)
    expect(filled).toContain(b.id)
  })

  it('ON: corridor-reachable enemies are reordered by true path length', () => {
    const { world, input } = setupWorld({ ...ISOLATED, pathTargetMode: 1 })
    buildLowWall(world)
    // X (6,12): manhattan 14 but corridor 22 (detour via the left gap).
    // Y (0,14): manhattan 18, corridor 18 (aligned with the gap). Manhattan
    // picks X; true-cost scoring flips to Y.
    placeEnemy(world, 6, 12)
    const y = placeEnemy(world, 0, 14)
    refresh(input, world)
    const t = input.selectTarget(input.playerCell())
    expect(t!.col).toBe(cellOf(input, y).col)
    expect(t!.row).toBe(cellOf(input, y).row)
  })

  it('ON: a terrain revision bump (gate opened) invalidates the memo and flips the pick', () => {
    const { world, input } = setupWorld({ ...ISOLATED, pathTargetMode: 1 })
    buildWall(world)
    const a = placeEnemy(world, 14, 6)
    const b = placeEnemy(world, 2, 2)
    refresh(input, world)
    let t = input.selectTarget(input.playerCell())
    expect(t!.col).toBe(cellOf(input, b).col) // dig-penalized A loses

    // Open a corridor gate — A is now path-nearest (24 < 28) and the memo
    // must be invalidated via tileMap.revision, not served stale.
    openGate(world)
    refresh(input, world)
    t = input.selectTarget(input.playerCell())
    expect(t!.col).toBe(cellOf(input, a).col)
    expect(t!.row).toBe(cellOf(input, a).row)
  })

  it('ON: repeated identical queries hit the memo (stable result, no churn)', () => {
    const { world, input } = setupWorld({ ...ISOLATED, pathTargetMode: 1 })
    buildWall(world)
    placeEnemy(world, 14, 6)
    placeEnemy(world, 2, 2)
    refresh(input, world)
    const t1 = input.selectTarget(input.playerCell())
    input._selTargetValid = false // force the uncached path again
    const t2 = input.selectTarget(input.playerCell())
    expect(t1!.col).toBe(t2!.col)
    expect(t1!.row).toBe(t2!.row)
    const bCell = input.tankCell(world.tanks[world.tanks.length - 1])
    expect(t2!.col).toBe(bCell.col)
    // Memo stayed within the two enemy slots — no eviction churn.
    let used = 0
    for (let s = 0; s < 8; s++) if (input._pathCostEId[s] >= 0) used++
    expect(used).toBeLessThanOrEqual(2)
  })

  it('reset() clears the path-cost memo', () => {
    const { world, input } = setupWorld({ ...ISOLATED, pathTargetMode: 1 })
    buildWall(world)
    placeEnemy(world, 2, 2)
    refresh(input, world)
    input.selectTarget(input.playerCell())
    expect(input._pathCostNext).toBeGreaterThan(0)
    input.reset()
    for (let s = 0; s < 8; s++) expect(input._pathCostEId[s]).toBe(-1)
    expect(input._pathCostNext).toBe(0)
  })

  it('defaults: shipped 0, classic restore 0, guard profile 0', () => {
    expect(DEFAULT_GOD_AI_PARAMS.pathTargetMode).toBe(0)
    expect(CLASSIC_MODEL_PARAMS.pathTargetMode).toBe(0)
    expect(GUARD_GOD_AI_PARAMS.pathTargetMode).toBe(0)
  })
})
