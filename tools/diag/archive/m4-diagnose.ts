#!/usr/bin/env bun
/**
 * m4-diagnose.ts — M4 (safe power-up / star growth) diagnosis pass.
 *
 * Question 1: is low star level the hard bottleneck? Compare final
 * playerLevel + star pickups between clears and failures, per stage.
 * Question 2: in failures, did star power-ups exist on the field but go
 * unpicked (spawned vs collected census), and where/when were they missed?
 *
 * Read-only: runs DEFAULT params, no behavior change, no Math.random.
 * Output: compact summary + tmp/m4-diag.json (per-run rows).
 */
import { STAGES } from '../../../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../../../src/ai/GodAIInput'
import { SimWorkerPool } from '../../sim/sim-pool'
import type { SimTask } from '../../sim/sim-worker'

const seeds = Array.from({ length: 60 }, (_, i) => i + 1)
const stageIdxs = Array.from({ length: STAGES.length }, (_, i) => i)

const tasks: SimTask[] = []
const meta: Array<{ stageIdx: number; seed: number }> = []
for (const stageIdx of stageIdxs) {
  for (const seed of seeds) {
    tasks.push({
      id: tasks.length,
      seed,
      stage: STAGES[stageIdx],
      difficulty: 'hard',
      maxTicks: 18000,
      params: { ...DEFAULT_GOD_AI_PARAMS },
      forensics: true,
    })
    meta.push({ stageIdx, seed })
  }
}

const pool = new SimWorkerPool()
process.stderr.write(
  `m4-diagnose: hard × ${stageIdxs.length} stages × ${seeds.length} seeds = ${tasks.length} runs (${pool.size} workers)\n`,
)
const t0 = Date.now()
const results = await pool.runBatch(tasks)
pool.terminate()
process.stderr.write(
  `m4-diagnose: ran ${results.length} runs in ${((Date.now() - t0) / 1000).toFixed(1)}s\n`,
)

interface Row {
  stage: number
  seed: number
  outcome: string
  finalLevel: number
  starsCollected: number
  kills: number
  deaths: number
  pickups: Record<string, number>
  baseHp: number
  ticks: number
}

const rows: Row[] = []
for (let i = 0; i < results.length; i++) {
  const r = results[i]
  const fx = r.forensics
  rows.push({
    stage: meta[i].stageIdx,
    seed: meta[i].seed,
    outcome: r.outcome,
    finalLevel: fx?.terminal?.playerLevel ?? 0,
    starsCollected:
      fx?.events.filter((e) => e.type === 'pickup' && e.detail === 'star').length ?? 0,
    kills: fx?.kills ?? 0,
    deaths: fx?.playerDeaths ?? 0,
    pickups: fx?.inventory ?? {},
    baseHp: r.baseAlive ? 120 : 0,
    ticks: r.ticks,
  })
}

// ---- Summary 1: final level by outcome ----
const byOutcome = new Map<string, Row[]>()
for (const row of rows) byOutcome.set(row.outcome, [...(byOutcome.get(row.outcome) ?? []), row])

console.log(`=== M4 diagnosis (hard 35×60, ${((Date.now() - t0) / 1000).toFixed(1)}s) ===`)
for (const [name, arr] of byOutcome) {
  const levelDist: Record<number, number> = {}
  for (const row of arr) levelDist[row.finalLevel] = (levelDist[row.finalLevel] ?? 0) + 1
  console.log(
    `[${name}] n=${arr.length}  avgFinalLevel=${(arr.reduce((a, r) => a + r.finalLevel, 0) / arr.length).toFixed(2)}` +
      `  levelDist=${JSON.stringify(levelDist)}  avgStarsPicked=${(arr.reduce((a, r) => a + r.starsCollected, 0) / arr.length).toFixed(2)}` +
      `  avgKills=${(arr.reduce((a, r) => a + r.kills, 0) / arr.length).toFixed(2)}` +
      `  avgDeaths=${(arr.reduce((a, r) => a + r.deaths, 0) / arr.length).toFixed(2)}`,
  )
}

// ---- Summary 2: per-stage clear rate vs avg final level ----
console.log('\n=== per-stage: clearRate | avgFinalLevel (clears only) | avgStarsPicked ===')
const perStage = new Map<number, Row[]>()
for (const row of rows) perStage.set(row.stage, [...(perStage.get(row.stage) ?? []), row])
const weak: Array<[number, number, number]> = []
for (const [st, arr] of perStage) {
  const clears = arr.filter((r) => r.outcome === 'stage_clear')
  const clearRate = clears.length / arr.length
  const avgLevel = clears.reduce((a, r) => a + r.finalLevel, 0) / Math.max(1, clears.length)
  const avgStars = arr.reduce((a, r) => a + r.starsCollected, 0) / arr.length
  weak.push([st, clearRate, avgLevel])
  console.log(
    `S${st + 1}: clearRate=${(clearRate * 100).toFixed(0)}%  avgLevel(clears)=${avgLevel.toFixed(2)}  avgStars=${avgStars.toFixed(2)}`,
  )
}

console.log('\nworst 8 by clearRate:')
for (const [st, cr, lvl] of weak.sort((a, b) => a[1] - b[1]).slice(0, 8)) {
  console.log(`  S${st + 1}: ${(cr * 100).toFixed(0)}%  avgLevel=${lvl.toFixed(2)}`)
}

await Bun.write('tmp/m4-diag.json', JSON.stringify(rows))
console.log('\nrows → tmp/m4-diag.json')

// ---- Summary 4: star census on failed runs (spawn vs pickup vs missed) ----
// Re-run only the failed (gameover) runs with the census observer: does the
// player get star opportunities it ignores (star on field, player far away /
// never within pickup reach), and do failed runs simply never see stars?
const failedRows = rows.filter((r) => r.outcome !== 'stage_clear')
console.log(`\n=== star census on ${failedRows.length} failed runs ===`)
const censusTasks: SimTask[] = []
const censusMeta: Array<{ stageIdx: number; seed: number }> = []
for (let i = 0; i < failedRows.length; i++) {
  const r = failedRows[i]
  censusTasks.push({
    id: censusTasks.length,
    seed: r.seed,
    stage: STAGES[r.stage],
    difficulty: 'hard',
    maxTicks: 18000,
    params: { ...DEFAULT_GOD_AI_PARAMS },
    powerupCensus: true,
  })
  censusMeta.push({ stageIdx: r.stage, seed: r.seed })
}
const pool2 = new SimWorkerPool()
const t1 = Date.now()
const censusResults = await pool2.runBatch(censusTasks)
pool2.terminate()
console.log(
  `census pass: ${censusResults.length} runs in ${((Date.now() - t1) / 1000).toFixed(1)}s`,
)
let spawned = 0
let picked = 0
let missedNever = 0 // star existed but player never got within 64px (4 cells)
let missedClose = 0 // star existed, player got within 64px, still not picked
let noStarsRuns = 0
const misses: Array<{
  stage: number
  seed: number
  spawnTick: number
  minDist: number
  despawnTick: number
}> = []
for (let i = 0; i < censusResults.length; i++) {
  const r = censusResults[i]
  const c = r.powerupCensus
  if (!c) continue
  if (c.spawned === 0) noStarsRuns++
  spawned += c.spawned
  picked += c.picked
  for (const s of c.stars) {
    if (!s.picked) {
      if (s.minDist < 0 || s.minDist >= 64) missedNever++
      else missedClose++
      misses.push({
        stage: censusMeta[i].stageIdx,
        seed: censusMeta[i].seed,
        spawnTick: s.spawnTick,
        minDist: s.minDist,
        despawnTick: s.despawnTick,
      })
    }
  }
}
console.log(`failed runs with NO star spawn: ${noStarsRuns}/${censusResults.length}`)
console.log(
  `star spawned: ${spawned} | picked by player: ${picked} (${((picked / Math.max(1, spawned)) * 100).toFixed(0)}%)`,
)
console.log(`missed: never-in-4-cells=${missedNever}  within-4-cells-but-not-picked=${missedClose}`)
const byStageMiss = new Map<number, number>()
for (const m of misses) byStageMiss.set(m.stage, (byStageMiss.get(m.stage) ?? 0) + 1)
console.log('misses by stage (top 10):')
for (const [st, n] of [...byStageMiss.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10)) {
  console.log(`  S${st + 1}: ${n}`)
}
await Bun.write('tmp/m4-misses.json', JSON.stringify(misses))
