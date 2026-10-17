/** trainer-heartbeat.ts — 共享 trainer（serve）心跳的读面 + **判据**（plan/single-resident-trainer §2）。
 *
 *  写侧 = python 的写线程（`worker/trainer_heartbeat.py`，每 5s 原子写
 *  `tmp/trainer-heartbeat.json`）。为什么写侧是线程而不是「每拍写」：单线程的 serve
 *  一次 `sup.step()` 之内可以合法阻塞到 +3600s（预采 join / PPO 让位 / 引擎冷启动），
 *  写点挂在「每拍」上会**误杀健康进程**（评审 P0-2）。
 *
 *  ## 判据（三个判决，一个函数）
 *
 *  · `fresh`——文件在、pid 对得上、`ts` 距现在 ≤ STALE ⇒ 健康；
 *  · `stale`——同上但超窗 ⇒ **疑似卡死**（消费面 = 告警坞一条 `err` + 人工「停止→启动」；
 *    plan §2.2 的裁决 B：不自动重启活进程）；
 *  · `unknown`——判不了就不判：① 文件缺失/坏 JSON（写侧还没写过，或旧版进程）；
 *    ② **pid 对不上**（冒烟预演占的是**同一个** `trainingLoop` 槽，心跳是 serve 写的、账本
 *    是冒烟的 ⇒ 拿它判 serve 就是误杀）；③ 进程起得比心跳判据还早（一次性兼容窗口，plan §2.3-3）；
 *    ④ 启动宽限（进程刚起 120s 内，第一拍可能还没落盘）。
 *
 *  ⇒ 调用方只需问「verdict 是不是 stale」：`unknown` 一律**不判死**（不可知 ≠ 有病）。
 *
 *  为什么住 `core/`（而同族的 `detached-trainers.ts` 住服务端 api/）：这里不只读，还给出
 *  **判据**——它被 `stack/specs.ts::trainerServeSpec.healthy`（进程健康面）与 `/api/state`
 *  （上屏面）同时消费；住 core 才能让两面共用同一份判决，不绕经服务端。
 */

import { readFileSync } from 'fs'
import { pidAlive } from './net'
import { trainerHeartbeatPath } from './paths'
import { entryForCourse, loadRegistry } from './registry'
import type { TrainerHeartbeatView } from '../web/view'

/** 判死窗口（秒）。写侧周期 5s ⇒ 60× 余量（plan §2.1/§2.3 的不变式）。 */
export const HEARTBEAT_STALE_SEC = 300
/** 启动宽限（秒）：刚起的进程第一拍可能还没落盘。 */
export const HEARTBEAT_GRACE_SEC = 120
/** 判据上线时刻（epoch ms）：更早启动的进程**没有**心跳文件是正常的（一次性兼容窗口，plan §2.3-3）。 */
export const HEARTBEAT_SINCE_MS = Date.UTC(2026, 9, 17)

export type HeartbeatVerdict = 'fresh' | 'stale' | 'unknown'

/** 心跳文件的原始字段（写侧形状；多出来的键原样保留）。 */
export interface RawHeartbeat {
  pid: number
  ts: number
  steps?: number
  waiting?: number
  courses?: Record<string, { state?: string; rounds_done?: number; next_it?: number }>
}

export interface HeartbeatVerdictOpts {
  staleSec?: number
  graceSec?: number
  sinceMs?: number
}

/** 最小可判的账本条目形状（只用到这两字段，免得把整个 Registry 类型拖进来）。 */
export interface HeartbeatSubject {
  pid?: number
  startedAt?: number
}

export function readHeartbeat(path?: string): RawHeartbeat | null {
  let raw: string
  try {
    raw = readFileSync(path ?? trainerHeartbeatPath(), 'utf-8')
  } catch {
    return null // 缺失/不可读 ⇒ 不可知（≠ 有病）
  }
  try {
    const got = JSON.parse(raw) as unknown
    if (typeof got !== 'object' || got === null) return null
    const rec = got as Record<string, unknown>
    const pid = Number(rec.pid)
    const ts = Number(rec.ts)
    if (!Number.isInteger(pid) || !Number.isFinite(ts)) return null
    return got as RawHeartbeat
  } catch {
    return null
  }
}

/** 判据（纯函数，`now` 注入 ⇒ 可单测）。`hb` 为 null 时走「不可知」的两条支路。 */
export function heartbeatVerdict(
  entry: HeartbeatSubject | undefined | null,
  hb: RawHeartbeat | null,
  now: number,
  opts: HeartbeatVerdictOpts = {},
): HeartbeatVerdict {
  const staleSec = opts.staleSec ?? HEARTBEAT_STALE_SEC
  const graceSec = opts.graceSec ?? HEARTBEAT_GRACE_SEC
  const sinceMs = opts.sinceMs ?? HEARTBEAT_SINCE_MS
  if (hb) {
    if (entry?.pid && hb.pid !== entry.pid) return 'unknown' // 槽位换人（冒烟预演）
    return now - hb.ts * 1000 <= staleSec * 1000 ? 'fresh' : 'stale'
  }
  // 没有心跳文件：只可能「还没写过」或「旧版进程」，一律不判死。
  if (!entry?.startedAt) return 'unknown' // 旧条目/手工起的进程：没有启动时刻可比
  if (entry.startedAt < sinceMs) return 'unknown' // 判据上线前启动的进程（一次性兼容）
  if (now - entry.startedAt < graceSec * 1000) return 'unknown' // 启动宽限
  return 'stale' // 上线后启动、过了宽限、却从没写过心跳 ⇒ 写侧没起来（不静默）
}

/** 读 + 判 + 投影。返回 `null` = 不可知（调用方不判死、不出告警）。 */
export function resolveTrainerHeartbeat(
  io: { path?: string; now?: number; entry?: HeartbeatSubject | null } = {},
): TrainerHeartbeatView | null {
  const now = io.now ?? Date.now()
  const entry =
    io.entry === undefined
      ? entryForCourse(loadRegistry(), 'trainingLoop', '')
      : (io.entry ?? undefined)
  const hb = readHeartbeat(io.path)
  const verdict = heartbeatVerdict(entry, hb, now)
  if (verdict === 'unknown' || !hb) return null
  return {
    pid: hb.pid,
    ts: hb.ts,
    steps: Number(hb.steps ?? 0),
    waiting: Number(hb.waiting ?? 0),
    courses: hb.courses ?? {},
    verdict,
    ageSec: Math.max(0, Math.round(now / 1000 - hb.ts)),
  }
}

/** 供进程内判据用：有没有「pid 对得上且已陈旧」的心跳（`specs.ts::healthy` 的读面）。 */
export function heartbeatAlive(): boolean {
  const entry = entryForCourse(loadRegistry(), 'trainingLoop', '')
  if (!pidAlive(entry?.pid)) return false
  return resolveTrainerHeartbeat()?.verdict !== 'stale'
}
