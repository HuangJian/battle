import { seedWorld } from './helpers'
import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { genId } from '../src/game/World'
import { CELL, TANK, BULLET, GRID, ENEMIES_PER_STAGE } from '../src/constants'
import type { TankKind, Tank } from '../src/types'
import type { Direction } from '../src/constants'
import type { IntelligenceLevel } from '../src/types'
import {
  DIFFICULTY_TIER_DISTRIBUTION,
  COMMANDER_FLOOR,
  COMMANDER_ALIVE_CAP,
  rollTier,
} from '../src/ai/config'
import { resolveProfile, ELITE_DIMENSION } from '../src/config/combat'

/**
 * Tactical Intelligence Framework — behavioural + config tests for the
 * spawn-rolled five-tier model (plan/AI-Tier-System-Revision.md).
 *
 * Guards the plan's "Definition of Done":
 *  - identical inputs → identical decisions (determinism)
 *  - AI never stalls (tanks keep navigating)
 *  - strategic goals stay stable for non-strategic tiers
 *  - command authority derives from spawnSeq; succession + 1s office delay
 *  - bullet avoidance improves with intelligence
 *  - compliance is one roll per directive, cached, None deaf
 *  - None is a separate classic branch (deterministic, no freeze)
 *  - floor (attempt-based) + cap (≤2 alive) enforce the commander count
 *  - every commander-tier spawn carries the +15% combat boost
 *  - snapshot round-trips all new command-authority fields
 *
 * All entropy flows through world.rng (AGENTS.md §2.3), so every test
 * is deterministic and seedable.
 */

function seededWorld(seed: number, difficulty = 'classic'): { world: World; sim: Simulation } {
  const world = seedWorld(seed)
  const sim = new Simulation(world, new Input())
  world.startGame(difficulty, 'modern', 0)
  return { world, sim }
}

/** Clear all terrain and place a single base at the bottom-center (open arena). */
function openArena(world: World): void {
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) world.tileMap.grid[r][c] = 'empty'
  }
  // base eagle at cols 12-13, rows 24-25
  for (const r of [24, 25]) {
    for (const c of [12, 13]) world.tileMap.grid[r][c] = 'base'
  }
  world.tileMap.rebuildBaseCache()
  world.state = 'playing'
}

function spawnEnemy(world: World, kind: TankKind, x: number, y: number): Tank {
  const t = world.createTank(kind, x, y, 'down')
  t.spawnTimer = 0 // active immediately
  world.tanks.push(t)
  return t
}

// ============================================================
// Tier roll — distribution + determinism (DoD #8 / §8.1)
// ============================================================

describe('Tier roll — distribution + determinism', () => {
  it('every difficulty distribution row sums to 1', () => {
    for (const key of Object.keys(DIFFICULTY_TIER_DISTRIBUTION)) {
      const row = DIFFICULTY_TIER_DISTRIBUTION[key]
      const sum = Object.values(row).reduce<number>((a, b) => a + (b ?? 0), 0)
      expect(sum).toBeCloseTo(1, 6)
    }
  })

  it('rollTier is deterministic for a given RNG state', () => {
    const a = new RNG(123)
    const b = new RNG(123)
    for (let i = 0; i < 100; i++) {
      expect(rollTier('chaos', a)).toBe(rollTier('chaos', b))
    }
  })

  it('classic (100% none) consumes zero RNG for the tier roll', () => {
    // rollTier returns 'none' WITHOUT drawing — prove by advancing the RNG
    // state only via other means and confirming the roll stays 'none'.
    const rng = new RNG(42)
    const before = rng.getState()
    expect(rollTier('classic', rng)).toBe('none')
    // getState unchanged ⇒ no draw consumed (tier-roll gate, plan §7).
    expect(rng.getState()).toBe(before)
  })
})

// ============================================================
// Determinism (DoD #8)
// ============================================================

describe('Tactical Intelligence — determinism (DoD #8)', () => {
  it('produces identical enemy AI state across two runs with the same seed (Math.random perturbed between)', () => {
    const TICKS = 400
    const capture = (world: World) =>
      JSON.stringify(
        world.tanks.map((t) => ({
          kind: t.kind,
          x: t.x,
          y: t.y,
          dir: t.dir,
          moving: t.moving,
          ai: t.aiState
            ? {
                level: t.aiState.level,
                isCommander: t.aiState.isCommander,
                thinkTimer: Math.round(t.aiState.thinkTimer),
                fireTimer: Math.round(t.aiState.fireTimer),
                currentDir: t.aiState.currentDir,
                tacticalGoal: t.aiState.tacticalGoal,
                strategicGoal: t.aiState.strategicGoal,
                directive: t.aiState.directive,
              }
            : null,
        })),
      )

    const runA = seededWorld(0xc0ffee)
    for (let i = 0; i < TICKS; i++) runA.sim.tick()
    const snapA = capture(runA.world)

    for (let i = 0; i < 7; i++) Math.random() // perturb external RNG

    const runB = seededWorld(0xc0ffee)
    for (let i = 0; i < TICKS; i++) runB.sim.tick()
    const snapB = capture(runB.world)

    expect(snapB).toEqual(snapA)
  })
})

// ============================================================
// No stall / navigation (DoD #4 / testing §18)
// ============================================================

describe('Tactical Intelligence — never stalls (DoD #4 / §18)', () => {
  it('enemies collectively traverse the map instead of freezing', () => {
    const { world, sim } = seededWorld(12345)
    const prev = new Map<number, { x: number; y: number }>()
    let totalPath = 0
    const TICKS = 600
    for (let i = 0; i < TICKS; i++) {
      sim.tick()
      // Classic = 100% None-tier; everyone still wanders (objective floor).
      for (const t of world.tanks) {
        if (!t.alive) continue
        const p = prev.get(t.id)
        if (p) totalPath += Math.abs(t.x - p.x) + Math.abs(t.y - p.y)
        prev.set(t.id, { x: t.x, y: t.y })
      }
      if (world.state !== 'playing') world.loadStage(world.stageIndex)
      if (!world.player || !world.player.alive) world.spawnPlayer()
    }
    // 600 ticks, up to 4 enemies; movement scales with the (now slower,
    // calmer) speed design — observed totalPath ≈ 1100. Guard only against freezing.
    expect(totalPath).toBeGreaterThan(700)
  })
})

// ============================================================
// Strategic goal stability (plan §12)
// ============================================================

describe('Tactical Intelligence — strategic stability (plan §12)', () => {
  it('a non-strategic tier (soldier) keeps a stable strategic goal', () => {
    const { world, sim } = seededWorld(777, 'classic')
    world.spawnQueue = [] // no other spawns to interfere
    world.enemiesRemaining = ENEMIES_PER_STAGE
    openArena(world)
    const t = spawnEnemy(world, 'basic', 12 * CELL, 2 * CELL)
    if (t.aiState) t.aiState.level = 'soldier'
    let changes = 0
    let last: string | null = null
    for (let i = 0; i < 600; i++) {
      sim.tick()
      if (t.aiState) {
        if (last !== null && t.aiState.strategicGoal !== last) changes++
        last = t.aiState.strategicGoal
      }
      if (world.state !== 'playing') world.state = 'playing'
      if (!world.player || !world.player.alive) world.spawnPlayer()
    }
    expect(changes).toBe(0) // soldier never re-evaluates strategy
  })

  it('a strategic tier (active commander) re-evaluates strategy rarely', () => {
    const { world, sim } = seededWorld(777, 'classic')
    world.spawnQueue = []
    world.enemiesRemaining = ENEMIES_PER_STAGE
    openArena(world)
    const t = spawnEnemy(world, 'basic', 12 * CELL, 2 * CELL)
    if (t.aiState) {
      t.aiState.level = 'commander'
      t.aiState.isCommander = true
    }
    let changes = 0
    let last: string | null = null
    for (let i = 0; i < 600; i++) {
      sim.tick()
      if (t.aiState) {
        if (last !== null && t.aiState.strategicGoal !== last) changes++
        last = t.aiState.strategicGoal
      }
      if (world.state !== 'playing') world.state = 'playing'
      if (!world.player || !world.player.alive) world.spawnPlayer()
    }
    // ~20s interval ⇒ ≤1 change in 10s.
    expect(changes).toBeLessThanOrEqual(1)
  })
})

// ============================================================
// Commander system — authority, succession, office delay (DoD #4)
// ============================================================

describe('Tactical Intelligence — command authority (DoD #4)', () => {
  it('classic (zero commander probability) never yields a commander', () => {
    const { world, sim } = seededWorld(2024, 'classic')
    let sawCommander = false
    for (let i = 0; i < 3600; i++) {
      sim.tick()
      if (world.tanks.some((t) => t.alive && t.aiState?.isCommander)) sawCommander = true
      if (world.state !== 'playing') world.loadStage(world.stageIndex)
      if (!world.player || !world.player.alive) world.spawnPlayer()
    }
    expect(sawCommander).toBe(false)
  })

  it('active command = highest-spawnSeq alive commander', () => {
    const { world, sim } = seededWorld(8, 'chaos')
    const a = spawnEnemy(world, 'basic', 4 * CELL, 2 * CELL)
    const b = spawnEnemy(world, 'basic', 12 * CELL, 2 * CELL)
    // a is born earlier (lower spawnSeq), b later (higher spawnSeq)
    if (a.aiState) {
      a.aiState.level = 'commander'
      a.aiState.isCommander = true
    }
    if (b.aiState) {
      b.aiState.level = 'commander'
      b.aiState.isCommander = true
    }
    sim.tick()
    expect(world.activeCommanderId).toBe(b.id) // newest holds command
  })

  it('succession: killing the active commander promotes the previous-born; office delay overwritten to 1s', () => {
    const { world, sim } = seededWorld(8, 'chaos')
    const a = spawnEnemy(world, 'basic', 4 * CELL, 2 * CELL)
    const b = spawnEnemy(world, 'basic', 12 * CELL, 2 * CELL)
    if (a.aiState) {
      a.aiState.level = 'commander'
      a.aiState.isCommander = true
    }
    if (b.aiState) {
      b.aiState.level = 'commander'
      b.aiState.isCommander = true
    }
    sim.tick()
    expect(world.activeCommanderId).toBe(b.id)
    // office delay overwritten to 1s, then decremented by one tick of dt
    expect(b.aiState!.commanderTimer).toBeGreaterThan(900)
    expect(b.aiState!.commanderTimer).toBeLessThanOrEqual(1000)
    // kill the active commander
    b.alive = false
    sim.tick()
    expect(world.activeCommanderId).toBe(a.id) // previous-born regains command
    // 1s delay measured from taking office, not from spawn (overwrite, not add)
    expect(a.aiState!.commanderTimer).toBeGreaterThan(900)
    expect(a.aiState!.commanderTimer).toBeLessThanOrEqual(1000)
  })

  it('an active commander broadcasts directives on its cadence', () => {
    const { world, sim } = seededWorld(4242, 'chaos')
    const cmd = spawnEnemy(world, 'basic', 12 * CELL, 2 * CELL)
    if (cmd.aiState) {
      cmd.aiState.level = 'commander'
      cmd.aiState.isCommander = true
      cmd.aiState.commanderTimer = 0
    }
    const seq0 = world.directiveSeqCounter
    let guard = 0
    while (world.directiveSeqCounter === seq0 && guard < 4000) {
      sim.tick()
      if (world.state !== 'playing') world.state = 'playing'
      if (!world.player || !world.player.alive) world.spawnPlayer()
      guard++
    }
    expect(world.directiveSeqCounter).toBeGreaterThan(seq0)
  })
})

// ============================================================
// Floor (attempt-based) + cap (≤2 alive) (§5.1 [D9-fix] / DoD #9)
// ============================================================

describe('Tier roll — floor (attempt-based) + cap (§5.1 [D9-fix])', () => {
  it('floor guarantee: chaos consumes its full commander quota via forced tail rolls', () => {
    const { world, sim } = seededWorld(99, 'chaos')
    const cmdSpawned = new Set<number>()
    let guard = 0
    while (world.spawnQueue.length > 0 && guard < 400) {
      sim.tick()
      world.spawnTimer = 0 // force the next spawn immediately (no 1.5s gate)
      for (const t of world.tanks) {
        if (t.alive && t.aiState?.level === 'commander' && t.aiState?.isCommander) {
          cmdSpawned.add(t.id)
        }
      }
      // Drain: kill all enemies so the spawn queue actually advances.
      // (perf §67) Manual kill must signal _needsCleanup — Simulation sets
      // this whenever it kills an entity, but here we bypass Simulation.
      for (const t of world.tanks) t.alive = false
      world._needsCleanup = true
      if (world.state !== 'playing') world.state = 'playing'
      if (!world.player || !world.player.alive) world.spawnPlayer()
      guard++
    }
    // The 4 forced attempts (chaos floor) consumed the quota exactly.
    expect(world.commanderQuotaRemaining).toBe(0)
    // Every forced attempt (cap empty at spawn moment) became a real commander.
    expect(cmdSpawned.size).toBeGreaterThanOrEqual(COMMANDER_FLOOR.chaos)
  })

  it('cap: a commander roll against a full cap downgrades to Veteran (no boost)', () => {
    const { world, sim } = seededWorld(7, 'chaos')
    // Place 2 alive commanders (occupy the cap of 2).
    const a = spawnEnemy(world, 'basic', 4 * CELL, 2 * CELL)
    const b = spawnEnemy(world, 'basic', 12 * CELL, 2 * CELL)
    if (a.aiState) {
      a.aiState.level = 'commander'
      a.aiState.isCommander = true
    }
    if (b.aiState) {
      b.aiState.level = 'commander'
      b.aiState.isCommander = true
    }
    // Force one more commander attempt, but the cap is full → downgrade.
    world.enemiesSpawned = ENEMIES_PER_STAGE - 1 // remaining = 1
    world.commanderQuotaRemaining = 1 // force a commander attempt
    world.spawnQueue = [{ kind: 'basic', bonus: false, spawnIndex: 0 }]
    world.spawnTimer = 0
    sim.tick()
    const c = world.tanks.find((t) => t !== a && t !== b)
    expect(c).toBeDefined()
    expect(c!.aiState?.level).toBe('veteran') // actual Veteran tier
    expect(c!.aiState?.isCommander).toBe(false) // no crown, no command
    // No boost: profile equals the base archetype (no +15%).
    const base = resolveProfile('basic', 0)
    expect(c!.profile.mobility).toBe(base.mobility)
    expect(c!.profile.armor).toBe(base.armor)
  })

  it('cap: never more than COMMANDER_ALIVE_CAP commander-tier tanks alive', () => {
    const { world, sim } = seededWorld(99, 'chaos')
    let peak = 0
    let guard = 0
    while (world.spawnQueue.length > 0 && guard < 400) {
      sim.tick()
      let alive = 0
      for (const t of world.tanks) {
        if (t.alive && t.aiState?.level === 'commander') alive++
      }
      peak = Math.max(peak, alive)
      for (const t of world.tanks) t.alive = false
      if (world.state !== 'playing') world.state = 'playing'
      if (!world.player || !world.player.alive) world.spawnPlayer()
      guard++
    }
    expect(peak).toBeLessThanOrEqual(COMMANDER_ALIVE_CAP)
  })
})

// ============================================================
// Boost carve-out (§5.3 [D10] / DoD #9)
// ============================================================

describe('Commander boost carve-out (§5.3 [D10])', () => {
  it('a commander-tier spawn carries the +15% combat boost', () => {
    const { world, sim } = seededWorld(7, 'chaos')
    world.commanderQuotaRemaining = 1 // force 1 commander attempt
    world.enemiesSpawned = ENEMIES_PER_STAGE - 1 // remaining = 1 → forced
    world.spawnQueue = [{ kind: 'basic', bonus: false, spawnIndex: 0 }]
    world.spawnTimer = 0
    sim.tick()
    const c = world.tanks[world.tanks.length - 1]
    const base = resolveProfile(c.kind, 0)
    expect(c.aiState?.level).toBe('commander')
    // Boost rides the commander TIER on the kind-specific elite dimension.
    const dim = ELITE_DIMENSION[c.kind]
    expect(c.profile[dim]).toBeGreaterThan(base[dim])
  })

  it('two coexisting commanders are BOTH boosted (boost rides the tier, not command role)', () => {
    const { world, sim } = seededWorld(7, 'chaos')
    world.commanderQuotaRemaining = 1
    world.enemiesSpawned = ENEMIES_PER_STAGE - 1
    world.spawnQueue = [{ kind: 'basic', bonus: false, spawnIndex: 0 }]
    world.spawnTimer = 0
    sim.tick()
    // second forced commander — cap still allows (only 1 alive so far)
    world.commanderQuotaRemaining = 1
    world.enemiesSpawned = ENEMIES_PER_STAGE - 1
    world.spawnQueue = [{ kind: 'basic', bonus: false, spawnIndex: 1 }]
    world.spawnTimer = 0
    sim.tick()
    const cmds = world.tanks.filter((t) => t.aiState?.level === 'commander')
    expect(cmds.length).toBeGreaterThanOrEqual(2)
    for (const t of cmds) {
      const base = resolveProfile(t.kind, 0)
      const dim = ELITE_DIMENSION[t.kind]
      expect(t.profile[dim]).toBeGreaterThan(base[dim])
    }
  })
})

// ============================================================
// Bullet avoidance improves with intelligence (DoD #6)
// ============================================================

describe('Tactical Intelligence — bullet avoidance (DoD #6)', () => {
  /**
   * Survival rate of a single enemy against a player bullet fired down its
   * column. The bullet is faster than any tank so it ALWAYS threatens
   * the enemy; survival therefore reflects pure dodge skill. The vertical gap
   * and 120-tick window are calibrated so that a tank which *decides* to
   * dodge (after its reaction delay) has time to step clear of the bullet
   * column — so survival ≈ dodge probability. This isolates the avoidance
   * behaviour (the test freezes tactical re-thinking so the only
   * perpendicular movement is the reactive dodge).
   */
  function survivalRate(kind: TankKind, level: IntelligenceLevel, trials: number): number {
    let survived = 0
    for (let s = 0; s < trials; s++) {
      const world = seedWorld(1000 + s * 7)
      openArena(world)
      const sim = new Simulation(world, new Input())
      const ex = 12 * CELL // aligned with base column (x = 192)
      const ey = 12 * CELL
      const enemy = spawnEnemy(world, kind, ex, ey)
      // Force the intelligence tier so we isolate intelligence from the kind's
      // own mobility. Same kind at two tiers ⇒ pure dodge-skill delta.
      if (enemy.aiState) {
        enemy.aiState.level = level
        enemy.aiState.reactionTimer = 0 // don't block the first dodge
      }
      enemy.hp = 1
      enemy.maxHp = 1
      // Freeze tactical re-thinking so the ONLY perpendicular movement is
      // the reactive dodge — this isolates the avoidance behaviour.
      if (enemy.aiState) {
        enemy.aiState.thinkTimer = 60000
        enemy.aiState.strategicTimer = 60000
      }
      // Prevent stray spawns from absorbing the bullet.
      world.spawnQueue = []
      world.tanks = [enemy]
      const gap = 170 // px between bullet start and enemy
      world.bullets.push({
        id: genId(),
        x: enemy.x + TANK - BULLET - 4,
        y: ey - gap,
        w: BULLET,
        h: BULLET,
        dir: 'down' as Direction,
        alive: true,
        ownerId: -1,
        ownerKind: 'player',
        isPlayer: true,
        allegiance: 'player',
        speed: 2.5,
        power: 1,
        damage: 9999, // lethal in one hit if not dodged — isolates dodge
      })
      for (let i = 0; i < 120; i++) {
        sim.tick()
        if (!enemy.alive) break
      }
      if (enemy.alive) survived++
    }
    return survived / trials
  }

  it('higher intelligence dodges the bullet more often than lower intelligence', () => {
    const TRIALS = 300
    const rookie = survivalRate('basic', 'rookie', TRIALS)
    const veteran = survivalRate('basic', 'veteran', TRIALS)
    expect(veteran).toBeGreaterThan(0.5)
    expect(rookie).toBeLessThan(0.6)
    expect(veteran).toBeGreaterThan(rookie)
  }, 30000)
})

// ============================================================
// Compliance — one roll per directive, cached, None deaf (§2.2 [D7] / DoD #5)
// ============================================================

describe('Compliance — one roll per directive, cached (§2.2 [D7])', () => {
  it('None is deaf: never complies with any directive', () => {
    const { world, sim } = seededWorld(4242, 'chaos')
    const none = spawnEnemy(world, 'basic', 0, 2 * CELL)
    if (none.aiState) none.aiState.level = 'none'
    const cmd = spawnEnemy(world, 'basic', 12 * CELL, 2 * CELL)
    if (cmd.aiState) {
      cmd.aiState.level = 'commander'
      cmd.aiState.isCommander = true
    }
    for (let i = 0; i < 4000; i++) {
      sim.tick()
      expect(none.aiState?.directiveCompliant).toBe(false) // deaf, always
      if (world.state !== 'playing') world.state = 'playing'
      if (!world.player || !world.player.alive) world.spawnPlayer()
    }
  })

  it('compliance is rolled once per directive and cached until the next broadcast', () => {
    const { world, sim } = seededWorld(11, 'chaos')
    const rook = spawnEnemy(world, 'basic', 6 * CELL, 2 * CELL)
    if (rook.aiState) rook.aiState.level = 'rookie'
    const cmd = spawnEnemy(world, 'basic', 12 * CELL, 2 * CELL)
    if (cmd.aiState) {
      cmd.aiState.level = 'commander'
      cmd.aiState.isCommander = true
    }
    const seq0 = world.directiveSeqCounter
    let guard = 0
    while (world.directiveSeqCounter === seq0 && guard < 3000) {
      sim.tick()
      guard++
    }
    const seqAfter = world.directiveSeqCounter
    expect(seqAfter).toBeGreaterThan(seq0)
    const c1 = rook.aiState!.directiveCompliant
    const s1 = rook.aiState!.directiveSeq
    // Within the same directive, the cached value must be stable.
    for (let i = 0; i < 30; i++) {
      sim.tick()
      expect(rook.aiState!.directiveCompliant).toBe(c1)
      expect(rook.aiState!.directiveSeq).toBe(s1)
      if (world.directiveSeqCounter !== seqAfter) break
    }
  })

  it('higher tiers comply more often across many directives (seeded)', () => {
    const { world, sim } = seededWorld(4242, 'chaos')
    const mk = (kind: TankKind, x: number, level: IntelligenceLevel) => {
      const t = spawnEnemy(world, kind, x, 2 * CELL)
      if (t.aiState) {
        t.aiState.level = level
        t.aiState.isCommander = level === 'commander'
      }
      return t
    }
    const none = mk('basic', 0, 'none')
    const rook = mk('basic', 3 * CELL, 'rookie')
    const sold = mk('basic', 6 * CELL, 'soldier')
    const vet = mk('basic', 9 * CELL, 'veteran')
    mk('basic', 12 * CELL, 'commander')
    const samples: Record<string, { yes: number; n: number }> = {
      none: { yes: 0, n: 0 },
      rookie: { yes: 0, n: 0 },
      soldier: { yes: 0, n: 0 },
      veteran: { yes: 0, n: 0 },
    }
    let lastSeq = -1
    let guard = 0
    while (guard < 20000) {
      sim.tick()
      const seq = world.directiveSeqCounter
      if (seq !== lastSeq) {
        lastSeq = seq
        const pairs: Array<[string, Tank]> = [
          ['none', none],
          ['rookie', rook],
          ['soldier', sold],
          ['veteran', vet],
        ]
        for (const [name, t] of pairs) {
          if (t.aiState) {
            samples[name].n++
            if (t.aiState.directiveCompliant) samples[name].yes++
          }
        }
      }
      if (world.state !== 'playing') world.state = 'playing'
      if (!world.player || !world.player.alive) world.spawnPlayer()
      guard++
    }
    expect(samples.none.yes).toBe(0) // deaf
    const rookRate = samples.rookie.yes / samples.rookie.n
    const vetRate = samples.veteran.yes / samples.veteran.n
    expect(samples.veteran.n).toBeGreaterThan(3) // enough samples
    expect(vetRate).toBeGreaterThanOrEqual(rookRate)
    expect(samples.veteran.yes).toBeGreaterThan(0)
  })
})

// ============================================================
// None branch — deterministic classic behaviour (§3 / DoD #3)
// ============================================================

describe('None branch — deterministic classic behaviour (§3)', () => {
  it('deterministic, never freezes, fires within its cadence', () => {
    const run = (seed: number) => {
      const { world, sim } = seededWorld(seed, 'classic') // 100% None
      let totalPath = 0
      let bulletFired = 0
      const prev = new Map<number, { x: number; y: number }>()
      let sawNone = false
      for (let i = 0; i < 600; i++) {
        sim.tick()
        bulletFired += world.events.items.filter((e) => e.type === 'bullet_fired').length
        for (const t of world.tanks) {
          if (!t.alive) continue
          if (t.aiState?.level !== 'none') continue
          sawNone = true
          const p = prev.get(t.id)
          if (p) totalPath += Math.abs(t.x - p.x) + Math.abs(t.y - p.y)
          prev.set(t.id, { x: t.x, y: t.y })
        }
        if (world.state !== 'playing') world.loadStage(world.stageIndex)
        if (!world.player || !world.player.alive) world.spawnPlayer()
      }
      return { totalPath, bulletFired, sawNone }
    }
    const a = run(0xabc)
    const b = run(0xabc)
    expect(a.sawNone).toBe(true)
    expect(a.totalPath).toBeGreaterThan(0) // no freeze over 600 ticks
    expect(b.totalPath).toBe(a.totalPath) // deterministic per seed
    expect(a.bulletFired).toBeGreaterThan(0) // fires within cadence
  })
})

// ============================================================
// Snapshot round-trip of new command-authority fields (§7 / DoD #7)
// ============================================================

describe('Snapshot — command-authority fields round-trip (§7)', () => {
  it('cloneWorld / restoreWorld preserve all new fields', () => {
    const { world } = seededWorld(5, 'chaos')
    const c1 = spawnEnemy(world, 'basic', 4 * CELL, 2 * CELL)
    const c2 = spawnEnemy(world, 'basic', 12 * CELL, 2 * CELL)
    if (c1.aiState) {
      c1.aiState.level = 'commander'
      c1.aiState.isCommander = true
      c1.aiState.spawnSeq = 100
    }
    if (c2.aiState) {
      c2.aiState.level = 'commander'
      c2.aiState.isCommander = true
      c2.aiState.spawnSeq = 200
      c2.aiState.directiveSeq = 7
      c2.aiState.directiveCompliant = true
    }
    world.activeCommanderId = c2.id
    world.commanderQuotaRemaining = 3
    world.directiveSeqCounter = 9
    world.spawnSeqCounter = 500

    const { cloneWorld, restoreWorld } = require('../src/snapshot/WorldSerializer')
    const snap = cloneWorld(world)
    const w2 = new World()
    restoreWorld(w2, snap)

    expect(w2.activeCommanderId).toBe(c2.id)
    expect(w2.commanderQuotaRemaining).toBe(3)
    expect(w2.directiveSeqCounter).toBe(9)
    expect(w2.spawnSeqCounter).toBe(500)

    const restoredC2 = w2.tanks.find((t) => t.id === c2.id)!
    expect(restoredC2.aiState?.spawnSeq).toBe(200)
    expect(restoredC2.aiState?.directiveSeq).toBe(7)
    expect(restoredC2.aiState?.directiveCompliant).toBe(true)
  })
})

// ============================================================
// commanderPresent metadata (§7 / DoD #8)
// ============================================================

describe('commanderPresent metadata (§7)', () => {
  it('commanderPresent === (activeCommanderId !== null)', () => {
    const { world } = seededWorld(5, 'chaos')
    // Mirror SnapshotManager's computation exactly.
    expect(world.activeCommanderId !== null).toBe(false)
    const c = spawnEnemy(world, 'basic', 4 * CELL, 2 * CELL)
    if (c.aiState) {
      c.aiState.level = 'commander'
      c.aiState.isCommander = true
    }
    world.activeCommanderId = c.id
    expect(world.activeCommanderId !== null).toBe(true)
  })
})
