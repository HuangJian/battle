import type { World } from '../game/World'
import type { GameSnapshot, SnapshotID } from './types'
import type { SnapshotManager } from './SnapshotManager'

// ================================================================
// Failure Recovery (plan §11)
//
//   Mission Failed → Pause Simulation → Recovery Menu
//
// The controller owns the recovery flow (menu → fade → restore →
// countdown) and delegates every snapshot decision to SnapshotManager.
// It mutates the World only by atomic restoration / stage restart —
// never by participating in gameplay rules (AGENTS §2.1).
// ================================================================

export type RecoveryPhase = 'idle' | 'menu' | 'fading' | 'countdown'

/** Recovery menu options, in display order (plan §11). */
export const RECOVERY_OPTIONS = [
  'continue',
  'loadLatest',
  'replayStage',
  'restartStage',
  'chooseSnapshot',
] as const
export type RecoveryOption = (typeof RECOVERY_OPTIONS)[number]
export const RECOVERY_OPTION_COUNT = RECOVERY_OPTIONS.length

/** Fade-to-black duration before restoring a snapshot. */
const FADE_DURATION_MS = 500
/** Duration of each countdown number (3, 2, 1). */
const COUNTDOWN_STEP_MS = 800
/** Total countdown steps. */
const COUNTDOWN_STEPS = 3

/** What Game should do after a menu selection. */
export type RecoverySelectResult =
  | { kind: 'none' } // option unavailable — nothing happened
  | { kind: 'continue' } // accept defeat → classic game over
  | { kind: 'browse' } // open the Snapshot Browser
  | { kind: 'transition' } // fade → restore/restart → countdown

export class RecoveryController {
  phase: RecoveryPhase = 'idle'

  private manager: SnapshotManager
  private fadeTimer = 0
  private countdownTimer = 0
  private countdownValue = COUNTDOWN_STEPS
  private pendingSnap: GameSnapshot | null = null
  /** When set, the fade ends in a fresh stage restart instead of a restore. */
  private pendingRestart = false

  constructor(manager: SnapshotManager) {
    this.manager = manager
  }

  // ================================================================
  // Entry points
  // ================================================================

  /** Begin the recovery flow after a Mission Failed. */
  start(world: World): void {
    this.phase = 'menu'
    this.resetTransition()
    world.state = 'recovery'
    world.recoveryCursor = 0
    world.recoveryCountdown = 0
    world.recoveryFading = false
  }

  /**
   * Load a specific snapshot (Snapshot Browser → Load). Works from any
   * state — menu, pause, recovery — via the same fade/countdown flow.
   */
  beginLoad(id: SnapshotID, world: World): boolean {
    const snap = this.manager.get(id)
    if (!snap) return false
    this.pendingSnap = snap
    this.pendingRestart = false
    this.beginTransition(world)
    return true
  }

  /** Player picked a recovery menu option. */
  select(option: RecoveryOption, world: World): RecoverySelectResult {
    if (!this.isOptionAvailable(option, world)) return { kind: 'none' }

    switch (option) {
      case 'continue': {
        // Accept defeat — hand back to the classic game-over screen.
        this.phase = 'idle'
        world.state = 'gameover'
        world.recoveryFading = false
        world.recoveryCountdown = 0
        return { kind: 'continue' }
      }
      case 'loadLatest': {
        const snap = this.manager.pickRecoverySnapshot()
        if (!snap) return { kind: 'none' }
        this.pendingSnap = snap
        this.pendingRestart = false
        this.beginTransition(world)
        return { kind: 'transition' }
      }
      case 'replayStage': {
        const snap = this.manager.latest({ type: 'stage-start', stage: world.stageIndex })
        if (!snap) return { kind: 'none' }
        this.pendingSnap = snap
        this.pendingRestart = false
        this.beginTransition(world)
        return { kind: 'transition' }
      }
      case 'restartStage': {
        // Fresh restart — no snapshot is loaded (plan §11).
        this.pendingSnap = null
        this.pendingRestart = true
        this.beginTransition(world)
        return { kind: 'transition' }
      }
      case 'chooseSnapshot': {
        return { kind: 'browse' }
      }
    }
  }

  isOptionAvailable(option: RecoveryOption, world: World): boolean {
    switch (option) {
      case 'continue':
      case 'restartStage':
        return true
      case 'loadLatest':
      case 'chooseSnapshot':
        return this.manager.count() > 0
      case 'replayStage':
        return this.manager.latest({ type: 'stage-start', stage: world.stageIndex }) !== null
    }
  }

  /**
   * Manual "时光宝盒" rewind (new-powerups-plan §4.3): start the same
   * fade→restore→countdown flow as Load Latest, but triggered by the player
   * mid-game (F7) with a stock charge, NOT by a Mission Failed.
   *
   * Returns false (and changes nothing) when:
   *  - the controller is already busy (phase !== 'idle'), or
   *  - the world is not in active play, or
   *  - no rewindable snapshot exists (auto / stage-start).
   * The caller (Game.ts) consumes the `rewindPending` flag and refunds the
   * spent stock charge on a false return.
   */
  beginManualRewind(world: World): boolean {
    if (this.phase !== 'idle' || world.state !== 'playing') return false
    const snap = this.manager.pickRewindSnapshot(world)
    if (!snap) return false
    this.pendingSnap = snap
    this.pendingRestart = false
    this.beginTransition(world)
    return true
  }

  // ================================================================
  // Flow (fade → apply → countdown)
  // ================================================================

  /** Advance the flow. Call every frame while world.state === 'recovery'. */
  update(world: World, dt: number): void {
    switch (this.phase) {
      case 'fading': {
        this.fadeTimer += dt
        if (this.fadeTimer >= FADE_DURATION_MS) {
          if (this.pendingRestart) {
            // Restart Without Loading — a clean stage start.
            world.startGame(world.difficultyKey, world.themeKey, world.stageIndex)
          } else if (this.pendingSnap) {
            this.manager.restore(this.pendingSnap.id, world)
          }
          this.pendingSnap = null
          this.pendingRestart = false
          // Countdown — but keep the world suspended until it finishes.
          world.state = 'recovery'
          this.phase = 'countdown'
          this.countdownTimer = 0
          this.countdownValue = COUNTDOWN_STEPS
          world.recoveryFading = false
          world.recoveryCountdown = this.countdownValue
        }
        break
      }
      case 'countdown': {
        this.countdownTimer += dt
        if (this.countdownTimer >= COUNTDOWN_STEP_MS) {
          this.countdownTimer = 0
          this.countdownValue--
          if (this.countdownValue <= 0) {
            world.recoveryCountdown = 0
            world.state = 'playing'
            this.phase = 'idle'
          } else {
            world.recoveryCountdown = this.countdownValue
          }
        }
        break
      }
      // 'menu' and 'idle' need no per-frame updates
    }
  }

  /** Full reset (returning to menu). */
  reset(): void {
    this.phase = 'idle'
    this.resetTransition()
  }

  // ================================================================
  // Queries
  // ================================================================

  isMenuPhase(): boolean {
    return this.phase === 'menu'
  }

  // ================================================================
  // Internal
  // ================================================================

  private beginTransition(world: World): void {
    this.phase = 'fading'
    this.fadeTimer = 0
    world.state = 'recovery'
    world.recoveryFading = true
    world.recoveryCountdown = 0
  }

  private resetTransition(): void {
    this.fadeTimer = 0
    this.countdownTimer = 0
    this.countdownValue = COUNTDOWN_STEPS
    this.pendingSnap = null
    this.pendingRestart = false
  }
}
