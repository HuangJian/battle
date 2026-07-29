#!/usr/bin/env bun
/**
 * diagnose-s32.ts — Log failure details for S32 Diamond.
 * Reports player distance to base, killer kind, and tick for each failure.
 */
import { STAGES } from '../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { applyStageOverrides } from '../src/ai/godai-stage-overrides'
import { runSimulation } from './simulation-runner'

const seeds = 120
const stage = STAGES[32]
const params = applyStageOverrides(stage.name, DEFAULT_GOD_AI_PARAMS)

let baseDestroyed = 0
let livesExhausted = 0
const baseFailures: Array<{ seed: number; tick: number; dist: number; killer?: string }> = []
const livesFailures: Array<{ seed: number; tick: number; dist: number }> = []

for (let seed = 1; seed <= seeds; seed++) {
  const r = runSimulation({
    seed,
    stage,
    difficulty: 'classic',
    godAIParams: params,
    maxTicks: 18000,
    skipStageOverrides: true,
  })
  if (r.outcome !== 'stage_clear') {
    if (r.failure?.cause === 'base_destroyed') {
      baseDestroyed++
      baseFailures.push({
        seed,
        tick: r.failure.tick,
        dist: r.failure.playerDistToBase ?? -1,
        killer: r.failure.killerKind,
      })
    } else if (r.failure?.cause === 'lives_exhausted') {
      livesExhausted++
      livesFailures.push({
        seed,
        tick: r.failure.tick,
        dist: r.failure.playerDistToBase ?? -1,
      })
    }
  }
}

console.log(`S32 Diamond × ${seeds} seeds`)
console.log(`Wins: ${seeds - baseDestroyed - livesExhausted}`)
console.log(`Base destroyed: ${baseDestroyed}`)
console.log(`Lives exhausted: ${livesExhausted}`)

console.log(`\n--- Base destroyed failures ---`)
for (const f of baseFailures) {
  console.log(
    `  seed ${f.seed.toString().padStart(3)}: tick ${f.tick.toString().padStart(5)}  dist=${f.dist.toString().padStart(2)}  killer=${f.killer ?? '?'}`,
  )
}

console.log(`\n--- Base destroyed: killer kind distribution ---`)
const killerCounts: Record<string, number> = {}
for (const f of baseFailures) {
  const k = f.killer ?? 'unknown'
  killerCounts[k] = (killerCounts[k] ?? 0) + 1
}
for (const [k, v] of Object.entries(killerCounts)) {
  console.log(`  ${k}: ${v}`)
}

console.log(`\n--- Base destroyed: player dist distribution ---`)
const distBuckets = [0, 0, 0, 0] // 0-5, 6-10, 11-15, 16+
for (const f of baseFailures) {
  if (f.dist <= 5) distBuckets[0]++
  else if (f.dist <= 10) distBuckets[1]++
  else if (f.dist <= 15) distBuckets[2]++
  else distBuckets[3]++
}
console.log(`  0-5 cells:  ${distBuckets[0]}`)
console.log(`  6-10 cells: ${distBuckets[1]}`)
console.log(`  11-15 cells: ${distBuckets[2]}`)
console.log(`  16+ cells:   ${distBuckets[3]}`)

console.log(`\n--- Base destroyed: tick distribution ---`)
const tickBuckets = [0, 0, 0, 0] // 0-3000, 3000-6000, 6000-9000, 9000+
for (const f of baseFailures) {
  if (f.tick < 3000) tickBuckets[0]++
  else if (f.tick < 6000) tickBuckets[1]++
  else if (f.tick < 9000) tickBuckets[2]++
  else tickBuckets[3]++
}
console.log(`  0-3000 ticks:   ${tickBuckets[0]}`)
console.log(`  3000-6000:      ${tickBuckets[1]}`)
console.log(`  6000-9000:      ${tickBuckets[2]}`)
console.log(`  9000+:          ${tickBuckets[3]}`)
