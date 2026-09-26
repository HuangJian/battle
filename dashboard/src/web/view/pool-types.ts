/** pool-types.ts — /api/pool 视图类型（节点历史 / 训练流 / 窗口 / selfStatus）。
 *
 *  ★ 2026-09-26（plan/nodes-decouple-from-course.plan.md）：节点统计与课程解耦 ——
 *   · 视图合并 tmp/ 下**所有**训练流，按**本地日**窗口投影；
 *   · 课程内序号 `it` **不再出现在任何展示字段**（只作服务端内部过滤器）；
 *   · 「上轮贡献」→ **窗口内局数**（`winRollout` / `winEval` 两列），「状态」列改名「成功率」。
 */
// ────────────────────────── /api/pool 视图类型 ──────────────────────────

export type NodePoolStatus = 'healthy' | 'warn' | 'bad' | 'noping' | 'nodata' | 'disabled'

export interface NodeHistoryRow {
  id: string
  /** node = rl-config 节点；local = 本机直跑槽位。 */
  kind: 'node' | 'local'
  status: NodePoolStatus
  okN: number
  recentN: number
  /** 展示用规格：'8 核' / '2 槽' / '-'. */
  spec: string
  /** 版本是否与本机一致；null = 不可比（local/未起）。 */
  versionOk: boolean | null
  /** 短版本号（前 7 位）。 */
  version: string
  /** 训练机侧短 hash（前 7 位）——F5（plan/dist-codehash-stale-fix.md）：stale 诊断。 */
  versionLocal: string
  pingMs: number | null
  ok: number
  fail: number
  /** ★窗口内成功局数（rollout）——取代旧的「对齐轮 contribRollout」。 */
  winRollout: number
  /** ★窗口内成功局数（eval）。 */
  winEval: number
  /** 最近**完成**轮（跨课按完成时刻选）该节点成功局数（rollout + eval）；-1 = 无池数据。 */
  lastContrib: number
  avgElapsedSec: number | null
  /** 训练机派发→结算墙钟滑动均值（含网络/轮询）；null = 窗口内无 wallSec。 */
  avgWallSec: number | null
  lastOkTs: string
  lastFailTs: string
  /** 已剥离 agent ISO 前缀（GLM-U3）。 */
  lastError: string
  recent: boolean[]
}

/** 本次合并的训练流（诊断：脚注「数据来自 N 个训练流」）。 */
export interface ActiveFlowInfo {
  dir: string
  mtimeMs: number
  lines: number
  /** 该流因体积过大只读了尾部（诚实截断）。 */
  truncated?: boolean
}

/** 会话视图的窗口（本地日）。 */
export interface PoolWindowInfo {
  key: string
  label: string
  startMs: number
  endMs: number
}

/** 节点页窗口选项（UI Segmented；服务端 `?days=` 同键）。 */
export const WINDOW_OPTIONS = [
  { key: 'today', label: '今天' },
  { key: 'yesterday', label: '昨天' },
  { key: '7', label: '7 天' },
  { key: 'all', label: '全部' },
] as const

export type PoolWindowKey = (typeof WINDOW_OPTIONS)[number]['key']

export interface SelfStatus {
  workers: number
  inflight: number
  gamesDoneTotal: number
  diskFreeMB: number | null
  lastError: string | null
  uptimeSec: number | null
  resultCacheItems: number
  resultCacheBytes: number
  recentFailed: number
}

export interface PoolView {
  /** **探测层**（ping/池历史/codeHash/selfStatus）的算完时刻（ms）。
   *  视图是两层拼的：结构（节点行/order/enabled/local 槽数）每请求现算、无缓存；
   *  这个字段只标探测列的新鲜度 —— 也是客户端判定「后台重算落地了」的判据。 */
  cachedAt: number
  /** 操作员课程回显（`?course=` 已移除；池视图本身与课程无关）。 */
  course: string
  epochMs: number
  /** 合并的训练流（诊断）。 */
  sources: ActiveFlowInfo[]
  /** 本次投影的窗口（本地日；切天只改它，不重算聚合）。 */
  window: PoolWindowInfo
  nodes: NodeHistoryRow[]
  local: NodeHistoryRow | null
  selfStatus: SelfStatus | null
  localHash: string
}
