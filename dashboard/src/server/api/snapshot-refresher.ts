/** snapshot-refresher.ts — 慢快照读写门面与后台刷新器（请求只读缓存）。
 *
 *  两层（见 snapshot-cache.ts 文件头「两层」）：
 *    · **机群探测器** `getFleetProbes`（节点 ping / 共享组件健康 / push 机群探活 / 池历史）——
 *      单条目、跨课程共用，用 SWR 语义（新鲜直接给、陈旧先给旧值再后台重算、被动作
 *      `refresh()` 后同样先给旧值）。切课程与动作后**永不**撞上这几笔探测；
 *    · **课程快照** `getSlowSnapshot`（组件表 ⊕ 现算的结构 ⊕ 探测器 + 阶段 + 账本尾派生）——
 *      按课程键控，重算只剩毫秒级的现算 + 账本尾读。
 */
import type { RlConfig } from '../../core/types'
import { createSwrCache } from '../../core/swr-cache'
import { loadConsoleState } from '../actions'
import { loadConfigSafe } from './config'
import { discoverCourses, effectiveCourse } from './courses'
import { getHubAdmin, refreshHubAdmin } from './overview'
import { refreshLoopQueue } from './loop-queue'
import { refreshPoolViews } from './pool'
import { invalidateEvalBoard } from '../eval-board'
import {
  type FleetProbes,
  SNAPSHOT_REFRESH_MS,
  SNAPSHOT_VIEW_TTL_MS,
  SlowSnapshot,
  computeFleetProbes,
  computeSlowSnapshot,
  slowSnapshots,
  snapshotInFlight,
} from './snapshot-cache'

// ────────────────────────── 机群级探测（跨课程共用） ──────────────────────────

/** 机群级探测的全局缓存（单条目）。陈旧窗口与快照同节奏（5s）；后台刷新器每拍续命。 */
const fleetProbeCache = createSwrCache<FleetProbes>(SNAPSHOT_REFRESH_MS)

/** 取机群级探测（节点/组件健康/push 群/池历史）。请求路径上**永不**等探测。 */
export function getFleetProbes(cfg: RlConfig): Promise<FleetProbes> {
  return fleetProbeCache.get(() => computeFleetProbes(cfg))
}

// ────────────────────────── 课程级 ──────────────────────────

export function getSlowSnapshot(cfg: RlConfig, course: string): Promise<SlowSnapshot> {
  const now = Date.now()
  const ent = slowSnapshots.get(course)
  if (ent) ent.lastAccess = now
  if (ent && now - ent.at < SNAPSHOT_REFRESH_MS) return Promise.resolve(ent.snap)
  const inflight = snapshotInFlight.get(course)
  if (inflight) return inflight
  const p = getFleetProbes(cfg)
    .then((probes) => computeSlowSnapshot(cfg, course, probes))
    .then(
      (snap) => {
        slowSnapshots.set(course, { snap, at: Date.now(), lastAccess: Date.now() })
        snapshotInFlight.delete(course)
        return snap
      },
      (e) => {
        snapshotInFlight.delete(course)
        throw e
      },
    )
  snapshotInFlight.set(course, p)
  return p
}

/** **动作后的缓存处置（生产路径唯一入口）**。
 *
 *  **一个入口、两种处置**，按「重算贵不贵」分（而不是按缓存住在哪个文件）：
 *    · **硬清**（下一次读必须等重算）：只给**课程级**快照——重算毫秒级（结构现算 + 账本尾读），
 *      而组件存活/日志尾/阶段这类动作结果**必须即时上屏**，不值得为它引入一次陈旧窗口；
 *    · **软作废**（`refresh()`：读路径先给旧值，重算丢后台）：给机群级探测（节点 ping /
 *      共享 hub 2.5s 超时窗）、hub 观测面、调度器视图（python 子进程）与**池视图**
 *      （其**探测层**冷算实测 2448–2552ms：逐节点 ping 2.5s ∥ + 池历史聚合 + codeHash）——它们的
 *      重算要等一次 1.2–20s 的等待，让请求等它 = 「动作后第一帧又卡几秒」（2026-09-22 用户报障
 *      的下半场）；而它们提供的是**探测列**（online / healthy / hub 队列 / 每课在等什么 / 池状态），
 *      结构（节点行的 enabled/url、worker 行、执行面 mode、组件 running/stopped、暂停意图与
 *      生效回执）都是现算的，所以**动作结果照样即时上屏**。
 *    · **再加一个硬清：评估板视图**（`invalidateEvalBoard`，见上一条追加：它 0–1ms 冷算、全是
 *      读盘+纯聚合，但输入就是这些动作刚写下的请求/批次文件——**必须下一帧就可见**）。
 *
 *  ★ 软作废要求重算体**真的不按住事件循环**：调度器视图的默认执行体已改成异步子进程
 *  （`runRunPythonAsyncScript`），否则 spawnSync 会把旧值的响应也一起堵住。
 *
 *  ★ 切课（视图态动作）**不在**作废之列：它不改任何组件/节点事实——见 server.ts 的
 *  `invalidatesSnapshot`。 */
export function invalidateAfterAction(): void {
  slowSnapshots.clear()
  fleetProbeCache.refresh()
  refreshHubAdmin()
  refreshLoopQueue()
  refreshPoolViews()
  // 评估板（`/api/evalboard`）：动作写下的请求文件/批次表就是它的输入（入队 → 「已入队」那行
  // 必须下一帧就上屏，否则最长要等 30s TTL / 5min 面板轮询）。这里用**硬清**而不是软作废：
  // 它没有探测类输入（全是读盘 + 纯聚合，实测冷算 0–1ms、入账 2000 行 4–8ms）——「先给旧值」
  // 在这个量级上是净损失；而按课程键控的视图整张清掉也只是每门课重读一次。
  invalidateEvalBoard()
}

/** **硬作废**（下一次读**必须**重算）：改了输入（rl-config / 账本 / registry）或要隔离测试
 *  夹具时用。生产路径只有产物导入（`/api/deliverUpload`）用它——那里换的是盘上的东西，
 *  「先给旧值」没有必要。动作路径走 `invalidateAfterAction()`。 */
export function invalidateSlowSnapshot(): void {
  slowSnapshots.clear()
  fleetProbeCache.clear()
}

/** 后台刷新器：立即暖一次 + 每 intervalMs 重算（unref，不阻止进程退出）。服务端启动时
 *  调用一次。操作员课程每拍必刷（保持原语义）；近期被查看（SNAPSHOT_VIEW_TTL_MS 内）的
 *  其它课程跟随刷新；超时未看的课程丢弃，不再占用探测预算。
 *
 *  机群级（`getFleetProbes` / `getHubAdmin`）每拍也暖一次：它们跨课程共用，暖一拍
 *  ≠ N 门课各探一遍——这既是切课/动作后的即时性来源，也让探测列在无人看课时不至于陈旧
 *  （新鲜度仍是 ≤1 个刷新周期）。 */
export function startSnapshotRefresher(intervalMs: number = SNAPSHOT_REFRESH_MS): void {
  const run = async (): Promise<void> => {
    try {
      const cfg = loadConfigSafe()
      const operatorCourse = effectiveCourse(loadConsoleState(), discoverCourses())
      const now = Date.now()
      const targets = new Set<string>([operatorCourse])
      for (const [c, ent] of slowSnapshots) {
        if (c === operatorCourse) continue
        if (now - ent.lastAccess > SNAPSHOT_VIEW_TTL_MS) {
          slowSnapshots.delete(c)
          continue
        }
        targets.add(c)
      }
      await Promise.all([
        getFleetProbes(cfg),
        getHubAdmin(cfg, operatorCourse),
        ...[...targets].map((c) => getSlowSnapshot(cfg, c)),
      ])
    } catch {
      /* 下一拍重试 */
    }
  }
  void run()
  const t = setInterval(() => void run(), intervalMs)
  t.unref?.()
}
