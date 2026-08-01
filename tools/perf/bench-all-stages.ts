/**
 * bench-all-stages.ts — Per-stage wall-time baseline across all 35 classic stages.
 *
 * Runs N games per stage (default 10) with W warmup games, prints a per-stage
 * table + total. Used to locate the slowest stages and measure cross-stage
 * optimization impact with a single command.
 *
 * Run:  bun tools/perf/bench-all-stages.ts
 *       bun tools/perf/bench-all-stages.ts --games=20 --warmup=2
 */
import { runSimulation } from '../sim/simulation-runner'
import { STAGES } from '../../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'

const N = Number(process.argv.find((a) => a.startsWith('--games'))?.split('=')[1] ?? 10)
const WARMUP = Number(process.argv.find((a) => a.startsWith('--warmup'))?.split('=')[1] ?? 2)
const DIFF = process.argv.find((a) => a.startsWith('--diff'))?.split('=')[1] ?? 'classic'

// Warmup once (shared JIT state) — stage-0, distinct negative seeds.
for (let g = 0; g < WARMUP; g++) {
  runSimulation({
    seed: -1000 - g * 7,
    stage: STAGES[0],
    difficulty: DIFF,
    godAIParams: DEFAULT_GOD_AI_PARAMS,
    maxTicks: 36000,
    sampleInterval: 100000,
  })
}

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
      difficulty: DIFF,
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

console.log(`\n=== ${DIFF} / all 35 stages / ${N} games each (warmup=${WARMUP}) ===`)
console.log(
  `${'stage'.padStart(5)}  ${'name'.padEnd(20)}  ${'wall(ms)'.padStart(9)}  ${'ticks'.padStart(8)}  ${'win'.padStart(5)}`,
)
for (const r of rows) {
  console.log(
    `S${r.idx.toString().padStart(2, '0')}    ${r.name.padEnd(20)}  ${r.wall.toFixed(0).padStart(7)}ms  ${r.ticks.toString().padStart(7)}  ${(r.wins + '/' + N).padStart(5)}`,
  )
}
console.log(
  `\nTOTAL  wall=${totalWall.toFixed(0)}ms  ticks=${totalTicks}  wins=${totalWins}/${N * STAGES.length}`,
)
console.log(`perTick=${(totalWall / totalTicks).toFixed(4)}ms`)
