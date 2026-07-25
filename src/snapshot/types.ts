import type { Tank, Bullet, PowerUp, TerrainType } from '../types'
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
  bullets: Bullet[]
  powerUps: PowerUp[]

  // Stage info
  stageIndex: number
  spawnQueue: SpawnEntry[]
  enemiesSpawned: number
  enemiesRemaining: number
  /** Round-robin spawn-point cursor (determinism — AGENTS §2.3). */
  spawnPointIndex: number

  // Game state
  score: number
  lives: number
  playerLevel: number
  highScore: number
  killCount: number
  playTimeMs: number

  // Timers
  freezeTimer: number
  stageClearTimer: number
  gameOverTimer: number
  spawnTimer: number

  // RNG state (for determinism / future replay)
  rngState: number

  // Simulation frame counter
  frame: number

  // Monotonic bullet counter — seed for per-bullet speed jitter (determinism)
  bulletSeq: number
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
