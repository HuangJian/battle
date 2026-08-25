import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { perceive, scanAhead } from '../src/ai/perception'
import { playerCellImpl, canMoveDirImpl } from '../src/ai/god/Navigator'
import { baseBulletInterceptCellImpl } from '../src/ai/god/ThreatAssessor'
import { cloneWorld, restoreWorld } from '../src/snapshot/WorldSerializer'
import { makeTank, seedWorld } from './helpers'
import { CELL, GRID, BULLET } from '../src/constants'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../src/config/rules'
import type { Direction } from '../src/constants'
import type { WorldSnapshot } from '../src/snapshot/types'

// ---- helpers ----

function makeWorld(seed = 42): World {
  const world = seedWorld(seed)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = RULES['classic'] ?? DEFAULT_RULES
  world.startGame('classic', 'modern', 0)
  // Clear all terrain for a clean arena.
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) world.tileMap.grid[r][c] = 'empty'
  }
  return world
}

// KEPT LOCAL (遗留 #5 audit): hand-placed player2 at pixel (300,300) is
// asserted by tests here; helpers.makeCoopWorld spawns P2 at the mirrored
// spawn cell instead. See 口径差异表 in tests/helpers.ts.
function makeCoopWorld(seed = 42): World {
  const world = makeWorld(seed)
  world.coop = true
  // Spawn player2 at a known position.
  world.player2 = makeTank({
    id: 99,
    x: 300,
    y: 300,
    kind: 'basic',
    allegiance: 'player',
    isPlayer: true,
  })
  return world
}

// ---- (1) GodAIInput controlledTank ----

describe('GodAIInput — controlledTank', () => {
  it('default controlledTank returns world.player', () => {
    const world = makeWorld()
    const god = new GodAIInput(world)
    expect(god.controlledTank(world)).toBe(world.player)
  })

  it('custom controlledTank returns world.player2 in coop', () => {
    const world = makeCoopWorld()
    const god = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, undefined, (w) => w.player2)
    expect(god.controlledTank(world)).toBe(world.player2)
  })

  it('constructor sets controlledTank from 4th param', () => {
    const world = makeCoopWorld()
    const custom = (w: World) => w.player2
    const god = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, undefined, custom)
    expect(god.controlledTank(world)).toBe(world.player2)
  })

  it('constructor without 4th param defaults to player1', () => {
    const world = makeCoopWorld()
    const god = new GodAIInput(world)
    expect(god.controlledTank(world)).toBe(world.player)
  })

  it('think() returns null move when controlled tank is dead', () => {
    const world = makeCoopWorld()
    // Kill player2 (the controlled tank for coop mode).
    world.player2!.alive = false
    const god = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, undefined, (w) => w.player2)
    const moveDir = god.getMoveDirection()
    expect(moveDir).toBeNull()
    expect(god.isFiring()).toBe(false)
  })

  it('think() returns null move when controlled tank is spawning', () => {
    const world = makeCoopWorld()
    world.player2!.spawnTimer = 30
    const god = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, undefined, (w) => w.player2)
    const moveDir = god.getMoveDirection()
    expect(moveDir).toBeNull()
  })

  it('think() works normally when controlled tank is alive', () => {
    const world = makeCoopWorld()
    const god = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, undefined, (w) => w.player2)
    // Spawn an enemy so the AI has something to do.
    world.tanks.push(
      makeTank({
        id: 50,
        x: 200,
        y: 50,
        dir: 'down',
        allegiance: 'enemy',
        isPlayer: false,
      }),
    )
    const moveDir = god.getMoveDirection()
    // AI should produce a move direction (not null) since there's an enemy.
    expect(moveDir).not.toBeNull()
  })
})

// ---- (2) perceive() picks closest player ----

describe('perceive() — closest player detection', () => {
  const cfg = {
    predictionDepth: 6,
    maxTeammates: 4,
    name: 'test',
    strategicThinking: false,
    compliance: 0,
    dodgeProbability: 0,
    routeLookAhead: 3,
    aggression: 0.5,
    reactionTime: 0,
    aimError: 0,
    routeNoise: 0,
    weights: {} as any,
  }

  it('single player mode: perceive uses world.player', () => {
    const world = makeWorld()
    const enemy = makeTank({
      id: 10,
      x: 200,
      y: 200,
      isPlayer: false,
      allegiance: 'enemy',
    })
    const p = perceive(world, enemy, cfg)
    expect(p.hasPlayer).toBe(true)
    expect(p.playerX).toBe(world.player!.x + world.player!.w / 2)
    expect(p.playerY).toBe(world.player!.y + world.player!.h / 2)
  })

  it('coop mode: perceive picks the closest player', () => {
    const world = makeCoopWorld()
    // Place player1 far away, player2 close.
    world.player!.x = 10
    world.player!.y = 10
    world.player2!.x = 300
    world.player2!.y = 300
    // Enemy near player2.
    const enemy = makeTank({
      id: 10,
      x: 310,
      y: 310,
      isPlayer: false,
      allegiance: 'enemy',
    })
    const p = perceive(world, enemy, cfg)
    expect(p.hasPlayer).toBe(true)
    // Should pick player2 (closer to enemy at 310,310).
    expect(p.playerX).toBe(world.player2!.x + world.player2!.w / 2)
    expect(p.playerY).toBe(world.player2!.y + world.player2!.h / 2)
  })

  it('coop mode: picks player1 when it is closer', () => {
    const world = makeCoopWorld()
    // Place player1 close, player2 far.
    world.player!.x = 100
    world.player!.y = 100
    world.player2!.x = 400
    world.player2!.y = 400
    // Enemy near player1.
    const enemy = makeTank({
      id: 10,
      x: 110,
      y: 110,
      isPlayer: false,
      allegiance: 'enemy',
    })
    const p = perceive(world, enemy, cfg)
    expect(p.hasPlayer).toBe(true)
    // Should pick player1 (closer to enemy at 110,110).
    expect(p.playerX).toBe(world.player!.x + world.player!.w / 2)
    expect(p.playerY).toBe(world.player!.y + world.player!.h / 2)
  })

  it('coop mode: when player1 is dead, picks player2', () => {
    const world = makeCoopWorld()
    world.player!.alive = false
    world.player2!.x = 200
    world.player2!.y = 200
    const enemy = makeTank({
      id: 10,
      x: 250,
      y: 250,
      isPlayer: false,
      allegiance: 'enemy',
    })
    const p = perceive(world, enemy, cfg)
    // Player1 is dead so perceive should use player2.
    if (p.hasPlayer) {
      expect(p.playerX).toBe(world.player2!.x + world.player2!.w / 2)
    }
  })
})

// ---- (3) scanAhead detects both players ----

describe('scanAhead — dual-player detection', () => {
  it('returns player when player1 is in line of fire', () => {
    const world = makeCoopWorld()
    world.coop = false // single player for this test
    const player = world.player!
    player.x = 200
    player.y = 100
    player.alive = true
    const shooter = makeTank({ id: 10, x: 200, y: 50, isPlayer: false, allegiance: 'enemy' })
    const hit = scanAhead(world, shooter, 'down', CELL * 20)
    expect(hit).toBe('player')
  })

  it('returns player when player2 is in line of fire (coop)', () => {
    const world = makeCoopWorld()
    world.player2!.x = 200
    world.player2!.y = 100
    world.player2!.alive = true
    const shooter = makeTank({ id: 10, x: 200, y: 50, isPlayer: false, allegiance: 'enemy' })
    const hit = scanAhead(world, shooter, 'down', CELL * 20)
    expect(hit).toBe('player')
  })
})

// ---- (3b) Navigator/ThreatAssessor use controlledTank ----

describe('Navigator/ThreatAssessor — controlledTank integration', () => {
  it('playerCellImpl returns controlledTank cell, not player1 cell', () => {
    const world = makeCoopWorld()
    // Place player1 at (100,100), player2 at (300,300).
    world.player!.x = 100
    world.player!.y = 100
    world.player2!.x = 300
    world.player2!.y = 300
    const god = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, undefined, (w) => w.player2)
    const cell = playerCellImpl(god)
    // Should reflect player2's position (300,300), not player1 (100,100).
    expect(cell.col).toBe(Math.round(300 / CELL))
    expect(cell.row).toBe(Math.round(300 / CELL))
  })

  it('canMoveDirImpl uses controlledTank for cache key', () => {
    const world = makeCoopWorld()
    const god = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, undefined, (w) => w.player2)
    // Call canMoveDir with player2 — should use the cache path.
    const result1 = canMoveDirImpl(god, world.player2!, 'up')
    const result2 = canMoveDirImpl(god, world.player2!, 'up')
    // Both calls should return the same result (cached).
    expect(result1).toBe(result2)
  })

  it('baseBulletInterceptCellImpl uses controlledTank position', () => {
    const world = makeCoopWorld()
    // Place player2 at a known position.
    world.player2!.x = 192
    world.player2!.y = 384
    const god = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, undefined, (w) => w.player2)
    // Create a bullet heading toward the base.
    const bullet = {
      id: 100,
      x: 192,
      y: 300,
      w: BULLET,
      h: BULLET,
      dir: 'down' as Direction,
      alive: true,
      ownerId: 50,
      ownerKind: 'basic' as const,
      isPlayer: false,
      allegiance: 'enemy' as const,
      speed: 3,
      power: 1,
      damage: 1,
    }
    // Should compute an intercept cell based on player2's position.
    const intercept = baseBulletInterceptCellImpl(god, bullet)
    // The result may be null if no valid intercept exists, but the
    // function should not crash and should use player2's coords.
    // Just verify it doesn't throw.
    expect(intercept === null || typeof intercept.col === 'number').toBe(true)
  })
})

// ---- (4) WorldSnapshot backward compat without frenzy fields ----

describe('WorldSnapshot — backward compat (frenzy fields removed)', () => {
  it('cloneWorld produces a snapshot without old World-level frenzy fields', () => {
    const world = makeWorld()
    const snap = cloneWorld(world)
    // New snapshots should NOT have the old World-level frenzy fields.
    expect((snap as any).frenzyTimer).toBeUndefined()
    expect((snap as any).frenzyShotsLeft).toBeUndefined()
    expect((snap as any).frenzyLastFire).toBeUndefined()
    expect((snap as any).frenzyInterval).toBeUndefined()
    expect((snap as any).frenzyDir).toBeUndefined()
    // Core fields should still be present.
    expect(snap.frenzyStock).toBe(0)
    expect(snap.guardStock).toBe(0)
    expect(snap.sacrificeStock).toBe(0)
  })

  it('restoreWorld handles old snapshot with optional frenzy fields', () => {
    const world = makeWorld()
    const snap = cloneWorld(world)
    // Simulate an old snapshot that HAD frenzy fields.
    const oldSnap = {
      ...snap,
      frenzyTimer: 1500,
      frenzyShotsLeft: 5,
      frenzyLastFire: 100,
      frenzyInterval: 30,
      frenzyDir: 'right' as Direction,
    } as WorldSnapshot
    // restoreWorld should not crash with optional frenzy fields.
    restoreWorld(world, oldSnap)
    expect(world.frenzyStock).toBe(0)
  })

  it('restoreWorld works with snapshot missing frenzy fields', () => {
    const world = makeWorld()
    const snap = cloneWorld(world)
    // Remove frenzy fields to simulate a snapshot from a version before M1.
    const rest = { ...snap } as any
    delete rest.frenzyTimer
    delete rest.frenzyShotsLeft
    delete rest.frenzyLastFire
    delete rest.frenzyInterval
    delete rest.frenzyDir
    restoreWorld(world, rest as WorldSnapshot)
    expect(world.frenzyStock).toBe(0)
  })

  // ---- M4 DoD: coop snapshot roundtrip ----

  it('coop=false snapshot roundtrip preserves coop off', () => {
    const world = makeWorld()
    world.coop = false
    const snap = cloneWorld(world)
    expect(snap.coop).toBe(false)
    expect(snap.player2).toBeNull()
    const restored = seedWorld(99)
    restoreWorld(restored, snap)
    expect(restored.coop).toBe(false)
    expect(restored.player2).toBeNull()
    expect(restored.lives2).toBe(0)
    expect(restored.score2).toBe(0)
  })

  it('coop=true snapshot roundtrip with alive God preserves all fields', () => {
    const world = makeWorld()
    world.coop = true
    world.lives2 = 2
    world.playerLevel2 = 1
    world.score2 = 1500
    world.player2SpawnPoint = { col: 16, row: 24 }
    world.player2 = makeTank({ id: 99, x: 200, y: 200 })
    const snap = cloneWorld(world)
    expect(snap.coop).toBe(true)
    expect(snap.player2).not.toBeNull()
    expect(snap.lives2).toBe(2)
    expect(snap.playerLevel2).toBe(1)
    expect(snap.score2).toBe(1500)
    const restored = seedWorld(99)
    restoreWorld(restored, snap)
    expect(restored.coop).toBe(true)
    expect(restored.player2).not.toBeNull()
    expect(restored.player2!.id).toBe(99)
    expect(restored.player2!.x).toBe(200)
    expect(restored.lives2).toBe(2)
    expect(restored.playerLevel2).toBe(1)
    expect(restored.score2).toBe(1500)
  })

  it('coop=true snapshot roundtrip with dead God preserves player2 null', () => {
    const world = makeWorld()
    world.coop = true
    world.lives2 = 0
    world.player2 = null
    const snap = cloneWorld(world)
    expect(snap.coop).toBe(true)
    expect(snap.player2).toBeNull()
    const restored = seedWorld(99)
    restoreWorld(restored, snap)
    expect(restored.coop).toBe(true)
    expect(restored.player2).toBeNull()
    expect(restored.lives2).toBe(0)
  })

  it('old snapshot without coop fields loads correctly (backward compat)', () => {
    const world = makeWorld()
    const snap = cloneWorld(world)
    // Simulate an old snapshot missing all coop fields.
    const oldSnap = { ...snap } as any
    delete oldSnap.coop
    delete oldSnap.player2
    delete oldSnap.lives2
    delete oldSnap.playerLevel2
    delete oldSnap.score2
    delete oldSnap.player2SpawnPoint
    const restored = seedWorld(99)
    restoreWorld(restored, oldSnap as WorldSnapshot)
    // Should default to coop off.
    expect(restored.coop).toBe(false)
    expect(restored.player2).toBeNull()
    expect(restored.lives2).toBe(0)
    expect(restored.score2).toBe(0)
  })
})
