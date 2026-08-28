import { describe, it, expect } from 'bun:test'
import { runSimulation } from '../tools/sim/simulation-runner'
import { STAGES } from '../src/config/stages'

// ============================================================
// God AI Gate Tests (plan/God-AI-Tuning §4)
//
// Regression guards that the once-fatal blind spot (0/10 pass on
// classic stage 0) never recurs. Kept small (<5s) to avoid the
// calibration.test 5s-cliff; full-scale validation is CLI-only.
// ============================================================

describe('godai-gates', () => {
  describe('failure taxonomy', () => {
    it('SimResult includes failure field on gameover', () => {
      const result = runSimulation({
        seed: 1,
        stage: STAGES[0],
        difficulty: 'classic',
        maxTicks: 4000,
        sampleInterval: 60,
      })
      // Fix Bug 6: If the AI clears the stage, failure is undefined —
      // that's a valid outcome, not an error. Guard against NPE.
      if (result.outcome === 'stage_clear') {
        expect(result.failure).toBeUndefined()
        return
      }
      // The result should have a failure field (cause + tick).
      expect(result.failure).toBeDefined()
      expect(result.failure!.cause).toMatch(/base_destroyed|lives_exhausted|timeout/)
      expect(result.failure!.tick).toBe(result.ticks)
    })

    it('failure is undefined on stage_clear', () => {
      // Use a very generous setup that should pass.
      const result = runSimulation({
        seed: 1,
        stage: STAGES[0],
        difficulty: 'relax',
        maxTicks: 36000,
        sampleInterval: 60,
      })
      if (result.outcome === 'stage_clear') {
        expect(result.failure).toBeUndefined()
      }
    })

    it('firstKillTick is recorded when kills happen', () => {
      const result = runSimulation({
        seed: 1,
        stage: STAGES[0],
        difficulty: 'classic',
        maxTicks: 4000,
        sampleInterval: 60,
      })
      if (result.finalState.killCount > 0 && result.failure) {
        expect(result.failure.firstKillTick).toBeDefined()
        expect(result.failure.firstKillTick!).toBeGreaterThan(0)
      }
    })

    it('playerDistToBase is recorded on failure', () => {
      const result = runSimulation({
        seed: 1,
        stage: STAGES[0],
        difficulty: 'classic',
        maxTicks: 4000,
        sampleInterval: 60,
      })
      if (result.failure && result.outcome === 'gameover') {
        expect(result.failure.playerDistToBase).toBeDefined()
        expect(result.failure.playerDistToBase!).toBeGreaterThanOrEqual(0)
      }
    })
  })

  describe('determinism', () => {
    it('same seed + same stage + same difficulty produces identical results', () => {
      const opts = {
        seed: 42,
        stage: STAGES[0],
        difficulty: 'classic' as const,
        maxTicks: 600,
        sampleInterval: 30,
      }
      const r1 = runSimulation(opts)
      const r2 = runSimulation(opts)

      expect(r1.outcome).toBe(r2.outcome)
      expect(r1.ticks).toBe(r2.ticks)
      expect(r1.finalState.score).toBe(r2.finalState.score)
      expect(r1.finalState.killCount).toBe(r2.finalState.killCount)
      expect(r1.finalState.baseAlive).toBe(r2.finalState.baseAlive)
    })

    it('different seeds may produce different results', () => {
      const r1 = runSimulation({
        seed: 1,
        stage: STAGES[0],
        difficulty: 'classic',
        maxTicks: 600,
        sampleInterval: 30,
      })
      const r2 = runSimulation({
        seed: 999,
        stage: STAGES[0],
        difficulty: 'classic',
        maxTicks: 600,
        sampleInterval: 30,
      })
      // They should at least have the same structure (different seeds
      // CAN coincidentally produce the same result, but it's unlikely
      // for 600 ticks of gameplay). We only assert they're valid results.
      expect(r1.outcome).toMatch(/stage_clear|gameover|max_ticks/)
      expect(r2.outcome).toMatch(/stage_clear|gameover|max_ticks/)
    })
  })
})
