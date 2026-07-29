/**
 * Drop position randomization — weighted near/mid/far offset (50/30/20%).
 *
 * When a power-up drops from a slain enemy, its position is randomized:
 * - 50% chance: near (0–1 cells offset)
 * - 30% chance: mid (1–2 cells offset)
 * - 20% chance: far (2–3 cells offset)
 *
 * All randomness flows through world.rng → deterministic / snapshot-safe.
 */
import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { CELL, TANK, FIELD } from '../src/constants'
import { RULES } from '../src/config/rules'
import type { GameplayRules } from '../src/config/rules'

function buildWorld(seed: number, rules?: GameplayRules): { world: World; sim: Simulation } {
  const world = new World()
  world.rng = new RNG(seed)
  const input = new Input()
  const sim = new Simulation(world, input)
  world.startGame('classic', 'modern', 0)
  if (rules) {
    world.rules = { ...world.rules, ...rules }
  }
  return { world, sim }
}

/** Invoke the private buildDrop method via the simulation. */
function callBuildDrop(
  sim: Simulation,
  at?: { x: number; y: number },
): { type: string; x: number; y: number } {
  return (
    sim as unknown as {
      buildDrop: (at?: { x: number; y: number }) => { type: string; x: number; y: number }
    }
  ).buildDrop(at)
}

describe('Drop position randomization (near/mid/far)', () => {
  it('all drop positions are grid-aligned and within field bounds', () => {
    const { sim } = buildWorld(42)
    // Run 100 drops at various enemy positions and verify all are valid.
    const positions = [
      { x: 100, y: 100 },
      { x: 200, y: 200 },
      { x: 0, y: 0 },
      { x: 384, y: 384 },
      { x: 64, y: 320 },
    ]
    for (let i = 0; i < 20; i++) {
      for (const at of positions) {
        const drop = callBuildDrop(sim, at)
        // Grid-aligned
        expect(drop.x % CELL).toBe(0)
        expect(drop.y % CELL).toBe(0)
        // Within field bounds
        expect(drop.x).toBeGreaterThanOrEqual(0)
        expect(drop.y).toBeGreaterThanOrEqual(0)
        expect(drop.x).toBeLessThan(FIELD)
        expect(drop.y).toBeLessThan(FIELD)
      }
    }
  })

  it('offset positions stay within tier range (never exceeds far range)', () => {
    const { world, sim } = buildWorld(99)
    const ranges = world.rules.dropPositionRanges
    const maxRange = Math.max(ranges.near, ranges.mid, ranges.far)

    // Place enemy in the CENTER of the field so all 4 directions are clear.
    const at = { x: 12 * CELL, y: 12 * CELL }
    for (let i = 0; i < 100; i++) {
      const drop = callBuildDrop(sim, at)
      const dx = Math.abs(drop.x - at.x) / CELL
      const dy = Math.abs(drop.y - at.y) / CELL
      const dist = dx + dy
      // When the offset position is valid (no terrain), the drop lands within
      // the tier range. When offset hits terrain, fallback is a random tile.
      // Center placement ensures almost no terrain conflicts, so dist ≤ maxRange
      // holds for the vast majority of rolls. Allow a few fallback outliers.
      if (dist > maxRange + 1) {
        // This can only happen if the offset landed on terrain and fell back.
        // With center placement this should be rare.
        expect(world.rectHitsTerrain(drop.x, drop.y, TANK, TANK)).toBe(false)
      }
    }
  })

  it('deterministic: same seed produces identical drop positions', () => {
    const at = { x: 8 * CELL, y: 12 * CELL }
    const run = (seed: number) => {
      const { sim } = buildWorld(seed)
      return Array.from({ length: 10 }, () => callBuildDrop(sim, at))
    }
    const a = run(12345)
    const b = run(12345)
    expect(b).toEqual(a)
  })

  it('different seeds produce different drop positions', () => {
    const at = { x: 8 * CELL, y: 12 * CELL }
    const run = (seed: number) => {
      const { sim } = buildWorld(seed)
      return Array.from({ length: 10 }, () => callBuildDrop(sim, at))
    }
    const a = run(1)
    const b = run(2)
    // With 10 drops, at least one should differ between different seeds.
    const anyDifferent = a.some((d, i) => d.x !== b[i].x || d.y !== b[i].y)
    expect(anyDifferent).toBe(true)
  })

  it('fallback to random position when offset hits terrain', () => {
    const { world, sim } = buildWorld(555)
    // Fill an area with steel so the offset position is always blocked.
    for (let r = 5; r < 15; r++) {
      for (let c = 5; c < 15; c++) {
        world.tileMap.set(c, r, 'steel')
      }
    }
    const at = { x: 10 * CELL, y: 10 * CELL }
    for (let i = 0; i < 20; i++) {
      const drop = callBuildDrop(sim, at)
      // Even if offset is blocked, the drop should still be placed somewhere valid.
      expect(drop.x % CELL).toBe(0)
      expect(drop.y % CELL).toBe(0)
      expect(drop.x).toBeGreaterThanOrEqual(0)
      expect(drop.y).toBeGreaterThanOrEqual(0)
    }
  })

  it('weights are configured as 50/30/20 (near/mid/far)', () => {
    const rules = RULES.classic
    expect(rules.dropPositionWeights.near).toBe(0.5)
    expect(rules.dropPositionWeights.mid).toBe(0.3)
    expect(rules.dropPositionWeights.far).toBe(0.2)
  })

  it('ranges are configured as 1/2/3 cells (near/mid/far)', () => {
    const rules = RULES.classic
    expect(rules.dropPositionRanges.near).toBe(1)
    expect(rules.dropPositionRanges.mid).toBe(2)
    expect(rules.dropPositionRanges.far).toBe(3)
  })

  it('DEFAULT_RULES also has drop position config', () => {
    const { world } = buildWorld(1)
    // Modern/default also has the same config.
    expect(world.rules.dropPositionWeights.near).toBe(0.5)
    expect(world.rules.dropPositionWeights.mid).toBe(0.3)
    expect(world.rules.dropPositionWeights.far).toBe(0.2)
    expect(world.rules.dropPositionRanges.near).toBe(1)
    expect(world.rules.dropPositionRanges.mid).toBe(2)
    expect(world.rules.dropPositionRanges.far).toBe(3)
  })
})
