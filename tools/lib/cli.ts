/**
 * cli.ts — the one CLI argument layer for tools/ (plan/refactor.zcode.md §1.2).
 *
 * Before this module existed, ~34 tools each hand-rolled an `arg()` helper in
 * three mutually incompatible signatures, `parseSeeds()` was copied ≥6× with
 * drift (ab-diff/ab-param/ab-multi-param lost the count-only branch), and six
 * diag tools filtered `--stages` with lenient hand-rolled parsers that
 * silently dropped bad tokens — the exact §213 failure class (a "35-stage
 * sweep" silently running S1) that ../lib/stage-spec.ts exists to prevent.
 *
 * Rules:
 *   - New tools MUST import from here (`import { arg, parseSeeds, parseStages }
 *     from '../lib/cli'`). Do not hand-roll argv indexing.
 *   - Parsers are assertive: a bad token throws — never a silently truncated
 *     or defaulted list.
 *
 * Known dialect exception: tools/perf/* use a self-rolled `--flag=value`
 * EQUALS-form scanner. This module only recognizes the two-token form
 * (`--flag value`). Both syntaxes coexist by design — when touching perf/*
 * tools keep their form; do not "fix" it to match this module.
 *
 * Pure module: no RNG, no params mutation, no filesystem access.
 */
import { STAGES } from '../../src/config/stages'
import { parseStageSpec } from './stage-spec'

/**
 * Value of `--${name}`, or `def` when the flag is absent.
 * Only the two-token form is recognized: `--name value`.
 * This is THE canonical signature — the historical variants were
 * `arg(name)` (no default) and flip-scan's `arg('--name')` (dashes included).
 */
export function arg(name: string, def?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : def
}

/** Boolean presence flag: `--strict` → true. */
export function flag(name: string): boolean {
  return process.argv.includes(`--${name}`)
}

/**
 * Seed spec → seed list.
 *   undefined      → [1..defaultCount] (historical sweep default: 120)
 *   "1-60"         → inclusive range
 *   "60"           → count (seeds 1..60)
 *   "1,3,5"        → explicit list (each token must be a positive integer)
 *
 * The count-only branch is load-bearing: half the historical copies dropped
 * it, so `--seeds 60` silently meant "seed 60 only" there.
 */export function parseSeeds(spec: string | undefined, defaultCount = 120): number[] {
  if (!spec) return Array.from({ length: defaultCount }, (_, i) => i + 1)
  const s = spec.trim()
  if (/^\d+-\d+$/.test(s)) {
    const [lo, hi] = s.split('-').map(Number)
    return Array.from({ length: hi - lo + 1 }, (_, i) => lo + i)
  }
  if (/^\d+$/.test(s)) {
    const n = Number(s)
    return Array.from({ length: n }, (_, i) => i + 1)
  }
  return s.split(',').map((tok) => {
    const n = Number(tok.trim())
    if (!Number.isInteger(n) || n < 1) {
      throw new Error(`--seeds: illegal token "${tok}" in "${spec}" (use "1-60", "60", or "1,3,5")`)
    }
    return n
  })
}

/**
 * `--stages` value → 0-based stage indexes via the strict §213-guard parser
 * (../lib/stage-spec.ts). Absent/empty spec means all stages. Any junk,
 * out-of-range, or reverse-range token THROWS — silent token-dropping is
 * exactly what this wrapper forbids. Callers that catch should prefix the
 * message with their tool name; uncaught, the `--stages "...": reason`
 * message is self-describing.
 */
export function parseStages(spec: string | undefined): number[] {
  return parseStageSpec(spec ?? 'all', STAGES.length)
}

/**
 * Collect every `--set <key>=<value>` occurrence into a numeric override
 * table (refactor.zcode.md §2.4 — replaces the per-tool argv scan loops).
 *
 * `allowed` is the key-validity oracle (pass DEFAULT_GOD_AI_PARAMS); a key
 * outside it, a missing `=`, or a non-numeric value THROWS with the flag
 * name in the message. Repeatable: later occurrences win.
 */
export function parseParamSets(allowed: object): Record<string, number> {
  const out: Record<string, number> = {}
  const argv = process.argv
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] !== '--set') continue
    const kv = argv[i + 1]
    if (!kv || !kv.includes('=')) {
      throw new Error('--set expects key=value (e.g. --set chokepointMode=1)')
    }
    const eq = kv.indexOf('=')
    const key = kv.slice(0, eq)
    const val = Number(kv.slice(eq + 1))
    if (Number.isNaN(val) || !(key in allowed)) {
      throw new Error(`--set: unknown or non-numeric param '${key}'`)
    }
    out[key] = val
  }
  return out
}

/**
 * SINGLE-SEED seed-spec dialect (refactor.zcode.md §2.3/§2.4): a bare count
 * ("60") means the SINGLE seed 60, not 1..60 — the exact opposite of
 * {@link parseSeeds}. Both dialects are load-bearing in historical tools;
 * this is the canonical home for the single-seed flavor (moved here from
 * sim/batch-sim.ts, which re-exports it).
 *   undefined → def (caller supplies)
 *   "1-60"    → inclusive range
 *   "60"      → [60]
 */
export function parseSeedSpec(spec: string | undefined, def = '1-10'): number[] {
  const s = spec ?? def
  if (s.includes('-')) {
    const [start, end] = s.split('-').map(Number)
    if (!Number.isInteger(start) || !Number.isInteger(end) || end < start) {
      throw new Error(`--seeds: illegal range "${s}"`)
    }
    return Array.from({ length: end - start + 1 }, (_, i) => start + i)
  }
  const n = Number.parseInt(s, 10)
  if (!Number.isInteger(n) || n < 1) {
    throw new Error(`--seeds: illegal value "${s}" (use "1-60" or a single seed number)`)
  }
  return [n]
}
