import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { RNG } from '../src/utils/RNG'
import { cloneWorld, restoreWorld } from '../src/snapshot/WorldSerializer'
import { computePlayer2SpawnCol } from '../src/utils/helpers'
import { GodAIInput } from '../src/ai/GodAIInput'
import { DIFFICULTIES } from '../src/config/difficulty'
import { THEMES } from '../src/config/theme'
import { STAGES } from '../src/config/stages'
import type { InputLike } from '../src/game/Input'
import type { Tank } from '../src/types'
import { TANK } from '../src/constants'

// ================================================================
// 督战双玩家 (dual supervise) — God AI drives BOTH player1 and player2.
// Headless coverage (AGENTS §8: no DOM) of the pure pieces:
//   - spawn-col helper (P1↔P2 mirror + overlap nudge)
//   - WorldSerializer round-trip of `spectateDual`
//   - Simulation deferred dual apply (the fix that makes dual survive startGame)
//   - GodAIInput dual wiring (godInput2 controls player2, partner logic)
// ================================================================

function makeWorld(seed = 42): World {
  const world = new World()
  world.rng = new RNG(seed)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.themeKey = 'classic'
  world.theme = THEMES['classic']
  world.rules = { ...world.rules }
  world.state = 'playing'
  world.player = null
  world.tanks = []
  world.bullets = []
  world.spectate = false
  world.spectateDual = false
  return world
}

function makeTank(overrides: Partial<Tank> = {}): Tank {
  return {
    id: 0,
    kind: 'basic',
    x: 100,
    y: 100,
    w: TANK,
    h: TANK,
    dir: 'up',
    speed: 1,
    moving: false,
    alive: true,
    hp: 1,
    maxHp: 1,
    level: 0,
    spawnTimer: 0,
    shieldTimer: 0,
    lastFire: 0,
    nextFireInterval: 500,
    fireCooldown: 0,
    fireCount: 0,
    bulletPower: 1,
    damage: 1,
    bulletSpeed: 3,
    vx: 0,
    vy: 0,
    profile: {
      firepower: 50,
      projectileSpeed: 50,
      fireControl: 50,
      mobility: 50,
      armor: 50,
      special: 50,
    },
    allegiance: 'player',
    isPlayer: true,
    ...overrides,
  }
}

/** A minimal InputLike that never moves or fires (idle keyboard). */
class IdleInput implements InputLike {
  getMoveDirection() {
    return null
  }
  isFiring() {
    return false
  }
  wasItemPressed() {
    return false
  }
  endFrame() {}
  reset() {}
}

// ---- (1) Spawn-col helper ----

describe('computePlayer2SpawnCol', () => {
  it('mirrors player-1 across the field center', () => {
    expect(computePlayer2SpawnCol(8)).toBe(16)
    expect(computePlayer2SpawnCol(16)).toBe(8)
  })

  it('nudges one cell when P1 is on the center column (avoids overlap)', () => {
    expect(computePlayer2SpawnCol(12)).toBe(11)
  })
})

// ---- (2) WorldSerializer round-trip of spectateDual ----

describe('spectateDual — WorldSerializer round-trip', () => {
  it('cloneWorld preserves the spectateDual flag', () => {
    const w = makeWorld()
    w.spectateDual = true
    const snap = cloneWorld(w)
    expect(snap.spectateDual).toBe(true)
  })

  it('restoreWorld restores spectateDual from the snapshot', () => {
    const w = makeWorld()
    w.spectateDual = true
    const snap = cloneWorld(w)
    const w2 = makeWorld()
    restoreWorld(w2, snap)
    expect(w2.spectateDual).toBe(true)
  })

  it('legacy snapshot without the field defaults to false', () => {
    const w = makeWorld()
    const snap = cloneWorld(w)
    const old = { ...snap } as Record<string, unknown>
    delete (old as { spectateDual?: boolean }).spectateDual
    const w2 = makeWorld()
    restoreWorld(w2, old as never)
    expect(w2.spectateDual).toBe(false)
  })
})

// ---- (3) Simulation deferred dual apply (the startGame-survival fix) ----

describe('spectateDual — Simulation deferred apply', () => {
  it('requestSpectateDualToggle(true) spawns player2 + sets flags on next tick', () => {
    const w = makeWorld()
    w.loadStageData(STAGES[0], 0) // spawns player, state → playing
    expect(w.player).not.toBeNull()
    expect(w.player2).toBeNull()
    const sim = new Simulation(w, new IdleInput())
    sim.requestSpectateToggle(true)
    sim.requestSpectateDualToggle(true)
    // Not applied until a playing tick fires (One-Author).
    expect(w.spectate).toBe(false)
    expect(w.spectateDual).toBe(false)
    expect(w.player2).toBeNull()
    sim.tick()
    expect(w.spectate).toBe(true)
    expect(w.spectateDual).toBe(true)
    expect(w.player2).not.toBeNull()
  })

  it('single supervise (dual=false) does NOT spawn player2 after the tick', () => {
    const w = makeWorld()
    w.loadStageData(STAGES[0], 0)
    const sim = new Simulation(w, new IdleInput())
    sim.requestSpectateToggle(true)
    sim.requestSpectateDualToggle(false)
    sim.tick()
    expect(w.spectate).toBe(true)
    expect(w.spectateDual).toBe(false)
    expect(w.player2).toBeNull()
  })

  it('clearPendingSpectateToggle cancels a pending dual toggle', () => {
    const w = makeWorld()
    w.loadStageData(STAGES[0], 0)
    const sim = new Simulation(w, new IdleInput())
    sim.requestSpectateToggle(true)
    sim.requestSpectateDualToggle(true)
    sim.clearPendingSpectateToggle()
    sim.tick()
    expect(w.spectate).toBe(false)
    expect(w.spectateDual).toBe(false)
    expect(w.player2).toBeNull()
  })

  // §BUG: 2x督战 接管后 P2 消失 —— 接管应从 dual spectate 切到 躺赢 (coop) 模式，
  // 保留 player2。修复靠 `w.coop = true` 让 deferred spectate-off apply 不剥离 P2。
  it('dual-spectate → coop takeover keeps player2 (regression guard)', () => {
    const w = makeWorld()
    w.loadStageData(STAGES[0], 0)
    const sim = new Simulation(w, new IdleInput())
    // Enter 2x督战 (God AI drives P1 + P2).
    sim.requestSpectateToggle(true)
    sim.requestSpectateDualToggle(true)
    sim.tick()
    expect(w.spectateDual).toBe(true)
    expect(w.player2).not.toBeNull()

    // Mimic takeOverFromSpectate for the dual case: flip to coop, then
    // queue a deferred spectate-off (what the sim applies on resume).
    w.coop = true
    sim.requestSpectateToggle(false)
    sim.requestSpectateDualToggle(false)
    const lives2Before = w.lives2
    const player2Before = w.player2

    sim.tick()

    // Spectate fully off, coop on, and crucially player2 SURVIVES the take-over.
    expect(w.spectate).toBe(false)
    expect(w.spectateDual).toBe(false)
    expect(w.coop).toBe(true)
    expect(w.player2).toBe(player2Before)
    expect(w.lives2).toBe(lives2Before)
  })
})

// ---- (4) GodAIInput dual wiring ----

describe('spectateDual — GodAIInput dual wiring', () => {
  it('godInput2 (controlledTank = player2) reports isPlayer2() and a living partner', () => {
    const w = makeWorld()
    const p1 = makeTank({ id: 1, isPlayer: true })
    const p2 = makeTank({ id: 2, isPlayer: true })
    w.player = p1
    w.player2 = p2
    w.tanks = [p1, p2]

    const god1 = new GodAIInput(w, undefined, new RNG(1), (world) => world.player)
    const god2 = new GodAIInput(w, undefined, new RNG(2), (world) => world.player2)

    expect(god1.isPlayer2()).toBe(false)
    expect(god2.isPlayer2()).toBe(true)
    expect(god2.coopPartner()).toBe(p1)
    expect(god2.hasLivingPartner()).toBe(true)
    expect(god1.hasLivingPartner()).toBe(true)
  })

  it('hasLivingPartner() is false when the partner is dead', () => {
    const w = makeWorld()
    const p1 = makeTank({ id: 1, isPlayer: true, alive: false })
    const p2 = makeTank({ id: 2, isPlayer: true })
    w.player = p1
    w.player2 = p2
    w.tanks = [p1, p2]
    // god2 controls player2, so its partner is player1 (dead) → no living partner.
    const god2 = new GodAIInput(w, undefined, new RNG(2), (world) => world.player2)
    expect(god2.hasLivingPartner()).toBe(false)
  })
})
