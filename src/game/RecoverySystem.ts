import type { World, SpawnEntry } from './World'
import type { Tank, Bullet, PowerUp, TerrainType } from '../types'
import { GRID } from '../constants'

// ================================================================
// Snapshot Types
// ================================================================

/**
 * WorldSnapshot — a complete, self-contained description of the World
 * at a single point in time.  It exists entirely in memory and is used
 * by the Recovery System to restore gameplay state atomically.
 *
 * Design rules (from RecoverySystem.md §9, §12, §20):
 * - A snapshot is a *complete* description of the World.
 * - Never reconstruct missing state during restoration.
 * - Deep clone — no shared references with the live World.
 * - Transient visual data (explosions, popups, events) is excluded;
 *   Presentation rebuilds itself after restoration.
 */
export interface WorldSnapshot {
  // Terrain
  tileGrid: TerrainType[][]

  // Entities
  player: Tank | null
  tanks: Tank[]
  bullets: Bullet[]
  powerUps: PowerUp[]

  // Stage info
  stageIndex: number
  spawnQueue: SpawnEntry[]
  enemiesSpawned: number
  enemiesRemaining: number

  // Game state
  score: number
  lives: number
  playerLevel: number
  highScore: number

  // Timers
  freezeTimer: number
  stageClearTimer: number
  gameOverTimer: number
  spawnTimer: number

  // RNG state (for determinism / future replay)
  rngState: number

  // Simulation frame counter
  frame: number
}

// ================================================================
// Deep Clone Helpers
// ================================================================

function cloneTank(t: Tank): Tank {
  return {
    ...t,
    aiState: t.aiState ? { ...t.aiState } : undefined,
  }
}

function cloneBullet(b: Bullet): Bullet {
  return { ...b }
}

function clonePowerUp(p: PowerUp): PowerUp {
  return { ...p }
}

function cloneSpawnEntry(s: SpawnEntry): SpawnEntry {
  return { ...s }
}

/**
 * Deep-clone all gameplay-relevant state from the World into a
 * self-contained snapshot.  The snapshot shares no references with the
 * live World, so subsequent gameplay mutations never corrupt it.
 */
function cloneWorld(world: World): WorldSnapshot {
  // Tile grid — copy each row
  const tileGrid: TerrainType[][] = []
  for (let r = 0; r < GRID; r++) {
    tileGrid.push([...world.tileMap.grid[r]])
  }

  return {
    tileGrid,
    player: world.player ? cloneTank(world.player) : null,
    tanks: world.tanks.map(cloneTank),
    bullets: world.bullets.map(cloneBullet),
    powerUps: world.powerUps.map(clonePowerUp),
    stageIndex: world.stageIndex,
    spawnQueue: world.spawnQueue.map(cloneSpawnEntry),
    enemiesSpawned: world.enemiesSpawned,
    enemiesRemaining: world.enemiesRemaining,
    score: world.score,
    lives: world.lives,
    playerLevel: world.playerLevel,
    highScore: world.highScore,
    freezeTimer: world.freezeTimer,
    stageClearTimer: world.stageClearTimer,
    gameOverTimer: world.gameOverTimer,
    spawnTimer: world.spawnTimer,
    rngState: world.rng.getState(),
    frame: world.frame,
  }
}

/**
 * Restore the World from a snapshot.  Every gameplay field is
 * overwritten; transient visual data (explosions, popups, events) is
 * cleared.  The World object identity is preserved so that all
 * existing references (Game, Simulation, Presentation) remain valid.
 *
 * The snapshot itself is re-cloned during restoration so that it stays
 * immutable and can be reused for future recoveries.
 */
function restoreWorld(world: World, snap: WorldSnapshot): void {
  // Tile grid
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) {
      world.tileMap.grid[r][c] = snap.tileGrid[r][c]
    }
  }

  // Entities — clone from snapshot so the snapshot stays pristine
  world.player = snap.player ? cloneTank(snap.player) : null
  world.tanks = snap.tanks.map(cloneTank)
  world.bullets = snap.bullets.map(cloneBullet)
  world.powerUps = snap.powerUps.map(clonePowerUp)

  // Clear transient visual data — Presentation will rebuild
  world.explosions = []
  world.popups = []
  world.events = []

  // Stage info
  world.stageIndex = snap.stageIndex
  world.spawnQueue = snap.spawnQueue.map(cloneSpawnEntry)
  world.enemiesSpawned = snap.enemiesSpawned
  world.enemiesRemaining = snap.enemiesRemaining

  // Game state
  world.score = snap.score
  world.lives = snap.lives
  world.playerLevel = snap.playerLevel
  world.highScore = snap.highScore

  // Timers
  world.freezeTimer = snap.freezeTimer
  world.stageClearTimer = snap.stageClearTimer
  world.gameOverTimer = snap.gameOverTimer
  world.spawnTimer = snap.spawnTimer

  // RNG
  world.rng.reseed(snap.rngState)

  // Frame counter
  world.frame = snap.frame

  // Resume playing
  world.state = 'playing'
  // Reset game-over timer
  world.gameOverTimer = 0
}

// ================================================================
// Circular Buffer
// ================================================================

/**
 * Fixed-size circular buffer.  Used for history snapshots.
 *
 * When the buffer is full, the newest entry overwrites the oldest,
 * keeping memory usage constant.
 */
class CircularBuffer<T> {
  private buf: (T | null)[]
  private head = 0 // next write position
  private _size = 0
  readonly capacity: number

  constructor(capacity: number) {
    this.capacity = capacity
    this.buf = Array.from({ length: capacity }, () => null)
  }

  push(item: T): void {
    this.buf[this.head] = item
    this.head = (this.head + 1) % this.capacity
    if (this._size < this.capacity) this._size++
  }

  get size(): number {
    return this._size
  }

  /** Newest entry (most recently pushed), or null if empty. */
  newest(): T | null {
    if (this._size === 0) return null
    const idx = (this.head - 1 + this.capacity) % this.capacity
    return this.buf[idx]
  }

  /** Oldest entry (least recently pushed), or null if empty. */
  oldest(): T | null {
    if (this._size === 0) return null
    if (this._size < this.capacity) return this.buf[0]
    return this.buf[this.head] // head points to oldest when full
  }

  /**
   * Get entry at `offset` from newest.
   * offset 0 = newest, 1 = second newest, etc.
   * Returns null if offset >= size.
   */
  fromNewest(offset: number): T | null {
    if (offset < 0 || offset >= this._size) return null
    const idx = (this.head - 1 - offset + this.capacity * 2) % this.capacity
    return this.buf[idx]
  }

  clear(): void {
    this.buf.fill(null)
    this.head = 0
    this._size = 0
  }
}

// ================================================================
// Recovery Phases
// ================================================================

export type RecoveryPhase = 'idle' | 'menu' | 'fading' | 'countdown'

// ================================================================
// Recovery System
// ================================================================

/** Recording interval — one snapshot per second of gameplay. */
const HISTORY_INTERVAL_MS = 1000

/** Maximum number of history snapshots kept in the circular buffer. */
const HISTORY_MAX = 60

/** Fade-to-black duration before restoring a snapshot. */
const FADE_DURATION_MS = 500

/** Duration of each countdown number (3, 2, 1). */
const COUNTDOWN_STEP_MS = 800

/** Total countdown steps. */
const COUNTDOWN_STEPS = 3

/** Recovery menu options. */
export const RECOVERY_OPTION_30S = 0
export const RECOVERY_OPTION_60S = 1
export const RECOVERY_OPTION_RESTART = 2
export const RECOVERY_OPTION_COUNT = 3

/**
 * RecoverySystem — automatic state preservation & timeline recovery.
 *
 * Four logical modules (RecoverySystem.md §5):
 * 1. Snapshot Manager  — createStageSnapshot / restoreStage
 * 2. History Recorder   — recordHistory / circular buffer
 * 3. Recovery Controller — startRecovery / updateFlow / selectOption
 * 4. Recovery UI         — handled by UIManager (reads world fields)
 *
 * The Recovery System observes the World; it never participates in
 * gameplay logic.  Simulation and Presentation remain independent.
 */
export class RecoverySystem {
  // Snapshot Manager
  private stageSnap: WorldSnapshot | null = null

  // History Recorder
  private history: CircularBuffer<WorldSnapshot>
  private recordAccumulator = 0

  // Recovery Controller
  phase: RecoveryPhase = 'idle'
  private fadeTimer = 0
  private countdownTimer = 0
  private countdownValue = COUNTDOWN_STEPS
  private pendingSnap: WorldSnapshot | null = null

  constructor() {
    this.history = new CircularBuffer<WorldSnapshot>(HISTORY_MAX)
  }

  // ================================================================
  // Snapshot Manager
  // ================================================================

  /**
   * Create an immutable Stage Snapshot from the current World.
   * Called once when a stage begins.  Clears any previous history.
   */
  createStageSnapshot(world: World): void {
    this.stageSnap = cloneWorld(world)
    this.clearHistory()
  }

  /**
   * Record a History Snapshot of the current World into the circular
   * buffer.  Called automatically every second during gameplay.
   */
  recordHistory(world: World): void {
    this.history.push(cloneWorld(world))
  }

  /**
   * Restore the Stage Snapshot, returning the World to the beginning
   * of the current stage.  Returns false if no stage snapshot exists.
   */
  restoreStage(): boolean {
    if (!this.stageSnap) return false
    this.pendingSnap = this.stageSnap
    return true
  }

  /**
   * Restore a History Snapshot from approximately `seconds` seconds
   * ago.  If fewer snapshots are available than requested, the oldest
   * available snapshot is used.  Returns false if no history exists.
   */
  restoreHistory(seconds: number): boolean {
    if (this.history.size === 0) return false

    // Each snapshot is ~1 second apart.
    // offset 0 = newest (~1s ago), offset N = ~N+1 seconds ago.
    // We want the snapshot closest to `seconds` seconds ago.
    const offset = Math.max(0, seconds - 1)
    let snap = this.history.fromNewest(offset)

    // Fall back to oldest available if we don't have enough history
    if (!snap) {
      snap = this.history.oldest()
    }

    if (!snap) return false
    this.pendingSnap = snap
    return true
  }

  /** Remove all history snapshots. */
  clearHistory(): void {
    this.history.clear()
    this.recordAccumulator = 0
  }

  /** Full reset — clears stage snapshot, history, and recovery state. */
  reset(): void {
    this.stageSnap = null
    this.clearHistory()
    this.phase = 'idle'
    this.fadeTimer = 0
    this.countdownTimer = 0
    this.countdownValue = COUNTDOWN_STEPS
    this.pendingSnap = null
  }

  // ================================================================
  // History Recorder — automatic recording
  // ================================================================

  /**
   * Called every frame.  Accumulates real-time delta and records a
   * history snapshot once per second while the game is in 'playing'
   * state.  Recording is invisible — it never pauses gameplay.
   */
  updateRecording(world: World, dt: number): void {
    if (world.state !== 'playing') return
    this.recordAccumulator += dt
    if (this.recordAccumulator >= HISTORY_INTERVAL_MS) {
      this.recordAccumulator -= HISTORY_INTERVAL_MS
      this.recordHistory(world)
    }
  }

  // ================================================================
  // Recovery Controller — flow management
  // ================================================================

  /**
   * Begin the recovery flow.  Called when a failure is detected.
   * Sets the phase to 'menu' so the UI can display recovery options.
   */
  startRecovery(world: World): void {
    this.phase = 'menu'
    this.fadeTimer = 0
    this.countdownTimer = 0
    this.countdownValue = COUNTDOWN_STEPS
    this.pendingSnap = null
    world.state = 'recovery'
    world.recoveryCursor = 0
    world.recoveryCountdown = 0
    world.recoveryFading = false
  }

  /**
   * Player selected a recovery option.
   * Begins the fade-to-black transition.  The actual snapshot restore
   * happens after the fade completes (see updateFlow).
   */
  selectOption(option: number, world: World): boolean {
    let ok = false
    switch (option) {
      case RECOVERY_OPTION_30S:
        ok = this.restoreHistory(30)
        break
      case RECOVERY_OPTION_60S:
        ok = this.restoreHistory(60)
        break
      case RECOVERY_OPTION_RESTART:
        ok = this.restoreStage()
        break
    }

    if (!ok) return false

    // Begin fade
    this.phase = 'fading'
    this.fadeTimer = 0
    world.recoveryFading = true
    return true
  }

  /**
   * Advance the recovery flow.  Called every frame while
   * world.state === 'recovery'.
   *
   * Phases:
   *   menu      → waiting for player input (no timed advance)
   *   fading    → fade to black, then restore snapshot
   *   countdown → 3, 2, 1 countdown, then resume gameplay
   */
  updateFlow(world: World, dt: number): void {
    switch (this.phase) {
      case 'fading': {
        this.fadeTimer += dt
        if (this.fadeTimer >= FADE_DURATION_MS) {
          // Restore the pending snapshot
          if (this.pendingSnap) {
            restoreWorld(world, this.pendingSnap)
            this.pendingSnap = null
          }
          // Clear history after recovery — old timeline is void
          this.clearHistory()
          // Enter countdown
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
            // Resume gameplay
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

  // ================================================================
  // Queries (used by UI / Game)
  // ================================================================

  /** Is the recovery menu currently interactive? */
  isMenuPhase(): boolean {
    return this.phase === 'menu'
  }

  /** Number of history snapshots currently stored. */
  get historySize(): number {
    return this.history.size
  }

  /** Is a stage snapshot available? */
  hasStageSnapshot(): boolean {
    return this.stageSnap !== null
  }

  /** Is the given recovery option currently available? */
  isOptionAvailable(option: number): boolean {
    switch (option) {
      case RECOVERY_OPTION_30S:
      case RECOVERY_OPTION_60S:
        return this.history.size > 0
      case RECOVERY_OPTION_RESTART:
        return this.stageSnap !== null
      default:
        return false
    }
  }
}
