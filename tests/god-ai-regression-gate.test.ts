import { describe, it, expect } from 'bun:test'
import { runSimulation } from '../tools/simulation-runner'
import { STAGES } from '../src/config/stages'

// ============================================================
// God AI Wide-Seed Regression Gate (plan/God-AI-Curriculum §6)
//
// The split-parity test above locks exact per-seed output (8 seeds).
// This gate is the REAL tuning regression check: it runs a WIDE, fixed
// seed set and asserts aggregate win-rate / base-survival / kill floors
// that reflect the current God AI strength. The simulation is
// deterministic, so a fixed seed set yields fixed aggregates — these
// floors catch any regression in the God AI's actual playing strength,
// not just refactor drift.
//
// When params are intentionally changed (e.g. a new CMA-ES round),
// re-measure on seeds 1..30 @classic @18000 and bump the floors to the
// new measured values (keep a small margin for benign variance).
//
// Current measured (seeds 1..30, classic, 18000 ticks) AFTER the P0 T2a
// deadlock fix (2026-07-29, plan/God-AI-Next-Round.md):
//   Stage 0: winRate=66.7%  baseSurvival=90.0%  avgKills=16.9
//   Stage 1: winRate=83.3%  baseSurvival=96.7%  avgKills=19.4
//
// Before P0 (deadlock era), Stage 0 was ~20% / Stage 1 ~22.5% — the
// floors below sit comfortably above those baselines so a regression
// back to the deadlock is caught, while leaving margin for benign tuning.
//
// Run: bun test tests/god-ai-regression-gate.test.ts
// ============================================================

const GATE_SEEDS = Array.from({ length: 30 }, (_, i) => i + 1) // 1..30

interface GateFloors {
  wins: number
  baseAlive: number
  avgKills: number
}

function runGate(stageIdx: number, floors: GateFloors): void {
  let wins = 0
  let baseAlive = 0
  let kills = 0
  const perSeed: string[] = []
  for (const seed of GATE_SEEDS) {
    const r = runSimulation({
      seed,
      stage: STAGES[stageIdx],
      difficulty: 'classic',
      maxTicks: 18000,
      sampleInterval: 18000,
    })
    if (r.outcome === 'stage_clear') wins++
    if (r.finalState.baseAlive) baseAlive++
    kills += r.finalState.killCount
    perSeed.push(
      `s${seed}:${r.outcome[0]}${r.finalState.baseAlive ? 'B' : 'b'}k${r.finalState.killCount}`,
    )
  }
  const n = GATE_SEEDS.length
  const winRate = wins / n
  const baseSurvival = baseAlive / n
  const avgKills = kills / n
  console.log(`[gate stage ${stageIdx}] ${perSeed.join(' ')}`)
  console.log(
    `[gate stage ${stageIdx}] winRate=${(winRate * 100).toFixed(1)}% baseSurvival=${(baseSurvival * 100).toFixed(1)}% avgKills=${avgKills.toFixed(1)}`,
  )
  expect(wins).toBeGreaterThanOrEqual(floors.wins)
  expect(baseAlive).toBeGreaterThanOrEqual(floors.baseAlive)
  expect(avgKills).toBeGreaterThanOrEqual(floors.avgKills)
}

describe('god-ai-regression-gate', () => {
  it('Stage 0 classic aggregate meets the P0 tuning floor', () => {
    runGate(0, { wins: 16, baseAlive: 25, avgKills: 12 })
  }, 180000)

  it('Stage 1 classic aggregate meets the P0 tuning floor', () => {
    runGate(1, { wins: 20, baseAlive: 27, avgKills: 14 })
  }, 180000)
})
