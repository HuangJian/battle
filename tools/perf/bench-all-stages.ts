/**
 * bench-all-stages.ts — Per-stage wall-time baseline across all 35 stages.
 *
 * Runs N games per stage (default 10) with W warmup games, prints a per-stage
 * table + total. Used to locate the slowest stages and measure cross-stage
 * optimization impact with a single command.
 *
 * DIFFICULTY COVERAGE (§128): by default ALL THREE difficulties run —
 * classic / hard / chaos, each over the full 35 stages × N games (each
 * difficulty is 1/3 of the baseline load). This makes the baseline exercise
 * the God-AI-heavy paths (hard/chaos: replanInterval=1, pool combat) that
 * classic alone (replanInterval=50 via CLASSIC_MODEL_PARAMS) barely touches —
 * §127 showed classic-only baselines hid the -27~32% chaos win.
 *
 *   --diff=classic|hard|chaos   restrict to ONE difficulty (pre-§128 behavior)
 *
 * Run:  bun tools/perf/bench-all-stages.ts
 *       bun tools/perf/bench-all-stages.ts --games=20 --warmup=2
 *       bun tools/perf/bench-all-stages.ts --diff=chaos
 */
import { runSimulation } from '../sim/simulation-runner'
import { STAGES } from '../../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'

const N = Number(process.argv.find((a) => a.startsWith('--games'))?.split('=')[1] ?? 10)
const WARMUP = Number(process.argv.find((a) => a.startsWith('--warmup'))?.split('=')[1] ?? 2)
const DIFF_ARG = process.argv.find((a) => a.startsWith('--diff'))?.split('=')[1]
// §128: default = all three difficulties, each 1/3 of the baseline load.
const DIFFS: string[] = DIFF_ARG ? [DIFF_ARG] : ['classic', 'hard', 'chaos']

// Warmup once per difficulty (shared JIT state) — stage-0, distinct negative seeds.
for (const diff of DIFFS) {
  for (let g = 0; g < WARMUP; g++) {
    runSimulation({
      seed: -1000 - g * 7,
      stage: STAGES[0],
      difficulty: diff,
      godAIParams: DEFAULT_GOD_AI_PARAMS,
      maxTicks: 36000,
      sampleInterval: 100000,
    })
  }
}

let grandWall = 0
let grandTicks = 0
let grandWins = 0
const grandGames = DIFFS.length * N * STAGES.length
const diffWalls: Array<{ diff: string; wall: number }> = []

for (const diff of DIFFS) {
  let totalWall = 0
  let totalTicks = 0
  let totalWins = 0
  const rows: Array<{ idx: number; name: string; wall: number; ticks: number; wins: number }> = []

  for (let s = 0; s < STAGES.length; s++) {
    const stage = STAGES[s]
    let wins = 0
    let ticks = 0
    const t0 = performance.now()
    for (let g = 0; g < N; g++) {
      const res = runSimulation({
        seed: 1000 + g * 7,
        stage,
        difficulty: diff,
        godAIParams: DEFAULT_GOD_AI_PARAMS,
        maxTicks: 36000,
        sampleInterval: 100000,
      })
      ticks += res.ticks
      if (res.outcome === 'stage_clear') wins++
    }
    const wall = performance.now() - t0
    totalWall += wall
    totalTicks += ticks
    totalWins += wins
    rows.push({ idx: s, name: stage.name, wall, ticks, wins })
  }

  grandWall += totalWall
  grandTicks += totalTicks
  grandWins += totalWins
  diffWalls.push({ diff, wall: totalWall })

  console.log(`\n=== ${diff} / all 35 stages / ${N} games each (warmup=${WARMUP}) ===`)
  console.log(
    `${'stage'.padStart(5)}  ${'name'.padEnd(20)}  ${'wall(ms)'.padStart(9)}  ${'ticks'.padStart(8)}  ${'win'.padStart(5)}`,
  )
  for (const r of rows) {
    console.log(
      `S${(r.idx + 1).toString().padStart(2, '0')}    ${r.name.padEnd(20)}  ${r.wall.toFixed(0).padStart(7)}ms  ${r.ticks.toString().padStart(7)}  ${(r.wins + '/' + N).padStart(5)}`,
    )
  }
  console.log(
    `\n${diff.toUpperCase()} TOTAL  wall=${totalWall.toFixed(0)}ms  ticks=${totalTicks}  wins=${totalWins}/${N * STAGES.length}`,
  )
  console.log(`perTick=${(totalWall / totalTicks).toFixed(4)}ms`)
}

if (DIFFS.length === 1) {
  // Single-difficulty mode: the per-difficulty TOTAL above is the only total
  // (pre-§128 behavior) — no redundant GRAND TOTAL block.
  process.exit(0)
}
console.log(`\n=== GRAND TOTAL (${DIFFS.join('+')}) ===`)
console.log(
  `wall=${grandWall.toFixed(0)}ms  ticks=${grandTicks}  wins=${grandWins}/${grandGames}  perTick=${(grandWall / grandTicks).toFixed(4)}ms`,
)
console.log(
  `difficulty split: ${diffWalls.map((d) => `${d.diff} ${d.wall.toFixed(0)}ms (${((d.wall / grandWall) * 100).toFixed(0)}%)`).join('  ')}`,
)
