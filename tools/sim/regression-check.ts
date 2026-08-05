#!/usr/bin/env bun
/**
 * regression-check.ts — God-AI clear-rate regression harness.
 *
 * Runs headless god-ai simulations in BOTH single-player and co-op modes and
 * reports the stage-clear rate + outcome breakdown. Used to verify that the
 * §79 co-op fix (God AI must drive `controlledTank`, not `w.player`) did not
 * regress the clear rate — either in single-player (parity) or in co-op
 * (where the bug lived).
 *
 * Usage:
 *   bun tools/sim/regression-check.ts --stages 1-26 --seeds 1-3 --difficulty classic
 *   bun tools/sim/regression-check.ts --stages 1-16 --seeds 1-5 --difficulty classic --output /tmp/r.json
 */
import { STAGES } from '../../src/config/stages'
import { runSimulation } from './simulation-runner'
import type { StageData } from '../../src/types'

function parseRange(spec: string, max: number): number[] {
  if (spec === 'all') return Array.from({ length: max }, (_, i) => i)
  if (spec.includes('-')) {
    const [s, e] = spec.split('-').map(Number)
    return Array.from({ length: e - s + 1 }, (_, i) => s + i)
  }
  return [parseInt(spec, 10)]
}

interface ModeSummary {
  mode: 'single' | 'coop'
  total: number
  stageClear: number
  gameOver: number
  maxTicks: number
  clearRate: number
  baseAliveRate: number
  avgLives: number
  avgKills: number
  avgScore: number
  // co-op only
  avgLives2?: number
  player2AliveRate?: number
}

function runMode(
  mode: 'single' | 'coop',
  stages: StageData[],
  seeds: number[],
  difficulty: string,
): ModeSummary {
  const coop = mode === 'coop'
  let total = 0
  let stageClear = 0
  let gameOver = 0
  let maxTicks = 0
  let baseAlive = 0
  let livesSum = 0
  let killsSum = 0
  let scoreSum = 0
  let lives2Sum = 0
  let p2Alive = 0

  const grand = stages.length * seeds.length
  let done = 0
  for (const stage of stages) {
    for (const seed of seeds) {
      const r = runSimulation({
        seed,
        stage,
        difficulty,
        coop,
        maxTicks: 36000,
        sampleInterval: 9999,
      })
      total++
      if (r.outcome === 'stage_clear') stageClear++
      else if (r.outcome === 'gameover') gameOver++
      else maxTicks++
      if (r.finalState.baseAlive) baseAlive++
      livesSum += r.finalState.lives
      killsSum += r.finalState.killCount
      scoreSum += r.finalState.score
      if (coop) {
        lives2Sum += r.finalState.lives2 ?? 0
        if (r.finalState.player2Alive) p2Alive++
      }
      done++
      if (done % 20 === 0 || done === grand) {
        process.stderr.write(`\r  [regression-check:${mode}] ${done}/${grand}...`)
      }
    }
  }
  process.stderr.write('\n')

  const s: ModeSummary = {
    mode,
    total,
    stageClear,
    gameOver,
    maxTicks,
    clearRate: total ? stageClear / total : 0,
    baseAliveRate: total ? baseAlive / total : 0,
    avgLives: total ? livesSum / total : 0,
    avgKills: total ? killsSum / total : 0,
    avgScore: total ? scoreSum / total : 0,
  }
  if (coop) {
    s.avgLives2 = total ? lives2Sum / total : 0
    s.player2AliveRate = total ? p2Alive / total : 0
  }
  return s
}

if (import.meta.main) {
  function arg(name: string, fallback: string): string {
    const i = process.argv.indexOf(`--${name}`)
    return i >= 0 ? process.argv[i + 1] : fallback
  }
  const difficulty = arg('difficulty', 'classic')
  const seeds = parseRange(arg('seeds', '1-3'), 1_000_000)
  const stageIdxs = parseRange(arg('stages', '1-26'), STAGES.length)
    .map((n) => n - 1) // CLI is 1-based (1..35); internal index is 0-based
    .filter((i) => i >= 0 && i < STAGES.length)
  const stages = stageIdxs.map((i) => STAGES[i]).filter(Boolean)
  const outputFile = arg('output', '')

  process.stderr.write(
    `[regression-check] ${stages.length} stages × ${seeds.length} seeds = ${stages.length * seeds.length} runs/mode, difficulty=${difficulty}\n`,
  )

  const single = runMode('single', stages, seeds, difficulty)
  const coop = runMode('coop', stages, seeds, difficulty)

  const out = { config: { difficulty, seeds, stageCount: stages.length }, single, coop }
  const text = JSON.stringify(out, null, 2)
  if (outputFile) {
    await Bun.write(outputFile, text)
    process.stderr.write(`[regression-check] wrote ${outputFile}\n`)
  }
  console.log(text)
}
