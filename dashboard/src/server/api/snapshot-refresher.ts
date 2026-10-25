/** snapshot-refresher.ts — 慢快照读写门面与后台刷新器（请求只读缓存）。
 *
 *  两层（见 snapshot-cache.ts 文件头「两层」）：
 *    · **机群探测器** `getFleetProbes`（节点 ping / 共享组件健康 / push 机群探活 / 池历史）——
 *      单条目、跨课程共用，用 SWR 语义（新鲜直接给、陈旧先给旧值再后台重算、被动作
 *      `refresh()` 后同样先给旧值）。切课程与动作后**永不**撞上这几笔探测；
 *    · **课程快照** `getSlowSnapshot`（组件表 ⊕ 现算的结构 ⊕ 探测器 + 阶段 + 账本尾派生）——
 *      按课程键控，重算只剩毫秒级的现算 + 账本尾读。
 */
import path from 'path'
import { statSync } from 'fs'
import { tmpLogsDir } from '../../core/paths'
import type { RlConfig } from '../../core/types'
import { createSwrCache } from '../../core/swr-cache'
import { courseEnabled } from '../../stack/courses'
import { loadConsoleState } from '../actions'
import { readIterMetrics } from '../iters'
import { loadConfigSafe } from './config'
import { discoverCourses, effectiveCourse } from './courses'
import { ERROR_SCAN_INTERVAL_MS, refreshErrorRegistry } from './error-registry'
import { getHubAdmin, refreshHubAdmin } from './overview'
import { refreshLoopQueue } from './loop-queue'
import { refreshPoolViews } from './pool'
import { invalidateEvalBoard } from '../eval-board'
import {
  type FleetProbes,
  SNAPSHOT_REFRESH_MS,
  SNAPSHOT_VIEW_TTL_MS,
  EMPTY_FLEET_PROBES,
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

/** 只读当前机群探测值（**不触发**重算；无值 = null）。首帧用它：没有值就留空，不冷算。 */
export function peekFleetProbes(): FleetProbes | null {
  return fleetProbeCache.peek()
}

// ────────────────────────── 课程级 ──────────────────────────

/** 快照代际：硬作废（`invalidateSlowSnapshot` / 动作后清课级快照）自增。
 *
 *  ★ 2026-10-25（首帧渐进暴露出的一类假绿）：`invalidateSlowSnapshot` 清了 `slowSnapshots`，
 *  但**在飞的重算**没有任何记号——它落地时会无条件 `set` 回去，作废看起来生效了、实际是
 *  旧值回填（与 `swr-cache` 的「代际」同一种病）。首帧把「重算丢后台」变成常见形态后，
 *  这个窗口就变成了硬清之后的**第一条请求又拿到陈旧探测**。早于作废的重算不许回填。 */
let slowGeneration = 0

export function getSlowSnapshot(cfg: RlConfig, course: string): Promise<SlowSnapshot> {
  const now = Date.now()
  const ent = slowSnapshots.get(course)
  if (ent) ent.lastAccess = now
  if (ent && now - ent.at < SNAPSHOT_REFRESH_MS) return Promise.resolve(ent.snap)
  const inflight = snapshotInFlight.get(course)
  if (inflight) return inflight
  const gen = slowGeneration
  const p = getFleetProbes(cfg)
    .then((probes) => computeSlowSnapshot(cfg, course, probes))
    .then(
      (snap) => {
        if (gen === slowGeneration) {
          slowSnapshots.set(course, { snap, at: Date.now(), lastAccess: Date.now() })
          snapshotInFlight.delete(course)
        }
        return snap
      },
      (e) => {
        if (gen === slowGeneration) snapshotInFlight.delete(course)
        throw e
      },
    )
  snapshotInFlight.set(course, p)
  return p
}

/** **首帧快照**（2026-10-25，冷启动渐进渲染）：能立刻给的先给，**永不**等探测。
 *
 *  三条路径，全部毫秒级：
 *    ① 缓存新鲜 ⇒ 直接给（与 `getSlowSnapshot` 完全一致，热路径零新增开销）；
 *    ② 缓存陈旧 ⇒ 给陈旧值（SWR 语义，重算丢后台）；
 *    ③ 从未算过（进程刚起 / 刚硬清）⇒ 给**结构壳**（`EMPTY_FLEET_PROBES` + `probesPending`），
 *       同时把真算丢进后台（`getSlowSnapshot` 会落缓存，下一帧就换成真值）。
 *
 *  ★ 为什么值得（实测 2026-10-25，Bun 1.4.4）：冷 `buildStateView` 2299ms 全在等探测预算
 *  （节点 ping 1.5s ∥ hub 1.2–1.5s ∥ 隧道 2.5s）；结构部分（账本/registry/日志尾/阶段 +
 *  视图组装）实测 **28–33ms**。首帧等它 = 白屏一两秒，而不等它只是让探测列晚一拍上屏。
 *  ★ 为什么不能只是「给空值」（必须带 `probesPending`）：空值在现有 UI 里就是「贡献 0」
 *  ⇒ 画成红「离线」。未知必须画成未知——见 `ConsoleStateView.probesPending`。
 *  ★ 只有**首帧**走它（SSR 页面 / 启动时的监督回执）；`/api/state` 轮询仍走 `getSlowSnapshot`：
 *  后台那一算落缓存后，轮询拿到的就是真值。 */
export async function getFirstPaintSnapshot(cfg: RlConfig, course: string): Promise<SlowSnapshot> {
  const now = Date.now()
  const ent = slowSnapshots.get(course)
  if (ent) {
    ent.lastAccess = now
    if (now - ent.at < SNAPSHOT_REFRESH_MS) return ent.snap
    // 陈旧：先给旧值，重算丢后台（与 SWR ④ 同语义）。冷算失败由刷新器下一拍重试，这里不上抛。
    if (consoleWatched(now)) void getSlowSnapshot(cfg, course).catch(() => undefined)
    return ent.snap
  }
  // 冷：起跑真算（不 await），本帧只给结构。没人看（例：启动后没人开页时的监督拍）不起跑。
  if (consoleWatched(now)) void getSlowSnapshot(cfg, course).catch(() => undefined)
  return computeSlowSnapshot(cfg, course, EMPTY_FLEET_PROBES, true)
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
  // 课程级是**硬清**（下一次读必须重算）：在飞的重算同样作废（见 `slowGeneration`）。
  slowGeneration++
  snapshotInFlight.clear()
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
  slowGeneration++
  slowSnapshots.clear()
  // 在飞的重算一并作废（否则 `getSlowSnapshot` 会把那个**旧输入**的 promise 直接回给下一次读，
  // 「下一次读必须重算」就成了空话）。落地时由代际闸挡住回填，见 `slowGeneration`。
  snapshotInFlight.clear()
  fleetProbeCache.clear()
}

/** 逐轮实际值抄录的节拍。窗口怎么定：课程一轮 ≈1.5min，而 `keep_iters`（rl-config 缺省 3）
 *  轮转 ⇒ 一轮在盘上活 ≈4.5min ⇒ 15s 有十几次机会，够宽。 */
const METRICS_HARVEST_MS = 15_000

/** 各课账本 stat 签名（`size:mtime`）——「账本没动过 ⇒ 没有新轮可抄」的便宜闸。
 *  实测（2026-09-28，h5a-earlydmg 30 行 / h5b-clean 29 行）：冷抄一轮 186/162ms（第一次扫
 *  `it<N>`），全命中缓存 27/24ms；闸生效时 0ms。长驻服务里这个差别会乘以**在训课数 × 天数**。 */
const harvestLastStat = new Map<string, string>()

/** 逐轮实际值抄录（副作用 = `iters.ts::readIterMetrics` 的缓存写入）。
 *
 *  为什么必须有它（2026-09-28 事故）：这几列**不在账本里**——它们是扫 `it<N>` 现算后缓存的
 *  派生值，而 `/api/state` 只对**查看课程**算（`state-view.ts`），训练侧每轮又按 `keep_iters`
 *  删 `it<N>`（`biz/loop_guards_sweep.py::_rotate_cleanup`）⇒ 没人看过的那门课的轮次到第 4 轮
 *  就永久空白（实测 `h5b-clean` 缺 it7–17、`h5a-earlydmg` 缺 it22–25，两个缺口在时间轴上
 *  互补 = 「谁在屏幕上谁才有数」）。这里把「看见」从人手上拿走：在训的课每拍都抄一遍。
 *
 *  两道闸（都是为了让「每拍」不变成「每拍扫盘」）：
 *    ① 只抄**已开课**课（`training-enabled.txt`，与训练侧同一道闸）：tmp/ 下几十门历史课的
 *       账本永远在盘上，逐拍全扫才是真浪费；而训练一停就不再轮转（plan/course-archive
 *       §3.6 N1），停课后打开控制台现算来得及；
 *    ② 账本签名未变则跳过——**但只在上一轮把所有轮次都抄到了时才跳过**：云机/回传腿的
 *       `it<N>/per-game.json` 与账本行是同一次落地的，先后顺序不保证，早一拍抄到 null 若
 *       不再重试就永远是空白（那正是本函数要治的病）。有缺口 ⇒ 抹掉签名、下一拍重试。
 *
 *  单门课失败只少抄一门（观测面坏掉不该带崩刷新器），下一拍重试。
 *  返回本轮实际抄录（= 账本不再被签名跳过的）课程，观测与单测用。 */
export function harvestTrainingCourseActuals(): string[] {
  const harvested: string[] = []
  for (const course of discoverCourses()) {
    try {
      if (!courseEnabled(course)) continue
      const dir = path.join(tmpLogsDir(), course)
      let sig: string
      try {
        const st = statSync(path.join(dir, 'training_log.jsonl'))
        sig = `${st.size}:${st.mtimeMs}`
      } catch {
        continue // 无账本 ⇒ 还不是课程
      }
      if (harvestLastStat.get(course) === sig) continue
      const { rows } = readIterMetrics(dir)
      // 有轮次没抄到实际值 ⇒ 不落签名（下一拍重试）；全抄到 ⇒ 记签名（下拍零成本）。
      if (rows.some((r) => r.iter > 0 && !r.actuals)) harvestLastStat.delete(course)
      else harvestLastStat.set(course, sig)
      harvested.push(course)
    } catch {
      /* 单门课失败只少抄一门 */
    }
  }
  return harvested
}

// ────────────────────────── 「没人在看就不刷」（2026-10-25） ──────────────────────────

/** 最近一次**有人在看**（HTTP 请求到达）的时刻；初值 = 模块加载时刻（刚起来的控制台先暖一拍）。
 *  由 `server.ts` 的请求入口调 `markConsoleActivity()` 刷新。 */
let lastActivityAt = Date.now()

/** 多久没有任何请求就停刷（毫秒）。客户端缺省 3s 轮询（`usePolling`）⇒ 取 2 分钟：
 *  一次 tab 后台/网络抖动不误判，真「关了页面」两分钟内停火。 */
export const IDLE_REFRESH_SKIP_MS = 120_000

/** 记一次「有人在看」（`server.ts` 每个请求首行调用）。`now` 可注入 ⇒ 判据不靠墙钟。 */
export function markConsoleActivity(now: number = Date.now()): void {
  lastActivityAt = now
}

/** 距离最后一次活动的毫秒数（观测/单测用）。 */
export function consoleIdleMs(now: number = Date.now()): number {
  return now - lastActivityAt
}

/** 是否有人在看（= 空闲闸的同一判据）。首帧路径的**后台起跑**都过这道闸：没人看时
 *  （例：启动后没人开页，`reconcileWatch` 仍每 15s 跑）不应把探测丢回后台重算。 */
export function consoleWatched(now: number = Date.now()): boolean {
  return now - lastActivityAt <= IDLE_REFRESH_SKIP_MS
}

/** 刷新器的**一拍**（定时器与单测共用；`now` 注入 ⇒ 不靠墙钟）。
 *
 *  返回本拍的去向（观测/断言用）：`idle` = 没人在看、只做内存清理；`refreshed` = 照常重算。
 *
 *  ★ 为什么可以「没人在看就不刷」（而不是白刷着保温）：陈旧值不会变成延迟——
 *  `getFleetProbes` 是 SWR（陈旧先给旧值 + 重算丢后台），而 `getSlowSnapshot` 的冷算本身只
 *  是毫秒级的现算 + 账本尾读。所以空闲后的**首个请求照样不等探测**，代价只是那一帧的
 *  探测列可能是几小时前的（≤1 个快照周期内自愈）；换掉的是「没人看也每 5s 探一轮」的常驻
 *  CPU/网络开销（Bun 1.4.3 起空转自耗已经很底，但控制台的探测是**主动出网**，不是空转）。 */
export async function refreshSnapshotTick(now: number = Date.now()): Promise<'idle' | 'refreshed'> {
  // 内存卫生与「有没有人看」无关：超窗没再被看的课程快照一律丢掉（否则关页后一直占着）。
  if (now - lastActivityAt > IDLE_REFRESH_SKIP_MS) {
    for (const [c, ent] of slowSnapshots) {
      if (now - ent.lastAccess > SNAPSHOT_VIEW_TTL_MS) slowSnapshots.delete(c)
    }
    return 'idle'
  }
  const cfg = loadConfigSafe()
  const operatorCourse = effectiveCourse(loadConsoleState(), discoverCourses())
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
  return 'refreshed'
}

/** 后台刷新器：立即暖一次 + 每 intervalMs 重算（unref，不阻止进程退出）。服务端启动时
 *  调用一次。操作员课程每拍必刷（保持原语义）；近期被查看（SNAPSHOT_VIEW_TTL_MS 内）的
 *  其它课程跟随刷新；超时未看的课程丢弃，不再占用探测预算。
 *
 *  机群级（`getFleetProbes` / `getHubAdmin`）每拍也暖一次：它们跨课程共用，暖一拍
 *  ≠ N 门课各探一遍——这既是切课/动作后的即时性来源，也让探测列在无人看课时不至于陈旧
 *  （新鲜度仍是 ≤1 个刷新周期）。
 *
 *  ★ 「无人看就停刷」见 `refreshSnapshotTick`：**判据是请求到达**（`markConsoleActivity`），
 *  不是「有没有人开过页面」——控制台重启后没人打开时，也不该每 5s 出去探一轮。
 *
 *  ★ 另挂一条**独立节拍**的逐轮实际值抄录（`harvestTrainingCourseActuals`）：它不做任何
 *  探测，也不该被节点 ping / 共享 hub 那 1.2–2.5s 的超时预算拖住（抄录的本职是**赶在
 *  `keep_iters` 轮转前落盘**）。理由见该函数。它**不受空闲闸**——关页期间轮转照样在发生。 */
export function startSnapshotRefresher(intervalMs: number = SNAPSHOT_REFRESH_MS): void {
  const run = async (): Promise<void> => {
    try {
      await refreshSnapshotTick()
    } catch {
      /* 下一拍重试 */
    }
  }
  void run()
  const t = setInterval(() => void run(), intervalMs)
  t.unref?.()
  // 逐轮实际值抄录（抗轮转）：与慢快照同生共死——`server.ts` 只调这一个入口。
  void harvestTrainingCourseActuals()
  const th = setInterval(() => void harvestTrainingCourseActuals(), METRICS_HARVEST_MS)
  th.unref?.()
  // 组件错误巡检（2026-10-16，plan/component-error-registry §4.2）：**第三条独立节拍**。
  // 为什么独立：它读盘（各组件日志尾）与探 hub（腿 C），与「节点 ping / 共享 hub 探测」
  // 那 1.2–2.5s 的预算不共享；而 `/api/state` 路径**一次都不许**碰它（N9 / R1 结构闸）——
  // 登记表是 SWR 缓存值，两个读面（`state-view.ts` 与 `/api/errors`）都只走 `getErrorRegistry()`。
  void refreshErrorRegistry()
  const te = setInterval(() => void refreshErrorRegistry(), ERROR_SCAN_INTERVAL_MS)
  te.unref?.()
}
