// ============================================================
// Shared harness for the God-AI SCORE gate — WORKER POOL edition.
//
// This is NOT a test file (no `.test` infix). It is imported by
// `godai-score-gate.test.ts` and the `score-gate-worker.ts` Bun web worker.
//
// Counterpart to the retired `gate-core.ts` (pass-rate / clear-count gate,
// since deleted from tests/ — recoverable from git history). Where that gate
// asserted on *clear counts* (win rate), this gate asserts on
// the *godai-score v7 composite* of every run — so a regression that keeps a
// stage clearing but wrecks the underlying behavior (e.g. always turtles,
// never kills, drags clear-time to the ceiling) still trips the gate. Per
// Phase III this is the primary behavior-preservation guard; the clear-count
// gate is disabled (see AGENTS §6.3b).
//
// Parallelism: same reasoning as that gate — a worker *pool* gives true
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
import { runChunkedWorkers, gateCoreCount, splitRoundRobin } from '../tools/lib/worker-pool'

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
// seeds 20→10 reduction (suite <20s target).
// §276 (2026-08-26): re-captured after the code-review batch fixes
// (footprint-aware buildCarveCosts / ring-steel fire block / EnemyModel
// corrections) shifted sim behavior — same harness/口径, deterministic sims.
// 2026-08-26 (post window-0 merge): re-captured after the super-item knobs
// retired to OFF by default (AI-No-Items-Warmstart M0, plan §0.2 R4 cost:
// hard 0.7663→0.7575 (−0.89pt), chaos 0.7562→0.7372 (−1.9pt)).
// 2026-08-28 (DECISIONS §293): God AI unfrozen — super-item activation restored
// (superItemMode/superItemGuardThreat → 1). hard returns to 0.7663, chaos to
// 0.7562 — the exact pre-retirement numbers, i.e. the M0 cost is fully reversed;
// classic byte-unchanged (superItemMode kept 0 via CLASSIC_MODEL_PARAMS).
// Re-capture with `bun tools/diag/recapture-score-truth.ts` (standing tool).
export const TRUTH_SCORES: Record<string, number[]> = {
  classic: [
    0.9763, 0.8795, 0.9112, 0.966, 0.9731, 0.8188, 0.8526, 0.956, 0.9651, 0.9616, 0.8218, 0.648,
    0.9295, 0.9774, 0.7204, 0.9614, 0.7426, 0.8946, 0.7148, 0.8249, 0.8221, 0.9605, 0.9492, 0.8149,
    0.8274, 0.6587, 0.8563, 0.9725, 0.8763, 0.891, 0.9002, 0.9529, 0.6606, 0.9064, 0.8945,
  ],
  // §303 re-capture (2026-08-29): pursuitTailMode=7 + AlongMode=3 shipped ON.
  // hard 0.7663 → 0.7890 (+2.26pt); chaos 0.7562 → 0.7337 (−2.25pt);
  // classic byte-unchanged (pursuitTailMode kept 0 via CLASSIC_OVERRIDES).
  hard: [
    0.8015, 0.7869, 0.8185, 0.7273, 0.8128, 0.6498, 0.7175, 0.7325, 0.9734, 0.6682, 0.7963, 0.6011,
    0.6514, 0.7182, 0.7432, 0.8197, 0.8691, 0.9501, 0.7905, 0.5881, 0.8261, 0.8581, 0.9663, 0.8023,
    0.9298, 0.726, 0.937, 0.734, 0.8934, 0.8908, 0.7932, 0.8013, 0.9499, 0.3723, 0.918,
  ],
  chaos: [
    0.8968, 0.7972, 0.8116, 0.7244, 0.6669, 0.6414, 0.5738, 0.5521, 0.8936, 0.8801, 0.6576, 0.5395,
    0.8756, 0.7112, 0.6674, 0.9006, 0.8054, 0.743, 0.6503, 0.3595, 0.8099, 0.7371, 0.9658, 0.4647,
    0.8131, 0.6485, 0.7182, 0.6649, 0.9575, 0.7171, 0.7216, 0.8785, 0.9277, 0.3556, 0.9508,
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

  const cores = gateCoreCount()
  const chunks = splitRoundRobin(jobs, cores)

  // Chunk-per-worker fan-out via the shared pool helper (§3.6).
  const settled = await runChunkedWorkers<{ jobs: ScoreJob[] }, ScoreResult>(
    new URL('./score-gate-worker.ts', import.meta.url).href,
    chunks.map((jobs) => ({ jobs })),
  )

  const scores = new Map<string, number>()
  for (const r of settled) scores.set(`${r.difficulty}:${r.idx}`, r.score)
  return scores
}

export { STAGES }
