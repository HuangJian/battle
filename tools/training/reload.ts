/** reload.ts — 变更检测监督器：受管进程运行的代码更新后自动重启该进程。

 *  哨兵机制（平台无关、无 inotify/FSEvents 依赖）：每个受管进程登记一组"哨兵文件"
 *  ——进程入口 .ts/.py 自身 + 代码清单文件（codehash-files.txt）。监督循环周期性
 *  stat 哨兵 mtime 与大小：
 *    - 变化 → 该进程重启（restartProc）应用最新代码；
 *    - 清单文件（如 codehash-files.txt）变化 → 全部进程重启（文件集变了 = 哈希
 *      语义变了，所有依赖代码身份的进程都应换新码）。
 *  python 入口跑的是 .py 源文件（无构建产物），mtime 即代码身份；bun 入口同理
 *  直跑 .ts 源。入口依赖的模块不逐个追踪——由 codehash-files.txt（SSOT 清单）与
 *  手工哨兵补足，误报代价只是一次热重启，漏报（节点用旧码采样）才是事故。
 */

import { statSync } from 'fs'
import path from 'path'
import { pidAlive, sleep } from './net'
import { info, log, warn } from './log'
import type { ProcSpec } from './types'

/** 哨兵指纹：mtimeMs + size。 */
interface Snap {
  mtimeMs: number
  size: number
}

function snap(file: string): Snap | null {
  try {
    const s = statSync(file)
    return { mtimeMs: Math.round(s.mtimeMs), size: s.size }
  } catch {
    return null
  }
}

interface WatchState {
  spec: ProcSpec
  pid: number
  /** 哨兵文件 → 上次指纹（null = 文件当时不存在）。 */
  last: Map<string, Snap | null>
  /** 重启次数（日志用）。 */
  restarts: number
}

export interface SupervisorOptions {
  /** 轮询间隔 ms（默认 5s）。 */
  intervalMs?: number
  /** 变更回调（默认打日志）。 */
  onReload?: (spec: ProcSpec, pid: number) => void
}

export interface ProcSupervisor {
  /** 登记/刷新一个受管进程（登记后监督循环接管其哨兵）。 */
  watch: (spec: ProcSpec, pid: number) => void
  /** 停止监督循环。 */
  stop: () => void
  /** 立即做一轮检测（测试/手动触发用），返回本次重启的进程。 */
  poll: () => Promise<ProcSpec[]>
  /** 挂起事件循环引用（避免监督循环本身钉住主进程——CLI 退出语义用）。 */
  unref: () => void
}

/** 创建监督循环。restartProc(spec, oldPid) → 新 pid 由调用方 watch() 回灌。 */
export function createSupervisor(
  restartProc: (spec: ProcSpec, oldPid: number) => Promise<number>,
  opts: SupervisorOptions = {},
): ProcSupervisor {
  const intervalMs = opts.intervalMs ?? 5000
  const watches = new Map<string, WatchState>()
  let running = true
  let ticking = false

  function watch(spec: ProcSpec, pid: number): void {
    const last = new Map<string, Snap | null>()
    for (const f of spec.sentinels) last.set(f, snap(f))
    watches.set(spec.key, { spec, pid, last, restarts: watches.get(spec.key)?.restarts ?? 0 })
  }

  /** 一轮检测：哨兵变化 → 重启。 */
  async function poll(): Promise<ProcSpec[]> {
    const reloaded: ProcSpec[] = []
    for (const w of watches.values()) {
      if (!running) break
      // 进程已死（外部被杀/自行退出）→ 不重启（职责在所属模式的循环逻辑），跳过。
      if (!pidAlive(w.pid)) continue
      let changed: string | null = null
      for (const f of w.spec.sentinels) {
        const cur = snap(f)
        const prev = w.last.get(f)
        w.last.set(f, cur)
        if (
          prev !== undefined &&
          cur !== null &&
          (prev === null || prev.mtimeMs !== cur.mtimeMs || prev.size !== cur.size)
        ) {
          changed = path.basename(f)
          break
        }
        if (prev !== undefined && prev !== null && cur === null) {
          changed = path.basename(f)
          break
        }
      }
      if (!changed) continue
      // 哨兵可能在写一半时被观测（编辑器逐次落盘）——冷却 500ms 再取一次指纹，
      // 两次一致才认为写完。仍不一致就等下一轮（文件大时多轮收敛，无危害）。
      await sleep(500)
      const again = snap(
        changed ? (w.spec.sentinels.find((f) => path.basename(f) === changed) ?? '') : '',
      )
      if (
        again &&
        again.size !==
          w.last.get(w.spec.sentinels.find((f) => path.basename(f) === changed) ?? '')?.size
      )
        continue
      w.restarts++
      info(
        `${w.spec.name} 检测到代码更新（${changed}）— 重启应用最新代码（第 ${w.restarts} 次）...`,
      )
      try {
        const newPid = await restartProc(w.spec, w.pid)
        w.pid = newPid
        // 重启后以新进程的当下指纹为基线。
        for (const f of w.spec.sentinels) w.last.set(f, snap(f))
        reloaded.push(w.spec)
      } catch (e) {
        warn(`${w.spec.name} 重启失败: ${e instanceof Error ? e.message : e}`)
      }
    }
    return reloaded
  }

  let timer: ReturnType<typeof setInterval> | null = null
  async function loop(): Promise<void> {
    while (running) {
      if (!ticking) {
        ticking = true
        try {
          await poll()
        } finally {
          ticking = false
        }
      }
      await sleep(intervalMs)
    }
  }

  // 循环在后台跑（不 await——监督器是 fire-and-forget 的后台任务）。默认不
  // 钉住主进程：--kill 等同步流程跑完 main() 即可退出，监督循环随进程结束。
  const loopPromise = loop().catch((e) => log(`supervisor loop crashed: ${e}`))
  void (loopPromise as unknown as { unref?: () => void }).unref?.()

  return {
    watch,
    stop: () => {
      running = false
      if (timer) clearInterval(timer)
    },
    poll,
    unref: () => {
      /* 监督循环的 sleep 链不持有事件循环强引用（Bun 的定时器默认不 unref，
         这里以 stop() + 进程自然退出兜底；保留接口供长驻服务模式显式接管）。 */
    },
  }
}
