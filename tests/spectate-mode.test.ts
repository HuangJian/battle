import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { cloneWorld, restoreWorld } from '../src/snapshot/WorldSerializer'
import type { WorldSnapshot } from '../src/snapshot/types'
import { makeTank, seedWorld } from './helpers'
import { cycleBattleSpeed, BATTLE_SPEEDS } from '../src/game/battleSpeed'
import { DIFFICULTIES } from '../src/config/difficulty'
import { THEMES } from '../src/config/theme'
import type { InputLike } from '../src/game/Input'

// ================================================================
// 督战 (supervise) mode — God AI fights as PLAYER1, no human input.
// Headless coverage of the pure pieces (AGENTS §8: no DOM in tests):
// battle-speed ladder, spectate serialization, deferred World toggle,
// and the Q4-style high-score gating (spectate runs never save).
// ================================================================

function makeWorld(seed = 42): World {
  const world = seedWorld(seed)
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
  world.coop = false
  return world
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

// ---- (1) Battle-speed ladder (Alt+> / Alt+<) ----

describe('battleSpeed — cycleBattleSpeed ladder', () => {
  it('steps up the ladder from 1×', () => {
    expect(BATTLE_SPEEDS).toEqual([1, 1.5, 2, 4])
    expect(cycleBattleSpeed(1, 1)).toBe(1.5)
    expect(cycleBattleSpeed(1.5, 1)).toBe(2)
    expect(cycleBattleSpeed(2, 1)).toBe(4)
  })

  it('steps down the ladder', () => {
    expect(cycleBattleSpeed(4, -1)).toBe(2)
    expect(cycleBattleSpeed(2, -1)).toBe(1.5)
    expect(cycleBattleSpeed(1.5, -1)).toBe(1)
  })

  it('clamps at both ends', () => {
    expect(cycleBattleSpeed(4, 1)).toBe(4)
    expect(cycleBattleSpeed(1, -1)).toBe(1)
  })

  it('handles unknown current values as 1×', () => {
    expect(cycleBattleSpeed(3, 1)).toBe(1.5)
    expect(cycleBattleSpeed(0.1, -1)).toBe(1)
  })
})

// ---- (2) Spectate serialization ----

describe('spectate — WorldSerializer round-trip', () => {
  it('cloneWorld preserves the spectate flag', () => {
    const w = makeWorld()
    w.spectate = true
    const snap = cloneWorld(w)
    expect(snap.spectate).toBe(true)
  })

  it('restoreWorld restores spectate from the snapshot', () => {
    const w = makeWorld()
    w.spectate = true
    const snap = cloneWorld(w)
    const w2 = makeWorld()
    restoreWorld(w2, snap)
    expect(w2.spectate).toBe(true)
  })

  it('legacy snapshot without the spectate field defaults to false', () => {
    const w = makeWorld()
    const snap = cloneWorld(w)
    const oldSnap = { ...snap } as Partial<WorldSnapshot>
    delete oldSnap.spectate
    const w2 = makeWorld()
    restoreWorld(w2, oldSnap as WorldSnapshot)
    expect(w2.spectate).toBe(false)
  })
})

// ---- (3) Deferred World toggle (One-Author) ----

describe('spectate — Simulation deferred toggle', () => {
  it('requestSpectateToggle(true) applies world.spectate on the next tick', () => {
    const w = makeWorld()
    const sim = new Simulation(w, new IdleInput())
    sim.requestSpectateToggle(true)
    // Not applied until a playing tick fires (One-Author).
    expect(w.spectate).toBe(false)
    sim.tick()
    expect(w.spectate).toBe(true)
  })

  it('requestSpectateToggle(false) turns spectate off on the next tick', () => {
    const w = makeWorld()
    w.spectate = true
    const sim = new Simulation(w, new IdleInput())
    sim.requestSpectateToggle(false)
    sim.tick()
    expect(w.spectate).toBe(false)
  })

  it('clearPendingSpectateToggle cancels a stale toggle', () => {
    const w = makeWorld()
    const sim = new Simulation(w, new IdleInput())
    sim.requestSpectateToggle(true)
    sim.clearPendingSpectateToggle()
    sim.tick()
    expect(w.spectate).toBe(false)
  })
})

// ---- (4) High-score gating (督战 runs never save) ----

describe('spectate — high-score gating (Q4 analog)', () => {
  it('spectate game over does NOT update the high score', () => {
    const w = makeWorld()
    w.spectate = true
    w.score = 5000
    w.highScore = 1000
    w.lives = 1
    w.player = makeTank({ id: 1, alive: false })
    const sim = new Simulation(w, new IdleInput())
    sim.tick()
    expect(w.state).toBe('gameover')
    // score (5000) exceeded highScore (1000), but spectate skips saving.
    expect(w.highScore).toBe(1000)
  })

  it('normal game over DOES update the high score', () => {
    const w = makeWorld()
    w.spectate = false
    w.score = 5000
    w.highScore = 1000
    w.lives = 1
    w.player = makeTank({ id: 1, alive: false })
    const sim = new Simulation(w, new IdleInput())
    sim.tick()
    expect(w.state).toBe('gameover')
    expect(w.highScore).toBe(5000)
  })

  it('startGame resets spectate to false (fresh runs start clean)', () => {
    const w = makeWorld()
    w.spectate = true
    w.startGame('classic', 'classic', 0)
    expect(w.spectate).toBe(false)
  })
})
