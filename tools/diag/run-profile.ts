#!/usr/bin/env bun
/**
 * run-profile.ts — M0 corpus runner for GOD-AI-all-strategies-CMA-ES.md.
 *
 * Runs one God-AI params profile across the (stage × seed) grid at the
 * official caliber (stageIndex=0, maxTicks=36000, telemetry on) and saves a
 * per-run artifact: outcome / ticks / kills / base state / lives / failure
 * cause / paramsHash. Live-probes that the profile actually reached the
 * Simulation (paramsHash identity), and aborts on mismatch.
 *
 * Usage:
 *   bun tools/diag/run-profile.ts --profile all-on            # hard 35×60 (defaults)
 *   bun tools/diag/run-profile.ts --profile default --difficulty classic --seeds 1-10
 *   bun tools/diag/run-profile.ts --profile tmp/cand.json --stages 8 --json tmp/art.json
 *
 * Named profiles: default | all-on | all-on-m5-off (src/ai/god/all-on-experiment.ts).
 * --profile <path.json> loads a bare GodAIParams object (or {bestParams}/{params}).
 */
import { STAGES } from '../../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import {
  ALL_ON_EXPERIMENT_PARAMS,
  ALL_ON_M5_OFF_CONTROL_PARAMS,
  ALL_ON_MINUS_FLM_PARAMS,
} from '../../src/ai/god/all-on-experiment'
import { SimWorkerPool } from '../sim/sim-pool'
import type { SimTask, SimTaskResult } from '../sim/sim-worker'
import { parseStageSpec, StageSpecError, runHeader, paramsHash } from '../lib/stage-spec'
import { readFileSync } from 'fs'
import type { GodAIParams } from '../../src/ai/GodAIInput'

function arg(name: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : undefined
}

function parseSeeds(spec: string | undefined): number[] {
  if (!spec) return Array.from({ length: 60 }, (_, i) => i + 1)
  const s = spec.trim()
  if (/^\d+-\d+$/.test(s)) {
    const [lo, hi] = s.split('-').map(Number)
    return Array.from({ length: hi - lo + 1 }, (_, i) => lo + i)
  }
  return s.split(',').map(Number)
}

const difficulty = arg('difficulty') ?? 'hard'
const seeds = parseSeeds(arg('seeds'))
const maxTicks = parseInt(arg('max-ticks') ?? '36000', 10)
const jsonOut = arg('json')

const stageSpec = arg('stages') ?? 'all'
let stageIdxs: number[]
try {
  stageIdxs = parseStageSpec(stageSpec, STAGES.length)
} catch (e) {
  console.error(e instanceof StageSpecError ? e.message : `invalid --stages: ${stageSpec}`)
  process.exit(1)
}

const profileArg = arg('profile')
if (!profileArg) {
  console.error(
    'usage: run-profile.ts --profile <default|all-on|all-on-m5-off|path.json> [--difficulty hard] [--stages all] [--seeds 1-60] [--max-ticks 36000] [--json out.json]',
  )
  process.exit(1)
}
let profileName: string
let params: GodAIParams
if (
  profileArg === 'default' ||
  profileArg === 'all-on' ||
  profileArg === 'all-on-m5-off' ||
  profileArg === 'all-on-minus-flm'
) {
  profileName = profileArg
  params =
    profileArg === 'all-on'
      ? ALL_ON_EXPERIMENT_PARAMS
      : profileArg === 'all-on-m5-off'
        ? ALL_ON_M5_OFF_CONTROL_PARAMS
        : profileArg === 'all-on-minus-flm'
          ? ALL_ON_MINUS_FLM_PARAMS
          : DEFAULT_GOD_AI_PARAMS
} else {
  profileName = profileArg.replace(/\\/g, '/').split('/').pop() ?? profileArg
  const raw = JSON.parse(readFileSync(profileArg, 'utf8'))
  params = { ...DEFAULT_GOD_AI_PARAMS, ...(raw.bestParams ?? raw.params ?? raw) }
}
const wantHash = paramsHash(params)

const pool = new SimWorkerPool()
const started = Date.now()

// Live probe (§3): one run at the official caliber must record the profile's
// params hash — proves the profile reached the Simulation, not the default.
const probeResults = await pool.runBatch([
  {
    id: 0,
    seed: seeds[0],
    stage: STAGES[stageIdxs[0]],
    stageIndex: 0,
    difficulty,
    params,
    maxTicks,
    telemetry: true,
  },
])
const pr = probeResults[0]
if (!pr.ok || pr.paramsHash !== wantHash) {
  console.error(
    `live probe FAILED: profile=${profileName} expected ${wantHash}, got ${pr.paramsHash ?? 'none'} — params did not reach Simulation`,
  )
  process.exit(1)
}
console.error(`live probe: profile=${profileName} hash=${wantHash} OK (params reached Simulation)`)

console.error(
  runHeader({
    difficulty,
    stageCount: stageIdxs.length,
    seedCount: seeds.length,
    stageIndex: 0,
    maxTicks,
    params,
  }) + ` profile=${profileName}`,
)

const tasks: SimTask[] = []
for (const si of stageIdxs) {
  for (const seed of seeds) {
    tasks.push({
      id: tasks.length,
      seed,
      stage: STAGES[si],
      stageIndex: 0,
      difficulty,
      params,
      maxTicks,
      telemetry: true,
    })
  }
}
const results = await pool.runBatch(tasks)
pool.terminate()
console.error(`elapsed ${((Date.now() - started) / 1000).toFixed(1)}s for ${tasks.length} runs`)

function failureCause(r: SimTaskResult): string | null {
  if (r.outcome === 'stage_clear') return null
  if (r.outcome === 'max_ticks') return 'timeout'
  // 'gameover': base destroyed iff the base is gone; otherwise lives exhausted.
  return r.baseAlive ? 'lives_exhausted' : 'base_destroyed'
}

if (jsonOut) {
  const out: Record<string, unknown> = {
    profile: profileName,
    paramsHash: wantHash,
    difficulty,
    stageIndex: 0,
    maxTicks,
    seeds,
    runs: results.map((r, i) => {
      const si = stageIdxs[Math.floor(i / seeds.length)]
      return {
        stage: si,
        stageName: STAGES[si].name,
        seed: seeds[i % seeds.length],
        outcome: r.outcome,
        ticks: r.ticks,
        kills: r.killCount,
        baseAlive: r.baseAlive,
        lives: r.lives,
        failureCause: failureCause(r),
      }
    }),
  }
  await Bun.write(jsonOut, JSON.stringify(out, null, 1))
  console.error(`wrote ${jsonOut}`)
}

const win = (r: SimTaskResult) => r.outcome === 'stage_clear'
const cause = (r: SimTaskResult) => failureCause(r) ?? ''
const suite = { w: 0, bd: 0, le: 0, to: 0, ticks: 0, kills: 0 }
console.log(
  `=== ${difficulty} ${stageIdxs.length} stages × ${seeds.length} seeds  profile ${profileName}`,
)
console.log('stage  W/n   base_destroyed  lives_exhausted  timeout  avgTicks  avgKills')
for (let i = 0; i < stageIdxs.length; i++) {
  const si = stageIdxs[i]
  let w = 0
  const c = { bd: 0, le: 0, to: 0, ticks: 0, kills: 0 }
  for (let s = 0; s < seeds.length; s++) {
    const r = results[i * seeds.length + s]
    if (win(r)) w++
    else if (cause(r) === 'base_destroyed') c.bd++
    else if (cause(r) === 'lives_exhausted') c.le++
    else c.to++
    c.ticks += r.ticks
    c.kills += r.killCount
  }
  const n = seeds.length
  console.log(
    `s${String(si + 1).padStart(2)} ${String(w).padStart(2)}/${n}  ${String(c.bd).padStart(4)}          ${String(c.le).padStart(4)}          ${String(c.to).padStart(3)}   ${(c.ticks / n).toFixed(0).padStart(6)}  ${(c.kills / n).toFixed(1)}`,
  )
  suite.w += w
  suite.bd += c.bd
  suite.le += c.le
  suite.to += c.to
  suite.ticks += c.ticks
  suite.kills += c.kills
}
const total = stageIdxs.length * seeds.length
console.log(
  `TOTAL W=${suite.w}/${total} (${((suite.w / total) * 100).toFixed(1)}%)  base_destroyed=${suite.bd}  lives_exhausted=${suite.le}  timeout=${suite.to}  avgTicks=${(suite.ticks / total).toFixed(0)}  avgKills=${(suite.kills / total).toFixed(1)}`,
)
