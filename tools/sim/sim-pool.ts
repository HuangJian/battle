/**
 * sim-pool.ts — Bun Worker pool for parallel headless simulations.
 *
 * Determinism contract (why parallel == serial, byte for byte):
 *   Each simulation is a pure function of (seed, stage, difficulty, params,
 *   maxTicks) — fresh World, own seeded RNG, zero shared state (AGENTS
 *   §2.2/§2.3). Which thread runs it, and in which order, cannot change its
 *   outcome. The pool re-orders results by task id before returning, so any
 *   downstream aggregation consumes records in exactly the serial order and
 *   floating-point summation order is preserved.
 *
 * Sizing: defaults to hw.ncpu − 1 workers (leave one core for the rest of
 * the system), overridable via SIM_POOL_WORKERS env or the constructor.
 */
import { cpus } from 'node:os'
import { execSync } from 'node:child_process'
import type { SimTask, SimTaskResult } from './sim-worker'

/**
 * Physical core count. Hyper-thread "cores" share execution units and L1/L2;
 * measured on a 4c/8t i7-4770HQ, >physical−1 workers actively *hurt* (7w
 * 1.70x vs 3w 2.40x) because sibling threads evict each other's caches.
 */
function physicalCores(): number {
  if (process.platform === 'darwin') {
    try {
      const n = parseInt(execSync('sysctl -n hw.physicalcpu', { encoding: 'utf8' }).trim(), 10)
      if (Number.isInteger(n) && n >= 1) return n
    } catch {
      // fall through to logical count
    }
  }
  return cpus().length
}

/**
 * Default worker count: physical cores minus one — leave one full core for
 * the main thread (CMA-ES bookkeeping) and the rest of the system, so a
 * long tuning run doesn't make the machine unusable. Override: SIM_POOL_WORKERS.
 */
export function defaultWorkerCount(): number {
  const env = Number(process.env.SIM_POOL_WORKERS)
  if (Number.isInteger(env) && env >= 1) return env
  return Math.max(1, physicalCores() - 1)
}

const WORKER_URL = new URL('./sim-worker.ts', import.meta.url).href

export class SimWorkerPool {
  private workers: Worker[] = []
  readonly size: number

  constructor(size: number = defaultWorkerCount()) {
    this.size = Math.max(1, size)
    for (let i = 0; i < this.size; i++) {
      this.workers.push(new Worker(WORKER_URL))
    }
  }

  /**
   * Run a batch of tasks across the pool. Resolves with results ordered by
   * task id (0..n-1) — identical ordering to a serial for-loop over `tasks`.
   */
  runBatch(tasks: SimTask[]): Promise<SimTaskResult[]> {
    if (tasks.length === 0) return Promise.resolve([])
    return new Promise((resolve, reject) => {
      const results: SimTaskResult[] = Array.from({ length: tasks.length })
      let nextTask = 0
      let done = 0

      const dispatch = (worker: Worker): void => {
        if (nextTask >= tasks.length) return
        const task = tasks[nextTask++]
        worker.postMessage(task)
      }

      for (const worker of this.workers) {
        worker.onmessage = (event: MessageEvent<SimTaskResult>) => {
          const res = event.data
          results[res.id] = res
          done++
          if (done === tasks.length) {
            resolve(results)
          } else {
            dispatch(worker)
          }
        }
        worker.onerror = (err) => {
          reject(new Error(`sim-worker failed: ${err.message ?? err}`))
        }
      }

      // Prime every worker with an initial task.
      for (const worker of this.workers) dispatch(worker)
    })
  }

  /** Terminate all workers. Call once when the batch workload is finished. */
  terminate(): void {
    for (const worker of this.workers) worker.terminate()
    this.workers = []
  }
}
