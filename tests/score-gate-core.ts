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
  // §304 re-capture #7 (2026-08-29, merge of the guard-spawn fix §303 +
  // pursuit-tail §304): hard 0.7743 → 0.7775 (+0.32pt), chaos 0.7528 → 0.7534
  // (+0.06pt), classic byte-unchanged. Win-rate and score metrics disagree in
  // direction for hard — recorded in DECISIONS §304; the user's governing
  // metric is win rate.
  hard: [
    0.8743, 0.8001, 0.825, 0.7995, 0.7263, 0.731, 0.878, 0.569, 0.976, 0.8821, 0.807, 0.7252,
    0.7412, 0.5786, 0.8784, 0.7947, 0.6625, 0.9705, 0.8558, 0.5752, 0.7421, 0.8686, 0.9707, 0.6119,
    0.8532, 0.722, 0.8672, 0.6729, 0.8261, 0.8009, 0.7909, 0.7435, 0.8768, 0.3493, 0.8644,
  ],
  chaos: [
    0.7208, 0.8077, 0.6515, 0.6595, 0.5775, 0.7918, 0.5821, 0.4166, 0.9806, 0.8998, 0.8104, 0.6711,
    0.8699, 0.8819, 0.6659, 0.8958, 0.5009, 0.8893, 0.7149, 0.6534, 0.8085, 0.75, 0.9654, 0.6659,
    0.7946, 0.7923, 0.8869, 0.5813, 0.9537, 0.8927, 0.7859, 0.7366, 0.9394, 0.296, 0.8778,
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
