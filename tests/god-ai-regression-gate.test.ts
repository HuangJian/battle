import { describe, it, expect } from 'bun:test'
import { runSimulation } from '../tools/simulation-runner'
import { STAGES } from '../src/config/stages'

// ============================================================
// God AI Wide-Seed Regression Gate (plan/God-AI-Curriculum §6)
//
// The split-parity test above locks exact per-seed output (8 seeds).
// This gate is the REAL tuning regression check: it runs a WIDE, fixed
// seed set and asserts aggregate win-rate / base-survival / kill floors
// that reflect the 2026-07-28 CMA-ES v3 + float-param-fix state.
//
// The simulation is deterministic, so a fixed seed set yields fixed
// aggregates. These floors therefore catch any regression in the God
// AI's actual playing strength — not just refactor drift. When params
// are intentionally changed, re-measure on seeds 1..30 @classic @18000
// and bump the floors to the new measured values.
//
// Current measured (seeds 1..30, classic, 18000 ticks):
//   winRate=23.3%  baseSurvival=86.7%  avgKills=11.3
//
// Run: bun test tests/god-ai-regression-gate.test.ts
// ============================================================

const GATE_SEEDS = Array.from({ length: 30 }, (_, i) => i + 1) // 1..30

describe('god-ai-regression-gate', () => {
  it('wide-seed classic aggregate meets the v3 tuning floor', () => {
    let wins = 0
    let baseAlive = 0
    let kills = 0
    const perSeed: string[] = []
    for (const seed of GATE_SEEDS) {
      const r = runSimulation({
        seed,
        stage: STAGES[0],
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
    console.log(`[gate] ${perSeed.join(' ')}`)
    console.log(
      `[gate] winRate=${(winRate * 100).toFixed(1)}% baseSurvival=${(baseSurvival * 100).toFixed(1)}% avgKills=${avgKills.toFixed(1)}`,
    )

    // Floors reflect the 2026-07-28 state (7 wins, 26 base-alive, ~11 kills).
    // A 1-seed margin keeps the gate stable against minor benign variance.
    expect(wins).toBeGreaterThanOrEqual(6)
    expect(baseAlive).toBeGreaterThanOrEqual(25)
    expect(avgKills).toBeGreaterThanOrEqual(9)
  }, 120000)
})
