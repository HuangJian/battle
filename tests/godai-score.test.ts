import { describe, it, expect } from 'bun:test'
import {
  scoreRun,
  computeDimensions,
  aggregateStage,
  aggregateSuite,
  cvar,
  powerMean,
  comparePaired,
  diagnoseWeights,
  fitnessV6,
  fitnessV7,
  LOSS_BAND_MAX,
  CLEAR_BAND_MIN,
  DEFAULT_SCORE_CONFIG,
  V7_LOSS_BAND_MAX,
  V7_CLEAR_BAND_MIN,
  V7_SCORE_CONFIG,
  DEFAULT_STAGE_REFS,
  DEFAULT_LOSS_WEIGHTS,
  type ScorableRun,
  type RunScore,
} from '../tools/eval/godai-score'
import type { RunTelemetry } from '../tools/sim/simulation-runner'

/**
 * v6 evaluation model — axiom tests (plan/God-AI-Evaluation-Redesign.md §2).
 *
 * The scorer is the objective function the God AI tuner optimises against, so
 * a flaw here does not produce a wrong number — it produces a wrong AI. v5's
 * history is the cautionary tale: the gameover loophole was a scoring bug that
 * taught the optimizer to sacrifice the base for points.
 *
 * These tests pin the six properties that make the score safe to optimise:
 *   A1 outcome dominance     no loss can outscore any clear
 *   A2 monotonicity          more kills/lives/base integrity never lowers score
 *   A3 boundedness           scores stay in [0,1] under any input
 *   A4 no dominated exploit  no reachable state beats a strictly better one
 *   A5 determinism           same input ⇒ same output, always
 *   A6 difficulty neutrality per-stage references, not global constants
 */

function tel(over: Partial<RunTelemetry> = {}): RunTelemetry {
  return {
    enemyTotal: 20,
    startLives: 3,
    playerDeaths: 0,
    playerShots: 100,
    powerUpsSpawned: 2,
    powerUpsCollected: 1,
    starsCollected: 1,
    finalPlayerLevel: 1,
    baseWallIntact: 8,
    baseWallTotal: 8,
    basePressureMean: 0.05,
    basePressureSamples: 500,
    cellsVisited: 110,
    ...over,
  }
}

function run(over: Partial<ScorableRun> = {}, t: Partial<RunTelemetry> = {}): ScorableRun {
  return {
    outcome: 'max_ticks',
    ticks: 9000,
    finalState: { killCount: 10, lives: 2, baseAlive: true },
    firstKillTick: 600,
    telemetry: tel(t),
    ...over,
  }
}

const clear = (over: Partial<ScorableRun> = {}, t: Partial<RunTelemetry> = {}): ScorableRun =>
  run(
    {
      outcome: 'stage_clear',
      ticks: 7000,
      finalState: { killCount: 20, lives: 2, baseAlive: true },
      ...over,
    },
    t,
  )

describe('A1 — outcome dominance', () => {
  it('the best possible loss still scores below the worst possible clear', () => {
    const bestLoss = scoreRun(
      run(
        { ticks: 3600, finalState: { killCount: 19, lives: 3, baseAlive: true } },
        {
          playerShots: 20,
          powerUpsCollected: 2,
          finalPlayerLevel: 3,
          basePressureMean: 0,
          cellsVisited: 400,
        },
      ),
    )
    const worstClear = scoreRun(
      clear(
        { ticks: 18000, finalState: { killCount: 20, lives: 0, baseAlive: true } },
        {
          playerShots: 2000,
          powerUpsSpawned: 4,
          powerUpsCollected: 0,
          finalPlayerLevel: 0,
          baseWallIntact: 0,
          basePressureMean: 1,
          cellsVisited: 1,
        },
      ),
    )
    expect(bestLoss.score).toBeLessThanOrEqual(LOSS_BAND_MAX)
    expect(worstClear.score).toBeGreaterThanOrEqual(CLEAR_BAND_MIN)
    expect(bestLoss.score).toBeLessThan(worstClear.score)
  })

  it('a base-destroyed gameover cannot beat a timeout that saved the base', () => {
    // The v5 loophole in its purest form: v4's fitness preferred a 3-kill
    // gameover to a 0-kill timeout. Here the gameover must lose outright.
    const gameover = scoreRun(
      run(
        { outcome: 'gameover', finalState: { killCount: 3, lives: 0, baseAlive: false } },
        { baseWallIntact: 0 },
      ),
    )
    const timeout = scoreRun(
      run({ outcome: 'max_ticks', finalState: { killCount: 0, lives: 1, baseAlive: true } }),
    )
    expect(gameover.score).toBeLessThan(timeout.score)
  })
})

describe('A2 — monotonicity', () => {
  it('score rises strictly with kills on a loss (0 vs 19 must differ)', () => {
    const scores: number[] = []
    for (let k = 0; k <= 20; k++) {
      scores.push(scoreRun(run({ finalState: { killCount: k, lives: 1, baseAlive: true } })).score)
    }
    for (let i = 1; i < scores.length; i++) {
      expect(scores[i]).toBeGreaterThan(scores[i - 1])
    }
    // The headline complaint about the old standard: these were identical.
    expect(scores[19] - scores[0]).toBeGreaterThan(0.2)
  })

  it('score rises strictly with lives on a clear (0 vs 3 must differ)', () => {
    const scores = [0, 1, 2, 3].map(
      (l) => scoreRun(clear({ finalState: { killCount: 20, lives: l, baseAlive: true } })).score,
    )
    for (let i = 1; i < scores.length; i++) expect(scores[i]).toBeGreaterThan(scores[i - 1])
    expect(scores[3] - scores[0]).toBeGreaterThan(0.1)
  })

  it('a faster clear scores higher than a slower one', () => {
    const fast = scoreRun(clear({ ticks: 3600 })).score
    const slow = scoreRun(clear({ ticks: 14400 })).score
    expect(fast).toBeGreaterThan(slow)
  })

  it('a healthier base scores higher, and a destroyed base scores lowest', () => {
    const intact = scoreRun(run({}, { baseWallIntact: 8 })).score
    const stripped = scoreRun(run({}, { baseWallIntact: 2 })).score
    const destroyed = scoreRun(
      run({ finalState: { killCount: 10, lives: 2, baseAlive: false } }, { baseWallIntact: 0 }),
    ).score
    expect(intact).toBeGreaterThan(stripped)
    expect(stripped).toBeGreaterThan(destroyed)
    expect(
      computeDimensions(run({ finalState: { killCount: 1, lives: 1, baseAlive: false } }))
        .baseIntegrity.value,
    ).toBe(0)
  })

  it('collecting more star buffs never lowers the score', () => {
    const none = scoreRun(clear({}, { powerUpsCollected: 0, finalPlayerLevel: 0 })).score
    const some = scoreRun(clear({}, { powerUpsCollected: 1, finalPlayerLevel: 1 })).score
    const many = scoreRun(clear({}, { powerUpsCollected: 2, finalPlayerLevel: 3 })).score
    expect(some).toBeGreaterThan(none)
    expect(many).toBeGreaterThan(some)
  })
})

describe('A3 — boundedness', () => {
  it('every score lands in [0,1] under absurd inputs', () => {
    const wild: ScorableRun[] = [
      run({ ticks: 0, finalState: { killCount: 0, lives: 0, baseAlive: false } }),
      run({ ticks: 1e9, finalState: { killCount: 1e6, lives: 1e6, baseAlive: true } }),
      run({ finalState: { killCount: -5, lives: -3, baseAlive: true } }),
      clear({ ticks: -100 }),
      { outcome: 'error', ticks: 0, finalState: { killCount: 0, lives: 0, baseAlive: false } },
      run(
        {},
        {
          enemyTotal: 0,
          startLives: 0,
          playerShots: 0,
          powerUpsSpawned: 0,
          basePressureSamples: 0,
          baseWallTotal: 0,
        },
      ),
    ]
    for (const r of wild) {
      const s = scoreRun(r)
      expect(s.score).toBeGreaterThanOrEqual(0)
      expect(s.score).toBeLessThanOrEqual(1)
      expect(Number.isFinite(s.score)).toBe(true)
    }
  })

  it('NaN telemetry degrades to a finite score instead of poisoning the suite', () => {
    const s = scoreRun(run({ ticks: NaN }, { basePressureMean: NaN, cellsVisited: NaN }))
    expect(Number.isFinite(s.score)).toBe(true)
    expect(s.score).toBeGreaterThanOrEqual(0)
  })

  it('a run with no telemetry at all is still scorable', () => {
    const s = scoreRun({
      outcome: 'stage_clear',
      ticks: 5000,
      finalState: { killCount: 20, lives: 2, baseAlive: true },
    })
    expect(s.score).toBeGreaterThanOrEqual(CLEAR_BAND_MIN)
    expect(s.score).toBeLessThanOrEqual(1)
  })
})

describe('A4 — no dominated exploit', () => {
  it('a strictly worse run never outscores a strictly better one', () => {
    // Sweep the reachable space; any Pareto-dominated run must score lower.
    type Pt = { kills: number; lives: number; wall: number; ticks: number; run: ScorableRun }
    const pts: Pt[] = []
    for (const kills of [0, 5, 10, 15, 20]) {
      for (const lives of [0, 1, 2, 3]) {
        for (const wall of [0, 4, 8]) {
          for (const ticks of [4000, 9000, 15000]) {
            pts.push({
              kills,
              lives,
              wall,
              ticks,
              run: run(
                { ticks, finalState: { killCount: kills, lives, baseAlive: true } },
                { baseWallIntact: wall },
              ),
            })
          }
        }
      }
    }
    const scored = pts.map((p) => ({ ...p, s: scoreRun(p.run).score }))
    let checked = 0
    for (const a of scored) {
      for (const b of scored) {
        // b dominates a: at least as good everywhere, strictly better somewhere.
        // (fewer ticks is better on a loss only via tempo, so compare at equal ticks)
        if (a.ticks !== b.ticks) continue
        const dominates =
          b.kills >= a.kills &&
          b.lives >= a.lives &&
          b.wall >= a.wall &&
          (b.kills > a.kills || b.lives > a.lives || b.wall > a.wall)
        if (!dominates) continue
        checked++
        expect(b.s).toBeGreaterThan(a.s)
      }
    }
    expect(checked).toBeGreaterThan(100)
  })

  it('stalling to farm time cannot beat finishing the job', () => {
    const stall = scoreRun(
      run({ ticks: 18000, finalState: { killCount: 19, lives: 3, baseAlive: true } }),
    ).score
    const finish = scoreRun(
      clear({ ticks: 18000, finalState: { killCount: 20, lives: 0, baseAlive: true } }),
    ).score
    expect(finish).toBeGreaterThan(stall)
  })
})

describe('A5 — determinism', () => {
  it('scoring the same run twice yields bit-identical output', () => {
    const r = run()
    const a = scoreRun(r)
    const b = scoreRun(r)
    expect(a.score).toBe(b.score)
    expect(a.quality).toBe(b.quality)
    expect(JSON.stringify(a.dims)).toBe(JSON.stringify(b.dims))
  })

  it('aggregation is order-independent in value and reproducible', () => {
    const runs: RunScore[] = [0.1, 0.9, 0.5, 0.3, 0.7].map((v) =>
      scoreRun(run({ finalState: { killCount: Math.round(v * 20), lives: 1, baseAlive: true } })),
    )
    const a = aggregateStage('X', runs)
    const b = aggregateStage('X', [...runs].reverse())
    expect(a.score).toBeCloseTo(b.score, 12)
    expect(a.cvar).toBeCloseTo(b.cvar, 12)
    expect(a.winRate).toBe(b.winRate)
  })
})

describe('A6 — difficulty neutrality', () => {
  it('the same clear time scores differently under different stage references', () => {
    const r = clear({ ticks: 7000 })
    const easy = scoreRun(r, {
      ...DEFAULT_SCORE_CONFIG,
      refs: { ...DEFAULT_STAGE_REFS, clearTicksFast: 2000, clearTicksSlow: 6000 },
    })
    const hard = scoreRun(r, {
      ...DEFAULT_SCORE_CONFIG,
      refs: { ...DEFAULT_STAGE_REFS, clearTicksFast: 6000, clearTicksSlow: 20000 },
    })
    // 7000 ticks is slow for the easy stage, brisk for the hard one.
    expect(easy.dims.clearSpeed.value).toBe(0)
    expect(hard.dims.clearSpeed.value!).toBeGreaterThan(0.8)
    expect(hard.score).toBeGreaterThan(easy.score)
  })
})

describe('weight renormalisation', () => {
  it('dropping an inapplicable dimension redistributes its weight, not zeroes it', () => {
    const collectedAll = scoreRun(clear({}, { powerUpsSpawned: 2, powerUpsCollected: 2 }))
    const collectedNone = scoreRun(clear({}, { powerUpsSpawned: 2, powerUpsCollected: 0 }))
    const noneOffered = scoreRun(clear({}, { powerUpsSpawned: 0, powerUpsCollected: 0 }))

    // A stage that never offered a power-up has nothing to measure, so `loot`
    // must leave the weighted mean entirely...
    expect(noneOffered.effectiveWeights.loot).toBeUndefined()
    expect(collectedAll.effectiveWeights.loot).toBeGreaterThan(0)

    // ...which makes it score as if loot were neutral: strictly better than
    // being blamed for missing power-ups that were there, strictly worse than
    // being credited for sweeping them up. Treating "absent" as 0 (the naive
    // implementation) would collapse the first two of these into one value and
    // systematically underrate every low-drop stage.
    expect(noneOffered.score).toBeGreaterThan(collectedNone.score)
    expect(noneOffered.score).toBeLessThan(collectedAll.score)

    // The redistribution is exact: the surviving weights still sum to 1.
    const sum = Object.values(noneOffered.effectiveWeights).reduce((a, b) => a + b, 0)
    expect(sum).toBeCloseTo(1, 12)
  })

  it('effective weights always sum to 1', () => {
    for (const r of [
      scoreRun(run()),
      scoreRun(clear()),
      scoreRun(clear({}, { powerUpsSpawned: 0 })),
    ]) {
      const sum = Object.values(r.effectiveWeights).reduce((a, b) => a + b, 0)
      expect(sum).toBeCloseTo(1, 10)
    }
  })
})

describe('aggregation math', () => {
  it('CVaR is the mean of the worst tail, not the mean', () => {
    const v = [0, 0.25, 0.5, 0.75, 1]
    expect(cvar(v, 0.2)).toBeCloseTo(0, 10)
    expect(cvar(v, 0.4)).toBeCloseTo(0.125, 10)
    expect(cvar(v, 1)).toBeCloseTo(0.5, 10)
  })

  it('power mean interpolates from arithmetic to minimum as p falls', () => {
    const v = [0.2, 0.9, 0.9, 0.9]
    const arith = powerMean(v, 1)
    const geo = powerMean(v, 0)
    const harm = powerMean(v, -1)
    expect(arith).toBeCloseTo(0.725, 10)
    expect(geo).toBeLessThan(arith)
    expect(harm).toBeLessThan(geo)
    expect(harm).toBeGreaterThan(Math.min(...v))
    expect(powerMean(v, -30)).toBeCloseTo(0.2, 1)
  })

  it('a soft minimum makes fixing the weakest stage worth more than padding the best', () => {
    // This is the property that replaces v5's discontinuous floor penalty.
    const base = [0.3, 0.9, 0.9]
    const liftWeak = powerMean([0.4, 0.9, 0.9], -1)
    const liftStrong = powerMean([0.3, 1.0, 0.9], -1)
    expect(liftWeak).toBeGreaterThan(powerMean(base, -1))
    expect(liftWeak).toBeGreaterThan(liftStrong)
  })

  it('power mean never divides by zero when a stage scores 0', () => {
    expect(Number.isFinite(powerMean([0, 0.9], -1, 0.02))).toBe(true)
  })

  it('the suite penalises a collapsed stage more than the arithmetic mean does', () => {
    const mk = (name: string, scores: number[]) =>
      aggregateStage(
        name,
        scores.map((v) => ({
          score: v,
          quality: v,
          cleared: v >= CLEAR_BAND_MIN,
          dims: {} as never,
          effectiveWeights: {},
        })),
      )
    const even = aggregateSuite([mk('a', [0.7]), mk('b', [0.7]), mk('c', [0.7])])
    const spiky = aggregateSuite([mk('a', [0.95]), mk('b', [0.95]), mk('c', [0.2])])
    expect(spiky.arithmeticMean).toBeCloseTo(0.7, 10)
    expect(even.arithmeticMean).toBeCloseTo(0.7, 10)
    // Same average, very different risk — the suite score must separate them.
    expect(spiky.suite).toBeLessThan(even.suite)
    expect(spiky.worstStage!.name).toBe('c')
  })

  it('the lower confidence bound sits below the point estimate and rewards more seeds', () => {
    const noisy = aggregateStage(
      'n',
      [0.1, 0.95, 0.2, 0.9].map((v) => ({
        score: v,
        quality: v,
        cleared: false,
        dims: {} as never,
        effectiveWeights: {},
      })),
    )
    const s = aggregateSuite([noisy])
    expect(s.lcb).toBeLessThanOrEqual(s.suite)
    expect(fitnessV6(s)).toBeCloseTo(s.lcb * 1000, 10)
  })

  it('v7 fitness blends quality with win-rate harmonic mean', () => {
    // Two suites with the same quality but different win-rate distributions.
    // The one with a weak stage (50% win) must score lower than the one
    // where all stages are at 80%.
    const mkStage = (name: string, scores: number[]) =>
      aggregateStage(
        name,
        scores.map((v) => ({
          score: v,
          quality: v,
          cleared: v >= CLEAR_BAND_MIN,
          dims: {} as never,
          effectiveWeights: {},
        })),
      )
    // Override winRate manually after construction.
    const mk = (name: string, scores: number[], wr: number) => {
      const s = mkStage(name, scores)
      return { ...s, winRate: wr }
    }
    const balanced = aggregateSuite([mk('a', [0.8, 0.8], 0.8), mk('b', [0.8, 0.8], 0.8)])
    const spiky = aggregateSuite([mk('a', [0.8, 0.8], 1.0), mk('b', [0.8, 0.8], 0.6)])
    // Same arithmetic mean win rate (0.8), but the spiky one has a 60% stage.
    expect(spiky.meanWinRate).toBeCloseTo(balanced.meanWinRate, 10)
    // v6 fitness ignores win rate entirely → identical.
    expect(fitnessV6(spiky)).toBe(fitnessV6(balanced))
    // v7 fitness penalises the weak stage via harmonic mean.
    expect(fitnessV7(spiky)).toBeLessThan(fitnessV7(balanced))
  })

  it('empty inputs are handled without NaN', () => {
    const s = aggregateSuite([])
    expect(s.suite).toBe(0)
    expect(s.worstStage).toBeNull()
    expect(aggregateStage('x', []).score).toBe(0)
    expect(cvar([], 0.2)).toBe(0)
    expect(powerMean([], -1)).toBe(0)
  })
})

describe('paired comparison', () => {
  it('detects a consistent improvement that a win-rate diff would miss', () => {
    // Every cell improves slightly, but no loss flips to a win — the old
    // metric reports "no change", the paired continuous test sees it clearly.
    // The deltas vary, so this exercises the ordinary (non-degenerate) path.
    const a = Array.from({ length: 40 }, (_, i) => 0.3 + (i % 5) * 0.01)
    const b = a.map((v, i) => v + 0.02 + (i % 3) * 0.002)
    const c = comparePaired(a, b)
    expect(c.n).toBe(40)
    expect(c.meanDelta).toBeGreaterThan(0.02)
    expect(c.wins).toBe(40)
    expect(c.se).toBeGreaterThan(0)
    expect(c.p).toBeLessThan(0.001)
  })

  it('treats a perfectly uniform shift as maximally significant, not as noise', () => {
    // Degenerate case: zero within-pair variance. A naive `se === 0 ⇒ t = 0`
    // guard reports p = 1 here — "no difference" for a change that improved
    // every single cell. That would silently block real wins at the A/B gate.
    const a = Array.from({ length: 30 }, (_, i) => 0.3 + (i % 5) * 0.01)
    const b = a.map((v) => v + 0.02)
    const c = comparePaired(a, b)
    expect(c.se).toBe(0)
    expect(c.wins).toBe(30)
    expect(c.t).toBe(Infinity)
    expect(c.p).toBe(0)
    // ...and a perfectly uniform *no-op* is still correctly insignificant.
    const same = comparePaired(a, [...a])
    expect(same.t).toBe(0)
    // (the erf approximation carries ~1e-9 absolute error, hence not toBe(1))
    expect(same.p).toBeCloseTo(1, 6)
  })

  it('reports no significance for pure noise', () => {
    const a = Array.from({ length: 40 }, (_, i) => 0.5 + Math.sin(i) * 0.2)
    const b = Array.from({ length: 40 }, (_, i) => 0.5 + Math.cos(i) * 0.2)
    expect(comparePaired(a, b).p).toBeGreaterThan(0.05)
  })

  it('is antisymmetric and safe on empty input', () => {
    const a = [0.1, 0.4, 0.6]
    const b = [0.2, 0.3, 0.9]
    expect(comparePaired(a, b).meanDelta).toBeCloseTo(-comparePaired(b, a).meanDelta, 12)
    expect(comparePaired([], []).p).toBe(1)
  })
})

describe('weight diagnostics', () => {
  it('flags a saturated dimension as having zero real influence', () => {
    // Every run maxes out `tempo`, so its nominal weight buys no ranking power.
    const runs = [5, 10, 15, 20].map((k) =>
      scoreRun(run({ finalState: { killCount: k, lives: 1, baseAlive: true } }, {}), {
        ...DEFAULT_SCORE_CONFIG,
        refs: { ...DEFAULT_STAGE_REFS, kpmRef: 0.001 },
      }),
    )
    const diag = diagnoseWeights(runs, DEFAULT_LOSS_WEIGHTS)
    const tempo = diag.find((d) => d.key === 'tempo')!
    expect(tempo.stdev).toBe(0)
    expect(tempo.effective).toBe(0)
    expect(tempo.nominal).toBeGreaterThan(0)
  })

  it('effective weights sum to 1 across a varied corpus', () => {
    const runs = [0, 5, 10, 15, 20].map((k) =>
      scoreRun(
        run(
          { finalState: { killCount: k, lives: k % 4, baseAlive: true } },
          { baseWallIntact: k % 9 },
        ),
      ),
    )
    const total = diagnoseWeights(runs, DEFAULT_LOSS_WEIGHTS).reduce((a, d) => a + d.effective, 0)
    expect(total).toBeCloseTo(1, 10)
  })
})

describe('the four scenarios from the brief', () => {
  it('separates 0-kill loss, 19-kill loss, 0-life clear and 3-life clear', () => {
    const zeroKillLoss = scoreRun(
      run(
        {
          outcome: 'gameover',
          ticks: 6000,
          finalState: { killCount: 0, lives: 0, baseAlive: false },
        },
        {
          playerDeaths: 3,
          baseWallIntact: 0,
          basePressureMean: 0.45,
          finalPlayerLevel: 0,
          powerUpsSpawned: 0,
          cellsVisited: 30,
        },
      ),
    ).score
    const nearMissLoss = scoreRun(
      run(
        {
          outcome: 'max_ticks',
          ticks: 18000,
          finalState: { killCount: 19, lives: 1, baseAlive: true },
        },
        { playerDeaths: 2, baseWallIntact: 6, basePressureMean: 0.12 },
      ),
    ).score
    const uglyClear = scoreRun(
      clear(
        { ticks: 14000, finalState: { killCount: 20, lives: 0, baseAlive: true } },
        {
          playerDeaths: 3,
          baseWallIntact: 1,
          basePressureMean: 0.3,
          finalPlayerLevel: 0,
          powerUpsCollected: 0,
        },
      ),
    ).score
    const cleanClear = scoreRun(
      clear(
        { ticks: 3400, finalState: { killCount: 20, lives: 3, baseAlive: true } },
        { playerShots: 60, powerUpsCollected: 2, finalPlayerLevel: 3, basePressureMean: 0.02 },
      ),
    ).score

    expect(zeroKillLoss).toBeLessThan(nearMissLoss)
    expect(nearMissLoss).toBeLessThan(uglyClear)
    expect(uglyClear).toBeLessThan(cleanClear)
    // All four used to collapse into two values (0 or 1). They must now be
    // meaningfully spread, not merely ordered.
    expect(nearMissLoss - zeroKillLoss).toBeGreaterThan(0.3)
    expect(cleanClear - uglyClear).toBeGreaterThan(0.2)
  })
})

describe('v7 — widened band gap', () => {
  it('the v7 gap is 6× wider than v6, making loss→win conversion the top-margin move', () => {
    const v6Gap = CLEAR_BAND_MIN - LOSS_BAND_MAX
    const v7Gap = V7_CLEAR_BAND_MIN - V7_LOSS_BAND_MAX
    expect(v6Gap).toBeCloseTo(0.05, 10)
    expect(v7Gap).toBeCloseTo(0.3, 10)
    expect(v7Gap).toBeGreaterThan(v6Gap * 5)
  })

  it('A1 still holds: the best v7 loss is below the worst v7 clear', () => {
    const bestLoss = scoreRun(
      run(
        { ticks: 3600, finalState: { killCount: 19, lives: 3, baseAlive: true } },
        {
          playerShots: 20,
          powerUpsCollected: 2,
          finalPlayerLevel: 3,
          basePressureMean: 0,
          cellsVisited: 400,
        },
      ),
      V7_SCORE_CONFIG,
    )
    const worstClear = scoreRun(
      clear(
        { ticks: 18000, finalState: { killCount: 20, lives: 0, baseAlive: true } },
        {
          playerShots: 2000,
          powerUpsSpawned: 4,
          powerUpsCollected: 0,
          finalPlayerLevel: 0,
          baseWallIntact: 0,
          basePressureMean: 1,
          cellsVisited: 1,
        },
      ),
      V7_SCORE_CONFIG,
    )
    expect(bestLoss.score).toBeLessThanOrEqual(V7_LOSS_BAND_MAX)
    expect(worstClear.score).toBeGreaterThanOrEqual(V7_CLEAR_BAND_MIN)
    expect(bestLoss.score).toBeLessThan(worstClear.score)
  })

  it('v7 within-band discrimination is preserved (0-kill loss ≠ 19-kill loss)', () => {
    const zeroKill = scoreRun(
      run({ finalState: { killCount: 0, lives: 1, baseAlive: true } }),
      V7_SCORE_CONFIG,
    ).score
    const nineteenKill = scoreRun(
      run({ finalState: { killCount: 19, lives: 1, baseAlive: true } }),
      V7_SCORE_CONFIG,
    ).score
    // With lossBandMax=0.40, the within-band range is narrower than v6's 0.55,
    // but the 0→19 kill spread still produces >0.15 of discrimination.
    expect(nineteenKill - zeroKill).toBeGreaterThan(0.15)
    expect(nineteenKill).toBeLessThanOrEqual(V7_LOSS_BAND_MAX)
  })

  it('v7 makes a mixed-win-rate stage score lower than v6 does (steeper boundary)', () => {
    // A stage with 50% win rate: half losses (best-case), half clears (worst-case).
    const lossRun = run(
      { ticks: 3600, finalState: { killCount: 19, lives: 3, baseAlive: true } },
      { playerShots: 20, powerUpsCollected: 2, finalPlayerLevel: 3, basePressureMean: 0 },
    )
    const clearRun = clear(
      { ticks: 18000, finalState: { killCount: 20, lives: 0, baseAlive: true } },
      { playerShots: 2000, baseWallIntact: 0, basePressureMean: 1, finalPlayerLevel: 0 },
    )
    const v6Loss = scoreRun(lossRun).score
    const v6Clear = scoreRun(clearRun).score
    const v7Loss = scoreRun(lossRun, V7_SCORE_CONFIG).score
    const v7Clear = scoreRun(clearRun, V7_SCORE_CONFIG).score
    // The v7 loss is lower and the v7 clear is higher, so the harmonic mean
    // of a mixed stage is pulled down harder — exactly the property that
    // pushes the optimizer to convert losses to wins.
    expect(v7Loss).toBeLessThan(v6Loss)
    expect(v7Clear).toBeGreaterThan(v6Clear)
  })
})
