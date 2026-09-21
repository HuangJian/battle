import type { ProbeManifest } from './manifest'

// ================================================================
// Human-opening probe — boot query resolution (plan v7 §1.2 / T2)
//
// `?probe=<course>&game=<n>`, following the `?fireLineDetour=1` precedent in
// main.ts (a boot-time launch configuration, never gameplay state).
//
// Runtime rejection is EXACTLY two paths — course name mismatch and
// game out of range (plus an unparseable manifest, handled by the caller).
// `courseSha` is NOT verified at runtime: `nn-training/levels/*.jsonc` is not
// served to the browser, so there is nothing to recompute it from. Its
// integrity is guaranteed by the T1 generator + the CI drift check.
//
// Pure module: no DOM, no fs.
// ================================================================

export const PROBE_QUERY_KEY = 'probe'

export type ProbeTargetRejection =
  | 'absent'
  | 'course-mismatch'
  | 'game-not-integer'
  | 'game-out-of-range'

export type ProbeTarget =
  | { ok: true; course: string; game: number }
  | { ok: false; reason: ProbeTargetRejection }

/** Read the launch query (string `?a=b` form or an already-parsed object). */
export function resolveProbeTarget(
  search: string | URLSearchParams,
  manifest: ProbeManifest,
): ProbeTarget {
  const params = typeof search === 'string' ? new URLSearchParams(search) : search
  const course = params.get(PROBE_QUERY_KEY)
  if (course === null || course === '') return { ok: false, reason: 'absent' }
  if (course !== manifest.course) return { ok: false, reason: 'course-mismatch' }

  const raw = params.get('game')
  if (raw === null || raw === '') return { ok: true, course, game: 0 }
  if (!/^\d+$/.test(raw)) return { ok: false, reason: 'game-not-integer' }
  const game = Number(raw)
  if (game >= manifest.games.length) return { ok: false, reason: 'game-out-of-range' }
  return { ok: true, course, game }
}

/** Relative href used by the Control Center launcher links (pure display). */
export function probeGameHref(course: string, game: number): string {
  return `?${PROBE_QUERY_KEY}=${encodeURIComponent(course)}&game=${game}`
}
