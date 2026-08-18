import type { Direction } from '../constants'
import type { WorldSnapshot } from '../snapshot/types'

// ================================================================
// Replay System — shared types
// (plan/replay.md §3, §4, §10)
// ================================================================

/**
 * Replay result type. Four-state enum matching .replay filename status.
 * - 'clear' — stage completed successfully
 * - 'base' — base destroyed
 * - 'died' — lives exhausted
 * - 'timeout' — max ticks reached (sim-only; browser games never timeout)
 *
 * Legacy 'victory' | 'defeat' values from IndexedDB are migrated on load
 * (plan/God-AI-Replay-Visualization §3.1).
 */
export type ReplayType = 'clear' | 'base' | 'died' | 'timeout'

/** Replays are addressed by UUID. */
export type ReplayID = string

/**
 * One tick of player input, captured every simulation tick (60/sec).
 * Packed to 1 byte for storage.
 */
export interface InputFrame {
  /** Movement direction, or null when idle. */
  direction: Direction | null
  /** Fire key held this tick. */
  firing: boolean
  /** Guard (天降神兵) pressed this tick. */
  guard: boolean
  /** Frenzy (狂暴宣泄) pressed this tick. */
  frenzy: boolean
}

/**
 * Descriptive gameplay information recorded with every replay
 * (denormalized for browsing, no World load needed).
 */
export interface ReplayMetadata {
  stage: number
  stageName: string
  difficulty: string
  lives: number
  playerLevel: number
  score: number
  killCount: number
  enemiesTotal: number // total enemies spawned this stage
  playTimeMs: number
  /** Lie-Back-Win-Mode: whether this replay used cooperative mode. */
  coop?: boolean
  /** 督战 (supervise) mode: whether God AI played as player1 (no human input). */
  spectate?: boolean
  /** 督战双玩家: whether both P1 and P2 were controlled by God AI. */
  spectateDual?: boolean
}

/**
 * The Replay object — a starting WorldSnapshot + a stream of player inputs.
 */
export interface Replay {
  /** UUID — stable identity across sessions. */
  id: ReplayID
  type: ReplayType
  /** Wall-clock creation time (epoch ms). */
  createdAt: number
  gameVersion: string
  /** Packed-frame format version (for future format changes). */
  schemaVersion: number

  /** RNG seed of the run (surfaces in the .replay filename / round-trips).
   *  Browser recordings use the World's Date.now() seed; sim recordings carry
   *  the --seed value. 0 only when genuinely unknown. */
  seed: number

  /** The starting WorldSnapshot (stage-start or loaded-save). */
  initialSnapshot: WorldSnapshot

  /** Packed input frames (Uint8Array), prefixed with schema version byte. */
  frames: Uint8Array

  /** Lie-Back-Win-Mode: packed God AI input frames (v2 only). Null for v1 replays. */
  frames2: Uint8Array | null

  /** Number of ticks in the recording. */
  totalTicks: number

  /** Duration in ms (totalTicks × TICK_MS). */
  durationMs: number

  /** Metadata (denormalized for browsing). */
  metadata: ReplayMetadata

  /** Canvas preview (JPEG data-URL) captured right after the replay was
   *  saved — the frame that was on screen at the victory/defeat moment.
   *  Null until the deferred capture runs (or in headless contexts). */
  thumbnail: string | null

  isFavorite: boolean
  favoriteAt: number | null // epoch ms when favorited

  /** Runtime-only flag: imported .replay files are transient (Q3). */
  transient?: boolean

  /**
   * Desync-locator chain (plan/Replay-TickHash-Chain.md): one world hash per
   * hashInterval ticks, sampled after the corresponding sim.tick(). The
   * verifier compares its own hashes against this list to localize any
   * divergence to a tick window. Absent in legacy files (verified as
   * `hashVerified === null`).
   */
  tickHashes?: string[]
  /** Ticks between hash checkpoints — written so readers never misalign
   *  checkpoints if REPLAY_HASH_INTERVAL ever changes. Defaults to the
   *  verifier's own REPLAY_HASH_INTERVAL when absent. */
  hashInterval?: number
}

/** Filter for browsing/searching replays. */
export interface ReplayFilter {
  type?: ReplayType
  favorite?: boolean
  stage?: number
}

/**
 * Async persistence backend for replays.
 * Mirrors SnapshotStorageBackend — same pattern, separate IndexedDB database.
 */
export interface ReplayStorageBackend {
  save(replay: Replay): Promise<void>
  delete(id: ReplayID): Promise<void>
  loadAll(): Promise<Replay[]>
}
