// ============================================================
// Shared harness for the God-AI regression gate — WORKER POOL edition.
//
// This is NOT a test file (no `.test` infix). It is imported by
// `god-ai-gate.test.ts` and the `gate-worker.ts` Bun web worker.
//
// Why a worker POOL (and not `--parallel` file-split, and not `test.concurrent`)?
//   * The gate is ~2100 CPU-bound headless `runSimulation` calls (35 stages ×
//     3 difficulties × 20 seeds). `test.concurrent` is cooperative in bun and
//     does NOT parallelize synchronous work → single core only.
//   * File-level `--parallel` *does* spread work across processes, but the
//     previous 14-part split was SLOWER (48.75s) than the original single
//     file (~33s) because the per-part fixed overhead + cold-start of 14
//     worker PROCESSES dominated the small per-part work.
//   * A single Bun Worker *thread* pool inside ONE test file gives true
//     in-process parallelism over the SAME isolates, no per-file cold start,
//     and shares the already-loaded game engine → ~13-26s for all 2100 sims.
//
// Thread safety: Bun Workers are isolated isolates. `World.genId()`'s
// module-level counter (`let nextId` in World.ts) is therefore NOT shared
// across workers — each isolate has its own. `runSimulation` is a pure
// function (fresh World/RNG/Simulation per call) so it is safe to fan out.
// ============================================================

// NOTE: use the GLOBAL Bun `Worker` (web-worker style: supports
// addEventListener + self.onmessage/self.postMessage). Do NOT import from
// `node:worker_threads` — that variant has no addEventListener and silently
// fails to deliver messages.
import { runSimulation } from '../tools/sim/simulation-runner'
import { STAGES } from '../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'

export const GATE_SEEDS = Array.from({ length: 20 }, (_, i) => i + 1) // 1..20
export const MARGIN_WINS = 4
export const STAGE_COUNT = STAGES.length // 35

// ---- Per-stage win-count truth (out of GATE_SEEDS=20) ----

// Hard + chaos truth measured at 35×20 (re-based 2026-08-09, §134 drop-range
// re-tune 1/2/3 -> 2/4/6 + spawn avoidance + §134 defenseInterceptMode=1).
// Values are the 60-seed winrate converted to a 20-seed-equivalent count
// (round(pct*20)) so they drop straight into the 20-seed floor math.
export const HARD_TRUTH_WINS: number[] = [
  18, 18, 12, 14, 13, 13, 16, 7, 17, 17, 18, 12, 13, 11, 15, 13, 16, 18, 17, 11, 13, 13, 20, 9, 17,
  15, 15, 13, 16, 17, 16, 16, 18, 7, 17,
]

export const CHAOS_TRUTH_WINS: number[] = [
  15, 16, 13, 15, 12, 11, 12, 8, 16, 15, 16, 9, 15, 15, 14, 14, 15, 19, 14, 10, 13, 12, 20, 8, 17,
  10, 16, 10, 16, 17, 15, 13, 18, 4, 16,
]

// Classic truth calibrated 2026-08-10 via tmp/calib-classic.ts (worker pool,
// 35×20). Sims are deterministic per seed, so these exact counts are a stable
// baseline; the per-stage floor (truth − MARGIN_WINS) catches future regressions.
export const CLASSIC_TRUTH_WINS: number[] = [
  20, 18, 19, 20, 20, 17, 16, 20, 20, 19, 17, 13, 20, 20, 17, 20, 15, 18, 12, 16, 17, 20, 19, 18,
  16, 14, 15, 19, 17, 19, 18, 20, 15, 20, 16,
]

// ---- Aggregate floors: truth mean − 3.7pp (3 binomial sd at n=700) ----
export const HARD_AGGREGATE_FLOOR = Math.floor(
  ((72.86 - 3.7) / 100) * STAGE_COUNT * GATE_SEEDS.length,
) // 484/700
export const CHAOS_AGGREGATE_FLOOR = Math.floor(
  ((68.71 - 3.7) / 100) * STAGE_COUNT * GATE_SEEDS.length,
) // 455/700
// Classic floor: calibrated mean 88.6% − 3.7pp (3 binomial sd at n=700) = 594/700.
export const CLASSIC_AGGREGATE_FLOOR = Math.floor(
  ((88.6 - 3.7) / 100) * STAGE_COUNT * GATE_SEEDS.length,
) // 594/700

export const TRUTH: Record<string, number[]> = {
  classic: CLASSIC_TRUTH_WINS,
  hard: HARD_TRUTH_WINS,
  chaos: CHAOS_TRUTH_WINS,
}
export const AGGREGATE_FLOOR: Record<string, number> = {
  classic: CLASSIC_AGGREGATE_FLOOR,
  hard: HARD_AGGREGATE_FLOOR,
  chaos: CHAOS_AGGREGATE_FLOOR,
}

export function stageFloor(truth: number[]): (idx: number) => number {
  // truth holds per-stage WIN COUNTS out of GATE_SEEDS (not percentages).
  return (idx: number) => Math.max(0, truth[idx] - MARGIN_WINS)
}

/** Run one stage for a difficulty across all GATE_SEEDS; return win count. */
export function runStage(difficulty: string, idx: number): number {
  let wins = 0
  for (const seed of GATE_SEEDS) {
    // Fresh params clone per run: the gate measures the SHIPPED defaults and
    // must be immune to any singleton pollution from other test files
    // (cross-file module state is shared in bun test — DECISIONS §98).
    const r = runSimulation({
      seed,
      stage: STAGES[idx],
      difficulty,
      maxTicks: 18000,
      sampleInterval: 18000,
      // The gate only counts `outcome === 'stage_clear'` — the metrics array
      // and retained event log are pure waste (skipping them is read-only).
      collectMetrics: false,
      collectEvents: false,
      godAIParams: { ...DEFAULT_GOD_AI_PARAMS },
    })
    if (r.outcome === 'stage_clear') wins++
  }
  return wins
}

// ============================================================
// Worker pool orchestration
// ============================================================

export interface GateJob {
  difficulty: string
  idx: number
}
export interface GateResult {
  difficulty: string
  idx: number
  wins: number
}

function splitRoundRobin(jobs: GateJob[], n: number): GateJob[][] {
  const chunks: GateJob[][] = Array.from({ length: n }, () => [])
  jobs.forEach((job, i) => chunks[i % n].push(job))
  return chunks
}

function coreCount(): number {
  const env = Number(process.env.GATE_CORES)
  if (Number.isFinite(env) && env > 0) return Math.floor(env)
  // Default tuned for THIS host. `navigator.hardwareConcurrency` reports 16
  // logical CPUs, but the pool is FASTEST at ~4 workers — beyond that, extra
  // workers contend and slow down (measured: 1→10.5s, 4→6.1s, 8→7.5s, 16→10.3s
  // for 700 classic sims; full 2100-sim gate: 4→27.9s vs 16→36s). Over-
  // subscribing wastes time, so we cap the default well below the reported count.
  return 4
}

/**
 * Fan out every (difficulty × stage) job across a Bun Worker pool and return a
 * Map keyed `"<difficulty>:<idx>"` → win count. Pure aggregation: the win math
 * lives in `runStage` (gate-worker.ts), keeping this side free of CPU work.
 */
export async function runGodAIGate(
  difficulties: string[],
  stageCount: number = STAGE_COUNT,
): Promise<Map<string, number>> {
  const jobs: GateJob[] = []
  for (const d of difficulties)
    for (let i = 0; i < stageCount; i++) jobs.push({ difficulty: d, idx: i })

  const cores = coreCount()
  const chunks = splitRoundRobin(jobs, cores)

  const settled = await Promise.all(
    chunks.map(
      (chunk) =>
        new Promise<GateResult[]>((resolve, reject) => {
          const w = new Worker(new URL('./gate-worker.ts', import.meta.url))
          // NOTE: Bun's Worker `onmessage`/`onerror` *property* assignment does
          // not fire — use addEventListener (validated). `self.onmessage` inside
          // the worker is fine.
          w.addEventListener('message', (ev: MessageEvent) => {
            resolve((ev.data as { results: GateResult[] }).results)
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

  const wins = new Map<string, number>()
  for (const results of settled) {
    for (const r of results) wins.set(`${r.difficulty}:${r.idx}`, r.wins)
  }
  return wins
}

export { STAGES }
