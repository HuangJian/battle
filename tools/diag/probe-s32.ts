#!/usr/bin/env bun
/**
 * probe-s32.ts — Quick A/B probe for S32 parameter changes.
 * Compares the current override (baseline) vs a variant with modified params.
 *
 * Usage:
 *   bun tools/diag/probe-s32.ts --seeds 120
 *   bun tools/diag/probe-s32.ts --seeds 120 --variants "t8MaxInterceptDistCells=16"
 */
import { STAGES } from '../../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../../src/ai/GodAIInput'
import { runSimulation } from '../sim/simulation-runner'

import { arg } from '../lib/cli'

const seedCount = parseInt(arg('seeds', '120')!, 10)
const variantsSpec = arg('variants', '')!

const stage = STAGES[32]
const baseParams = DEFAULT_GOD_AI_PARAMS

const variants: Array<{ label: string; params: GodAIParams }> = [
  { label: 'BASELINE', params: baseParams },
]
for (const spec of variantsSpec.split(';')) {
  if (!spec.trim()) continue
  const overrides: Record<string, number> = {}
  for (const pair of spec.split(',')) {
    const [k, v] = pair.split('=')
    overrides[k.trim()] = parseFloat(v)
  }
  variants.push({ label: spec.trim(), params: { ...baseParams, ...overrides } })
}

console.log(`S32 Diamond × ${seedCount} seeds, classic, 18000t\n`)
for (const v of variants) {
  let wins = 0,
    baseDeaths = 0,
    livesOut = 0
  for (let seed = 1; seed <= seedCount; seed++) {
    const r = runSimulation({
      seed,
      stage,
      difficulty: 'classic',
      godAIParams: v.params,
      maxTicks: 18000,
    })
    if (r.outcome === 'stage_clear') wins++
    else if (r.failure?.cause === 'base_destroyed') baseDeaths++
    else livesOut++
  }
  const pct = ((wins / seedCount) * 100).toFixed(1).padStart(5)
  console.log(`${pct}%  (base=${baseDeaths} lives=${livesOut})  ${v.label}`)
}
