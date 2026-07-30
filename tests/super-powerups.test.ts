import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input, type InputLike } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import {
  GRID,
  FENCE_DURATION_FRAMES,
  POWERUP_DURATION_MS,
  BOAT_DURATION_MS,
} from '../src/constants'
import type { Direction } from '../src/constants'
import {
  SUPER_POWERUP_TYPES,
  SUPER_POWERUP_DROP_CHANCE,
  FRENZY_SHOTS,
  SACRIFICE_BASE_RADIUS_CELLS,
} from '../src/config/powerups'

/**
 * Super power-up system (强力道具, DECISIONS.md §31) — Phase 1 coverage:
 *   1. 10% super-item roll (uniform across all drop sources).
 *   2. Pickup accumulates into an inventory (not applied instantly).
 *   3. 同归于尽 (sacrifice) AoE on losing a life: radius + consumption + kill
 *      accounting + brick destruction.
 *   4. 狂暴宣泄 (frenzy) barrage: activation, exactly FRENZY_SHOTS shells,
 *      player lock (no move/turn/other items).
 *
 * Phase 2 (天降神兵 summon) is intentionally excluded from the pool here.
 */

const NORMAL_POWERUP_TYPES = [
  'star',
  'bomb',
  'shield',
  'freeze',
  'tank',
  'fence',
  'boat',
  'repair',
  'emp',
  'decoy',
  'mine',
] as const

/** Fresh, seeded World on stage 0 in 'playing' state. Defaults to 'classic'
 *  (used by the classic-coupled inventory/AoE tests), but callers that verify
 *  the MODERN super-drop feature pass 'hard' so they aren't affected by
 *  classic's faithful no-super / no-boat profile. */
function buildSeededWorld(
  seed: number,
  difficulty: string = 'classic',
): { world: World; sim: Simulation } {
  const world = new World()
  world.rng = new RNG(seed)
  const input = new Input()
  const sim = new Simulation(world, input)
  world.startGame(difficulty, 'modern', 0)
  return { world, sim }
}

describe('Super power-up — 10% super-item roll (DECISIONS.md §31)', () => {
  it('rollPowerUpType returns a SUPER type when rng < 10% chance', () => {
    const { world, sim } = buildSeededWorld(11, 'hard')
    // Force the super branch every call.
    world.rng.next = () => 0.01
    const t = (sim as unknown as { rollPowerUpType: () => string }).rollPowerUpType()
    expect(SUPER_POWERUP_TYPES as readonly string[]).toContain(t)
  })

  it('rollPowerUpType returns a NORMAL type when rng >= 10% chance', () => {
    const { world, sim } = buildSeededWorld(11, 'hard')
    world.rng.next = () => 0.5
    const t = (sim as unknown as { rollPowerUpType: () => string }).rollPowerUpType()
    expect(NORMAL_POWERUP_TYPES as readonly string[]).toContain(t)
  })

  it('super roll probability is ~10% over many deterministic samples', () => {
    const { sim } = buildSeededWorld(0x5eed, 'hard')
    let superCount = 0
    const N = 4000
    for (let i = 0; i < N; i++) {
      const t = (sim as unknown as { rollPowerUpType: () => string }).rollPowerUpType()
      if ((SUPER_POWERUP_TYPES as readonly string[]).includes(t)) superCount++
    }
    const frac = superCount / N
    // 10% ± tight tolerance — the decision is purely rng.next() < 0.1.
    expect(frac).toBeGreaterThan(SUPER_POWERUP_DROP_CHANCE - 0.03)
    expect(frac).toBeLessThan(SUPER_POWERUP_DROP_CHANCE + 0.03)
  })
})

describe('Super power-up — pickup accumulates into inventory (DECISIONS.md §31)', () => {
  it('applyPowerUp stores guard/frenzy/sacrifice into stocks, not instant effects', () => {
    const { world, sim } = buildSeededWorld(7)
    const apply = (sim as unknown as { applyPowerUp: (t: string) => void }).applyPowerUp.bind(sim)
    const g0 = world.guardStock
    const f0 = world.frenzyStock
    const s0 = world.sacrificeStock

    apply('guard')
    apply('frenzy')
    apply('sacrifice')

    expect(world.guardStock).toBe(g0 + 1)
    expect(world.frenzyStock).toBe(f0 + 1)
    expect(world.sacrificeStock).toBe(s0 + 1)
    // No gameplay side-effect: player stats / lives untouched by mere pickup.
    expect(world.lives).toBe(3)
    expect(world.player!.hp).toBe(world.player!.maxHp)
  })
})

describe('Super power-up — 同归于尽 (sacrifice) AoE (DECISIONS.md §31)', () => {
  /** Plant an enemy tank at an absolute position, spawned (not invulnerable). */
  function plantEnemy(world: World, x: number, y: number) {
    const e = world.createTank('basic', 0, 0, 'down')
    e.x = x
    e.y = y
    e.spawnTimer = 0
    if (e.aiState) {
      e.aiState.level = 'rookie'
      e.aiState.isCommander = false
    }
    world.tanks.push(e)
    return e
  }

  it('destroys enemies + brick walls within radius, consumes stock, scores kills', () => {
    const { world, sim } = buildSeededWorld(3)
    const p = world.player!
    p.x = 200
    p.y = 200 // center ≈ (216, 216)
    const trigger = (
      sim as unknown as { triggerSacrificeAoE: (pl: unknown) => void }
    ).triggerSacrificeAoE.bind(sim)

    // Enemy at the blast center → should die.
    const near = plantEnemy(world, p.x, p.y)
    // Brick well within 5-cell (80px) radius → should be destroyed.
    const bc = 10
    const br = 13
    world.tileMap.set(bc, br, 'brick')
    expect(world.tileMap.get(bc, br)).toBe('brick')
    // Brick far away → must survive.
    world.tileMap.set(0, 0, 'brick')
    expect(world.tileMap.get(0, 0)).toBe('brick')

    world.sacrificeStock = 1
    const scoreBefore = world.score
    trigger(p)

    expect(near.alive).toBe(false)
    expect(world.tileMap.get(bc, br)).not.toBe('brick') // destroyed
    expect(world.tileMap.get(0, 0)).toBe('brick') // untouched
    expect(world.sacrificeStock).toBe(0) // consumed
    expect(world.score).toBeGreaterThan(scoreBefore) // normal kill accounting
    expect(world.killCount).toBe(1)
  })

  it('radius scales: 5 cells at stock 1, +1 cell per extra stock', () => {
    const { world, sim } = buildSeededWorld(5)
    const p = world.player!
    p.x = 200
    p.y = 200
    const trigger = (
      sim as unknown as { triggerSacrificeAoE: (pl: unknown) => void }
    ).triggerSacrificeAoE.bind(sim)

    // Mid enemy at ~88px from center: inside 6 cells (96px) but outside 5 (80px).
    const mid = plantEnemy(world, p.x + 88, p.y)

    // Stock 1 → radius 80px → mid (88px) survives.
    world.sacrificeStock = 1
    trigger(p)
    expect(mid.alive).toBe(true)
    expect(world.sacrificeStock).toBe(0)

    // Reset for stock 2 scenario.
    mid.alive = true
    mid.spawnTimer = 0
    world.sacrificeStock = 2
    trigger(p)
    expect(mid.alive).toBe(false) // now within 96px radius
    expect(world.sacrificeStock).toBe(0)
    // Sanity check the documented base radius constant.
    expect(SACRIFICE_BASE_RADIUS_CELLS).toBe(5)
  })
})

describe('Super power-up — 狂暴宣泄 (frenzy) barrage (DECISIONS.md §31)', () => {
  it('activation consumes stock and fires exactly FRENZY_SHOTS player shells; player locked', () => {
    const { world, sim } = buildSeededWorld(9)
    const p = world.player!
    p.x = 100
    p.y = 100
    p.dir = 'right'
    p.spawnTimer = 0
    world.frenzyStock = 1

    const activate = (
      sim as unknown as { activateFrenzy: (pl: unknown) => void }
    ).activateFrenzy.bind(sim)
    activate(p)

    expect(world.frenzyStock).toBe(0)
    expect(p.frenzyTimer).toBeGreaterThan(0)
    expect(p.frenzyShotsLeft).toBe(FRENZY_SHOTS)
    expect(p.frenzyDir).toBe('right')

    // Count player bullets added during the barrage (wrapper on addBullet).
    let playerShells = 0
    const orig = world.addBullet.bind(world)
    world.addBullet = ((b: { isPlayer: boolean }) => {
      if (b.isPlayer) playerShells++
      return orig(b as never)
    }) as typeof world.addBullet

    const updatePlayerTank = (
      sim as unknown as { updatePlayerTank: (t: any, i: any) => void }
    ).updatePlayerTank.bind(sim)
    let guard = 0
    while ((p.frenzyShotsLeft ?? 0) > 0 && guard < 500) {
      world.frame++
      updatePlayerTank(world.player, sim.input)
      guard++
    }

    expect(playerShells).toBe(FRENZY_SHOTS)
    expect(p.frenzyTimer).toBe(0)
    expect(p.frenzyShotsLeft).toBe(0)
    // Player never moved during the barrage (locked to frenzyDir).
    expect(p.x).toBe(100)
    expect(p.y).toBe(100)
    expect(p.dir).toBe('right')
    expect(p.moving).toBe(false)
  })

  it('frenzy ignores movement input — player cannot turn/move during the barrage', () => {
    const { world } = buildSeededWorld(9)
    const p = world.player!
    p.x = 100
    p.y = 100
    p.dir = 'right'
    p.spawnTimer = 0
    world.frenzyStock = 1

    // Mock input that demands a LEFT move + fire every frame.
    const mockInput: InputLike = {
      getMoveDirection: (): Direction => 'left',
      isFiring: () => true,
      wasItemPressed: () => false,
      endFrame: () => {},
      reset: () => {},
    }
    const sim2 = new Simulation(world, mockInput)
    const activate = (
      sim2 as unknown as { activateFrenzy: (pl: unknown) => void }
    ).activateFrenzy.bind(sim2)
    const updatePlayerTank2 = (
      sim2 as unknown as { updatePlayerTank: (t: any, i: any) => void }
    ).updatePlayerTank.bind(sim2)

    activate(p)
    world.frame++
    updatePlayerTank2(world.player, mockInput)

    // Despite the input demanding LEFT + fire, the barrage forces 'right'
    // (frenzyDir) and suppresses normal firing/movement.
    expect(p.dir).toBe('right')
    expect(p.moving).toBe(false)
  })
})

describe('Power-up — boat only drops on water stages (DECISIONS.md §31 follow-up)', () => {
  /** Strip every water cell from the current stage and refresh the cache. */
  function clearWater(world: World) {
    for (let r = 0; r < GRID; r++) {
      for (let c = 0; c < GRID; c++) {
        if (world.tileMap.grid[r][c] === 'water') world.tileMap.grid[r][c] = 'empty'
      }
    }
    world.tileMap.rebuildBaseCache()
  }

  /** Inject a single water cell so the stage counts as having water. */
  function addWater(world: World) {
    world.tileMap.grid[5][5] = 'water'
    world.tileMap.rebuildBaseCache()
  }

  it('rollPowerUpType never returns boat on a no-water stage', () => {
    const { world, sim } = buildSeededWorld(123, 'hard')
    clearWater(world)
    expect(world.tileMap.hasWater()).toBe(false)
    const roll = (sim as unknown as { rollPowerUpType: () => string }).rollPowerUpType
    for (let i = 0; i < 500; i++) {
      expect(roll.call(sim)).not.toBe('boat')
    }
  })

  it('boat is only reachable when the stage has water', () => {
    const { world, sim } = buildSeededWorld(123, 'hard')
    const roll = (sim as unknown as { rollPowerUpType: () => string }).rollPowerUpType

    // With water: boat lives in the normal tier — it must be a reachable roll.
    addWater(world)
    expect(world.tileMap.hasWater()).toBe(true)
    let sawBoat = false
    for (let i = 0; i < 4000; i++) {
      if (roll.call(sim) === 'boat') {
        sawBoat = true
        break
      }
    }
    expect(sawBoat).toBe(true)

    // Without water: boat is filtered out of the normal tier — never reachable.
    clearWater(world)
    expect(world.tileMap.hasWater()).toBe(false)
    for (let i = 0; i < 500; i++) {
      expect(roll.call(sim)).not.toBe('boat')
    }
  })

  it('flushPendingDrops skips a deferred boat on a no-water stage', () => {
    const { world, sim } = buildSeededWorld(123)
    clearWater(world)
    world.pendingDrops = [{ type: 'boat', x: 128, y: 128 }]
    ;(sim as unknown as { flushPendingDrops: () => void }).flushPendingDrops()
    expect(world.powerUps.length).toBe(0)
  })

  it('flushPendingDrops releases a deferred boat on a water stage', () => {
    const { world, sim } = buildSeededWorld(123)
    addWater(world)
    world.pendingDrops = [{ type: 'boat', x: 128, y: 128 }]
    ;(sim as unknown as { flushPendingDrops: () => void }).flushPendingDrops()
    expect(world.powerUps.length).toBe(1)
    expect(world.powerUps[0].type).toBe('boat')
  })
})

describe('Super power-up — 栅栏 (fence) 20s steel ring reverts to brick (DECISIONS.md §31)', () => {
  /** Mirror of Simulation.baseRingPositions for assertions (base at 12,24). */
  function baseRingCells(): Array<{ col: number; row: number }> {
    const bc = 12
    const br = 24
    const cells: Array<{ col: number; row: number }> = []
    const consider = (c: number, r: number) => {
      if (c >= 0 && c < GRID && r >= 0 && r < GRID) cells.push({ col: c, row: r })
    }
    for (let c = bc - 1; c <= bc + 2; c++) consider(c, br - 1)
    consider(bc - 1, br)
    consider(bc - 1, br + 1)
    consider(bc + 2, br)
    consider(bc + 2, br + 1)
    return cells
  }

  it('places a steel ring around the base and arms a 20s (1200-frame) timer', () => {
    const { world, sim } = buildSeededWorld(5)
    const apply = (sim as unknown as { applyPowerUp: (t: string) => void }).applyPowerUp.bind(sim)
    for (const { col, row } of baseRingCells()) world.tileMap.set(col, row, 'empty')

    apply('fence')

    for (const { col, row } of baseRingCells()) {
      expect(world.tileMap.get(col, row)).toBe('steel')
    }
    expect(world.fenceExpireFrame).toBe(world.frame + FENCE_DURATION_FRAMES)
  })

  it('reverts the steel ring to brick when the timer expires', () => {
    const { world, sim } = buildSeededWorld(5)
    const apply = (sim as unknown as { applyPowerUp: (t: string) => void }).applyPowerUp.bind(sim)
    const updateFence = (sim as unknown as { updateFence: () => void }).updateFence.bind(sim)
    for (const { col, row } of baseRingCells()) world.tileMap.set(col, row, 'empty')

    apply('fence')
    expect(world.fenceExpireFrame).toBeDefined()

    // Advance to the expiry frame and tick the fence system.
    world.frame = world.fenceExpireFrame!
    updateFence()

    for (const { col, row } of baseRingCells()) {
      expect(world.tileMap.get(col, row)).toBe('brick')
    }
    expect(world.fenceExpireFrame).toBeUndefined()
  })

  it('does NOT revert before the timer elapses', () => {
    const { world, sim } = buildSeededWorld(5)
    const apply = (sim as unknown as { applyPowerUp: (t: string) => void }).applyPowerUp.bind(sim)
    const updateFence = (sim as unknown as { updateFence: () => void }).updateFence.bind(sim)
    for (const { col, row } of baseRingCells()) world.tileMap.set(col, row, 'empty')

    apply('fence')
    world.frame = (world.fenceExpireFrame ?? 0) - 1
    updateFence()

    for (const { col, row } of baseRingCells()) {
      expect(world.tileMap.get(col, row)).toBe('steel')
    }
    expect(world.fenceExpireFrame).toBeDefined()
  })
})

describe('Timed power-ups stack their duration when re-picked (DECISIONS.md §33)', () => {
  /**
   * Per the gameplay change, picking up a SECOND timed power-up while the
   * first is still active must ADD a full duration on top of the remaining
   * time — not reset to a fresh duration. Verifies the canonical example
   * (freeze: 3s left + 20s → 23s) and the same rule for shield/boat/fence.
   */
  const apply = (sim: Simulation, t: string) =>
    (sim as unknown as { applyPowerUp: (t: string) => void }).applyPowerUp.bind(sim)(t)

  it('freeze accumulates: 3s remaining + 20s → 23s', () => {
    const { world, sim } = buildSeededWorld(7)
    apply(sim, 'freeze')
    expect(world.freezeTimer).toBe(POWERUP_DURATION_MS) // fresh: full 20s
    world.freezeTimer = 3000 // simulate 3s remaining
    apply(sim, 'freeze')
    expect(world.freezeTimer).toBe(3000 + POWERUP_DURATION_MS) // 23000 ms = 23s
  })

  it('shield accumulates on the player tank', () => {
    const { world, sim } = buildSeededWorld(7)
    const p = world.player!
    // Clear the 3s spawn-protection shield so the first pickup starts clean.
    p.shieldTimer = 0
    apply(sim, 'shield')
    expect(p.shieldTimer).toBe(POWERUP_DURATION_MS)
    p.shieldTimer = 5000
    apply(sim, 'shield')
    expect(p.shieldTimer).toBe(5000 + POWERUP_DURATION_MS)
  })

  it('boat accumulates on the player tank', () => {
    const { world, sim } = buildSeededWorld(7)
    const p = world.player!
    apply(sim, 'boat')
    expect(p.boatTimer).toBe(BOAT_DURATION_MS)
    p.boatTimer = 4000
    apply(sim, 'boat')
    expect(p.boatTimer).toBe(4000 + BOAT_DURATION_MS)
  })

  it('fence accumulates its frame duration when already active', () => {
    const { world, sim } = buildSeededWorld(5)
    apply(sim, 'fence')
    const firstExpire = world.fenceExpireFrame!
    expect(firstExpire).toBe(world.frame + FENCE_DURATION_FRAMES)
    // Simulate the ring being partway through its life.
    world.frame += 300
    apply(sim, 'fence')
    // Remaining frames (firstExpire - frame) + a fresh FENCE_DURATION_FRAMES.
    expect(world.fenceExpireFrame).toBe(firstExpire + FENCE_DURATION_FRAMES)
  })

  it('re-pickup does NOT reset a full-duration buff to less', () => {
    const { world, sim } = buildSeededWorld(7)
    apply(sim, 'freeze')
    const full = world.freezeTimer
    apply(sim, 'freeze')
    expect(world.freezeTimer).toBe(full + POWERUP_DURATION_MS)
  })
})
