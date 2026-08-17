#!/usr/bin/env bun
/**
 * loo-allon.ts — leave-one-out screening over ALL_ON_EXPERIMENT_PARAMS
 * (GOD-AI-all-strategies-CMA-ES.md §4 M1: all-on 灾难性负向 → 先 LOO 找冲突策略).
 *
 * For every manifest switch, runs all-on minus that switch across the
 * (stage × seed) grid at the official caliber (stageIndex=0, maxTicks=36000,
 * telemetry on) and reports wins / base_destroyed / lives_exhausted vs the
 * all-on reference — a switch whose removal RECOVERS wins is a conflict.
 *
 * Screening caliber: --seeds 1-10 by default (21 × 350 ≈ 7.4k runs, ~3 min);
 * pass --seeds 1-60 to confirm a suspect at the full corpus.
 *
 * Usage:
 *   bun tools/diag/loo-allon.ts [--seeds 1-10] [--difficulty hard] [--json tmp/loo.json]
 */
import { STAGES } from '../../src/config/stages'
import { ALL_ON_EXPERIMENT_PARAMS } from '../../src/ai/god/all-on-experiment'
import { SimWorkerPool } from '../sim/sim-pool'
import type { SimTask, SimTaskResult } from '../sim/sim-worker'
import { parseStageSpec, StageSpecError, runHeader, paramsHash } from '../lib/stage-spec'
import type { GodAIParams } from '../../src/ai/GodAIInput'

function arg(name: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : undefined
}

function parseSeeds(spec: string | undefined): number[] {
  if (!spec) return Array.from({ length: 10 }, (_, i) => i + 1)
  const s = spec.trim()
  if (/^\d+-\d+$/.test(s)) {
    const [lo, hi] = s.split('-').map(Number)
    return Array.from({ length: hi - lo + 1 }, (_, i) => lo + i)
  }
  return s.split(',').map(Number)
}

const difficulty = arg('difficulty') ?? 'hard'
const seeds = parseSeeds(arg('seeds'))
const jsonOut = arg('json')
const stageSpec = arg('stages') ?? 'all'
let stageIdxs: number[]
try {
  stageIdxs = parseStageSpec(stageSpec, STAGES.length)
} catch (e) {
  console.error(e instanceof StageSpecError ? e.message : `invalid --stages: ${stageSpec}`)
  process.exit(1)
}

/** Manifest switches (GOD-AI-all-strategies-CMA-ES.md §2) — one LOO each. */
const MANIFEST_SWITCHES = [
  'baseLaneSentryInBandNav',
  'baseAlertPickupSuppress',
  'baseGuardAnchorMode',
  'actionContractMode',
  'targetValueMode',
  'intentMode',
  'coverageMode',
  'candidateMode',
  'firingLaneMode',
  'defenseInterceptPredictCells',
  'defenseInterceptDigBricks',
  'dodgeCentroidMode',
  'pathThreatAvoidance',
  'dodgeCounterFire',
  'dodgeClearanceScore',
  'dodgeHorizonScore',
  'playerHpAwareness',
  'survivalModeLives',
  'suicideReturnMode',
  'baseDamageRecall',
  'pathTargetMode',
] as const

// --only a,b,c restricts the LOO to those switches (60-seed confirmation of
// screening suspects without re-running the full 21 × corpus).
const onlyRaw = arg('only')
const only = onlyRaw ? new Set(onlyRaw.split(',')) : null
const switches = only ? MANIFEST_SWITCHES.filter((sw) => only.has(sw)) : [...MANIFEST_SWITCHES]

const CANDIDATES: Array<{ name: string; params: GodAIParams }> = [
  { name: 'all-on (reference)', params: ALL_ON_EXPERIMENT_PARAMS },
  ...switches.map((sw) => ({
    name: `off:${sw}`,
    params: { ...ALL_ON_EXPERIMENT_PARAMS, [sw]: 0 } as GodAIParams,
  })),
]

const pool = new SimWorkerPool()
const started = Date.now()
const tasks: SimTask[] = []
const labels: string[] = []
for (let c = 0; c < CANDIDATES.length; c++) {
  for (const si of stageIdxs) {
    for (const seed of seeds) {
      tasks.push({
        id: tasks.length,
        seed,
        stage: STAGES[si],
        stageIndex: 0,
        difficulty,
        params: CANDIDATES[c].params,
        maxTicks: 36000,
        telemetry: true,
      })
      labels.push(`${c}|${si}|${seed}`)
    }
  }
}

console.error(
  runHeader({
    difficulty,
    stageCount: stageIdxs.length,
    seedCount: seeds.length,
    stageIndex: 0,
    maxTicks: 36000,
    params: ALL_ON_EXPERIMENT_PARAMS,
  }) + ` loo-candidates=${CANDIDATES.length}`,
)
const results = await pool.runBatch(tasks)
pool.terminate()
console.error(`elapsed ${((Date.now() - started) / 1000).toFixed(1)}s for ${tasks.length} runs`)

const byKey = new Map<string, SimTaskResult>()
results.forEach((r) => byKey.set(labels[r.id], r))
const cause = (r: SimTaskResult) =>
  r.outcome === 'stage_clear'
    ? null
    : r.outcome === 'max_ticks'
      ? 'timeout'
      : r.baseAlive
        ? 'lives_exhausted'
        : 'base_destroyed'

const win = (r?: SimTaskResult) => r?.outcome === 'stage_clear'
const rows = CANDIDATES.map((cand, c) => {
  let w = 0
  let bd = 0
  let le = 0
  let to = 0
  let kills = 0
  let ticks = 0
  for (const si of stageIdxs) {
    for (const seed of seeds) {
      const r = byKey.get(`${c}|${si}|${seed}`)!
      if (win(r)) w++
      else if (cause(r) === 'base_destroyed') bd++
      else if (cause(r) === 'lives_exhausted') le++
      else to++
      kills += r.killCount
      ticks += r.ticks
    }
  }
  return {
    name: cand.name,
    w,
    bd,
    le,
    to,
    kills,
    ticks,
    total: stageIdxs.length * seeds.length,
    hash: paramsHash(cand.params),
  }
})

const ref = rows[0]
console.log(`=== LOO screening  ${difficulty} ${stageIdxs.length} stages × ${seeds.length} seeds`)
console.log(
  'candidate                        W/n      Δwins  base_destroyed  lives_exhausted  timeout  avgKills',
)
for (const r of rows) {
  const dw = r === ref ? '' : String(r.w - ref.w)
  console.log(
    `${r.name.padEnd(32)} ${String(r.w).padStart(4)}/${r.total}  ${dw.padStart(5)}  ${String(r.bd).padStart(4)}           ${String(r.le).padStart(4)}           ${String(r.to).padStart(3)}   ${(r.kills / r.total).toFixed(1)}`,
  )
}

if (jsonOut) {
  await Bun.write(
    jsonOut,
    JSON.stringify({ difficulty, stageIndex: 0, maxTicks: 36000, seeds, rows }, null, 1),
  )
  console.error(`wrote ${jsonOut}`)
}
