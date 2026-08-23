#!/usr/bin/env bun
/**
 * diag-weak-stages.ts — Failure-family profiler for below-floor stages.
 *
 * Runs the given stages × seeds serially with params from --params (or
 * defaults) and tallies, per stage: failure causes (base_destroyed vs
 * lives_exhausted), kills at failure, failure tick, and enemy types alive.
 *
 * Usage:
 *   bun tools/diag/diag-weak-stages.ts --stages 7,15,19,33 --seeds 20 --params <summary.json>
 */
import { STAGES } from '../../src/config/stages'
import { GodAIParams, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { runSimulation } from '../sim/simulation-runner'
import { readFileSync } from 'fs'

import { arg } from '../lib/cli'

const stageIdxs = arg('stages', '7,15,19,33')!
  .split(',')
  .map((s) => parseInt(s, 10) - 1) // CLI is 1-based (1..35); internal index is 0-based
const seedCount = parseInt(arg('seeds', '20')!, 10)
const paramsFile = arg('params', '')
const maxTicks = 18000

let params: GodAIParams = DEFAULT_GOD_AI_PARAMS
if (paramsFile) {
  const raw = JSON.parse(readFileSync(paramsFile, 'utf8'))
  params = { ...DEFAULT_GOD_AI_PARAMS, ...(raw.bestParams ?? raw) }
  console.log(`params: ${paramsFile}`)
}

for (const si of stageIdxs) {
  const stage = STAGES[si]
  let wins = 0
  const failures: Array<{
    seed: number
    cause: string
    kills: number
    tick: number
    lives: number
  }> = []

  for (let seed = 1; seed <= seedCount; seed++) {
    const r = runSimulation({
      seed,
      stage,
      difficulty: 'classic',
      godAIParams: params,
      maxTicks,
    })
    if (r.outcome === 'stage_clear') {
      wins++
    } else {
      failures.push({
        seed,
        cause: r.outcome === 'gameover' ? (r.failure?.cause ?? 'unknown') : 'timeout',
        kills: r.finalState.killCount,
        tick: r.ticks,
        lives: r.finalState.lives,
      })
    }
  }

  const baseLost = failures.filter((f) => f.cause === 'base_destroyed')
  const livesOut = failures.filter((f) => f.cause === 'lives_exhausted')
  const timeouts = failures.filter((f) => f.cause === 'timeout')
  console.log(
    `\nS${si + 1} ${stage.name}: win ${wins}/${seedCount}` +
      ` | base_destroyed=${baseLost.length} lives_exhausted=${livesOut.length} timeout=${timeouts.length}`,
  )
  for (const f of failures) {
    console.log(
      `  seed ${String(f.seed).padStart(2)}: ${f.cause.padEnd(16)} kills=${String(f.kills).padStart(2)} tick=${String(f.tick).padStart(5)} lives=${f.lives}`,
    )
  }
}
