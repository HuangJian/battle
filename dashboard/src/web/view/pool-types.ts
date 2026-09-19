/** pool-types.ts — /api/pool 视图类型（节点历史 / 活跃流 / selfStatus）。 */
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
  /** 训练机侧短 hash（前 7 位）——F5（plan/dist-codehash-stale-fix.md）：stale 诊断
   *  Pill 展示两侧 hash，一眼看出差异在哪一侧。 */
  versionLocal: string
  pingMs: number | null
  ok: number
  fail: number
  /** 上轮贡献合计（rollout + eval）。 */
  contrib: number
  /** F5（plan/dist-codehash-stale-fix.md）：上轮贡献按 mode 分桶——"只跑 eval 的
   * 节点"不再看起来在贡献 rollout。 */
  contribRollout: number
  contribEval: number
  lastIter: number
  globalMaxIt: number
  avgElapsedSec: number | null
  /** 训练机派发→结算墙钟滑动均值（含网络/轮询）；null = meta 尚无 wallSec。 */
  avgWallSec: number | null
  lastOkTs: string
  lastFailTs: string
  /** 已剥离 agent ISO 前缀（GLM-U3）。 */
  lastError: string
  recent: boolean[]
}

export interface ActiveFlowInfo {
  dir: string
  mtimeMs: number
  lines: number
}

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
  /** 服务端结果缓存的构建时刻（ms）。 */
  cachedAt: number
  course: string
  epochMs: number
  activeFlow: ActiveFlowInfo | null
  nodes: NodeHistoryRow[]
  local: NodeHistoryRow | null
  selfStatus: SelfStatus | null
  localHash: string
}
