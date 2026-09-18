/** series.ts — 指标序列切分与逐指标序列抽取（趋势图数据源）。 */
import { IterRow } from './metric-types'

// ────────────────────────── 纯函数：指标系列 ──────────────────────────

export interface Series {
  key: string
  label: string
  vals: number[]
  /** 与 vals 逐位对齐的迭代序号（时间正序）。 */
  iters: number[]
}

/** 走势图范围档位：全量 / 最近 30 轮 / 最近 10 轮。 */
export type TrendRange = 'all' | '30' | '10'

/** eval 源稀疏序列（干净评估只在部分迭代出现，中间轮 = NaN 缺口）：
 *  「最近 N」语义 = 最近 N 个有效评估点。双序列叠加时由 TrendChart 按主序列 iters 对齐。 */
const SPARSE_SERIES_KEYS: ReadonlySet<string> = new Set([
  'eval',
  'evalTicks',
  'evalKills',
  'evalPu',
  'evalWinTicks',
  'evalWinHp',
  'evalDmgPerKill',
  'evalLossTicks',
])

/**
 * 按范围档位截取序列。eval 源序列（eval 胜率）在有限轮里常带 NaN 缺口，
 * 其「最近 N」语义 = 最近 N 个**有效**评估点（而非最近 N 轮迭代），
 * 避免窗口内全是 NaN 画空图；「全量」档同样只保留有效点。
 * 其它指标（含 rollout 口径的胜局耗时/胜局残血/承伤·杀/败局耗时）= 最近 N 轮迭代（按 iter 截取）。
 */
export function sliceSeries(series: Series, range: TrendRange): Series {
  const { key, label, vals, iters } = series
  if (vals.length === 0) return { key, label, vals, iters }
  // eval 源：先过滤到有效评估点，再按档位截取。
  if (SPARSE_SERIES_KEYS.has(key)) {
    const pairs = vals.map((v, i) => ({ v, it: iters[i] })).filter((p) => Number.isFinite(p.v))
    const sliced = range === 'all' ? pairs : pairs.slice(-Number(range))
    return { key, label, vals: sliced.map((p) => p.v), iters: sliced.map((p) => p.it) }
  }
  if (range === 'all') return { key, label, vals, iters }
  return { key, label, vals: vals.slice(-Number(range)), iters: iters.slice(-Number(range)) }
}

/** 指标表列集（时间正序；actuals/eval 缺轮断点 = NaN 会被 sparkPoints 过滤）。
 *  返回全量时序（不做 20 轮截断），由 sliceSeries / TrendChart 按范围档位截取。 */
export function metricSeries(rows: IterRow[]): Series[] {
  const chrono = [...rows].sort((a, b) => a.iter - b.iter)
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
      key: 'pu',
      label: '道具',
      vals: chrono.map((r) =>
        r.actuals && r.actuals.games > 0 ? r.actuals.totalPU / r.actuals.games : Number.NaN,
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
      // 胜局残血占比 0–1；缺容量时回退绝对 hp（趋势形状仍可读）
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
