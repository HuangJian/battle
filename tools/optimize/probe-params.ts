#!/usr/bin/env bun
/**
 * probe-params.ts — Cheap single-stage param sensitivity probe.
 *
 * Runs one stage × N seeds for a list of param variants (each a set of
 * overrides on top of a base params file) and prints the win rate per
 * variant. Used to find which knob actually moves a resistant stage
 * before spending a full CMA-ES round on it.
 *
 * Usage:
 *   bun tools/optimize/probe-params.ts --stage 19 --seeds 20 \
 *     --base .workbuddy/optimization-p4-r6/optimization-summary.json \
 *     --variants "aimError=0;outnumberedEnemyCount=2;maxPlayerDistFromBase=12,defenseRowOffset=3"
 *
 * Variant syntax: ';' separates variants, ',' separates key=val pairs
 * within one variant. An empty string probes the unmodified base.
 */
import { STAGES } from '../../src/config/stages'
import { GodAIParams, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { runSimulation } from '../sim/simulation-runner'
import { readFileSync } from 'fs'

import { arg } from '../lib/cli'

const stageIdx = parseInt(arg('stage', '19')!, 10) - 1 // CLI is 1-based (1..35)
const seedCount = parseInt(arg('seeds', '20')!, 10)
const seedStart = parseInt(arg('seedStart', '1')!, 10)
const baseFile = arg('base', '')
const variantsSpec = arg('variants', '')!
const maxTicks = 18000

let base: GodAIParams = DEFAULT_GOD_AI_PARAMS
if (baseFile) {
  const raw = JSON.parse(readFileSync(baseFile, 'utf8'))
  base = { ...DEFAULT_GOD_AI_PARAMS, ...(raw.bestParams ?? raw) }
}

const variants: Array<{ label: string; params: GodAIParams }> = [{ label: 'BASE', params: base }]
for (const spec of variantsSpec.split(';')) {
  if (!spec.trim()) continue
  const overrides: Record<string, number> = {}
  for (const pair of spec.split(',')) {
    const [k, v] = pair.split('=')
    overrides[k.trim()] = parseFloat(v)
  }
  variants.push({ label: spec.trim(), params: { ...base, ...overrides } })
}

const stage = STAGES[stageIdx]
console.log(`S${stageIdx + 1} ${stage.name} x ${seedCount} seeds, classic, ${maxTicks}t`)
for (const v of variants) {
  let wins = 0
  let baseDeaths = 0
  let livesOut = 0
  for (let seed = seedStart; seed < seedStart + seedCount; seed++) {
    const r = runSimulation({
      seed,
      stage,
      difficulty: 'classic',
      godAIParams: v.params,
      maxTicks,
    })
    if (r.outcome === 'stage_clear') wins++
    else if ((r as { failure?: { cause?: string } }).failure?.cause === 'base_destroyed')
      baseDeaths++
    else livesOut++
  }
  const pct = ((wins / seedCount) * 100).toFixed(0).padStart(3)
  console.log(`${pct}%  (base_destroyed=${baseDeaths} lives=${livesOut})  ${v.label}`)
}
