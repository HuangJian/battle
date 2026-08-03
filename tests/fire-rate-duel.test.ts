import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { CELL } from '../src/constants'
import type { Tank, TankKind } from '../src/types'
import { TANK_PROFILES, applyEliteModifier, profileToStats } from '../src/config/combat'
import { FIRE_FREQUENCY_MULTIPLIER, baseFireIntervalMs } from '../src/config/fire-rate'

/**
 * Fire-rate standard — ordering contract (user requirement, 2026-07-26).
 *
 * Fire cadence is now a single, data-driven standard (config/fire-rate.ts),
 * NOT the old `fireControl`→cooldown formula. A *higher* firing-frequency
 * multiplier ⇒ a SHORTER interval ⇒ fires MORE often:
 *   basic 1.00×, fast 1.05×, power 1.10×, armor 0.90×, player 1.05× (no star).
 *
 * This deliberately REVERSES the 2026-07-23 "player never out-fired" invariant:
 * the user's new spec lets the power enemy out-rate the unbuffed player
 * (1.10× > 1.05×). The no-star player still out-rates (or ties) basic / fast /
 * armor. A max-level player (1.20×) out-rates every enemy.
 *
 * Two layers of protection:
 *  1. Config contract — assert the derived ordering directly.
 *  2. Behavioral duel — run the real Simulation with the player and one enemy
 *     of each kind locked in a cleared corridor, BOTH firing at max cadence,
 *     and assert the shot-count ordering matches the spec.
 */

const ENEMY_KINDS: Exclude<TankKind, 'player'>[] = ['basic', 'fast', 'power', 'armor']

// ================================================================
// 1. Config contract — the firing-frequency ordering
// ================================================================

describe('Fire-rate standard — frequency ordering (config contract)', () => {
  const playerM = FIRE_FREQUENCY_MULTIPLIER.player // 1.05× (no star)

  it('basic / fast / armor do NOT out-rate the unbuffed (level 0) player', () => {
    expect(FIRE_FREQUENCY_MULTIPLIER.basic).toBeLessThanOrEqual(playerM)
    expect(FIRE_FREQUENCY_MULTIPLIER.fast).toBeLessThanOrEqual(playerM) // exactly ties
    expect(FIRE_FREQUENCY_MULTIPLIER.armor).toBeLessThanOrEqual(playerM)
  })

  it('power OUT-rates the unbuffed player (explicit new design: 1.10× > 1.05×)', () => {
    expect(FIRE_FREQUENCY_MULTIPLIER.power).toBeGreaterThan(playerM)
  })

  it('translates to cooldown ordering: playerCd <= basic/fast/armor, playerCd > power', () => {
    const playerCd = baseFireIntervalMs('player', 0)
    expect(baseFireIntervalMs('basic')).toBeGreaterThanOrEqual(playerCd)
    expect(baseFireIntervalMs('fast')).toBeCloseTo(playerCd, 6) // tie
    expect(baseFireIntervalMs('armor')).toBeGreaterThanOrEqual(playerCd)
    expect(baseFireIntervalMs('power')).toBeLessThan(playerCd)
  })

  it('a max-level (3★) player out-rates every enemy archetype', () => {
    const player3M = FIRE_FREQUENCY_MULTIPLIER.player + 3 * 0.05 // 1.20×
    expect(player3M).toBeGreaterThan(FIRE_FREQUENCY_MULTIPLIER.basic)
    expect(player3M).toBeGreaterThan(FIRE_FREQUENCY_MULTIPLIER.fast)
    expect(player3M).toBeGreaterThan(FIRE_FREQUENCY_MULTIPLIER.power)
    expect(player3M).toBeGreaterThan(FIRE_FREQUENCY_MULTIPLIER.armor)
  })

  it('elite promotion never changes fire cadence (so the ordering is preserved)', () => {
    for (const kind of ENEMY_KINDS) {
      const base = profileToStats(TANK_PROFILES[kind], kind).fireCooldown
      const elite = profileToStats(applyEliteModifier(TANK_PROFILES[kind], kind), kind).fireCooldown
      expect(elite).toBe(base)
    }
  })
})

// ================================================================
// 2. Behavioral duel — real Simulation, no buffs on either side
// ================================================================

interface DuelResult {
  playerDeaths: number
  enemyDied: boolean
  playerShots: number
  enemyShots: number
  playerCd: number
  enemyCd: number
  kind: Exclude<TankKind, 'player'>
}

/**
 * Head-on duel harness.
 *
 * Setup: hard difficulty with the player PINNED to level 0 below (the "no
 * buffs" baseline — hard now starts at 1★ since §104/M6, so the pin is what
 * guarantees the level-0 cadence, not the difficulty). Corridor columns 8–9
 * cleared of all terrain. The player sits at its spawn (col 8, row 24) facing
 * up; the enemy is pinned at the top of the same corridor facing down. The
 * player holds the fire key; the enemy is force-fired every tick through the
 * same `tryFire` gate the game uses, so BOTH sides shoot at exactly their
 * per-type cadence — the pure worst case for whichever side is slower.
 *
 * The enemy is re-pinned after every tick (position/direction) so AI movement
 * cannot break the alignment; its firing itself stays fully authentic
 * (cooldown gate, bullet creation, collisions all run in the Simulation).
 * Every opposing bullet meets head-on and cancels 1:1, so the faster cadence
 * lands the surplus shell.
 */
function runDuel(kind: Exclude<TankKind, 'player'>, ticks: number): DuelResult {
  const world = new World()
  world.rng = new RNG(1234)
  const input = new Input()
  const sim = new Simulation(world, input)
  world.startGame('hard', 'modern', 0)
  // §104 (M6): hard now starts at 1★. This duel tests the no-buff ordering,
  // so pin the player to level 0 regardless of difficulty config. The tank's
  // cadence fields are frozen at spawn, so they must be reset too — the same
  // pattern the max-level duel below uses for 3★.
  world.playerLevel = 0
  const player = world.player!
  player.level = 0
  // Round to match profileToStats' Math.round — the enemy's fireCooldown is
  // the rounded value, so the 0★ player (same 1.05× multiplier as fast) must
  // be too, or the <= comparison fails on a float edge (825.3968 vs 825).
  const base0 = Math.round(baseFireIntervalMs('player', 0))
  player.fireCooldown = base0
  player.nextFireInterval = base0

  // No other enemies — this is a 1v1 duel.
  world.spawnQueue.length = 0

  player.spawnTimer = 0
  // Shield the player for the whole duel. With opposing bullets cancelling 1:1,
  // the faster cadence wins the exchange; the loser's single surplus shell
  // would otherwise trade the player down. The shield isolates the *ordering*
  // we are testing (who fires more), not the HP trade.
  player.shieldTimer = 1e9

  // Clear the duel corridor (both sub-block columns of the player's lane).
  for (let r = 0; r <= 25; r++) {
    world.tileMap.set(8, r, 'empty')
    world.tileMap.set(9, r, 'empty')
  }

  // Enemy pinned at the top of the corridor, same x as the player, facing down.
  const ex = player.x
  const ey = CELL // y = 16, well inside the cleared corridor
  const enemy = world.createTank(kind, ex, ey, 'down')
  enemy.spawnTimer = 0 // active immediately (and hittable — a fair duel)
  if (enemy.aiState) enemy.aiState.isCommander = true // never promote to elite mid-duel
  world.tanks.push(enemy)

  // Hold the fire key (drive Input's keydown handler directly — no DOM).
  ;(
    input as unknown as {
      onKeyDown: (e: { code: string; preventDefault: () => void }) => void
    }
  ).onKeyDown({ code: input.keys.fire, preventDefault: () => {} })

  const fire = (sim as unknown as { tryFire: (t: Tank) => void }).tryFire.bind(sim)

  let playerDeaths = 0
  let enemyDied = false
  let playerShots = 0
  let enemyShots = 0

  for (let t = 0; t < ticks; t++) {
    sim.tick()

    for (const ev of world.consumeEvents()) {
      if (ev.type === 'bullet_fired') {
        if (ev.bullet.isPlayer) playerShots++
        else enemyShots++
      } else if (ev.type === 'tank_destroyed') {
        if (ev.tank.isPlayer) playerDeaths++
        else enemyDied = true
      }
    }

    if (enemyDied) break

    // Re-pin the enemy and fire at max cadence (gated by its nextFireInterval).
    enemy.x = ex
    enemy.y = ey
    enemy.dir = 'down'
    enemy.moving = false
    fire(enemy)
  }

  return {
    playerDeaths,
    enemyDied,
    playerShots,
    enemyShots,
    playerCd: player.fireCooldown,
    enemyCd: enemy.fireCooldown,
    kind,
  }
}

describe('Fire-rate standard — head-on duel vs every enemy type (no buffs)', () => {
  const DUEL_TICKS = 6000

  for (const kind of ENEMY_KINDS) {
    it(`fire-rate ordering vs '${kind}' matches the spec`, () => {
      const r = runDuel(kind, DUEL_TICKS)

      // Player is shielded for the whole duel, so it can never die.
      expect(r.playerDeaths).toBe(0)
      // Sanity: the duel really happened — both sides kept shooting.
      expect(r.playerShots).toBeGreaterThan(5)
      expect(r.enemyShots).toBeGreaterThan(5)

      if (kind === 'power') {
        // NEW spec: power (1.10×) out-rates the no-star player (1.05×).
        expect(r.enemyCd).toBeLessThan(r.playerCd)
        expect(r.enemyShots).toBeGreaterThanOrEqual(r.playerShots - 1)
        // Power's surplus shells are absorbed by the player's shield, so the
        // enemy need not die in this isolated ordering test.
        expect(r.enemyDied).toBe(false)
      } else {
        // basic / fast / armor do NOT out-rate the no-star player.
        expect(r.playerCd).toBeLessThanOrEqual(r.enemyCd)
        expect(r.playerShots).toBeGreaterThanOrEqual(r.enemyShots - 1)
        // When the player is strictly faster, its surplus shells must land:
        // the enemy dies (proves the surplus really gets through, not a
        // fake stalemate). fast ties ⇒ no kill asserted.
        if (r.enemyCd > r.playerCd) {
          expect(r.enemyDied).toBe(true)
        }
      }
    })
  }

  it('max-level player out-rates even the power enemy (behavioral)', () => {
    // Promote the player to 3 stars and re-run the power duel; the player must
    // now fire strictly more often than power.
    const world = new World()
    world.rng = new RNG(99)
    const input = new Input()
    const sim = new Simulation(world, input)
    world.startGame('hard', 'modern', 0)

    world.spawnQueue.length = 0
    const player = world.player!
    // Promote the player to level 3 directly: the duels above use the level-0
    // player; here we prove a MAX-level player out-rates even power. Only the
    // cadence fields matter for shot counts (tryFire reads `tank.level`).
    player.level = 3
    const base3 = baseFireIntervalMs('player', 3)
    player.fireCooldown = base3
    player.nextFireInterval = base3
    player.spawnTimer = 0
    player.shieldTimer = 1e9
    for (let r = 0; r <= 25; r++) {
      world.tileMap.set(8, r, 'empty')
      world.tileMap.set(9, r, 'empty')
    }
    const ex = player.x
    const ey = CELL
    const enemy = world.createTank('power', ex, ey, 'down')
    enemy.spawnTimer = 0
    if (enemy.aiState) enemy.aiState.isCommander = true
    world.tanks.push(enemy)
    ;(
      input as unknown as { onKeyDown: (e: { code: string; preventDefault: () => void }) => void }
    ).onKeyDown({ code: input.keys.fire, preventDefault: () => {} })
    const fire = (sim as unknown as { tryFire: (t: Tank) => void }).tryFire.bind(sim)

    let playerShots = 0
    let enemyShots = 0
    for (let t = 0; t < 6000; t++) {
      sim.tick()
      for (const ev of world.consumeEvents()) {
        if (ev.type === 'bullet_fired') {
          if (ev.bullet.isPlayer) playerShots++
          else enemyShots++
        }
      }
      enemy.x = ex
      enemy.y = ey
      enemy.dir = 'down'
      enemy.moving = false
      fire(enemy)
    }
    expect(player.fireCooldown).toBeLessThan(enemy.fireCooldown)
    expect(playerShots).toBeGreaterThan(enemyShots)
  })
})
