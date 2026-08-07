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
 * §170: hunt commit (追击承诺) — unit tests.
 *
 * Root cause (tail-stage probe + S34 deep dive, DECISIONS §170): losses
 * spend 73.8% of ticks in navigate with a longer mean distance to the
 * nearest enemy — the per-tick nearest reselection re-routes the player
 * mid-approach whenever the nearest identity flips. S34 losses close to
 * within 5 cells of an enemy on only 9.0% of ticks (wins 31.8%). The
 * commit keeps the approach on one enemy for huntCommitTicks.
 *
 * Isolation: chokepointMode=0 + baseGuardAnchorMode=0 (no §88/§137 holds),
 * enemiesRemaining=20 (so canHunt stays false — the test exercises the
 * NORMAL nearest-selection branch the commit lives in), enemies parked in
 * the upper rows far from the base (no threat, no retreat triggers).
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

function placeEnemy(world: World, col: number, row: number): Tank {
  const enemy = world.createTank('basic', col * CELL, row * CELL, 'down')
  enemy.alive = true
  enemy.spawnTimer = 0
  world.tanks.push(enemy)
  return enemy
}

function moveEnemy(enemy: Tank, col: number, row: number): void {
  enemy.x = col * CELL
  enemy.y = row * CELL
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

describe('§170: hunt commit (追击承诺)', () => {
  it('OFF (default 0): the target follows the nearest enemy every pick', () => {
    const { world, input } = setupWorld(ISOLATED)
    const a = placeEnemy(world, 4, 4)
    const b = placeEnemy(world, 6, 6) // nearer to the player at (8,24)
    refresh(input, world)
    const t1 = input.selectTarget(input.playerCell())
    expect(t1).not.toBeNull()
    expect(t1!.col).toBe(cellOf(input, b).col)
    expect(t1!.row).toBe(cellOf(input, b).row)
    // B moves far — with no commit the pick flips to A immediately.
    moveEnemy(b, 20, 2)
    refresh(input, world)
    const t2 = input.selectTarget(input.playerCell())
    expect(t2!.col).toBe(cellOf(input, a).col)
    expect(t2!.row).toBe(cellOf(input, a).row)
    expect(input._huntCommitId).toBe(-1)
  })

  it('ON: the commit holds the picked enemy while the window is open', () => {
    const { world, input } = setupWorld({ ...ISOLATED, huntCommitTicks: 120 })
    const a = placeEnemy(world, 4, 4)
    const b = placeEnemy(world, 6, 6)
    refresh(input, world)
    const t1 = input.selectTarget(input.playerCell())
    expect(input._huntCommitId).toBe(b.id)
    expect(input._huntCommitUntil).toBe(world.frame + 120)
    expect(t1!.col).toBe(cellOf(input, b).col)

    // B is now FARTHER than A — the commit keeps chasing B.
    moveEnemy(b, 20, 2)
    world.frame += 10
    refresh(input, world)
    const t2 = input.selectTarget(input.playerCell())
    expect(t2!.col).toBe(cellOf(input, b).col)
    expect(t2!.row).toBe(cellOf(input, b).row)

    // Window expires — free selection resumes and picks the nearer A.
    world.frame = input._huntCommitUntil + 1
    refresh(input, world)
    const t3 = input.selectTarget(input.playerCell())
    expect(t3!.col).toBe(cellOf(input, a).col)
    expect(t3!.row).toBe(cellOf(input, a).row)
    expect(input._huntCommitId).toBe(a.id) // re-committed to the new pick
  })

  it('ON: the commit drops immediately when the committed enemy dies', () => {
    const { world, input } = setupWorld({ ...ISOLATED, huntCommitTicks: 120 })
    const a = placeEnemy(world, 4, 4)
    const b = placeEnemy(world, 6, 6)
    refresh(input, world)
    input.selectTarget(input.playerCell())
    expect(input._huntCommitId).toBe(b.id)
    b.alive = false
    refresh(input, world)
    const t = input.selectTarget(input.playerCell())
    expect(t!.col).toBe(cellOf(input, a).col)
    expect(input._huntCommitId).toBe(a.id)
  })

  it('reset() clears the commit state', () => {
    const { world, input } = setupWorld({ ...ISOLATED, huntCommitTicks: 120 })
    placeEnemy(world, 6, 6)
    refresh(input, world)
    input.selectTarget(input.playerCell())
    expect(input._huntCommitId).toBeGreaterThanOrEqual(0)
    input.reset()
    expect(input._huntCommitId).toBe(-1)
    expect(input._huntCommitUntil).toBe(0)
  })

  it('defaults: shipped 0, classic restore 0, guard profile 0', () => {
    expect(DEFAULT_GOD_AI_PARAMS.huntCommitTicks).toBe(0)
    expect(CLASSIC_MODEL_PARAMS.huntCommitTicks).toBe(0)
    expect(GUARD_GOD_AI_PARAMS.huntCommitTicks).toBe(0)
  })
})
