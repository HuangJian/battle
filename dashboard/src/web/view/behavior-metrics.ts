/** behavior-metrics.ts — 逐局指标注册表 + 统计量 + 走势图配置（纯函数，客户端安全）。
 *
 *  指标 = per-game 账本里的全部指标列：rollout 侧来自 `it<N>/per-game.json` /
 *  单局 `manifest.json` 的 `metricValues`，eval 侧来自 `eval_log.jsonl` 的
 *  `event:"eval"` 逐局行。服务端按 (iter[, wver]) 聚合六种统计量；这里只定义
 *  **展示口径**，不碰 IO。新增一个指标 = 在 `METRIC_DEFS` 加一行——趋势图、
 *  指标表、显示配置都从同一份表里长出来。
 */

// ────────────────────────── 统计量 ──────────────────────────

export type MetricStatKey = 'mean' | 'median' | 'min' | 'max' | 'p90' | 'p99'

export interface MetricStats {
  mean: number
  median: number
  min: number
  max: number
  p90: number
  p99: number
}

export const METRIC_STAT_KEYS: readonly MetricStatKey[] = [
  'mean',
  'median',
  'min',
  'max',
  'p90',
  'p99',
] as const

export const METRIC_STAT_LABEL: Record<MetricStatKey, string> = {
  mean: '均值',
  median: '中位数',
  min: '最小',
  max: '最大',
  p90: 'p90',
  p99: 'p99',
}

export const METRIC_STAT_OPTIONS: Array<{ value: MetricStatKey; label: string }> = [
  { value: 'mean', label: '均值' },
  { value: 'median', label: '中位数' },
  { value: 'min', label: '最小' },
  { value: 'max', label: '最大' },
  { value: 'p90', label: 'p90' },
  { value: 'p99', label: 'p99' },
]

export function isMetricStat(v: unknown): v is MetricStatKey {
  return (
    typeof v === 'string' &&
    (v === 'mean' || v === 'median' || v === 'min' || v === 'max' || v === 'p90' || v === 'p99')
  )
}

export function parseMetricStat(raw: string | null | undefined): MetricStatKey {
  return isMetricStat(raw) ? raw : 'mean'
}

/** 线性插值分位（与 numpy 默认 percentile 同口径；n=1 时恒该值）。 */
function percentile(sorted: number[], p: number): number {
  const n = sorted.length
  if (n === 0) return Number.NaN
  if (n === 1) return sorted[0]
  const pos = (n - 1) * p
  const lo = Math.floor(pos)
  const hi = Math.ceil(pos)
  if (lo === hi) return sorted[lo]
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo)
}

/** 逐局值 → 六种统计量；非有限值跳过，空数组 = null（缺读数，不编 0）。 */
export function computeMetricStats(values: Array<number | null | undefined>): MetricStats | null {
  const xs: number[] = []
  for (const v of values) {
    if (typeof v === 'number' && Number.isFinite(v)) xs.push(v)
  }
  if (xs.length === 0) return null
  xs.sort((a, b) => a - b)
  let sum = 0
  for (const v of xs) sum += v
  return {
    mean: sum / xs.length,
    median: percentile(xs, 0.5),
    min: xs[0],
    max: xs[xs.length - 1],
    p90: percentile(xs, 0.9),
    p99: percentile(xs, 0.99),
  }
}

// ────────────────────────── 指标定义 ──────────────────────────

/** 展示格式：整数 / 一位小数 / 0–1 百分比。 */
export type BehaviorMetricFmt = 'int' | 'dec1' | 'pct'

/** [key, 展示名, 格式, hover 口径]；key 与 `biz.reward_library.METRICS` /
 *  `tools/sim/export-rl-rollout.ts::METRICS_COLUMN_NAMES` 逐字一致。
 *  末尾两个是 eval 专属读数（rollout 侧没有对应列，缺值显示 —）。 */
const METRIC_DEFS = [
  ['ticks', '总时长', 'int', '本局总 tick 数'],
  ['kills', '击杀数', 'int', '本局击杀敌车数'],
  ['lives', '命数', 'int', '终局剩余命数'],
  ['playerHits', '受击', 'int', '玩家被命中次数'],
  ['playerDamageTaken', '承伤', 'int', '玩家累计承伤'],
  ['playerShots', '开火', 'int', '玩家累计开火次数'],
  ['enemyHits', '命中', 'int', '玩家命中敌车次数'],
  ['powerUpsCollected', '拾取', 'int', '道具拾取总数'],
  ['powerUpsSpawned', '掉落', 'int', '道具掉落总数'],
  ['stuckTicks', '卡滞', 'int', '卡滞累计 tick 数'],
  ['starsCollected', '星星', 'int', '星星拾取数'],
  ['baseAlive', '基地存活', 'pct', '终局基地是否存活（1/0）'],
  ['baseWallTotal', '基地墙总数', 'int', '基地环墙总格数'],
  ['baseWallIntact', '基地墙完好', 'int', '终局基地环墙完好格数'],
  ['basePressureSum', '基地压力累计', 'int', '基地压力逐采样累计值'],
  ['basePressureSamples', '基地压力采样', 'int', '基地压力采样次数'],
  ['firstKillTick', '首杀 tick', 'int', '首次击杀的 tick；-1 = 本局无击杀'],
  ['playerDeaths', '阵亡', 'int', '玩家阵亡次数'],
  ['cellsVisited', '到访格', 'int', '玩家到访过的格子数'],
  ['playerLevel', '等级', 'int', '终局玩家等级'],
  ['enemyTotal', '敌总数', 'int', '本关敌人总数'],
  ['puSpawnBomb', '炸弹掉落', 'int', '炸弹道具掉落数'],
  ['puSpawnTank', '坦克掉落', 'int', '加命道具掉落数'],
  ['puSpawnFreeze', '冻结掉落', 'int', '冻结道具掉落数'],
  ['puSpawnShield', '护盾掉落', 'int', '护盾道具掉落数'],
  ['puGotBomb', '炸弹拾取', 'int', '炸弹道具拾取数'],
  ['puGotTank', '加命拾取', 'int', '加命道具拾取数'],
  ['puGotFreeze', '冻结拾取', 'int', '冻结道具拾取数'],
  ['puGotShield', '护盾拾取', 'int', '护盾道具拾取数'],
  ['puSpawnStar', '星星掉落', 'int', '星星道具掉落数'],
  ['clearTick', '清场 tick', 'int', '敌人首次全灭的 tick；-1 = 未清场'],
  ['killsBasic', '击杀·基础', 'int', '击杀基础敌车数'],
  ['killsFast', '击杀·快速', 'int', '击杀快速敌车数'],
  ['killsPower', '击杀·火力', 'int', '击杀火力敌车数'],
  ['killsArmor', '击杀·装甲', 'int', '击杀装甲敌车数'],
  ['hitsBasic', '命中·基础', 'int', '命中基础敌车数'],
  ['hitsFast', '命中·快速', 'int', '命中快速敌车数'],
  ['hitsPower', '命中·火力', 'int', '命中火力敌车数'],
  ['hitsArmor', '命中·装甲', 'int', '命中装甲敌车数'],
  ['puGotOther', '其它拾取', 'int', '四桶之外的其它道具拾取数'],
  ['pickupDist', '拾取距离', 'int', '到最近可达拾取中心的路径距离；-1 = 无'],
  ['playerHpRatio', '玩家血量', 'pct', '终局玩家血量比例（0–1）'],
  ['dangerTicks', '危险时长', 'int', 'hpRatio < 0.4 的累计 tick 数（越低越稳）'],
  ['threatTicks', '威胁时长', 'int', '处于威胁射界内的累计 tick 数'],
  ['dmgFirst600', '前600承伤', 'int', '开局前 600 tick 的承伤总量'],
  ['backHits', '背击', 'int', '背刺命中次数'],
  ['sideHits', '侧击', 'int', '侧击命中次数'],
  ['frontHitsExempt', '正面豁免', 'int', '正面命中但处于冻/盾豁免的次数'],
  ['farHits', '流弹命中', 'int', '开火时物理打不到、目标自己走进弹道的命中数'],
  ['geoFallback', '几何回退', 'int', '开火记录反查失败的命中数'],
  ['onLaneTicks', '在线时长', 'int', '在敌坦射界线上的累计 tick 数'],
  ['onLaneExemptTicks', '在线豁免', 'int', '在线但处于冻结/护盾豁免的累计 tick 数'],
  ['onLaneMoveTicks', '在线移动', 'int', '在线上且中心格发生变化的累计 tick 数'],
  ['onLaneHoldFireTicks', '在线憋火', 'int', '在线且静止/未移动但开火的累计 tick 数'],
  ['stopTicks', '停止时长', 'int', '无移动输出的累计 tick 数'],
  ['fireHeldTicks', '按住开火', 'int', '开火输出为真的累计 tick 数'],
  ['idleTicks', '静止时长', 'int', 'player.moving=false 的累计 tick 数'],
  ['enemyDist', '敌距', 'dec1', '最近已激活敌车中心距（px）；-1 = 无'],
  ['nearEnemy4Ticks', '近敌时长', 'int', '最近敌距 ≤4 格的累计 tick 数'],
  ['damageBursts', '伤害簇', 'int', '相邻两笔扣血间隔 ≤120t 的次数'],
  ['maxDamage120', '120t最大承伤', 'int', '任意 120t 滑窗内最大累计承伤'],
  ['damageWhileLow', '低血承伤', 'int', '低血期（hpRatio<0.4）承受的伤害累计'],
  ['encl1Ticks', '单轴包围', 'int', '同拍同行/同列敌车数 = 1 的 tick 数'],
  ['encl2Ticks', '双轴包围', 'int', '同拍同行/同列敌车数 = 2 的 tick 数'],
  ['encl3pTicks', '三轴包围', 'int', '同拍同行/同列敌车数 ≥3 的 tick 数'],
  ['enclMax', '包围峰值', 'int', '本局同拍包围敌数峰值'],
  ['aimHits', '瞄准命中', 'int', '玩家弹命中数（含致死）'],
  ['aimHitDistSum', '命中距离和', 'dec1', '命中拍切比雪夫格距之和'],
  ['aimBricks', '拆砖', 'int', '真拆砖/破钢次数'],
  ['aimIgnited', '弹弹对消', 'int', '玩家弹与敌弹对消次数'],
  ['aimMisses', '射失', 'int', '未命中（含打钢/出界/局末在飞）次数'],
  ['hurtWeight', '火线承伤', 'int', '敌弹火线承伤加权累计'],
  ['enclWeightTicks', '包围加权', 'int', '包围敌数加权 tick 累计（拌入豁免）'],
  ['cornerWeightTicks', '角落加权', 'int', '四角距离加权 tick 累计（拌入豁免）'],
  ['nearSqSum', '近距平方和', 'int', '近敌距离核 (3−d)² 的逐拍累计'],
  ['postHitLaneTicks', '受击后在线', 'int', '被击中后仍留在该源火线轴上的 tick 数'],
  ['freezeShieldWindowTicks', '冻/盾窗口', 'int', '冻结 ∪ 道具盾的累计 tick 数'],
  ['hitsInFreezeShieldWindow', '窗口内命中', 'int', '冻/盾窗口内的玩家命中数'],
  ['deathDerivedShieldTicks', '死亡派生盾', 'int', '复活盾/星盾 grace 的累计 tick 数'],
  ['thBreach', '破墙射界', 'int', '基地破墙射界打开、炮口未对准的累计 tick 数'],
  ['thBreachAimed', '破墙对准', 'int', '基地破墙射界打开且炮口已对准的累计 tick 数'],
  ['thDirect', '直射基地', 'int', '直接射界覆盖基地的累计 tick 数'],
  ['thDirectAimed', '直射对准', 'int', '直射射界且炮口对准鹰所在轴的累计 tick 数'],
  ['thShotsAtBaseCum', '朝基地开火', 'int', '敌弹朝鹰/环墙发射的累计次数'],
  ['thBulletsAtBase', '在飞弹', 'int', '当前在飞、目标为鹰/环墙的敌弹数'],
  ['thCostBreach', '破墙成本', 'int', '破墙射界的累计成本流'],
  ['thCostBreachAimed', '破墙对准成本', 'int', '破墙且对准的累计成本流'],
  ['thCostDirect', '直射成本', 'int', '直射基地的累计成本流'],
  ['thCostDirectAimed', '直射对准成本', 'int', '直射且对准的累计成本流'],
  ['wallPressTicks', '贴墙时长', 'int', '贴墙推进的累计 tick 数'],
  ['aliveTicks', '存活时长', 'int', '玩家存活累计 tick 数（eval 专属）'],
  ['postThawDamageRate', '解冻承伤', 'pct', '解冻后 60 拍内的承伤占比（eval 专属）'],
] as const

export type BehaviorMetricKey = (typeof METRIC_DEFS)[number][0]

export interface BehaviorMetricDef {
  /** 逐局账本的键名（与 metrics 族同名）。 */
  key: BehaviorMetricKey
  label: string
  /** 表头 / 图标题的 hover 口径。 */
  title: string
  /** 展示格式：整数 tick / 一位小数 / 0–1 百分比。 */
  fmt: BehaviorMetricFmt
}

/** 完整逐局指标集（90 个 rollout metrics + 2 个 eval 专属读数）。 */
export const BEHAVIOR_METRICS: readonly BehaviorMetricDef[] = METRIC_DEFS.map(
  ([key, label, fmt, title]) => ({ key, label, fmt, title }),
)

/** @deprecated 旧名（本文件最初只接 eval）；新代码用 `BEHAVIOR_METRICS`。 */
export const EVAL_BEHAVIOR_METRICS = BEHAVIOR_METRICS

export const BEHAVIOR_METRIC_KEYS: readonly BehaviorMetricKey[] = BEHAVIOR_METRICS.map((m) => m.key)

/** @deprecated 旧名；新代码用 `BEHAVIOR_METRIC_KEYS`。 */
export const EVAL_BEHAVIOR_METRIC_KEYS = BEHAVIOR_METRIC_KEYS

/** 逐指标统计量偏好（key = 指标 key；默认全部均值）。 */
export type BehaviorMetricStatMap = Record<BehaviorMetricKey, MetricStatKey>

export const DEFAULT_BEHAVIOR_METRIC_STATS: BehaviorMetricStatMap = Object.fromEntries(
  BEHAVIOR_METRICS.map((m) => [m.key, 'mean' as MetricStatKey]),
) as BehaviorMetricStatMap

/** localStorage → 逐指标统计量。
 *  兼容旧版单值（如 `'median'`，铺到全部指标）；新格式 `{metricKey: stat}`；
 *  坏 JSON / 未知键 / 非法值退默认均值。 */
export function parseBehaviorMetricStats(raw: string | null | undefined): BehaviorMetricStatMap {
  const out = { ...DEFAULT_BEHAVIOR_METRIC_STATS }
  if (!raw) return out
  if (isMetricStat(raw)) {
    for (const m of BEHAVIOR_METRICS) out[m.key] = raw
    return out
  }
  try {
    const o = JSON.parse(raw) as Record<string, unknown> | null
    if (!o || typeof o !== 'object') return out
    for (const m of BEHAVIOR_METRICS) {
      const v = o[m.key]
      if (isMetricStat(v)) out[m.key] = v
    }
  } catch {
    /* 坏 JSON 退默认 */
  }
  return out
}

const BEHAVIOR_BY_KEY = new Map<string, BehaviorMetricDef>(BEHAVIOR_METRICS.map((m) => [m.key, m]))

export function behaviorMetricDef(key: string): BehaviorMetricDef | undefined {
  return BEHAVIOR_BY_KEY.get(key)
}

/** 统计值展示（表格与趋势轴共用；缺值 = —，不冒充 0）。 */
export function fmtMetricStat(v: number | null | undefined, def: BehaviorMetricDef): string {
  if (v == null || !Number.isFinite(v)) return '—'
  if (def.fmt === 'pct') return `${(v * 100).toFixed(1)}%`
  if (def.fmt === 'dec1') return v.toFixed(1)
  return Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1)
}

// ────────────────────────── 走势图显示配置 ──────────────────────────

/** 现有六项（默认全部显示；它们不是逐局分布，统计值切换只作用于逐局指标）。 */
export const TREND_BASE_CHARTS = [
  { key: 'winRate', label: '胜率' },
  { key: 'dmgPerKill', label: '承伤/杀' },
  { key: 'winTicks', label: '胜局耗时' },
  { key: 'kills', label: '击杀' },
  { key: 'winHp', label: '胜局残血' },
  { key: 'pu', label: '道具' },
] as const

export type TrendBaseKey = (typeof TREND_BASE_CHARTS)[number]['key']
/** 逐局指标走势键：带 `bm:` 前缀，避开与六项基础图（如 `kills`）的键冲突。 */
export type BehaviorTrendKey = `bm:${BehaviorMetricKey}`
export type TrendChartKey = TrendBaseKey | BehaviorTrendKey

export function behaviorTrendKey(key: BehaviorMetricKey): BehaviorTrendKey {
  return `bm:${key}`
}

export const DEFAULT_TREND_VISIBILITY: Record<TrendChartKey, boolean> = Object.fromEntries([
  ...TREND_BASE_CHARTS.map((c) => [c.key, true]),
  ...BEHAVIOR_METRICS.map((m) => [behaviorTrendKey(m.key), false]),
]) as Record<TrendChartKey, boolean>

/** localStorage JSON → 显示表；缺键/坏值一律退默认，未知键忽略（旧版本残留）。 */
export function parseTrendVisibility(
  raw: string | null | undefined,
): Record<TrendChartKey, boolean> {
  const out: Record<TrendChartKey, boolean> = { ...DEFAULT_TREND_VISIBILITY }
  if (!raw) return out
  try {
    const o = JSON.parse(raw) as Record<string, unknown> | null
    if (!o || typeof o !== 'object') return out
    for (const k of Object.keys(out) as TrendChartKey[]) {
      if (typeof o[k] === 'boolean') out[k] = o[k]
    }
  } catch {
    /* 坏 JSON 退默认 */
  }
  return out
}

/** 浮窗里六条统计曲线的配色（与卡片单图区分；颜色只用于 SVG 属性）。 */
export const METRIC_STAT_COLORS: Record<MetricStatKey, string> = {
  mean: 'var(--accent)',
  median: 'var(--green)',
  min: 'var(--yellow)',
  max: 'var(--red)',
  p90: '#8b5cf6',
  p99: '#0ea5e9',
}
