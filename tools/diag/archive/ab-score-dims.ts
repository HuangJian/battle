#!/usr/bin/env bun
/**
 * ab-score-dims.ts — dimension-level forensics for one param override.
 * Usage:
 *   bun tools/diag/ab-score-dims.ts --param baseLaneSentryStation=1 --stage 34 --difficulty chaos --seeds 1-20
 */
import { STAGES } from '../../../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../../../src/ai/GodAIInput'
import { runSimulation } from '../../sim/simulation-runner'
import { scoreRun, V7_SCORE_CONFIG, type RunScore } from '../../eval/godai-score'

function arg(name: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : undefined
}

const paramSpec = arg('param')
if (!paramSpec) throw new Error('--param key=value required')
const [pKey, pVal] = paramSpec.split('=')
const pNum = Number(pVal)
const difficulty = arg('difficulty') ?? 'hard'
const stageIdx = Number(arg('stage')) - 1
const seeds = (arg('seeds') ?? '1-20').split(',').flatMap((s) =>
  s.includes('-')
    ? (() => {
        const [a, b] = s.split('-').map(Number)
        return Array.from({ length: b - a + 1 }, (_, i) => a + i)
      })()
    : [Number(s)],
)
const stage = STAGES[stageIdx]

interface Dims {
  score: number
  wins: number
  lives: number
  clearSpeed: number
  baseIntegrity: number
  baseSafety: number
  loot: number
  growth: number
  accuracy: number
  progress: number
  tempo: number
  openingTempo: number
  mobility: number
}

function run(seed: number, station: number): Dims {
  const r = runSimulation({
    seed,
    stage,
    difficulty,
    maxTicks: 18000,
    telemetry: true,
    godAIParams: { ...DEFAULT_GOD_AI_PARAMS, [pKey]: station === 1 ? pNum : 0 },
  })
  const sc = scoreRun(r, V7_SCORE_CONFIG)
  const dv = (k: keyof RunScore['dims']): number => sc.dims[k]?.value ?? 0
  return {
    score: sc.score,
    wins: r.outcome === 'stage_clear' ? 1 : 0,
    lives: dv('lives'),
    clearSpeed: dv('clearSpeed'),
    baseIntegrity: dv('baseIntegrity'),
    baseSafety: dv('baseSafety'),
    loot: dv('loot'),
    growth: dv('growth'),
    accuracy: dv('accuracy'),
    progress: dv('progress'),
    tempo: dv('tempo'),
    openingTempo: dv('openingTempo'),
    mobility: dv('mobility'),
  }
}

console.log(
  `${difficulty} S${stageIdx + 1} seeds ${seeds[0]}-${seeds[seeds.length - 1]}  param ${pKey} cand=${pNum}`,
)
console.log(
  'seed baseScore candScore baseW candW  dims(base->cand): lives clearSpeed baseIntegrity loot accuracy progress tempo mobility',
)
let sb = 0,
  sc = 0,
  wb = 0,
  wc = 0
const dimsB: Record<string, number> = {}
const dimsC: Record<string, number> = {}
for (const seed of seeds) {
  const b = run(seed, 0)
  const c = run(seed, 1)
  sb += b.score
  sc += c.score
  wb += b.wins
  wc += c.wins
  for (const k of Object.keys(b)) {
    dimsB[k] = (dimsB[k] ?? 0) + b[k as keyof Dims]
    dimsC[k] = (dimsC[k] ?? 0) + c[k as keyof Dims]
  }
  const delta = c.score - b.score
  if (Math.abs(delta) > 0.01)
    console.log(
      `s${String(seed).padStart(2)} ${b.score.toFixed(3)}      ${c.score.toFixed(3)}      ${b.wins}     ${c.wins}   d=${delta >= 0 ? '+' : ''}${delta.toFixed(3)}`,
    )
}
console.log(
  `MEAN base ${(sb / seeds.length).toFixed(3)} cand ${(sc / seeds.length).toFixed(3)}  wins ${wb}/${seeds.length} -> ${wc}/${seeds.length}`,
)
const keys: (keyof Dims)[] = [
  'lives',
  'clearSpeed',
  'baseIntegrity',
  'baseSafety',
  'loot',
  'growth',
  'accuracy',
  'progress',
  'tempo',
  'openingTempo',
  'mobility',
]
for (const k of keys) {
  const b = dimsB[k] / seeds.length,
    c = dimsC[k] / seeds.length
  const d = c - b
  if (Math.abs(d) > 0.005)
    console.log(
      `  dim ${k.padEnd(14)} ${b.toFixed(3)} -> ${c.toFixed(3)}  (${d >= 0 ? '+' : ''}${d.toFixed(3)})`,
    )
}
