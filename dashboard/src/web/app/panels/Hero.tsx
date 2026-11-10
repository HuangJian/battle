/** Hero.tsx — 训练状态 hero：走势图（rollout 实线 + eval 琥珀虚线叠加，hover 双口径）
 *  + 最新 6 轮完整指标（非 eval 轮可手动入队 eval A，完成后回填）。
 *  数据口径 = /api/state.metrics；「完整指标表 ›」进抽屉。 */

import {
  behaviorMetricSeries,
  BEHAVIOR_METRICS,
  behaviorTrendKey,
  DEFAULT_BEHAVIOR_METRIC_STATS,
  DEFAULT_HIDDEN_COL_KEYS,
  DEFAULT_TREND_VISIBILITY,
  evalBannerDone,
  filterGroups,
  fmtMetricStat,
  fmtPaired,
  fmtPct,
  heroMainRows,
  iterGroups,
  isTrendRange,
  isTrendSource,
  latestRow,
  METRIC_STAT_LABEL,
  metricSeries,
  pairedBaselineOf,
  parseBehaviorMetricStats,
  parseHiddenCols,
  parseTrendVisibility,
  TC_HERO_ITER_VIEW,
  TC_HERO_ITERS_COLLAPSED,
  TC_HERO_ITERS_COLS,
  TC_HERO_ITERS_STAT,
  TC_TREND_RANGE,
  TC_TREND_SCALE,
  TC_TREND_SOURCE,
  TC_TREND_STAT,
  TC_TREND_VISIBILITY,
  parseTrendScales,
  TREND_RANGE_OPTIONS,
  TREND_SCALES_DEFAULT,
  TREND_SOURCE_OPTIONS,
  winTone,
  type BehaviorMetricKey,
  type BehaviorMetricStatMap,
  type ConsoleStateView,
  type EvalABanner,
  type EvalCkptFile,
  type IterRow,
  type MetricStatKey,
  type PairedReferee,
  type Series,
  type TrendChartKey,
  type TrendRange,
  type TrendScale,
  type TrendScales,
  type TrendSource,
} from '../../view'
import { EvalBanner } from '../../components/EvalBanner'
import { SegmentedControl } from '../../components/SegmentedControl'
import { Switch } from '../../components/Switch'
import { COLOR_EVAL, TrendChart } from '../../components/TrendChart'
import { TrendZoomModal, type ZoomTarget } from '../../components/TrendZoomModal'
import type { Col } from '../../components/DataTable'
import { ckptForIter, loadCourseCkpts, startEvalA } from '../lib/eval-a'
import { buildMetricCols, type MetricRow } from './metric-table-cols'
import { ReplayExportModal } from './ReplayExportModal'
import { CompareTrendsModal } from './CompareTrendsModal'
import { TrendConfigModal } from './TrendConfigModal'
import { useEffect, useRef, useState } from 'preact/hooks'
import { MetricsConfigModal } from './MetricsConfigModal'

export interface HeroProps {
  stateView: ConsoleStateView | null
  onMore: () => void
  /** 触发评估入队后拉一次 /api/state（回填 eval 列）。 */
  onRefresh?: () => void
  readOnly?: boolean
  /** 顶栏刷新节奏（秒）：「比较课程」弹窗跟它走（同一个旋钮管两处，不新增第二个开关）。
   *  缺省 300 = `REFRESH_INTERVALS` 的默认档（直构 Hero 的旧用例不受影响）。 */
  refreshSec?: number
}

/** 序列最后一个有效点（缺值 = null，不冒充 0）。 */
function lastFinite(s: Series | undefined): number | null {
  if (!s) return null
  const v = s.vals.filter(Number.isFinite).slice(-1)[0]
  return v === undefined ? null : v
}

/** 右侧趋势格：数据源档位决定画一条还是两条。
 *
 *  导出理由与 `NodeRow` 同：三种档位的**渲染形状**（一条还是两条、标签/线色是哪个口径）
 *  必须能单独渲染断言——SSR 首帧恒为「全部」档，只测整页就只测到一条分支。
 *
 *  · `all`     —— 主序列（rollout 实线）+ 叠加序列（eval 橙线）；值列 `a / b` 双口径。
 *  · `rollout` —— 只主序列（去掉橙线）。
 *  · `eval`    —— 只 eval，且它**升为主序列**：标签/悬停用 eval 自己的词，线色改橙
 *                  （口径色是身份，不变；语义 tone 只给值列）。
 *
 *  `toneOf` 是**函数**（而非一个已算好的档）：值列与线色必须跟着「当前显示的那条」走——
 *  否则切到 eval 档时，eval 胜率会被 rollout 胜率的阈值染成色（一个已经过期的判断）。 */
export function TrendCell({
  series,
  seriesEval,
  source,
  fmt,
  toneOf,
  range,
  yFloor,
  yMin,
  title,
  hoverIter,
  onHoverIter,
  scale,
  onScale,
  onExpand,
}: {
  series: Series | undefined
  seriesEval?: Series | undefined
  source: TrendSource
  fmt: (v: number | null) => string
  toneOf?: (v: number) => 'g' | 'y' | 'r'
  range: TrendRange
  /** y 轴下界上限：击杀/道具 0；胜率 0.3（基底不得高于 30%）。 */
  yFloor?: number
  /** y 轴强制下界（胜局耗时量级缩放）。 */
  yMin?: number
  /** 标签悬停提示（口径说明）。 */
  title?: string
  /** 同组共享的悬停迭代号（Hero 六张图联动；见 `TrendChart` 同名 prop）。 */
  hoverIter?: number | null
  /** 受控悬停回报（鼠标移入/移动/离开）。 */
  onHoverIter?: (it: number | null) => void
  /** 纵坐标档位（仅击杀/胜局残血/道具三图）：小钮显示**当前**单位，点一下切换。 */
  scale?: TrendScale
  onScale?: (s: TrendScale) => void
  /** 点击图表主体放大（放大浮窗由调用方渲染）。 */
  onExpand?: (main: Series, overlay: Series | undefined) => void
}) {
  const evalOnly = source === 'eval'
  const main = evalOnly ? seriesEval : series
  const overlay = source === 'all' ? seriesEval : undefined
  const last = lastFinite(main)
  const lastOverlay = lastFinite(overlay)
  const tone = toneOf && last != null ? toneOf(last) : undefined
  return (
    <div className="tc-tcell">
      <span className="tc-tcell__hd">
        <span className="tc-tcell__hdmid">
          <span className="tc-tcell__lbl" title={title}>
            {main ? main.label : '—'}
          </span>
          {onScale ? (
            <>
              <Switch
                label="纵坐标：百分比"
                checked={scale === 'pct'}
                onChange={(pct) => onScale(pct ? 'pct' : 'abs')}
                title={`纵坐标：${scale === 'pct' ? '百分比' : '绝对值'}（开关切换，关 = 数值）`}
              />
              <span className="tc-tcell__unit">{scale === 'pct' ? '%' : '数值'}</span>
            </>
          ) : null}
        </span>
        <b className={tone ? `tc-mtrend__val--${tone}` : undefined}>
          {fmt(last)}
          {overlay && lastOverlay != null ? (
            <span className="tc-tcell__pair">
              {' / '}
              {fmt(lastOverlay)}
            </span>
          ) : null}
        </b>
      </span>
      {main ? (
        <div className="tc-tcell__chart">
          <TrendChart
            series={main}
            series2={overlay}
            range={range}
            fmt={fmt}
            // eval 档一条线：色 = 口径（橙），不叠语义 tone（值列已经把判断说完了）。
            tone={evalOnly ? undefined : tone}
            color={evalOnly ? COLOR_EVAL : undefined}
            height={56}
            yFloor={yFloor}
            yMin={yMin}
            hoverIter={hoverIter}
            onHoverIter={onHoverIter}
          />
          {/* 放大是**独立按钮**：图表主体只负责 hover 读数（十字光标），两者不再抢同一个点击面。
              也不能给图表主体挂原生 `title`——浏览器浮层会和读数框抢悬停。 */}
          {onExpand ? (
            <button
              type="button"
              className="tc-tcell__zoom"
              aria-label={`放大走势图：${main.label}`}
              title="放大走势图（浮窗内可切换统计量）"
              onClick={() => onExpand(main, overlay)}
            >
              <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true">
                <path
                  d="M7.5 1H11v3.5M4.5 11H1V7.5M11 1 7.5 4.5M1 11l3.5-3.5"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.4"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
              </svg>
            </button>
          ) : null}
        </div>
      ) : (
        <span className="tc-muted tc-small">{evalOnly ? '该课程暂无干净评估' : '暂无数据'}</span>
      )}
    </div>
  )
}

/** 胜局耗时 y 轴强制下界（用户 2026-09-18）：
 *  max(0, 全部 it 最少耗时×0.8, 全部 it 最多耗时×0.5)。
 *  按序列全量有效点计算（不随「最近 N」档位变）。 */
function winTicksYMin(...seriesList: Array<{ vals: number[] } | undefined>): number | undefined {
  const vals: number[] = []
  for (const s of seriesList) {
    if (!s) continue
    for (const v of s.vals) if (Number.isFinite(v)) vals.push(v)
  }
  if (vals.length === 0) return undefined
  const mn = Math.min(...vals)
  const mx = Math.max(...vals)
  return Math.max(0, mn * 0.8, mx * 0.5)
}

/** 胜局耗时（ticks）/ 胜局残血 展示：整数（平均值已四舍五入）。 */
function fmtInt(v: number | null): string {
  return v != null ? String(Math.round(v)) : '—'
}

/** 趋势图「数值」档：每局平均击杀 1 位 / 道具 2 位。 */
function fmtDec1(v: number | null): string {
  return v != null ? v.toFixed(1) : '—'
}
function fmtDec2(v: number | null): string {
  return v != null ? v.toFixed(2) : '—'
}

/** 配对裁判行（只读哨子，不进门判）：最新 eval vs 开腿 / vs 上一轮，同语料逐 seed 配对。
 * 灰（flat）是正常态——100 对下 99% 时间证据不够，不是故障。 */
function PairedRefereeLine({ ref }: { ref: PairedReferee | null | undefined }) {
  if (!ref || (!ref.vsFirst && !ref.vsPrev)) return null
  const title =
    '同语料逐 seed 配对（McNemar）：b01=基线输新权重赢（政绩）/b10=反之（学费）；' +
    '只看不一致对。灰=证据不够（正常态），不是故障；显著跌也只变色，不触发任何动作。'
  return (
    <div className="tc-muted tc-small tc-mb-1" title={title} role="status">
      配对裁判（贪心同卷）
      {ref.vsFirst ? (
        <span>
          {' '}
          ·{' '}
          {fmtPaired(
            ref.vsFirst,
            // it0 = 训练前 bc 权重基线（不是开腿首轮）——文案分开，防止误读成中途两点
            ref.vsFirst.baseIter === 0 ? 'vs bc基线' : `vs开腿it${ref.vsFirst.baseIter}`,
          )}
        </span>
      ) : null}
      {ref.vsPrev ? (
        <span> · {fmtPaired(ref.vsPrev, `vs上一轮it${ref.vsPrev.baseIter}`)}</span>
      ) : null}
    </div>
  )
}

/** 按 iter 解析权重路径 → 见 lib/eval-a.ts */

/** 速览表壳：列与格子**全部**来自 `metric-table-cols.tsx`（与「完整指标表」同一份工厂）。
 *
 *  为什么不再自己写格子（用户 2026-11-10：「备选列必须和完整指标表完全一样，所有可见指标
 *  都要能配到首页」）：速览表以前手挑十几列、自己重写每格渲染，逐局指标（90+）与窗口/逐关账
 *  根本上不了首页，同一个读数还可能两处分叉。现在这里只剩排版——表头从列模型取、单元格按
 *  同一个列数组铺；速览与完整表的差别只有「哪 6 行 / 无排序 / 无表格自带列菜单」。 */
function SummaryTable({ rows, cols }: { rows: MetricRow[]; cols: Col<MetricRow>[] }) {
  return (
    <table className="tc-table tc-table--dense">
      <thead>
        <tr>
          {cols.map((c) => (
            <th key={c.key} className={c.align === 'num' ? 'tc-num' : undefined} title={c.thTitle}>
              {c.label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 ? (
          <tr>
            {/* colSpan 跟着实际可见列数走（藏列时也不会少跨一格）。 */}
            <td colSpan={cols.length} className="tc-muted tc-center">
              该课程暂无 eval 评估记录
            </td>
          </tr>
        ) : (
          rows.map((r) => (
            <tr key={r.kind === 'main' ? `m${r.iter}` : `e${r.iter}`}>
              {cols.map((c) => (
                <td key={c.key} className={c.align === 'num' ? 'tc-num' : undefined}>
                  {c.cell(r)}
                </td>
              ))}
            </tr>
          ))
        )}
      </tbody>
    </table>
  )
}

/** 最新 6 轮区块：主行 / eval 双视图 toggle（持久化 localStorage）；表头行右侧
 *  「导出 replay」+「完整指标表 ›」进指标抽屉（与标题同一行、右对齐）。 */
function LastIters({
  iters,
  onMore,
  course,
  ckpts,
  readOnly,
  onEvalQueued,
  busyIters,
}: {
  iters: IterRow[]
  onMore: () => void
  course: string
  ckpts: EvalCkptFile[]
  readOnly: boolean
  onEvalQueued: (iter: number, ckpt: string) => void
  busyIters: Set<number>
}) {
  const [view, setView] = useState<'main' | 'eval'>('main')
  const [replayOpen, setReplayOpen] = useState(false)
  // hydrate 后从 localStorage 恢复视图（SSR 首帧恒主行，避免 hydration 不一致）。
  useEffect(() => {
    try {
      const v = localStorage.getItem(TC_HERO_ITER_VIEW)
      if (v === 'main' || v === 'eval') setView(v)
    } catch {
      /* 隐私模式等不可写场景忽略 */
    }
  }, [])
  const onView = (v: 'main' | 'eval'): void => {
    setView(v)
    try {
      localStorage.setItem(TC_HERO_ITER_VIEW, v)
    } catch {
      /* ignore */
    }
  }
  // 折叠/展开（持久化；SSR 首帧恒展开，与 hydrate 一致，偏好 hydrate 后恢复）。
  const [collapsed, setCollapsed] = useState(false)
  useEffect(() => {
    try {
      if (localStorage.getItem(TC_HERO_ITERS_COLLAPSED) === '1') setCollapsed(true)
    } catch {
      /* 隐私模式等不可写场景忽略 */
    }
  }, [])
  const onToggleCollapsed = (): void => {
    setCollapsed((c) => {
      const next = !c
      try {
        localStorage.setItem(TC_HERO_ITERS_COLLAPSED, next ? '1' : '0')
      } catch {
        /* ignore */
      }
      return next
    })
  }
  // 列显隐 + 逐局指标统计量（用户 2026-11-10）：速览表的**备选列 = 完整指标表的列**
  // （同一个列工厂 buildMetricCols），配置各存一份（主行视图 / eval 视图各一套键）。
  // 默认集合 = 模型里 `hiddenByDefault` 的那些（逐局指标块），与指标页一致。
  const [hiddenCols, setHiddenCols] = useState<Set<string>>(() => new Set(DEFAULT_HIDDEN_COL_KEYS))
  useEffect(() => {
    try {
      setHiddenCols(parseHiddenCols(localStorage.getItem(TC_HERO_ITERS_COLS)))
    } catch {
      /* 隐私模式等不可写场景忽略 */
    }
  }, [])
  const onToggleCol = (key: string, on: boolean): void => {
    const next = new Set(hiddenCols)
    if (on) next.delete(key)
    else next.add(key)
    setHiddenCols(next)
    try {
      localStorage.setItem(TC_HERO_ITERS_COLS, JSON.stringify([...next]))
    } catch {
      /* ignore */
    }
  }
  const [metricStats, setMetricStats] = useState<BehaviorMetricStatMap>(() => ({
    ...DEFAULT_BEHAVIOR_METRIC_STATS,
  }))
  useEffect(() => {
    try {
      setMetricStats(parseBehaviorMetricStats(localStorage.getItem(TC_HERO_ITERS_STAT)))
    } catch {
      /* 隐私模式等不可写场景忽略 */
    }
  }, [])
  const onMetricStat = (key: BehaviorMetricKey, stat: MetricStatKey): void => {
    const next = { ...metricStats, [key]: stat }
    setMetricStats(next)
    try {
      localStorage.setItem(TC_HERO_ITERS_STAT, JSON.stringify(next))
    } catch {
      /* ignore */
    }
  }
  const [colsOpen, setColsOpen] = useState(false)

  const mains = heroMainRows(iters)
  if (mains.length === 0) return null
  const ev = view === 'eval'
  // 行：主行视图 = 最新 6 个主行；eval 视图 = 最新 6 个 eval 子行（与完整表 eval 档同源）。
  const rows: MetricRow[] = ev
    ? filterGroups(iterGroups(iters), 'eval')
        .slice(0, 6)
        .map((g) => ({ kind: 'eval', iter: g.iter, time: g.eval!.time, eval: g.eval! }))
    : mains.map((r) => ({ kind: 'main', iter: r.iter, time: r.time, main: r }))
  const allCols = buildMetricCols(
    {
      course,
      ckpts,
      readOnly,
      busyIters,
      onEvalA: (iter) => {
        const ckpt = ckptForIter(ckpts, iter)
        if (ckpt) onEvalQueued(iter, ckpt)
      },
    },
    ev ? 'eval' : 'all',
    pairedBaselineOf(iters.map((r) => r.evalData?.pairedVsFirst)),
    metricStats,
    // 最新一轮不挂状态徽标（用户 2026-11-10）：它恒是刚采集收官、PPO 未回传的那轮。
    { quietStatusIter: rows[0]?.iter ?? null },
  )
  const cols = allCols.filter((c) => !hiddenCols.has(c.key))
  const n = ev ? filterGroups(iterGroups(iters), 'eval').length : mains.length
  return (
    <div className="tc-hero__iters">
      <div className="tc-hero__iters-hd">
        <span className="tc-hero__iters-left">
          <button
            type="button"
            className="tc-hero__iters-toggle"
            aria-expanded={!collapsed}
            aria-label={collapsed ? '展开最新指标表' : '折叠最新指标表'}
            title={collapsed ? '展开' : '折叠'}
            onClick={onToggleCollapsed}
          >
            {collapsed ? '▸' : '▾'}
          </button>
          <span>
            最新 {Math.min(n, 6)} 轮{ev ? ' eval 评估' : '完整指标'}
          </span>
          <SegmentedControl<'main' | 'eval'>
            value={view}
            ariaLabel="指标行视图"
            options={[
              { value: 'main', label: '主行' },
              { value: 'eval', label: 'eval' },
            ]}
            onChange={onView}
          />
        </span>
        <span className="tc-hero__iters-right">
          <button
            type="button"
            className="tc-btn tc-btn--sm"
            title="选这张速览表显示哪些列（主行 / eval 各一套；藏起来的列在完整指标表里照旧可看）"
            onClick={() => setColsOpen(true)}
          >
            列配置
          </button>
          <button
            type="button"
            className="tc-btn tc-btn--sm"
            title="从训练的任意 in-loop eval 轮导出仿真 replay（弹窗里按评估轮选，默认最新轮；勾选局 → 确定性重放 → .replay 下载）"
            onClick={() => setReplayOpen(true)}
          >
            从任意评估轮导出回放
          </button>
          <button type="button" className="tc-link" onClick={onMore}>
            完整指标表 ›
          </button>
        </span>
      </div>
      <ReplayExportModal
        open={replayOpen}
        course={course}
        readOnly={readOnly}
        onClose={() => setReplayOpen(false)}
      />
      {/* 列配置：主行与 eval 的列集合不同，弹窗跟着当前视图走（两套键互不相干）。 */}
      <MetricsConfigModal
        open={colsOpen}
        title="速览表列配置"
        cols={allCols.map((c) => ({ key: c.key, label: c.label, hint: c.thTitle }))}
        hidden={hiddenCols}
        stats={metricStats}
        onToggle={onToggleCol}
        onStat={onMetricStat}
        onClose={() => setColsOpen(false)}
      />
      {/* 横向滚动只给表体（列可配到 100+ 列）：标题与按钮留在视野内。 */}
      <div className="tc-hero__iters-wrap">
        {collapsed ? null : <SummaryTable rows={rows} cols={cols} />}
      </div>
    </div>
  )
}

export function Hero({
  stateView,
  onMore,
  onRefresh,
  readOnly = false,
  refreshSec = 300,
}: HeroProps) {
  const iters = stateView?.metrics.iters ?? []
  const course = stateView?.course ?? ''
  const head = latestRow(iters)
  const series = metricSeries(iters)
  const winSeries = series.find((m) => m.key === 'winRate')
  const evalSeries = series.find((m) => m.key === 'eval')
  // 三张图各两条序列（绝对值 / 百分比）；选哪条由下面的纵坐标档位决定。
  const killsPctSeries = series.find((m) => m.key === 'killsPct')
  const evalKillsPctSeries = series.find((m) => m.key === 'evalKillsPct')
  const killsAbsSeries = series.find((m) => m.key === 'killsAbs')
  const evalKillsAbsSeries = series.find((m) => m.key === 'evalKillsAbs')
  const winHpAbsSeries = series.find((m) => m.key === 'winHpAbs')
  const evalWinHpAbsSeries = series.find((m) => m.key === 'evalWinHpAbs')
  const puPctSeries = series.find((m) => m.key === 'puPct')
  const evalPuPctSeries = series.find((m) => m.key === 'evalPuPct')
  const puSeries = series.find((m) => m.key === 'pu')
  const evalPuSeries = series.find((m) => m.key === 'evalPu')
  const winTicksSeries = series.find((m) => m.key === 'winTicks')
  const evalWinTicksSeries = series.find((m) => m.key === 'evalWinTicks')
  const winHpSeries = series.find((m) => m.key === 'winHp')
  const evalWinHpSeries = series.find((m) => m.key === 'evalWinHp')
  const dmgPerKillSeries = series.find((m) => m.key === 'dmgPerKill')
  const evalDmgPerKillSeries = series.find((m) => m.key === 'evalDmgPerKill')

  // ckpt 发现（eval A 入队用）；课程切换重拉。
  const [ckpts, setCkpts] = useState<EvalCkptFile[]>([])
  const [busyIters, setBusyIters] = useState<Set<number>>(() => new Set())
  // evalA 横幅（用户 2026-10-18：原来是一行 `tc-notice` 灰字，改成横幅；读数回填后自动隐藏）。
  const [evalBanner, setEvalBanner] = useState<EvalABanner | null>(null)
  const aliveRef = useRef(true)
  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
    }
  }, [])
  useEffect(() => {
    if (!course || readOnly) {
      setCkpts([])
      return
    }
    let ok = true
    void loadCourseCkpts(course).then((files) => {
      if (ok) setCkpts(files)
    })
    return () => {
      ok = false
    }
  }, [course, readOnly])

  const onEvalQueued = (iter: number, ckpt: string): void => {
    if (readOnly || !course) return
    setBusyIters((s) => new Set(s).add(iter))
    setEvalBanner({ course, iter, message: `it${iter} evalA 启动中…`, ok: true })
    void (async () => {
      try {
        const { ok, message } = await startEvalA(course, iter, ckpt)
        if (!aliveRef.current) return
        setEvalBanner({ course, iter, message, ok })
        if (!ok) {
          setBusyIters((s) => {
            const n = new Set(s)
            n.delete(iter)
            return n
          })
          return
        }
        // 轻量回填：每 20s 拉一次 state，直到 iters 带上该 iter 的 evalData（effect 清 busy）。
        for (let i = 0; i < 30 && aliveRef.current; i++) {
          await new Promise((res) => setTimeout(res, 20_000))
          if (!aliveRef.current) return
          onRefresh?.()
        }
        if (aliveRef.current) {
          setBusyIters((s) => {
            const n = new Set(s)
            n.delete(iter)
            return n
          })
        }
      } catch (e) {
        if (!aliveRef.current) return
        setEvalBanner({ course, iter, message: String(e), ok: false })
        setBusyIters((s) => {
          const n = new Set(s)
          n.delete(iter)
          return n
        })
      }
    })()
  }

  // 读数回填 ⇒ 横幅自动隐藏（用户 2026-10-18）。判据在 `view/eval-a-banner.ts`（纯函数，可单测）：
  // 只有**点的那一轮**出现 evalData 才算完成；切课也作废（横幅回答的是「这门课刚做了什么」）。
  // 失败横幅本来就没有读数会来 ⇒ 它不自己消失，留给用户看完手动关（万一读数随后真到了，判据照样会收它）。
  useEffect(() => {
    setEvalBanner((b) => (b && evalBannerDone(b, iters, course) ? null : b))
  }, [iters, course])

  // eval 回填：iters 里出现 evalData 的轮，清除 busy。
  useEffect(() => {
    setBusyIters((s) => {
      if (s.size === 0) return s
      const next = new Set(s)
      for (const r of iters) {
        if (r.evalData && next.has(r.iter)) next.delete(r.iter)
      }
      return next.size === s.size ? s : next
    })
  }, [iters])

  // 同组共享的悬停迭代号：六张趋势图读同一个 it（鼠标在哪张图上移动，全组一起亮）。
  const [hoverIter, setHoverIter] = useState<number | null>(null)
  // 走势范围档位（全量 / 最近60% / 最近30%）；持久化到 localStorage，hydrate 后恢复。
  const [range, setRange] = useState<TrendRange>('30pct')
  useEffect(() => {
    try {
      const v = localStorage.getItem(TC_TREND_RANGE)
      if (isTrendRange(v)) setRange(v)
    } catch {
      /* 隐私模式等不可写场景忽略 */
    }
  }, [])
  const onRange = (r: TrendRange): void => {
    setRange(r)
    try {
      localStorage.setItem(TC_TREND_RANGE, r)
    } catch {
      /* ignore */
    }
  }

  // 走势数据源档位（全部叠加 / 只看 rollout / 只看 eval）；持久化与范围档同一个套路：
  // SSR 首帧恒「全部」（= 原有画面），hydrate 后恢复偏好；非法值过 isTrendSource 遭退默认。
  const [source, setSource] = useState<TrendSource>('all')
  useEffect(() => {
    try {
      const v = localStorage.getItem(TC_TREND_SOURCE)
      if (isTrendSource(v)) setSource(v)
    } catch {
      /* 隐私模式等不可写场景忽略 */
    }
  }, [])
  const onSource = (s: TrendSource): void => {
    setSource(s)
    try {
      localStorage.setItem(TC_TREND_SOURCE, s)
    } catch {
      /* ignore */
    }
  }

  // 纵坐标档位（击杀 / 胜局残血 / 道具）：绝对值 ↔ 百分比。
  // 持久化与范围/数据源同一个套路：SSR 首帧 = 改造前的样子，hydrate 后恢复偏好。
  const [scales, setScales] = useState<TrendScales>(TREND_SCALES_DEFAULT)
  useEffect(() => {
    try {
      setScales(parseTrendScales(localStorage.getItem(TC_TREND_SCALE)))
    } catch {
      /* 隐私模式等不可写场景忽略 */
    }
  }, [])
  const onScale = (k: keyof TrendScales, s: TrendScale): void => {
    const next = { ...scales, [k]: s }
    setScales(next)
    try {
      localStorage.setItem(TC_TREND_SCALE, JSON.stringify(next))
    } catch {
      /* ignore */
    }
  }

  // 走势图显示表 + 逐指标统计量。SSR 首帧 = 默认六项 + 全均值，hydrate 后恢复偏好。
  const [visibility, setVisibility] = useState<Record<TrendChartKey, boolean>>(() => ({
    ...DEFAULT_TREND_VISIBILITY,
  }))
  useEffect(() => {
    try {
      setVisibility(parseTrendVisibility(localStorage.getItem(TC_TREND_VISIBILITY)))
    } catch {
      /* 隐私模式等不可写场景忽略 */
    }
  }, [])
  const onToggleChart = (k: TrendChartKey, on: boolean): void => {
    const next = { ...visibility, [k]: on }
    setVisibility(next)
    try {
      localStorage.setItem(TC_TREND_VISIBILITY, JSON.stringify(next))
    } catch {
      /* ignore */
    }
  }
  const [metricStats, setMetricStats] = useState<BehaviorMetricStatMap>(() => ({
    ...DEFAULT_BEHAVIOR_METRIC_STATS,
  }))
  useEffect(() => {
    try {
      setMetricStats(parseBehaviorMetricStats(localStorage.getItem(TC_TREND_STAT)))
    } catch {
      /* 隐私模式等不可写场景忽略 */
    }
  }, [])
  const onMetricStat = (key: BehaviorMetricKey, s: MetricStatKey): void => {
    const next = { ...metricStats, [key]: s }
    setMetricStats(next)
    try {
      localStorage.setItem(TC_TREND_STAT, JSON.stringify(next))
    } catch {
      /* ignore */
    }
  }
  const [configOpen, setConfigOpen] = useState(false)
  /** 放大浮窗的目标（null = 关着）。统计值曲线只在行为指标上出现。 */
  const [zoom, setZoom] = useState<ZoomTarget | null>(null)
  /** 打开放大浮窗：顺手清掉共享 hover（浮窗盖住图后，准星/读数不再留在底下）。 */
  const openZoom = (t: ZoomTarget): void => {
    setHoverIter(null)
    setZoom(t)
  }

  // 「比较课程」弹窗开关（弹窗自持数据与轮询；Hero 只负责入口与透传节奏）。
  const [compareOpen, setCompareOpen] = useState(false)

  if (!head) {
    return (
      <section className="tc-hero" aria-label="训练状态">
        <div className="tc-hero__right">
          <div className="tc-tcell">
            <span className="tc-tcell__hd">
              <span className="tc-tcell__lbl">采样胜率</span>
              <b>—</b>
            </span>
            <span className="tc-muted tc-small">
              {stateView && stateView.metrics.available === false
                ? '该课程暂无迭代记录'
                : '等待最新迭代…'}
            </span>
          </div>
          <button type="button" className="tc-link" onClick={onMore}>
            完整指标表 ›
          </button>
          {/* 当前课没迭代也能比**别的**课（历史课有账本）⇒ 空态分支同样给入口 */}
          <button
            type="button"
            className="tc-btn tc-btn--sm"
            title="把多门课的同一指标叠在一张大图上（默认在训课；含历史课，最多 8 门）"
            onClick={() => setCompareOpen(true)}
          >
            比较课程
          </button>
        </div>
        <CompareTrendsModal
          open={compareOpen}
          stateView={stateView}
          refreshSec={refreshSec}
          onClose={() => setCompareOpen(false)}
        />
      </section>
    )
  }

  return (
    <section className="tc-hero" aria-label="训练状态">
      <div className="tc-hero__right">
        {/* 控制条：左 = 看哪条口径（数据源），右 = 看多少轮（范围）。两个问题独立，各一个档位。 */}
        <div className="tc-trendctl">
          <SegmentedControl<TrendSource>
            value={source}
            ariaLabel="走势数据源"
            options={TREND_SOURCE_OPTIONS}
            onChange={onSource}
          />
          <div className="tc-trend-range" role="group" aria-label="走势范围">
            {TREND_RANGE_OPTIONS.map((r) => (
              <button
                key={r.value}
                type="button"
                className={`tc-trend-range__btn${range === r.value ? ' tc-trend-range__btn--on' : ''}`}
                aria-pressed={range === r.value}
                onClick={() => onRange(r.value)}
              >
                {r.label}
              </button>
            ))}
          </div>
          <span className="tc-trendctl__end">
            <button
              type="button"
              className="tc-btn tc-btn--sm"
              aria-haspopup="dialog"
              aria-expanded={configOpen}
              title="选择显示哪些走势图，以及行为指标的统计值"
              onClick={() => setConfigOpen(true)}
            >
              走势配置
            </button>
            <button
              type="button"
              className="tc-btn tc-btn--sm"
              title="把多门课的同一指标叠在一张大图上（默认在训课；含历史课，最多 8 门）"
              onClick={() => setCompareOpen(true)}
            >
              比较课程
            </button>
          </span>
        </div>
        {evalBanner ? (
          <EvalBanner banner={evalBanner} onDismiss={() => setEvalBanner(null)} />
        ) : null}
        <div className="tc-trends">
          {/* 行1：胜率 / 承伤·杀 / 胜局耗时（与击杀对调，2026-09-18） */}
          {visibility.winRate ? (
            <TrendCell
              series={winSeries}
              seriesEval={evalSeries}
              source={source}
              fmt={fmtPct}
              toneOf={winTone}
              range={range}
              hoverIter={hoverIter}
              onHoverIter={setHoverIter}
              yFloor={0.3}
              title="rollout 采样胜率 + eval 胜率（橙色）；数据源档位可只看一边"
              onExpand={(main, overlay) =>
                openZoom({
                  title: '胜率',
                  series: main,
                  series2: overlay,
                  fmt: fmtPct,
                  color: source === 'eval' ? COLOR_EVAL : undefined,
                  yFloor: 0.3,
                })
              }
            />
          ) : null}
          {visibility.dmgPerKill ? (
            <TrendCell
              series={dmgPerKillSeries}
              seriesEval={evalDmgPerKillSeries}
              source={source}
              fmt={fmtPct}
              range={range}
              hoverIter={hoverIter}
              onHoverIter={setHoverIter}
              yFloor={0}
              title="每杀承伤 / (命数×满血)（rollout 实线 · eval 橙点）；越小越会周旋"
              onExpand={(main, overlay) =>
                openZoom({
                  title: '承伤/杀',
                  series: main,
                  series2: overlay,
                  fmt: fmtPct,
                  color: source === 'eval' ? COLOR_EVAL : undefined,
                  yFloor: 0,
                })
              }
            />
          ) : null}
          {visibility.winTicks ? (
            <TrendCell
              series={winTicksSeries}
              seriesEval={evalWinTicksSeries}
              source={source}
              fmt={fmtInt}
              range={range}
              hoverIter={hoverIter}
              onHoverIter={setHoverIter}
              yMin={winTicksYMin(winTicksSeries, evalWinTicksSeries)}
              title="胜局平均耗时（ticks；rollout 实线 · eval 橙点）；y 下界 = max(0, min×0.8, max×0.5)"
              onExpand={(main, overlay) =>
                openZoom({
                  title: '胜局耗时',
                  series: main,
                  series2: overlay,
                  fmt: fmtInt,
                  color: source === 'eval' ? COLOR_EVAL : undefined,
                  yMin: winTicksYMin(winTicksSeries, evalWinTicksSeries),
                })
              }
            />
          ) : null}
          {/* 行2：击杀 / 胜局残血 / 道具（纵坐标可切 绝对值 ↔ 百分比） */}
          {visibility.kills ? (
            <TrendCell
              series={scales.kills === 'pct' ? killsPctSeries : killsAbsSeries}
              seriesEval={scales.kills === 'pct' ? evalKillsPctSeries : evalKillsAbsSeries}
              source={source}
              fmt={scales.kills === 'pct' ? fmtPct : fmtDec1}
              range={range}
              hoverIter={hoverIter}
              onHoverIter={setHoverIter}
              yFloor={0}
              scale={scales.kills}
              onScale={(s) => onScale('kills', s)}
              title={
                scales.kills === 'pct'
                  ? '歼灭率 = Σ击杀 / Σ关卡敌数（rollout 实线 · eval 橙点）'
                  : '每局平均击杀（rollout 实线 · eval 橙点）'
              }
              onExpand={(main, overlay) =>
                openZoom({
                  title: scales.kills === 'pct' ? '击杀（歼灭率）' : '击杀（每局平均）',
                  series: main,
                  series2: overlay,
                  fmt: scales.kills === 'pct' ? fmtPct : fmtDec1,
                  color: source === 'eval' ? COLOR_EVAL : undefined,
                  yFloor: 0,
                })
              }
            />
          ) : null}
          {visibility.winHp ? (
            <TrendCell
              series={scales.winHp === 'pct' ? winHpSeries : winHpAbsSeries}
              seriesEval={scales.winHp === 'pct' ? evalWinHpSeries : evalWinHpAbsSeries}
              source={source}
              fmt={scales.winHp === 'pct' ? fmtPct : fmtInt}
              range={range}
              hoverIter={hoverIter}
              onHoverIter={setHoverIter}
              yFloor={0}
              scale={scales.winHp}
              onScale={(s) => onScale('winHp', s)}
              title={
                scales.winHp === 'pct'
                  ? '胜局剩余总血量 / 单命满血（三命全在 = 300%；rollout 实线 · eval 橙点）'
                  : '胜局平均剩余 HP（剩余命每命计满额；rollout 实线 · eval 橙点）'
              }
              onExpand={(main, overlay) =>
                openZoom({
                  title: scales.winHp === 'pct' ? '胜局残血（百分比）' : '胜局残血（HP）',
                  series: main,
                  series2: overlay,
                  fmt: scales.winHp === 'pct' ? fmtPct : fmtInt,
                  color: source === 'eval' ? COLOR_EVAL : undefined,
                  yFloor: 0,
                })
              }
            />
          ) : null}
          {visibility.pu ? (
            <TrendCell
              series={scales.pu === 'pct' ? puPctSeries : puSeries}
              seriesEval={scales.pu === 'pct' ? evalPuPctSeries : evalPuSeries}
              source={source}
              fmt={scales.pu === 'pct' ? fmtPct : fmtDec2}
              range={range}
              hoverIter={hoverIter}
              onHoverIter={setHoverIter}
              yFloor={0}
              scale={scales.pu}
              onScale={(s) => onScale('pu', s)}
              title={
                scales.pu === 'pct'
                  ? '拾取率 = Σ拾取 / Σ掉落（rollout 实线 · eval 橙点）'
                  : '每局平均拾取数（rollout 实线 · eval 橙点）'
              }
              onExpand={(main, overlay) =>
                openZoom({
                  title: scales.pu === 'pct' ? '道具（拾取率）' : '道具（每局平均）',
                  series: main,
                  series2: overlay,
                  fmt: scales.pu === 'pct' ? fmtPct : fmtDec2,
                  color: source === 'eval' ? COLOR_EVAL : undefined,
                  yFloor: 0,
                })
              }
            />
          ) : null}
          {BEHAVIOR_METRICS.filter((m) => visibility[behaviorTrendKey(m.key)]).map((m) => {
            const statKey = metricStats[m.key]
            const rollout = behaviorMetricSeries(iters, m.key, statKey, 'rollout')
            const evalSeries = behaviorMetricSeries(iters, m.key, statKey, 'eval')
            const fmt = (v: number | null): string => (v == null ? '—' : fmtMetricStat(v, m))
            return (
              <TrendCell
                key={m.key}
                series={rollout}
                seriesEval={evalSeries}
                source={source}
                fmt={fmt}
                range={range}
                hoverIter={hoverIter}
                onHoverIter={setHoverIter}
                yFloor={0}
                title={`逐局统计（${METRIC_STAT_LABEL[statKey]}；rollout 实线 · eval 橙点）· ${m.title}`}
                onExpand={() =>
                  openZoom({
                    title: `${m.label}·${METRIC_STAT_LABEL[statKey]}`,
                    fmt,
                    behavior: { key: m.key, iters, stat: statKey, source },
                  })
                }
              />
            )
          })}
          {!Object.values(visibility).some(Boolean) ? (
            <p className="tc-muted tc-small">走势图已全部隐藏；点「走势配置」重新显示。</p>
          ) : null}
        </div>
        <PairedRefereeLine ref={stateView?.metrics.pairedReferee} />
      </div>
      <LastIters
        iters={iters}
        onMore={onMore}
        course={course}
        ckpts={ckpts}
        readOnly={readOnly}
        onEvalQueued={onEvalQueued}
        busyIters={busyIters}
      />
      <CompareTrendsModal
        open={compareOpen}
        stateView={stateView}
        refreshSec={refreshSec}
        onClose={() => setCompareOpen(false)}
      />
      <TrendConfigModal
        open={configOpen}
        visibility={visibility}
        metricStats={metricStats}
        onToggleChart={onToggleChart}
        onMetricStat={onMetricStat}
        onClose={() => setConfigOpen(false)}
      />
      {/* 只在打开时挂载：首帧的可见统计量直接由 target 决定，不会先闪一下默认均值。 */}
      {zoom ? <TrendZoomModal target={zoom} range={range} onClose={() => setZoom(null)} /> : null}
    </section>
  )
}
