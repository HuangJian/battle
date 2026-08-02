import type { Tank, Bullet, PowerUp, Mine, PowerUpType, TerrainType } from '../types'
import type { Direction } from '../constants'
import type { SpawnEntry } from '../game/World'

// ================================================================
// Snapshot Management Framework — shared types
// (plan/Snapshot-Management-Framework.md §3, §6, §7, §9, §15)
// ================================================================

/**
 * Snapshot origin. Version 1 ships four origins; the union is left open
 * (`string & {}`) so future systems (checkpoint / replay / debug) can add
 * new types by adding a retention policy entry — configuration, not
 * redesign (plan §19).
 */
export type SnapshotType = 'stage-start' | 'pause' | 'auto' | 'manual' | (string & {})

/** Snapshots are addressed by UUID, never by numbered save files (plan §6). */
export type SnapshotID = string

/**
 * Declarative retention policy — storage behavior belongs to the policy
 * layer, not the snapshot itself (plan §9, Constitution §2). The framework
 * must never branch on `if (autoSave) / if (manualSave)`.
 */
export interface RetentionPolicy {
  /** Maximum number of snapshots of this type. */
  limit: number
  /**
   * What happens when the limit is reached:
   * - 'circular': the oldest snapshot of this type is deleted to make room.
   * - 'never':    creation is refused; the caller should ask for cleanup.
   */
  overwrite: 'circular' | 'never'
}

/**
 * Descriptive gameplay information recorded with every snapshot
 * (plan §7 — "Metadata is a first-class citizen").
 */
export interface SnapshotMetadata {
  stage: number
  stageName: string
  difficulty: string
  lives: number
  starLevel: number
  hp: number
  maxHp: number
  /** Aggregate combat capability (mean of the player's six profile dims). */
  combatLevel: number
  enemiesRemaining: number
  commanderPresent: boolean
  killCount: number
  score: number
  /** Total gameplay time of the run (ms), simulation-derived. */
  playTimeMs: number
}

/**
 * WorldSnapshot — a complete, self-contained description of the World at a
 * single point in time (moved here from the retired RecoverySystem).
 *
 * Design rules (Constitution §6, AGENTS §2):
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
  /** Allied 天降神兵 guard tanks (third faction, DECISIONS.md §31 Phase 2). */
  allies: Tank[]
  bullets: Bullet[]
  powerUps: PowerUp[]
  /** Drops deferred from a prior stage (released on the next stage's first
   *  enemy kill). Buffered so a rewind restores them deterministically. */
  pendingDrops: { type: PowerUpType; x: number; y: number }[]

  // Stage info
  stageIndex: number
  spawnQueue: SpawnEntry[]
  enemiesSpawned: number
  enemiesRemaining: number
  /** Total enemies for this stage (plan/God-AI-Curriculum §3 Gap A).
   *  Falls back to `ENEMIES_PER_STAGE` when undefined (legacy snapshots).
   *  Persisted so RecoverySystem restores the correct enemy count. */
  enemiesTotal?: number
  /** Round-robin spawn-point cursor (determinism — AGENTS §2.3). */
  spawnPointIndex: number

  // Game state
  score: number
  lives: number
  playerLevel: number
  highScore: number
  killCount: number
  playTimeMs: number

  // Run profile — active difficulty/theme keys. Persisted so a loaded save
  // restores the EXACT rules profile it was created with (a classic save must
  // NOT silently run modern rules after load). The `rules` / `difficulty` /
  // `theme` objects are re-derived from these keys on restore (Constitution §6:
  // a snapshot is a complete World description).
  difficultyKey: string
  themeKey: string

  // Timers
  freezeTimer: number
  stageClearTimer: number
  gameOverTimer: number
  spawnTimer: number
  /**
   * Post-victory bonus collection window: counts down while the player grabs
   * any power-ups left after the last enemy is destroyed. Undefined = legacy
   * snapshot taken before the window fields were serialized (restore falls
   * back to 0/false — the pre-window state).
   */
  pickupWindowTimer?: number
  /** True once the bonus window has begun for the current stage. */
  pickupWindowEntered?: boolean

  // RNG state (for determinism / future replay)
  rngState: number

  // Simulation frame counter
  frame: number

  // Monotonic bullet counter — seed for per-bullet speed jitter (determinism)
  bulletSeq: number

  // ---- AI command authority (plan §4, §7) ----
  /** Per-World monotonic enemy birth order (stamped onto aiState.spawnSeq). */
  spawnSeqCounter: number
  /** Tank currently holding command, or null. */
  activeCommanderId: number | null
  /** Remaining Commander *spawn attempts* (floor guarantee, decremented per roll). */
  commanderQuotaRemaining: number
  /** Monotonic counter incremented on every active-Commander broadcast. */
  directiveSeqCounter: number

  // ---- Base (eagle) HP (2026-07-27) ----
  baseHp: number
  baseMaxHp: number

  // ---- Stage spawn-point overrides (plan/God-AI-Curriculum §3.5) ----
  /** Pixel-coordinate enemy spawn points for this stage (cached from StageData).
   *  Undefined = use default ENEMY_SPAWN_POINTS (legacy snapshots). */
  enemySpawnPoints?: { x: number; y: number }[]
  /** Player spawn point in sub-block coords for this stage. Undefined = default. */
  playerSpawnPoint?: { col: number; row: number }

  // ---- Lie-Back-Win-Mode (coop) fields ----
  /** Whether cooperative mode was active at snapshot time. */
  coop?: boolean
  /** 督战 (supervise) mode: God AI as player1, no human input. */
  spectate?: boolean
  /** Player2 tank snapshot (null when coop is off, absent in old snapshots). */
  player2?: Tank | null
  /** God AI lives at snapshot time. */
  lives2?: number
  /** God AI star level at snapshot time. */
  playerLevel2?: number
  /** God AI score at snapshot time. */
  score2?: number
  /** Player2 spawn point (col/row in sub-block coords). */
  player2SpawnPoint?: { col: number; row: number }

  // ---- Super power-up inventory & frenzy state (DECISIONS.md §31) ----
  guardStock: number
  frenzyStock: number
  sacrificeStock: number
  // Per-tank frenzy fields (Q9): now live on Tank, not World.
  // Optional here for backward compat with old snapshots that had them.
  frenzyTimer?: number
  frenzyShotsLeft?: number
  frenzyLastFire?: number
  frenzyInterval?: number
  frenzyDir?: Direction
  fenceExpireFrame?: number // 栅栏道具: 钢墙到期帧（之后恢复为砖墙）

  // ---- New power-ups (new-powerups-plan.md) ----
  empTimer: number // 电磁静默: enemy silence timer
  rewindStock: number // 时光宝盒: inventory count
  mines: Mine[] // 地雷: active mines
}

/**
 * The Snapshot object — every preserved state shares this one model
 * (plan §2 "One Snapshot Model", §6).
 */
export interface GameSnapshot {
  /** UUID — stable identity across sessions. */
  id: SnapshotID
  /** Timeline link (plan §14). Version 1 records it; browsing comes later. */
  parentId: SnapshotID | null
  type: SnapshotType
  /** Wall-clock creation time (epoch ms). */
  createdAt: number
  gameVersion: string
  metadata: SnapshotMetadata
  /** Data-URL preview (256×256 square, matching the playfield), or null until captured. */
  thumbnail: string | null
  world: WorldSnapshot
}

/** Filter for browsing/searching snapshots (plan §15). */
export interface SnapshotFilter {
  type?: SnapshotType
  stage?: number
}

/**
 * Async persistence backend. The physical storage implementation remains
 * internal (plan §16) — the manager works entirely in memory and mirrors
 * every mutation to the backend when one is present. Tests run without one.
 */
export interface SnapshotStorageBackend {
  save(snapshot: GameSnapshot): Promise<void>
  delete(id: SnapshotID): Promise<void>
  loadAll(): Promise<GameSnapshot[]>
}

/**
 * Captures a preview of the current frame as a data URL (or null when no
 * canvas is available — e.g. under tests). Injected by the presentation
 * layer; the framework itself never touches the DOM.
 */
export type ThumbnailProvider = () => string | null
