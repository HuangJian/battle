/**
 * stuck-track.ts — the one zone-stuck tracker (refactor.trae.md §3.4).
 *
 * Replaces the four hand-copied "±1 zone + killCount baseline + timeout +
 * suppression window" quartets that lived as scalar field groups on
 * GodAIInput (Engage _camp*, Aggro _aggCamp* / _aggNavStuck*, Hunt
 * _navStuck*) — the fifth such guard would have copied it a fifth time.
 * Pure data + pure functions; no RNG, no World mutation.
 */
import type { World } from '../../game/World'

/** One tracker's full state. AI-internal, never serialized. */
export interface StuckTrack {
  /** Zone anchor cell (null = not tracking). */
  cell: { col: number; row: number } | null
  /** Consecutive evaluations inside the zone. */
  ticks: number
  /** world.killCount when tracking started (progress detection). */
  killsAtStart: number
  /** Escape-suppression window countdown (0 = off). Not every site uses it. */
  suppress: number
}

export function newStuckTrack(): StuckTrack {
  return { cell: null, ticks: 0, killsAtStart: 0, suppress: 0 }
}

/**
 * Advance the tracker for this evaluation:
 *   - inside the zone → ticks++ (kill progress re-baselines the counter);
 *   - outside → fresh anchor at pc.
 * `zoneRadius` 1 = ±1-cell zone (sub-pixel jitter tolerant, §168),
 * 0 = exact cell match.
 *
 * Returns true when `timeoutTicks` is exceeded with no kill since the
 * baseline. Trigger side effects (reset / suppression window) stay with the
 * caller — they differ per candidate by design.
 */
export function updateStuckTrack(
  st: StuckTrack,
  w: World,
  pc: { col: number; row: number },
  timeoutTicks: number,
  zoneRadius: number,
): boolean {
  const inZone =
    st.cell !== null &&
    (zoneRadius > 0
      ? Math.abs(st.cell.col - pc.col) <= 1 && Math.abs(st.cell.row - pc.row) <= 1
      : st.cell.col === pc.col && st.cell.row === pc.row)
  if (inZone) {
    st.ticks++
    // If a kill happened since tracking started, reset the timer — the
    // player is being productive.
    if (w.killCount !== st.killsAtStart) {
      st.ticks = 1
      st.killsAtStart = w.killCount
    }
  } else {
    st.cell = { col: pc.col, row: pc.row }
    st.ticks = 1
    st.killsAtStart = w.killCount
  }
  return st.ticks > timeoutTicks && w.killCount === st.killsAtStart
}
