import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { evalBaseLaneSeal } from '../src/ai/god/candidates/BaseLaneSeal'
import type { DecisionContext } from '../src/ai/god/DecisionCore'
import { BASE_POS, CELL } from '../src/constants'
import { clearArena, seedWorld } from './helpers'

/**
 * A9 mode 2 (2026-10-27): PRE-SEAL an already-open base column.
 *
 * Autopsy driving this test (Battlement / Riverbed base-loss forensics, hard
 * 60 seeds): every failure is `base_destroyed`; the lethal shell is fired from
 * row 2-5 straight down a base column the enemies dug open — 19-22 cells of
 * flight — and no defense candidate reacts because they all leash on the
 * distance to the SHOOTER (`baseLaneSentry` 6 cells, `baseLaneSeal` 14).
 * Measured: `enemyCanShootBase` is true on 9.9% of ticks while the seal claims
 * 1.5% and the sentry 0.0-0.2%.
 *
 * mode 2 leashes on the distance to the SEAL CELL instead — the player's job is
 * to reach the lane, not the sniper. These tests pin that difference.
 */

function scenario(params: Partial<typeof DEFAULT_GOD_AI_PARAMS> = {}): {
  world: World
  input: GodAIInput
  ctx: DecisionContext
} {
  const world = seedWorld(7)
  world.startGame('hard', 'modern', 0)
  clearArena(world) // empty arena → the base columns are clear to the top edge
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS, ...params })
  world.enemiesRemaining = 20
  // Player sits two rows above the eagle, one column off the sniper's lane.
  world.player!.x = (BASE_POS.col - 1) * CELL
  world.player!.y = (BASE_POS.row - 4) * CELL
  input.hasBase = world.tileMap.hasBase()
  input.reset()
  const p = world.player!
  const ctx: DecisionContext = {
    w: world,
    p,
    pcx: p.x + p.w / 2,
    pcy: p.y + p.h / 2,
    onCooldown: false,
    aimDir: null,
    threat: null,
    shielded: false,
  }
  return { world, input, ctx }
}

function placeSniper(world: World, col: number, row: number): void {
  const e = world.createTank('basic', col * CELL, row * CELL, 'down')
  e.alive = true
  e.spawnTimer = 0
  world.tanks.push(e)
}

describe('A9 mode 2 — pre-seal leashes on the lane cell, not the shooter', () => {
  it('mode 1 refuses a sniper 15+ cells away (the shipped 14-cell shooter leash)', () => {
    const { world, input, ctx } = scenario({ baseLaneSealMode: 1 })
    placeSniper(world, BASE_POS.col + 1, 4)
    expect(evalBaseLaneSeal(input, ctx)).toBe(false)
  })

  it('mode 2 claims the same sniper because the seal cell is close', () => {
    const { world, input, ctx } = scenario({ baseLaneSealMode: 2 })
    placeSniper(world, BASE_POS.col + 1, 4)
    expect(evalBaseLaneSeal(input, ctx)).toBe(true)
    expect(input._lastBranch).toBe('baseLaneSeal')
    expect(input._moveDir).not.toBeNull()
  })

  it('mode 2 still refuses when the seal cell itself is far away', () => {
    const { world, input, ctx } = scenario({
      baseLaneSealMode: 2,
      baseLaneSealCellRange: 2,
    })
    placeSniper(world, BASE_POS.col + 1, 4)
    expect(evalBaseLaneSeal(input, ctx)).toBe(false)
  })

  it('mode 2 is inert on stages whose base columns contain steel (user gate)', () => {
    const { world, input, ctx } = scenario({ baseLaneSealMode: 2 })
    // One steel in the LEFT base column is enough to disqualify the stage.
    world.tileMap.grid[6][BASE_POS.col] = 'steel'
    placeSniper(world, BASE_POS.col + 1, 4)
    // Falls back to the shipped mode-1 body, which refuses a 15+ cell sniper.
    expect(evalBaseLaneSeal(input, ctx)).toBe(false)
  })

  it('mode 2 leaves a broken (brick-blocked) column alone — no sniper lane', () => {
    const { world, input, ctx } = scenario({ baseLaneSealMode: 2 })
    placeSniper(world, BASE_POS.col + 1, 4)
    // Rebuild the lane with one brick: no clear line to the eagle.
    world.tileMap.grid[12][BASE_POS.col + 1] = 'brick'
    expect(evalBaseLaneSeal(input, ctx)).toBe(false)
  })
})
