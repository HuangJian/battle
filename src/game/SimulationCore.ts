import type { World } from './World'
import type { Tank, PowerUpType } from '../types'
import type { InputLike } from './Input'
import { TacticalIntelligence } from '../ai/TacticalIntelligence'
import { TICK_MS, RESPAWN_SHIELD_MS } from '../constants'
import { computePlayer2SpawnCol } from '../utils/helpers'

/** Constructor type for the Simulation mixin chain (base = SimulationCore). */
export type SimulationConstructor<T = SimulationCore> = new (...args: any[]) => T

/**
 * Simulation — the only layer allowed to modify the World (base layer).
 *
 * Holds every field plus the cross-cutting wiring: the constructor, the
 * deferred coop/spectate toggles, `tick` (state dispatch), the `updatePlaying`
 * orchestrator, and `togglePause`. The six subsystem mixins — SimulationSpawn,
 * SimulationPlayer, SimulationEnemies, SimulationCombat, SimulationPowerUps,
 * SimulationEffects — provide the private update methods stubbed at the bottom
 * of this class; `Simulation.ts` composes them into the final `Simulation`.
 *
 * The stubs exist so that cross-mixin calls (and the `updatePlaying`
 * orchestrator) type-check against this base. The composed `Simulation`
 * always installs every mixin, so a stub is never reached at runtime; throwing
 * loudly if one IS reached makes a broken composition fail fast instead of
 * silently no-op'ing.
 */
export class SimulationCore {
  world: World
  input: InputLike
  /** Lie-Back-Win-Mode: second player input (God AI or null). */
  input2: InputLike | null = null
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
  /** Tactical Intelligence Framework — owns all enemy decision-making. */
  protected ai: TacticalIntelligence

  constructor(world: World, input: InputLike) {
    this.world = world
    this.input = input
    this.ai = new TacticalIntelligence()
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
   * 督战 (supervise) mode: cancel any pending spectate toggle. Called when
   * returning to menu — a stale pending toggle would otherwise fire on the
   * next playing tick and re-enable spectate against the player's intent.
   */
  clearPendingSpectateToggle(): void {
    this.pendingSpectateToggle = null
    this.pendingSpectateDual = null
  }

  /** Run one simulation tick (1/60s) */
  tick(): void {
    const w = this.world
    w.frame++

    if (w.state === 'playing') {
      this.updatePlaying()
    } else if (w.state === 'stageclear') {
      this.updateStageClear()
    } else if (w.state === 'gameover') {
      this.updateGameOver()
    }
  }

  // ================================================================
  // Playing state
  // ================================================================

  protected updatePlaying(): void {
    const w = this.world

    // Lie-Back-Win-Mode §3.5: apply deferred coop toggle at tick start.
    if (this.pendingCoopToggle !== null) {
      const enable = this.pendingCoopToggle
      this.pendingCoopToggle = null
      if (enable && !w.coop) {
        w.coop = true
        const d = w.difficulty
        w.lives2 = d?.startLives ?? 3
        w.playerLevel2 = d?.playerStartLevel ?? 0
        const p1Col = w.playerSpawnPoint?.col ?? 8
        w.player2SpawnPoint = { col: computePlayer2SpawnCol(p1Col), row: 24 }
        w.spawnPlayer2()
        w.player2!.shieldTimer = RESPAWN_SHIELD_MS
      } else if (!enable && w.coop) {
        w.coop = false
        w.player2 = null
        w.lives2 = 0
        w.playerLevel2 = 0
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
      if (dual) {
        // 督战双玩家: ensure player2 exists — startGame/loadStage wipe it.
        if (!w.player2) {
          const d = w.difficulty
          w.lives2 = d?.startLives ?? 3
          w.playerLevel2 = d?.playerStartLevel ?? 0
          const p1Col = w.playerSpawnPoint?.col ?? 8
          w.player2SpawnPoint = { col: computePlayer2SpawnCol(p1Col), row: 24 }
          w.spawnPlayer2()
          w.player2!.shieldTimer = RESPAWN_SHIELD_MS
        }
      } else if (w.player2 && !w.coop) {
        // Dual switched off (or spectate off) and co-op doesn't own P2 → remove it.
        w.player2 = null
        w.lives2 = 0
        w.playerLevel2 = 0
      }
    }

    // Run statistics — total play time advances only while playing.
    w.playTimeMs += 1000 / 60

    // Update timers
    if (w.freezeTimer > 0) w.freezeTimer -= 1000 / 60
    if (w.empTimer > 0) w.empTimer -= 1000 / 60
    if (w.spawnTimer > 0) w.spawnTimer -= 1000 / 60
    if (w.pickupWindowTimer > 0) w.pickupWindowTimer -= TICK_MS

    // Update mine arm timers (indexed loop — AGENTS §14.1)
    // (perf §68) Skip the loop when no mines exist — classic mode rarely
    // uses mines, so this saves the per-tick array-length + property probe.
    if (w._hasActiveMines) {
      const armMines = w.mines
      for (let mi = 0; mi < armMines.length; mi++) {
        const mine = armMines[mi]
        if (mine.alive && mine.armTimer > 0) {
          mine.armTimer -= 1000 / 60
          if (mine.armTimer < 0) mine.armTimer = 0
        }
      }
    }

    // Update player boat timers (both players)
    if (w.player && w.player.boatTimer && w.player.boatTimer > 0) {
      w.player.boatTimer -= 1000 / 60
      if (w.player.boatTimer < 0) w.player.boatTimer = 0
    }
    if (w.player2 && w.player2.boatTimer && w.player2.boatTimer > 0) {
      w.player2.boatTimer -= 1000 / 60
      if (w.player2.boatTimer < 0) w.player2.boatTimer = 0
    }

    // Update spawn animations
    this.updateSpawnTimers()

    // Spawn enemies
    this.updateSpawning()

    // Player input (both players)
    this.updatePlayerTank(w.player, this.input)
    this.updatePlayerTank(w.player2, this.input2)

    // Enemy AI
    this.updateEnemyAI()

    // Allied guard AI (天降神兵, §31 Phase 2) — runs before movement so the
    // guard's intent (dir/moving) is applied this tick.
    this.updateGuards()

    // Fence power-up: revert steel ring to brick when its timer expires.
    this.updateFence()

    // Movement
    this.updateMovement()

    // Bullets
    this.updateBullets()

    // Power-ups
    this.updatePowerUps()

    // Mine collision detection (after bullets, before cleanup)
    this.updateMines()

    // Explosions & popups
    this.updateExplosions()
    this.updatePopups()

    // Check game conditions
    this.checkConditions()

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

  // ------------------------------------------------------------------
  // Mixin-provided methods (stubs).
  //
  // The subsystem mixins (SimulationSpawn / SimulationPlayer /
  // SimulationEnemies / SimulationCombat / SimulationPowerUps /
  // SimulationEffects) override these. The stubs exist so that cross-mixin
  // calls (and the `updatePlaying` orchestrator) type-check against this
  // base class; the composed `Simulation` in Simulation.ts always installs
  // every mixin, so a stub is never reached at runtime. Throwing loudly if
  // one IS reached makes a broken composition fail fast instead of silently
  // no-op'ing.
  // ------------------------------------------------------------------

  protected updateSpawnTimers(): void {
    throw new Error(
      'SimulationCore stub: updateSpawnTimers() must be provided by SimulationSpawnMixin',
    )
  }

  protected updateSpawning(): void {
    throw new Error(
      'SimulationCore stub: updateSpawning() must be provided by SimulationSpawnMixin',
    )
  }

  protected updatePlayerTank(_p: Tank | null, _input: InputLike | null): void {
    throw new Error(
      'SimulationCore stub: updatePlayerTank() must be provided by SimulationPlayerMixin',
    )
  }

  protected updateEnemyAI(): void {
    throw new Error(
      'SimulationCore stub: updateEnemyAI() must be provided by SimulationEnemiesMixin',
    )
  }

  protected updateGuards(): void {
    throw new Error(
      'SimulationCore stub: updateGuards() must be provided by SimulationEnemiesMixin',
    )
  }

  protected activateGuard(_p: Tank): void {
    throw new Error(
      'SimulationCore stub: activateGuard() must be provided by SimulationEnemiesMixin',
    )
  }

  protected triggerSacrificeAoE(_player: Tank): void {
    throw new Error(
      'SimulationCore stub: triggerSacrificeAoE() must be provided by SimulationEnemiesMixin',
    )
  }

  protected updateMovement(): void {
    throw new Error(
      'SimulationCore stub: updateMovement() must be provided by SimulationCombatMixin',
    )
  }

  protected tryFire(_tank: Tank): void {
    throw new Error('SimulationCore stub: tryFire() must be provided by SimulationCombatMixin')
  }

  protected updateBullets(): void {
    throw new Error(
      'SimulationCore stub: updateBullets() must be provided by SimulationCombatMixin',
    )
  }

  protected updateMines(): void {
    throw new Error('SimulationCore stub: updateMines() must be provided by SimulationPlayerMixin')
  }

  protected activateDecoy(_p: Tank): void {
    throw new Error(
      'SimulationCore stub: activateDecoy() must be provided by SimulationPlayerMixin',
    )
  }

  protected placeMine(_p: Tank): void {
    throw new Error('SimulationCore stub: placeMine() must be provided by SimulationPlayerMixin')
  }

  protected updateFence(): void {
    throw new Error(
      'SimulationCore stub: updateFence() must be provided by SimulationPowerUpsMixin',
    )
  }

  protected updatePowerUps(): void {
    throw new Error(
      'SimulationCore stub: updatePowerUps() must be provided by SimulationPowerUpsMixin',
    )
  }

  protected buildDrop(_at?: { x: number; y: number }): { type: PowerUpType; x: number; y: number } {
    throw new Error('SimulationCore stub: buildDrop() must be provided by SimulationPowerUpsMixin')
  }

  protected spawnPowerUp(_at?: { x: number; y: number }): void {
    throw new Error(
      'SimulationCore stub: spawnPowerUp() must be provided by SimulationPowerUpsMixin',
    )
  }

  protected flushPendingDrops(): void {
    throw new Error(
      'SimulationCore stub: flushPendingDrops() must be provided by SimulationPowerUpsMixin',
    )
  }

  protected createExplosion(_x: number, _y: number, _kind: 'small' | 'big'): void {
    throw new Error(
      'SimulationCore stub: createExplosion() must be provided by SimulationEffectsMixin',
    )
  }

  protected updateExplosions(): void {
    throw new Error(
      'SimulationCore stub: updateExplosions() must be provided by SimulationEffectsMixin',
    )
  }

  protected updatePopups(): void {
    throw new Error(
      'SimulationCore stub: updatePopups() must be provided by SimulationEffectsMixin',
    )
  }

  protected checkConditions(): void {
    throw new Error(
      'SimulationCore stub: checkConditions() must be provided by SimulationEffectsMixin',
    )
  }

  protected updateStageClear(): void {
    throw new Error(
      'SimulationCore stub: updateStageClear() must be provided by SimulationEffectsMixin',
    )
  }

  protected updateGameOver(): void {
    throw new Error(
      'SimulationCore stub: updateGameOver() must be provided by SimulationEffectsMixin',
    )
  }
}
