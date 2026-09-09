import type { World } from './World'
import type { InputLike } from './Input'
import { TacticalIntelligence } from '../ai/TacticalIntelligence'
import { TICK_MS } from '../constants'
import { SpawnSystem } from './SimulationSpawn'
import { PlayerSystem } from './SimulationPlayer'
import { EnemiesSystem } from './SimulationEnemies'
import { CombatSystem } from './SimulationCombat'
import { PowerUpSystem } from './SimulationPowerUps'
import { EffectsSystem } from './SimulationEffects'
import type { SimulationSystems } from './systems'

/**
 * Simulation — the only layer allowed to modify the World. Runs all game
 * systems in a fixed timestep.
 *
 * Composition, not inheritance (plan/refactor.agy.md §1.1): the former
 * six-mixin chain + 21 throwing stubs became six explicit subsystem classes
 * wired through one {@link SimulationSystems} registry:
 *
 * - {@link SpawnSystem}   — spawn timers + queue-driven spawning.
 * - {@link PlayerSystem}  — player input, frenzy, decoy, mines.
 * - {@link EnemiesSystem} — allied guards, sacrifice AoE, enemy AI.
 * - {@link CombatSystem}  — movement, fire, bullets, base damage.
 * - {@link PowerUpSystem} — drops, pickups, fence/boat/repair.
 * - {@link EffectsSystem} — explosions, popups, win/lose conditions.
 *
 * The registry is populated immediately after construction; systems only
 * dereference siblings when their methods RUN, so dependency cycles
 * (Player↔PowerUps, Enemies↔Effects) need no setters. An agent adding a new
 * subsystem creates a class, adds it to `SimulationSystems`, constructs it
 * here, and calls it from `updatePlaying` at the right position.
 */
export class Simulation {
  world: World
  input: InputLike
  /** Lie-Back-Win-Mode: second player input (God AI or null). */
  input2: InputLike | null = null

  /** Shared inter-system registry (see systems.ts). */
  private readonly s: SimulationSystems

  /**
   * White-box access for tests/diagnostics — replaces the old mixin pattern of
   * casting `sim as unknown as { privateMethod }`. Composition makes the
   * subsystem boundary explicit; tests name the system they are exercising.
   */
  get systems(): SimulationSystems {
    return this.s
  }

  /**
   * Lie-Back-Win-Mode §3.5: pending coop toggle, set by Game.ts.
   * Applied at the start of updatePlaying() so the One-Author invariant
   * (only Simulation mutates World) is preserved.
   */
  private pendingCoopToggle: boolean | null = null
  /**
   * 督战 (supervise) mode: pending spectate toggle, set by Game.ts.
   * Same deferred-application contract as pendingCoopToggle.
   */
  private pendingSpectateToggle: boolean | null = null
  /**
   * 督战双玩家 (dual supervise): pending dual flag, applied together with
   * `pendingSpectateToggle` on the next playing tick. Without this, the
   * `spectateDual` World flag — reset by `startGame`/`loadStageData` — would
   * not survive a real game start, silently degrading dual mode to single.
   */
  private pendingSpectateDual: boolean | null = null
  /**
   * 双打 Two-Player mode: pending toggle, same deferred-application contract
   * as pendingCoopToggle. Carries the toggle through `startGame` (which resets
   * `world.twoPlayer`) and applies on the first playing tick — identical to
   * how coop survives a menu-time enable.
   */
  private pendingTwoPlayerToggle: boolean | null = null

  constructor(world: World, input: InputLike) {
    const s = {} as SimulationSystems
    s.world = world
    s.spawn = new SpawnSystem(s)
    s.player = new PlayerSystem(s)
    s.enemies = new EnemiesSystem(s)
    s.combat = new CombatSystem(s)
    s.powerUps = new PowerUpSystem(s)
    s.effects = new EffectsSystem(s)
    // Tactical Intelligence Framework — owns all enemy decision-making.
    s.ai = new TacticalIntelligence()
    this.s = s
    this.world = world
    this.input = input
  }

  /**
   * Lie-Back-Win-Mode §3.5: request a coop toggle (called by Game.ts).
   * The actual World mutation is deferred to updatePlaying() to preserve
   * the One-Author invariant.
   */
  requestCoopToggle(on: boolean): void {
    this.pendingCoopToggle = on
  }

  /**
   * One-Author routing (refactor.trae.md §4.1): takeover flows must flip the
   * gameplay flag `coop` through the Simulation, not by direct World writes
   * from Game controllers. Applied immediately (the callers are mid-handoff,
   * not inside a tick) — same semantics as the previous direct writes.
   */
  applyTakeover(coop: boolean): void {
    this.world.coop = coop
  }

  /** One-Author routing (§4.1): consume the pending manual-rewind flag. */
  clearRewindPending(): void {
    this.world.rewindPending = false
    // rewindPendingBy is deliberately NOT reset here — GameLoop calls this
    // BEFORE deciding whether the rewind can start, and a failed start
    // refunds through refundRewind() right after, which needs the flag to
    // know whose inventory to return the charge to. It is refreshed on every
    // activateRewind, so a stale value is never observable.
  }

  /** One-Author routing (§4.1): refund a rewind stock charge (rewind could
   * not start — recovery busy or not playing). Returned to the SAME
   * player's inventory that paid for it (rewindPendingBy, set by
   * activateRewind — superStocks.ts per-player inventories). */
  refundRewind(): void {
    if (this.world.rewindPendingBy === 2) this.world.rewindStock2++
    else this.world.rewindStock++
  }

  /**
   * Lie-Back-Win-Mode §3.5: cancel any pending coop toggle. Called when
   * returning to menu — a stale pending toggle would otherwise fire on the
   * next playing tick and re-enable coop against the player's intent.
   */
  clearPendingCoopToggle(): void {
    this.pendingCoopToggle = null
  }

  /**
   * 督战 (supervise) mode: request a spectate toggle (called by Game.ts).
   * The actual World mutation is deferred to updatePlaying() to preserve
   * the One-Author invariant.
   */
  requestSpectateToggle(on: boolean): void {
    this.pendingSpectateToggle = on
  }

  /** 督战双玩家 (dual supervise): request a deferred dual flag (see above). */
  requestSpectateDualToggle(on: boolean): void {
    this.pendingSpectateDual = on
  }

  /**
   * 双打 Two-Player mode: request a toggle (called by Game.ts). The actual
   * World mutation is deferred to updatePlaying() to preserve One-Author.
   */
  requestTwoPlayerToggle(on: boolean): void {
    this.pendingTwoPlayerToggle = on
  }

  /**
   * One-Author routing (§4.1): immediate twoPlayer flag flip for mid-handoff
   * callers (menu/paused, no tick will fire) — same semantics as
   * {@link applyTakeover}.
   */
  applyTwoPlayer(on: boolean): void {
    this.world.twoPlayer = on
  }

  /**
   * 督战 (supervise) mode: cancel any pending spectate toggle. Called when
   * returning to menu — a stale pending toggle would otherwise fire on the
   * next playing tick and re-enable spectate against the player's intent.
   */
  clearPendingSpectateToggle(): void {
    this.pendingSpectateToggle = null
    this.pendingSpectateDual = null
  }

  /**
   * 双打 Two-Player mode: cancel any pending toggle (returning to menu —
   * same stale-pending hazard as coop/spectate).
   */
  clearPendingTwoPlayerToggle(): void {
    this.pendingTwoPlayerToggle = null
  }

  /** Run one simulation tick (1/60s) */
  tick(): void {
    const w = this.world
    w.frame++

    if (w.state === 'playing') {
      this.updatePlaying()
    } else if (w.state === 'stageclear') {
      this.s.effects.updateStageClear()
    } else if (w.state === 'gameover') {
      this.s.effects.updateGameOver()
    }
  }

  // ================================================================
  // Playing state
  // ================================================================

  private updatePlaying(): void {
    const w = this.world
    const s = this.s

    // 双打 Two-Player: apply deferred toggle at tick start — BEFORE the coop
    // /spectate applies below so mode handoffs converge regardless of the
    // order the pendings were requested in (each enable strips the other
    // modes' flags; the shared P2 slot keeps exactly one owner).
    if (this.pendingTwoPlayerToggle !== null) {
      const enable = this.pendingTwoPlayerToggle
      this.pendingTwoPlayerToggle = null
      if (enable && !w.twoPlayer) {
        w.coop = false
        w.spectate = false
        w.spectateDual = false
        w.twoPlayer = true
        if (!w.player2) w.enablePlayer2({ respawnShield: true })
      } else if (!enable && w.twoPlayer) {
        w.twoPlayer = false
        if (!w.coop && !w.spectateDual) w.disablePlayer2()
      }
    }

    // Lie-Back-Win-Mode §3.5: apply deferred coop toggle at tick start.
    if (this.pendingCoopToggle !== null) {
      const enable = this.pendingCoopToggle
      this.pendingCoopToggle = null
      if (enable && !w.coop) {
        w.coop = true
        // twoPlayer owns the same P2 slot — coop enable takes it over.
        w.twoPlayer = false
        w.enablePlayer2({ respawnShield: true })
      } else if (!enable && w.coop) {
        w.coop = false
        w.disablePlayer2()
      }
    }

    // 督战 (supervise) mode + 督战双玩家: apply deferred toggles at tick start.
    if (this.pendingSpectateToggle !== null || this.pendingSpectateDual !== null) {
      const enableSpectate = this.pendingSpectateToggle ?? w.spectate
      const dual = this.pendingSpectateDual ?? w.spectateDual
      this.pendingSpectateToggle = null
      this.pendingSpectateDual = null
      w.spectate = enableSpectate
      w.spectateDual = dual
      // Enabling any spectate form strips twoPlayer (same P2 slot owner).
      if (enableSpectate) w.twoPlayer = false
      if (dual) {
        // 督战双玩家: ensure player2 exists — startGame/loadStage wipe it.
        if (!w.player2) {
          w.enablePlayer2({ respawnShield: true })
        }
      } else if (w.player2 && !w.coop) {
        // Dual switched off (or spectate off) and co-op doesn't own P2 → remove it.
        w.disablePlayer2()
      }
    }

    // Run statistics — total play time advances only while playing.
    w.playTimeMs += TICK_MS

    // Update timers
    if (w.freezeTimer > 0) w.freezeTimer -= TICK_MS
    if (w.empTimer > 0) w.empTimer -= TICK_MS
    if (w.spawnTimer > 0) w.spawnTimer -= TICK_MS
    if (w.pickupWindowTimer > 0) w.pickupWindowTimer -= TICK_MS

    // Update mine arm timers (indexed loop — AGENTS §14.1)
    // (perf §68) Skip the loop when no mines exist — classic mode rarely
    // uses mines, so this saves the per-tick array-length + property probe.
    if (w._hasActiveMines) {
      const armMines = w.mines
      for (let mi = 0; mi < armMines.length; mi++) {
        const mine = armMines[mi]
        if (mine.alive && mine.armTimer > 0) {
          mine.armTimer -= TICK_MS
          if (mine.armTimer < 0) mine.armTimer = 0
        }
      }
    }

    // Update player boat timers (both players)
    if (w.player && w.player.boatTimer && w.player.boatTimer > 0) {
      w.player.boatTimer -= TICK_MS
      if (w.player.boatTimer < 0) w.player.boatTimer = 0
    }
    if (w.player2 && w.player2.boatTimer && w.player2.boatTimer > 0) {
      w.player2.boatTimer -= TICK_MS
      if (w.player2.boatTimer < 0) w.player2.boatTimer = 0
    }

    // Update spawn animations
    s.spawn.updateSpawnTimers()

    // Spawn enemies
    s.spawn.updateSpawning()

    // Player input (both players)
    s.player.updatePlayerTank(w.player, this.input)
    s.player.updatePlayerTank(w.player2, this.input2)

    // Enemy AI
    s.enemies.updateEnemyAI()

    // Allied guard AI (天降神兵, §31 Phase 2) — runs before movement so the
    // guard's intent (dir/moving) is applied this tick.
    s.enemies.updateGuards()

    // Fence power-up: revert steel ring to brick when its timer expires.
    s.powerUps.updateFence()

    // Movement
    s.combat.updateMovement()

    // Bullets
    s.combat.updateBullets()

    // Power-ups
    s.powerUps.updatePowerUps()

    // Mine collision detection (after bullets, before cleanup)
    s.player.updateMines()

    // Explosions & popups
    s.effects.updateExplosions()
    s.effects.updatePopups()

    // Check game conditions
    s.effects.checkConditions()

    // Cleanup
    w.removeDeadEntities()
  }

  // ================================================================
  // Pause
  // ================================================================

  togglePause(): void {
    const w = this.world
    if (w.state === 'playing') {
      w.state = 'paused'
    } else if (w.state === 'paused') {
      w.state = 'playing'
    }
  }
}
