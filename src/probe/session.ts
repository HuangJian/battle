import { writeStoreZip, type ZipEntry } from './zip'
import { validateProbeVerdict, type ProbeVerdict } from './verdict'

// ================================================================
// Human-opening probe — session pack
// (human-opening-probe.plan v7 §1.2 / T4)
//
// "End this probe session" produces ONE zip download:
//   session.json    — course identity + the (game, stage, seed) table
//   <game>.replay   — the recorded run, existing .replay format (tickHashes)
//   verdicts.jsonl  — one verdict line per judged game
//
// The pack refuses to build when an `unsolvable` verdict carries no reason
// (the protocol's single hard validation, plan §4 rule 6) — a loud throw, not
// a silently-incomplete archive.
//
// Pure module: no DOM, no fs.
// ================================================================

export interface ProbeSessionGame {
  game: number
  stage: number
  seed: number
}

export interface ProbeSessionMeta {
  course: string
  courseSha: string
  /** ISO timestamp of session start (the only non-derived field in the pack). */
  startedAt: string
  games: ProbeSessionGame[]
}

export interface ProbeReplayFile {
  /** File name inside the zip, e.g. `3.replay`. */
  file: string
  /** Serialized `.replay` envelope text. */
  text: string
}

export function buildSessionJson(meta: ProbeSessionMeta): string {
  return JSON.stringify(
    {
      course: meta.course,
      courseSha: meta.courseSha,
      startedAt: meta.startedAt,
      games: meta.games.map((g) => ({ game: g.game, stage: g.stage, seed: g.seed })),
    },
    null,
    2,
  )
}

/**
 * Serialize verdict lines (one JSON per line, ordered by game). Throws
 * `ProbeVerdictError` on the first invalid line — notably an `unsolvable`
 * verdict with a blank reason.
 */
export function buildVerdictsJsonl(verdicts: readonly ProbeVerdict[]): string {
  const sorted = [...verdicts].sort((a, b) => a.game - b.game)
  const lines: string[] = []
  for (const v of sorted) {
    validateProbeVerdict(v)
    lines.push(
      JSON.stringify({
        game: v.game,
        stage: v.stage,
        seed: v.seed,
        attempts: v.attempts,
        best: { band: v.best.band, reason: v.best.reason },
        kills: v.kills,
        deathTick: v.deathTick,
      }),
    )
  }
  return lines.join('\n') + (lines.length > 0 ? '\n' : '')
}

/** `probe-<course>-<YYYYMMDD>.zip` — date taken from `startedAt`. */
export function packFilename(course: string, startedAt: string): string {
  const date = startedAt.slice(0, 10).replace(/-/g, '')
  return `probe-${course}-${date || 'undated'}.zip`
}

export interface SessionPackInput {
  meta: ProbeSessionMeta
  replays: readonly ProbeReplayFile[]
  verdicts: readonly ProbeVerdict[]
}

export interface SessionPack {
  filename: string
  bytes: Uint8Array
}

/** Build the whole pack. Refuses (throws) on an invalid verdict line. */
export function buildSessionPack(input: SessionPackInput): SessionPack {
  const verdictsJsonl = buildVerdictsJsonl(input.verdicts)
  const encoder = new TextEncoder()
  const entries: ZipEntry[] = [
    { name: 'session.json', data: encoder.encode(buildSessionJson(input.meta)) },
    ...[...input.replays]
      .sort((a, b) => a.file.localeCompare(b.file))
      .map((r) => ({ name: r.file, data: encoder.encode(r.text) })),
    { name: 'verdicts.jsonl', data: encoder.encode(verdictsJsonl) },
  ]
  return {
    filename: packFilename(input.meta.course, input.meta.startedAt),
    bytes: writeStoreZip(entries),
  }
}
