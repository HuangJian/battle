import { TICK_MS } from '../constants'
import type { WorldSnapshot } from '../snapshot/types'
import type { ThumbnailProvider } from '../snapshot/types'
import type {
  Replay,
  ReplayFilter,
  ReplayID,
  ReplayMetadata,
  ReplayStorageBackend,
  ReplayType,
} from './types'
import { REPLAY_RETENTION_POLICIES, REPLAY_FAVORITE_LIMIT } from './config'
import { GAME_VERSION } from '../snapshot/config'
import { FRAME_SCHEMA_VERSION } from './config'

/** Generate a UUID (crypto.randomUUID when available, fallback otherwise). */
function generateUUID(): string {
  const c = globalThis.crypto as Crypto | undefined
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  let out = ''
  const hex = '0123456789abcdef'
  for (let i = 0; i < 36; i++) {
    if (i === 8 || i === 13 || i === 18 || i === 23) out += '-'
    else if (i === 14) out += '4'
    else out += hex[Math.floor(Math.random() * 16)]
  }
  return out
}

export interface ReplayManagerOptions {
  backend?: ReplayStorageBackend | null
  now?: () => number
}

/**
 * ReplayManager — owns all replay creation, deletion, retention,
 * and queries. Mirrors SnapshotManager's pattern.
 *
 * Retention is fully policy-driven: circular overwrite for victory/defeat,
 * favorited replays are exempt from eviction.
 */
export class ReplayManager {
  private replays: Replay[] = []
  private byId = new Map<ReplayID, Replay>()
  private backend: ReplayStorageBackend | null
  private now: () => number
  private pendingThumbnails: ReplayID[] = []

  constructor(opts: ReplayManagerOptions = {}) {
    this.backend = opts.backend ?? null
    this.now = opts.now ?? (() => Date.now())
  }

  // ================================================================
  // Lifecycle
  // ================================================================

  /** Load persisted replays into memory (called once at startup). */
  async hydrate(): Promise<void> {
    if (!this.backend) return
    try {
      const stored = await this.backend.loadAll()
      stored.sort((a, b) => a.createdAt - b.createdAt)
      for (const replay of stored) {
        // Migrate legacy ReplayType values (plan/God-AI-Replay-Visualization §3.1)
        // Cast needed: old IndexedDB entries may still have 'victory'/'defeat'
        const legacy = replay.type as string
        if (legacy === 'victory') replay.type = 'clear'
        else if (legacy === 'defeat') replay.type = 'died'
        if (this.byId.has(replay.id)) continue
        this.replays.push(replay)
        this.byId.set(replay.id, replay)
      }
    } catch {
      /* storage unavailable — in-memory operation continues */
    }
  }

  // ================================================================
  // Creation
  // ================================================================

  /**
   * Create and save a replay. Enforces retention policy (circular overwrite).
   * Favorited replays are exempt from eviction.
   *
   * Returns the created replay.
   */
  create(
    type: ReplayType,
    initialSnapshot: WorldSnapshot,
    frames: Uint8Array,
    tickCount: number,
    metadata: ReplayMetadata,
  ): Replay {
    const policy = REPLAY_RETENTION_POLICIES[type]

    // Evict oldest non-favorited replays of this type to make room
    const nonFavs = this.replays.filter((r) => r.type === type && !r.isFavorite)
    while (nonFavs.length >= policy.limit) {
      const oldest = nonFavs.shift()!
      this.delete(oldest.id)
    }

    // totalTicks is the authoritative frame count (== frames.length); duration
    // is derived from it, NOT from wall-clock playTimeMs, which drifts with
    // frame drops and pause. (L1)
    const safeTicks = Math.max(0, Math.floor(tickCount))
    const replay: Replay = {
      id: generateUUID(),
      type,
      createdAt: this.now(),
      gameVersion: GAME_VERSION,
      schemaVersion: FRAME_SCHEMA_VERSION,
      initialSnapshot,
      frames,
      totalTicks: safeTicks,
      durationMs: Math.round(safeTicks * TICK_MS),
      metadata,
      thumbnail: null,
      isFavorite: false,
      favoriteAt: null,
    }

    this.replays.push(replay)
    this.byId.set(replay.id, replay)
    this.persist(replay)
    return replay
  }

  // ================================================================
  // Deletion
  // ================================================================

  delete(id: ReplayID): void {
    const replay = this.byId.get(id)
    if (!replay) return
    this.byId.delete(id)
    const idx = this.replays.indexOf(replay)
    if (idx >= 0) this.replays.splice(idx, 1)
    const pi = this.pendingThumbnails.indexOf(id)
    if (pi >= 0) this.pendingThumbnails.splice(pi, 1)
    if (this.backend) {
      this.backend.delete(id).catch(() => {})
    }
  }

  // ================================================================
  // Favorites
  // ================================================================

  /**
   * Toggle favorite status. Returns true if now favorited, false if unfavorited.
   * When favoriting: checks the 100-replay favorite cap. If at capacity,
   * returns false and the caller should show a toast.
   */
  toggleFavorite(id: ReplayID): boolean {
    const replay = this.byId.get(id)
    if (!replay) return false

    if (!replay.isFavorite) {
      // Check favorite cap
      const favCount = this.favoriteCount()
      if (favCount >= REPLAY_FAVORITE_LIMIT) return false
      replay.isFavorite = true
      replay.favoriteAt = this.now()
    } else {
      replay.isFavorite = false
      replay.favoriteAt = null
    }

    this.persist(replay)
    return replay.isFavorite
  }

  // ================================================================
  // Queries
  // ================================================================

  get(id: ReplayID): Replay | null {
    return this.byId.get(id) ?? null
  }

  /** All replays matching the filter, newest first. */
  getAll(filter?: ReplayFilter): Replay[] {
    let list = this.replays
    if (filter) {
      list = list.filter(
        (r) =>
          (filter.type === undefined || r.type === filter.type) &&
          (filter.favorite === undefined || r.isFavorite === filter.favorite) &&
          (filter.stage === undefined || r.metadata.stage === filter.stage),
      )
    } else {
      list = list.slice()
    }
    return list.sort((a, b) => b.createdAt - a.createdAt)
  }

  count(type?: ReplayType): number {
    if (type === undefined) return this.replays.length
    let n = 0
    for (const r of this.replays) if (r.type === type) n++
    return n
  }

  favoriteCount(): number {
    let n = 0
    for (const r of this.replays) if (r.isFavorite) n++
    return n
  }

  /**
   * Whether a replay can actually be played back. Strict on the packed-frame
   * format version — a mismatch means the bytes cannot be unpacked. The
   * game-version mismatch (GAME_VERSION) is a soft warning handled by the
   * caller, not a hard block. (L3)
   */
  canPlay(replay: Replay): boolean {
    if (!replay) return false
    if (replay.schemaVersion !== FRAME_SCHEMA_VERSION) return false
    if (!replay.frames || replay.frames.length === 0) return false
    if (!replay.initialSnapshot) return false
    return true
  }

  // ================================================================
  // Thumbnails (mirrors SnapshotManager pattern)
  // ================================================================

  /** Enqueue a replay for thumbnail capture. */
  enqueueThumbnail(id: ReplayID): void {
    if (!this.pendingThumbnails.includes(id)) {
      this.pendingThumbnails.push(id)
    }
  }

  /**
   * Capture previews for replays created since the last rendered frame.
   * Called by Game right after a canvas repaint so the thumbnail always
   * shows the replay's own frame.
   */
  capturePendingThumbnails(provider: ThumbnailProvider): void {
    if (this.pendingThumbnails.length === 0) return
    const ids = this.pendingThumbnails
    this.pendingThumbnails = []
    for (const id of ids) {
      const replay = this.byId.get(id)
      if (!replay) continue
      const dataUrl = provider()
      if (dataUrl) {
        replay.thumbnail = dataUrl
        this.persist(replay)
      }
    }
  }

  /** Whether any replay is still waiting for its preview. */
  get hasPendingThumbnails(): boolean {
    return this.pendingThumbnails.length > 0
  }

  // ================================================================
  // Storage estimation
  // ================================================================

  /**
   * Estimate the total storage bytes used by all loaded replays.
   * Measures via TextEncoder (accurate UTF-8 byte count) rather than
   * navigator.storage.estimate() which reports origin-level totals.
   */
  estimateBytes(): number {
    const encoder = new TextEncoder()
    let bytes = 0
    for (const replay of this.replays) {
      // Frames Uint8Array is the largest component
      bytes += replay.frames.byteLength
      // Thumbnail data-URL
      bytes += encoder.encode(replay.thumbnail ?? '').byteLength
      // Initial snapshot world state + metadata
      bytes += encoder.encode(JSON.stringify(replay.initialSnapshot)).byteLength
      bytes += encoder.encode(JSON.stringify(replay.metadata)).byteLength
      // Overhead for id, type, etc.
      bytes += 300
    }
    return bytes
  }

  // ================================================================
  // Persistence (internal)
  // ================================================================

  persist(replay: Replay): void {
    if (!this.backend) return
    this.backend.save(replay).catch(() => {})
  }

  /**
   * Add an external replay (e.g. imported .replay file) to in-memory state
   * and persist to backend. The replay is assumed to be fully formed.
   */
  addReplay(replay: Replay): void {
    if (this.byId.has(replay.id)) return
    this.replays.push(replay)
    this.byId.set(replay.id, replay)
    this.persist(replay)
  }
}
