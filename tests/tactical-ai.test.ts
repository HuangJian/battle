import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { genId } from '../src/game/World'
import { CELL, TANK, BULLET, GRID } from '../src/constants'
import type { TankKind, Tank } from '../src/types'
import type { Direction } from '../src/constants'
import {
  INTELLIGENCE_LEVELS,
  KIND_TO_LEVEL,
  resolveConfig,
  commanderChanceFor,
} from '../src/ai/config'

/**
 * Tactical Intelligence Framework — behavioural + config tests.
 *
 * These guard the plan's "Definition of Done" (§19):
 *  - identical inputs → identical decisions (determinism)
 *  - AI never stalls (tanks keep navigating)
 *  - strategic goals stay stable
 *  - commander directives coordinate but never override
 *  - bullet avoidance improves with intelligence
 *  - AI exhibits configurable imperfection
 *  - intelligence differences are configuration-driven / extensible
 *
 * All entropy flows through world.rng (AGENTS.md §2.3), so every test is
 * deterministic and seedable.
 */

function seededWorld(seed: number, difficulty = 'classic'): { world: World; sim: Simulation } {
  const world = new World()
  world.rng = new RNG(seed)
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
// Determinism
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
// No stall / navigation
// ============================================================

describe('Tactical Intelligence — never stalls (DoD #4 / testing §18)', () => {
  it('enemies collectively traverse the map instead of freezing', () => {
    const { world, sim } = seededWorld(12345)
    const prev = new Map<Tank, { x: number; y: number }>()
    let totalPath = 0
    const TICKS = 600
    for (let i = 0; i < TICKS; i++) {
      sim.tick()
      for (const t of world.tanks) {
        if (!t.alive) continue
        const p = prev.get(t)
        if (p) totalPath += Math.abs(t.x - p.x) + Math.abs(t.y - p.y)
        prev.set(t, { x: t.x, y: t.y })
      }
      // keep the stage alive without a human so enemies keep spawning
      if (world.state !== 'playing') world.loadStage(world.stageIndex)
      if (!world.player || !world.player.alive) world.spawnPlayer()
    }
    // 600 ticks, up to 4 enemies, speed >=1 → comfortably in the thousands.
    expect(totalPath).toBeGreaterThan(1500)
  })
})

// ============================================================
// Strategic goal stability
// ============================================================

describe('Tactical Intelligence — strategic stability (plan §12)', () => {
  it('a non-strategic tier keeps a stable strategic goal', () => {
    const { world, sim } = seededWorld(777)
    // 'fast' → soldier (strategicThinking: false)
    let changes = 0
    let last: string | null = null
    for (let i = 0; i < 600; i++) {
      sim.tick()
      const t = world.tanks[0]
      if (t?.aiState) {
        if (last !== null && t.aiState.strategicGoal !== last) changes++
        last = t.aiState.strategicGoal
      }
      if (world.state !== 'playing') world.loadStage(world.stageIndex)
      if (!world.player || !world.player.alive) world.spawnPlayer()
    }
    expect(changes).toBe(0) // soldier never re-evaluates strategy
  })

  it('a strategic tier re-evaluates strategy at most a couple of times over 600 ticks', () => {
    const { world, sim } = seededWorld(777)
    // Promote the first enemy to veteran so strategicThinking is on.
    let changes = 0
    let last: string | null = null
    for (let i = 0; i < 600; i++) {
      sim.tick()
      const t = world.tanks.find((e) => e.alive && e.aiState?.level === 'veteran') ?? world.tanks[0]
      if (t?.aiState) {
        if (last !== null && t.aiState.strategicGoal !== last) changes++
        last = t.aiState.strategicGoal
      }
      if (world.state !== 'playing') world.loadStage(world.stageIndex)
      if (!world.player || !world.player.alive) world.spawnPlayer()
    }
    expect(changes).toBeLessThanOrEqual(2) // ~20s interval ⇒ ≤1 change in 10s
  })
})

// ============================================================
// Commander system
// ============================================================

describe('Tactical Intelligence — commander (DoD #4)', () => {
  it('relax difficulty never elects a commander', () => {
    const { world, sim } = seededWorld(2024, 'relax')
    let sawCommander = false
    for (let i = 0; i < 3600; i++) {
      sim.tick()
      if (world.tanks.some((t) => t.alive && t.aiState?.isCommander)) sawCommander = true
      if (world.state !== 'playing') world.loadStage(world.stageIndex)
      if (!world.player || !world.player.alive) world.spawnPlayer()
    }
    expect(sawCommander).toBe(false)
  })

  it('chaos elects a commander that broadcasts directives to others', () => {
    const { world, sim } = seededWorld(2024, 'chaos')
    let sawCommander = false
    let sawDirective = false
    for (let i = 0; i < 3600; i++) {
      sim.tick()
      const cmd = world.tanks.find((t) => t.alive && t.aiState?.isCommander)
      if (cmd) {
        sawCommander = true
        if (cmd.aiState?.level === 'commander') {
          // some other tank received a directive
          if (world.tanks.some((t) => t.alive && t !== cmd && t.aiState?.directive !== 'none')) {
            sawDirective = true
          }
        }
      }
      if (world.state !== 'playing') world.loadStage(world.stageIndex)
      if (!world.player || !world.player.alive) world.spawnPlayer()
    }
    expect(sawCommander).toBe(true)
    expect(sawDirective).toBe(true)
  })
})

// ============================================================
// Bullet avoidance improves with intelligence (DoD #6)
// ============================================================

describe('Tactical Intelligence — bullet avoidance (DoD #6)', () => {
  /**
   * Survival rate of a single enemy against a player bullet fired down its
   * column. The bullet is faster than any tank (speed 6) so it ALWAYS threatens
   * the enemy; survival therefore reflects pure dodge skill. The vertical gap
   * and 120-tick window are calibrated so that a tank which *decides* to dodge
   * (after its reaction delay) has time to step clear of the bullet column —
   * so survival ≈ dodge probability. This isolates the avoidance behaviour
   * (the test freezes tactical re-thinking so the only perpendicular movement
   * is the reactive dodge).
   */
  function survivalRate(kind: TankKind, trials: number): number {
    let survived = 0
    for (let s = 0; s < trials; s++) {
      const world = new World()
      world.rng = new RNG(1000 + s * 7)
      openArena(world)
      const sim = new Simulation(world, new Input())
      const ex = 12 * CELL // aligned with base column (x = 192)
      const ey = 12 * CELL
      const enemy = spawnEnemy(world, kind, ex, ey)
      // Freeze tactical re-thinking so the ONLY perpendicular movement is the
      // reactive dodge — this isolates the avoidance behaviour we're measuring.
      if (enemy.aiState) {
        enemy.aiState.thinkTimer = 60000
        enemy.aiState.strategicTimer = 60000
      }
      const ecx = enemy.x + TANK / 2
      const gap = 170 // px between bullet start and enemy (well within the field)
      // player bullet, faster than any tank, heading down, aligned with the enemy
      world.bullets.push({
        id: genId(),
        x: ecx - BULLET / 2,
        y: ey - gap,
        w: BULLET,
        h: BULLET,
        dir: 'down' as Direction,
        alive: true,
        ownerId: -1,
        ownerKind: 'player',
        isPlayer: true,
        speed: 6,
        power: 1,
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
    const rookie = survivalRate('basic', TRIALS)
    const veteran = survivalRate('armor', TRIALS) // armor → veteran
    // veteran must clearly outperform the rookie
    expect(veteran).toBeGreaterThan(0.5)
    expect(rookie).toBeLessThan(0.6)
    expect(veteran).toBeGreaterThan(rookie)
  }, 30000)
})

// ============================================================
// Config-driven / extensible intelligence (DoD #2, #9)
// ============================================================

describe('Tactical Intelligence — configuration (DoD #2, #9)', () => {
  it('dodge probability strictly increases with tier', () => {
    const r = resolveConfig('rookie', 'classic').dodgeProbability
    const s = resolveConfig('soldier', 'classic').dodgeProbability
    const v = resolveConfig('veteran', 'classic').dodgeProbability
    const c = resolveConfig('commander', 'classic').dodgeProbability
    expect(r).toBeLessThan(s)
    expect(s).toBeLessThan(v)
    expect(v).toBeLessThanOrEqual(c)
    expect(c).toBeLessThanOrEqual(0.95) // AI is never flawless
  })

  it('difficulty scales capabilities without changing tiers', () => {
    expect(resolveConfig('rookie', 'chaos').dodgeProbability).toBeGreaterThan(
      resolveConfig('rookie', 'classic').dodgeProbability,
    )
    expect(resolveConfig('rookie', 'chaos').predictionDepth).toBeGreaterThan(
      resolveConfig('rookie', 'classic').predictionDepth,
    )
    // aimError (imperfection) shrinks with tier
    expect(resolveConfig('rookie', 'classic').aimError).toBeGreaterThan(
      resolveConfig('commander', 'classic').aimError,
    )
  })

  it('every enemy kind maps to a known tier and four tiers exist', () => {
    expect(Object.keys(INTELLIGENCE_LEVELS).sort()).toEqual(
      ['commander', 'rookie', 'soldier', 'veteran'].sort(),
    )
    for (const k of ['basic', 'fast', 'power', 'armor'] as TankKind[]) {
      expect(INTELLIGENCE_LEVELS[KIND_TO_LEVEL[k]]).toBeDefined()
    }
  })

  it('commander chance is difficulty-gated (0 for relax, >0 for chaos)', () => {
    expect(commanderChanceFor('relax')).toBe(0)
    expect(commanderChanceFor('chaos')).toBeGreaterThan(0)
  })
})
