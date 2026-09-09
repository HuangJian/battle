import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import type { InputLike } from '../src/game/Input'
import { makeCoopWorld } from './helpers'
import { cloneWorld, restoreWorld } from '../src/snapshot/WorldSerializer'
import type { WorldSnapshot } from '../src/snapshot/types'
import type { Direction } from '../src/constants'

/**
 * 双打/躺赢 强力道具分家 (per-player super-item inventories): P1 and P2 each
 * collect, hoard and spend their OWN guard / frenzy / sacrifice / rewind
 * stock — the four World fields keep P1 (guardStock …) and the `2`-suffixed
 * fields hold P2's (superStocks.ts). P2 = the tank that IS world.player2,
 * whether driven by a human (双打), the coop God AI, or dual-spectate.
 *
 * These are headless SIMULATION tests: pickup (applyPowerUp), the consume
 * gate (updatePlayerTank), the passive sacrifice release
 * (triggerSacrificeAoE) and the rewind refund (Simulation.refundRewind) —
 * no DOM, no wall-clock, fixed seeds.
 */

/** One-press input: holds a single super-item edge for one update. */
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

/** 2p World with BOTH tanks online and a sim wired to them. makeCoopWorld
 *  only brings player2 up — spawn player1 through the canonical path so the
 *  consume gate (updatePlayerTank) actually drives a tank. */
function make2pWorld(): { world: World; sim: Simulation } {
  const world = makeCoopWorld()
  if (!world.player) {
    world.spawnPlayer()
    world.player!.spawnTimer = 0
    world.player!.shieldTimer = 0
  }
  const sim = new Simulation(world, new Input())
  sim.input2 = new Input()
  return { world, sim }
}

describe('per-player super-item PICKUP routing (superStocks.ts)', () => {
  it('a super item goes to the COLLECTOR: P1→P1 stock, P2→P2 stock', () => {
    const { world, sim } = make2pWorld()
    sim.systems.powerUps.applyPowerUp('guard', world.player ?? undefined)
    sim.systems.powerUps.applyPowerUp('frenzy', world.player2 ?? undefined)
    sim.systems.powerUps.applyPowerUp('rewind', world.player2 ?? undefined)
    sim.systems.powerUps.applyPowerUp('sacrifice', world.player ?? undefined)

    expect(world.guardStock).toBe(1)
    expect(world.guardStock2).toBe(0)
    expect(world.frenzyStock).toBe(0)
    expect(world.frenzyStock2).toBe(1)
    expect(world.rewindStock).toBe(0)
    expect(world.rewindStock2).toBe(1)
    expect(world.sacrificeStock).toBe(1)
    expect(world.sacrificeStock2).toBe(0)
  })
})

describe('per-player super-item CONSUMPTION (各自使用)', () => {
  it("P1 pressing guard spends ONLY P1's 天降神兵 stock", () => {
    const { world, sim } = make2pWorld()
    world.guardStock = 2
    world.guardStock2 = 1
    sim.systems.player.updatePlayerTank(world.player, new ItemInput('guard'))
    expect(world.guardStock).toBe(1)
    expect(world.guardStock2).toBe(1) // P2's untouched
  })

  it("P2 pressing guard spends ONLY P2's stock — P1's is protected", () => {
    const { world, sim } = make2pWorld()
    world.guardStock = 2
    world.guardStock2 = 1
    sim.systems.player.updatePlayerTank(world.player2, new ItemInput('guard'))
    expect(world.guardStock).toBe(2) // P1's untouched
    expect(world.guardStock2).toBe(0)
  })

  it("frenzy spends the OWNER's inventory (P2 fires its own barrage)", () => {
    const { world, sim } = make2pWorld()
    world.frenzyStock = 0
    world.frenzyStock2 = 2
    sim.systems.player.updatePlayerTank(world.player2, new ItemInput('frenzy'))
    expect(world.frenzyStock).toBe(0)
    expect(world.frenzyStock2).toBe(1)
    expect(world.player2!.frenzyTimer).toBeGreaterThan(0)
  })

  it('an empty inventory blocks the press (no cross-player borrowing)', () => {
    const { world, sim } = make2pWorld()
    world.guardStock = 0
    world.guardStock2 = 0
    sim.systems.player.updatePlayerTank(world.player, new ItemInput('guard'))
    expect(world.allies.length).toBe(0) // no summon, nothing spent
  })

  it("rewind charges the presser's own stock and records WHO pays (refund target)", () => {
    const { world, sim } = make2pWorld()
    world.rewindStock = 1
    world.rewindStock2 = 1
    sim.systems.player.updatePlayerTank(world.player2, new ItemInput('rewind'))
    expect(world.rewindStock).toBe(1) // P1's untouched
    expect(world.rewindStock2).toBe(0)
    expect(world.rewindPending).toBe(true)
    expect(world.rewindPendingBy).toBe(2)

    // The refund (rewind could not start — recovery busy) returns the charge
    // to the SAME player who paid it.
    sim.clearRewindPending()
    sim.refundRewind()
    expect(world.rewindStock2).toBe(1)
    expect(world.rewindStock).toBe(1)

    // P1's press + refund round-trips the other way too.
    sim.systems.player.updatePlayerTank(world.player, new ItemInput('rewind'))
    expect(world.rewindPendingBy).toBe(1)
    sim.clearRewindPending()
    sim.refundRewind()
    expect(world.rewindStock).toBe(1)
    expect(world.rewindStock2).toBe(1)
  })
})

describe("同归于尽 sacrifice — the FALLEN player's own stock blasts", () => {
  it("P2's death consumes P2's sacrifice stock, never P1's", () => {
    const { world, sim } = make2pWorld()
    world.sacrificeStock = 2
    world.sacrificeStock2 = 2
    sim.systems.enemies.triggerSacrificeAoE(world.player2!)
    expect(world.sacrificeStock).toBe(2) // P1's untouched
    expect(world.sacrificeStock2).toBe(0) // all of P2's released at once
  })

  it('no-op when the fallen player has no sacrifice stock', () => {
    const { world, sim } = make2pWorld()
    world.sacrificeStock = 2
    world.sacrificeStock2 = 0
    sim.systems.enemies.triggerSacrificeAoE(world.player2!)
    expect(world.sacrificeStock).toBe(2)
    expect(world.sacrificeStock2).toBe(0)
  })
})

describe('snapshot round-trip of the P2 inventories', () => {
  it('clone/restore preserves all four P2 stock fields', () => {
    const { world } = make2pWorld()
    world.guardStock2 = 3
    world.frenzyStock2 = 1
    world.sacrificeStock2 = 2
    world.rewindStock2 = 4
    world.guardStock = 5

    const snap = cloneWorld(world)
    const fresh = new World()
    restoreWorld(fresh, snap)
    expect(fresh.guardStock2).toBe(3)
    expect(fresh.frenzyStock2).toBe(1)
    expect(fresh.sacrificeStock2).toBe(2)
    expect(fresh.rewindStock2).toBe(4)
    expect(fresh.guardStock).toBe(5) // P1 fields keep their historical names
  })

  it('legacy snapshots (no P2 fields) restore P2 to zero', () => {
    const { world } = make2pWorld()
    world.rewindStock2 = 7
    const snap = cloneWorld(world) as WorldSnapshot & { guardStock2?: number }
    // Simulate an old save taken before the per-player split.
    delete snap.guardStock2
    delete snap.frenzyStock2
    delete snap.sacrificeStock2
    delete snap.rewindStock2

    const fresh = new World()
    fresh.rewindStock2 = 9 // divergent stale value must be wiped
    restoreWorld(fresh, snap)
    expect(fresh.guardStock2).toBe(0)
    expect(fresh.frenzyStock2).toBe(0)
    expect(fresh.sacrificeStock2).toBe(0)
    expect(fresh.rewindStock2).toBe(0)
  })
})
