/**
 * worker-pool.ts — generic Bun Worker pool shared by all batch tools
 * (plan/refactor.agy.md §3.6: four independent dispatch/error/termination
 * loops collapsed into one implementation).
 *
 * Two shapes are provided:
 *
 *  - {@link WorkerPool}: N persistent workers, tasks dispatched one at a time,
 *    results re-ordered by task id before resolving — identical ordering to a
 *    serial for-loop, so downstream floating-point aggregation is stable.
 *  - {@link runChunkedWorkers}: one fresh worker per pre-split chunk; the
 *    worker aggregates its chunk and returns `{ results }` once. Used by the
 *    gate harnesses where each job set is small and worker startup cost is
 *    irrelevant.
 *
 * Determinism note (why parallel == serial): every task must be a pure
 * function of its payload (fresh World, own seeded RNG, zero shared state —
 * AGENTS §2.2/§2.3). Which thread runs it cannot change its outcome.
 */
import { cpus } from 'node:os'
import { execSync } from 'node:child_process'

/**
 * Physical core count. Hyper-thread "cores" share execution units and L1/L2;
 * measured on a 4c/8t i7-4770HQ, >physical−1 workers actively *hurt* (7w
 * 1.70x vs 3w 2.40x) because sibling threads evict each other's caches.
 */
export function physicalCores(): number {
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
 * the main thread and the rest of the system. Override: SIM_POOL_WORKERS.
 */
export function defaultWorkerCount(): number {
  const env = Number(process.env.SIM_POOL_WORKERS)
  if (Number.isInteger(env) && env >= 1) return env
  return Math.max(1, physicalCores() - 1)
}

/**
 * Gate-harness worker count (tests/score-gate-core.ts — the single shared
 * copy; the retired gate-core twin was deleted). Override: GATE_CORES.
 * Default tuned for THIS host: `navigator.hardwareConcurrency` reports 16
 * logical CPUs, but the gate pool is FASTEST at ~4 workers — beyond that,
 * extra workers contend and slow down (measured: 1→10.5s, 4→6.1s, 8→7.5s,
 * 16→10.3s for 700 classic sims; full 2100-sim gate: 4→27.9s vs 16→36s).
 */
export function gateCoreCount(): number {
  const env = Number(process.env.GATE_CORES)
  if (Number.isFinite(env) && env > 0) return Math.floor(env)
  return 4
}

/**
 * Round-robin job split into n chunks (gate harness fan-out). Shared copy of
 * the identical helper that used to live in both gate cores.
 */
export function splitRoundRobin<T>(jobs: T[], n: number): T[][] {
  const chunks: T[][] = Array.from({ length: n }, () => [])
  jobs.forEach((job, i) => chunks[i % n].push(job))
  return chunks
}

/**
 * Persistent pool of `size` workers running `TTask` → `TResult` jobs.
 * Results carry an `id` field indexing into the submitted task order.
 */
export class WorkerPool<TTask, TResult extends { id: number }> {
  private workers: Worker[] = []
  readonly size: number

  constructor(
    /** URL of the worker script (new URL('./x-worker.ts', import.meta.url).href). */
    private readonly workerUrl: string,
    size: number = defaultWorkerCount(),
    /** Label used in error messages (e.g. 'sim-worker'). */
    private readonly label = 'worker',
  ) {
    this.size = Math.max(1, size)
    for (let i = 0; i < this.size; i++) {
      this.workers.push(new Worker(this.workerUrl))
    }
  }

  /**
   * Run a batch of tasks across the pool. Resolves with results ordered by
   * task id (0..n-1) — identical ordering to a serial for-loop over `tasks`.
   */
  runBatch(tasks: TTask[], onProgress?: (done: number) => void): Promise<TResult[]> {
    if (tasks.length === 0) return Promise.resolve([])
    return new Promise((resolve, reject) => {
      const results: TResult[] = Array.from({ length: tasks.length })
      let nextTask = 0
      let done = 0

      const dispatch = (worker: Worker): void => {
        if (nextTask >= tasks.length) return
        const task = tasks[nextTask++]
        // NOTE: postMessage via property-assigned handlers is unreliable on
        // some Bun versions — addEventListener is the validated-safe path.
        worker.postMessage(task)
      }

      for (const worker of this.workers) {
        worker.addEventListener('message', (event: MessageEvent<TResult>) => {
          const res = event.data
          results[res.id] = res
          done++
          onProgress?.(done)
          if (done === tasks.length) {
            resolve(results)
          } else {
            dispatch(worker)
          }
        })
        worker.addEventListener('error', (err) => {
          reject(new Error(`${this.label} failed: ${(err as ErrorEvent)?.message ?? err}`))
        })
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

/**
 * Chunk-per-worker runner: each payload is posted to its own short-lived
 * worker which resolves with a single `{ results }` message holding its
 * chunk's results; the resolved arrays are concatenated into one flat
 * `TResult[]` (no `.flat()` on unknown generics). Wrap your chunk at the
 * call site to match the worker's payload contract (e.g. `{ jobs: chunk }`).
 */
export async function runChunkedWorkers<TPayload, TResult>(
  workerUrl: string,
  payloads: TPayload[],
): Promise<TResult[]> {
  const chunks = await Promise.all(
    payloads.map(
      (payload) =>
        new Promise<TResult[]>((resolve, reject) => {
          const w = new Worker(workerUrl)
          w.addEventListener('message', (ev: MessageEvent) => {
            resolve((ev.data as { results: TResult[] }).results)
            w.terminate()
          })
          w.addEventListener('error', (err: unknown) => {
            w.terminate()
            reject(err)
          })
          w.postMessage(payload)
        }),
    ),
  )
  return ([] as TResult[]).concat(...chunks)
}
