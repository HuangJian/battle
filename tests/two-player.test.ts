import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { InputLike } from '../src/game/Input'
import { CELL, TICK_MS } from '../src/constants'
import type { Direction } from '../src/constants'
import { createTestWorld, makeEmptyStage, placeEnemy } from './helpers'
import { cloneWorld, restoreWorld } from '../src/snapshot/WorldSerializer'

/**
 * Two-Player mode (双打模式) — a second HUMAN input source drives player2
 * through the same `simulation.input2` slot Lie-Back-Win coop uses for the
 * God AI. World flag `twoPlayer` mirrors `coop`'s contract: same P2 slot,
 * same score/lives/star pools, but human-controlled (high score eligible).
 *
 * Tests are headless (AGENTS §8): scripted InputLike sources, fixed seeds.
 */

/** Scripted input source: the test sets dir/fire before each tick. */
class ScriptedInput implements InputLike {
  dir: Direction | null = null
  fire = false
  getMoveDirection(): Direction | null {
    return this.dir
  }
  isFiring(): boolean {
    return this.fire
  }
  wasItemPressed(_kind: 'guard' | 'frenzy' | 'rewind'): boolean {
    return false
  }
  endFrame(): void {}
  reset(): void {}
}

/** Two-player World: playing state, empty arena, P2 online, human-driven. */
function makeTwoPlayerWorld(seed = 42): { world: World; sim: Simulation; in1: ScriptedInput; in2: ScriptedInput } {
  const world = createTestWorld({ rngSeed: seed })
  world.difficultyKey = 'classic'
  world.loadStageData(makeEmptyStage(), 0)
  world.state = 'playing'
  world.twoPlayer = true
  world.enablePlayer2()
  for (const p of [world.player, world.player2]) {
    if (p) {
      p.spawnTimer = 0
      p.shieldTimer = 0
    }
  }
  const in1 = new ScriptedInput()
  const in2 = new ScriptedInput()
  const sim = new Simulation(world, in1)
  sim.input2 = in2
  return { world, sim, in1, in2 }
}

/** Park an enemy so it cannot move/fire — a deterministic bullet target. */
function placeDummy(world: World, col: number, row: number): void {
  const enemy = placeEnemy(world, col, row, 'basic', 'down')
  enemy.hp = 1
  enemy.maxHp = 1
  enemy.speed = 0
  if (enemy.aiState) {
    enemy.aiState.thinkTimer = 1e9
    enemy.aiState.fireTimer = 1e9
    enemy.aiState.strategicTimer = 1e9
  }
}

describe('Two-Player mode — input driving', () => {
  it('input2 moves player2 while input drives player1 independently', () => {
    const { world, sim, in1, in2 } = makeTwoPlayerWorld()
    const p1 = world.player!
    const p2 = world.player2!
    const p1x = p1.x
    const p2x = p2.x

    in1.dir = null
    in2.dir = 'left'
    for (let i = 0; i < 30; i++) sim.tick()

    expect(p2.x).toBeLessThan(p2x) // P2 moved left
    expect(p1.x).toBe(p1x) // P1 did not move

    in1.dir = 'up'
    in2.dir = null
    for (let i = 0; i < 30; i++) sim.tick()

    expect(p1.y).toBeLessThan(p2.y) // P1 moved up, P2 stayed
  })

  it('P2 firing spawns a bullet owned by player2', () => {
    const { world, sim, in2 } = makeTwoPlayerWorld()
    in2.dir = null
    in2.fire = true
    // Hold fire across a burst window: the fire gate (cooldown model —
    // nextFireInterval ≈ 825ms on a fresh tank) blocks the first ~50 ticks.
    let found: { ownerId: number; isPlayer: boolean } | null = null
    for (let i = 0; i < 60 && !found; i++) {
      sim.tick()
      const b = world.bullets.find((bl) => bl.ownerId === world.player2!.id)
      if (b) found = { ownerId: b.ownerId, isPlayer: b.isPlayer }
    }
    expect(found).not.toBeNull()
    expect(found!.isPlayer).toBe(true)
  })
})

describe('Two-Player mode — score attribution', () => {
  it('P1 kill credits score, P2 kill credits score2', () => {
    const { world, sim, in1, in2 } = makeTwoPlayerWorld()
    const p1 = world.player!
    const p2 = world.player2!
    const p1Col = Math.round(p1.x / CELL)
    const p2Col = Math.round(p2.x / CELL)

    // Park one dummy directly above each player.
    placeDummy(world, p1Col, 20)
    placeDummy(world, p2Col, 20)

    in1.dir = null
    in2.dir = null
    in1.fire = true
    in2.fire = true
    for (let i = 0; i < 90; i++) sim.tick()

    expect(world.score).toBeGreaterThan(0) // P1's kill → P1 pool
    expect(world.score2).toBeGreaterThan(0) // P2's kill → P2 pool
    expect(world.killCount).toBe(2)
  })
})

describe('Two-Player mode — deaths & defeat', () => {
  it('P2 death consumes lives2 and respawns player2', () => {
    const { world, sim } = makeTwoPlayerWorld()
    world.lives2 = 3
    world.player2!.alive = false
    sim.tick()
    expect(world.lives2).toBe(2)
    expect(world.player2).not.toBeNull()
    expect(world.player2!.alive).toBe(true)
    expect(world.state).toBe('playing')
  })

  it('both players out → gameover', () => {
    const { world, sim } = makeTwoPlayerWorld()
    world.lives = 1
    world.lives2 = 1
    world.player!.alive = false
    world.player2!.alive = false
    sim.tick()
    expect(world.state).toBe('gameover')
  })
})

describe('Two-Player mode — mode exclusivity (Simulation level)', () => {
  it('enabling twoPlayer through the deferred toggle disables coop', () => {
    const world = createTestWorld({ rngSeed: 7 })
    world.state = 'playing'
    world.coop = true
    world.enablePlayer2()
    const sim = new Simulation(world, new ScriptedInput())
    sim.requestTwoPlayerToggle(true)
    sim.tick()
    expect(world.twoPlayer).toBe(true)
    expect(world.coop).toBe(false)
    expect(world.player2).not.toBeNull()
  })

  it('disabling twoPlayer removes player2', () => {
    const { world, sim } = makeTwoPlayerWorld()
    sim.requestTwoPlayerToggle(false)
    sim.tick()
    expect(world.twoPlayer).toBe(false)
    expect(world.player2).toBeNull()
    expect(world.lives2).toBe(0)
  })
})

describe('Two-Player mode — snapshot roundtrip', () => {
  it('twoPlayer flag + P2 pools survive clone/restore', () => {
    const { world } = makeTwoPlayerWorld()
    world.score2 = 500
    world.lives2 = 2

    const snap = cloneWorld(world)
    const restored = new World()
    restoreWorld(restored, snap)

    expect(restored.twoPlayer).toBe(true)
    expect(restored.score2).toBe(500)
    expect(restored.lives2).toBe(2)
    expect(restored.player2).not.toBeNull()
    expect(restored.player2!.id).toBe(world.player2!.id)
  })
})

describe('Two-Player mode — determinism (AGENTS §2.3)', () => {
  it('same seed + same scripted inputs → identical world state', () => {
    const script: { d1: Direction | null; f1: boolean; d2: Direction | null; f2: boolean }[] = []
    // Deterministic 240-tick script for both players.
    for (let i = 0; i < 240; i++) {
      script.push({
        d1: (['up', 'left', 'right', null] as const)[i % 4],
        f1: i % 17 === 0,
        d2: (['down', 'right', null, 'up'] as const)[i % 4],
        f2: i % 23 === 0,
      })
    }

    const run = (): Record<string, unknown> => {
      const { world, sim, in1, in2 } = makeTwoPlayerWorld(123)
      for (const frame of script) {
        in1.dir = frame.d1
        in1.fire = frame.f1
        in2.dir = frame.d2
        in2.fire = frame.f2
        sim.tick()
        in1.endFrame()
        in2.endFrame()
      }
      return {
        frame: world.frame,
        score: world.score,
        score2: world.score2,
        p1: world.player ? { x: world.player.x, y: world.player.y, hp: world.player.hp } : null,
        p2: world.player2 ? { x: world.player2.x, y: world.player2.y, hp: world.player2.hp } : null,
        bullets: world.bullets.map((b) => ({ id: b.id, x: b.x, y: b.y })).length,
        rng: world.rng.getState(),
      }
    }

    expect(run()).toEqual(run())
  })
})

describe('Two-Player mode — tick pacing sanity', () => {
  it('tick() advances the frame counter (TICK_MS contract untouched)', () => {
    const { world, sim } = makeTwoPlayerWorld()
    const before = world.frame
    sim.tick()
    expect(world.frame).toBe(before + 1)
    expect(TICK_MS).toBe(1000 / 60)
  })
})
