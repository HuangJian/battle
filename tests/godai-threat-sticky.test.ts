import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { CLASSIC_MODEL_PARAMS, GUARD_GOD_AI_PARAMS } from '../src/ai/god/params'
import { CELL } from '../src/constants'
import type { Tank } from '../src/types'
import { placeEnemy, setupGodGame } from './helpers'

/**
 * §169: base-threat signal stickiness — unit tests.
 *
 * Root cause (defeat decision-chain probe, 363 base_destroyed losses,
 * tmp/probe-base-response.ts): isBaseUnderThreat() FLICKERS — in the 10s
 * before the base's first hit the signal is true only 69.6% of ticks,
 * flipping ~9.8× per 10s. Each false gap drops selectTarget into the
 * nearest-enemy hunt branch, pulling the player off the base approach.
 *
 * Fix: once the signal goes true it stays true for threatStickyTicks even
 * if the underlying detection clears (hold refreshes while true; only
 * extends, never shortens). 0 = OFF = byte-identical.
 *
 * Tests use chokepointMode=0 + baseClearShotThreat=0 to isolate the sticky
 * hold from the §88/§157 detections — the flicker scenario is built by
 * moving a single enemy in/out of the static threat box.
 */

// setupWorld (§1.1 canary) → shared setupGodGame from ./helpers; the local
// copy was byte-identical to base-clear-shot-threat.test.ts's.

function moveEnemy(enemy: Tank, col: number, row: number): void {
  enemy.x = col * CELL
  enemy.y = row * CELL
}

function refresh(input: GodAIInput, world: World): void {
  input._baseUnderThreatCache = null
  input._enemies.length = 0
  for (const t of world.tanks) {
    if (t.alive && t.spawnTimer <= 0) input._enemies.push(t)
  }
}

const ISOLATED = { chokepointMode: 0, baseClearShotThreat: 0 }

describe('§169: base-threat signal stickiness', () => {
  it('OFF (default 0): the signal clears the tick the enemy leaves the box', () => {
    const { world, input } = setupGodGame({ params: ISOLATED })
    const enemy = placeEnemy(world, 12, 20) // inside static box (±3 col, row>=18)
    refresh(input, world)
    expect(input.isBaseUnderThreat()).toBe(true)
    moveEnemy(enemy, 24, 0) // far away — no box, no race, no clear-shot
    refresh(input, world)
    expect(input.isBaseUnderThreat()).toBe(false)
    expect(input._threatStickyHold).toBe(0)
  })

  it('ON: the signal persists through the gap after the enemy leaves', () => {
    const { world, input } = setupGodGame({ params: { ...ISOLATED, threatStickyTicks: 10 } })
    const enemy = placeEnemy(world, 12, 20)
    refresh(input, world)
    expect(input.isBaseUnderThreat()).toBe(true)
    expect(input._threatStickyHold).toBe(10)

    moveEnemy(enemy, 24, 0)
    refresh(input, world)
    // Underlying detection is now false, but the hold keeps it true.
    expect(input.isBaseUnderThreat()).toBe(true)

    // A few ticks inside the hold window — still true.
    for (let i = 0; i < 4; i++) {
      input.endFrame()
      refresh(input, world)
      expect(input.isBaseUnderThreat()).toBe(true)
    }
    // Run the hold out — the signal clears.
    for (let i = 0; i < 10; i++) input.endFrame()
    refresh(input, world)
    expect(input.isBaseUnderThreat()).toBe(false)
  })

  it('ON: re-entering the box refreshes the hold (only extends, never shortens)', () => {
    const { world, input } = setupGodGame({ params: { ...ISOLATED, threatStickyTicks: 10 } })
    const enemy = placeEnemy(world, 12, 20)
    refresh(input, world)
    expect(input.isBaseUnderThreat()).toBe(true)

    moveEnemy(enemy, 24, 0)
    refresh(input, world)
    for (let i = 0; i < 6; i++) input.endFrame() // hold drained to ~4
    expect(input._threatStickyHold).toBeLessThan(10)

    moveEnemy(enemy, 12, 20) // back in the box
    refresh(input, world)
    expect(input.isBaseUnderThreat()).toBe(true)
    expect(input._threatStickyHold).toBe(10) // refreshed
  })

  it('reset() clears the sticky hold', () => {
    const { world, input } = setupGodGame({ params: { ...ISOLATED, threatStickyTicks: 10 } })
    placeEnemy(world, 12, 20)
    refresh(input, world)
    expect(input.isBaseUnderThreat()).toBe(true)
    expect(input._threatStickyHold).toBe(10)
    input.reset()
    expect(input._threatStickyHold).toBe(0)
  })

  it('defaults: shipped 0, classic restore 0, guard profile 0', () => {
    expect(DEFAULT_GOD_AI_PARAMS.threatStickyTicks).toBe(0)
    expect(CLASSIC_MODEL_PARAMS.threatStickyTicks).toBe(0)
    expect(GUARD_GOD_AI_PARAMS.threatStickyTicks).toBe(0)
  })
})
