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
import { cpus } from 'node:os'
import { exec } from 'node:child_process'
import { WorkerPool, defaultWorkerCount, physicalCores } from '../lib/worker-pool'
import type { SimTask, SimTaskResult } from './sim-worker'

export { defaultWorkerCount, physicalCores }

const WORKER_URL = new URL('./sim-worker.ts', import.meta.url).href

export class SimWorkerPool extends WorkerPool<SimTask, SimTaskResult> {
  constructor(size: number = defaultWorkerCount()) {
    super(WORKER_URL, size, 'sim-worker')
  }
}

// ============================================================
// CPU load sampling (cross-platform)
// ============================================================

/**
 * Sample aggregate system CPU load as a 0–100 percentage, or -1 if it cannot
 * be determined. Unix derives it from a delta of `os.cpus().times` (cheap,
 * no subprocess); Windows shells out to wmic, falling back to PowerShell's
 * `Get-CimInstance Win32_Processor` (wmic is deprecated/removed on Win11).
 */
let lastCpu: { idle: number; total: number } | null = null
function cpuTimes(): { idle: number; total: number } {
  let idle = 0
  let total = 0
  for (const c of cpus()) {
    idle += c.times.idle
    total += c.times.user + c.times.nice + c.times.sys + c.times.irq + c.times.idle
  }
  return { idle, total }
}
function cpuLoadUnix(): number {
  const cur = cpuTimes()
  const prev = lastCpu ?? cur
  lastCpu = cur
  const idleDiff = cur.idle - prev.idle
  const totalDiff = cur.total - prev.total
  if (totalDiff <= 0) return -1
  return Math.round((1 - idleDiff / totalDiff) * 100)
}
function cpuLoadWin32(): Promise<number> {
  return new Promise((resolve) => {
    exec('wmic cpu get LoadPercentage', { encoding: 'utf8', timeout: 3000 }, (err, out) => {
      if (!err && out) {
        const vals = out
          .split(/\r?\n/)
          .map((s) => parseInt(s.trim(), 10))
          .filter((n) => Number.isInteger(n) && n >= 0 && n <= 100)
        if (vals.length) {
          resolve(Math.round(vals.reduce((a, b) => a + b, 0) / vals.length))
          return
        }
      }
      exec(
        'pwsh -NoProfile -Command "(Get-CimInstance Win32_Processor | ForEach-Object { $_.LoadPercentage } | Where-Object { $_ -ne $null } | Measure-Object -Average).Average"',
        { encoding: 'utf8', timeout: 6000 },
        (err2, out2) => {
          if (!err2 && out2) {
            const n = parseInt(out2.trim(), 10)
            if (Number.isInteger(n) && n >= 0 && n <= 100) {
              resolve(n)
              return
            }
          }
          resolve(-1)
        },
      )
    })
  })
}
export function sampleCpuLoad(): Promise<number> {
  if (process.platform === 'win32') return cpuLoadWin32()
  return Promise.resolve(cpuLoadUnix())
}

// ============================================================
// AdaptiveSimWorkerPool — concurrency follows system CPU load
// ============================================================

export interface AdaptivePoolOptions {
  /** Load above this fraction → reduce one worker (default 0.9). */
  high?: number
  /** Load below this fraction → add one worker (default 0.85). */
  low?: number
  /** Sampling cadence in ms (default 2500). */
  sampleMs?: number
  /** Starting concurrency; clamped to [min, max] (default = max). */
  initial?: number
  /**
   * Fixed concurrency (default false): skip the CPU-load sampler entirely.
   * For CPU-bound batch runs (e.g. intent-NN evals where every core is pinned
   * at ~100%), the adaptive rule "load > 90% → −1 worker" self-throttles to 1
   * worker and starves the run. `fixed` keeps desired = max for the whole run.
   */
  fixed?: boolean
}

/**
 * Like {@link SimWorkerPool} but the live worker count tracks system CPU load:
 *
 *   load > high (90%)  →  concurrency −1
 *   load < low  (85%)  →  concurrency +1
 *   max = physical cores (never oversubscribe the machine)
 *   min = 1 (never stall the run)
 *
 * Resizing is *drain-based*: growing spawns extra idle workers that immediately
 * pull the next pending task; shrinking never kills an in-flight simulation —
 * an over-quota worker is terminated only after it returns its current result.
 */
export class AdaptiveSimWorkerPool {
  private max: number
  private min: number
  private desired: number
  private active = 0
  private workers: Worker[] = []
  private idle: Worker[] = []
  private tasks: SimTask[] = []
  private results: SimTaskResult[] = []
  private nextTask = 0
  private done = 0
  private total = 0
  private onProgress?: (done: number, total: number) => void
  private resolveFn?: (r: SimTaskResult[]) => void
  private rejectFn?: (e: Error) => void
  private sampler?: ReturnType<typeof setInterval>
  private sampling = false
  private failed = false
  private onAdjust?: (desired: number, load: number) => void

  constructor(max: number = physicalCores(), min = 1) {
    this.max = Math.max(1, max)
    this.min = Math.max(1, Math.min(min, this.max))
    this.desired = this.max
  }

  /** Hook fired whenever the target concurrency changes (for logging). */
  setAdjustHook(fn: (desired: number, load: number) => void): void {
    this.onAdjust = fn
  }

  private spawn(): Worker {
    const w = new Worker(WORKER_URL)
    this.workers.push(w)
    this.active++
    w.onmessage = (e: MessageEvent<SimTaskResult>) => this.onResult(w, e.data)
    w.onerror = (err) => this.fail(err)
    return w
  }

  private fail(err: Error | Event): void {
    if (this.failed) return
    this.failed = true
    if (this.sampler) clearInterval(this.sampler)
    this.terminateAll()
    this.rejectFn?.(err instanceof Error ? err : new Error(String(err)))
  }

  private terminateWorker(w: Worker): void {
    w.terminate()
    this.active--
    const i = this.workers.indexOf(w)
    if (i >= 0) this.workers.splice(i, 1)
  }

  private terminateAll(): void {
    for (const w of this.workers) w.terminate()
    this.workers = []
    this.idle = []
    this.active = 0
  }

  private dispatch(w: Worker): void {
    if (this.nextTask < this.total) {
      w.postMessage(this.tasks[this.nextTask++])
      return
    }
    // No more work. Shrink if over quota, otherwise park as idle.
    if (this.active > this.desired) {
      this.terminateWorker(w)
      return
    }
    this.idle.push(w)
  }

  private onResult(w: Worker, res: SimTaskResult): void {
    if (this.failed) return
    this.results[res.id] = res
    this.done++
    this.onProgress?.(this.done, this.total)
    if (this.done >= this.total) {
      this.finish()
      return
    }
    this.dispatch(w)
  }

  private finish(): void {
    if (this.sampler) clearInterval(this.sampler)
    this.terminateAll()
    this.resolveFn?.(this.results)
  }

  /**
   * Run all tasks with adaptive concurrency. Resolves with results ordered by
   * task id (same ordering as a serial loop).
   */
  runAdaptive(
    tasks: SimTask[],
    onProgress?: (done: number, total: number) => void,
    opts: AdaptivePoolOptions = {},
  ): Promise<SimTaskResult[]> {
    if (tasks.length === 0) return Promise.resolve([])
    const high = (opts.high ?? 0.9) * 100
    const low = (opts.low ?? 0.85) * 100
    const sampleMs = opts.sampleMs ?? 2500
    this.tasks = tasks
    this.total = tasks.length
    this.results = Array.from({ length: tasks.length })
    this.nextTask = 0
    this.done = 0
    this.onProgress = onProgress
    this.desired = Math.max(this.min, Math.min(opts.initial ?? this.max, this.max))

    return new Promise<SimTaskResult[]>((resolve, reject) => {
      this.resolveFn = resolve
      this.rejectFn = reject

      // Prime with up to `desired` workers (never more than there is work, so
      // a tiny run doesn't spawn idle workers that immediately park).
      const prime = Math.min(this.desired, tasks.length)
      for (let i = 0; i < prime; i++) {
        const w = this.spawn()
        this.dispatch(w)
      }

      // Adaptive sampler: nudge `desired` from system CPU load, then resize.
      // Fixed mode skips the sampler — desired stays at max for the whole run
      // (CPU-bound batch: 100% load is the normal state, not overload).
      if (opts.fixed) {
        return
      }
      this.sampler = setInterval(() => {
        if (this.sampling || this.failed) return
        this.sampling = true
        sampleCpuLoad()
          .then((load) => {
            this.sampling = false
            if (load < 0) return
            if (load > high) this.desired = Math.max(this.min, this.desired - 1)
            else if (load < low) this.desired = Math.min(this.max, this.desired + 1)
            // Grow: spawn extra workers that immediately pull pending work.
            while (this.desired > this.active && this.nextTask < this.total) {
              const w = this.spawn()
              this.dispatch(w)
            }
            // Shrink idle excess promptly (busy over-quota workers drain on completion).
            while (this.idle.length > 0 && this.active > this.desired) {
              this.terminateWorker(this.idle.pop()!)
            }
            this.onAdjust?.(this.desired, load)
          })
          .catch(() => {
            this.sampling = false
          })
      }, sampleMs)
    })
  }
}
