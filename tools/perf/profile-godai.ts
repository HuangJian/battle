/**
 * profile-godai.ts — Profiling harness for the REAL God-AI tuning workload.
 *
 * Mirrors exactly what `optimize-godai.ts` / `calibrate.ts` run: many headless
 * games via `runSimulation` (World + Simulation.tick + GodAIInput.endFrame).
 * This exists to feed `bun --cpu-prof` so we can see where the per-tick cost
 * actually goes. It prints aggregate ticks + wall time for a sanity check.
 *
 * Run:
 *   bun --cpu-prof --cpu-prof-name=/tmp/sim-godai.cpuprofile tools/perf/profile-godai.ts
 *   bun tools/perf/profile-godai.ts            # timing only
 */
import { runSimulation } from '../simulation-runner'
import { STAGES } from '../../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'

const N = Number(process.argv.find((a) => a.startsWith('--games'))?.split('=')[1] ?? 30)
const DIFF = process.argv.find((a) => a.startsWith('--diff'))?.split('=')[1] ?? 'chaos'
const STAGE_IDX = Number(process.argv.find((a) => a.startsWith('--stage'))?.split('=')[1] ?? 0)
const WARMUP = Number(process.argv.find((a) => a.startsWith('--warmup'))?.split('=')[1] ?? 3)

const stage = STAGES[STAGE_IDX]
if (!stage) {
  console.error(`No stage at index ${STAGE_IDX}`)
  process.exit(1)
}

// Warmup: run a few games first (separate seed range) so the JIT is hot and
// the timed loop measures steady-state cost, not cold-start. Warmup games use
// a distinct negative seed range so the TIMED seeds (1000+g*7) stay identical
// run-to-run — keeping the determinism signature comparable across measures.
for (let g = 0; g < WARMUP; g++) {
  runSimulation({
    seed: -1000 - g * 7,
    stage,
    difficulty: DIFF,
    godAIParams: DEFAULT_GOD_AI_PARAMS,
    maxTicks: 36000,
    sampleInterval: 100000,
  })
}

const t0 = performance.now()
let totalTicks = 0
let wins = 0
let gameovers = 0
let timeouts = 0
for (let g = 0; g < N; g++) {
  const res = runSimulation({
    seed: 1000 + g * 7,
    stage,
    difficulty: DIFF,
    godAIParams: DEFAULT_GOD_AI_PARAMS,
    maxTicks: 36000,
    sampleInterval: 100000, // disable per-frame metric sampling to isolate sim core
  })
  totalTicks += res.ticks
  if (res.outcome === 'stage_clear') wins++
  else if (res.outcome === 'gameover') gameovers++
  else timeouts++
}
const wall = performance.now() - t0
const msPerTick = wall / totalTicks
console.log(
  `games=${N} diff=${DIFF} stage=${STAGE_IDX} | ticks=${totalTicks} wall=${wall.toFixed(0)}ms ` +
    `(${(wall / N).toFixed(1)}ms/game) | perTick=${msPerTick.toFixed(3)}ms ` +
    `| win=${wins} go=${gameovers} timeout=${timeouts}`,
)
