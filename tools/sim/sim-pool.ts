/**
 * sim-pool.ts — Bun Worker pool for parallel headless simulations.
 *
 * Thin wrapper over the shared generic pool in tools/lib/worker-pool.ts
 * (§3.6). Kept as its own module so every diag/eval script can keep importing
 * `SimWorkerPool` / `defaultWorkerCount` from here.
 *
 * Determinism contract (why parallel == serial, byte for byte):
 *   Each simulation is a pure function of (seed, stage, difficulty, params,
 *   maxTicks) — fresh World, own seeded RNG, zero shared state (AGENTS
 *   §2.2/§2.3). Which thread runs it, and in which order, cannot change its
 *   outcome.
 */
import { WorkerPool, defaultWorkerCount } from '../lib/worker-pool'
import type { SimTask, SimTaskResult } from './sim-worker'

export { defaultWorkerCount }

const WORKER_URL = new URL('./sim-worker.ts', import.meta.url).href

export class SimWorkerPool extends WorkerPool<SimTask, SimTaskResult> {
  constructor(size: number = defaultWorkerCount()) {
    super(WORKER_URL, size, 'sim-worker')
  }
}
