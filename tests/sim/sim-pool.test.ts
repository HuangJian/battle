import { describe, it, expect } from 'bun:test'
import { AdaptiveSimWorkerPool, SimWorkerPool } from '../../tools/sim/sim-pool'
import { STAGES } from '../../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import type { SimTask } from '../../tools/sim/sim-worker'

/**
 * Ledger-resume sub-batches keep the original global task ids (e.g. 6,7 after
 * 0–5 were settled). AdaptiveSimWorkerPool sizes `results` by batch length but
 * used to write `results[res.id]` — stretching the array and leaving holes.
 * m1-eval then does `for (const r of sub) r.id` and crashes on undefined
 * (TypeError: undefined is not an object (evaluating 'r.id')).
 *
 * Contract: the returned array is dense (length === tasks.length), ordered by
 * batch slot, each entry still carrying the original task id.
 */
function resumeTasks(): SimTask[] {
  const stage = STAGES[0]!
  return [6, 7].map((id) => ({
    id,
    seed: id - 5, // 1, 2
    stage,
    difficulty: 'hard',
    params: DEFAULT_GOD_AI_PARAMS,
    maxTicks: 40,
    stageIndex: 0,
    policy: 'god',
    telemetry: false,
  }))
}

describe('AdaptiveSimWorkerPool resume sub-batch indexing', () => {
  it('non-zero-based task ids stay dense (ledger resume 6/7)', async () => {
    const tasks = resumeTasks()
    const pool = new AdaptiveSimWorkerPool(2, 1)
    try {
      const sub = await pool.runAdaptive(tasks, undefined, { fixed: true })
      expect(sub.length).toBe(tasks.length)
      // Mirror m1-eval's settle loop — this is the crash site before the fix.
      for (const r of sub) {
        expect(r).toBeDefined()
        expect(r.id).toBeGreaterThanOrEqual(6)
      }
      const ids = sub.map((r) => r.id).sort((a, b) => a - b)
      expect(ids).toEqual([6, 7])
      // No holes: every slot is a real result object.
      expect(sub.every((r) => r != null && typeof r.id === 'number')).toBe(true)
    } finally {
      // Adaptive pool has no public dispose; terminate via a 0-task run is
      // unnecessary — workers exit when the promise resolves. On failure,
      // leave them; bun test teardown kills the process.
    }
  }, 30_000)
})

/**
 * Same contract on the non-adaptive WorkerPool: results are dense and ordered
 * by batch slot even when task ids are not 0..n-1 (ledger-resume subsets).
 * Writing results[res.id] into a batch-length array left holes and made
 * callers crash on `for (const r of sub) r.id`.
 */
describe('WorkerPool.runBatch resume sub-batch indexing', () => {
  it('non-zero-based task ids stay dense (ledger resume 6/7)', async () => {
    const tasks = resumeTasks()
    const pool = new SimWorkerPool(2)
    try {
      const sub = await pool.runBatch(tasks)
      expect(sub.length).toBe(tasks.length)
      for (const r of sub) {
        expect(r).toBeDefined()
        expect(r.id).toBeGreaterThanOrEqual(6)
      }
      const ids = sub.map((r) => r.id).sort((a, b) => a - b)
      expect(ids).toEqual([6, 7])
      expect(sub.every((r) => r != null && typeof r.id === 'number')).toBe(true)
    } finally {
      pool.terminate()
    }
  }, 30_000)
})
