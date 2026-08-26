import type { RetentionPolicy } from '../snapshot/types'
import type { ReplayType } from './types'

// ================================================================
// Replay System — configuration
// (plan/replay.md §4, §10)
//
// Everything here is data. Adding a replay type = adding a policy row.
// ================================================================

/**
 * Retention policies per replay type (plan/God-AI-Replay-Visualization §3.1).
 *
 * | Type     | Limit | Overwrite |
 * | -------- | ----: | --------- |
 * | clear    |   250 | circular  |
 * | base     |   250 | circular  |
 * | died     |   250 | circular  |
 * | timeout  |   250 | circular  |
 *
 * 250 (not the original 20): human-demonstration corpora for NN behavior
 * cloning run 100+ games in one sitting — a 20-deep circular buffer silently
 * evicted the earliest recordings. IndexedDB holds ~100KB per replay, so
 * 250×4 types ≈ 100MB worst case, well within budget.
 */
export const REPLAY_RETENTION_POLICIES: Record<ReplayType, RetentionPolicy> = {
  clear: { limit: 250, overwrite: 'circular' },
  base: { limit: 250, overwrite: 'circular' },
  died: { limit: 250, overwrite: 'circular' },
  timeout: { limit: 250, overwrite: 'circular' },
}

/** Maximum number of favorited replays. Enforced in toggleFavorite(). */
export const REPLAY_FAVORITE_LIMIT = 100

/**
 * Packed-frame schema version. Bump when the bit layout changes.
 * Stored as the first byte of every packed-frame blob.
 *
 * v1 (0x01): single input stream per tick.
 * v2 (0x02): dual input stream when coop is active — [flags byte][p1][p2] per tick.
 */
export const FRAME_SCHEMA_VERSION = 0x02
export const FRAME_SCHEMA_V1 = 0x01

/**
 * Every packed-frame schema this build can decode (see `unpackFrames`).
 *
 * The writer still *emits* the newest layout it needs, but a v1 blob stays v1
 * forever: single-stream recordings are deliberately downgraded so older
 * readers keep working. Readers must therefore accept the whole set — gating
 * on `=== FRAME_SCHEMA_VERSION` silently orphans perfectly playable files
 * (DECISIONS #76).
 */
export const SUPPORTED_FRAME_SCHEMA_VERSIONS: readonly number[] = [
  FRAME_SCHEMA_V1,
  FRAME_SCHEMA_VERSION,
]

/** Whether this build can decode a packed-frame blob of the given version. */
export function isSupportedFrameSchema(version: unknown): boolean {
  return typeof version === 'number' && SUPPORTED_FRAME_SCHEMA_VERSIONS.includes(version)
}

/**
 * Ticks between world-hash checkpoints (plan/Replay-TickHash-Chain.md §1.2).
 * The recorder samples the post-tick world state every N frames; the verifier
 * compares at the same cadence. Written into the .replay file so readers
 * never misalign checkpoints if this constant ever changes.
 */
export const REPLAY_HASH_INTERVAL = 100
