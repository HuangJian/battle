import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import type { InputLike } from '../src/game/Input'
import type { Direction } from '../src/constants'
import { CELL } from '../src/constants'
import { PLAYER_PROGRESSION } from '../src/config/combat'
import { createTestWorld, makeEmptyStage, makeCoopWorld, placeEnemy } from './helpers'
import { cloneWorld, restoreWorld } from '../src/snapshot/WorldSerializer'

/**
 * hud.review.md P0 回归锁（75d3d1f + cee5d13 评审逐项修复）。
 * Headless、固定种子、无 wall-clock（AGENTS §7/§8）。
 */

/** One-press super-item input. */
class ItemInput implements InputLike {
  constructor(private item: 'guard' | 'frenzy' | 'rewind' | null) {}
  getMoveDirection(): Direction | null {
    return null
  }
  isFiring(): boolean {
    return false
  }
  wasItemPressed(kind: 'guard' | 'frenzy' | 'rewind'): boolean {
    return this.item === kind
  }
  endFrame(): void {}
  reset(): void {}
}

/** 2p world with both tanks online (mirrors super-stocks-2p.test.ts). */
function make2pWorld(): { world: World; sim: Simulation } {
  const world = makeCoopWorld()
  if (!world.player) {
    world.spawnPlayer()
    world.player!.spawnTimer = 0
    world.player!.shieldTimer = 0
  }
  if (world.player2) {
    world.player2.spawnTimer = 0
    world.player2.shieldTimer = 0
  }
  const sim = new Simulation(world, new Input())
  sim.input2 = new Input()
  return { world, sim }
}

/** Park a 1-HP dummy that cannot move/fire — deterministic AoE/bullet target. */
function placeDummy(world: World, col: number, row: number) {
  const enemy = placeEnemy(world, col, row, 'basic', 'down')
  enemy.hp = 1
  enemy.maxHp = 1
  enemy.speed = 0
  if (enemy.aiState) {
    enemy.aiState.thinkTimer = 1e9
    enemy.aiState.fireTimer = 1e9
    enemy.aiState.strategicTimer = 1e9
  }
  return enemy
}

describe('P0-1: same-tick double rewind spends only once', () => {
  it('P1 then P2 press in one tick — second press blocked while pending', () => {
    const { world, sim } = make2pWorld()
    world.state = 'playing'
    world.rewindStock = 1
    world.rewindStock2 = 1
    sim.systems.player.updatePlayerTank(world.player, new ItemInput('rewind'))
    expect(world.rewindStock).toBe(0)
    expect(world.rewindPendingBy).toBe(1)
    // Same tick: P2's press must not spend while a rewind is already pending.
    sim.systems.player.updatePlayerTank(world.player2, new ItemInput('rewind'))
    expect(world.rewindStock).toBe(0)
    expect(world.rewindStock2).toBe(1)
    expect(world.rewindPendingBy).toBe(1)
  })
})

describe('P0-2: no ghost P2 inventory across disable/enable', () => {
  it('disablePlayer2 drops Stock2; enablePlayer2 starts empty', () => {
    const { world } = make2pWorld()
    world.guardStock2 = 3
    world.frenzyStock2 = 2
    world.sacrificeStock2 = 1
    world.rewindStock2 = 4
    world.disablePlayer2()
    expect(world.guardStock2).toBe(0)
    expect(world.frenzyStock2).toBe(0)
    expect(world.sacrificeStock2).toBe(0)
    expect(world.rewindStock2).toBe(0)
    // A fresh P2 inherits nothing — even if someone refills behind our back,
    // enable resets to empty like lives2/level2.
    world.guardStock2 = 9
    world.enablePlayer2()
    expect(world.guardStock2).toBe(0)
    expect(world.frenzyStock2).toBe(0)
    expect(world.sacrificeStock2).toBe(0)
    expect(world.rewindStock2).toBe(0)
  })
})

describe('P0-3: restoreWorld clears the transient rewind signal', () => {
  it('a stale pending flag does not survive a restore', () => {
    const { world } = make2pWorld()
    const snap = cloneWorld(world)
    const fresh = make2pWorld().world
    fresh.rewindPending = true
    fresh.rewindPendingBy = 2
    restoreWorld(fresh, snap)
    expect(fresh.rewindPending).toBe(false)
    expect(fresh.rewindPendingBy as number).toBe(1)
  })
})

describe('P0-4: AoE kill credit follows the owner', () => {
  it("P2's sacrifice credits score2, never score", () => {
    const { world, sim } = make2pWorld()
    world.score = 0
    world.score2 = 0
    world.sacrificeStock2 = 1
    const p2 = world.player2!
    placeDummy(world, Math.round(p2.x / CELL), Math.round(p2.y / CELL))
    sim.systems.enemies.triggerSacrificeAoE(p2)
    expect(world.sacrificeStock2).toBe(0)
    expect(world.score2).toBeGreaterThan(0)
    expect(world.score).toBe(0)
  })

  it("P1's sacrifice still credits score", () => {
    const { world, sim } = make2pWorld()
    world.score = 0
    world.score2 = 0
    world.sacrificeStock = 1
    const p1 = world.player!
    placeDummy(world, Math.round(p1.x / CELL), Math.round(p1.y / CELL))
    sim.systems.enemies.triggerSacrificeAoE(p1)
    expect(world.score).toBeGreaterThan(0)
    expect(world.score2).toBe(0)
  })

  it("P2's bomb credits score2", () => {
    const { world, sim } = make2pWorld()
    world.score = 0
    world.score2 = 0
    placeDummy(world, 5, 5)
    sim.systems.powerUps.applyPowerUp('bomb', world.player2 ?? undefined)
    expect(world.score2).toBeGreaterThan(0)
    expect(world.score).toBe(0)
  })

  it("P2's mine credits score2 (ownerSlot)", () => {
    const { world, sim } = make2pWorld()
    world.score = 0
    world.score2 = 0
    const p2 = world.player2!
    sim.systems.player.placeMine(p2)
    expect(world.mines.length).toBe(1)
    expect(world.mines[0].ownerSlot).toBe(2)
    world.mines[0].armTimer = 0
    const m = world.mines[0]
    placeDummy(world, Math.round(m.x / CELL), Math.round(m.y / CELL))
    sim.systems.player.updateMines()
    expect(world.score2).toBeGreaterThan(0)
    expect(world.score).toBe(0)
  })

  it('spectateDual P2 bullet kills credit score2 (isGodKill)', () => {
    const world = createTestWorld({ rngSeed: 42 })
    world.difficultyKey = 'classic'
    world.loadStageData(makeEmptyStage(), 0)
    world.state = 'playing'
    world.spectateDual = true
    world.enablePlayer2()
    for (const p of [world.player, world.player2]) {
      if (p) {
        p.spawnTimer = 0
        p.shieldTimer = 0
      }
    }
    const sim = new Simulation(world, new Input())
    sim.input2 = new Input()
    world.tanks.length = 0
    world.spawnQueue.length = 0
    world.enemiesRemaining = 1000
    world.spawnTimer = 1e9
    world.score = 0
    world.score2 = 0
    const dummy = placeDummy(world, 5, 5)
    world.addBullet({
      id: 990001,
      ownerId: world.player2!.id,
      ownerKind: 'player',
      isPlayer: true,
      allegiance: 'player',
      x: dummy.x + dummy.w / 2 - 2,
      y: dummy.y + dummy.h / 2 - 2,
      w: 4,
      h: 4,
      dir: 'down',
      speed: 0,
      power: 2,
      damage: 100,
      alive: true,
    })
    sim.tick()
    expect(world.score2).toBeGreaterThan(0)
    expect(world.score).toBe(0)
  })
})

describe('P0-5: star shield spends the hit tank’s own level', () => {
  it("P2's shield drops playerLevel2, never playerLevel", () => {
    const world = createTestWorld({ rngSeed: 42 })
    world.difficultyKey = 'classic'
    world.loadStageData(makeEmptyStage(), 0)
    world.state = 'playing'
    world.twoPlayer = true
    world.enablePlayer2()
    const sim = new Simulation(world, new Input())
    sim.input2 = new Input()
    world.tanks.length = 0
    world.spawnQueue.length = 0
    world.enemiesRemaining = 1000
    world.spawnTimer = 1e9
    const max = PLAYER_PROGRESSION.maximumLevel
    world.playerLevel = max
    world.player!.level = max
    world.playerLevel2 = max
    world.player2!.level = max
    for (const p of [world.player, world.player2]) {
      if (p) {
        p.spawnTimer = 0
        p.shieldTimer = 0
      }
    }
    const p2 = world.player2!
    // NOTE: loadStageData keeps modern pool HP even with difficultyKey
    // classic (rules come from startGame) — use overwhelming damage so the
    // hit is lethal and the shield path triggers.
    world.addBullet({
      id: 991002,
      ownerId: -1,
      ownerKind: 'basic',
      isPlayer: false,
      allegiance: 'enemy',
      x: p2.x + p2.w / 2 - 2,
      y: p2.y,
      w: 4,
      h: 4,
      dir: 'down',
      speed: 0,
      power: 1,
      damage: 10000,
      alive: true,
    })
    sim.tick()
    expect(world.player2!.alive).toBe(true)
    expect(world.playerLevel2).toBe(max - 1)
    expect(world.player2!.level).toBe(max - 1)
    expect(world.playerLevel).toBe(max)
  })
})
