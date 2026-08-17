/**
 * stage-spec.ts — shared CLI stage parser + run-header 口径 (open-test protocol M0).
 *
 * Before M0 every tool parsed `--stages` its own way:
 *   - optimize-godai.ts: `parseInt('1-35')` → silently S1 only (the §213
 *     CMA-ES 口径 bug — the recorded "35-stage search" ran on S1);
 *   - ab-param / threat-ledger / run-forensics / eval-suite: `all` or comma
 *     lists, with out-of-range tokens silently dropped;
 *   - none of them rejected reverse ranges or junk tokens.
 *
 * One parser, one semantics (protocol §3.1):
 *   --stages all       → every stage, ascending
 *   --stages 1-35      → inclusive 1-based range
 *   --stages 1,3,7     → listed stages (tokens may mix singles and ranges)
 *   --stage 34         → single stage
 *
 * Anything else — empty/reverse/out-of-range/junk — THROWS. Silent S1
 * fallback is exactly the class of bug this module exists to prevent.
 *
 * Pure module: no RNG, no seed/params mutation, no filesystem access.
 */

/** Total number of stages (single source: the STAGES config length). */
export const STAGE_COUNT = 35

export class StageSpecError extends Error {
  constructor(spec: string, reason: string) {
    super(`--stages "${spec}": ${reason}`)
    this.name = 'StageSpecError'
  }
}

const TOKEN_RE = /^\d+(?:-\d+)?$/

/**
 * Parse a stage spec into 0-based indices. Throws StageSpecError on any
 * invalid token — never returns a silently-truncated or defaulted list.
 *
 * Order: tokens are expanded left-to-right; duplicates keep their first
 * position (a spec like `1,1,3` is redundant, not wrong — dedup, not error).
 */
export function parseStageSpec(spec: string, total: number = STAGE_COUNT): number[] {
  const s = spec.trim()
  if (s === '') throw new StageSpecError(spec, 'empty spec')
  if (s.toLowerCase() === 'all') {
    const out: number[] = []
    for (let i = 0; i < total; i++) out.push(i)
    return out
  }
  const seen = new Set<number>()
  const out: number[] = []
  for (const raw of s.split(',')) {
    const tok = raw.trim()
    if (tok === '') throw new StageSpecError(spec, `empty token in "${s}"`)
    if (!TOKEN_RE.test(tok)) throw new StageSpecError(spec, `illegal token "${tok}"`)
    const dash = tok.indexOf('-')
    let lo: number
    let hi: number
    if (dash < 0) {
      lo = Number(tok)
      hi = lo
    } else {
      lo = Number(tok.slice(0, dash))
      hi = Number(tok.slice(dash + 1))
    }
    // 1-based CLI → 0-based internal.
    if (lo < 1 || hi < 1) throw new StageSpecError(spec, `token "${tok}": stages are 1-based`)
    if (lo > total || hi > total) {
      throw new StageSpecError(spec, `token "${tok}": out of range (1..${total})`)
    }
    if (hi < lo) throw new StageSpecError(spec, `token "${tok}": reverse range`)
    for (let n = lo; n <= hi; n++) {
      const idx = n - 1
      if (!seen.has(idx)) {
        seen.add(idx)
        out.push(idx)
      }
    }
  }
  if (out.length === 0) throw new StageSpecError(spec, 'no stages selected')
  return out
}

// ============================================================
// Run header (protocol §3.2) — every comparison tool prints the same
// caliber line so experiments can't silently mix口径:
//   difficulty / stage count / seed count / stageIndex / maxTicks / params hash
// ============================================================

/** FNV-1a over a stable (key-sorted) JSON encoding — params identity tag. */
export function paramsHash(params: unknown): string {
  const stable = (v: unknown): string => {
    if (v === null || typeof v !== 'object') return JSON.stringify(v) ?? 'null'
    if (Array.isArray(v)) return `[${v.map(stable).join(',')}]`
    const keys = Object.keys(v as Record<string, unknown>).sort()
    return `{${keys.map((k) => `${JSON.stringify(k)}:${stable((v as Record<string, unknown>)[k])}`).join(',')}}`
  }
  const str = stable(params)
  let h = 0x811c9dc5
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return h.toString(16).padStart(8, '0')
}

export interface RunHeaderInfo {
  difficulty: string
  stageCount: number
  seedCount: number
  /** The World stageIndex caliber — 0 unless the experiment says otherwise. */
  stageIndex: number
  maxTicks: number
  /** Baseline params (A). Paired A/B tools pass both sides. */
  params: unknown
  /** Candidate params (B) — printed as paramsB when present. */
  paramsB?: unknown
}

/** One-line caliber header; tools print it before any results. */
export function runHeader(info: RunHeaderInfo): string {
  const hashes =
    info.paramsB !== undefined
      ? `paramsA=${paramsHash(info.params)} paramsB=${paramsHash(info.paramsB)}`
      : `params=${paramsHash(info.params)}`
  return (
    `caliber: difficulty=${info.difficulty} stages=${info.stageCount} seeds=${info.seedCount} ` +
    `stageIndex=${info.stageIndex} maxTicks=${info.maxTicks} ` +
    hashes
  )
}
