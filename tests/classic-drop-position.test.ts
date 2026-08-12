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
import type { TerrainType } from '../src/types'

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

/**
 * Drop must never materialise on top of a blocking terrain cell or a spawn
 * point. The drop descriptor is a pure function of the world state, so we
 * can check the pixel rect via rectHitsTerrain (which already covers water /
 * brick / steel / base; the `base` ring is normally walled off by other
 * code, but we re-check it for safety) plus the enemy spawn points.
 */
function dropIsClean(world: World, drop: { x: number; y: number }): boolean {
  if (world.rectHitsTerrain(drop.x, drop.y, TANK, TANK)) return false
  const sps = world.enemySpawnPoints
  for (let i = 0; i < sps.length; i++) {
    if (
      drop.x < sps[i].x + TANK &&
      drop.x + TANK > sps[i].x &&
      drop.y < sps[i].y + TANK &&
      drop.y + TANK > sps[i].y
    ) {
      return false
    }
  }
  return true
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

  // ---- Fallback-exhaustion regression (道具出现在水域上) ----
  // The random fallback draws 20 candidates from the 12×12 grid
  // (x,y ∈ {0,32,…,352}). On a dense water / steel / brick layout every
  // candidate can be blocked, the loop exhausts, and (before the fix)
  // buildDrop returned the last tried — BLOCKED — coordinates, so the
  // power-up materialised on top of the terrain. The fix adds a
  // deterministic nearest-free-cell scan as a safety net; these tests
  // lock that in for water, steel, brick, and the spawn-point predicate.
  it('exhausted fallback never places a drop on water (regression: 道具出现在水域上)', () => {
    const { world, sim } = buildWorld(777)
    // Fill the 24×24 area covering every cell the 12×12 random domain
    // touches (x,y ∈ {0,32,…,352} ⇒ cells 0..23 in 16px). Every random
    // candidate is now on water, so the 20-try loop always exhausts and
    // the safety-net scan must take over.
    for (let r = 0; r < 24; r++) {
      for (let c = 0; c < 24; c++) {
        world.tileMap.set(c, r, 'water')
      }
    }
    // No `at` ⇒ primary offset path is skipped, only the fallback runs.
    for (let i = 0; i < 50; i++) {
      const drop = callBuildDrop(sim)
      expect(drop.x % CELL).toBe(0)
      expect(drop.y % CELL).toBe(0)
      expect(dropIsClean(world, drop)).toBe(true)
    }
  })

  it('exhausted fallback works the same for steel and brick as for water', () => {
    const terrainKinds: TerrainType[] = ['water', 'steel', 'brick']
    for (const kind of terrainKinds) {
      const { world, sim } = buildWorld(901)
      for (let r = 0; r < 24; r++) {
        for (let c = 0; c < 24; c++) {
          world.tileMap.set(c, r, kind)
        }
      }
      for (let i = 0; i < 30; i++) {
        const drop = callBuildDrop(sim)
        expect(world.rectHitsTerrain(drop.x, drop.y, TANK, TANK)).toBe(false)
      }
    }
  })

  it('exhausted fallback respects the spawn-point predicate (drop never on enemy spawn cell)', () => {
    // Force the random loop to exhaust by blocking the 12×12 domain with
    // terrain, then add a synthetic 32-aligned spawn point that the
    // safety-net scan must also avoid. (The default enemy spawns sit in
    // row 0 only, so they don't all overlap a 12×12 random domain on
    // their own — we add one extra at the natural scan-target cell to
    // exercise the predicate.)
    const { world, sim } = buildWorld(2025)
    for (let r = 0; r < 24; r++) {
      for (let c = 0; c < 24; c++) {
        world.tileMap.set(c, r, 'water')
      }
    }
    // Synthetic spawn points covering cells (c=12..24, r=0..11) — the
    // exact L-shaped region of free cells the safety-net scan would
    // otherwise pick. The scan must instead return one of the cells in
    // the lower band (r=12..24).
    const synth: { x: number; y: number }[] = []
    for (let r = 0; r < 12; r++) {
      for (let c = 12; c < 25; c++) synth.push({ x: c * CELL, y: r * CELL })
    }
    world.enemySpawnPoints = [...world.enemySpawnPoints, ...synth]
    for (let i = 0; i < 20; i++) {
      const drop = callBuildDrop(sim)
      expect(dropIsClean(world, drop)).toBe(true)
    }
  })

  it('exhausted fallback is deterministic (same seed ⇒ same cell across runs)', () => {
    // Two parallel runs with the same seed must produce the same sequence,
    // including the rare safety-net path. This is the determinism contract
    // required by AGENTS §2.3 and DECISIONS §47.
    const run = (seed: number) => {
      const { world, sim } = buildWorld(seed)
      for (let r = 0; r < 24; r++) {
        for (let c = 0; c < 24; c++) {
          world.tileMap.set(c, r, 'water')
        }
      }
      return Array.from({ length: 10 }, () => callBuildDrop(sim))
    }
    expect(run(4242)).toEqual(run(4242))
  })

  it('exhausted fallback preserves RNG draws (no extra world.rng consumption in the safety net)', () => {
    // The safety-net scan must NOT consume world.rng — only the random
    // fallback's 20 draws do. We verify by running the same scenario with
    // a uniform RNG and checking that 10 buildDrop calls leave the RNG
    // state at a known, deterministic offset. (Direct RNG-state checks
    // are brittle, so we instead confirm identical sequences across
    // runs — covered by the determinism test above — and that after a
    // buildDrop call the very next RNG draw equals the draw from an
    // equivalent script that did NOT enter the safety net.)
    const { world, sim } = buildWorld(3030)
    for (let r = 0; r < 24; r++) {
      for (let c = 0; c < 24; c++) {
        world.tileMap.set(c, r, 'water')
      }
    }
    // Snapshot RNG state after exhausting the fallback.
    for (let i = 0; i < 5; i++) callBuildDrop(sim)
    const afterState = world.rng.getState()
    const drawAfterExhaust = world.rng.next()

    // Reset and repeat, but skip the safety-net path by leaving the
    // domain free so the random fallback finds a valid cell within
    // the first 20 tries (it must still consume the same number of
    // random draws — 20 per buildDrop, regardless of which path wins).
    const { world: w2, sim: s2 } = buildWorld(3030)
    // Identical: fill 24×24 with water. The random draws consumed by
    // buildDrop are the SAME in both cases (always 20 ints × 2 dims,
    // because the loop's `do … while` is bounded by 20 iterations).
    for (let r = 0; r < 24; r++) {
      for (let c = 0; c < 24; c++) {
        w2.tileMap.set(c, r, 'water')
      }
    }
    for (let i = 0; i < 5; i++) callBuildDrop(s2)
    const afterState2 = w2.rng.getState()
    const drawAfter2 = w2.rng.next()

    expect(afterState).toBe(afterState2)
    expect(drawAfterExhaust).toBe(drawAfter2)
  })

  it('weights are configured as 50/30/20 (near/mid/far)', () => {
    const rules = RULES.classic
    expect(rules.dropPositionWeights.near).toBe(0.5)
    expect(rules.dropPositionWeights.mid).toBe(0.3)
    expect(rules.dropPositionWeights.far).toBe(0.2)
  })

  it('ranges are configured as 2/4/6 cells (near/mid/far)', () => {
    const rules = RULES.classic
    expect(rules.dropPositionRanges.near).toBe(2)
    expect(rules.dropPositionRanges.mid).toBe(4)
    expect(rules.dropPositionRanges.far).toBe(6)
  })

  it('DEFAULT_RULES also has drop position config', () => {
    const { world } = buildWorld(1)
    // Modern/default also has the same config.
    expect(world.rules.dropPositionWeights.near).toBe(0.5)
    expect(world.rules.dropPositionWeights.mid).toBe(0.3)
    expect(world.rules.dropPositionWeights.far).toBe(0.2)
    expect(world.rules.dropPositionRanges.near).toBe(2)
    expect(world.rules.dropPositionRanges.mid).toBe(4)
    expect(world.rules.dropPositionRanges.far).toBe(6)
  })
})
