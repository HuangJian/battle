#!/usr/bin/env bun
/**
 * probe-parallel-parity.ts — Prove the worker-pool evaluation path is
 * byte-identical to the serial reference path, and measure wall-clock speedup.
 *
 * Method:
 *   1. Generate N deterministic candidates (seeded RNG over SEARCH_SPACE,
 *      candidate 0 = DEFAULT_GOD_AI_PARAMS) — no Math.random, reproducible.
 *   2. Evaluate all candidates serially (evaluateParams) and in parallel
 *      (evaluateCandidatesParallel on the ncpu−1 pool).
 *   3. Compare full EvalResults via JSON — fitness floats, rates, penalties
 *      and every perSeed record must match byte for byte.
 *   4. Report wall times and speedup.
 *
 * Usage: bun tools/perf/probe-parallel-parity.ts [--candidates=8] [--seeds=4]
 */
import { STAGES } from '../../src/config/stages'
import { RNG } from '../../src/utils/RNG'
import { DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../../src/ai/GodAIInput'
import {
  SEARCH_SPACE,
  vectorToParams,
  evaluateParams,
  evaluateCandidatesParallel,
  type EvalConfig,
} from '../optimize-godai'
import { SimWorkerPool } from '../sim-pool'

function argNum(name: string, fallback: number): number {
  const m = process.argv.find((a) => a.startsWith(`--${name}=`))
  return m ? parseInt(m.split('=')[1], 10) : fallback
}

const N_CANDIDATES = argNum('candidates', 8)
const N_SEEDS = argNum('seeds', 4)

// Deterministic candidate set: candidate 0 = defaults, rest sampled
// uniformly inside each param's [min, max] from a fixed-seed RNG.
const rng = new RNG(20260729)
const candidates: GodAIParams[] = [DEFAULT_GOD_AI_PARAMS]
for (let i = 1; i < N_CANDIDATES; i++) {
  const vec = SEARCH_SPACE.map((s) => s.min + rng.next() * (s.max - s.min))
  candidates.push(vectorToParams(vec))
}

const config: EvalConfig = {
  stage: STAGES[0],
  stages: [STAGES[0]],
  difficulty: 'classic',
  seeds: Array.from({ length: N_SEEDS }, (_, i) => i + 1),
  maxTicks: 18000,
}

console.log(
  `probe-parallel-parity: ${N_CANDIDATES} candidates x ${N_SEEDS} seeds (stage 0, classic)`,
)

// --- Serial reference (warmed: first pass amortizes main-thread JIT) ---
candidates.map((p) => evaluateParams(p, config))
const t0 = performance.now()
const serial = candidates.map((p) => evaluateParams(p, config))
const serialMs = performance.now() - t0
console.log(`serial:   ${serialMs.toFixed(0)}ms (warmed)`)

// --- Parallel ---
const pool = new SimWorkerPool()
// Warm the pool: each worker needs its own JIT warmup (~3 games to reach
// steady state, per sim-bench findings). Run the full batch once untimed so
// the timed run measures steady-state throughput, mirroring a long CMA-ES
// session where generation 1's warmup cost is amortized over hundreds.
await evaluateCandidatesParallel(pool, candidates, config)
const t1 = performance.now()
const parallel = await evaluateCandidatesParallel(pool, candidates, config)
const parallelMs = performance.now() - t1
pool.terminate()
console.log(`parallel: ${parallelMs.toFixed(0)}ms (${pool.size} workers, warmed)`)

// --- Byte-identical comparison ---
let mismatches = 0
for (let i = 0; i < candidates.length; i++) {
  const a = JSON.stringify(serial[i])
  const b = JSON.stringify(parallel[i])
  if (a !== b) {
    mismatches++
    console.error(`MISMATCH candidate ${i}:\n  serial:   ${a}\n  parallel: ${b}`)
  }
}

console.log(
  `compared=${candidates.length} mismatches=${mismatches} | speedup=${(serialMs / parallelMs).toFixed(2)}x`,
)
console.log(mismatches === 0 ? 'PARITY OK' : 'PARITY FAILED')
process.exit(mismatches === 0 ? 0 : 1)
