/**
 * admission-queue.ts — 节点准入的**有界排队**（纯策略 + 注入时钟，sampler-agent 与单测共享）。
 *
 * 背景（2026-11-01 503 busy 排查，`docs/nn/runtime-opt.md` §39）：agent 的准入是
 * `activeWorkers >= workers` **立刻** 503，而协调器每条腿（rollout / eval）、每门课各自按
 * `concurrency` 开满 per-node 线程 ⇒ 需求常是池的 2–4 倍（24h 日志实测 23.95 万条 busy）。
 * 节点没有队列，就只能让协调器用「拒收 + 退避重投」表达排队——把调度代价推给最不了解
 * 节点状态的一方，且每次重投都再撞一次同一堵墙。
 *
 * 本模块只做一件事：池满时**排队等到空槽**（上限 `waitMs`），队列超过 `queueMax` 或等到
 * 超时才回 busy。判据全在这里，agent 只做接线（`acquire()` / `release()`）。
 *
 * 两条设计约束：
 *   · **`waitMs = 0` 必须逐字节回到旧行为**（立即 busy）——它是回退档，运维不用改代码。
 *   · 时钟是参数：等待是这里唯一的时间语义，单测靠手动推进，不 sleep（见
 *     `tests/agent/admission-queue.test.ts`）。
 */

/** 缺省等待上界：远小于协调器单局请求的超时（`taskFetchTimeoutSec` 50s 级），
 *  又不至于把一次「池刚满」放大成一次失败重投。 */
export const ADMISSION_WAIT_MS = 12_000

/** 等待队列上限：超过它立即 busy（保护 agent 不被一次波次塞爆内存/连接）。 */
export const ADMISSION_QUEUE_MAX = 32

export type AdmitResult = 'admitted' | 'busy'

/** 只暴露 `schedule`：测试用假时钟，生产用 `setTimeout`（见 `defaultClock`）。 */
export interface AdmissionClock {
  schedule(ms: number, fn: () => void): () => void
}

export const defaultClock: AdmissionClock = {
  schedule(ms, fn) {
    const t = setTimeout(fn, ms)
    // Bun/Node 的定时器都带 unref（不拖着进程退出）；类型上按可选处理。
    ;(t as unknown as { unref?: () => void }).unref?.()
    return () => clearTimeout(t)
  },
}

export interface AdmissionCounters {
  /** 直接拿到槽位的请求数（一次 `acquire` 成功计一次）。 */
  admitted: number
  /** `waitMs ≤ 0` 或容量为 0 ⇒ 立即 busy（旧行为）。 */
  rejected: number
  /** 等待队列已满 ⇒ 立即 busy（背压，不是超时）。 */
  queueFull: number
  /** 排到了却等过 `waitMs` ⇒ busy（协调器照旧回队重投）。 */
  timedOut: number
  /** 历史最大等待人数（可观测性：一眼看出池被压了多久）。 */
  peakWaiting: number
}

export interface Admission {
  /**
   * 排到一个槽位。返回 `admitted` 时**调用方拥有该槽**，任务结束必须 `release()` 一次；
   * 返回 `busy` 时什么都没占用（不需要 release）。
   *
   * `signal` 用于客户端断开时把排队者摘掉（否则一个断开的连接会一直占着队位到超时）。
   */
  acquire(signal?: AbortSignal): Promise<AdmitResult>
  /** 归还一个槽位并唤醒队首（多归还就多唤醒，直到 `capacity`）。 */
  release(): void
  active(): number
  waiting(): number
  counters(): AdmissionCounters
}

interface Waiter {
  resolve: (r: AdmitResult) => void
  cancel: () => void
  detach: () => void
  done: boolean
}

export function createAdmission(opts: {
  capacity: () => number
  queueMax?: number
  waitMs?: number
  clock?: AdmissionClock
}): Admission {
  const capacity = opts.capacity
  const queueMax = Math.max(0, opts.queueMax ?? ADMISSION_QUEUE_MAX)
  const waitMs = Math.max(0, opts.waitMs ?? ADMISSION_WAIT_MS)
  const clock = opts.clock ?? defaultClock

  let active = 0
  const waiters: Waiter[] = []
  const counters: AdmissionCounters = {
    admitted: 0,
    rejected: 0,
    queueFull: 0,
    timedOut: 0,
    peakWaiting: 0,
  }

  const free = (): number => Math.max(0, capacity()) - active

  function admit(): AdmitResult | null {
    if (free() <= 0) return null
    active++
    counters.admitted++
    return 'admitted'
  }

  function drop(w: Waiter): void {
    if (w.done) return
    w.done = true
    const i = waiters.indexOf(w)
    if (i >= 0) waiters.splice(i, 1)
    w.cancel()
    w.detach()
  }

  function drain(): void {
    while (waiters.length > 0 && free() > 0) {
      const w = waiters[0]!
      if (w.done) {
        waiters.shift()
        continue
      }
      waiters.shift()
      w.done = true
      w.cancel()
      w.detach()
      active++
      counters.admitted++
      w.resolve('admitted')
    }
  }

  return {
    acquire(signal?: AbortSignal): Promise<AdmitResult> {
      // 有空槽就先拿（`waitMs = 0` 只影响「满了怎么办」，不影响准入本身）。
      const now = admit()
      if (now) return Promise.resolve(now)
      // 池满且不排队（回退档）⇒ 逐字节旧行为：立刻 busy。
      if (waitMs <= 0 || capacity() <= 0) {
        counters.rejected++
        return Promise.resolve('busy')
      }
      if (signal?.aborted) {
        counters.rejected++
        return Promise.resolve('busy')
      }
      if (waiters.length >= queueMax) {
        counters.queueFull++
        return Promise.resolve('busy')
      }
      return new Promise<AdmitResult>((resolve) => {
        const w: Waiter = {
          resolve,
          cancel: () => {},
          detach: () => {},
          done: false,
        }
        const settle = (): void => {
          drop(w)
          counters.timedOut++
          resolve('busy')
        }
        w.cancel = clock.schedule(waitMs, settle)
        const onAbort = (): void => {
          drop(w)
          counters.rejected++
          resolve('busy')
        }
        if (signal) {
          signal.addEventListener('abort', onAbort, { once: true })
          w.detach = () => signal.removeEventListener('abort', onAbort)
        }
        waiters.push(w)
        counters.peakWaiting = Math.max(counters.peakWaiting, waiters.length)
      })
    },

    release(): void {
      if (active > 0) active--
      drain()
    },

    active: () => active,
    waiting: () => waiters.length,
    counters: () => ({ ...counters }),
  }
}
