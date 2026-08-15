#!/usr/bin/env bun
/**
 * ab-param.ts — generic paired A/B for a single param override.
 * Baseline = DEFAULT_GOD_AI_PARAMS; candidate = baseline + one override.
 *
 * Usage:
 *   bun tools/diag/ab-param.ts --param midLaneStickyTicks=90
 *         [--difficulty hard] [--stages 8] [--seeds 1-60] [--json tmp/ab.json]
 *
 * Output: per-stage W counts + L->W / W->L flips with tick details,
 * and a suite summary. --json persists a full per-run record for later
 * forensics (keyed "base|s|seed" / "cand|s|seed").
 */
import { STAGES } from '../../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { SimWorkerPool } from '../sim/sim-pool'
import type { SimTask } from '../sim/sim-worker'

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
const stageSpec = arg('stages') ?? 'all'
const stageIdxs =
  stageSpec === 'all'
    ? STAGES.map((_, i) => i)
    : stageSpec
        .split(',')
        .map((n) => Number(n) - 1)
        .filter((i) => Number.isInteger(i) && i >= 0 && i < STAGES.length)
const jsonOut = arg('json')

const paramSpec = arg('param')
if (!paramSpec) {
  console.error('usage: ab-param.ts --param key=value [--difficulty hard] [--stages 8|all] [--seeds 1-60] [--json out.json]')
  process.exit(1)
}
const [pKey, pVal] = paramSpec.split('=')
const pNum = Number(pVal)

const BASELINE = { ...DEFAULT_GOD_AI_PARAMS }
const CANDIDATE = { ...DEFAULT_GOD_AI_PARAMS, [pKey]: pNum }

const pool = new SimWorkerPool()
const tasks: SimTask[] = []
const labels: string[] = []
for (const [label, params] of [
  ['base', BASELINE],
  ['cand', CANDIDATE],
] as const) {
  for (const si of stageIdxs) {
    for (const seed of seeds) {
      tasks.push({ id: tasks.length, seed, stage: STAGES[si], stageIndex: si, difficulty, params, maxTicks: 36000 })
      labels.push(`${label}|${si}|${seed}`)
    }
  }
}

const started = Date.now()
const results = await pool.runBatch(tasks)
pool.terminate()
const resByKey = new Map<string, (typeof results)[number]>()
results.forEach((r) => resByKey.set(labels[r.id], r))
console.error(`elapsed ${((Date.now() - started) / 1000).toFixed(1)}s for ${tasks.length} runs`)

if (jsonOut) {
  const out: Record<string, { o: string; t: number; hp: number; lives: number }> = {}
  for (const [k, r] of resByKey) out[k] = { o: r.outcome, t: r.ticks, hp: r.baseAlive ? 120 : 0, lives: r.lives ?? 0 }
  await Bun.write(jsonOut, JSON.stringify(out, null, 1))
  console.error(`wrote ${jsonOut}`)
}

const win = (r?: (typeof results)[number]) => r?.outcome === 'stage_clear'
let totalBase = 0
let totalCand = 0
let lw = 0
let wl = 0
console.log(`=== ${difficulty} ${stageIdxs.length} stages × ${seeds.length} seeds  param ${pKey}=${pNum}`)
console.log('stage baseW candW  L->W W->L  flips')
for (const si of stageIdxs) {
  let b = 0
  let c = 0
  let lwS = 0
  let wlS = 0
  const detail: string[] = []
  for (const seed of seeds) {
    const br = resByKey.get(`base|${si}|${seed}`)
    const cr = resByKey.get(`cand|${si}|${seed}`)
    const bm = win(br)
    const cm = win(cr)
    if (bm) {
      b++
      totalBase++
    }
    if (cm) {
      c++
      totalCand++
    }
    if (!bm && cm) {
      lw++
      lwS++
      detail.push(`s${seed}:L${br?.ticks}->W${cr?.ticks}`)
    }
    if (bm && !cm) {
      wl++
      wlS++
      detail.push(`s${seed}:W${br?.ticks}->L${cr?.ticks}`)
    }
  }
  console.log(`s${String(si + 1).padStart(2)} ${String(b).padStart(4)}/60 ${String(c).padStart(4)}/60  ${String(lwS).padStart(3)}    ${String(wlS).padStart(3)}    ${detail.join(' ')}`)
}
console.log(`TOTAL baseW=${totalBase}/${stageIdxs.length * seeds.length} candW=${totalCand}/${stageIdxs.length * seeds.length}  L->W=${lw} W->L=${wl} net=${totalCand - totalBase}`)
