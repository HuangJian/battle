/** metric-table-cols.tsx — 逐轮指标表的**列工厂（唯一一份）**。
 *
 *  为什么要抽出来（用户 2026-11-10）：「首页『最新 6 轮完整指标』速览表的备选列，必须和完整
 *  指标表完全一样」——两处各写一套格子时，首页只能手挑十几列，逐局指标（90+）与窗口/逐关账
 *  永远上不了首页，而且同一个读数在两张表里会慢慢分叉。现在两处都从这里取列：
 *   · 列顺序 = `view/metric-columns.ts` 的 `ROLLOUT_ROW_KEYS` / `EVAL_ROW_KEYS` + 逐局指标块；
 *   · 格子内容 = 函数体只在这里，一处改动两处同时生效。
 *
 *  调用方只需决定：显示哪些行（`buildRows` 或首页自己挑 6 行）、藏哪些列（各自的配置弹窗）、
 *  逐局指标用哪种统计量。
 *  列模型（表头文字 / hover 口径 / 数字对齐）仍只住在 `view/metric-columns.ts`。 */

import type { ComponentChildren } from 'preact'
import { Badge } from '../../components/Pill'
import type { Col } from '../../components/DataTable'
import {
  BEHAVIOR_METRICS,
  behaviorColKey,
  EVAL_ROW_KEYS,
  filterGroups,
  fmtLootPickDrop,
  fmtMetricStat,
  fmtNum,
  fmtPct,
  fmtPhaseSecs,
  fmtStampToday,
  formulaLabel,
  formulaMetricName,
  iterGroups,
  klTone,
  METRIC_COLS,
  METRIC_STAT_KEYS,
  METRIC_STAT_LABEL,
  metricColLabelWithStat,
  metricStatValue,
  PAIRED_COL_TITLES,
  pairedTone,
  pairedVerdictText,
  phaseSecs,
  phaseSecsTitle,
  retTone,
  ROLLOUT_ROW_KEYS,
  volumeText,
  volumeTitle,
  winTone,
  windowText,
  windowTitle,
  type BehaviorMetricDef,
  type BehaviorMetricKey,
  type BehaviorMetricStatMap,
  type EvalCkptFile,
  type EvalSummary,
  type IterFilter,
  type IterRow,
  type MetricColKey,
  type PairedCompare,
} from '../../view'
import { ckptForIter } from '../lib/eval-a'

/** 显示行 = 主行 | eval 子行 的联合（eval only 时只保留子行，与旧语义一致）。 */
export type MetricRow =
  | { kind: 'main'; iter: number; time: string; main: IterRow }
  | { kind: 'eval'; iter: number; time: string; eval: EvalSummary }

/** evalA 入队那一格需要的东西（iter 列里的「evalA」小按钮）。 */
export interface EvalACols {
  course: string
  ckpts: EvalCkptFile[]
  readOnly: boolean
  busyIters: Set<number>
  onEvalA: (iter: number) => void
}

export interface MetricColsOptions {
  /** 这个 iter 上不挂状态徽标（halted / PPO 中）：首页最新一轮恒是「刚采集收官、PPO 未回传」，
   *  徽标一直亮着就是噪声（用户 2026-11-10）。不传 = 照旧标。 */
  quietStatusIter?: number | null
}

/** 列模型 → `DataTable` 的列壳（键/表头/对齐/hover 口径）。
 *
 *  为什么用展开而不是逐个赋值：`Col` 的这四个字段本来就该由模型独占，展开能保证「模型加一个字段、
 *  这里自动跟上」，不必两处同步；`key` 也从模型取，顺手保证列 id 与模型键一致。 */
export function colDef(
  key: MetricColKey,
): Pick<Col<MetricRow>, 'key' | 'label' | 'align' | 'thTitle' | 'hiddenByDefault'> {
  const m = METRIC_COLS[key]
  return {
    key: m.key,
    label: m.label,
    align: m.num ? 'num' : undefined,
    thTitle: m.title,
    hiddenByDefault: m.hiddenByDefault,
  }
}

/** 显示行 = 主行 | eval 子行 的联合（eval only 时只保留子行）。 */
export function buildRows(rows: IterRow[], mode: IterFilter): MetricRow[] {
  const out: MetricRow[] = []
  for (const g of filterGroups(iterGroups(rows), mode)) {
    // it0 = bc 权重基线（训练前）：只有干净评估、没有 rollout 采样，不渲染主行
    // （合成行在 console/iters.ts；rollout 派生字段是 NaN 缺口，不是 0）。
    if (mode !== 'eval' && g.iter > 0)
      out.push({ kind: 'main', iter: g.iter, time: g.main.time, main: g.main })
    if (mode !== 'rollout' && g.eval)
      out.push({ kind: 'eval', iter: g.iter, time: g.eval.time, eval: g.eval })
  }
  return out
}

/** 击杀/道具每局平均展示；「无读数」显 `—`（2026-10-19：过 JSON 的 null）。 */
function fmtPerGame(total: number | null | undefined, games: number, digits = 1): string {
  if (total == null || !Number.isFinite(total)) return '—'
  if (games <= 0) return String(total)
  return (total / games).toFixed(digits)
}

/** eval 行配对单元格：null → 基线轮标「基线」，否则「-」（与基线无交集等）。 */
function pairedCell(
  p: PairedCompare | null | undefined,
  isBaseline: boolean,
  pick: (c: PairedCompare) => ComponentChildren,
): ComponentChildren {
  if (!p) {
    return isBaseline ? (
      <span className="tc-muted" title="配对基线本轮：vs自己不判">
        基线
      </span>
    ) : (
      <span className="tc-muted">-</span>
    )
  }
  return pick(p)
}

/** eval-only 模式的替换列：b01/b10/p/delta（PPO 诊断列在 eval 行恒为—，换成裁判）。 */
function pairedCols(baselineIter: number | null): Col<MetricRow>[] {
  const isBase = (r: MetricRow): boolean =>
    r.kind === 'eval' && baselineIter !== null && r.iter === baselineIter
  const get = (r: MetricRow): PairedCompare | null | undefined =>
    r.kind === 'eval' ? r.eval.pairedVsFirst : undefined
  return [
    {
      ...colDef('b01'),
      sortValue: (r) => get(r)?.b01 ?? null,
      cell: (r) =>
        pairedCell(get(r), isBase(r), (c) => <span title={PAIRED_COL_TITLES.b01}>{c.b01}</span>),
    },
    {
      ...colDef('b10'),
      sortValue: (r) => get(r)?.b10 ?? null,
      cell: (r) =>
        pairedCell(get(r), isBase(r), (c) => <span title={PAIRED_COL_TITLES.b10}>{c.b10}</span>),
    },
    {
      ...colDef('pairedP'),
      sortValue: (r) => get(r)?.p ?? null,
      cell: (r) =>
        pairedCell(get(r), isBase(r), (c) => (
          <Badge
            tone={pairedTone(c.verdict)}
            title={`${PAIRED_COL_TITLES.p}；${pairedVerdictText(c.verdict)}`}
          >
            {c.p.toFixed(2)}
          </Badge>
        )),
    },
    {
      ...colDef('delta'),
      sortValue: (r) => get(r)?.deltaPp ?? null,
      cell: (r) =>
        pairedCell(get(r), isBase(r), (c) => (
          <span title={`${PAIRED_COL_TITLES.delta}；${pairedVerdictText(c.verdict)}`}>
            {(c.deltaPp > 0 ? '+' : '') + c.deltaPp.toFixed(1)}pp
          </span>
        )),
    },
  ]
}

/** iter 列：主行带状态徽标与 evalA 入口，eval 行是 `eval it<N>` + 缺N。 */
function iterCol(ea: EvalACols, opts: MetricColsOptions): Col<MetricRow> {
  const quiet = opts.quietStatusIter ?? null
  return {
    ...colDef('iter'),
    cell: (r) =>
      r.kind === 'main' ? (
        <span className="tc-nowrap">
          <b>
            {r.main.iter}
            {r.main.halted && r.iter !== quiet ? (
              <span className="tc-pill tc-pill--note">halted</span>
            ) : null}
          </b>
          {/* 采集已收官、PPO/权重未回传：这一行有 rollout 读数，PPO 侧列是空缺口。 */}
          {r.main.pendingPpo && r.iter !== quiet ? (
            <span
              className="tc-pill tc-pill--note"
              title="本轮采集已收官，PPO 梯度步尚未回传（下列 PPO 读数为空是预期，不是 0）"
            >
              PPO 中
            </span>
          ) : null}
          {r.main.evalData ? (
            // 已跑过 evalA（用户 2026-11-10）：按钮退役，**原位换成那次干净评估的胜率**——
            // 同一格要么是「可以点一下」的入口，要么是点完之后的结果，不让两者并存。
            r.main.evalData.winRate !== null ? (
              <Badge
                tone={winTone(r.main.evalData.winRate)}
                title={`该轮 evalA 结果：干净评估（greedy 固定语料）· 评估权重 = 第 ${r.main.iter} 轮 PPO 更新前 · ${r.main.evalData.games} 局 ${r.main.evalData.wins} 胜`}
              >
                {fmtPct(r.main.evalData.winRate)}
              </Badge>
            ) : (
              <span className="tc-muted" title="该轮有评估记录，但没有胜率读数">
                —
              </span>
            )
          ) : !ea.readOnly && ea.course ? (
            <button
              type="button"
              className="tc-btn tc-btn--sm tc-ml-2"
              disabled={ea.busyIters.has(r.main.iter) || !ckptForIter(ea.ckpts, r.main.iter)}
              title={
                ckptForIter(ea.ckpts, r.main.iter)
                  ? `为 it${r.main.iter} 启动课程设计评估（evalA）`
                  : `未发现 it${r.main.iter} 权重归档`
              }
              onClick={() => ea.onEvalA(r.main.iter)}
            >
              {ea.busyIters.has(r.main.iter) ? '…' : 'evalA'}
            </button>
          ) : null}
        </span>
      ) : (
        <span className="tc-muted tc-nowrap">
          eval it{r.iter}
          {r.eval.dropped > 0 ? (
            <span
              className="tc-pill tc-pill--note"
              title="评估窗口内未收官、被下轮权重分发清场的评估局数"
            >
              缺{r.eval.dropped}
            </span>
          ) : null}
        </span>
      ),
  }
}

/** 共享列：技能读数（主行走 rollout 实际值；eval 行走干净评估实际值）。 */
function skillCols(): Col<MetricRow>[] {
  return [
    {
      ...colDef('avgWinTicks'),
      cell: (r) =>
        r.kind === 'main' ? (
          r.main.actuals?.avgWinTicks != null ? (
            r.main.actuals.avgWinTicks
          ) : r.main.actuals ? (
            <span title="无胜局耗时字段，回退全样本均值">{r.main.actuals.avgTicks}≈</span>
          ) : (
            <span className="tc-muted" title="该轮磁盘数据已清理，估算值">
              {r.main.avgTicks}≈
            </span>
          )
        ) : r.eval.avgWinTicks != null ? (
          r.eval.avgWinTicks
        ) : r.eval.avgTicks !== null ? (
          <span title="无胜局耗时字段，回退全样本均值">{r.eval.avgTicks}≈</span>
        ) : (
          <span className="tc-muted">-</span>
        ),
    },
    {
      ...colDef('kills'),
      cell: (r) =>
        r.kind === 'main' ? (
          r.main.actuals?.killRate != null ? (
            <span title="歼灭率 = Σ击杀 / Σ关卡敌数">{fmtPct(r.main.actuals.killRate)}</span>
          ) : r.main.actuals ? (
            <span className="tc-muted" title="缺关卡敌数，显示每局平均击杀（非百分比）">
              {fmtPerGame(r.main.actuals.totalKills, r.main.actuals.games)}
            </span>
          ) : (
            <span className="tc-muted" title="该轮磁盘数据已清理，估算值">
              {fmtNum(r.main.kills, 1)}≈
            </span>
          )
        ) : r.eval.killRate != null ? (
          <span title="歼灭率 = Σ击杀 / Σ关卡敌数">{fmtPct(r.eval.killRate)}</span>
        ) : r.eval.totalKills != null && r.eval.games > 0 ? (
          <span className="tc-muted" title="缺关卡敌数，显示每局平均击杀">
            {fmtPerGame(r.eval.totalKills, r.eval.games)}
          </span>
        ) : (
          <span className="tc-muted">-</span>
        ),
    },
    {
      ...colDef('dmgPerKill'),
      cell: (r) => {
        const pct = r.kind === 'main' ? r.main.actuals?.dmgPerKillPct : r.eval.dmgPerKillPct
        const abs = r.kind === 'main' ? r.main.actuals?.dmgPerKill : r.eval.dmgPerKill
        if (pct != null)
          return <span title="每杀承伤 / (命数×满血)；越小越会周旋">{fmtPct(pct)}</span>
        if (abs != null)
          return (
            <span className="tc-muted" title="缺容量分母，显示每杀承伤绝对 HP">
              {fmtNum(abs, 1)}
            </span>
          )
        return <span className="tc-muted">-</span>
      },
    },
    {
      ...colDef('residualHp'),
      cell: (r) => {
        const pct = r.kind === 'main' ? r.main.actuals?.avgResidualHpPct : r.eval.avgResidualHpPct
        const abs = r.kind === 'main' ? r.main.actuals?.avgResidualHp : r.eval.avgResidualHp
        if (pct != null)
          return (
            <span title="胜局剩余总血量 / 单命满血（三命全在 = 300%，按满额计）">
              {fmtPct(pct)}
            </span>
          )
        if (abs != null)
          return (
            <span className="tc-muted" title="缺容量分母，显示胜局平均残血 HP">
              {abs}
            </span>
          )
        return <span className="tc-muted">-</span>
      },
    },
    {
      ...colDef('loot'),
      cell: (r) =>
        r.kind === 'main' ? (
          r.main.actuals ? (
            <span title="每局平均拾取数/掉落数">
              {fmtLootPickDrop(
                r.main.actuals.totalPU,
                r.main.actuals.totalPUSpawn,
                r.main.actuals.games,
              )}
            </span>
          ) : (
            <span className="tc-muted" title="该轮磁盘数据已清理，估算值">
              {fmtNum(r.main.loot == null ? null : r.main.loot * 100, 0)}%≈
            </span>
          )
        ) : r.eval.totalPU !== null ? (
          <span title="每局平均拾取数/掉落数">
            {fmtLootPickDrop(r.eval.totalPU, r.eval.totalPUSpawn, r.eval.games)}
          </span>
        ) : (
          <span className="tc-muted">-</span>
        ),
    },
  ]
}

/** 本行的逐局统计（主行 = rollout 采样、eval 子行 = 干净评估）；缺读数 = undefined。 */
function rowMetricStats(r: MetricRow, key: BehaviorMetricKey) {
  return r.kind === 'eval' ? r.eval.behaviorMetrics?.[key] : r.main.actuals?.behaviorMetrics?.[key]
}

/** 逐局指标列：表内放**哪种统计量**由配置弹窗逐指标选（缺省均值），
 *  排序、单元格与表头文字都跟着它走——否则表头说均值、格子里放中位数。 */
function behaviorCols(stats: BehaviorMetricStatMap): Col<MetricRow>[] {
  return BEHAVIOR_METRICS.map((def: BehaviorMetricDef) => {
    const stat = stats[def.key] ?? 'mean'
    const model = METRIC_COLS[behaviorColKey(def.key)]
    // formula 名（用户 2026-11-10）：这一列在课程 reward.formula 里叫什么。
    // eval 专属两列在 metrics 向量里没有位置 ⇒ formula = null（不冒充）。
    const formula = formulaMetricName(def.key)
    const formulaText = formula ? `\n${formulaLabel(formula)}` : ''
    return {
      ...colDef(model.key),
      // 统计量非均值时表头带后缀（中位数/p90 不能只靠 hover 才知道）。
      label: metricColLabelWithStat(model.label, stat),
      thTitle: `${def.title}；表内显示${METRIC_STAT_LABEL[stat]}（单元格 hover 列全六种）${
        formula ? `；${formulaLabel(formula)}` : ''
      }`,
      sortValue: (r) => metricStatValue(rowMetricStats(r, def.key), stat),
      cell: (r) => {
        const st = rowMetricStats(r, def.key)
        if (!st) return <span className="tc-muted">-</span>
        const all = METRIC_STAT_KEYS.map(
          (k) => `${METRIC_STAT_LABEL[k]} ${fmtMetricStat(st[k], def)}`,
        ).join(' · ')
        return (
          <span title={`${def.title}${formulaText}\n${all}\n表内显示：${METRIC_STAT_LABEL[stat]}`}>
            {fmtMetricStat(metricStatValue(st, stat), def)}
          </span>
        )
      },
    }
  })
}

/** 采样范围两列（动态采集/轮换专属；eval 行/非动态采集轮显—，不编读数）。
 *
 *  为什么单独两列而不是并进胜率列：窗口（采了哪些关）与各关进度（采够了没）是**采样**
 *  事实，与「练得好不好」正交；混在一格会让两个问题互相淹没。 */
function samplingCols(): Col<MetricRow>[] {
  return [
    {
      ...colDef('window'),
      cell: (r) =>
        r.kind === 'main' ? (
          <span title={windowTitle(r.main.rotation)}>{windowText(r.main.rotation)}</span>
        ) : (
          <span className="tc-muted">-</span>
        ),
    },
    {
      ...colDef('stageProgress'),
      cell: (r) =>
        r.kind === 'main' && r.main.volume ? (
          <span title={volumeTitle(r.main.volume)}>{volumeText(r.main.volume)}</span>
        ) : (
          <span className="tc-muted">-</span>
        ),
    },
  ]
}

/** PPO 诊断列（rollout 行专属；eval-only 模式下由配对列替换）。 */
function klEntropyCols(): Col<MetricRow>[] {
  return [
    {
      ...colDef('kl'),
      cell: (r) =>
        r.kind === 'main' ? (
          <Badge tone={klTone(r.main.kl)}>{fmtNum(r.main.kl, 4)}</Badge>
        ) : (
          <span className="tc-muted">-</span>
        ),
    },
    {
      ...colDef('entropy'),
      cell: (r) =>
        r.kind === 'main' ? fmtNum(r.main.entropy, 3) : <span className="tc-muted">-</span>,
    },
    {
      ...colDef('meanRet'),
      cell: (r) =>
        r.kind === 'main' ? (
          <Badge tone={retTone(r.main.meanRet)}>{fmtNum(r.main.meanRet, 3)}</Badge>
        ) : (
          <span className="tc-muted">-</span>
        ),
    },
    {
      ...colDef('lr'),
      cell: (r) => (r.kind === 'main' ? fmtNum(r.main.lr, 6) : <span className="tc-muted">-</span>),
    },
  ]
}

/** 时间列：今天的时间只显时分秒（用户 2026-11-10，两张表同一口径）。 */
function timeCol(): Col<MetricRow> {
  return {
    ...colDef('time'),
    cell: (r) => (
      <span className="tc-muted">
        {fmtStampToday(r.kind === 'main' ? r.main.time : r.eval.time)}
      </span>
    ),
  }
}

/** 胜率列：主行「胜率」与 eval 视图「eval 胜率」共用一个格子渲染。 */
function winRateCol(key: 'winRate' | 'evalRate'): Col<MetricRow> {
  return {
    ...colDef(key),
    cell: (r) =>
      r.kind === 'main' ? (
        <Badge tone={winTone(r.main.winRate)}>{fmtPct(r.main.winRate)}</Badge>
      ) : r.eval.winRate !== null ? (
        <>
          <Badge
            tone={winTone(r.eval.winRate)}
            title={`干净评估（greedy 固定语料）· 评估权重 = 第 ${r.iter} 轮 PPO 更新前 · ${r.eval.games} 局 ${r.eval.wins} 胜 · 全歼 ${r.eval.clears} · outcomes: ${
              Object.entries(r.eval.outcomes)
                .map(([k, v]) => `${k}×${v}`)
                .join(' ') || '-'
            } · 用时 ${r.eval.sec}s · wver ${r.eval.wver.slice(0, 12)}…`}
          >
            {fmtPct(r.eval.winRate)}
          </Badge>{' '}
          <span className="tc-muted">
            {r.eval.wins}/{r.eval.games}
          </span>
        </>
      ) : (
        <span className="tc-muted">-</span>
      ),
  }
}

/**
 * 列工厂。
 * - eval：iter / 时间 / eval 胜率 / 配对四列 / 技能列 / 得分 / 用时 / wver + 逐局指标块。
 * - all / rollout：iter / 时间 / 胜率 / 技能列 / 阶段耗时 / 得分 / PPO 诊断 / 采样范围
 *   + 逐局指标块。
 * 两种模式的结构与顺序只由 `view/metric-columns.ts` 的键数组决定（这里不再自己排）。
 */
export function buildMetricCols(
  ea: EvalACols,
  mode: IterFilter,
  baselineIter: number | null,
  stats: BehaviorMetricStatMap,
  opts: MetricColsOptions = {},
): Col<MetricRow>[] {
  const byKey = new Map<string, Col<MetricRow>>()
  const put = (cols: Col<MetricRow>[]): void => {
    for (const c of cols) byKey.set(c.key, c)
  }
  put([
    iterCol(ea, opts),
    timeCol(),
    winRateCol('winRate'),
    winRateCol('evalRate'),
    ...pairedCols(baselineIter),
    ...skillCols(),
    ...samplingCols(),
    ...klEntropyCols(),
    {
      ...colDef('phaseSecs'),
      cell: (r) =>
        r.kind === 'main' ? (
          <span title={phaseSecsTitle(phaseSecs(r.main))}>{fmtPhaseSecs(phaseSecs(r.main))}</span>
        ) : (
          <span title="eval 窗口用时" className="tc-num">
            {r.eval.sec.toFixed(0)}s
          </span>
        ),
    },
    {
      ...colDef('scoreMean'),
      cell: (r) =>
        r.kind === 'main' ? (
          fmtNum(r.main.scoreMean, 4)
        ) : r.eval.scoreMean !== null ? (
          r.eval.scoreMean.toFixed(4)
        ) : (
          <span className="tc-muted">-</span>
        ),
    },
    {
      ...colDef('evalSec'),
      cell: (r) =>
        r.kind === 'eval' ? `${r.eval.sec.toFixed(0)}s` : <span className="tc-muted">-</span>,
    },
    {
      ...colDef('wver'),
      cell: (r) =>
        r.kind === 'eval' ? (
          <span className="tc-mono tc-muted tc-small" title={r.eval.wver}>
            {r.eval.wver.slice(0, 7)}
          </span>
        ) : (
          <span className="tc-muted">-</span>
        ),
    },
    ...behaviorCols(stats),
  ])
  const keys = mode === 'eval' ? EVAL_ROW_KEYS : ROLLOUT_ROW_KEYS
  // 逐局指标块压尾（默认隐藏，配置弹窗里按需打开）——与两张表的直觉顺序一致。
  const order: string[] = [...keys, ...BEHAVIOR_METRICS.map((m) => behaviorColKey(m.key))]
  return order.map((k) => byKey.get(k)).filter((c): c is Col<MetricRow> => c !== undefined)
}
