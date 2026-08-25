#!/usr/bin/env bun
/**
 * validate-p4.ts — Full 35-stage classic validation sweep for the P4 goal.
 *
 * Reads a GodAIParams set (from an optimization-summary.json via --params, or
 * DEFAULT_GOD_AI_PARAMS if omitted) and runs 35 stages × 20 seeds headless
 * simulations on the worker pool (multi-threaded, byte-identical to serial).
 *
 * Reports:
 *   - per-stage win rate / avg kills / gameovers / base-survival
 *   - the MEAN win rate (P4 goal: > 80%)
 *   - every stage below the FLOOR (P4 goal: every stage > 60%)
 *
 * Usage:
 *   bun tools/eval/validate-p4.ts --params .workbuddy/optimization-p4/optimization-summary.json
 *   bun tools/eval/validate-p4.ts --floor 0.6 --mean 0.8
 */
import { STAGES } from '../../../src/config/stages'
import { GodAIParams, DEFAULT_GOD_AI_PARAMS } from '../../../src/ai/GodAIInput'
import { SimWorkerPool } from '../../sim/sim-pool'
import { readFileSync } from 'fs'

import { arg } from '../../lib/cli'

const SEED_COUNT = parseInt(arg('seeds', '20')!, 10)
const SEEDS = Array.from({ length: SEED_COUNT }, (_, i) => i + 1)
const MAX_TICKS = 18000
const FLOOR = parseFloat(arg('floor', '0.6')!)
const MEAN_TARGET = parseFloat(arg('mean', '0.8')!)
const paramsFile = arg('params', '')

let params: GodAIParams = DEFAULT_GOD_AI_PARAMS
if (paramsFile) {
  const raw = JSON.parse(readFileSync(paramsFile, 'utf8'))
  const best = raw.bestParams ?? raw
  params = { ...DEFAULT_GOD_AI_PARAMS, ...best }
  process.stderr.write(`Loaded params from ${paramsFile}\n`)
}
process.stderr.write(`Validating 35 stages x ${SEEDS.length} seeds, classic, ${MAX_TICKS}t\n`)
process.stderr.write(`Goal: floor(winRate) > ${FLOOR}, mean(winRate) > ${MEAN_TARGET}\n`)

async function main(): Promise<void> {
  const tasks = []
  for (let s = 0; s < STAGES.length; s++) {
    for (const seed of SEEDS) {
      tasks.push({
        id: tasks.length,
        seed,
        stage: STAGES[s],
        difficulty: 'classic',
        params,
        maxTicks: MAX_TICKS,
      })
    }
  }

  const pool = new SimWorkerPool()
  const results = await pool.runBatch(tasks)
  pool.terminate()

  const perStage: Array<{
    idx: number
    name: string
    winRate: number
    avgKills: number
    gameovers: number
    timeouts: number
    baseAlive: number
  }> = []
  let totalWins = 0
  for (let s = 0; s < STAGES.length; s++) {
    const group = results.slice(s * SEEDS.length, (s + 1) * SEEDS.length)
    let wins = 0
    let baseAlive = 0
    let kills = 0
    let go = 0
    let timeout = 0
    for (const r of group) {
      if (r.ok && r.outcome === 'stage_clear') wins++
      if (r.ok && r.baseAlive) baseAlive++
      kills += r.ok ? r.killCount : 0
      if (r.ok && r.outcome === 'gameover') go++
      if (r.ok && r.outcome === 'max_ticks') timeout++
    }
    const n = SEEDS.length
    perStage.push({
      idx: s,
      name: STAGES[s].name,
      winRate: wins / n,
      avgKills: kills / n,
      gameovers: go,
      timeouts: timeout,
      baseAlive: baseAlive / n,
    })
    totalWins += wins
  }

  const mean = totalWins / (STAGES.length * SEEDS.length)
  const belowFloor = perStage.filter((s) => s.winRate < FLOOR)
  const belowMean = perStage.filter((s) => s.winRate < MEAN_TARGET)

  // Print table.
  console.log(`\nS#   stage             win%   kills  GO  TO  base%`)
  for (const s of perStage) {
    const flag = s.winRate < FLOOR ? '  <-- FLOOR' : s.winRate < MEAN_TARGET ? '  <-- mean' : ''
    console.log(
      `${String(s.idx + 1).padStart(2)}   ${s.name.padEnd(16)} ${(s.winRate * 100).toFixed(1).padStart(5)}  ${s.avgKills.toFixed(1).padStart(5)}  ${String(s.gameovers).padStart(2)}  ${String(s.timeouts).padStart(2)}  ${(s.baseAlive * 100).toFixed(0).padStart(4)}${flag}`,
    )
  }

  console.log(
    `\nMean win rate: ${(mean * 100).toFixed(1)}%  (target > ${MEAN_TARGET * 100}%)  -> ${mean > MEAN_TARGET ? 'PASS' : 'FAIL'}`,
  )
  console.log(
    `Stages below floor ${FLOOR * 100}%: ${belowFloor.length} / ${STAGES.length}  -> ${belowFloor.length === 0 ? 'PASS' : 'FAIL'}`,
  )
  if (belowFloor.length > 0) {
    console.log(
      `  below floor: ${belowFloor.map((s) => `S${s.idx + 1}(${(s.winRate * 100).toFixed(0)}%)`).join(', ')}`,
    )
  }
  console.log(`Stages below mean ${MEAN_TARGET * 100}%: ${belowMean.length} / ${STAGES.length}`)

  // Emit machine-readable JSON to stdout (for downstream tooling).
  process.stdout.write(
    `\n__JSON__${JSON.stringify({ mean, floor: FLOOR, meanTarget: MEAN_TARGET, belowFloor: belowFloor.map((s) => s.idx), perStage })}__JSON__\n`,
  )
}

main()
