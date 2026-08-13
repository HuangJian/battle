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
// Captured from the merged main+origin/idle AI (2026-08-13 re-baseline after
// the idle-merge). Re-capture by running `bun tools/.../...` — see
// godai-score-gate.test.ts header. Values are the per-stage mean v7 composite
// across SCORE_SEEDS=20 sims (telemetry on). classic is byte-identical to the
// prior baseline; hard/chaos re-baselined to the shipped §192–§193-E AI.
export const TRUTH_SCORES: Record<string, number[]> = {
  classic: [
    0.9725, 0.8871, 0.9416, 0.9714, 0.964, 0.846, 0.8019, 0.9574, 0.9617, 0.9315,
    0.8467, 0.7054, 0.9323, 0.9741, 0.8458, 0.9617, 0.7735, 0.8923, 0.6358, 0.8142,
    0.8645, 0.9284, 0.9145, 0.8861, 0.8197, 0.7282, 0.7936, 0.9324, 0.8399, 0.9333,
    0.8863, 0.9513, 0.7673, 0.958, 0.8052,
  ],
  hard: [
    0.8439, 0.8424, 0.7447, 0.6986, 0.7342,
    0.7926, 0.8212, 0.5052, 0.8164, 0.8531,
    0.9212, 0.6695, 0.7967, 0.5878, 0.7952,
    0.7371, 0.9386, 0.8185, 0.7683, 0.6242,
    0.6937, 0.6017, 0.9503, 0.5818, 0.8318,
    0.7988, 0.9085, 0.6698, 0.8839, 0.9046,
    0.7905, 0.7684, 0.871, 0.3843, 0.7964,
  ],
  chaos: [
    0.798, 0.8389, 0.775, 0.733, 0.6791,
    0.5714, 0.5892, 0.5523, 0.8864, 0.8863,
    0.9126, 0.5529, 0.9138, 0.8423, 0.8321,
    0.7676, 0.6567, 0.9664, 0.6466, 0.6853,
    0.7649, 0.5886, 0.9195, 0.4995, 0.8022,
    0.4144, 0.8673, 0.5915, 0.78, 0.953,
    0.6067, 0.6173, 0.863, 0.4011, 0.8405,
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
