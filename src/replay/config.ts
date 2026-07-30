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
 * | clear    |    20 | circular  |
 * | base     |    20 | circular  |
 * | died     |    20 | circular  |
 * | timeout  |    20 | circular  |
 */
export const REPLAY_RETENTION_POLICIES: Record<ReplayType, RetentionPolicy> = {
  clear: { limit: 20, overwrite: 'circular' },
  base: { limit: 20, overwrite: 'circular' },
  died: { limit: 20, overwrite: 'circular' },
  timeout: { limit: 20, overwrite: 'circular' },
}

/** Maximum number of favorited replays. Enforced in toggleFavorite(). */
export const REPLAY_FAVORITE_LIMIT = 100

/** Thumbnail resolution (same as snapshots). */
export const REPLAY_THUMBNAIL_WIDTH = 256
export const REPLAY_THUMBNAIL_HEIGHT = 256
/** JPEG quality for stored thumbnails (size / fidelity tradeoff). */
export const REPLAY_THUMBNAIL_QUALITY = 0.72

/**
 * Packed-frame schema version. Bump when the bit layout changes.
 * Stored as the first byte of every packed-frame blob.
 */
export const FRAME_SCHEMA_VERSION = 0x01
