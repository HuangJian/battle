import { describe, it, expect } from 'bun:test'
import { World, genId } from '../src/game/World'
import { RNG } from '../src/utils/RNG'
import { GodAIInput } from '../src/ai/GodAIInput'
import {
  bulletLaneClearImpl,
  playerFasterThanImpl,
  findCloseEnemyImpl,
  safePerpDodgeImpl,
} from '../src/ai/god/ThreatAssessor'
import { CELL, BULLET, TANK, GRID } from '../src/constants'
import type { Bullet, Tank } from '../src/types'
import type { Direction } from '../src/constants'

// ================================================================
// §153 — hard S12 Lattice seed 3214953618 replay fixes (W1 + W2).
//
// W1 (~0:26, tick 1599): the player was oscillating at x≈24 (col 1) while an
//   enemy bullet ran straight DOWN column 0 (box x≈[13,19]) — the center-based
//   threat detector missed it (center already crossed / adjacent column), and
//   the player snapped its left edge from x=24 to x=16 INTO the bullet's lane
//   → hp 315→187. Expected: wait for the bullet to clear before moving.
// W2 (~0:45): close-combat sidestep trading damage. Expected: fire-rate-aware —
//   faster player → align & duel; slower player → dodge to safety.
// ================================================================

function makeWorld(): { world: World; input: GodAIInput } {
  const world = new World()
  world.rng = new RNG(42)
  const input = new GodAIInput(world)
  world.startGame('hard', 'modern', 0)
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) world.tileMap.grid[r][c] = 'empty'
  }
  for (const r of [24, 25]) {
    for (const c of [12, 13]) world.tileMap.grid[r][c] = 'base'
  }
  return { world, input }
}

function makeBullet(x: number, y: number, dir: Bullet['dir']): Bullet {
  return {
    id: genId(),
    x,
    y,
    w: BULLET,
    h: BULLET,
    dir,
    alive: true,
    ownerId: -1,
    ownerKind: 'basic',
    isPlayer: false,
    allegiance: 'enemy',
    speed: 4,
    power: 1,
    damage: 1,
  }
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

describe('§153-W1 — bulletLaneClear (wait for the bullet to clear)', () => {
  it('holds when the intended move would drive the body onto a passing bullet (the W1 geometry)', () => {
    // Repro geometry at hard S12 seed 3214953618 tick 1598: player body
    // x[23.6,55.6] y[144,176], dir=left, moveDir=up. The turn-snap puts the
    // off-axis coordinate on the 16px grid: snap(23.6) = 16, so the NEXT body
    // is x[16,48] y[142,174]. Enemy down-bullet box x[13,19] y[164,170]
    // (column 0, the player is in col 1) OVERLAPS that next body → the move
    // would clip it → must NOT clear (hold).
    const { world, input } = makeWorld()
    const p = makeTank({ x: 23.6, y: 144, w: TANK, h: TANK, isPlayer: true, allegiance: 'player' })
    world.bullets.push(makeBullet(13, 164, 'down'))
    expect(bulletLaneClearImpl(input, p, 'up')).toBe(false)
    // Exact prediction (margin 0) still holds — the overlap is real, not a
    // margin artifact.
    expect(bulletLaneClearImpl(input, p, 'up')).toBe(false)
  })

  it('is clear for a bullet PERPENDICULAR to the intended move (the §154 losing-seed family)', () => {
    // S9-5: player (56,128) dir=right, moveDir=left; down-bullet at (45,164.6)
    // below the body. Next body x[54,86] y[128,160] — the bullet (x[45,51],
    // y[164.6,170.6]) overlaps nothing → the old expanded-box check held
    // (freeze = §48 stationary death) but the move is safe → clear.
    // margin 1 = the measured knob value (8 re-catches near-miss perpendicular
    // bullets via the expansion tail — the §154 over-wait itself).
    const { world, input } = makeWorld()
    const p = makeTank({ x: 56, y: 128, w: TANK, h: TANK, isPlayer: true, allegiance: 'player' })
    world.bullets.push(makeBullet(45, 164.6, 'down'))
    expect(bulletLaneClearImpl(input, p, 'left')).toBe(true)
    // S9-15: player (64,169.1) moveDir=down; left-bullet above the head
    // (76.8,157) — it crosses the row above the next body → clear.
    const p2 = makeTank({ x: 64, y: 169.1, w: TANK, h: TANK, isPlayer: true, allegiance: 'player' })
    world.bullets.length = 0
    world.bullets.push(makeBullet(76.8, 157, 'left'))
    expect(bulletLaneClearImpl(input, p2, 'down')).toBe(true)
  })

  it('is clear for a same-axis bullet the move can no longer reach (the S12-1 over-wait family)', () => {
    // S12-1: player (23.9,128) moveDir=down; a down-bullet already BELOW the
    // predicted next body (bottom y=162) can never be hit by the move — the
    // old expanded-box version held ~7 extra ticks (freeze → loss), the
    // predictive check clears.
    const { world, input } = makeWorld()
    const p = makeTank({ x: 23.9, y: 128, w: TANK, h: TANK, isPlayer: true, allegiance: 'player' })
    world.bullets.push(makeBullet(13, 163, 'down'))
    expect(bulletLaneClearImpl(input, p, 'down')).toBe(true)
    // Same bullet still INSIDE the predicted next body band → hold.
    const p2 = makeTank({ x: 23.9, y: 128, w: TANK, h: TANK, isPlayer: true, allegiance: 'player' })
    world.bullets.length = 0
    world.bullets.push(makeBullet(13, 144.6, 'down'))
    expect(bulletLaneClearImpl(input, p2, 'down')).toBe(false)
  })

  it('is clear with no enemy bullets and when the bullet is far away', () => {
    const { world, input } = makeWorld()
    const p = makeTank({ x: 23.6, y: 144, w: TANK, h: TANK, isPlayer: true, allegiance: 'player' })
    expect(bulletLaneClearImpl(input, p, 'up')).toBe(true)
    world.bullets.push(makeBullet(300, 50, 'down'))
    expect(bulletLaneClearImpl(input, p, 'up')).toBe(true)
  })

  it('is clear when the only nearby bullet is a PLAYER bullet', () => {
    const { world, input } = makeWorld()
    const p = makeTank({ x: 23.6, y: 144, w: TANK, h: TANK, isPlayer: true, allegiance: 'player' })
    const b = makeBullet(13, 164, 'down')
    b.isPlayer = true
    b.allegiance = 'player'
    world.bullets.push(b)
    expect(bulletLaneClearImpl(input, p, 'up')).toBe(true)
  })

  it('is clear when there is no intended move (defensive)', () => {
    const { world, input } = makeWorld()
    const p = makeTank({ x: 23.6, y: 144, w: TANK, h: TANK, isPlayer: true, allegiance: 'player' })
    world.bullets.push(makeBullet(13, 164, 'down'))
    expect(bulletLaneClearImpl(input, p, null as unknown as Direction, 8)).toBe(true)
  })
})

describe('§153-W2 — fire-rate-aware close combat', () => {
  it('player fires faster than a slow enemy → duel is the better trade', () => {
    const player = makeTank({ nextFireInterval: 788, isPlayer: true, allegiance: 'player' })
    const armor = makeTank({ nextFireInterval: 963, isPlayer: false, allegiance: 'enemy' })
    expect(playerFasterThanImpl(player, armor)).toBe(true)
  })

  it('player fires SLOWER than the enemy → dodge instead of dueling', () => {
    const player = makeTank({ nextFireInterval: 788, isPlayer: true, allegiance: 'player' })
    const faster = makeTank({ nextFireInterval: 500, isPlayer: false, allegiance: 'enemy' })
    expect(playerFasterThanImpl(player, faster)).toBe(false)
  })

  it('findCloseEnemy finds the aligned close enemy in the danger direction', () => {
    const { world, input } = makeWorld()
    // Player center at (x=128, y=176) → col 8, row 10.
    const pcx = 8 * CELL + CELL / 2
    const pcy = 10 * CELL + CELL / 2
    // Enemy directly above in col 8 at row 8 (dist 2 cells < range 6).
    const enemy = makeTank({
      x: 8 * CELL + CELL / 2 - TANK / 2,
      y: 8 * CELL,
      isPlayer: false,
      allegiance: 'enemy',
      spawnTimer: 0,
    })
    world.tanks.push(enemy)
    const found = findCloseEnemyImpl(input, pcx, pcy, 'up', 6)
    expect(found).not.toBeNull()
    expect(found!.id).toBe(enemy.id)
  })

  it('findCloseEnemy ignores an enemy NOT aligned in the danger direction', () => {
    const { world, input } = makeWorld()
    const pcx = 8 * CELL + CELL / 2
    const pcy = 10 * CELL + CELL / 2
    // Enemy in same row but different column → NOT vertically aligned.
    const enemy = makeTank({
      x: 2 * CELL + CELL / 2,
      y: 2 * CELL,
      isPlayer: false,
      allegiance: 'enemy',
      spawnTimer: 0,
    })
    world.tanks.push(enemy)
    expect(findCloseEnemyImpl(input, pcx, pcy, 'up', 6)).toBeNull()
  })

  it('safePerpDodge returns a passable, bullet-safe perpendicular direction', () => {
    const { world, input } = makeWorld()
    const p = world.player!
    p.x = 8 * CELL + CELL / 2 - TANK / 2
    p.y = 10 * CELL + CELL / 2 - TANK / 2
    p.spawnTimer = 0
    p.shieldTimer = 0
    const pcx = p.x + TANK / 2
    const pcy = p.y + TANK / 2
    const dir: Direction | null = safePerpDodgeImpl(input, pcx, pcy, 'down')
    expect(dir === 'left' || dir === 'right').toBe(true)
    // Verify the returned direction is actually passable.
    if (dir) expect(input.canMoveDir(p, dir)).toBe(true)
  })
})
