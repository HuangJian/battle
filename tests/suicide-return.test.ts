import { describe, it, expect } from 'bun:test'
import { World, genId } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import {
  canShootEnemyFrom,
  spawnCanHitEnemyImpl,
  bulletWouldKillPlayer,
  controlledLives,
  findSuicideTargetImpl,
} from '../src/ai/god/SuicideReturn'
import { CELL, BULLET, GRID } from '../src/constants'
import type { Bullet, Tank } from '../src/types'

/**
 * §116 自杀秒回 (suicide quick-return) — unit tests.
 *
 * Tests the pure helpers (canShootEnemyFrom, spawnCanHitEnemyImpl,
 * bulletWouldKillPlayer, controlledLives, findSuicideTargetImpl) and the
 * SUICIDE_RETURN candidate's end-to-end behavior (all 5 preconditions met →
 * embrace death; any missing condition → fall through to the normal chain).
 *
 * Geometry: default spawn {col 8, row 24}, base at cols 12-13 / rows 24-25.
 * Terrain is cleared (all 'empty') except the base cells.
 */

function setupWorld(params: Partial<typeof DEFAULT_GOD_AI_PARAMS> = {}): {
  world: World
  input: GodAIInput
} {
  const world = new World()
  world.rng = new RNG(42)
  // Explicit clone (NOT the DEFAULT singleton) — mutating input.params must
  // not leak into DEFAULT_GOD_AI_PARAMS (DECISIONS §98).
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS, suicideReturnMode: 1, ...params })
  const sim = new Simulation(world, new Input())
  world.startGame('classic', 'modern', 0)
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) world.tileMap.grid[r][c] = 'empty'
  }
  for (const r of [24, 25]) {
    for (const c of [12, 13]) world.tileMap.grid[r][c] = 'base'
  }
  void sim
  input.reset()
  return { world, input }
}

function makeBullet(x: number, y: number, dir: Bullet['dir'], damage = 100, speed = 4): Bullet {
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
    speed,
    power: 1,
    damage,
  }
}

function placeEnemy(world: World, col: number, row: number): Tank {
  const enemy = world.createTank('basic', col * CELL, row * CELL, 'down')
  enemy.alive = true
  enemy.spawnTimer = 0
  world.tanks.push(enemy)
  return enemy
}

function positionPlayer(world: World, x: number, y: number): void {
  const p = world.player!
  p.x = x
  p.y = y
  p.hp = 100
  p.spawnTimer = 0
  p.shieldTimer = 0
  p.level = 0
  world.playerLevel = 0
}

// ============================================================
// canShootEnemyFrom — aligned + clear line of fire
// ============================================================

describe('canShootEnemyFrom', () => {
  it('same column, clear vertical line → true', () => {
    const { input } = setupWorld()
    expect(canShootEnemyFrom(input, 12, 2, 12, 10)).toBe(true)
  })

  it('same row, clear horizontal line → true', () => {
    const { input } = setupWorld()
    expect(canShootEnemyFrom(input, 4, 10, 10, 10)).toBe(true)
  })

  it('brick in the line blocks the shot', () => {
    const { world, input } = setupWorld()
    world.tileMap.grid[6][12] = 'brick'
    expect(canShootEnemyFrom(input, 12, 2, 12, 10)).toBe(false)
  })

  it('steel in the line blocks the shot', () => {
    const { world, input } = setupWorld()
    world.tileMap.grid[6][8] = 'steel'
    expect(canShootEnemyFrom(input, 4, 6, 12, 6)).toBe(false)
  })

  it('base in the line blocks the shot', () => {
    const { input } = setupWorld()
    expect(canShootEnemyFrom(input, 8, 24, 16, 24)).toBe(false)
  })

  it('not aligned (different row and column) → false', () => {
    const { input } = setupWorld()
    expect(canShootEnemyFrom(input, 4, 4, 12, 10)).toBe(false)
  })

  it('water does NOT block bullets', () => {
    const { world, input } = setupWorld()
    world.tileMap.grid[6][12] = 'water'
    expect(canShootEnemyFrom(input, 12, 2, 12, 10)).toBe(true)
  })
})

// ============================================================
// spawnCanHitEnemyImpl — immediate or one-turn firing position
// ============================================================

describe('spawnCanHitEnemyImpl', () => {
  it('immediate: spawn aligned (same column) → true', () => {
    const { input } = setupWorld()
    expect(spawnCanHitEnemyImpl(input, 8, 2)).toBe(true)
  })

  it('one turn: move vertically then fire horizontally → true', () => {
    const { input } = setupWorld()
    // Spawn (8, 24), enemy (12, 2). Move up to (8, 2), then fire right.
    expect(spawnCanHitEnemyImpl(input, 12, 2)).toBe(true)
  })

  it('one turn: move horizontally then fire vertically → true', () => {
    const { input } = setupWorld()
    // Spawn (8, 24), enemy (4, 10). Move left to (4, 24), then fire up.
    expect(spawnCanHitEnemyImpl(input, 4, 10)).toBe(true)
  })

  it('steel in the movement path blocks one-turn reach', () => {
    const { world, input } = setupWorld()
    world.tileMap.grid[15][8] = 'steel'
    world.tileMap.grid[15][9] = 'steel'
    expect(spawnCanHitEnemyImpl(input, 12, 2)).toBe(false)
  })

  it('steel at the firing position blocks one-turn reach', () => {
    const { world, input } = setupWorld()
    world.tileMap.grid[2][8] = 'steel'
    world.tileMap.grid[2][9] = 'steel'
    expect(spawnCanHitEnemyImpl(input, 12, 2)).toBe(false)
  })

  it('out of bounds enemy → false', () => {
    const { input } = setupWorld()
    expect(spawnCanHitEnemyImpl(input, 25, 2)).toBe(false)
  })
})

// ============================================================
// bulletWouldKillPlayer — lethal check with star shield
// ============================================================

describe('bulletWouldKillPlayer', () => {
  it('damage >= hp and below max level → lethal', () => {
    const p = { isPlayer: true, level: 0, hp: 100 } as Tank
    const b = { damage: 100 } as Bullet
    expect(bulletWouldKillPlayer(p, b)).toBe(true)
  })

  it('damage < hp → not lethal', () => {
    const p = { isPlayer: true, level: 0, hp: 250 } as Tank
    const b = { damage: 100 } as Bullet
    expect(bulletWouldKillPlayer(p, b)).toBe(false)
  })

  it('max level (3★) → star shield saves, not lethal', () => {
    const p = { isPlayer: true, level: 3, hp: 100 } as Tank
    const b = { damage: 100 } as Bullet
    expect(bulletWouldKillPlayer(p, b)).toBe(false)
  })
})

// ============================================================
// controlledLives — P1 vs P2
// ============================================================

describe('controlledLives', () => {
  it('single-player (controls P1) → world.lives', () => {
    const { world, input } = setupWorld()
    world.lives = 3
    expect(controlledLives(input)).toBe(3)
  })

  it('coop (controls P2) → world.lives2', () => {
    const world = new World()
    world.rng = new RNG(42)
    world.coop = true
    const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS }, undefined, (w) => w.player2)
    world.startGame('classic', 'modern', 0)
    // startGame resets lives2 to 0; set it AFTER startGame.
    world.lives2 = 5
    world.lives = 1
    input.reset()
    expect(controlledLives(input)).toBe(5)
  })
})

// ============================================================
// findSuicideTargetImpl — threat-point + spawn can hit + player far
// ============================================================

describe('findSuicideTargetImpl', () => {
  it('enemy at threat point, spawn can hit, player far → returns enemy', () => {
    const { world, input } = setupWorld({ suicideReturnEnemyDistTicks: 10 })
    positionPlayer(world, 0, 24 * CELL)
    placeEnemy(world, 12, 2)
    const pcx = world.player!.x + world.player!.w / 2
    const pcy = world.player!.y + world.player!.h / 2
    const target = findSuicideTargetImpl(input, pcx, pcy)
    expect(target).not.toBeNull()
    expect(target!.kind).toBe('basic')
  })

  it('enemy not at a threat point → null', () => {
    const { world, input } = setupWorld({ suicideReturnEnemyDistTicks: 10 })
    positionPlayer(world, 0, 24 * CELL)
    placeEnemy(world, 4, 4)
    const pcx = world.player!.x + world.player!.w / 2
    const pcy = world.player!.y + world.player!.h / 2
    expect(findSuicideTargetImpl(input, pcx, pcy)).toBeNull()
  })

  it('player close enough to reach enemy in time → null', () => {
    const { world, input } = setupWorld({ suicideReturnEnemyDistTicks: 1000 })
    positionPlayer(world, 10 * CELL, 2 * CELL)
    placeEnemy(world, 12, 2)
    const pcx = world.player!.x + world.player!.w / 2
    const pcy = world.player!.y + world.player!.h / 2
    expect(findSuicideTargetImpl(input, pcx, pcy)).toBeNull()
  })
})

// ============================================================
// SUICIDE_RETURN candidate — end-to-end via think()
// ============================================================

describe('SUICIDE_RETURN candidate (end-to-end)', () => {
  function fullSetup(): { world: World; input: GodAIInput } {
    const { world, input } = setupWorld({ suicideReturnEnemyDistTicks: 10 })
    positionPlayer(world, 0, 24 * CELL) // player far from the threat enemy
    world.lives = 3
    placeEnemy(world, 12, 2) // threat enemy the spawn can hit (one turn)
    // Lethal bullet approaching the player within 1s. Player center (16,400),
    // bullet at (0,160) dir down speed 4: distance 237px → 59 ticks < 60 (1s).
    world.bullets.length = 0
    world.bullets.push(makeBullet(0, 10 * CELL, 'down', 100, 4)) // player-lethal
    // Base-threat bullet heading to the base (center ~208,400) so the candidate's
    // base-under-active-threat GATE passes: at (192,160) dir down, its trajectory
    // crosses the base in column 12 (terrain is cleared + base cells).
    world.bullets.push(makeBullet(12 * CELL, 10 * CELL, 'down', 100, 4))
    return { world, input }
  }

  it('all 5 conditions met → embraces death (no move, no fire)', () => {
    const { input } = fullSetup()
    input.getMoveDirection()
    expect(input._moveDir).toBeNull()
    expect(input._fire).toBe(false)
    expect(input._lastBranch).toBe('suicideReturn')
    expect(input.branchCounts.suicideReturn).toBe(1)
  })

  it('OFF (suicideReturnMode=0) → candidate never commits', () => {
    const { input } = fullSetup()
    input.params.suicideReturnMode = 0
    input.getMoveDirection()
    expect(input._lastBranch).not.toBe('suicideReturn')
    expect(input.branchCounts.suicideReturn).toBe(0)
  })

  it('no bullet threat → falls through to normal chain', () => {
    const { world, input } = fullSetup()
    world.bullets.length = 0
    input.getMoveDirection()
    expect(input._lastBranch).not.toBe('suicideReturn')
  })

  it('lives < minLives (last life) → does not suicide', () => {
    const { world, input } = fullSetup()
    world.lives = 1
    input.getMoveDirection()
    expect(input._lastBranch).not.toBe('suicideReturn')
  })

  it('shielded (respawn shield) → does not suicide', () => {
    const { world, input } = fullSetup()
    world.player!.shieldTimer = 3000
    input.getMoveDirection()
    expect(input._lastBranch).not.toBe('suicideReturn')
  })

  it('bullet not lethal (3★ star shield) → does not suicide', () => {
    const { world, input } = fullSetup()
    world.player!.level = 3
    world.playerLevel = 3
    input.getMoveDirection()
    expect(input._lastBranch).not.toBe('suicideReturn')
  })

  it('bullet too far (>1s) → does not suicide', () => {
    const { world, input } = fullSetup()
    // Slow bullet (speed 1): 237px / 1 = 237 ticks > 60 (1s).
    world.bullets.length = 0
    world.bullets.push(makeBullet(0, 10 * CELL, 'down', 100, 1))
    input.getMoveDirection()
    expect(input._lastBranch).not.toBe('suicideReturn')
  })

  it('no threat-point enemy → does not suicide', () => {
    const { world, input } = fullSetup()
    world.tanks.length = 0
    placeEnemy(world, 4, 4) // not a threat point
    input.getMoveDirection()
    expect(input._lastBranch).not.toBe('suicideReturn')
  })

  it('player can reach enemy in time → does not suicide', () => {
    const { world, input } = fullSetup()
    positionPlayer(world, 10 * CELL, 2 * CELL) // next to the enemy
    world.bullets.length = 0
    world.bullets.push(makeBullet(10 * CELL, 0, 'down', 100, 4))
    input.params.suicideReturnEnemyDistTicks = 1000
    input.getMoveDirection()
    expect(input._lastBranch).not.toBe('suicideReturn')
  })
})

// ============================================================
// §117 condition-① variants — modes 2 (STAND) / 3 (CHARGE)
// ============================================================
// Same scene as fullSetup() but WITHOUT the player-lethal bullet — the whole
// point of §117 is that the trade triggers on an enemy at a threat point
// (condition ①) while a bullet flies at the base, not on the player being
// about to die (condition ⑤).

describe('SUICIDE_RETURN §117 condition-① variants (modes 2/3)', () => {
  function fullSetupV2(mode: number): { world: World; input: GodAIInput } {
    const { world, input } = setupWorld({
      suicideReturnEnemyDistTicks: 10,
      suicideReturnMode: mode,
    })
    positionPlayer(world, 0, 24 * CELL) // player far from the threat enemy
    world.lives = 3
    placeEnemy(world, 12, 2) // threat enemy the spawn can hit (one turn)
    // Base-threat bullet only — NO player-lethal bullet (the §117 trigger).
    world.bullets.length = 0
    world.bullets.push(makeBullet(12 * CELL, 10 * CELL, 'down', 100, 4))
    return { world, input }
  }

  it('mode 2: commits with only a base-bullet threat (no lethal bullet) → stands still', () => {
    const { input } = fullSetupV2(2)
    input.getMoveDirection()
    expect(input._lastBranch).toBe('suicideReturn')
    expect(input._moveDir).toBeNull()
    expect(input._fire).toBe(false)
    expect(input.branchCounts.suicideReturn).toBe(1)
  })

  it('mode 3: commits with only a base-bullet threat → charges the threat enemy', () => {
    const { input } = fullSetupV2(3)
    input.getMoveDirection()
    expect(input._lastBranch).toBe('suicideReturn')
    expect(input._moveDir).not.toBeNull() // actively moving toward the enemy
  })

  it('mode 1: still refuses without a lethal bullet (control — §116 unchanged)', () => {
    const { input } = fullSetupV2(1)
    input.getMoveDirection()
    expect(input._lastBranch).not.toBe('suicideReturn')
  })

  it('mode 2: trade continues on the next tick while the threat persists', () => {
    const { input } = fullSetupV2(2)
    input.getMoveDirection()
    input.endFrame()
    input.getMoveDirection()
    expect(input._lastBranch).toBe('suicideReturn')
    expect(input._moveDir).toBeNull()
  })

  it('mode 2: standing times out → resumes normal play (no freeze)', () => {
    const { input } = fullSetupV2(2)
    input.params.suicideReturnStandMaxTicks = 3
    input.getMoveDirection() // commit
    for (let i = 0; i < 5; i++) {
      input.endFrame()
      input.getMoveDirection()
    }
    // standTicks exceeded 3 → cleared → normal chain took over.
    expect(input._suicideStanding).toBe(false)
    expect(input._lastBranch).not.toBe('suicideReturn')
  })

  it('mode 2: standing clears when the base bullet threat is gone', () => {
    const { world, input } = fullSetupV2(2)
    input.getMoveDirection() // commit
    world.bullets.length = 0 // the base-bound bullet is cancelled/intercepted
    input.endFrame()
    input.getMoveDirection()
    expect(input._suicideStanding).toBe(false)
    expect(input._lastBranch).not.toBe('suicideReturn')
  })

  it('mode 2: standing clears when the player is shielded (post-respawn)', () => {
    const { world, input } = fullSetupV2(2)
    input.getMoveDirection() // commit
    world.player!.shieldTimer = 3000 // respawned behind the spawn shield
    input.endFrame()
    input.getMoveDirection()
    expect(input._suicideStanding).toBe(false)
    expect(input._lastBranch).not.toBe('suicideReturn')
  })

  it('mode 3: trade continues while the enemy is still at the threat point', () => {
    const { input } = fullSetupV2(3)
    input.getMoveDirection() // commit (charges)
    input.endFrame()
    input.getMoveDirection()
    expect(input._lastBranch).toBe('suicideReturn')
  })

  it('mode 2: still refuses without a threat-point enemy', () => {
    const { world, input } = fullSetupV2(2)
    world.tanks.length = 0
    placeEnemy(world, 4, 4) // not a threat point
    input.getMoveDirection()
    expect(input._lastBranch).not.toBe('suicideReturn')
  })

  it('mode 3: still refuses on the last life (lives < minLives)', () => {
    const { world, input } = fullSetupV2(3)
    world.lives = 1
    input.getMoveDirection()
    expect(input._lastBranch).not.toBe('suicideReturn')
  })
})

// ============================================================
// §118 strict-doom guard — baseHp threshold + defense-lost distance
// ============================================================
// The §117 flip-loss root cause: the trade committed while the base was at
// full HP with the normal defense still running — one in-flight bullet is NOT
// proof the base will fall. These tests assert the two new guards (both must
// be > 0 to activate; default 0 ⇒ byte-identical to §117). The scene is the
// mode-2/3 one (threat-point enemy + base-bound bullet), plus a pool-model
// base HP buffer (the default classic startGame gives baseMaxHp = 1, which
// would make a fractional threshold unsatisfiable).

describe('SUICIDE_RETURN §118 strict-doom guard (modes 2/3)', () => {
  function strictSetup(
    mode: number,
    params: Partial<typeof DEFAULT_GOD_AI_PARAMS> = {},
  ): {
    world: World
    input: GodAIInput
  } {
    const { world, input } = setupWorld({
      suicideReturnEnemyDistTicks: 10,
      suicideReturnMode: mode,
      ...params,
    })
    positionPlayer(world, 0, 24 * CELL) // player far from base (cell (0,24))
    world.lives = 3
    // Pool-model base buffer (hard/chaos): 120 HP.
    world.baseMaxHp = 120
    world.baseHp = 120
    placeEnemy(world, 12, 2) // threat enemy the spawn can hit (one turn)
    // Base-threat bullet only (the condition-① trigger).
    world.bullets.length = 0
    world.bullets.push(makeBullet(12 * CELL, 10 * CELL, 'down', 100, 4))
    return { world, input }
  }

  it("mode 2 + baseHpFrac: full-HP base refuses (root cause: don't abandon a working defense)", () => {
    const { input } = strictSetup(2, {
      suicideReturnBaseHpFrac: 0.5,
      suicideReturnDefendDistCells: 4,
    })
    // baseHp 120 > 0.5 × 120 → the doom threshold is not met → no trade.
    input.getMoveDirection()
    expect(input._lastBranch).not.toBe('suicideReturn')
    expect(input.branchCounts.suicideReturn).toBe(0)
  })

  it('mode 2 + baseHpFrac: base at/below the threshold commits', () => {
    const { world, input } = strictSetup(2, {
      suicideReturnBaseHpFrac: 0.5,
      suicideReturnDefendDistCells: 4,
    })
    world.baseHp = 60 // 60 ≤ 0.5 × 120 → base is a hit or two from falling
    input.getMoveDirection()
    expect(input._lastBranch).toBe('suicideReturn')
    expect(input._moveDir).toBeNull() // STAND
  })

  it('mode 2 + defendDist: player close to the base refuses (defense position held)', () => {
    const { world, input } = strictSetup(2, {
      suicideReturnBaseHpFrac: 0.5,
      suicideReturnDefendDistCells: 4,
    })
    world.baseHp = 60 // doom threshold met...
    positionPlayer(world, 10 * CELL, 24 * CELL) // ...but player at cell (10,24),
    // dist to base col 12 = 2 ≤ 4 → can still defend → no trade.
    input.getMoveDirection()
    expect(input._lastBranch).not.toBe('suicideReturn')
  })

  it('mode 2 + defendDist: player far from the base commits', () => {
    const { world, input } = strictSetup(2, {
      suicideReturnBaseHpFrac: 0.5,
      suicideReturnDefendDistCells: 4,
    })
    world.baseHp = 60
    // Player already at (0,24): dist to base = 12 > 4 → out of position.
    input.getMoveDirection()
    expect(input._lastBranch).toBe('suicideReturn')
  })

  it('mode 3 + strict guard: same refusal at full-HP base (control)', () => {
    const { input } = strictSetup(3, {
      suicideReturnBaseHpFrac: 0.5,
      suicideReturnDefendDistCells: 4,
    })
    input.getMoveDirection()
    expect(input._lastBranch).not.toBe('suicideReturn')
  })

  it('mode 3 + strict guard: commits when both doom conditions are met (far player + low base)', () => {
    const { world, input } = strictSetup(3, {
      suicideReturnBaseHpFrac: 0.5,
      suicideReturnDefendDistCells: 4,
    })
    world.baseHp = 60 // base at/below the doom threshold
    // Player already at (0,24): dist to base = 12 > 4 → out of position.
    input.getMoveDirection()
    expect(input._lastBranch).toBe('suicideReturn')
    expect(input._moveDir).not.toBeNull() // CHARGE
  })

  it('mode 2 + guards disabled (both 0, §117 defaults) → commits at full-HP base', () => {
    const { input } = strictSetup(2)
    // No strict params set → guards inert → byte-identical to §117 behavior.
    input.getMoveDirection()
    expect(input._lastBranch).toBe('suicideReturn')
  })
})
