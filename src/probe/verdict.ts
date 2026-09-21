// ================================================================
// Human-opening probe — band → verdict aggregation
// (human-opening-probe.plan v8 §0)
//
// The operator's per-game band IS the conclusion — skilled human play is the
// repo's only difficulty yardstick (AGENTS §0.2), so nothing here second-guesses
// it. ONE rule, ONE table:
//
//   any 「solvable/slight」    ⇒ solvable   (constructive: someone actually cleared it)
//   else any 「unsolvable」    ⇒ unsolvable (the operator read "no path", reason recorded)
//   else                       ⇒ unknown    (tough / not judged)
//
// The negative arm is therefore not a second protocol — it is the `unsolvable`
// band the bar already collects, and this table is where it lands. It is a
// JUDGEMENT, not a proof: it is refutable by construction (a later clear on the
// same seed overwrites it — see `aggregateSeedVerdict`), because the 60 Hz action
// space cannot be exhausted and using God AI / search as the referee is barred
// ("the teacher is a flawed teacher, never a ceiling"). Read it as "a skilled
// operator could not find a way", whose only job is to decide whether this seed
// belongs in the training-side fix at all.
//
// Same band vocabulary is used by the session bar buttons, the offline kit's
// table and the annotation output.
//
// Pure module: no DOM, no fs, no RNG.
// ================================================================

export const PROBE_BANDS = ['solvable', 'slight', 'tough', 'unsolvable', 'retry'] as const
export type ProbeBand = (typeof PROBE_BANDS)[number]

export type SeedVerdict = 'solvable' | 'unsolvable' | 'unknown'

export interface BandSpec {
  band: ProbeBand
  /** Kill band this ladder step covers (retry = not a band at all). */
  kills: string
  /** Human-facing meaning (the bar's tooltip / legend). */
  meaning: string
  /** Aggregation target, or null for "does not count toward the seed verdict". */
  seedVerdict: SeedVerdict | null
}

/**
 * The plan's §0 table, verbatim. `retry` is a UI escape hatch ("I need another
 * attempt") — it is never aggregated.
 */
export const BAND_TABLE: readonly BandSpec[] = [
  { band: 'solvable', kills: '20', meaning: 'cleared it comfortably', seedVerdict: 'solvable' },
  {
    band: 'slight',
    kills: '14–19',
    meaning: 'cleared it, but it took work',
    seedVerdict: 'solvable',
  },
  { band: 'tough', kills: '4–13', meaning: 'could not break through', seedVerdict: 'unknown' },
  {
    band: 'unsolvable',
    kills: '≤3',
    meaning: 'died inside ~1500t with a readable "no path" — reason required',
    seedVerdict: 'unsolvable',
  },
  { band: 'retry', kills: '—', meaning: 'another attempt (not counted)', seedVerdict: null },
]

export class ProbeVerdictError extends Error {
  constructor(message: string) {
    super(`probe verdict: ${message}`)
    this.name = 'ProbeVerdictError'
  }
}

export function isProbeBand(v: unknown): v is ProbeBand {
  return typeof v === 'string' && (PROBE_BANDS as readonly string[]).includes(v)
}

/** Suggested band from the run's outcome. The human may override; nothing locks. */
export function suggestBand(kills: number, won: boolean): ProbeBand {
  if (won) return 'solvable'
  if (kills >= 14) return 'slight'
  if (kills >= 4) return 'tough'
  return 'unsolvable'
}

/** Aggregation target for one band (null = does not count). */
export function verdictOfBand(band: ProbeBand): SeedVerdict | null {
  const spec = BAND_TABLE.find((b) => b.band === band)
  if (!spec) throw new ProbeVerdictError(`未知 band ${JSON.stringify(band)}`)
  return spec.seedVerdict
}

/**
 * §0 aggregation. Priority is `solvable` > `unsolvable` > `unknown`.
 *
 * A clear outranks a "no path" read on the same seed: finding an input sequence
 * that survives is direct evidence one exists, while the negative read is a
 * failure to find one — so a later clear WITHDRAWS the 不可解 label, and that is
 * the whole falsifiability story of the negative arm.
 */
export function aggregateSeedVerdict(bands: readonly ProbeBand[]): SeedVerdict {
  let sawUnsolvable = false
  for (const band of bands) {
    const verdict = verdictOfBand(band)
    if (verdict === 'solvable') return 'solvable'
    if (verdict === 'unsolvable') sawUnsolvable = true
  }
  return sawUnsolvable ? 'unsolvable' : 'unknown'
}

/** True for the one band that carries a mandatory free-text justification. */
export function bandRequiresReason(band: ProbeBand): boolean {
  return band === 'unsolvable'
}

export interface ProbeVerdictBest {
  band: ProbeBand
  reason: string
}

/** One line of `verdicts.jsonl` (plan §1.2). */
export interface ProbeVerdict {
  game: number
  stage: number
  seed: number
  attempts: number
  best: ProbeVerdictBest
  kills: number
  /** Tick of the death that ended the best attempt, or null when it cleared. */
  deathTick: number | null
}

/**
 * Validate one verdict line. The ONLY validation the protocol mandates is the
 * mandatory reason on `unsolvable` (plan §4 rule 6); kill counts are never
 * checked against the band — the human's read wins.
 */
export function validateProbeVerdict(v: ProbeVerdict): void {
  if (!Number.isInteger(v.game) || v.game < 0) throw new ProbeVerdictError('game 必须是非负整数')
  if (!Number.isInteger(v.attempts) || v.attempts < 1)
    throw new ProbeVerdictError('attempts 至少为 1')
  if (!isProbeBand(v.best.band)) {
    throw new ProbeVerdictError(`未知 band ${JSON.stringify(v.best.band)}`)
  }
  if (typeof v.best.reason !== 'string') throw new ProbeVerdictError('best.reason 必须是字符串')
  if (bandRequiresReason(v.best.band) && v.best.reason.trim() === '') {
    throw new ProbeVerdictError('verdict 为 unsolvable 时必须填写理由（格式：无解必须带理由）')
  }
}
