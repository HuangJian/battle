import type { Direction } from '../constants'
import type { WorldSnapshot } from '../snapshot/types'

// ================================================================
// Replay System — shared types
// (plan/replay.md §3, §4, §10)
// ================================================================

/**
 * Replay result type. Determines the auto-save bucket and retention policy.
 * - 'victory' — stage completed successfully
 * - 'defeat' — base destroyed or game over
 */
export type ReplayType = 'victory' | 'defeat'

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

  /** The starting WorldSnapshot (stage-start or loaded-save). */
  initialSnapshot: WorldSnapshot

  /** Packed input frames (Uint8Array), prefixed with schema version byte. */
  frames: Uint8Array

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
