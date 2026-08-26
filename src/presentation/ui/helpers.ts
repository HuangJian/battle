/**
 * Shared helpers for the Snapshot/Replay browsers (plan/refactor.agy.md
 * §3.3). Only truly identical utilities live here.
 *
 * Deliberately NOT shared:
 *  - `formatPlayTime` — the two browsers have different display contracts:
 *    SnapshotBrowser shows whole minutes only (`05m`), ReplayBrowser adapts
 *    minutes vs seconds (`05m` / `42s`). Unifying would change visible
 *    behavior for no friction gain.
 *  - Filter tabs / entry cards / drag-and-drop import — structurally similar
 *    but keyed to different data models (snapshot types vs replay types +
 *    favorites); a BrowserBase abstraction would couple them tighter than
 *    the ~40 lines it saves (Three Gates).
 */

/** Format a creation timestamp as `MM-DD HH:mm` (local time). */
export function formatCreated(epochMs: number): string {
  const d = new Date(epochMs)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** Format a byte count as `B` / `KB` / `MB`. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
