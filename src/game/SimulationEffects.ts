import {
  CELL,
  TANK,
  BULLET,
  FIELD,
  RESPAWN_SHIELD_MS,
  POWERUP_PICKUP_WINDOW_MS,
  POWERUP_PICKUP_END_DELAY_MS,
  STAGE_CLEAR_DELAY_MS,
} from '../constants'
import { stageClearScore } from '../config/score'
import { genId } from './World'
import type { Tank } from '../types'
import type { SimulationConstructor, SimulationCore } from './SimulationCore'

/**
 * True when every enemy in the list is either an "extra" (balance spawn,
 * §31 Phase 2 — outside the per-stage count) or already dead. Used by
 * `checkConditions` to decide stage clear. Indexed loop avoids allocating a
 * `.every()` closure every tick the stage-clear gate is evaluated (AGENTS §14.1).
 */
function allNonExtraEnemiesDead(tanks: Tank[]): boolean {
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (!t.isExtra && t.alive) return false
  }
  return true
}

/**
 * SimulationEffectsMixin — the Explosion System, the popups ticker, and the
 * Win/Lose Conditions (base destroyed, per-player death, life sharing, stage
 * clear with the bonus-collection window) plus the stage transitions.
 *
 * Composes onto {@link SimulationCore}. See `Simulation.ts` for the final
 * mixin order. Cross-mixin calls (sacrifice AoE) resolve to the stubs declared
 * on `SimulationCore`.
 */
export function SimulationEffectsMixin<TBase extends SimulationConstructor<SimulationCore>>(
  Base: TBase,
) {
  return class SimulationEffects extends Base {
    // ================================================================
    // Explosion System
    // ================================================================

    protected createExplosion(x: number, y: number, kind: 'small' | 'big'): void {
      const w = this.world
      const size = kind === 'big' ? TANK : BULLET * 2
      const maxTimer = kind === 'big' ? 500 : 200
      w.addExplosion({
        id: genId(),
        x,
        y,
        size,
        timer: maxTimer,
        maxTimer,
        kind,
      })
      w.pushEvent({ type: 'explosion', x, y, kind })
    }

    protected updateExplosions(): void {
      const w = this.world
      // Indexed loop — `for...of` allocates an iterator every tick even when
      // explosions is empty (AGENTS §14.1). Mark _needsCleanup when any timer
      // reaches 0 so removeDeadEntities reclaims the slot (perf §67 — most ticks
      // have no explosions expiring, so this is a precise signal rather than
      // always-on).
      const exps = w.explosions
      const step = 1000 / 60
      for (let i = 0; i < exps.length; i++) {
        const t = exps[i].timer - step
        exps[i].timer = t
        if (t <= 0) w._needsCleanup = true
      }
    }

    protected updatePopups(): void {
      const w = this.world
      const popups = w.popups
      const step = 1000 / 60
      for (let i = 0; i < popups.length; i++) {
        const t = popups[i].timer - step
        popups[i].timer = t
        if (t <= 0) w._needsCleanup = true
      }
    }

    // ================================================================
    // Win/Lose Conditions
    // ================================================================

    protected checkConditions(): void {
      const w = this.world

      // Base destroyed = game over
      if (w.tileMap.isBaseDestroyed()) {
        w.state = 'gameover'
        w.gameOverTimer = 3000
        // Lie-Back-Win Q4 + 督战: coop/spectate runs never save high scores.
        if (!w.coop && !w.spectate) w.saveHighScore()
        this.createExplosion(FIELD / 2, FIELD - CELL * 2, 'big')
        return
      }

      // --- Per-player death handling (Lie-Back-Win-Mode §3.2) ---
      // Process each player independently, then check life-sharing.
      const p1Dead = w.player && !w.player.alive
      const p2Dead = w.coop && w.player2 && !w.player2.alive

      if (p1Dead) {
        this.triggerSacrificeAoE(w.player!)
        // Cancel frenzy for this player (per-tank frenzy, §7.1 Q9)
        w.player!.frenzyTimer = 0
        w.player!.frenzyShotsLeft = 0
        w.lives--
        if (w.lives > 0) {
          w.playerLevel = w.difficulty.playerStartLevel
          w.spawnPlayer()
          w.player!.shieldTimer = RESPAWN_SHIELD_MS
        }
      }

      if (p2Dead) {
        this.triggerSacrificeAoE(w.player2!)
        w.player2!.frenzyTimer = 0
        w.player2!.frenzyShotsLeft = 0
        w.lives2--
        if (w.lives2 > 0) {
          w.playerLevel2 = w.difficulty.playerStartLevel
          w.spawnPlayer2()
          w.player2!.shieldTimer = RESPAWN_SHIELD_MS
        }
      }

      // --- Life sharing (§3.2): if one player is out and the other has > 2 lives ---
      if (w.coop) {
        // Player out, God has lives to share
        if (w.lives <= 0 && !w.player?.alive && w.lives2 > 2) {
          w.lives2--
          w.lives = 1
          w.playerLevel = w.difficulty.playerStartLevel
          w.spawnPlayer()
          w.player!.shieldTimer = RESPAWN_SHIELD_MS
        }
        // God out, player has lives to share
        if (w.lives2 <= 0 && !w.player2?.alive && w.lives > 2) {
          w.lives--
          w.lives2 = 1
          w.playerLevel2 = w.difficulty.playerStartLevel
          w.spawnPlayer2()
          w.player2!.shieldTimer = RESPAWN_SHIELD_MS
        }
        // Game over: both players dead and cannot share
        // Lie-Back-Win-Mode Q4: coop games never save high scores.
        const bothDead = w.lives <= 0 && !w.player?.alive && w.lives2 <= 0 && !w.player2?.alive
        if (bothDead) {
          w.state = 'gameover'
          w.gameOverTimer = 3000
          return
        }
      } else {
        // Single-player game over (original logic)
        if (w.lives <= 0) {
          w.state = 'gameover'
          w.gameOverTimer = 3000
          // Lie-Back-Win Q4 + 督战: coop/spectate runs never save high scores.
          if (!w.coop && !w.spectate) w.saveHighScore()
          return
        }
      }

      // Stage clear — all (non-extra) enemies defeated. Accompanying "balance"
      // enemies (isExtra) are outside the per-stage count and must NOT block
      // stage clear (§31 Phase 2).
      if (w.enemiesRemaining <= 0 && allNonExtraEnemiesDead(w.tanks)) {
        const hasAlivePowerUp = w.powerUps.some((p) => p.alive)

        if (!hasAlivePowerUp) {
          // Nothing left to collect → end the stage.
          // If we were collecting bonuses, the "1s after pickup" rule applies;
          // otherwise this is the normal immediate clear.
          w.state = 'stageclear'
          w.stageClearTimer = w.pickupWindowEntered
            ? POWERUP_PICKUP_END_DELAY_MS
            : STAGE_CLEAR_DELAY_MS
          w.pickupWindowTimer = 0
          w.pickupWindowEntered = false
          w.score += stageClearScore(w.stageIndex, w.rules)
          w.pushEvent({ type: 'stage_clear', stage: w.stageIndex })
          return
        }

        // Power-ups remain on the field.
        if (!w.pickupWindowEntered) {
          // Begin the bonus-collection window (once). Stay in 'playing' so the
          // player can still move and pick items up.
          w.pickupWindowEntered = true
          w.pickupWindowTimer = POWERUP_PICKUP_WINDOW_MS
          w.addPopup({
            id: genId(),
            x: FIELD / 2 - TANK / 2,
            y: FIELD / 2 - TANK,
            text: 'BONUS TIME!',
            timer: 1800,
          })
          return
        }

        // Window already running: if it has elapsed, end the stage (timeout).
        if (w.pickupWindowTimer <= 0) {
          w.state = 'stageclear'
          w.stageClearTimer = POWERUP_PICKUP_END_DELAY_MS
          w.pickupWindowEntered = false
          w.score += stageClearScore(w.stageIndex, w.rules)
          w.pushEvent({ type: 'stage_clear', stage: w.stageIndex })
          return
        }
        // Window active with items still unclaimed → keep playing.
      }
    }

    // ================================================================
    // Stage Transition
    // ================================================================

    protected updateStageClear(): void {
      const w = this.world
      w.stageClearTimer -= 1000 / 60
      if (w.stageClearTimer <= 0) {
        w.loadStage(w.stageIndex + 1)
      }
      this.updateExplosions()
      this.updatePopups()
    }

    protected updateGameOver(): void {
      const w = this.world
      w.gameOverTimer -= 1000 / 60
      this.updateExplosions()
      this.updatePopups()
    }
  }
}
