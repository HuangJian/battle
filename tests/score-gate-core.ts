// ============================================================
// Shared harness for the God-AI SCORE gate — WORKER POOL edition.
//
// This is NOT a test file (no `.test` infix). It is imported by
// `godai-score-gate.test.ts` and the `score-gate-worker.ts` Bun web worker.
//
// Counterpart to `gate-core.ts` (the pass-rate / clear-count gate). Where
// `gate-core.ts` asserts on *clear counts* (win rate), this gate asserts on
// the *godai-score v7 composite* of every run — so a regression that keeps a
// stage clearing but wrecks the underlying behavior (e.g. always turtles,
// never kills, drags clear-time to the ceiling) still trips the gate. Per
// Phase III this is the primary behavior-preservation guard; the clear-count
// gate is disabled (see AGENTS §6.3b).
//
// Parallelism: same reasoning as gate-core — a worker *pool* gives true
// in-process parallelism over the SAME isolates, no per-file cold start.
//
// §233 (2026-08-17): seeds 20 → 10 (suite <20s). Truth re-captured at 10
// seeds; MARGIN_SCORE 0.05→0.07 / AGG_MARGIN_SCORE 0.03→0.04 (~2 SE of the
// per-stage mean at n=10).
// ============================================================

import { runSimulation } from '../tools/sim/simulation-runner'
import { STAGES } from '../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { scoreRun, V7_SCORE_CONFIG } from '../tools/eval/godai-score'

// §233 (2026-08-17): seeds 20 → 10 to bring the full suite under 20s. The
// gate is deterministic — sims are a pure function of (seed, stage, difficulty)
// — so a 10-seed mean is a stable baseline, re-captured once. Margins widened
// to ~2 SE of the per-stage mean at n=10 (σ≈0.15 → SE≈0.047 → margin 0.07):
// the aggregate floor (35 stages × 10 seeds = 350 samples) stays statistically
// tight, the per-stage floor still catches single-stage regressions ~2 SE.
export const SCORE_SEEDS = Array.from({ length: 10 }, (_, i) => i + 1) // 1..10
/** Per-stage mean-score floor margin (every stage must stay within this of truth). */
export const MARGIN_SCORE = 0.07
/** Aggregate (mean over all stages) floor margin. */
export const AGG_MARGIN_SCORE = 0.04
export const STAGE_COUNT = STAGES.length // 35

// ---- Per-stage godai-score v7 truth (mean over SCORE_SEEDS) ----
// §233 (2026-08-17): re-captured at SCORE_SEEDS=10 (seeds 1-10) after the
// seeds 20→10 reduction (suite <20s target). Same harness/口径 as the 20-seed
// truth (telemetry on, v7 scoring) — the sims are deterministic, so a 10-seed
// mean is a stable baseline; per-stage means shift only by the sampling
// subset. Margins widened to ~2 SE of the per-stage mean at n=10
// (MARGIN_SCORE 0.05→0.07; AGG_MARGIN_SCORE 0.03→0.04). The aggregate floor
// (35 stages × 10 seeds = 350 samples) stays statistically tight.
// Re-capture via `tmp/capture-truth.ts` (must match SCORE_SEEDS).
export const TRUTH_SCORES: Record<string, number[]> = {
  classic: [
    0.9762, 0.8782, 0.9112, 0.966, 0.9703, 0.818, 0.785, 0.956, 0.9659, 0.9686, 0.8218, 0.6489,
    0.9295, 0.9774, 0.7204, 0.9651, 0.7426, 0.8946, 0.6467, 0.8249, 0.8257, 0.9613, 0.9461, 0.8149,
    0.8274, 0.6584, 0.8563, 0.9725, 0.8763, 0.891, 0.8959, 0.9529, 0.7305, 0.9715, 0.8945,
  ],
  hard: [
    0.878, 0.7352, 0.6829, 0.5787, 0.8128, 0.8521, 0.818, 0.8688, 0.8186, 0.7432, 0.9567, 0.6661,
    0.8553, 0.5029, 0.8708, 0.6689, 0.8591, 0.811, 0.7986, 0.6628, 0.7247, 0.6232, 0.9572, 0.5862,
    0.9411, 0.7193, 0.943, 0.6562, 0.9613, 0.8062, 0.7917, 0.7224, 0.9336, 0.4284, 0.8667,
  ],
  chaos: [
    0.7976, 0.8159, 0.8848, 0.6514, 0.7245, 0.6412, 0.5883, 0.5366, 0.81, 0.96, 0.8785, 0.5748,
    0.8218, 0.9449, 0.8006, 0.7276, 0.6501, 0.974, 0.7137, 0.5192, 0.7999, 0.5933, 0.9598, 0.5705,
    0.8109, 0.4929, 0.9401, 0.6654, 0.8831, 0.9504, 0.6323, 0.4386, 0.9372, 0.5119, 0.8742,
  ],
}

export const AGGREGATE_FLOOR_SCORE: Record<string, number> = {
  classic: aggFloor('classic'),
  hard: aggFloor('hard'),
  chaos: aggFloor('chaos'),
}

function aggFloor(d: string): number {
  const t = TRUTH_SCORES[d]
  const mean = t.reduce((a, b) => a + b, 0) / t.length
  return Math.max(0, mean - AGG_MARGIN_SCORE)
}

/** Run one stage for a difficulty across all SCORE_SEEDS; return mean v7 score. */
export function scoreStage(difficulty: string, idx: number): number {
  let sum = 0
  for (const seed of SCORE_SEEDS) {
    // Fresh params clone per run: the gate measures the SHIPPED defaults and
    // must be immune to any singleton pollution from other test files
    // (cross-file module state is shared in bun test — DECISIONS §98).
    const r = runSimulation({
      seed,
      stage: STAGES[idx],
      difficulty,
      maxTicks: 18000,
      telemetry: true, // required: v7 dims (baseIntegrity / accuracy / ...) need it
      // The scorer (scoreRun) reads only outcome/ticks/finalState/firstKillTick/
      // telemetry — the per-frame metrics array and the retained event log are
      // pure waste here. Skipping them is read-only, so outcomes and telemetry
      // (and therefore the v7 scores) are byte-identical (perf: ~30% faster sims).
      collectMetrics: false,
      collectEvents: false,
      godAIParams: { ...DEFAULT_GOD_AI_PARAMS },
    })
    sum += scoreRun(r, V7_SCORE_CONFIG).score
  }
  return sum / SCORE_SEEDS.length
}

// ============================================================
// Worker pool orchestration
// ============================================================

export interface ScoreJob {
  difficulty: string
  idx: number
}
export interface ScoreResult {
  difficulty: string
  idx: number
  score: number
}

function splitRoundRobin(jobs: ScoreJob[], n: number): ScoreJob[][] {
  const chunks: ScoreJob[][] = Array.from({ length: n }, () => [])
  jobs.forEach((job, i) => chunks[i % n].push(job))
  return chunks
}

function coreCount(): number {
  const env = Number(process.env.GATE_CORES)
  if (Number.isFinite(env) && env > 0) return Math.floor(env)
  // Default tuned for THIS host (mirrors gate-core: ~4 workers is fastest).
  return 4
}

/**
 * Fan out every (difficulty × stage) job across a Bun Worker pool and return a
 * Map keyed `"<difficulty>:<idx>"` → mean v7 score. Pure aggregation; the score
 * math lives in `scoreStage` (score-gate-worker.ts).
 */
export async function runGodAIScoreGate(
  difficulties: string[],
  stageCount: number = STAGE_COUNT,
): Promise<Map<string, number>> {
  const jobs: ScoreJob[] = []
  for (const d of difficulties)
    for (let i = 0; i < stageCount; i++) jobs.push({ difficulty: d, idx: i })

  const cores = coreCount()
  const chunks = splitRoundRobin(jobs, cores)

  const settled = await Promise.all(
    chunks.map(
      (chunk) =>
        new Promise<ScoreResult[]>((resolve, reject) => {
          const w = new Worker(new URL('./score-gate-worker.ts', import.meta.url))
          w.addEventListener('message', (ev: MessageEvent) => {
            resolve((ev.data as { results: ScoreResult[] }).results)
            w.terminate()
          })
          w.addEventListener('error', (err: unknown) => {
            w.terminate()
            reject(err)
          })
          w.postMessage({ jobs: chunk })
        }),
    ),
  )

  const scores = new Map<string, number>()
  for (const results of settled) {
    for (const r of results) scores.set(`${r.difficulty}:${r.idx}`, r.score)
  }
  return scores
}

export { STAGES }
