import { describe, it, expect } from 'bun:test'
import {
  runSimulation,
  type ThreatLedgerRun,
  type ThreatLedgerSample,
} from '../tools/sim/simulation-runner'
import { STAGES } from '../src/config/stages'
import {
  classifyFailure,
  NO_OUTPUT_MIN_SAMPLES,
  type FailureClass,
} from '../tools/diag/failure-classifier'

/**
 * M0 threat-ledger tests (plan/God-AI-Hard-Breakthrough-Implementation.md
 * §4 / §10 M0):
 *
 *   1. Parity — a run with `threatLedger: true` is byte-identical to the same
 *      run with it off (the ledger is read-only observation).
 *   2. Classifier — each §4.2 failure family is attributed by the synthetic
 *      evidence that defines it.
 */

function runOnce(seed: number, stageIdx: number, threatLedger: boolean) {
  return runSimulation({
    seed,
    stage: STAGES[stageIdx],
    difficulty: 'hard',
    stageIndex: 0,
    threatLedger,
    maxTicks: 12000,
  })
}

describe('threat ledger parity (M0)', () => {
  it('ledger ON and OFF produce identical runs (determinism preserved)', () => {
    // Several stage/seed combos incl. a base-loss and a clear.
    for (const [stageIdx, seed] of [
      [33, 1], // S34 Battlement hard (base-loss-prone)
      [7, 14], // S8 Riverbed
      [24, 7],
      [0, 3],
    ] as Array<[number, number]>) {
      const off = runOnce(seed, stageIdx, false)
      const on = runOnce(seed, stageIdx, true)
      expect(on.outcome).toBe(off.outcome)
      expect(on.ticks).toBe(off.ticks)
      expect(on.finalState).toEqual(off.finalState)
      expect(on.failure).toEqual(off.failure)
      expect(on.events.length).toBe(off.events.length)
    }
  })

  it('ledger is populated when requested and absent otherwise', () => {
    const on = runOnce(1, 33, true)
    expect(on.ledger).toBeDefined()
    expect(on.ledger!.samples.length).toBeGreaterThan(0)
    expect(on.ledger!.baseMaxHp).toBeGreaterThan(0)
    const off = runOnce(1, 33, false)
    expect(off.ledger).toBeUndefined()
  })

  it('samples are monotonic in tick and trigger on real changes', () => {
    const on = runOnce(2, 7, true)
    const samples = on.ledger!.samples
    for (let i = 1; i < samples.length; i++) {
      expect(samples[i].tick).toBeGreaterThan(samples[i - 1].tick)
    }
    // The first sample is the initial state (tick 0 or 1).
    expect(samples[0].tick).toBeLessThanOrEqual(2)
    // Every sample carries the required fields.
    for (const s of samples) {
      expect(typeof s.baseHp).toBe('number')
      expect(typeof s.intactRing).toBe('number')
      expect(typeof s.branch).toBe('string')
      expect(typeof s.baseThreatNow).toBe('boolean')
      expect(typeof s.threatSlack).toBe('number')
      expect(Array.isArray(s.enemies)).toBe(true)
    }
  })
})

// ============================================================
// Classifier unit tests — synthetic ledgers per §4.2 family
// ============================================================

const baseSample = (over: Partial<ThreatLedgerSample>): ThreatLedgerSample => ({
  tick: 1000,
  baseHp: 120,
  intactRing: 8,
  playerCell: { col: 8, row: 20 },
  playerDir: 'up',
  playerLives: 2,
  branch: 'navigate',
  onCooldown: false,
  liveEnemies: 1,
  baseThreatNow: true,
  nearestThreatEta: 30,
  playerEtaToBestIntercept: 10,
  threatSlack: 20,
  noOpReason: null,
  enemies: [],
  ...over,
})

const ringLedger = (
  samples: ThreatLedgerSample[],
  cause: string = 'base_destroyed',
): ThreatLedgerRun => ({
  outcome: 'gameover',
  failureCause: cause as ThreatLedgerRun['failureCause'],
  tick: samples[samples.length - 1].tick,
  baseMaxHp: 120,
  samples,
})

const csbEnemy = (id: number, col: number, row: number) => ({
  id,
  kind: 'basic' as const,
  hp: 1,
  cell: { col, row },
  dir: 'down' as const,
  canShootBase: true,
  canBreachRing: false,
  enemyToRingEta: 0,
  playerKillEta: 12,
  shootEta: 0,
})

function expectPrimary(ledger: ThreatLedgerRun, expected: FailureClass, cause?: string): void {
  const cls = classifyFailure(ledger, cause ?? ledger.failureCause)
  expect(cls.primary).toBe(expected)
  expect(cls.evidence.length).toBeGreaterThan(0)
}

describe('failure classifier (M0 §4.2)', () => {
  it('player_survival: lives_exhausted with a healthy base', () => {
    const ledger = ringLedger([baseSample({ baseHp: 120 })], 'lives_exhausted')
    expectPrimary(ledger, 'player_survival')
  })

  it('late_detection: base hit before any shoot-capable enemy appeared', () => {
    const ledger = ringLedger([
      baseSample({ tick: 100, baseHp: 120, enemies: [], baseThreatNow: false }),
      baseSample({ tick: 500, baseHp: 120, enemies: [], baseThreatNow: false }),
      baseSample({ tick: 600, baseHp: 20, enemies: [], baseThreatNow: false }), // first hit
      baseSample({ tick: 700, baseHp: 20, enemies: [csbEnemy(1, 12, 18)] }), // danger appears late
    ])
    expectPrimary(ledger, 'late_detection')
  })

  it('not late_detection when a threat was visible before the hit', () => {
    const ledger = ringLedger([
      baseSample({ tick: 100, baseHp: 120, enemies: [csbEnemy(1, 12, 18)] }),
      baseSample({ tick: 600, baseHp: 20, enemies: [csbEnemy(1, 12, 18)] }),
    ])
    expect(classifyFailure(ledger, 'base_destroyed').primary).not.toBe('late_detection')
  })

  it('no_output_commit: 3+ consecutive no-op samples with an active threat', () => {
    const noOps = Array.from({ length: NO_OUTPUT_MIN_SAMPLES }, (_, i) =>
      baseSample({ tick: 1000 + i * 50, noOpReason: 'midLaneDefense', threatSlack: 10 }),
    )
    const ledger = ringLedger(noOps)
    expectPrimary(ledger, 'no_output_commit')
  })

  it('no_output_commit does not fire on isolated no-op samples', () => {
    const ledger = ringLedger([
      baseSample({ tick: 1000, noOpReason: 'baseLaneSentry' }),
      baseSample({ tick: 1100 }),
      baseSample({ tick: 1200, noOpReason: 'midLaneDefense' }),
    ])
    expect(classifyFailure(ledger, 'base_destroyed').primary).not.toBe('no_output_commit')
  })

  it('multi_threat_overload: two simultaneous shoot-capable enemies', () => {
    const ledger = ringLedger([
      baseSample({
        enemies: [csbEnemy(1, 12, 18), csbEnemy(2, 9, 24)],
        threatSlack: 5,
      }),
    ])
    expectPrimary(ledger, 'multi_threat_overload')
  })

  it('turn_locked: aligned with csb enemy, standing on cooldown', () => {
    const ledger = ringLedger([
      baseSample({
        playerCell: { col: 12, row: 16 }, // same column as csb enemy
        enemies: [csbEnemy(1, 12, 18)],
        onCooldown: true,
        noOpReason: 'baseLaneSentry',
        branch: 'baseLaneSentry',
      }),
    ])
    expectPrimary(ledger, 'turn_locked')
  })

  it('travel_late: player ETA exceeds the threat deadline', () => {
    const ledger = ringLedger([
      baseSample({
        enemies: [csbEnemy(1, 12, 18)],
        nearestThreatEta: 5,
        playerEtaToBestIntercept: 40,
        threatSlack: -35,
        branch: 'navigate',
      }),
    ])
    expectPrimary(ledger, 'travel_late')
  })

  it('wrong_target: offense branch with movement while the base is threatened', () => {
    const ledger = ringLedger([
      baseSample({
        branch: 'hunt',
        enemies: [csbEnemy(1, 12, 18)],
        noOpReason: null,
      }),
    ])
    expectPrimary(ledger, 'wrong_target')
  })

  it('unknown: timeout', () => {
    const ledger = ringLedger([baseSample({})], 'timeout')
    expectPrimary(ledger, 'unknown', 'timeout')
  })

  it('every classification carries explainable evidence', () => {
    const ledgers = [
      ringLedger([baseSample({ baseHp: 120 })], 'lives_exhausted'),
      ringLedger([baseSample({ tick: 1, baseHp: 120 }), baseSample({ tick: 2, baseHp: 20 })]),
    ]
    for (const l of ledgers) {
      const cls = classifyFailure(l, l.failureCause)
      expect(cls.evidence.join(' ').length).toBeGreaterThan(0)
    }
  })
})
