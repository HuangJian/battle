import type { World } from '../game/World'
import type {
  GameSnapshot,
  SnapshotFilter,
  SnapshotID,
  SnapshotMetadata,
  SnapshotStorageBackend,
  SnapshotType,
  RetentionPolicy,
  ThumbnailProvider,
} from './types'
import { cloneWorld, restoreWorld } from './WorldSerializer'
import {
  AUTO_SNAPSHOT_INTERVAL_MS,
  GAME_VERSION,
  LATEST_FALLBACK_WINDOW_MS,
  RETENTION_POLICIES,
} from './config'
import { STAGES } from '../config/stages'
import { DIFFICULTIES } from '../config/difficulty'
import { generateUUID } from '../utils/uuid'

export interface SnapshotManagerOptions {
  /** Retention policy table — defaults to RETENTION_POLICIES (config.ts). */
  policies?: Record<SnapshotType, RetentionPolicy>
  /** Async persistence backend — omit for in-memory only (tests). */
  backend?: SnapshotStorageBackend | null
  /** Clock override for deterministic tests. */
  now?: () => number
  /** Auto-snapshot cadence override (ms). */
  autoIntervalMs?: number
  /** "Load Latest" fallback window override (ms). */
  fallbackWindowMs?: number
}

/**
 * SnapshotManager — owns all snapshot creation, loading, deletion, and
 * retention (plan §5). Gameplay code never manipulates snapshot storage
 * directly; it talks to this manager.
 *
 * The manager observes the World read-only when creating snapshots and
 * restores it atomically when loading — it never participates in gameplay
 * rules (AGENTS §2.1, RecoverySystem exception).
 *
 * Retention is fully policy-driven: there is no `if (autoSave)` anywhere.
 */
export class SnapshotManager {
  private snapshots: GameSnapshot[] = [] // insertion order = creation order
  private byId = new Map<SnapshotID, GameSnapshot>()
  /** Timeline head — parent of the next snapshot (plan §14). */
  private lastId: SnapshotID | null = null
  /** Snapshots created before their frame was rendered (awaiting preview). */
  private pendingThumbnails: SnapshotID[] = []

  private readonly policies: Record<SnapshotType, RetentionPolicy>
  private readonly backend: SnapshotStorageBackend | null
  private readonly now: () => number
  private readonly autoIntervalMs: number
  private readonly fallbackWindowMs: number

  private autoTimer = 0

  constructor(opts: SnapshotManagerOptions = {}) {
    this.policies = opts.policies ?? RETENTION_POLICIES
    this.backend = opts.backend ?? null
    this.now = opts.now ?? (() => Date.now())
    this.autoIntervalMs = opts.autoIntervalMs ?? AUTO_SNAPSHOT_INTERVAL_MS
    this.fallbackWindowMs = opts.fallbackWindowMs ?? LATEST_FALLBACK_WINDOW_MS
  }

  // ================================================================
  // Lifecycle
  // ================================================================

  /** Load persisted snapshots into memory (called once at startup). */
  async hydrate(): Promise<void> {
    if (!this.backend) return
    try {
      const stored = await this.backend.loadAll()
      stored.sort((a, b) => a.createdAt - b.createdAt)
      for (const snap of stored) {
        if (this.byId.has(snap.id)) continue
        this.snapshots.push(snap)
        this.byId.set(snap.id, snap)
      }
    } catch {
      /* storage unavailable — in-memory operation continues */
    }
  }

  // ================================================================
  // Creation (plan §10)
  // ================================================================

  /**
   * Create a snapshot of the current World.
   *
   * Returns the snapshot, or `null` when the type's retention policy is
   * 'never' and the store is full — the caller should surface a cleanup
   * notification (plan §3, Manual).
   */
  create(type: SnapshotType, world: World): GameSnapshot | null {
    const policy = this.policyFor(type)

    if (this.count(type) >= policy.limit) {
      if (policy.overwrite === 'never') return null
      // Circular: delete the oldest snapshot(s) of this type to make room.
      while (this.count(type) >= policy.limit) {
        const oldest = this.snapshots.find((s) => s.type === type)
        if (!oldest) break
        this.delete(oldest.id)
      }
    }

    const snapshot: GameSnapshot = {
      id: generateUUID(),
      parentId: this.lastId,
      type,
      createdAt: this.now(),
      gameVersion: GAME_VERSION,
      metadata: this.buildMetadata(world),
      thumbnail: null,
      world: cloneWorld(world),
    }

    this.snapshots.push(snapshot)
    this.byId.set(snapshot.id, snapshot)
    this.lastId = snapshot.id
    this.pendingThumbnails.push(snapshot.id)
    this.persist(snapshot)
    return snapshot
  }

  /** Build first-class metadata from the live World (plan §7). */
  private buildMetadata(world: World): SnapshotMetadata {
    const p = world.player
    let combatLevel = 0
    if (p) {
      const prof = p.profile
      combatLevel = Math.round(
        (prof.firepower +
          prof.projectileSpeed +
          prof.fireControl +
          prof.mobility +
          prof.armor +
          prof.special) /
          6,
      )
    }
    return {
      stage: world.stageIndex,
      stageName: STAGES[world.stageIndex]?.name ?? '?',
      difficulty: DIFFICULTIES[world.difficultyKey]?.name ?? world.difficultyKey,
      lives: world.lives,
      starLevel: world.playerLevel,
      hp: p?.hp ?? 0,
      maxHp: p?.maxHp ?? 0,
      combatLevel,
      enemiesRemaining: world.enemiesRemaining,
      commanderPresent: world.activeCommanderId !== null,
      killCount: world.killCount,
      score: world.score,
      playTimeMs: world.playTimeMs,
    }
  }

  // ================================================================
  // Auto snapshots (plan §10 — every 30 s after entering a stage)
  // ================================================================

  /**
   * Accumulate real gameplay time and create an 'auto' snapshot every
   * interval. Call once per frame while the game is in 'playing' state.
   * Returns the snapshot on the frame one was created (for notifications).
   */
  updateAuto(world: World, dt: number): GameSnapshot | null {
    this.autoTimer += dt
    if (this.autoTimer < this.autoIntervalMs) return null
    this.autoTimer -= this.autoIntervalMs
    return this.create('auto', world)
  }

  /** Restart the auto-snapshot countdown (on stage entry / recovery). */
  resetAutoTimer(): void {
    this.autoTimer = 0
  }

  // ================================================================
  // Loading / deletion (plan §15)
  // ================================================================

  /**
   * Atomically restore the World from a snapshot. The restored snapshot
   * becomes the timeline head — future snapshots record it as parent.
   */
  restore(id: SnapshotID, world: World): boolean {
    const snap = this.byId.get(id)
    if (!snap) return false
    restoreWorld(world, snap.world)
    this.lastId = snap.id
    this.resetAutoTimer()
    return true
  }

  delete(id: SnapshotID): void {
    const snap = this.byId.get(id)
    if (!snap) return
    this.byId.delete(id)
    const idx = this.snapshots.indexOf(snap)
    if (idx >= 0) this.snapshots.splice(idx, 1)
    const pi = this.pendingThumbnails.indexOf(id)
    if (pi >= 0) this.pendingThumbnails.splice(pi, 1)
    if (this.backend) {
      this.backend.delete(id).catch(() => {})
    }
  }

  // ================================================================
  // Queries
  // ================================================================

  get(id: SnapshotID): GameSnapshot | null {
    return this.byId.get(id) ?? null
  }

  /** All snapshots matching the filter, newest first. */
  getAll(filter?: SnapshotFilter): GameSnapshot[] {
    let list = this.snapshots
    if (filter) {
      list = list.filter(
        (s) =>
          (filter.type === undefined || s.type === filter.type) &&
          (filter.stage === undefined || s.metadata.stage === filter.stage),
      )
    } else {
      list = list.slice()
    }
    return list.sort((a, b) => b.createdAt - a.createdAt)
  }

  count(type?: SnapshotType): number {
    if (type === undefined) return this.snapshots.length
    let n = 0
    for (const s of this.snapshots) if (s.type === type) n++
    return n
  }

  /** Latest snapshot (optionally filtered), or null. */
  latest(filter?: SnapshotFilter): GameSnapshot | null {
    const all = this.getAll(filter)
    return all.length > 0 ? all[0] : null
  }

  /** Is the type's store at capacity under a 'never' policy? */
  isFull(type: SnapshotType): boolean {
    const policy = this.policyFor(type)
    return policy.overwrite === 'never' && this.count(type) >= policy.limit
  }

  policyFor(type: SnapshotType): RetentionPolicy {
    return this.policies[type] ?? { limit: 20, overwrite: 'circular' }
  }

  /**
   * Latest-snapshot fallback rule (plan §11): pick the newest snapshot,
   * but if it was created less than the fallback window before the
   * failure moment, select the previous one instead — restoring seconds
   * before an unavoidable failure would only replay the failure.
   */
  pickRecoverySnapshot(failureTime = this.now()): GameSnapshot | null {
    const all = this.getAll()
    if (all.length === 0) return null
    const latest = all[0]
    if (failureTime - latest.createdAt < this.fallbackWindowMs && all.length > 1) {
      return all[1]
    }
    return latest
  }

  /**
   * Target snapshot for a manual "时光宝盒" rewind (new-powerups-plan §4.3):
   * prefer the newest `auto` snapshot (≤30s of history), falling back to the
   * current stage's `stage-start` snapshot when no auto snapshot exists yet
   * (early in a stage). Returns null when no history is available at all —
   * the caller should refund the spent charge instead of rewinding.
   *
   * Deterministic by snapshot set (not wall-clock dependent): auto snapshots
   * are ordered by creation time, and `getAll` already returns newest-first.
   */
  pickRewindSnapshot(world: World): GameSnapshot | null {
    const autos = this.getAll({ type: 'auto' })
    if (autos.length > 0) return autos[0]
    return this.latest({ type: 'stage-start', stage: world.stageIndex })
  }

  // ================================================================
  // Thumbnails (plan §8)
  // ================================================================

  /**
   * Capture previews for snapshots created since the last rendered frame.
   * Called by Game right after a canvas repaint so the thumbnail always
   * shows the snapshot's own stage (a snapshot created mid-tick would
   * otherwise capture the previous frame's pixels).
   */
  capturePendingThumbnails(provider: ThumbnailProvider): void {
    if (this.pendingThumbnails.length === 0) return
    const ids = this.pendingThumbnails
    this.pendingThumbnails = []
    for (const id of ids) {
      const snap = this.byId.get(id)
      if (!snap) continue
      const dataUrl = provider()
      if (dataUrl) {
        snap.thumbnail = dataUrl
        this.persist(snap)
      }
    }
  }

  /** Whether any snapshot is still waiting for its preview. */
  get hasPendingThumbnails(): boolean {
    return this.pendingThumbnails.length > 0
  }

  // ================================================================
  // Storage estimation
  // ================================================================

  /**
   * Estimate the total storage bytes used by all loaded snapshots.
   * Measures via TextEncoder (accurate UTF-8 byte count) rather than
   * navigator.storage.estimate() which reports origin-level totals.
   */
  estimateBytes(): number {
    const encoder = new TextEncoder()
    let bytes = 0
    for (const snap of this.snapshots) {
      // Thumbnail data-URLs are the largest component by far
      bytes += encoder.encode(snap.thumbnail ?? '').byteLength
      // World state + metadata
      bytes += encoder.encode(JSON.stringify(snap.metadata)).byteLength
      bytes += encoder.encode(JSON.stringify(snap.world)).byteLength
      // Overhead for id, type, parentId, etc.
      bytes += 200
    }
    return bytes
  }

  // ================================================================
  // Persistence (internal)
  // ================================================================

  private persist(snapshot: GameSnapshot): void {
    if (!this.backend) return
    this.backend.save(snapshot).catch(() => {})
  }
}
