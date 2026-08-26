#!/usr/bin/env bun
/**
 * flip-scan.ts — Reusable A/B flip-seed scanner for God AI strategy tuning.
 *
 * The §88 chokepoint-holding campaign spent most of its debugging time on a
 * hand-rolled bash loop: `for s in $(seq 1 60); do dump A; dump B; diff; done`
 * — running 2×N FULL simulations per stage through per-seed-diff just to find
 * WHICH seeds flip. This tool is that loop, fixed:
 *
 *   - Runs both arms (A = baseline params, B = baseline + --set overrides)
 *     through the parallel SimWorkerPool (byte-identical to serial, AGENTS
 *     §2.3 determinism).
 *   - Classifies every (stage, seed) pair:
 *       FLIP-TO-WIN   A lose → B win   (the candidate's gain)
 *       FLIP-TO-LOSE  A win  → B lose  (the candidate's regression)
 *       TIED          same outcome both arms (the 95% that never diverge)
 *   - Reports per-stage flip counts + the exact seed list, so the next step
 *     (per-seed-diff dump/diff + decision-probe) targets only the seeds that
 *     actually matter.
 *
 * The three-step loop this encodes (progress doc §0.B):
 *   1. bun tools/diag/flip-scan.ts --stages 19,33 --seeds 1-60 --set chokepointMode=1
 *   2. for each FLIP-TO-LOSE seed: per-seed-diff dump/diff → root cause
 *   3. fix, re-run flip-scan → confirm flips resolved / only gains remain
 *
 * Usage:
 *   bun tools/diag/flip-scan.ts --stages 7,17,33 --seeds 1-60
 *                                [--set k=v ...]          candidate arm overrides
 *                                [--params-a a.json]      A arm from a params file
 *                                [--params-b b.json]      B arm from a params file
 *                                [--seeds 1-60]           default 1-60
 *                                [--workers N]            default = physical cores - 1
 *
 * Exit code 0 always (reports are informational); nonzero on usage errors.
 */
import { readFileSync } from 'node:fs'
import { STAGES } from '../../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../../src/ai/GodAIInput'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { SimWorkerPool } from '../sim/sim-pool'
import type { SimTask } from '../sim/sim-worker'
import { arg, parseSeeds, parseStages } from '../lib/cli'

const USAGE = `
flip-scan.ts — A/B flip-seed scanner (parallel worker pool).

Usage:
  bun tools/diag/flip-scan.ts [--stages 7,17,33] [--seeds 1-60] [--set k=v ...]
                              [--params-a a.json] [--params-b b.json] [--workers N]
                              [--difficulty classic|hard|chaos]

Arms:
  A = DEFAULT_GOD_AI_PARAMS        (or --params-a a.json, {params|bestParams} or flat)
  B = A + --set overrides          (or --params-b b.json)
  --set is repeatable, any numeric GodAIParams key (same contract as
  per-seed-diff --set, progress doc §0.C rule 2).

Output per stage: FLIP-TO-WIN / FLIP-TO-LOSE / TIED counts + seed lists,
then a one-line suite summary. Pair with per-seed-diff + decision-probe.
`

// ---------------------------------------------------------------- CLI parse

interface Cli {
  stageIdxs: number[]
  seeds: number[]
  setOverrides: Record<string, number>
  paramsA: GodAIParams
  paramsB: GodAIParams
  workers: number
  difficulty: string
}

function loadParamsFile(path: string): GodAIParams {
  const raw = JSON.parse(readFileSync(path, 'utf8'))
  const obj = raw.bestParams ?? raw.params ?? raw
  return { ...DEFAULT_GOD_AI_PARAMS, ...obj } as GodAIParams
}

function parseCli(): Cli {
  const stageIdxs = parseStages(arg('stages'))

  const seeds = parseSeeds(arg('seeds'), 60)

  // --set overrides (repeatable).
  const setOverrides: Record<string, number> = {}
  for (let i = 0; i < process.argv.length; i++) {
    if (process.argv[i] !== '--set') continue
    const kv = process.argv[i + 1]
    if (!kv || !kv.includes('='))
      throw new Error('--set expects key=value (e.g. --set counterFire=0)')
    const eq = kv.indexOf('=')
    const key = kv.slice(0, eq)
    const val = Number(kv.slice(eq + 1))
    if (isNaN(val) || !(key in DEFAULT_GOD_AI_PARAMS)) {
      throw new Error(`--set: unknown or non-numeric param '${key}'`)
    }
    setOverrides[key] = val
  }

  const paramsA = arg('params-a') ? loadParamsFile(arg('params-a')!) : { ...DEFAULT_GOD_AI_PARAMS }
  const paramsB = arg('params-b')
    ? loadParamsFile(arg('params-b')!)
    : { ...paramsA, ...setOverrides }

  const workersArg = arg('workers')
  const workers = workersArg ? Number(workersArg) : undefined

  const difficulty = arg('difficulty') ?? 'classic'
  if (!DIFFICULTIES[difficulty]) {
    throw new Error(`--difficulty: unknown difficulty '${difficulty}'`)
  }

  return { stageIdxs, seeds, setOverrides, paramsA, paramsB, workers: workers ?? -1, difficulty }
}

// ---------------------------------------------------------------- main

async function main(): Promise<void> {
  let cli: Cli
  try {
    cli = parseCli()
  } catch (err) {
    console.error(`flip-scan: ${(err as Error).message}`)
    console.error(USAGE)
    process.exit(1)
  }

  const pool = new SimWorkerPool(cli.workers > 0 ? cli.workers : undefined)
  const tasks: SimTask[] = []
  const meta: { stageIdx: number; seed: number; arm: 'A' | 'B' }[] = []

  for (const stageIdx of cli.stageIdxs) {
    for (const seed of cli.seeds) {
      for (const arm of ['A', 'B'] as const) {
        tasks.push({
          id: tasks.length,
          seed,
          stage: STAGES[stageIdx],
          difficulty: cli.difficulty,
          params: arm === 'A' ? cli.paramsA : cli.paramsB,
          maxTicks: 18000,
        })
        meta.push({ stageIdx, seed, arm })
      }
    }
  }

  process.stderr.write(
    `flip-scan: ${cli.stageIdxs.length} stages × ${cli.seeds.length} seeds × 2 arms = ${tasks.length} sims (${pool.size} workers)\n`,
  )
  const t0 = Date.now()
  const results = await pool.runBatch(tasks)
  pool.terminate()
  process.stderr.write(
    `flip-scan: ran ${results.length} sims in ${((Date.now() - t0) / 1000).toFixed(1)}s\n`,
  )

  // Group by stage.
  const byStage = new Map<
    number,
    { winA: number; winB: number; toWin: number[]; toLose: number[]; tied: number }
  >()
  for (const stageIdx of cli.stageIdxs)
    byStage.set(stageIdx, { winA: 0, winB: 0, toWin: [], toLose: [], tied: 0 })

  for (let i = 0; i < results.length; i++) {
    const r = results[i]
    const m = meta[i]
    const agg = byStage.get(m.stageIdx)!
    if (!r.ok) {
      console.error(`flip-scan: sim ${m.stageIdx}/${m.seed}/${m.arm} failed — aborting report`)
      process.exit(2)
    }
    const cleared = r.outcome === 'stage_clear'
    if (m.arm === 'A') agg.winA += cleared ? 1 : 0
    else agg.winB += cleared ? 1 : 0
  }

  // Second pass: classify flips (needs both arms). Tasks were pushed as
  // A then B per (stage, seed), and runBatch re-orders by task id, so
  // results[i] === A, results[i+1] === B. Assert the invariant so a future
  // edit to the task loop can't silently misclassify.
  for (let i = 0; i < results.length; i += 2) {
    if (meta[i].arm !== 'A' || meta[i + 1].arm !== 'B') {
      console.error('flip-scan: internal task-ordering invariant violated (expected A,B pairs)')
      process.exit(2)
    }
    const a = results[i]
    const b = results[i + 1]
    const m = meta[i]
    const agg = byStage.get(m.stageIdx)!
    const aClear = a.outcome === 'stage_clear'
    const bClear = b.outcome === 'stage_clear'
    if (aClear === bClear) {
      agg.tied++
    } else if (bClear && !aClear) {
      agg.toWin.push(m.seed)
    } else {
      agg.toLose.push(m.seed)
    }
  }

  // Report.
  let totalToWin = 0
  let totalToLose = 0
  for (const stageIdx of cli.stageIdxs) {
    const agg = byStage.get(stageIdx)!
    totalToWin += agg.toWin.length
    totalToLose += agg.toLose.length
    const name = STAGES[stageIdx].name
    console.log(
      `S${stageIdx + 1} ${name.padEnd(16)} A ${agg.winA}/${cli.seeds.length} → B ${agg.winB}/${cli.seeds.length}  ` +
        `win:${agg.toWin.length} lose:${agg.toLose.length} tied:${agg.tied}`,
    )
    if (agg.toWin.length > 0) console.log(`    FLIP-TO-WIN  seeds: ${agg.toWin.join(', ')}`)
    if (agg.toLose.length > 0) console.log(`    FLIP-TO-LOSE seeds: ${agg.toLose.join(', ')}`)
  }
  console.log(
    `\nSUITE: net ${totalToWin - totalToLose >= 0 ? '+' : ''}${totalToWin - totalToLose} flips ` +
      `(to-win ${totalToWin}, to-lose ${totalToLose})`,
  )
  console.log(
    'Next: per-seed-diff dump/diff on FLIP-TO-LOSE seeds, then decision-probe at the first divergence tick.',
  )
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
