/**
 * auto-restart.ts — 受管组件意外退出后的**自动重启**（2026-10-20 hub 崩溃事故）。
 *
 * 事故链：训练机**提交限额**（commit limit 37.9GB）被击穿 —— Windows System 日志
 * Event 2004「low virtual memory condition」@ 14:44:24（explorer.exe 14.5GB / Code.exe
 * 7.1GB / vmmemWSL 1.7GB 三家吃满）⇒ 共享 hub-server 进程被**外部**终止（无 traceback、
 * 无 stderr 输出、OS 无 Application Error 事件 ⇒ 不是自身异常退出）⇒ exit-watchdog
 * 4s 后记下「意外退出」，但**不重启**（它的文案就是「用控制台「启动」恢复」）⇒
 * hub 实际停了 **1 小时 29 分**（14:44:22 最后一条访问日志 → 16:13:36 恢复应答），
 * 期间在飞 job 随内存 job 表一起消失（重启后查该 job = 404）。
 *
 * 本模块补上那一环：**组件死了就自己爬起来**，而不是等人点按钮。
 *
 * ★ 覆盖范围 = **全部受管组件**（selfNode / hubServer / cloudflared / localWorker /
 *   trainingLoop）。用户口径（2026-10-20）：「trainingLoop 和 cloudflared 也需要自动重启」。
 *   关键不在「哪些组件」，而在**退出性质**：
 *     - **设计内停车**（门判决 gate_verdict / circuit_break / ALL DONE）⇒ **不重启**——
 *       那是判决，不是崩溃；悄悄续跑会推翻人的判死决定（§385）。
 *     - **意外退出**（崩溃 / 被外部杀死）⇒ 重启。
 *   「操作员手动停过」由**账本清账**保护（stop 会 clearAnyComponent ⇒ 条目消失 ⇒ 不复活），
 *   不靠时间窗口猜意图。
 *
 * 四道闸（任一不满足 ⇒ **不重启**，理由写进日志，便于事后判读）：
 *   ① 非设计内停车（planned 非空 ⇒ 是判决，不是崩溃）；
 *   ② **宽限后账本条目仍在** —— 操作员手动「停止」会清账（clearAnyComponent），
 *      条目消失 ⇒ 尊重操作员，不复活（意图是动作，账本是动作的果）；
 *   ③ 组件仍不健康（已被别人拉起来就别抢；健康探针与看护器判死用的是同一份
 *      `restartSpecFor(...).healthy()`，口径一致）；
 *   ④ 频次闸：窗口内同组件 ≤ AUTO_RESTART_BUDGET 次（防崩溃循环；超闸只报警不动手）。
 *
 * 启动动作**复用操作员的「启动」路径**（`startComponent`）——不是另写一套：单实例锁
 * 检查（trainer 的 run_cluster 锁）、端口回收、隧道 URL 回写、trainer 重启后的云端
 * 停机态复位（markCloudHaltRecovered）全在那条路径上，抄一份必然漂。
 */

import { warn } from '../core/log'
import { entryForCourse, loadRegistry, scopeOf } from '../core/registry'
import type { Component } from '../core/types'
import { startComponent } from './actions/start'
import { restartSpecFor } from './actions/restart'

/** 纳入自动重启的受管组件（全部共享单例；课程状态由「设计内停车」判据隔离）。 */
export const AUTO_RESTART_KEYS: readonly Component[] = [
  'selfNode',
  'hubServer',
  'cloudflared',
  'localWorker',
  'trainingLoop',
]

/** 宽限（ms）：等操作员的手动动作先落地（stop 清账 / start 占端口）。 */
export const AUTO_RESTART_GRACE_MS = 12_000

/** 频次闸：窗口内同组件最多自动重启几次。 */
export const AUTO_RESTART_BUDGET = 3

/** 频次闸窗口（ms）。 */
export const AUTO_RESTART_WINDOW_MS = 10 * 60_000

/** 单次拉起的尝试次数（瞬时失败重试：busy 互斥 / 端口未释放 / 锁未清）。 */
export const AUTO_RESTART_ATTEMPTS = 3

/** 两次尝试之间的间隔（ms）。 */
export const AUTO_RESTART_RETRY_GAP_MS = 8_000

/** 决策结果（reason 永远有值 —— 拒绝也要说清为什么，便于事后判读）。 */
export interface AutoRestartDecision {
  restart: boolean
  reason: string
}

/** 决策输入（全部可注入 ⇒ 纯函数可单测）。 */
export interface AutoRestartInput {
  /** 组件键。 */
  key: Component
  /** 设计内停车原因（非空 ⇒ 不是崩溃，不重启）。 */
  planned: string | null
  /** 宽限后账本条目是否仍在（操作员 stop 会清账 ⇒ false ⇒ 尊重操作员）。 */
  entryRegistered: boolean
  /** 组件此刻是否已健康（已被别人拉起 ⇒ 不抢）。 */
  healthy: boolean
  /** 窗口内已自动重启次数。 */
  restartsInWindow: number
  /** 同键是否已有重启在途（防并发重复拉起）。 */
  inFlight: boolean
}

/** 纯策略函数：该不该自动重启 + 为什么。 */
export function decideAutoRestart(i: AutoRestartInput): AutoRestartDecision {
  if (!AUTO_RESTART_KEYS.includes(i.key)) {
    return { restart: false, reason: '非受管组件（未纳入自动重启）' }
  }
  if (i.planned) {
    return { restart: false, reason: `设计内停车（${i.planned}）——不自动续跑` }
  }
  if (i.inFlight) return { restart: false, reason: '已有重启在途' }
  if (!i.entryRegistered) {
    return { restart: false, reason: '账本条目已清（操作员停过 / 已被接管）——尊重操作员' }
  }
  if (i.healthy) return { restart: false, reason: '组件已恢复应答——不抢' }
  if (i.restartsInWindow >= AUTO_RESTART_BUDGET) {
    return {
      restart: false,
      reason:
        `频次闸：${AUTO_RESTART_WINDOW_MS / 60000} 分钟内已重启 ${i.restartsInWindow} 次 ` +
        `（上限 ${AUTO_RESTART_BUDGET}）——只报警不自动拉起，请人工排查根因`,
    }
  }
  return { restart: true, reason: '意外退出且组件仍不健康——自动拉起' }
}

/** 组件健康检查（与看护器判死同一份探针：restartSpecFor(...).healthy()）。 */
async function healthyOf(key: Component): Promise<boolean> {
  try {
    const spec = restartSpecFor(key, '')
    if (!spec) return false // 没有可重建的 spec = 不可用（也起不来）
    return (await spec.healthy()) === true
  } catch {
    return false
  }
}

/** 拉起组件（复用操作员的「启动」路径 ⇒ 锁检查/端口回收/URL 回写/停机态复位同源）。 */
async function restartOf(key: Component): Promise<void> {
  const res = await startComponent(key, { course: '' })
  if (!res.ok) throw new Error(res.message)
}

/** 频次闸计数（key → 最近若干次重启时间戳）。 */
const _restarts = new Map<Component, number[]>()

/** 在途重启（key → true）。模块级：单 console 进程内有效。 */
const _inFlight = new Set<Component>()

/** 窗口内的重启次数（可注入 now 便于测试）。 */
export function restartsInWindow(key: Component, now = Date.now()): number {
  const list = (_restarts.get(key) ?? []).filter((t) => now - t < AUTO_RESTART_WINDOW_MS)
  _restarts.set(key, list)
  return list.length
}

/** 自动重启的可注入依赖（测试用；默认走真实实现）。 */
export interface AutoRestartIO {
  now?: () => number
  sleep?: (ms: number) => Promise<void>
  /** 账本里该组件的条目是否仍在（操作员 stop ⇒ 已清）。 */
  entryRegistered?: (key: Component) => boolean
  healthy?: (key: Component) => Promise<boolean>
  restart?: (key: Component) => Promise<void>
  warnFn?: (text: string) => void
}

function entryStillRegistered(key: Component): boolean {
  const reg = loadRegistry()
  const ent = entryForCourse(reg, key, scopeOf(key, ''))
  return !!ent && typeof ent.pid === 'number'
}

/**
 * 自动重启一次（含宽限 + 四道闸 + 有限重试）。返回是否真的拉起了。
 *
 * 宽限期后再复查账本与健康：操作员手动「停止」会清账（clearAnyComponent）⇒
 * 条目消失 ⇒ 不复活；这比「靠时间窗口猜操作员意图」可靠（意图是动作，账本是动作的果）。
 */
export async function attemptAutoRestart(key: Component, io: AutoRestartIO = {}): Promise<boolean> {
  const now = io.now ?? (() => Date.now())
  const sleep = io.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)))
  const warnFn = io.warnFn ?? warn
  if (!AUTO_RESTART_KEYS.includes(key)) return false
  if (_inFlight.has(key)) return false
  _inFlight.add(key)
  try {
    await sleep(AUTO_RESTART_GRACE_MS)
    const decision = decideAutoRestart({
      key,
      planned: null,
      entryRegistered: (io.entryRegistered ?? entryStillRegistered)(key),
      healthy: await (io.healthy ?? healthyOf)(key),
      restartsInWindow: restartsInWindow(key, now()),
      inFlight: false,
    })
    if (!decision.restart) {
      warnFn(`[console] ${key} 不自动重启：${decision.reason}`)
      return false
    }
    warnFn(
      `[console] ${key} 意外退出 → 自动重启（宽限 ${AUTO_RESTART_GRACE_MS / 1000}s 后，` +
        `最多试 ${AUTO_RESTART_ATTEMPTS} 次）…`,
    )
    // 有限重试：busy 互斥 / 端口未释放 / 单实例锁未清 都是瞬时的，重试比放弃更划算；
    // 每次重试都重新过一遍闸（期间可能已被别人拉起 ⇒ 立即停手）。
    for (let attempt = 1; attempt <= AUTO_RESTART_ATTEMPTS; attempt++) {
      if (attempt > 1) {
        await sleep(AUTO_RESTART_RETRY_GAP_MS)
        if (await (io.healthy ?? healthyOf)(key)) {
          warnFn(`[console] ${key} 已恢复应答——停止重试`)
          return true
        }
        if (!(io.entryRegistered ?? entryStillRegistered)(key)) {
          warnFn(`[console] ${key} 账本条目已清（操作员停过）——停止重试`)
          return false
        }
      }
      try {
        await (io.restart ?? restartOf)(key)
        const t = now()
        _restarts.set(key, [
          ...(_restarts.get(key) ?? []).filter((x) => t - x < AUTO_RESTART_WINDOW_MS),
          t,
        ])
        warnFn(`[console] ${key} 自动重启完成（第 ${attempt} 次尝试）`)
        return true
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        if (attempt < AUTO_RESTART_ATTEMPTS) {
          warnFn(
            `[console] ${key} 自动重启失败（第 ${attempt} 次）：${msg}——${AUTO_RESTART_RETRY_GAP_MS / 1000}s 后重试`,
          )
        } else {
          warnFn(
            `[console] ${key} 自动重启失败（已试 ${AUTO_RESTART_ATTEMPTS} 次）：${msg}——需人工介入`,
          )
        }
      }
    }
    return false
  } catch {
    return false
  } finally {
    _inFlight.delete(key)
  }
}

/**
 * 看护器侧入口：意外退出（planned 为空）⇒ 排一次自动重启。
 * **不 await**：看护器 4s 一轮，重启要等就绪窗口 —— 挂在看护器上会拖慢它的扫描。
 */
export function scheduleAutoRestart(key: Component, planned: string | null): void {
  if (planned) return
  if (!AUTO_RESTART_KEYS.includes(key)) return
  void attemptAutoRestart(key).catch(() => {
    /* 永不炸看护器循环 */
  })
}
