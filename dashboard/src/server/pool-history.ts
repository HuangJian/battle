/** pool-history.ts — 节点历史聚合（机群级：合并 tmp/ 下所有训练流）。
 *
 *  纯 fs 逻辑的服务端数据层。**2026-09-26 起节点统计与课程解耦**（plan/nodes-decouple-from-course.plan.md）：
 *
 *   · **数据源 = 所有流**：递归扫描 tmp/ 下所有 `dist-agent-meta.jsonl`（课程目录 + 独立 eval run
 *     目录），逐流取**完成水位**（排除进行中那一轮），再合并落桶。此前只聚合 mtime 最新那一个源。
 *   · **单一时间口径**：视图按「本地日」分桶（`byDay`），统计只用时间 —— 课程内序号 `it` 只作
 *     内部过滤器（`it <= baseIt_flow`），**不出现在任何展示字段**。
 *   · **一次算全量、切天纯投影**：`aggregateNodeHistory()`（与窗口无关）⊕ `projectWindow(agg, w)`
 *     （纯函数）。探测层缓存全量 agg；切天零重算。
 *   · **贡献数取「最新完成的整轮」**：跨课按**完成时刻**选（不是比 `it` 大小——`it` 是课程内序号，
 *     不可比）；停摆课因完成时刻旧而自然落选。
 *
 *  GLM-U3 修正：lastError 在聚合层剥离 sampler-agent 的 `new Date().toISOString()`（UTC）前缀
 *  ——注意那是 `lastError` **字符串**的前缀；meta 行的 `ts` 字段是 Python `strftime` 写的
 *  **训练机本地时间、无时区后缀**（§4.2，别混）。
 *
 *  历史锚点 tmp/dist-agent/pool-epoch.txt（受控清空）保留原语义。
 */

import { existsSync, readFileSync, readdirSync, statSync } from 'fs'
import { dirname, join } from 'path'
import { tmpPoolDir } from '../core/paths'
import {
  type ContributionSplit,
  fmtFullTs,
  latestIterationFromLedgerTail,
  stripIsoPrefix,
} from '../web/view'
// 直连 api/logs 的文件而非 `../api` 桶：桶经 snapshot-cache 反向 import 本模块（成环）。
import { readLedgerTail } from './api/logs'

/** 锚点文件路径：<池扫描根>/dist-agent/pool-epoch.txt（默认 tmp/dist-agent/pool-epoch.txt，
 *  与旧硬编码路径逐字节相同）。语义（用户 2026-08-31）：
 *  · 部署写入一次 → 历史自此刻起重新累计；
 *  · 此后任何部署/重启只读同一锚点 → 历史持续累计；
 *  · 用户明确要求清空时，重写/删除该文件 = 新锚点。
 *
 *  ★ 2026-09-26（二轮评审）：随 `tmpPoolDir()` 取根而不是硬钉 `REPO_ROOT` ——
 *  单测以 `BCITY_POOL_DIR` 重定向池根时，锚点跟着走，预筛才能端到端验（此前的 DoD
 *  只能测 `pruneByEpoch` 纯函数）。默认路径不变。 */
function epochFilePath(): string {
  return join(tmpPoolDir(), 'dist-agent', 'pool-epoch.txt')
}

/** 0 = 锚点未建立（累计全部历史）；>0 = 只统计该时刻之后的行。
 *
 *  ★ **惰性**取值（每次聚合读一次，不再是模块加载时常量）：单测写完锚点再聚合即可生效；
 *  代价是每次聚合一次 `existsSync` + 一次小文件读（毫秒级，且被进程内 memo 摊薄）。 */
function poolEpochMs(): number {
  try {
    const p = epochFilePath()
    if (!existsSync(p)) return 0
    const v = parseInt(readFileSync(p, 'utf8').trim(), 10)
    return Number.isFinite(v) && v > 0 ? v : 0
  } catch {
    return 0
  }
}

/** 大文件阈值：超过它只读尾部（`readLedgerTail`，诚实截断由 `ActiveFlow.truncated` 上屏）。 */
const LARGE_META_BYTES = 2 * 1024 * 1024
const LARGE_META_TAIL_LINES = 20_000

/** 滚动窗口（`近 30 分钟`/`近 2 小时`/`近 24 小时`）的事件环保留时长：**最长的滚动档 + 10min 余量**。
 *
 *  ⚠ 为什么滚动窗**不能**吃日桶：`DayBucket` 只有日级计数，`results` 是 `boolean[]`
 *  （无时间戳）——`now - T` 的窗口从日桶里根本投不出来。环里的事件全部带 ms，
 *  由 `projectRollingWindow` 过滤重建 `NodeHistory`。
 *
 *  ★ 这个常量必须 **≥ 最长滚动档**（2026-10-03）：加 `24h` 档时若不同步抬高，24h 窗口只会
 *  看到环里残留的 2h（数字偏低 ~12 倍，且**不会有任何标记**）—— 这类「窗口比数据源长」的
 *  错配是静默错数，比崩溃贵。代价只有内存：环有 `ROLLING_CAP` 封顶（有界），
 *  触顶由 `rollingTruncated` 诚实标注（UI 脚注）。 */
export const ROLLING_KEEP_MS = 24 * 60 * 60_000 + 10 * 60_000
/** 每节点事件环硬上限：命中即丢最旧一半并置截断标记（有界内存；极端忙节点才可能触发）。 */
export const ROLLING_CAP = 20_000

/** 子日事件环的一行（滚动窗的唯一数据源）。 */
export interface RollingEvent {
  ms: number
  /** 是否属于已完成轮（与 DayBucket 同一数据卫生口径；投影层按它过滤计数）。 */
  counted: boolean
  ok: boolean
  mode: 'rollout' | 'eval'
  elapsedSec: number | null
  wallSec: number | null
  /** 失败原因（已剥 ISO 前缀、截 120；成功行空串）。 */
  reason: string
  /** 课名（流目录首段；矩阵的课维度）。 */
  course: string
}

/** 进程内聚合 memo 的最短复用窗口（毫秒）。
 *
 *  **为什么必须同时有「时间下限」和「指纹」**（2026-09-26 二轮评审）：
 *   · 指纹（路径 + mtime + 体积）保证**空闲时零重扫**；
 *   · 但训练在跑时 meta 逐秒追加 ⇒ 指纹每秒都在变，只靠指纹等于每 5s 重扫一次全池
 *     （快照刷新器每拍都调 `getFleetProbes` → 本函数，而 5 个大文件 40+MB）。
 *     时间下限把重扫节奏封顶在 ≤ 1 次/窗口。*/
export const AGG_MEMO_MIN_MS = 30_000

interface AggMemo {
  root: string
  epochMs: number
  fp: string
  /** ★ 上一次**计算**的时刻（**命中不刷新**——见 `aggMemoReusable`）。 */
  computedAt: number
  val: HistoryAggregate
}
let aggMemo: AggMemo | null = null

/** memo 复用判定（纯函数，可单测）。语义两条：
 *  · **指纹相同**（池没动过）⇒ 复用，**与时间无关** ⇒ 空闲时零重扫；
 *  · **指纹变了**（训练在追加 meta）⇒ 距**上次计算**不足 `minMs` 先复用旧值，把重扫节奏封顶。
 *
 *  ★ `computedAt` 必须是「上次**计算**时刻」，**不能**是「上次命中时刻」：`snapshot-cache`
 *  的机群探测每 5s 拍一次本聚合，若命中就把时刻推到现在，时间下限永远成立 ⇒ 指纹比较
 *  **永远轮不到** ⇒ memo 冻结在进程第一次计算那一刻，此后新开的训练流一个都看不见
 *  （2026-09-27 实障：节点页「今天」整窗全空、而进程启动前就存在的「昨天」照旧有数——
 *  池根里当天新生的 3 个流从未进过 `sources`）。 */
export function aggMemoReusable(
  m: { fp: string; computedAt: number },
  fp: string,
  nowMs: number,
  minMs = AGG_MEMO_MIN_MS,
): boolean {
  if (m.fp === fp) return true
  return nowMs - m.computedAt < minMs
}

/** 硬清聚合 memo。生产路径只有 `?fresh=1`（手动刷新：操作员明确要「现在就给我新的」）调；
 *  其余时候无需调用——指纹/时间下限自会失效。单测夹具也用它隔离。 */
export function invalidateNodeHistoryMemo(): void {
  aggMemo = null
}

/** dist-agent-meta 的 ts 分布带 T（ISO）与空格两种写法；统一为空格格式后再比。 */
function normTs(s: string | undefined): string {
  return (s ?? '').replace('T', ' ')
}

/** 滑动窗口上限（服务时长 / 训练机墙钟共用）。 */
export const ELAPSED_WINDOW = 50

/** 只收正有限样本；窗口满则挤掉最旧。 */
export function pushWindowSample(arr: number[], v: unknown, max = ELAPSED_WINDOW): void {
  if (typeof v !== 'number' || !(v > 0) || !Number.isFinite(v)) return
  arr.push(v)
  if (arr.length > max) arr.shift()
}

/** 滑动均值，保留 1 位小数；空样本 = null。 */
export function windowMeanSec(arr: readonly number[]): number | null {
  return arr.length ? +(arr.reduce((a, b) => a + b, 0) / arr.length).toFixed(1) : null
}

/** 每节点的**窗口投影**行（2026-09-26 起：所有统计字段都是窗口口径）。 */
export interface NodeHistory {
  ok: number
  fail: number
  lastTs: string
  lastOkTs: string
  lastFailTs: string
  /** 已剥离 agent ISO 前缀（GLM-U3）。 */
  lastError: string
  /** 最近至多 50 局的节点侧服务时长样本（滑动窗口）。 */
  elapsedRecent: number[]
  /** elapsedRecent 的均值；null = 无样本。 */
  avgElapsedSec: number | null
  /** 最近至多 50 局的训练机侧墙钟样本（派发→结算，含网络/轮询）。 */
  wallRecent: number[]
  /** wallRecent 的均值；null = 无样本（历史 meta 无 wallSec 时）。 */
  avgWallSec: number | null
  /** 最近至多 10 条结算结果（ok=true），完成率（成功率）的判定依据。 */
  recent: boolean[]
  /** ★窗口内成功局数（rollout / eval 分列，取代旧的「对齐轮 contrib*」）。 */
  winRollout: number
  winEval: number
  /** ★最近**完成**轮（跨课按完成时刻选）该节点成功局数（rollout + eval）；-1 = 无池数据。 */
  lastContrib: number
  /** 最近一次成功结算的毫秒时刻（null = 从未结算）；isSlowNode 判定用。 */
  lastOkTsMs: number | null
  /** 最近一次成功结算的单局耗时（秒，null = 无样本）；isSlowNode 判定用。 */
  lastOkElapsedSec: number | null
}

export function emptyHistory(): NodeHistory {
  return {
    ok: 0,
    fail: 0,
    lastTs: '-',
    lastOkTs: '-',
    lastFailTs: '-',
    lastError: '',
    elapsedRecent: [],
    avgElapsedSec: null,
    wallRecent: [],
    avgWallSec: null,
    recent: [],
    winRollout: 0,
    winEval: 0,
    lastContrib: -1,
    lastOkTsMs: null,
    lastOkElapsedSec: null,
  }
}

/** 活跃训练流信息：一个 `dist-agent-meta.jsonl`（**诊断**用途，不再当过滤依据）。 */
export interface ActiveFlow {
  dir: string
  mtimeMs: number
  lines: number
  /** 该流因体积过大只读了尾部（诚实截断，UI 标注）。 */
  truncated?: boolean
}

/** 本地日桶：保留窗口投影所需的全部原始事实。
 *
 *  ⚠ 字段必须够重建 `NodeHistory` 的**每一个窗口字段**：只存 `ok/fail` 计数的话，
 *  「窗口内完成率（最近 ≤10 条结算）」与「最近错误」就无源可算（评审缺口，2026-09-26）。
 *
 *  ★ **两组字段的口径不同（二轮评审补正）**：
 *   · **计数 / 样本 / 完成率**（`ok`/`fail`/`rollout`/`eval`/`results`/`elapsed`/`wall`）
 *     只收**已完成轮**（`it <= baseIt_flow`）——「这一轮的账结清了吗」是数据卫生；
 *   · **时间戳 / 错误**（`lastOkTs*`/`lastFailTs*`/`lastError*`/`lastTs`）收**全部**行
 *     ——「这台机器最后一次有动静是什么时候」是**可达性**（isSlowNode / 最近成功列）。
 *     两组混用会把「轮前段就交完活、轮又长」的节点推回上一轮时刻，在 30 分钟可达性
 *     窗口上从「慢」翻成「离线」（2026-09-11 报障的反面）。 */
export interface DayBucket {
  ok: number
  fail: number
  /** 窗口内成功局数（分 mode）。 */
  rollout: number
  eval: number
  /** 最近 ≤10 条结算结果（时间升序；跨天合并后再截尾 10）。 */
  results: boolean[]
  /** 最近 ≤50 成功局样本。 */
  elapsed: number[]
  wall: number[]
  lastOkTs: string
  lastFailTs: string
  /** 最近失败原因 + 其毫秒（是否上屏由投影按「近一小时」判定）。 */
  lastError: string
  lastErrorTsMs: number | null
  /** 最近失败时刻的毫秒（与 `lastFailTs` 同源；投影按「近一小时」判定是否上屏）。 */
  lastFailTsMs: number | null
  /** 最近一次成功结算的毫秒 + 单局耗时（isSlowNode 输入）。 */
  lastOkTsMs: number | null
  lastOkElapsedSec: number | null
  lastTs: string
}

function emptyDayBucket(): DayBucket {
  return {
    ok: 0,
    fail: 0,
    rollout: 0,
    eval: 0,
    results: [],
    elapsed: [],
    wall: [],
    lastOkTs: '',
    lastFailTs: '',
    lastError: '',
    lastErrorTsMs: null,
    lastFailTsMs: null,
    lastOkTsMs: null,
    lastOkElapsedSec: null,
    lastTs: '',
  }
}

/** 跨课选出的「最新完成轮」（诊断/展示脚注）。 */
export interface LatestRound {
  dir: string
  it: number
  completedAtMs: number | null
}

/** 全量聚合（**与窗口无关**，进 SWR 缓存；切天只投影）。 */
export interface HistoryAggregate {
  /** 本地日（'YYYY-MM-DD'）→ 节点 → 日桶。 */
  byDay: Map<string, Map<string, DayBucket>>
  /** 日 → 节点 → 课 → 计数（课程矩阵的采样维度；只收已完成轮，与日桶同一过滤）。 */
  byCourse: Map<string, Map<string, Map<string, ContributionSplit>>>
  /** 子日有界事件环（滚动窗唯一数据源）：节点 → 按时间升序事件。 */
  rolling: Map<string, RollingEvent[]>
  /** 事件环触顶被截断的节点（滚动窗数字偏低时 UI 诚实标注）。 */
  rollingTruncated: Set<string>
  /** 本次合并的训练流（诊断：节点页脚注「数据来自 N 个流」）。 */
  sources: ActiveFlow[]
  /** 历史锚点毫秒（渲染层展示用）。 */
  epochMs: number
  /** 最新完成轮贡献（跨课按完成时刻选）；空 = 无完成信号（消费方 → -1）。 */
  lastContrib: Map<string, number>
  /** 最新完成轮的来源（诊断；null = 无池数据）。 */
  latestRound: LatestRound | null
}

/** 窗口计算结果（切天纯投影的输出）。 */
export interface WindowAggregate {
  hist: Map<string, NodeHistory>
  sources: ActiveFlow[]
  epochMs: number
  window: NodeWindow
  latestRound: LatestRound | null
}

/** 本地日 key（'YYYY-MM-DD'）。分桶必须用**本地时区**（tmp 所在机器 TZ）。 */
export function localDayKey(ms: number): string {
  const d = new Date(ms)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

/** 本地零点毫秒（按日历加减天，避免夏令时下的 24h 误差）。 */
function dayStart(ms: number, deltaDays = 0): number {
  const d = new Date(ms)
  d.setDate(d.getDate() + deltaDays)
  d.setHours(0, 0, 0, 0)
  return d.getTime()
}

/** 窗口描述：日窗按**本地日 key** 过滤（含首含尾）；滚动窗按 `now - T` 毫秒过滤。 */
export interface NodeWindow {
  /** 窗口种类：'day' = 本地日窗（切换只投影）；'rolling' = `now - T` 滚动窗（吃事件环）。 */
  kind: 'day' | 'rolling'
  key: string
  label: string
  /** 含首的本地日 key（rolling 窗为覆盖范围的展示值）。 */
  fromDay: string
  /** 含尾的本地日 key。 */
  toDay: string
  /** 展示用窗口起点毫秒（与 epoch 取较大者）。 */
  startMs: number
  endMs: number
  /** rolling 窗的时长（毫秒）；day 窗为 null。 */
  durationMs: number | null
}

/** `?days=` 规格 → 窗口。接受 `today` / `yesterday` / `all` / `7` / `d7` / 任意 `N`（N≥1 = 最近 N 天含今天）；
 *  缺省/非法 = 今天。 */
/** 滚动窗档位（与 UI `WINDOW_OPTIONS` 同键）。 */
const ROLLING_SPECS: Record<string, { ms: number; label: string }> = {
  '30m': { ms: 30 * 60_000, label: '近 30 分钟' },
  '2h': { ms: 2 * 60 * 60_000, label: '近 2 小时' },
  // 24h（2026-10-03 用户）：首页贡献度缩略的默认窗口 —— 与自然日「今天」不同，
  // 跨日不回零（凌晨时段照样是完整的 24 小时）。⚠ 必须与 ROLLING_KEEP_MS 同步。
  '24h': { ms: 24 * 60 * 60_000, label: '近 24 小时' },
}

export function resolveWindow(spec: string, nowMs: number, epochMs: number): NodeWindow {
  const s = (spec ?? '').trim().toLowerCase()
  const todayStart = dayStart(nowMs)
  const roll = ROLLING_SPECS[s]
  if (roll) {
    const startMs = Math.max(nowMs - roll.ms, epochMs)
    return {
      kind: 'rolling',
      key: s,
      label: roll.label,
      fromDay: localDayKey(startMs),
      toDay: localDayKey(nowMs),
      startMs,
      endMs: nowMs,
      durationMs: roll.ms,
    }
  }
  let fromDayMs: number
  let toDayMs = todayStart
  let key: string
  let label: string
  if (s === 'all') {
    key = 'all'
    label = '全部'
    fromDayMs = epochMs > 0 ? dayStart(epochMs) : 0
  } else if (s === 'yesterday') {
    key = 'yesterday'
    label = '昨天'
    fromDayMs = dayStart(todayStart, -1)
    toDayMs = fromDayMs
  } else {
    // 数字或别名：`d7`/`7` → 最近 7 天；缺省/非法 = 今天。key 与 UI 的 WINDOW_OPTIONS 同形。
    const n = s === '' || s === 'today' ? 1 : s === 'd7' ? 7 : Number(s)
    const days = Number.isInteger(n) && n >= 1 ? n : 1
    key = days === 1 ? 'today' : String(days)
    label = days === 1 ? '今天' : days === 7 ? '最近 7 天' : `最近 ${days} 天`
    fromDayMs = dayStart(todayStart, -(days - 1))
  }
  return {
    kind: 'day',
    key,
    label,
    fromDay: localDayKey(fromDayMs),
    toDay: localDayKey(toDayMs),
    startMs: Math.max(fromDayMs, epochMs),
    endMs: nowMs,
    durationMs: null,
  }
}

/** 窗口投影（**纯函数、零 IO**）：同一份 agg 切不同 days = 换一个窗口看同一份数据。
 *
 *  所有展示字段都是窗口口径（唯一例外：`lastContrib` 是「最新完成轮」、与窗口无关）。 */
export function projectWindow(agg: HistoryAggregate, w: NodeWindow): WindowAggregate {
  if (w.kind === 'rolling') return projectRollingWindow(agg, w)
  const hist = new Map<string, NodeHistory>()
  const nodes = new Set<string>()
  const days: Array<[string, Map<string, DayBucket>]> = []
  for (const [day, byNode] of agg.byDay) {
    if (day < w.fromDay || day > w.toDay) continue
    days.push([day, byNode])
    for (const node of byNode.keys()) nodes.add(node)
  }
  days.sort((a, b) => (a[0] < b[0] ? -1 : 1))
  for (const node of nodes) {
    const h = emptyHistory()
    h.lastContrib = agg.lastContrib.get(node) ?? -1
    let lastError = ''
    let lastErrorMs = -Infinity
    let lastFailCell = ''
    let lastFailMs = -Infinity
    for (const [, byNode] of days) {
      const b = byNode.get(node)
      if (!b) continue
      h.ok += b.ok
      h.fail += b.fail
      h.winRollout += b.rollout
      h.winEval += b.eval
      for (const ok of b.results) {
        h.recent.push(ok)
        if (h.recent.length > 10) h.recent.shift()
      }
      for (const v of b.elapsed) pushWindowSample(h.elapsedRecent, v)
      for (const v of b.wall) pushWindowSample(h.wallRecent, v)
      // days 已按日升序：直接取最大（'-' < 任何 'YYYY-…' 字符串）。
      if (b.lastOkTs > h.lastOkTs) h.lastOkTs = b.lastOkTs
      if (b.lastTs > h.lastTs) h.lastTs = b.lastTs
      if (b.lastOkTsMs != null && (h.lastOkTsMs == null || b.lastOkTsMs >= h.lastOkTsMs)) {
        h.lastOkTsMs = b.lastOkTsMs
        h.lastOkElapsedSec = b.lastOkElapsedSec
      }
      if (b.lastFailTsMs != null && b.lastFailTsMs > lastFailMs) {
        lastFailMs = b.lastFailTsMs
        lastFailCell = b.lastFailTs
      }
      if (b.lastErrorTsMs != null && b.lastErrorTsMs > lastErrorMs) {
        lastErrorMs = b.lastErrorTsMs
        lastError = b.lastError
      }
    }
    // 「最近错误」与「最近失败」**同一个近一小时口径**（用户指令；与成功率/统计列无关）。
    // 两者一起上屏（UI 只在 lastError 非空时渲染 lastFailTs）：分开判会出现
    // 「有错误文字、时间戳却是几天前」，所以这里一并按窗口末刻判定。
    h.lastError = lastError && w.endMs - lastErrorMs <= 3_600_000 ? lastError : ''
    h.lastFailTs = lastFailCell && w.endMs - lastFailMs <= 3_600_000 ? lastFailCell : '-'
    h.lastOkTs = h.lastOkTs || '-'
    h.lastTs = h.lastTs || '-'
    h.avgElapsedSec = windowMeanSec(h.elapsedRecent)
    h.avgWallSec = windowMeanSec(h.wallRecent)
    hist.set(node, h)
  }
  return {
    hist,
    sources: agg.sources,
    epochMs: agg.epochMs,
    window: w,
    latestRound: agg.latestRound,
  }
}

/** 滚动窗投影（纯函数）：从子日事件环过滤 `[startMs, endMs]` 重建 NodeHistory。
 *
 *  计数/样本/完成率只收 `counted`（进行中那一轮不算，与日桶同规）；时间戳/错误列收全部行
 *  （可达性口径与日窗一致）。`lastContrib` 与窗口无关，仍取聚合层的值。 */
function projectRollingWindow(agg: HistoryAggregate, w: NodeWindow): WindowAggregate {
  const hist = new Map<string, NodeHistory>()
  for (const [node, ring] of agg.rolling) {
    const h = emptyHistory()
    h.lastContrib = agg.lastContrib.get(node) ?? -1
    let lastOkTs = ''
    let lastFailTs = ''
    let lastTs = ''
    let lastError = ''
    let lastOkTsMs: number | null = null
    let lastOkElapsedSec: number | null = null
    let lastFailTsMs: number | null = null
    let lastErrorTsMs: number | null = null
    for (const e of ring) {
      if (e.ms < w.startMs || e.ms > w.endMs) continue
      const ts = fmtFullTs(e.ms)
      if (ts > lastTs) lastTs = ts
      if (e.ok) {
        if (ts > lastOkTs) lastOkTs = ts
        if (lastOkTsMs == null || e.ms >= lastOkTsMs) {
          lastOkTsMs = e.ms
          lastOkElapsedSec = e.elapsedSec
        }
      } else {
        if (ts > lastFailTs) lastFailTs = ts
        if (lastFailTsMs == null || e.ms > lastFailTsMs) lastFailTsMs = e.ms
        if (e.reason && (lastErrorTsMs == null || e.ms >= lastErrorTsMs)) {
          lastError = e.reason
          lastErrorTsMs = e.ms
        }
      }
      if (!e.counted) continue
      h.recent.push(e.ok)
      if (h.recent.length > 10) h.recent.shift()
      if (e.ok) {
        h.ok++
        if (e.mode === 'eval') h.winEval++
        else h.winRollout++
        pushWindowSample(h.elapsedRecent, e.elapsedSec)
        pushWindowSample(h.wallRecent, e.wallSec)
      } else {
        h.fail++
      }
    }
    // 「最近错误/失败」与日窗同规：只在近一小时上屏。
    h.lastError = lastError && w.endMs - (lastErrorTsMs ?? 0) <= 3_600_000 ? lastError : ''
    h.lastFailTs = lastFailTs && w.endMs - (lastFailTsMs ?? 0) <= 3_600_000 ? lastFailTs : '-'
    h.lastOkTs = lastOkTs || '-'
    h.lastTs = lastTs || '-'
    h.lastOkTsMs = lastOkTsMs
    h.lastOkElapsedSec = lastOkElapsedSec
    h.avgElapsedSec = windowMeanSec(h.elapsedRecent)
    h.avgWallSec = windowMeanSec(h.wallRecent)
    hist.set(node, h)
  }
  return {
    hist,
    sources: agg.sources,
    epochMs: agg.epochMs,
    window: w,
    latestRound: agg.latestRound,
  }
}

/** 课维度投影（纯函数，矩阵的采样半边）：day 窗按日过滤合并；rolling 窗从事件环过滤。
 *  只收已完成轮；返回 `节点 → 课 → 计数`。 */
export function projectCourseBreakdown(
  agg: HistoryAggregate,
  w: NodeWindow,
): Map<string, Map<string, ContributionSplit>> {
  const out = new Map<string, Map<string, ContributionSplit>>()
  const add = (node: string, course: string, ok: boolean, mode: 'rollout' | 'eval'): void => {
    let byCourse = out.get(node)
    if (!byCourse) {
      byCourse = new Map()
      out.set(node, byCourse)
    }
    let s = byCourse.get(course)
    if (!s) {
      s = { rollout: 0, eval: 0, fail: 0 }
      byCourse.set(course, s)
    }
    if (ok) {
      if (mode === 'eval') s.eval++
      else s.rollout++
    } else {
      s.fail++
    }
  }
  if (w.kind === 'rolling') {
    for (const [node, ring] of agg.rolling) {
      for (const e of ring) {
        if (!e.counted || e.ms < w.startMs || e.ms > w.endMs) continue
        add(node, e.course, e.ok, e.mode)
      }
    }
    return out
  }
  for (const [day, byNode] of agg.byCourse) {
    if (day < w.fromDay || day > w.toDay) continue
    for (const [node, byCourse] of byNode) {
      for (const [course, s] of byCourse) {
        let t = out.get(node)?.get(course)
        if (!t) {
          t = { rollout: 0, eval: 0, fail: 0 }
          let m = out.get(node)
          if (!m) {
            m = new Map()
            out.set(node, m)
          }
          m.set(course, t)
        }
        t.rollout += s.rollout
        t.eval += s.eval
        t.fail += s.fail
      }
    }
  }
  return out
}

/**
 * 最近**已完成**轮的水位：同一训练流目录里 `training_log.jsonl` 的最后一个 `iteration`
 * 事件（只认 iteration —— hub 追加的 `job_completed` 带 it，照它取会读出「还没跑完的那一轮」）。
 *
 * 为什么需要水位：meta 账本是**逐局**追加的，进行中那一轮的行会一直变多——按「最大 it」取
 * 对齐基准，先交活的节点贡献显示得高、还没跑到的显示 0（看着像掉线），而这台机器其实健康。
 * 完成水位由训练循环写在轮末（rollout + PPO 之后），正好是「这一轮的账已经结清」的判据。
 *
 * 目录取 meta 所在目录；再退一层父母录，兼容 meta 落在 `<traj root>/traj/` 的布局。
 * 读不出（无账本 / 一次性 run 目录 / 坏文件）→ null，调用方按旧口径退化。
 */
export function lastCompletedIter(metaAbsPath: string): number | null {
  return lastCompletedIterInfo(metaAbsPath)?.it ?? null
}

/** 同 `lastCompletedIter`，另带该轮的**完成时刻**（本地 ms；`iteration.time` 解析不出 → null）。
 *
 *  §3.4：跨课按**完成时刻**选「最新完成轮」——比「该轮 meta 最后一行的 ts」准（后者是
 *  「最后结算」，与「轮完成」差一个 PPO 的时间）。 */
export function lastCompletedIterInfo(
  metaAbsPath: string,
): { it: number; atMs: number | null } | null {
  const dir = dirname(metaAbsPath)
  for (const p of [join(dir, 'training_log.jsonl'), join(dirname(dir), 'training_log.jsonl')]) {
    try {
      if (!existsSync(p)) continue
      const ev = latestIterationFromLedgerTail(readLedgerTail(p, 600))
      if (ev) return { it: ev.iter, atMs: parseTsMs(ev.time) }
    } catch {
      /* 账本不可读 → 试下一个候选 */
    }
  }
  return null
}

/**
 * 对齐基准轮的选取（纯函数，可单测）：
 *  · 有完成水位、且该轮在 meta 里**确实有行** → 用水位（进行中那一轮不进统计）；
 *  · 水位缺失/在 meta 里无行/水位为负 → 退化为 meta 最大 it（一次性 run 目录、空池）。
 *
 * 「水位在 meta 里无行」这一条是安全阀：账本与 meta 不同步（换了训练流、账本被清）时，
 * 硬用水位会把**所有**节点算成 0 贡献（全员离线）——那比退化口径糟糕得多。
 */
export function pickBaseIter(
  completedIt: number | null,
  maxIt: number,
  hasRowsAt: (it: number) => boolean,
): number {
  if (maxIt < 0 || completedIt === null || completedIt < 0) return maxIt
  return hasRowsAt(completedIt) ? completedIt : maxIt
}

/** meta 行 ts（'YYYY-MM-DD HH:MM:SS' 或 ISO）→ 毫秒；无效返回 null（测试共用）。
 *
 *  ★ 这些 ts 是**训练机本地时间、无时区后缀**（Python `strftime`）——`Date.parse` 对无时区
 *  串按本地解析，正是我们要的。**禁止**改成 `Date.UTC`（会把傍晚的局跨天错桶）。 */
export function parseTsMs(s: string | undefined): number | null {
  if (!s) return null
  const t = Date.parse(s.includes('T') ? s : s.replace(' ', 'T'))
  return Number.isFinite(t) ? t : null
}

/** 解析后的一行（紧凑形态，避免持有整份原始文本）。 */
interface ParsedRow {
  node: string
  ok: boolean
  it: number
  mode: 'rollout' | 'eval'
  ts: string
  /** 已解析的毫秒时刻（非空：解析不出的行在 `parseMetaRow` 就被丢掉，不会落进 1970 桶）。 */
  tsMs: number
  elapsedSec: unknown
  wallSec: unknown
  reason: string
}

/** 进落桶队列的一行 + 「它的计数算不算」标记：
 *  `counted=false` = 属于该流的**进行中那一轮**（时间戳照记、计数不算，见 DayBucket）。 */
interface RowEntry {
  r: ParsedRow
  counted: boolean
  /** 流目录首段 = 课名（`tmp/<课>` 与 `tmp/<课>/traj` 同归一；**不用 it**）。 */
  course: string
}

function parseMetaRow(line: string, epochStr: string): ParsedRow | null {
  try {
    const r = JSON.parse(line) as {
      node?: string
      ok?: boolean
      elapsedSec?: number
      wallSec?: number
      ts?: string
      reason?: string
      it?: number
      mode?: string
    }
    if (!r.node) return null
    const nts = normTs(r.ts)
    // 只统计「清空锚点之后」的行；ts 缺失无法判定新旧 → 忽略，保守。
    if (!r.ts || nts < epochStr) return null
    const tsMs = parseTsMs(r.ts)
    // ts 在但解析不出（坏格式）→ 既不能分天、也不能定时刻：丢掉比静默塞进
    // `localDayKey(0)` = 1970-01-01 桶诚实（二轮评审）。
    if (tsMs == null) return null
    return {
      node: r.node,
      ok: !!r.ok,
      it: typeof r.it === 'number' && Number.isInteger(r.it) ? r.it : -1,
      mode: r.mode === 'eval' ? 'eval' : 'rollout',
      ts: nts,
      tsMs,
      elapsedSec: r.elapsedSec,
      wallSec: r.wallSec,
      reason: typeof r.reason === 'string' ? r.reason : '',
    }
  } catch {
    return null
  }
}

/** 落桶上下文：日桶 / 课维度 / 滚动环共用同一遍行序（时间升序）。 */
interface BucketCtx {
  nowMs: number
  course: string
  byCourse: Map<string, Map<string, Map<string, ContributionSplit>>>
  rolling: Map<string, RollingEvent[]>
  rollingTruncated: Set<string>
}

function bucketRow(
  byDay: Map<string, Map<string, DayBucket>>,
  r: ParsedRow,
  counted: boolean,
  ctx: BucketCtx,
): void {
  const key = localDayKey(r.tsMs)
  let byNode = byDay.get(key)
  if (!byNode) {
    byNode = new Map()
    byDay.set(key, byNode)
  }
  let b = byNode.get(r.node)
  if (!b) {
    b = emptyDayBucket()
    byNode.set(r.node, b)
  }
  // ① 时间戳 / 错误列：**可达性口径**——进行中那一轮的行也照记（见 aggregateNodeHistory）。
  if (r.ok) {
    if (r.ts > b.lastOkTs) b.lastOkTs = r.ts
    if (b.lastOkTsMs == null || r.tsMs >= b.lastOkTsMs) {
      b.lastOkTsMs = r.tsMs
      b.lastOkElapsedSec =
        typeof r.elapsedSec === 'number' && r.elapsedSec > 0 ? r.elapsedSec : null
    }
  } else {
    if (r.ts > b.lastFailTs) b.lastFailTs = r.ts
    if (b.lastFailTsMs == null || r.tsMs > b.lastFailTsMs) b.lastFailTsMs = r.tsMs
    if (r.reason && (b.lastErrorTsMs == null || r.tsMs >= b.lastErrorTsMs)) {
      b.lastError = stripIsoPrefix(r.reason).slice(0, 120)
      b.lastErrorTsMs = r.tsMs
    }
  }
  if (r.ts > b.lastTs) b.lastTs = r.ts
  // ② 子日事件环（滚动窗）：**全部行都进**（时间戳/错误列是可达性口径），计数由投影层
  //    按 `counted` 过滤。环只保留 `ROLLING_KEEP_MS` 内的事件（有界），触顶丢最旧一半。
  if (r.tsMs >= ctx.nowMs - ROLLING_KEEP_MS) {
    let ring = ctx.rolling.get(r.node)
    if (!ring) {
      ring = []
      ctx.rolling.set(r.node, ring)
    }
    if (ring.length >= ROLLING_CAP) {
      ring.splice(0, Math.floor(ROLLING_CAP / 2))
      ctx.rollingTruncated.add(r.node)
    }
    ring.push({
      ms: r.tsMs,
      counted,
      ok: r.ok,
      mode: r.mode,
      elapsedSec: typeof r.elapsedSec === 'number' && r.elapsedSec > 0 ? r.elapsedSec : null,
      wallSec: typeof r.wallSec === 'number' && r.wallSec > 0 ? r.wallSec : null,
      reason: r.ok ? '' : stripIsoPrefix(r.reason).slice(0, 120),
      course: ctx.course,
    })
  }
  // ③ 计数 / 样本 / 完成率：**只认已完成轮**（数据卫生：进行中那一轮的半截计数不作数）。
  if (!counted) return
  b.results.push(r.ok)
  if (b.results.length > 10) b.results.shift()
  if (r.ok) {
    b.ok++
    if (r.mode === 'eval') b.eval++
    else b.rollout++
    pushWindowSample(b.elapsed, r.elapsedSec)
    pushWindowSample(b.wall, r.wallSec)
  } else {
    b.fail++
  }
  // ④ 课维度（矩阵的采样半边）：与日桶同过滤、同分母（只收已完成轮）。
  let bn = ctx.byCourse.get(key)
  if (!bn) {
    bn = new Map()
    ctx.byCourse.set(key, bn)
  }
  let bc = bn.get(r.node)
  if (!bc) {
    bc = new Map()
    bn.set(r.node, bc)
  }
  let cs = bc.get(ctx.course)
  if (!cs) {
    cs = { rollout: 0, eval: 0, fail: 0 }
    bc.set(ctx.course, cs)
  }
  if (r.ok) {
    if (r.mode === 'eval') cs.eval++
    else cs.rollout++
  } else {
    cs.fail++
  }
}

/** 逐流状态（**每个源各自一份 it 分布**，绝不合并成一张全局图——同一 it 在两门课里是两回事）。 */
interface FlowState {
  src: ActiveFlow
  itByNode: Map<string, Map<number, { rollout: number; eval: number }>>
  baseIt: number
  completedAtMs: number
  contribAtBase: Map<string, number>
  /** 该流入账的**完成水位**（`training_log.jsonl` 最后一个 `iteration` 的 it）；null = 一轮都没跑完。
   *
   *  ★ 2026-10-02：它是「有没有资格当**最新完成轮**」的**唯一判据**（见做 winner 选择处）。 */
  completedIt: number | null
}

interface MetaCandidate {
  path: string
  dir: string
  mtimeMs: number
  size: number
}

/** 预筛（纯函数，可单测）：整份文件 mtime 早于 epoch ⇒ 全部行都在 epoch 前，跳过。
 *  **钉在 epoch（与「看哪天」无关）** —— 预筛若随窗口变，「切天零重算」就不成立（plan §4.3）。 */
export function pruneByEpoch<T extends { mtimeMs: number }>(cands: T[], epochMs: number): T[] {
  return cands.filter((c) => c.mtimeMs >= epochMs)
}

/** 全量聚合。`nowMs` 只喂 memo 的时间下限（生产调用方一律用缺省 `Date.now()`；
 *  测试注入可控时钟，才好验「稳态每 5s 调用也必须 ≤30s 重扫一次」这条不变量）。 */
export function aggregateNodeHistory(nowMs: number = Date.now()): HistoryAggregate {
  const byDay = new Map<string, Map<string, DayBucket>>()
  const byCourse = new Map<string, Map<string, Map<string, ContributionSplit>>>()
  const rolling = new Map<string, RollingEvent[]>()
  const rollingTruncated = new Set<string>()
  const tmpDir = tmpPoolDir()
  const epochMs = poolEpochMs()
  const epochStr = fmtFullTs(epochMs)

  // 收集所有候选 meta 文件（递归扫描 tmp/ 下所有 dist-agent-meta.jsonl）。
  // 训练流的 traj_root 可以是 tmp/X（一层）或 tmp/X/traj（两层），必须递归搜索。
  const candidates: MetaCandidate[] = []
  const walk = (base: string, rel: string): void => {
    try {
      for (const d of readdirSync(join(base, rel), { withFileTypes: true })) {
        const childRel = rel ? `${rel}/${d.name}` : d.name
        if (d.isDirectory()) {
          // §366：meta 只存在于文档声明的布局——tmp/X（一层）或 tmp/X/traj（两层）。
          // 训练迭代目录（itN，p1-godai-v2 有 1893 个）不可能放 meta，跳过其子树：
          // 此前下探 3 层把整个 it 目录树扫一遍，实测 1.39s/次 → 页面 6.7s 的隐藏大头。
          const depth = childRel.split('/').length
          if (depth < 2 || (depth === 2 && d.name === 'traj')) walk(base, childRel)
        } else if (d.name === 'dist-agent-meta.jsonl') {
          const p = join(base, childRel)
          try {
            const st = statSync(p)
            candidates.push({
              path: p,
              dir: childRel.replace(/\/dist-agent-meta\.jsonl$/, ''),
              mtimeMs: st.mtimeMs,
              size: st.size,
            })
          } catch {
            /* stat failed */
          }
        }
      }
    } catch {
      /* unreadable */
    }
  }
  try {
    walk(tmpDir, '')
  } catch {
    /* tmp missing */
  }
  // 指纹要稳定：readdir 顺序不作保证，先按目录名排（对结果无影响——下面还会按 mtime 排）。
  candidates.sort((a, b) => (a.dir < b.dir ? -1 : 1))

  // ── 进程内 memo（二轮评审）──
  // 两个消费者（`api/pool` 探测层 + `snapshot-cache` 机群探测）用同一份聚合；
  // 空闲时靠指纹零重扫，训练中靠 AGG_MEMO_MIN_MS 把重扫节奏封顶。
  const fp = candidates.map((c) => `${c.dir}|${c.mtimeMs}|${c.size}`).join('\n')
  if (
    aggMemo &&
    aggMemo.root === tmpDir &&
    aggMemo.epochMs === epochMs &&
    aggMemoReusable(aggMemo, fp, nowMs)
  ) {
    return aggMemo.val
  }

  // 预筛：整份文件 mtime 早于 epoch ⇒ 全部行都在 epoch 前，跳过。
  // **钉在 epoch（与「看哪天」无关）**：预筛若随窗口变，"切天零重算"就不成立（§4.3）。
  const srcs = pruneByEpoch(candidates, epochMs)
  srcs.sort((a, b) => b.mtimeMs - a.mtimeMs)
  const sources: ActiveFlow[] = srcs.map((c) => ({ dir: c.dir, mtimeMs: c.mtimeMs, lines: 0 }))

  const flows: FlowState[] = []
  const allRows: RowEntry[] = []
  for (let i = 0; i < srcs.length; i++) {
    const src = srcs[i]!
    const flow = sources[i]!
    let lines: string[]
    try {
      if (!existsSync(src.path)) continue
      // 大文件只读尾部（诚实截断：UI 标注「该流只统计最近 N 行」）。
      if (src.size > LARGE_META_BYTES) {
        lines = readLedgerTail(src.path, LARGE_META_TAIL_LINES)
        flow.truncated = true
      } else {
        lines = readFileSync(src.path, 'utf8').split(String.fromCharCode(10))
      }
    } catch {
      continue
    }
    flow.lines = lines.filter((l) => l.trim()).length

    // ① 解析 + 逐流 it 分布（用于水位）。
    const rows: ParsedRow[] = []
    const itByNode = new Map<string, Map<number, { rollout: number; eval: number }>>()
    let maxIt = -1
    for (const line of lines) {
      if (!line.trim()) continue
      const r = parseMetaRow(line, epochStr)
      if (!r) continue
      rows.push(r)
      if (r.ok && r.it >= 0) {
        let m = itByNode.get(r.node)
        if (!m) {
          m = new Map()
          itByNode.set(r.node, m)
        }
        const c = m.get(r.it) ?? { rollout: 0, eval: 0 }
        if (r.mode === 'eval') c.eval++
        else c.rollout++
        m.set(r.it, c)
        if (r.it > maxIt) maxIt = r.it
      }
    }

    // ② 逐流水位：只用于过滤「进行中那一轮」的行（数据卫生，不是展示口径）。
    const completed = lastCompletedIterInfo(src.path)
    const baseIt = pickBaseIter(completed?.it ?? null, maxIt, (it) => {
      for (const m of itByNode.values()) if (m.has(it)) return true
      return false
    })
    // 完成时刻：优先账本 iteration.time；读不出 → 该 meta 的 mtime（兜底 + 仍参与「最新完成轮」比较）。
    const completedAtMs = completed?.atMs ?? src.mtimeMs
    const contribAtBase = new Map<string, number>()
    if (baseIt >= 0) {
      for (const [node, m] of itByNode) {
        const c = m.get(baseIt)
        contribAtBase.set(node, c ? c.rollout + c.eval : 0)
      }
    }

    // ③ **全部行都进全量行集**（时间戳/错误列是可达性口径，见 DayBucket）；`counted` 标记
    //    「这行的计数算不算」——已完成轮 = it <= baseIt_flow（无 it 的行照算已完成）。
    //    课名 = 流目录首段（`tmp/<课>` 与 `tmp/<课>/traj` 同归一；**不用 it**，§4.3）。
    const courseName = flow.dir.split('/')[0] ?? flow.dir
    for (const r of rows) {
      const counted = !(baseIt >= 0 && r.it >= 0 && r.it > baseIt)
      allRows.push({ r, counted, course: courseName })
    }

    flows.push({
      src: flow,
      itByNode,
      baseIt,
      completedAtMs,
      contribAtBase,
      completedIt: completed?.it ?? null,
    })
  }

  // ④ 时间升序后落桶（跨流合并后仍按时间有序 ⇒ results 时间升序、lastXxx 取最大才对）。
  allRows.sort((a, b) => a.r.tsMs - b.r.tsMs)
  const ctx: BucketCtx = { nowMs, course: '', byCourse, rolling, rollingTruncated }
  for (const e of allRows) {
    ctx.course = e.course
    bucketRow(byDay, e.r, e.counted, ctx)
  }

  // ⑤ 最新完成轮：**跨课按完成时刻取最新**（不是比 it 大小——it 是课程内序号，§1.1）。
  //    停摆课因完成时刻旧而自然落选，不必额外过滤。
  //
  // ★ 2026-10-02：**只有真跑完过至少一轮的流才有资格**。没跑完的流本无完成时刻，
  //   `completedAtMs` 退化成 meta 文件的 mtime，而它常常是最新写入的那个 ⇒ 它抢走 winner，
  //   `contribAtBase` 就落在它那条**没跑完**的轮上，没参与那轮的节点全被算成 0 局：
  //   数据源 = 「最新完成轮」（`lastContrib`）→ `nodeHealth(0, 并发)` 直接判 `offline` ⇒
  //   pill 显示「贡献 0 / 离线」。现场：`h4-hurt-f75` 崩溃循环（0 条 iteration）压过所有
  //   正常流，`self` 全场贡献最高（9644 局）却显示「贡献 0 / 离线」（mac=130/a95=38 正是
  //   那条 it1 的数字，逐位吻合）。
  let winner: FlowState | null = null
  for (const f of flows) {
    if (f.completedIt === null) continue // 一轮都没跑完 ⇒ 不是「完成轮」的候选
    if (winner === null || f.completedAtMs > winner.completedAtMs) winner = f
  }
  const allNodes = new Set<string>()
  for (const byNode of byDay.values()) for (const n of byNode.keys()) allNodes.add(n)
  const lastContrib =
    winner && winner.baseIt >= 0
      ? new Map([...allNodes].map((n) => [n, winner.contribAtBase.get(n) ?? 0]))
      : new Map<string, number>()
  const latestRound: LatestRound | null = winner
    ? { dir: winner.src.dir, it: winner.baseIt, completedAtMs: winner.completedAtMs }
    : null

  const out: HistoryAggregate = {
    byDay,
    byCourse,
    rolling,
    rollingTruncated,
    sources,
    epochMs,
    lastContrib,
    latestRound,
  }
  // ★ 时刻只在**计算**这一遍落，命中路径一个字都不改（见 `aggMemoReusable`）。
  aggMemo = { root: tmpDir, epochMs, fp, computedAt: nowMs, val: out }
  return out
}

/** 状态徽章判定：最近 10 次结算完成率（成功率 ≥90% · 波动 ≥70% · 异常 <70%）。
 *
 *  ⚠ 输入 `recent` 是**窗口内**的最近 ≤10 条 ⇒ 成功率随所选窗口变（plan §3.2 裁决）。 */
export function poolStatus(h: NodeHistory): 'healthy' | 'warn' | 'bad' | 'nodata' {
  const n = h.recent.length
  if (n === 0) return 'nodata'
  const okN = h.recent.filter(Boolean).length
  if (okN / n >= 0.9) return 'healthy'
  if (okN / n >= 0.7) return 'warn'
  return 'bad'
}

// ────────────────────────── 慢节点判定（2026-09-11 用户指令：慢节点误标「离线」） ──────────────────────────

/** 单局耗时阈值（秒）：结算中位超过它 = 节点算力受限（Kaggle CPU 饱和），
 *  其 agent 响应 /v1/ping 慢是常态，ping 失败 ≠ 掉线。
 *  实测（2026-09-11 活跃流 c6b-margin）：健康节点 p50 ≤2.1s（local 1.25/self 0.7/mac 1.2），
 *  慢节点 a95/a97/a98 p50 4.4-19.9s —— 8s 取「健康 p99（2.6）与慢节点 p50（4.4+）」之间的
 *  一个量级间隔，不把偶尔排队慢一局的正常节点误判进来。 */
export const SLOW_NODE_ELAPSED_SEC = 8

/** 无成功结算多久后不再算「慢（仍有贡献）」（毫秒）：30 分钟 = 覆盖慢节点在轮内的
 *  自然沉默间隙（轮节奏 10-40 分钟）；超过窗口 + ping 失败 → 真离线（训练机大概率
 *  已不派活给它，如 codeHash 过期隔离），标「离线」才是可操作的语义。 */
export const SLOW_NODE_STALE_MS = 30 * 60_000

/** 慢节点判定（纯函数，NodePills/API 共用）：ping 探测失败（online=false）时，
 *  节点近期仍在成功结算游戏（且结算耗时表明算力受限）= 慢节点而非离线。
 *  @returns true = 「慢节点」（展示为琥珀点「慢」）；false = 维持离线语义。
 *  判定输入（lastOkTsMs / lastOkElapsedSec）缺样本时保守回 false——
 *  历史从未结算的节点保持「离线」口径不变。 */
export function isSlowNode(h: {
  lastOkTsMs: number | null
  lastOkElapsedSec: number | null
  avgElapsedSec?: number | null
}): boolean {
  if (h.lastOkTsMs == null) return false
  if (Date.now() - h.lastOkTsMs > SLOW_NODE_STALE_MS) return false
  return (h.lastOkElapsedSec ?? h.avgElapsedSec ?? 0) > SLOW_NODE_ELAPSED_SEC
}

/**
 * 慢节点判定（结构化重载）：键与 isSlowNode 完全一致，从活跃流 meta 行聚合的
 * 原始行数据推导（逐行覆盖取最新一行）；测试用独立重实现的对拍基准。
 * @returns true = 「慢节点」（展示为琥珀点「慢」）；false = 维持离线语义。
 */
export function isSlowNodeRows(
  rows: Array<{
    node?: string
    ok?: boolean
    elapsedSec?: number
    ts?: string
  }>,
  id: string,
  now: number,
): boolean {
  let lastOkTsMs: number | null = null
  let lastOkElapsedSec: number | null = null
  for (const r of rows) {
    if (r.node !== id || !r.ok) continue
    const t = parseTsMs(r.ts)
    if (t == null) continue
    if (lastOkTsMs == null || t >= lastOkTsMs) {
      lastOkTsMs = t
      lastOkElapsedSec = typeof r.elapsedSec === 'number' && r.elapsedSec > 0 ? r.elapsedSec : null
    }
  }
  if (lastOkTsMs == null) return false
  if (now - lastOkTsMs > SLOW_NODE_STALE_MS) return false
  return (lastOkElapsedSec ?? 0) > SLOW_NODE_ELAPSED_SEC
}
