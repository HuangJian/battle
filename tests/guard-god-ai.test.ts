import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { CELL } from '../src/constants'
import type { Direction } from '../src/constants'
import type { Tank } from '../src/types'
import type { GodAIInput } from '../src/ai/GodAIInput'

/**
 * 天降神兵 guard AI upgrade (DECISIONS.md §159):
 *   1. Guards are driven by the GOD AI (GodAIInput) — same decision pipeline
 *      as the God AI player — not the old simple "Commander-defend" policy.
 *   2. §159 避让 (yield): while the guard blocks the cell directly in front of
 *      a MOVING player, it must unconditionally get out of the way —
 *      perpendicular first (优先垂直让开), else turn to the player's direction
 *      and advance (escort); it keeps yielding until it no longer blocks, and
 *      keeps firing forward (the player's lane) to suppress enemies.
 *   3. Determinism: guard decisions are pure functions of World state (the
 *      guard profile zeros the RNG-driven imperfection gates), so identical
 *      seeds produce identical guard behavior.
 */

/** Fresh, seeded World on stage 0 in 'playing' state. */
function buildSeededWorld(seed: number): { world: World; sim: Simulation } {
  const world = new World()
  world.rng = new RNG(seed)
  const sim = new Simulation(world, new Input())
  world.startGame('classic', 'modern', 0)
  return { world, sim }
}

/** Plant an enemy tank (allegiance 'enemy', spawn-ready). */
function plantTank(world: World, kind: 'player' | 'basic', x: number, y: number): Tank {
  const t = world.createTank(kind, x, y, 'down')
  t.spawnTimer = 0
  if (t.aiState) {
    t.aiState.level = 'rookie'
    t.aiState.isCommander = false
  }
  return t
}

/** Plant an allied guard (third faction), spawn-ready, no expiry. */
function plantAlly(
  world: World,
  kind: 'basic' | 'fast' | 'power' | 'armor',
  x: number,
  y: number,
): Tank {
  const t = world.createTank(kind, x, y, 'up')
  t.allegiance = 'ally'
  t.isPlayer = false
  t.spawnTimer = 0
  if (t.aiState) {
    t.aiState.level = 'rookie'
    t.aiState.isCommander = false
  }
  return t
}

/** Overwrite a cell rectangle to 'empty' (clears stage-0 scenery for tests). */
function clearCells(world: World, c0: number, r0: number, c1: number, r1: number): void {
  for (let r = r0; r <= r1; r++) {
    for (let c = c0; c <= c1; c++) {
      world.tileMap.grid[r][c] = 'empty'
    }
  }
}

const updateGuards = (sim: Simulation): (() => void) =>
  (sim as unknown as { updateGuards: () => void }).updateGuards.bind(sim)

/** Bullets owned by one tank. */
function bulletsOf(world: World, ownerId: number) {
  return world.bullets.filter((b) => b.ownerId === ownerId)
}

describe('天降神兵 — guard uses the GOD AI (DECISIONS.md §159)', () => {
  it('turns toward and fires at an aligned enemy like the God AI player', () => {
    const { world, sim } = buildSeededWorld(71)
    const p = world.player!
    p.x = 100
    p.y = 300
    p.spawnTimer = 0
    p.moving = false // parked — no yield interference
    world.tanks = []
    world.allies = []

    // Guard NOT aligned with the enemy's axis (facing left) — the God AI must
    // turn it 'up' to stop-and-aim (T2a), exactly like it would the player.
    // (Widen with `as Direction` so TS property-narrowing doesn't pin g.dir
    // to 'left' across the updateGuards() call below.)
    const g = plantAlly(world, 'basic', 100, 150)
    g.dir = 'left' as Direction
    g.prevMoveDir = 'left' as Direction
    g.lastFire = -99999 // skip the engine fire-rate gate for this assertion
    world.allies.push(g)

    // Enemy above, vertically aligned, on a cleared lane.
    clearCells(world, 4, 2, 8, 10)
    const enemy = plantTank(world, 'basic', 100, 48)
    enemy.hp = 1
    world.tanks.push(enemy)

    updateGuards(sim)()

    // The God AI turned the guard to face the enemy and fired an ally bullet.
    expect(g.dir).toBe('up')
    expect(g.moving).toBe(true)
    const bullets = bulletsOf(world, g.id)
    expect(bullets.length).toBeGreaterThan(0)
    expect(bullets[0].dir).toBe('up')
    expect(bullets[0].allegiance).toBe('ally') // never fires on its own team
  })
})

describe('天降神兵 — §159 避让 yield (unblock the player lane)', () => {
  function parkedBelow(world: World): { p: Tank; g: Tank } {
    const p = world.player!
    p.x = 192
    p.y = 192
    p.spawnTimer = 0
    p.dir = 'up' as Direction
    world.tanks = []
    world.allies = []
    const g = plantAlly(world, 'basic', 192, 160) // directly in the forward cell
    g.dir = 'up'
    g.prevMoveDir = 'up'
    g.lastFire = -99999
    world.allies.push(g)
    return { p, g }
  }

  it('steps perpendicular while the player moves up, and keeps firing up the lane', () => {
    const { world, sim } = buildSeededWorld(72)
    world.rules = { ...world.rules, turnCooldownMs: 0 } // isolate the yield decision
    const { p, g } = parkedBelow(world)
    p.moving = true
    // Open ground around the guard; an enemy up the lane gives it something
    // to suppress (shouldFireInDir only fires when a target/wall is ahead).
    clearCells(world, 9, 2, 16, 14)
    const enemy = plantTank(world, 'basic', 192, 48)
    enemy.hp = 1
    world.tanks.push(enemy)

    updateGuards(sim)()

    // Unconditional yield: perpendicular step (优先垂直让开).
    expect(g.dir === 'left' || g.dir === 'right').toBe(true)
    expect(g.moving).toBe(true)
    // §160 enemy-first: the flank has only stage-0 brick (no enemy), so the
    // lane enemy wins — it keeps firing up the player's lane (向前方开火压制).
    const bullets = bulletsOf(world, g.id)
    expect(bullets.length).toBeGreaterThan(0)
    expect(bullets[0].dir).toBe('up')
  })

  it('escorts — turns to the player direction — when both perpendiculars are walled', () => {
    const { world, sim } = buildSeededWorld(73)
    world.rules = { ...world.rules, turnCooldownMs: 0 }
    const { p, g } = parkedBelow(world)
    p.moving = true
    clearCells(world, 9, 2, 16, 14)
    // Wall the two perpendicular steps: the guard spans cols 12-13 rows 10-11,
    // so brick cols 11 and 14 at those rows block both sideways moves.
    for (const c of [11, 14]) {
      for (const r of [10, 11]) world.tileMap.grid[r][c] = 'brick'
    }
    const enemy = plantTank(world, 'basic', 192, 48)
    enemy.hp = 1
    world.tanks.push(enemy)

    updateGuards(sim)()

    // 无条件转为与 player 同方向并前进 — the corridor-escort fallback.
    expect(g.dir).toBe('up')
    expect(g.moving).toBe(true)
    const bullets = bulletsOf(world, g.id)
    expect(bullets.length).toBeGreaterThan(0)
    expect(bullets[0].dir).toBe('up')
  })

  it('does NOT yield for a stationary player — the God AI keeps full control', () => {
    const { world, sim } = buildSeededWorld(74)
    world.rules = { ...world.rules, turnCooldownMs: 0 }
    const { p, g } = parkedBelow(world)
    p.moving = false // parked, just facing the guard
    clearCells(world, 9, 2, 16, 14)
    // Enemy above: the God AI engages it. A yield would have stepped sideways
    // (both sides are open here); the God AI instead stands, aims and fires
    // (T2a: already facing 'up' → _moveDir null → moving stays false). The
    // yield ALWAYS sets moving=true, so moving===false proves no yield ran.
    const enemy = plantTank(world, 'basic', 192, 48)
    enemy.hp = 1
    world.tanks.push(enemy)

    updateGuards(sim)()

    expect(g.dir).toBe('up') // God AI T2a, not the yield's perpendicular step
    expect(g.moving).toBe(false)
    expect(bulletsOf(world, g.id).length).toBeGreaterThan(0)
  })

  it('resumes autonomous play once the guard no longer blocks the lane', () => {
    const { world, sim } = buildSeededWorld(76)
    world.rules = { ...world.rules, turnCooldownMs: 0 }
    const { p, g } = parkedBelow(world)
    p.moving = true
    clearCells(world, 9, 2, 16, 14)
    const enemy = plantTank(world, 'basic', 192, 48)
    enemy.hp = 1
    world.tanks.push(enemy)

    updateGuards(sim)()
    const yieldDir = g.dir
    expect(yieldDir === 'left' || yieldDir === 'right').toBe(true)

    // The guard steps one cell sideways out of the lane…
    g.x += (yieldDir === 'left' ? -1 : 1) * CELL
    g.y += 0
    // …and the player parks. With the lane clear, autonomy resumes: the God
    // AI turns back toward the enemy (up) instead of continuing to step away.
    p.moving = false
    updateGuards(sim)()

    expect(g.dir).toBe('up')
  })
})

describe('天降神兵 — §160 避让中扫射压制 (sweep-axis fire first)', () => {
  // Player at (192,192) moving up; guard at (192,160) blocks the forward cell.
  // Walling the guard's right column (col 14, rows 10-11) forces the
  // perpendicular step LEFT — a deterministic sweep axis for the assertions.
  function yieldSteppingLeft(world: World): { p: Tank; g: Tank } {
    const p = world.player!
    p.x = 192
    p.y = 192
    p.spawnTimer = 0
    p.dir = 'up' as Direction
    p.moving = true
    world.tanks = []
    world.allies = []
    const g = plantAlly(world, 'basic', 192, 160) // directly in the forward cell
    g.dir = 'up'
    g.prevMoveDir = 'up'
    g.lastFire = -99999
    world.allies.push(g)
    // Wide open arena around the guard's rows: a clean flank matters — stage-0
    // brick to the left would be legitimate wall-break fire (allowWallFire),
    // defeating the "empty sweep axis" setup.
    clearCells(world, 0, 2, 17, 14)
    for (const r of [10, 11]) world.tileMap.grid[r][14] = 'brick'
    return { p, g }
  }

  it('fires along the sweep axis (left) even when the forward lane also has an enemy', () => {
    const { world, sim } = buildSeededWorld(78)
    world.rules = { ...world.rules, turnCooldownMs: 0 }
    const { g } = yieldSteppingLeft(world)
    // Two targets: one LEFT (the flank the guard is crossing) and one UP the
    // player's lane. §160 says the sweep axis wins — the bullet must go left,
    // and the barrel must match the movement direction.
    const flank = plantTank(world, 'basic', 80, 160)
    flank.hp = 1
    const lane = plantTank(world, 'basic', 192, 48)
    lane.hp = 1
    world.tanks.push(flank, lane)

    updateGuards(sim)()

    expect(g.dir).toBe('left')
    expect(g.moving).toBe(true)
    const bullets = bulletsOf(world, g.id)
    expect(bullets.length).toBeGreaterThan(0)
    expect(bullets[0].dir).toBe('left')
  })

  it('falls back to the forward lane when the sweep axis has no enemy', () => {
    const { world, sim } = buildSeededWorld(79)
    world.rules = { ...world.rules, turnCooldownMs: 0 }
    const { g } = yieldSteppingLeft(world)
    // Only a lane target (up); the flank is empty. The guard must still fire
    // up the player's lane (原 §159 向前方压制) while stepping left.
    const lane = plantTank(world, 'basic', 192, 48)
    lane.hp = 1
    world.tanks.push(lane)

    updateGuards(sim)()

    const bullets = bulletsOf(world, g.id)
    expect(bullets.length).toBeGreaterThan(0)
    expect(bullets[0].dir).toBe('up')
    // Movement still yields sideways — the fire axis never changes the step.
    expect(g.dir).toBe('left')
    expect(g.moving).toBe(true)
  })

  it('does NOT let a flank brick outrank a live enemy in the lane', () => {
    const { world, sim } = buildSeededWorld(80)
    world.rules = { ...world.rules, turnCooldownMs: 0 }
    const { g } = yieldSteppingLeft(world)
    // Brick on the flank — shouldFireInDir would treat it as a wall-fire
    // target (allowWallFire) — but NO enemy there. The lane holds a live
    // enemy: firing at the brick would 偏离目标, so the lane must win.
    world.tileMap.grid[10][6] = 'brick'
    world.tileMap.grid[11][6] = 'brick'
    const lane = plantTank(world, 'basic', 192, 48)
    lane.hp = 1
    world.tanks.push(lane)

    updateGuards(sim)()

    const bullets = bulletsOf(world, g.id)
    expect(bullets.length).toBeGreaterThan(0)
    expect(bullets[0].dir).toBe('up')
    expect(g.dir).toBe('left')
    expect(g.moving).toBe(true)
  })
})

describe('天降神兵 — guard God AI determinism (DECISIONS.md §159)', () => {
  it('guard brains run on GUARD_GOD_AI_PARAMS (pickups disabled, no RNG gates)', () => {
    const { world, sim } = buildSeededWorld(77)
    const p = world.player!
    p.spawnTimer = 0
    p.moving = false
    world.tanks = []
    world.allies = []
    const g = plantAlly(world, 'basic', 100, 150)
    world.allies.push(g)

    updateGuards(sim)()

    const brains = (sim as unknown as { guardAIById: Map<number, GodAIInput> }).guardAIById
    const brain = brains.get(g.id)
    expect(brain).toBeDefined()
    // Power-up branches disabled — an ally can never collect items.
    expect(brain!.params.pickupPriorityMode).toBe(0)
    expect(brain!.params.closePickupRange).toBe(0)
    expect(brain!.params.freezePickupRange).toBe(0)
    expect(brain!.params.powerupMaxDivertDistance).toBe(0)
    // Imperfection gates zeroed — decisions independent of the RNG seed.
    expect(brain!.params.aimError).toBe(0)
    expect(brain!.params.suboptimalPathProb).toBe(0)
  })

  it('produces identical guard behavior across identical seeds', () => {
    const run = (): { gx: number; gy: number; alive: boolean; frame: number; bullets: number } => {
      const { world, sim } = buildSeededWorld(75)
      const p = world.player!
      p.spawnTimer = 0
      p.moving = false
      world.tanks = []
      world.allies = []
      clearCells(world, 4, 2, 8, 10)
      const g = plantAlly(world, 'basic', 100, 150)
      g.lastFire = -99999
      world.allies.push(g)
      const enemy = plantTank(world, 'basic', 100, 48)
      enemy.hp = 1
      world.tanks.push(enemy)

      for (let i = 0; i < 200; i++) sim.tick()
      return { gx: g.x, gy: g.y, alive: g.alive, frame: world.frame, bullets: world.bullets.length }
    }
    expect(run()).toEqual(run())
  })
})
