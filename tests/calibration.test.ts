import { describe, it, expect } from 'bun:test'
import { batchRun, parseSeedSpec, type BatchOptions } from '../tools/sim/batch-sim'
import { generateReport, formatReport } from '../tools/sim/report'
import { runAICalibration } from '../tools/eval/ai-calibrate'
import { runEvaluationCalibration } from '../tools/eval/calibrate'
import { STAGES } from '../src/config/stages'
import { generateStage } from '../tools/level/level-gen'

// ============================================================
// Helpers
// ============================================================

function makeBatchOpts(overrides?: Partial<BatchOptions>): BatchOptions {
  return {
    stages: STAGES.slice(0, 2),
    stageNames: STAGES.slice(0, 2).map((s) => s.name),
    difficulty: 'hard',
    seeds: [1, 2],
    maxTicks: 300,
    sampleInterval: 30,
    evaluate: true,
    ...overrides,
  }
}

// ============================================================
// Tests
// ============================================================

describe('batch-sim', () => {
  describe('parseSeedSpec', () => {
    it('parses a single seed', () => {
      expect(parseSeedSpec('42')).toEqual([42])
    })

    it('parses a seed range', () => {
      expect(parseSeedSpec('1-5')).toEqual([1, 2, 3, 4, 5])
    })

    it('parses a single-element range', () => {
      expect(parseSeedSpec('3-3')).toEqual([3])
    })
  })

  describe('batchRun', () => {
    it('runs the correct number of simulations', () => {
      const results = batchRun(makeBatchOpts())
      expect(results).toHaveLength(4) // 2 stages × 2 seeds
    })

    it('produces results with correct structure', () => {
      const results = batchRun(makeBatchOpts())
      for (const r of results) {
        expect(r).toHaveProperty('stageIndex')
        expect(r).toHaveProperty('stageName')
        expect(r).toHaveProperty('seed')
        expect(r).toHaveProperty('simResult')
        expect(r.simResult).toHaveProperty('outcome')
        expect(r.simResult).toHaveProperty('ticks')
        expect(r.simResult).toHaveProperty('finalState')
      }
    })

    it('includes evaluation reports when evaluate=true', () => {
      const results = batchRun(makeBatchOpts({ evaluate: true }))
      for (const r of results) {
        expect(r.evalReport).toBeDefined()
        expect(r.evalReport).toHaveProperty('hardPass')
        expect(r.evalReport).toHaveProperty('softScore')
        expect(r.evalReport).toHaveProperty('totalScore')
      }
    })

    it('omits evaluation reports when evaluate=false', () => {
      const results = batchRun(makeBatchOpts({ evaluate: false }))
      for (const r of results) {
        expect(r.evalReport).toBeUndefined()
      }
    })

    it('works with generated stages', () => {
      const stage = generateStage({ seed: 1, difficulty: 'hard', theme: 'forest' })
      const results = batchRun({
        stages: [stage],
        stageNames: [stage.name],
        difficulty: 'hard',
        seeds: [1, 2],
        maxTicks: 300,
        sampleInterval: 30,
        evaluate: true,
      })
      expect(results).toHaveLength(2)
    })

    it('is deterministic: same opts = same results', () => {
      const opts = makeBatchOpts()
      const r1 = batchRun(opts)
      const r2 = batchRun(opts)
      expect(r1.length).toBe(r2.length)
      for (let i = 0; i < r1.length; i++) {
        expect(r1[i].simResult.outcome).toBe(r2[i].simResult.outcome)
        expect(r1[i].simResult.ticks).toBe(r2[i].simResult.ticks)
      }
    })
  })
})

describe('report', () => {
  it('generates a report with correct structure', () => {
    const results = batchRun(makeBatchOpts())
    const report = generateReport(results)
    expect(report).toHaveProperty('summary')
    expect(report).toHaveProperty('perStage')
    expect(report).toHaveProperty('metricDistributions')
    expect(report.summary.totalRuns).toBe(4)
    expect(report.perStage).toHaveLength(2) // 2 stages
  })

  it('formatReport produces human-readable text', () => {
    const results = batchRun(makeBatchOpts())
    const report = generateReport(results)
    const text = formatReport(report)
    expect(text).toContain('Batch Simulation Report')
    expect(text).toContain('Total runs:')
    expect(text).toContain('Pass rate:')
  })
})

describe('ai-calibrate', () => {
  it('produces a valid calibration result', () => {
    // Use a small stage subset to keep the test under the 5s timeout.
    // The full calibration (35 stages × 100 seeds) is run via the CLI.
    const testStages = STAGES.slice(0, 3)
    const result = runAICalibration({
      stages: testStages,
      seeds: [1, 2],
      difficulties: ['hard'],
      maxTicks: 300,
      sampleInterval: 30,
    })

    expect(result).toHaveProperty('godAI')
    expect(result).toHaveProperty('skilledHuman')
    expect(result).toHaveProperty('gates')
    expect(result).toHaveProperty('timestamp')

    // God AI params
    expect(result.godAI.params).toHaveProperty('reactionDelay')
    expect(result.godAI.params).toHaveProperty('aimError')
    expect(result.godAI.params).toHaveProperty('suboptimalPathProb')

    // Skilled Human params should have higher imperfections
    expect(result.skilledHuman.params.reactionDelay).toBeGreaterThan(
      result.godAI.params.reactionDelay,
    )
    expect(result.skilledHuman.params.aimError).toBeGreaterThan(result.godAI.params.aimError)

    // Per-difficulty results
    expect(result.godAI.perDifficulty['hard']).toBeDefined()
    expect(result.godAI.perDifficulty['hard'].totalRuns).toBe(6) // 3 stages × 2 seeds
  })

  it('gates are booleans', () => {
    const result = runAICalibration({
      seeds: [1],
      difficulties: ['hard'],
      maxTicks: 100,
    })
    expect(typeof result.gates.godAIHardPass).toBe('boolean')
    expect(typeof result.gates.godAIChaosPass).toBe('boolean')
    expect(typeof result.gates.skilledHumanHardPass).toBe('boolean')
    expect(typeof result.gates.skilledHumanChaosPass).toBe('boolean')
  })
})

describe('calibrate', () => {
  it('produces a valid calibration result', () => {
    const result = runEvaluationCalibration({
      seeds: [1, 2],
      difficulties: ['hard'],
      maxTicks: 300,
      sampleInterval: 30,
    })

    expect(result).toHaveProperty('baseline')
    expect(result).toHaveProperty('stageMetrics')
    expect(result).toHaveProperty('globalDistributions')
    expect(result).toHaveProperty('timestamp')

    // Baseline should have soft metric configs
    expect(result.baseline.soft).toHaveProperty('kpm')
    expect(result.baseline.soft).toHaveProperty('bulletDensity')
    expect(result.baseline.soft).toHaveProperty('threatRate')

    // Stage metrics
    expect(result.stageMetrics.length).toBeGreaterThan(0)
    expect(result.stageMetrics.length).toBe(35) // 35 classic stages

    // Global distributions
    const metricKeys = Object.keys(result.globalDistributions)
    expect(metricKeys.length).toBeGreaterThan(0)
  })

  it('calibrated baseline targets are within reasonable ranges', () => {
    const result = runEvaluationCalibration({
      seeds: [1, 2],
      difficulties: ['hard'],
      maxTicks: 300,
      sampleInterval: 30,
    })

    // KPM targets should be non-negative
    const [kpmLo, kpmHi] = result.baseline.soft.kpm.target
    expect(kpmLo).toBeGreaterThanOrEqual(0)
    expect(kpmHi).toBeGreaterThanOrEqual(kpmLo)

    // Terrain utilization should be between 0 and 1
    const [tuLo, tuHi] = result.baseline.soft.terrainUtil.target
    expect(tuLo).toBeGreaterThanOrEqual(0)
    expect(tuHi).toBeLessThanOrEqual(1)
  })
})
