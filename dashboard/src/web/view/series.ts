/** series.ts — 指标序列切分与逐指标序列抽取（趋势图数据源）。 */
import { behaviorMetricDef, type BehaviorMetricKey, type MetricStatKey } from './behavior-metrics'
import { IterRow } from './metric-types'

// ────────────────────────── 纯函数：指标系列 ──────────────────────────

export interface Series {
  key: string
  label: string
  vals: number[]
  /** 与 vals 逐位对齐的迭代序号（时间正序）。 */
  iters: number[]
  /** 稀疏口径（eval / 行为统计）：范围档位按有效点截取，而不是按迭代轮数。 */
  sparse?: boolean
}

/** 走势图范围档位：全量 / 最近 60% 点 / 最近 30% 点。 */
export type TrendRange = 'all' | '60pct' | '30pct'

export const TREND_RANGE_OPTIONS: Array<{ value: TrendRange; label: string }> = [
  { value: 'all', label: '全量' },
  { value: '60pct', label: '最近60%' },
  { value: '30pct', label: '最近30%' },
]

export function isTrendRange(v: string | null | undefined): v is TrendRange {
  return v === 'all' || v === '60pct' || v === '30pct'
}

/** 走势图数据源档位（用户 2026-09-20）：全部 = rollout 实线 + eval 橙线叠加（原行为）；
 *  rollout / eval = 只画一条——两种口径的量级/采样密度差很远（同图里 eval 常被压成孤点），
 *  想看单边趋势时叠加反而是噪声。 */
export type TrendSource = 'all' | 'rollout' | 'eval'

/** 纵坐标档位（用户 2026-11-02）：绝对值 / 百分比。只作用于 Hero 的击杀·胜局残血·道具三张图。
 *  三张图各有自己的「两种读数」语义（见 `metricSeries` 的 `*Pct` / `*Abs` 键），
 *  档位表只是「选哪一条序列」的开关，不在渲染层做单位换算。 */
export type TrendScale = 'abs' | 'pct'
export interface TrendScales {
  kills: TrendScale
  winHp: TrendScale
  pu: TrendScale
}
/** 默认档位 = 改造前的样子：击杀/残血本来就是百分比，道具本来就是绝对数。 */
export const TREND_SCALES_DEFAULT: TrendScales = { kills: 'pct', winHp: 'pct', pu: 'abs' }
export const TREND_SCALE_OPTIONS: Array<{ value: TrendScale; label: string }> = [
  { value: 'abs', label: '数值' },
  { value: 'pct', label: '%' },
]

export function isTrendScale(v: unknown): v is TrendScale {
  return v === 'abs' || v === 'pct'
}

/** localStorage 值 → 三张图的纵坐标档位（坏 JSON / 非法值逐键退默认，不上屏）。 */
export function parseTrendScales(raw: string | null | undefined): TrendScales {
  if (!raw) return { ...TREND_SCALES_DEFAULT }
  try {
    const o = JSON.parse(raw) as Record<string, unknown> | null
    return {
      kills: isTrendScale(o?.kills) ? o.kills : TREND_SCALES_DEFAULT.kills,
      winHp: isTrendScale(o?.winHp) ? o.winHp : TREND_SCALES_DEFAULT.winHp,
      pu: isTrendScale(o?.pu) ? o.pu : TREND_SCALES_DEFAULT.pu,
    }
  } catch {
    return { ...TREND_SCALES_DEFAULT }
  }
}

/** 数据源档位选项（顺序即上屏顺序；view 层给词，组件只渲染——`TrendRange` 同类）。 */
export const TREND_SOURCE_OPTIONS: Array<{ value: TrendSource; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'rollout', label: 'rollout' },
  { value: 'eval', label: 'eval' },
]

/** 是否为合法数据源档位：localStorage 的值必须过这道闸（非法值退默认，不上屏）。 */
export function isTrendSource(v: string | null | undefined): v is TrendSource {
  return v === 'all' || v === 'rollout' || v === 'eval'
}

/** eval 源稀疏序列（干净评估只在部分迭代出现，中间轮 = NaN 缺口）：
 *  「最近 N」语义 = 最近 N 个有效评估点。双序列叠加时由 TrendChart 按主序列 iters 对齐。 */
const SPARSE_SERIES_KEYS: ReadonlySet<string> = new Set([
  'eval',
  'evalTicks',
  'evalKills',
  'evalKillsPct',
  'evalKillsAbs',
  'evalPu',
  'evalPuPct',
  'evalWinTicks',
  'evalWinHp',
  'evalWinHpAbs',
  'evalDmgPerKill',
  'evalLossTicks',
])

/**
 * 按范围档位截取序列。eval 源序列（eval 胜率 / 行为统计）在有限轮里常带 NaN 缺口，
 * 其「最近 N」语义 = 最近 N 个**有效**评估点（而非最近 N 轮迭代），
 * 避免窗口内全是 NaN 画空图；「全量」档同样只保留有效点。
 * 其它指标（含 rollout 口径的胜局耗时/胜局残血/承伤·杀/败局耗时）= 最近 N 轮迭代（按 iter 截取）。
 * `60pct` / `30pct` = 最近 60% / 30% 的可见点（向上取整，至少 1 个）。
 */
export function sliceSeries(series: Series, range: TrendRange): Series {
  const { key, label, vals, iters, sparse } = series
  if (vals.length === 0) return { key, label, vals, iters, sparse }
  const tail = (n: number): number =>
    range === 'all' ? n : Math.max(1, Math.ceil(n * (range === '60pct' ? 0.6 : 0.3)))
  // eval / 行为统计：先过滤到有效评估点，再按档位截取。
  if (sparse || SPARSE_SERIES_KEYS.has(key)) {
    const pairs = vals.map((v, i) => ({ v, it: iters[i] })).filter((p) => Number.isFinite(p.v))
    const sliced = pairs.slice(-tail(pairs.length))
    return {
      key,
      label,
      vals: sliced.map((p) => p.v),
      iters: sliced.map((p) => p.it),
      sparse: true,
    }
  }
  const n = tail(vals.length)
  return {
    key,
    label,
    vals: range === 'all' ? vals : vals.slice(-n),
    iters: range === 'all' ? iters : iters.slice(-n),
  }
}

/** 逐局指标的数据口径：rollout 读 `actuals.behaviorMetrics`，eval 读 `evalData.behaviorMetrics`。 */
export type BehaviorMetricSource = 'rollout' | 'eval'

/** 单个逐局指标 × 一个统计值 × 一个数据源 → 趋势序列（缺轮 = NaN，不补 0）。 */
export function behaviorMetricSeries(
  rows: IterRow[],
  key: BehaviorMetricKey,
  stat: MetricStatKey,
  source: BehaviorMetricSource = 'eval',
): Series {
  const chrono = [...settledRows(rows)].sort((a, b) => a.iter - b.iter)
  const def = behaviorMetricDef(key)
  return {
    key: `behavior-${key}-${stat}-${source}`,
    label: `${def?.label ?? key}${source === 'eval' ? ' · eval' : ''}`,
    vals: chrono.map((r) => {
      const st =
        source === 'rollout'
          ? r.actuals?.behaviorMetrics?.[key]
          : r.evalData?.behaviorMetrics?.[key]
      return st ? st[stat] : Number.NaN
    }),
    iters: chrono.map((r) => r.iter),
    sparse: true,
  }
}

/** 趋势图只画**已结算**的轮（用户 2026-11-04）：一轮的 eval 未结束时，它的点不进序列 ——
 *  该轮还没结算（等 PPO）或迟到评估仍在跑时，rollout 读数虽然齐，与 eval 口径却不可比，
 *  画上去会被当成趋势拐点 / 与琥珀线错位对照。
 *
 *  两种「未结束」都排除：
 *   · `pendingPpo` —— 只有 `rollout_end` 行（第 ⑥ 步刚收官，eval 还没派/没回）；
 *   · `evalInFlight` —— `iteration` 行已落，但该轮 eval 的逐局行有了、summary 未结算。
 *  指标表**不过滤**（那是实时观察口，`PPO 中` 徽标已经标明半轮）——两处口径不同是刻意的。 */
function settledRows(rows: IterRow[]): IterRow[] {
  return rows.filter((r) => !r.pendingPpo && !r.evalInFlight)
}

/** 指标表列集（时间正序；actuals/eval 缺轮断点 = NaN 会被 sparkPoints 过滤）。
 *  返回全量时序（不做 20 轮截断），由 sliceSeries / TrendChart 按范围档位截取。
 *  **先丢掉未结算轮**（见 `settledRows`）：趋势图不画半轮。 */
export function metricSeries(rows: IterRow[]): Series[] {
  const chrono = [...settledRows(rows)].sort((a, b) => a.iter - b.iter)
  const iters = chrono.map((r) => r.iter)
  return [
    { key: 'winRate', label: '胜率', vals: chrono.map((r) => r.winRate), iters },
    { key: 'scoreMean', label: '得分', vals: chrono.map((r) => r.scoreMean), iters },
    { key: 'kl', label: 'KL', vals: chrono.map((r) => r.kl), iters },
    { key: 'entropy', label: '熵', vals: chrono.map((r) => r.entropy), iters },
    {
      key: 'eval',
      label: 'eval 胜率',
      vals: chrono.map((r) => (r.evalData ? (r.evalData.winRate as number) : Number.NaN)),
      iters,
    },
    {
      key: 'kills',
      label: '击杀',
      // 歼灭率 0–1 = Σkills/ΣenemyTotal；缺敌数时回退每局平均击杀（非 0-1，仅作趋势形状）
      vals: chrono.map((r) =>
        r.actuals
          ? r.actuals.killRate != null
            ? r.actuals.killRate
            : r.actuals.games > 0
              ? r.actuals.totalKills / r.actuals.games
              : Number.NaN
          : Number.NaN,
      ),
      iters,
    },
    {
      key: 'killsPct',
      label: '击杀',
      // 歼灭率 0–1 = Σkills/ΣenemyTotal（「百分比」档）。缺敌数 = 无读数，
      // **不拿每局击杀冒充**（那会让 12 显示成 1200%）；要形状请切「数值」档。
      vals: chrono.map((r) =>
        r.actuals && r.actuals.killRate != null ? r.actuals.killRate : Number.NaN,
      ),
      iters,
    },
    {
      key: 'killsAbs',
      label: '击杀',
      // 每局平均击杀（「数值」档）。
      vals: chrono.map((r) =>
        r.actuals && r.actuals.games > 0 ? r.actuals.totalKills / r.actuals.games : Number.NaN,
      ),
      iters,
    },
    {
      key: 'pu',
      label: '道具',
      vals: chrono.map((r) =>
        r.actuals && r.actuals.games > 0 ? r.actuals.totalPU / r.actuals.games : Number.NaN,
      ),
      iters,
    },
    {
      key: 'puPct',
      label: '道具',
      // 拾取率 0–1 = Σ拾取 / Σ掉落（「百分比」档）；spawn 未知 = 无读数（不编分母）。
      vals: chrono.map((r) =>
        r.actuals && r.actuals.totalPUSpawn != null && r.actuals.totalPUSpawn > 0
          ? r.actuals.totalPU / r.actuals.totalPUSpawn
          : Number.NaN,
      ),
      iters,
    },
    {
      key: 'avgTicks',
      label: '耗时',
      vals: chrono.map((r) => (r.actuals ? r.actuals.avgTicks : Number.NaN)),
      iters,
    },
    {
      key: 'winTicks',
      label: '胜局耗时',
      // 胜局平均耗时（ticks，rollout 胜局口径，所有 iter 采样）；无胜局轮 = NaN 缺口。
      vals: chrono.map((r) =>
        r.actuals && r.actuals.avgWinTicks != null ? r.actuals.avgWinTicks : Number.NaN,
      ),
      iters,
    },
    {
      key: 'winHp',
      label: '胜局残血',
      // 胜局残血 / 单命满血（可 >1）；缺时回退绝对 hp（趋势形状仍可读）
      vals: chrono.map((r) =>
        r.actuals
          ? r.actuals.avgResidualHpPct != null
            ? r.actuals.avgResidualHpPct
            : r.actuals.avgResidualHp != null
              ? r.actuals.avgResidualHp
              : Number.NaN
          : Number.NaN,
      ),
      iters,
    },
    {
      key: 'winHpAbs',
      label: '胜局残血',
      // 胜局平均残血 hp（剩余命每命计满额；「数值」档）。
      vals: chrono.map((r) =>
        r.actuals && r.actuals.avgResidualHp != null ? r.actuals.avgResidualHp : Number.NaN,
      ),
      iters,
    },
    {
      key: 'dmgPerKill',
      label: '承伤/杀',
      // 每杀承伤占比 0–1；缺容量时回退绝对 dmgPerKill
      vals: chrono.map((r) =>
        r.actuals
          ? r.actuals.dmgPerKillPct != null
            ? r.actuals.dmgPerKillPct
            : r.actuals.dmgPerKill != null
              ? r.actuals.dmgPerKill
              : Number.NaN
          : Number.NaN,
      ),
      iters,
    },
    {
      key: 'lossTicks',
      label: '败局耗时',
      // 败局平均耗时（ticks，rollout 败局口径，所有 iter 采样）；无败局轮 = NaN 缺口。
      // ⚠️ 高 = 清场停滞（见 EvalSummary.avgLossTicks 的方向警告），必须与胜率并排读。
      vals: chrono.map((r) =>
        r.actuals && r.actuals.avgLossTicks != null ? r.actuals.avgLossTicks : Number.NaN,
      ),
      iters,
    },
    // ── eval 叠加序列（与主序列同 iters 网格；无评估轮 = NaN，TrendChart 按 iter 对齐） ──
    {
      key: 'evalTicks',
      label: 'eval 耗时',
      vals: chrono.map((r) => (r.evalData?.avgTicks != null ? r.evalData.avgTicks : Number.NaN)),
      iters,
    },
    {
      key: 'evalKills',
      label: 'eval 击杀',
      vals: chrono.map((r) =>
        r.evalData
          ? r.evalData.killRate != null
            ? r.evalData.killRate
            : r.evalData.games > 0 && r.evalData.totalKills != null
              ? r.evalData.totalKills / r.evalData.games
              : Number.NaN
          : Number.NaN,
      ),
      iters,
    },
    {
      key: 'evalKillsPct',
      label: 'eval 击杀',
      vals: chrono.map((r) =>
        r.evalData && r.evalData.killRate != null ? r.evalData.killRate : Number.NaN,
      ),
      iters,
    },
    {
      key: 'evalKillsAbs',
      label: 'eval 击杀',
      vals: chrono.map((r) =>
        r.evalData && r.evalData.games > 0 && r.evalData.totalKills != null
          ? r.evalData.totalKills / r.evalData.games
          : Number.NaN,
      ),
      iters,
    },
    {
      key: 'evalPu',
      label: 'eval 道具',
      vals: chrono.map((r) =>
        r.evalData && r.evalData.games > 0 && r.evalData.totalPU != null
          ? r.evalData.totalPU / r.evalData.games
          : Number.NaN,
      ),
      iters,
    },
    {
      key: 'evalPuPct',
      label: 'eval 道具',
      vals: chrono.map((r) =>
        r.evalData &&
        r.evalData.totalPUSpawn != null &&
        r.evalData.totalPUSpawn > 0 &&
        r.evalData.totalPU != null
          ? r.evalData.totalPU / r.evalData.totalPUSpawn
          : Number.NaN,
      ),
      iters,
    },
    {
      key: 'evalWinTicks',
      label: 'eval 胜局耗时',
      vals: chrono.map((r) =>
        r.evalData?.avgWinTicks != null ? r.evalData.avgWinTicks : Number.NaN,
      ),
      iters,
    },
    {
      key: 'evalWinHp',
      label: 'eval 胜局残血',
      vals: chrono.map((r) =>
        r.evalData
          ? r.evalData.avgResidualHpPct != null
            ? r.evalData.avgResidualHpPct
            : r.evalData.avgResidualHp != null
              ? r.evalData.avgResidualHp
              : Number.NaN
          : Number.NaN,
      ),
      iters,
    },
    {
      key: 'evalWinHpAbs',
      label: 'eval 胜局残血',
      vals: chrono.map((r) =>
        r.evalData && r.evalData.avgResidualHp != null ? r.evalData.avgResidualHp : Number.NaN,
      ),
      iters,
    },
    {
      key: 'evalDmgPerKill',
      label: 'eval 承伤/杀',
      vals: chrono.map((r) =>
        r.evalData
          ? r.evalData.dmgPerKillPct != null
            ? r.evalData.dmgPerKillPct
            : r.evalData.dmgPerKill != null
              ? r.evalData.dmgPerKill
              : Number.NaN
          : Number.NaN,
      ),
      iters,
    },
    {
      key: 'evalLossTicks',
      label: 'eval 败局耗时',
      vals: chrono.map((r) =>
        r.evalData?.avgLossTicks != null ? r.evalData.avgLossTicks : Number.NaN,
      ),
      iters,
    },
  ]
}

/** 训练阶段（顶栏图标用）。 */
