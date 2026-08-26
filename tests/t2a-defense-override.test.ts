import { seedWorld } from './helpers'
import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { RNG } from '../src/utils/RNG'
import { CELL, GRID, BASE_POS } from '../src/constants'
import type { Tank } from '../src/types'

/**
 * §159: T2a defense override — unit tests.
 *
 * Root cause (hard S20 Bastion seed 383912762, 0:39~0:42): the player at
 * cell (17,2) was 1 cell past `maxPlayerDistFromBase` (dist 27 > 26) while
 * an armor enemy sat 2 cells to the left with a clear bullet lane.
 * `skipT2aForDefense` blocked ENGAGE, the player fell through to HUNT, and
 * the navigation target alternated between the base defense position and
 * the enemy — a sub-cell up/down oscillation that burned 160+ ticks.
 *
 * Fix: when `t2aDefenseOverrideRange > 0`, the `skipT2aForDefense` gate is
 * bypassed if a scan in the `aimDir` finds an enemy within that many cells.
 * The player can then stop-and-aim to kill the close enemy, which directly
 * helps defense (one fewer enemy threatening the base).
 */

function setupWorld(params: Partial<typeof DEFAULT_GOD_AI_PARAMS> = {}): {
  world: World
  input: GodAIInput
} {
  const world = seedWorld(42)
  // Give the AI a FRESH rng so startGame/reset don't consume its stream.
  const aiRng = new RNG(999)
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS, ...params }, aiRng)
  const sim = new Simulation(world, new Input())
  world.startGame('hard', 'modern', 0)
  // Clear the grid to empty.
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) world.tileMap.grid[r][c] = 'empty'
  }
  // Place the base at the standard position.
  for (const r of [24, 25]) {
    for (const c of [12, 13]) world.tileMap.grid[r][c] = 'base'
  }
  void sim
  input.hasBase = world.tileMap.hasBase()
  input.reset()
  return { world, input }
}

function placeEnemy(
  world: World,
  col: number,
  row: number,
  dir: Tank['dir'] = 'down',
  kind: Tank['kind'] = 'basic',
): Tank {
  const enemy = world.createTank(kind, col * CELL, row * CELL, dir)
  enemy.alive = true
  enemy.spawnTimer = 0
  enemy.hp = enemy.maxHp
  world.tanks.push(enemy)
  return enemy
}

function positionPlayer(world: World, col: number, row: number): void {
  const p = world.player!
  p.x = col * CELL
  p.y = row * CELL
  p.hp = 100
  p.spawnTimer = 0
  p.shieldTimer = 0
  p.level = 0
  world.playerLevel = 0
  // Ensure the player is NOT on fire cooldown (frame 0 + lastFire 0 would
  // trigger the time-based cooldown check in thinkImpl).
  p.lastFire = -10000
}

function refreshEnemies(input: GodAIInput, world: World): void {
  input._baseUnderThreatCache = null
  input._enemies.length = 0
  for (const t of world.tanks) {
    if (t.alive && t.spawnTimer <= 0) input._enemies.push(t)
  }
}

describe('§159: T2a defense override for close enemies', () => {
  it('without fix (range=0): player does NOT fire when past maxPlayerDistFromBase', () => {
    // Player at (17,2): distToBase = |17-12| + |2-24| = 5+22 = 27 > 26.
    // Enemy at (14,2): 3 cells to the left, same row → scan finds it.
    // A second enemy near the base triggers isBaseUnderThreat.
    const { world, input } = setupWorld({
      t2aDefenseOverrideRange: 0, // OFF — byte-identical to pre-§159
      maxPlayerDistFromBase: 26,
      baseRaceRangeCells: 18,
      baseRaceMarginCells: 2,
      baseClearShotThreat: 1,
    })
    positionPlayer(world, 17, 2)
    placeEnemy(world, 14, 2, 'left', 'armor') // close enemy to the left
    placeEnemy(world, 12, 20, 'up', 'basic') // triggers isBaseUnderThreat (static box)
    refreshEnemies(input, world)

    // Confirm the preconditions.
    expect(input.isBaseUnderThreat()).toBe(true)
    const pc = input.playerCell()
    const dist = Math.abs(pc.col - BASE_POS.col) + Math.abs(pc.row - BASE_POS.row)
    expect(dist).toBe(27) // 1 cell past the threshold of 26

    // The player should NOT fire — skipT2aForDefense blocks ENGAGE.
    input.getMoveDirection() // triggers think()
    expect(input._fire).toBe(false)
    expect(input._lastBranch).not.toBe('t2a')
  })

  it('with fix (range=4): player fires at close enemy despite being past threshold', () => {
    // Same setup as above, but with the fix active.
    const { world, input } = setupWorld({
      t2aDefenseOverrideRange: 4, // ON — allow ENGAGE for close enemies
      maxPlayerDistFromBase: 26,
      baseRaceRangeCells: 18,
      baseRaceMarginCells: 2,
      baseClearShotThreat: 1,
    })
    positionPlayer(world, 17, 2)
    placeEnemy(world, 14, 2, 'left', 'armor') // 3 cells left, same row
    placeEnemy(world, 12, 20, 'up', 'basic') // triggers isBaseUnderThreat
    refreshEnemies(input, world)

    // Confirm the preconditions.
    expect(input.isBaseUnderThreat()).toBe(true)
    const pc2 = input.playerCell()
    const dist2 = Math.abs(pc2.col - BASE_POS.col) + Math.abs(pc2.row - BASE_POS.row)
    expect(dist2).toBe(27) // still past the threshold

    // The player SHOULD fire — the override bypasses skipT2aForDefense.
    input.getMoveDirection() // triggers think()
    expect(input._fire).toBe(true)
    expect(input._lastBranch).toBe('t2a')
    // The player should aim left (toward the enemy).
    expect(
      input._moveDir === 'left' || (world.player!.dir === 'left' && input._moveDir === null),
    ).toBe(true)
  })

  it('fix does NOT trigger when enemy is beyond override range', () => {
    // Enemy at (5,2): 12 cells to the left — beyond the 4-cell override.
    const { world, input } = setupWorld({
      t2aDefenseOverrideRange: 4,
      maxPlayerDistFromBase: 26,
      baseRaceRangeCells: 18,
      baseRaceMarginCells: 2,
      baseClearShotThreat: 1,
    })
    positionPlayer(world, 17, 2)
    placeEnemy(world, 5, 2, 'left', 'basic') // 12 cells left — too far
    placeEnemy(world, 12, 20, 'up', 'basic') // triggers isBaseUnderThreat
    refreshEnemies(input, world)

    expect(input.isBaseUnderThreat()).toBe(true)

    // The player should NOT fire — enemy is beyond the override range.
    input.getMoveDirection()
    expect(input._lastBranch).not.toBe('t2a')
  })

  it('fix does NOT trigger when base is NOT under threat', () => {
    // No enemy near the base → isBaseUnderThreat = false → skipT2a doesn't
    // even fire. The override is irrelevant. This test confirms the override
    // doesn't interfere with normal T2a behavior.
    const { world, input } = setupWorld({
      t2aDefenseOverrideRange: 4,
      maxPlayerDistFromBase: 26,
      baseRaceRangeCells: 18,
      baseRaceMarginCells: 2,
      baseClearShotThreat: 1,
    })
    positionPlayer(world, 17, 2)
    placeEnemy(world, 14, 2, 'left', 'armor') // close enemy
    // No enemy near the base.
    refreshEnemies(input, world)

    expect(input.isBaseUnderThreat()).toBe(false)

    // Normal T2a — the player should fire (no skipT2aForDefense).
    input.getMoveDirection()
    expect(input._lastBranch).toBe('t2a')
    expect(input._fire).toBe(true)
  })

  it('end-to-end (constructed): override increases fire count when stuck far + base threatened', () => {
    // Full-Simulation integration check for the override mechanism. Builds the
    // exact root-cause scenario the fix targets — player 1 cell past
    // maxPlayerDistFromBase (dist 27 > 26) with the base under threat and a
    // close armor enemy in the aimDir — and confirms range=4 makes the player
    // fire at the close enemy where range=0 (skipT2aForDefense) blocks it.
    //
    // NOTE: the original seed-based version (S20 Bastion 383912762, 2400-tick)
    // is no longer a valid regression target after the §159–§164 defense
    // improvements were merged in: those changes keep the player near the base
    // whenever it is threatened, so in that seed the player is never
    // simultaneously far-from-base AND under-threat — the precise combined
    // condition the override gates on (measured ovCondMet === 0). The override
    // mechanism itself is unaffected (the unit tests above still pass), so we
    // exercise it deterministically here with a short window where the
    // condition actually holds.
    function run(overrideRange: number): number {
      const { world, input } = setupWorld({
        t2aDefenseOverrideRange: overrideRange,
        maxPlayerDistFromBase: 26,
        baseRaceRangeCells: 18,
        baseRaceMarginCells: 2,
        baseClearShotThreat: 1,
      })
      positionPlayer(world, 17, 2)
      placeEnemy(world, 14, 2, 'left', 'armor') // close enemy to the left, in aimDir
      placeEnemy(world, 12, 20, 'up', 'basic') // triggers isBaseUnderThreat
      refreshEnemies(input, world)
      const sim = new Simulation(world, input)
      const start = world.player!.fireCount
      // Short window: while the stuck-far + base-threat condition holds, the
      // override lets the player fire at the close enemy; range=0 blocks it.
      for (let tick = 0; tick < 8; tick++) {
        sim.tick()
        input.endFrame()
        world.consumeEvents()
      }
      return (world.player?.fireCount ?? 0) - start
    }

    const before = run(0)
    const after = run(4)
    expect(after).toBeGreaterThan(before)
  })
})
