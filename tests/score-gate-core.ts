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
// ============================================================

import { runSimulation } from '../tools/sim/simulation-runner'
import { STAGES } from '../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { scoreRun, V7_SCORE_CONFIG } from '../tools/eval/godai-score'

export const SCORE_SEEDS = Array.from({ length: 20 }, (_, i) => i + 1) // 1..20
/** Per-stage mean-score floor margin (every stage must stay within this of truth). */
export const MARGIN_SCORE = 0.05
/** Aggregate (mean over all stages) floor margin. */
export const AGG_MARGIN_SCORE = 0.03
export const STAGE_COUNT = STAGES.length // 35

// ---- Per-stage godai-score v7 truth (mean over SCORE_SEEDS) ----
// Captured from current HEAD (2026-08-12, Phase III baseline). Re-capture by
// running `bun tools/.../...` — see godai-score-gate.test.ts header. Values are
// the per-stage mean v7 composite across SCORE_SEEDS=20 sims (telemetry on).
export const TRUTH_SCORES: Record<string, number[]> = {
  classic: [
    0.9725, 0.8871, 0.9416, 0.9714, 0.964, 0.846, 0.8019, 0.9574, 0.9617, 0.9315,
    0.8467, 0.7054, 0.9323, 0.9741, 0.8458, 0.9617, 0.7735, 0.8923, 0.6358, 0.8142,
    0.8645, 0.9284, 0.9145, 0.8861, 0.8197, 0.7282, 0.7936, 0.9324, 0.8399, 0.9333,
    0.8863, 0.9513, 0.7673, 0.958, 0.8052,
  ],
  hard: [
    0.7356, 0.7306, 0.7385, 0.692, 0.6185, 0.7213, 0.7802, 0.4416, 0.8861, 0.8474,
    0.6775, 0.7528, 0.8632, 0.6894, 0.885, 0.8109, 0.7757, 0.9264, 0.753, 0.495,
    0.7953, 0.6923, 0.9611, 0.6183, 0.7966, 0.7231, 0.8027, 0.5264, 0.8415, 0.8455,
    0.7834, 0.7681, 0.8986, 0.3794, 0.7598,
  ],
  chaos: [
    0.7377, 0.8053, 0.6946, 0.6144, 0.657, 0.7925, 0.8466, 0.5397, 0.6997, 0.768,
    0.8408, 0.6171, 0.6875, 0.7219, 0.8045, 0.6575, 0.7545, 0.9249, 0.7591, 0.6164,
    0.7043, 0.7016, 0.9737, 0.4335, 0.7653, 0.5285, 0.8399, 0.5231, 0.7016, 0.8668,
    0.7139, 0.8038, 0.8095, 0.3687, 0.7066,
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
