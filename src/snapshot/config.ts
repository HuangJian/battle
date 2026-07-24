import type { RetentionPolicy, SnapshotType } from './types'

// ================================================================
// Snapshot Management Framework — configuration
// (plan/Snapshot-Management-Framework.md §3, §9, §10, §11)
//
// Everything here is data. Adding a snapshot type = adding a policy row.
// ================================================================

/** Stamped into every snapshot for forward-compatibility checks. */
export const GAME_VERSION = '0.1.0'

/**
 * Retention policies per snapshot type (plan §9).
 *
 * | Type        | Limit | Overwrite |
 * | ----------- | ----: | --------- |
 * | Stage Start |    20 | Circular  |
 * | Pause       |    20 | Circular  |
 * | Auto        |    20 | Circular  |
 * | Manual      |   100 | Never     |
 */
export const RETENTION_POLICIES: Record<SnapshotType, RetentionPolicy> = {
  'stage-start': { limit: 20, overwrite: 'circular' },
  pause: { limit: 20, overwrite: 'circular' },
  auto: { limit: 20, overwrite: 'circular' },
  manual: { limit: 100, overwrite: 'never' },
}

/** Auto snapshots are created every 30 s after entering a stage (plan §10). */
export const AUTO_SNAPSHOT_INTERVAL_MS = 30_000

/**
 * "Load Latest" fallback window (plan §11): if the latest snapshot was
 * created less than this many ms before the failure, automatically select
 * the previous snapshot instead — restoring immediately before an
 * unavoidable failure would be useless.
 */
export const LATEST_FALLBACK_WINDOW_MS = 15_000

/**
 * Recommended thumbnail resolution (plan §8).
 *
 * The playfield canvas is square (FIELD × FIELD), so the thumbnail is square
 * too — this preserves the game's aspect ratio and avoids stretch/crop
 * artifacts. 256 is a balance of crispness vs. IndexedDB storage size.
 */
export const THUMBNAIL_WIDTH = 256
export const THUMBNAIL_HEIGHT = 256
/** JPEG quality for stored thumbnails (size / fidelity tradeoff). */
export const THUMBNAIL_QUALITY = 0.72

/** Default manual-snapshot shortcut (plan §3) — rebindable in Controls. */
export const DEFAULT_SNAPSHOT_KEY = 'Shift+KeyS'
