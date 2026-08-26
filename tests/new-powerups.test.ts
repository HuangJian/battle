import { seedWorld } from './helpers'
import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import type { PowerUpType } from '../src/types'
import { cloneWorld, restoreWorld } from '../src/snapshot/WorldSerializer'
import { SnapshotManager } from '../src/snapshot/SnapshotManager'
import { RecoveryController } from '../src/snapshot/RecoveryController'
import {
  EMP_DURATION_MS,
  MINE_ARM_MS,
  CELL,
  GRID,
  REPAIR_HEAL_AMOUNT,
  BOAT_DURATION_MS,
} from '../src/constants'
import { DECOY_LIFESPAN_FRAMES } from '../src/constants'
import { perceive, analyze } from '../src/ai/perception'
import { INTELLIGENCE_LEVELS } from '../src/ai/config'
import { POWERUP_TIERS, SUPER_POWERUP_TYPES } from '../src/config/powerups'

/**
 * New power-ups (new-powerups-plan):
 *   Repair / EMP·Silence / 时光宝盒(Rewind) / Decoy / Mine
 *
 * Covers: activation correctness, the user's weighted 3-tier drop (10/40/50),
 * classic exclusion, EMP silencing enemies-but-not-allies, decoy never fires,
 * mine detonation + arm delay, 时光宝盒 stock/recovery, and rewind-safe
 * serialization (WorldSerializer round-trip).
 */

/** Fresh, seeded World on stage 0 in 'playing' state, modern mode. */
function buildWorld(seed: number, difficulty = 'hard'): { world: World; sim: Simulation } {
  const world = seedWorld(seed)
  const input = new Input()
  const sim = new Simulation(world, input)
  world.startGame(difficulty, 'modern', 0)
  return { world, sim }
}

const apply = (sim: Simulation, t: PowerUpType) =>
  sim.systems.powerUps.applyPowerUp.bind(sim.systems.powerUps)(t)
/** Dynamic dispatch into subsystems (composition replaces sim.method). */
const call = (sim: Simulation, method: string, ...args: unknown[]): unknown => {
  const reg = sim.systems as unknown as Record<
    string,
    Record<string, ((...a: unknown[]) => unknown) | undefined>
  >
  for (const key of ['spawn', 'player', 'enemies', 'combat', 'powerUps', 'effects'] as const) {
    const fn = reg[key]?.[method]
    if (typeof fn === 'function') return fn.apply(reg[key], args)
  }
  throw new Error(`unknown simulation method: ${method}`)
}

describe('Repair (维修) — restores PLAYER HP by a fixed amount', () => {
  it('heals by REPAIR_HEAL_AMOUNT (= one basic enemy bullet damage, not full)', () => {
    const { world, sim } = buildWorld(1)
    const p = world.player!
    p.hp = 1 // simulate damage
    const hpBefore = p.hp
    expect(p.hp).toBeLessThan(p.maxHp)
    apply(sim, 'repair')
    // §189: heals by a fixed amount (100 HP = basic enemy bullet damage), not full
    expect(p.hp).toBe(Math.min(hpBefore + REPAIR_HEAL_AMOUNT, p.maxHp))
  })

  it('does not change base HP (the eagle has its own health)', () => {
    const { world, sim } = buildWorld(2)
    const baseBefore = world.baseHp
    apply(sim, 'repair')
    expect(world.baseHp).toBe(baseBefore)
  })

  it('heals the COLLECTOR (coop P2), not player1 (§3.1)', () => {
    const { world, sim } = buildWorld(7)
    const p1 = world.player!
    // A coop-style player2 tank grabs the repair item.
    const p2 = world.createTank('basic', 100, 100, 'right')
    world.player2 = p2
    // Damage both; only the collector (P2) should be healed.
    p1.hp = 1
    p2.hp = 1
    const p1Before = p1.hp
    const p2Before = p2.hp
    // applyPowerUp is private; pass the collector explicitly.
    sim.systems.powerUps.applyPowerUp('repair', p2)
    expect(p2.hp).toBe(Math.min(p2Before + REPAIR_HEAL_AMOUNT, p2.maxHp))
    expect(p1.hp).toBe(p1Before) // player1 must stay untouched
  })

  it('boat (水陆两栖) also applies to the COLLECTOR (coop P2), not player1', () => {
    const { world, sim } = buildWorld(8)
    const p1 = world.player!
    const p2 = world.createTank('basic', 100, 100, 'right')
    world.player2 = p2
    p1.boatTimer = 0
    p2.boatTimer = 0
    sim.systems.powerUps.applyPowerUp('boat', p2)
    expect(p2.boatTimer).toBe(BOAT_DURATION_MS)
    expect(p1.boatTimer ?? 0).toBe(0) // player1 must stay untouched
  })
})

describe('EMP·Silence (电磁静默) — enemies cannot fire, allies can', () => {
  it('applyPowerUp sets empTimer, and accumulates on re-pickup', () => {
    const { world, sim } = buildWorld(3)
    apply(sim, 'emp')
    expect(world.empTimer).toBe(EMP_DURATION_MS)
    apply(sim, 'emp')
    expect(world.empTimer).toBe(EMP_DURATION_MS * 2)
  })

  it('silences ENEMY tanks but NOT friendly allies during EMP', () => {
    const { world, sim } = buildWorld(4)
    world.empTimer = 1000

    // Enemy: must be silenced.
    const enemy = world.createTank('basic', 200, 200, 'right')
    enemy.allegiance = 'enemy'
    enemy.spawnTimer = 0
    enemy.lastFire = -1e9
    const beforeEnemy = world.bullets.length
    call(sim, 'tryFire', enemy)
    expect(world.bullets.length).toBe(beforeEnemy) // no bullet

    // Friendly ally: must still be able to fire (so 天降神兵 works under EMP).
    const ally = world.createTank('basic', 100, 100, 'right')
    ally.allegiance = 'ally'
    ally.isPlayer = false
    ally.spawnTimer = 0
    ally.lastFire = -1e9
    const beforeAlly = world.bullets.length
    call(sim, 'tryFire', ally)
    expect(world.bullets.length).toBe(beforeAlly + 1) // fired
  })

  it('enemies can fire again once empTimer elapses', () => {
    const { world, sim } = buildWorld(5)
    const enemy = world.createTank('basic', 200, 200, 'right')
    enemy.allegiance = 'enemy'
    enemy.spawnTimer = 0
    enemy.lastFire = -1e9
    world.empTimer = 0
    const before = world.bullets.length
    call(sim, 'tryFire', enemy)
    expect(world.bullets.length).toBe(before + 1)
  })
})

describe('Decoy (诱饵) — spawns a non-firing ally that draws fire', () => {
  it('applyPowerUp spawns an ally flagged isDecoy with normal enemy HP', () => {
    const { world, sim } = buildWorld(6)
    const before = world.allies.length
    apply(sim, 'decoy')
    expect(world.allies.length).toBe(before + 1)
    const decoy = world.allies[world.allies.length - 1]
    expect(decoy.isDecoy).toBe(true)
    expect(decoy.allegiance).toBe('ally')
    expect(decoy.isPlayer).toBe(false)
    // Decoy HP = the normal (basic) enemy HP value, NOT a hardcoded 1.
    const refBasic = world.createTank('basic', 0, 0, 'up')
    expect(decoy.hp).toBe(refBasic.maxHp)
    expect(decoy.maxHp).toBe(refBasic.maxHp)
    expect(decoy.guardExpireFrame).toBe(world.frame + DECOY_LIFESPAN_FRAMES)
  })

  it('a decoy never fires (tryFire is a no-op for isDecoy)', () => {
    const { world, sim } = buildWorld(7)
    apply(sim, 'decoy')
    const decoy = world.allies[world.allies.length - 1]
    decoy.lastFire = -1e9
    const before = world.bullets.length
    call(sim, 'tryFire', decoy)
    expect(world.bullets.length).toBe(before) // no bullet
  })

  it('enemy AI perceives a decoy and can aim at it (decoyInLineOfFire)', () => {
    const { world } = buildWorld(7)
    // Clear the field so LOS between enemy and decoy is unobstructed.
    for (let r = 0; r < GRID; r++) {
      for (let c = 0; c < GRID; c++) world.tileMap.grid[r][c] = 'empty'
    }
    world.tileMap.rebuildBaseCache()

    const enemy = world.createTank('basic', 100, 100, 'right')
    enemy.allegiance = 'enemy'
    enemy.spawnTimer = 0
    world.tanks.push(enemy)

    // Decoy just ahead of the enemy along its facing direction (within LOS range).
    const decoy = world.createTank('basic', 132, 100, 'left')
    decoy.allegiance = 'ally'
    decoy.isPlayer = false
    decoy.isDecoy = true
    decoy.spawnTimer = 0
    world.allies.push(decoy)

    const p = perceive(world, enemy, INTELLIGENCE_LEVELS['rookie'])
    expect(p.hasDecoy).toBe(true)
    // decoyInLineOfFire is surfaced through analyze() (the brain's signal)
    const s = analyze(world, enemy, p, INTELLIGENCE_LEVELS['rookie'])
    expect(s.decoyInLineOfFire).toBe(true)

    // With the decoy removed, the enemy no longer perceives one.
    world.allies.length = 0
    const p2 = perceive(world, enemy, INTELLIGENCE_LEVELS['rookie'])
    expect(p2.hasDecoy).toBe(false)
  })
})

describe('Mine (地雷) — placed by player, detonates on enemy contact', () => {
  it('applyPowerUp places a mine at the player grid cell', () => {
    const { world, sim } = buildWorld(8)
    const p = world.player!
    p.x = 100
    p.y = 100
    const before = world.mines.length
    apply(sim, 'mine')
    expect(world.mines.length).toBe(before + 1)
    const mine = world.mines[world.mines.length - 1]
    expect(mine.x).toBe(Math.round(p.x / CELL) * CELL)
    expect(mine.y).toBe(Math.round(p.y / CELL) * CELL)
    expect(mine.alive).toBe(true)
    expect(mine.armTimer).toBe(MINE_ARM_MS) // armed after a short delay
  })

  it('does NOT detonate while still arming (armTimer > 0)', () => {
    const { world, sim } = buildWorld(9)
    apply(sim, 'mine')
    const mine = world.mines[world.mines.length - 1]
    expect(mine.armTimer).toBeGreaterThan(0) // still arming

    const enemy = world.createTank('basic', mine.x, mine.y, 'down')
    enemy.allegiance = 'enemy'
    enemy.spawnTimer = 0
    world.tanks.push(enemy)

    call(sim, 'updateMines')
    expect(enemy.alive).toBe(true) // safe while arming
    expect(mine.alive).toBe(true)
  })

  it('detonates on enemy contact once armed, destroying the enemy', () => {
    const { world, sim } = buildWorld(10)
    apply(sim, 'mine')
    const mine = world.mines[world.mines.length - 1]
    mine.armTimer = 0 // simulate fully armed

    const enemy = world.createTank('basic', mine.x, mine.y, 'down')
    enemy.allegiance = 'enemy'
    enemy.spawnTimer = 0
    world.tanks.push(enemy)

    expect(world.killCount).toBe(0)
    call(sim, 'updateMines')
    expect(enemy.alive).toBe(false) // killed by the blast
    expect(mine.alive).toBe(false) // consumed
    expect(world.killCount).toBe(1) // normal kill accounting
  })
})

describe('时光宝盒 (Rewind) — super item stock + manual recovery', () => {
  it('applyPowerUp accumulates a stock charge', () => {
    const { world, sim } = buildWorld(11)
    apply(sim, 'rewind')
    expect(world.rewindStock).toBe(1)
    apply(sim, 'rewind')
    expect(world.rewindStock).toBe(2)
  })

  it('activateRewind consumes a charge and signals the consumer (rewindPending)', () => {
    const { world, sim } = buildWorld(12)
    world.rewindStock = 1
    world.rewindPending = false
    call(sim, 'activateRewind', world.player!)
    expect(world.rewindStock).toBe(0)
    expect(world.rewindPending).toBe(true)
  })

  it('activateRewind is a no-op with zero stock (no false signal)', () => {
    const { world, sim } = buildWorld(13)
    world.rewindStock = 0
    world.rewindPending = false
    call(sim, 'activateRewind', world.player!)
    expect(world.rewindStock).toBe(0)
    expect(world.rewindPending).toBe(false)
  })

  it('RecoveryController.beginManualRewind starts when a snapshot exists', () => {
    const { world } = buildWorld(14)
    const manager = new SnapshotManager({ backend: null })
    const recovery = new RecoveryController(manager)
    manager.create('auto', world) // give it rewindable history
    expect(recovery.phase).toBe('idle')
    const ok = recovery.beginManualRewind(world)
    expect(ok).toBe(true)
    expect(recovery.phase).toBe('fading')
    expect(world.state).toBe('recovery')
  })

  it('beginManualRewind refuses (and the caller must refund) when no history exists', () => {
    const { world } = buildWorld(15)
    const manager = new SnapshotManager({ backend: null })
    const recovery = new RecoveryController(manager)
    // Model the real flow: activateRewind already spent the charge (stock 1→0)
    // before the consumer calls beginManualRewind.
    world.rewindStock = 0
    const ok = recovery.beginManualRewind(world)
    expect(ok).toBe(false)
    expect(recovery.phase).toBe('idle')
    expect(world.state).toBe('playing')
    // The Game.ts consumer refunds the spent charge on a false return:
    if (!ok) world.rewindStock++
    expect(world.rewindStock).toBe(1)
  })
})

describe('Weighted 3-tier drop (user decision: 10% / 40% / 50%)', () => {
  it('modern mode distributes super/practical/normal at ~10/40/50 over many samples', () => {
    const { sim } = buildWorld(0x5eed, 'hard')
    const N = 6000
    let s = 0
    let pr = 0
    let no = 0
    for (let i = 0; i < N; i++) {
      const t = sim.systems.powerUps.rollPowerUpType()
      if ((POWERUP_TIERS.super as readonly string[]).includes(t)) s++
      else if ((POWERUP_TIERS.practical as readonly string[]).includes(t)) pr++
      else no++
    }
    const fs = s / N
    const fpr = pr / N
    const fno = no / N
    // ±4% tolerance around the decision ratios.
    expect(Math.abs(fs - 0.1)).toBeLessThan(0.04)
    expect(Math.abs(fpr - 0.4)).toBeLessThan(0.04)
    expect(Math.abs(fno - 0.5)).toBeLessThan(0.04)
  })

  it('the super tier contains exactly the 4 强力道具 (incl. 时光宝盒)', () => {
    expect([...POWERUP_TIERS.super].sort()).toEqual([...SUPER_POWERUP_TYPES].sort())
  })
})

describe('Classic mode excludes all new power-ups (user decision ①)', () => {
  it('rollPowerUpType never returns a new item in classic', () => {
    const { sim } = buildWorld(99, 'classic')
    const newItems = ['repair', 'emp', 'decoy', 'mine', 'rewind', 'boat']
    for (let i = 0; i < 2000; i++) {
      const t = sim.systems.powerUps.rollPowerUpType()
      expect(newItems).not.toContain(t)
    }
  })

  it('classic never enters the modern 3-tier path (superDropChance = 0)', () => {
    const { world } = buildWorld(99, 'classic')
    expect(world.rules.superDropChance).toBe(0)
    expect(world.rules.dropSchedule).not.toBe('modern')
  })
})

describe('Rewind-safe serialization (WorldSerializer round-trip)', () => {
  it('clone/restore preserves empTimer, rewindStock, mines, and decoy flag', () => {
    const { world, sim } = buildWorld(20)
    // Exercise every new piece of state.
    world.empTimer = 777
    world.rewindStock = 3
    apply(sim, 'mine')
    apply(sim, 'decoy')
    const mineCount = world.mines.length

    const snap = cloneWorld(world)
    // Restore into a fresh world and verify fidelity.
    const fresh = new World()
    fresh.startGame('hard', 'modern', 0)
    restoreWorld(fresh, snap)

    expect(fresh.empTimer).toBe(777)
    expect(fresh.rewindStock).toBe(3)
    expect(fresh.mines.length).toBe(mineCount)
    const restoredDecoy = fresh.allies.find((a) => a.isDecoy)
    expect(restoredDecoy).toBeDefined()
    expect(restoredDecoy!.isDecoy).toBe(true)
  })
})
