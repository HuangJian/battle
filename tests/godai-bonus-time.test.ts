import { seedWorld } from './helpers'
import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { RNG } from '../src/utils/RNG'
import { RULES } from '../src/config/rules'
import { DIFFICULTIES } from '../src/config/difficulty'
import { THEMES } from '../src/config/theme'
import { GRID, TANK, POWERUP_PICKUP_WINDOW_MS } from '../src/constants'
import type { PowerUp, StageData } from '../src/types'

// ================================================================
// 「督战模式」BONUS TIME — the God AI must collect the remaining
// power-ups during the post-clear pickup window.
//
// Root cause (DECISIONS §84): findPowerUpTarget applied the NORMAL-play
// divert-distance cap (powerupMaxDivertDistance = 16 cells; 8 for
// bomb/star) even during the pickup window. A power-up dropped farther
// than that was never targeted — the AI fell through to selectTarget →
// no enemies → defense position and stood there while the 10s window
// expired, so the God player (督战 spectate) never collected the bonus.
// ================================================================

// ---------------------------------------------------------------- helpers

/** Empty 26×26 arena with the classic base (eagle) at rows 24-25, cols 12-13. */
function makeEmptyStage(): StageData {
  const tiles: string[] = []
  for (let r = 0; r < GRID; r++) {
    let row = ''
    for (let c = 0; c < GRID; c++) row += '.'
    if (r === 24 || r === 25) row = row.slice(0, 12) + 'EE' + row.slice(14)
    tiles.push(row)
  }
  return { id: 9999, name: 'Empty Arena', tiles, enemies: ['basic'] }
}

/**
 * Empty arena plus a 4×4 steel ring (cells 1-4 × 1-4) enclosing cells
 * (2,2)-(3,3). A power-up placed at (32,32) sits inside the box — the tank
 * can never reach it (steel blocks movement at any level).
 */
function makeBoxedArena(): StageData {
  const grid = makeEmptyStage().tiles.map((row) => row.split(''))
  for (let c = 1; c <= 4; c++) {
    grid[1][c] = 's'
    grid[4][c] = 's'
  }
  for (let r = 1; r <= 4; r++) {
    grid[r][1] = 's'
    grid[r][4] = 's'
  }
  return { id: 9998, name: 'Boxed Arena', tiles: grid.map((r) => r.join('')), enemies: ['basic'] }
}

function makeWorld(seed = 42): World {
  const world = seedWorld(seed)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.themeKey = 'classic'
  world.theme = THEMES['classic']
  world.rules = { ...RULES['classic'] }
  world.state = 'playing'
  world.coop = false
  world.spectate = true // 督战: God AI drives P1
  return world
}

/**
 * A cleared stage with no enemies: player parked at the bottom-left (0,384),
 * spawn queue empty, God AI constructed and reset. The caller then decides
 * whether to enter the pickup window and where the loot sits.
 */
function bonusTimeWorld(
  seed = 42,
  stage: StageData = makeEmptyStage(),
): { world: World; ai: GodAIInput } {
  const world = makeWorld(seed)
  world.loadStageData(stage, 0)
  // Stage cleared: no spawn queue, no enemies on the field.
  world.spawnQueue = []
  world.tanks = []
  world.enemiesSpawned = 0
  world.enemiesTotal = 1
  world.enemiesRemaining = 0
  // Player awake, parked at the bottom-left corner (cell 0,24).
  const p = world.player!
  p.spawnTimer = 0
  p.shieldTimer = 0
  p.x = 0
  p.y = 384
  p.dir = 'up'
  const ai = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, new RNG(seed ^ 0x1234))
  ai.reset()
  return { world, ai }
}

function makePowerUp(
  id: number,
  type: PowerUp['type'],
  x: number,
  y: number,
  lifeTimer = 0,
): PowerUp {
  return { id, type, x, y, w: TANK, h: TANK, alive: true, blinkTimer: 0, lifeTimer }
}

function playerCenter(world: World): { x: number; y: number } {
  const p = world.player!
  return { x: p.x + TANK / 2, y: p.y + TANK / 2 }
}

// ---------------------------------------------------------------- targeting

describe('godai BONUS TIME — findPowerUpTarget', () => {
  it('window active: a 24-cell-away star IS targeted (whole field is fair game)', () => {
    const { world, ai } = bonusTimeWorld()
    world.pickupWindowEntered = true
    world.pickupWindowTimer = POWERUP_PICKUP_WINDOW_MS
    // Star at the far bottom-right — 24 cells from the player, beyond both
    // the 16-cell general cap and the 8-cell bomb/star cap.
    world.addPowerUp(makePowerUp(900, 'star', 384, 384))

    const { x, y } = playerCenter(world)
    expect(ai.findPowerUpTarget(x, y)).toEqual({ col: 24, row: 24 })
  })

  it('window inactive: the same far star is NOT targeted (divert cap applies in combat)', () => {
    const { world, ai } = bonusTimeWorld()
    world.pickupWindowEntered = false
    world.pickupWindowTimer = 0
    world.addPowerUp(makePowerUp(900, 'star', 384, 384))

    const { x, y } = playerCenter(world)
    expect(ai.findPowerUpTarget(x, y)).toBeNull()
    // And think() does NOT enter the power-up branch.
    ai.getMoveDirection()
    expect(ai.branchCounts.powerup).toBe(0)
  })

  it('window active: an about-to-despawn item outranks a fresh item', () => {
    const { world, ai } = bonusTimeWorld()
    world.pickupWindowEntered = true
    world.pickupWindowTimer = POWERUP_PICKUP_WINDOW_MS
    // Fresh star, 18 cells away (despawns in 20s).
    world.addPowerUp(makePowerUp(900, 'star', 0, 96))
    // Shield with only 3s of life left, 10 cells away (passable ground).
    world.addPowerUp(makePowerUp(901, 'shield', 160, 384, 17000))

    const { x, y } = playerCenter(world)
    expect(ai.findPowerUpTarget(x, y)).toEqual({ col: 10, row: 24 })
  })

  it('window active: an unreachable steel-boxed item is skipped for a reachable one', () => {
    const { world, ai } = bonusTimeWorld(42, makeBoxedArena())
    world.pickupWindowEntered = true
    world.pickupWindowTimer = POWERUP_PICKUP_WINDOW_MS
    // Bomb inside the steel box — first in iteration order and scores higher
    // than the star (priority 0, same 24-cell distance), but unreachable.
    world.addPowerUp(makePowerUp(900, 'bomb', 32, 32))
    // Reachable star at the far bottom-right.
    world.addPowerUp(makePowerUp(901, 'star', 384, 384))

    const { x, y } = playerCenter(world)
    expect(ai.findPowerUpTarget(x, y)).toEqual({ col: 24, row: 24 })
  })

  it('window active: only unreachable items → no target (the AI is not stuck)', () => {
    const { world, ai } = bonusTimeWorld(42, makeBoxedArena())
    world.pickupWindowEntered = true
    world.pickupWindowTimer = POWERUP_PICKUP_WINDOW_MS
    world.addPowerUp(makePowerUp(900, 'bomb', 32, 32))

    const { x, y } = playerCenter(world)
    expect(ai.findPowerUpTarget(x, y)).toBeNull()
    ai.getMoveDirection()
    expect(ai.branchCounts.powerup).toBe(0)
  })

  it('window active: an item straddling the base is collected from an adjacent cell', () => {
    const { world, ai } = bonusTimeWorld()
    world.pickupWindowEntered = true
    world.pickupWindowTimer = POWERUP_PICKUP_WINDOW_MS
    // Star at (176,384): its footprint spans cols 11-12 / rows 24-25, so the
    // base (cols 12-13) blocks the item's own tank cell (11,24). The tank can
    // still overlap the item by parking on cell (10,24) — that cell is what
    // the AI must target (not the impassable item cell).
    world.addPowerUp(makePowerUp(900, 'star', 176, 384))

    const { x, y } = playerCenter(world)
    expect(ai.findPowerUpTarget(x, y)).toEqual({ col: 10, row: 24 })
  })

  it('window active: think() enters the power-up branch and moves toward the item', () => {
    const { world, ai } = bonusTimeWorld()
    world.pickupWindowEntered = true
    world.pickupWindowTimer = POWERUP_PICKUP_WINDOW_MS
    world.addPowerUp(makePowerUp(900, 'star', 384, 384))

    const dir = ai.getMoveDirection()
    expect(dir).not.toBeNull()
    expect(ai.branchCounts.powerup).toBe(1)
  })
})

// ---------------------------------------------------------------- integration

describe('godai BONUS TIME — simulation', () => {
  it('the God AI drives over and collects a 24-cell-away power-up before the window expires', () => {
    const { world, ai } = bonusTimeWorld(777)
    world.pickupWindowEntered = true
    world.pickupWindowTimer = POWERUP_PICKUP_WINDOW_MS
    world.addPowerUp(makePowerUp(900, 'star', 384, 384))

    const sim = new Simulation(world, ai)
    // 900 ticks = 15s. The pickup window is 10s (600 ticks); the star must
    // be collected before it expires, otherwise the stage auto-ends with
    // the item unclaimed (playerLevel stays 0).
    for (let i = 0; i < 900; i++) {
      sim.tick()
      ai.endFrame()
      if (world.state === 'stageclear') break
    }

    // The star was picked up: classic star pickup levels the player 0 → 1.
    expect(world.playerLevel).toBe(1)
    expect(world.powerUps.some((p) => p.alive)).toBe(false)
  })

  it('the God AI skips the steel-boxed bomb and collects the reachable star', () => {
    const { world, ai } = bonusTimeWorld(777, makeBoxedArena())
    world.pickupWindowEntered = true
    world.pickupWindowTimer = POWERUP_PICKUP_WINDOW_MS
    // Higher-scoring but unreachable (steel box).
    world.addPowerUp(makePowerUp(900, 'bomb', 32, 32))
    // Reachable star at the far bottom-right.
    world.addPowerUp(makePowerUp(901, 'star', 384, 384))

    const sim = new Simulation(world, ai)
    // 900 ticks covers the 10s window (600 ticks) + the 1s end delay.
    for (let i = 0; i < 900; i++) {
      sim.tick()
      ai.endFrame()
      if (world.state === 'stageclear') break
    }

    // The reachable star was picked up (player 0 → 1)…
    expect(world.playerLevel).toBe(1)
    // …and the boxed bomb was left on the field (unreachable, never burned
    // the window): it stays alive instead of being collected.
    expect(world.powerUps.some((p) => p.type === 'bomb' && p.alive)).toBe(true)
  })

  it('the God AI collects a base-straddling item by parking beside it', () => {
    const { world, ai } = bonusTimeWorld(999)
    world.pickupWindowEntered = true
    world.pickupWindowTimer = POWERUP_PICKUP_WINDOW_MS
    // Star overlapping the base (cols 12-13, rows 24-25) — the tank cannot
    // occupy its own cell, but can overlap it from cell (10,24), which is
    // exactly the cell findPowerUpTarget returns.
    world.addPowerUp(makePowerUp(900, 'star', 176, 384))

    const sim = new Simulation(world, ai)
    for (let i = 0; i < 900; i++) {
      sim.tick()
      ai.endFrame()
      if (world.state === 'stageclear') break
    }

    expect(world.playerLevel).toBe(1)
    expect(world.powerUps.some((p) => p.alive)).toBe(false)
  })
})
