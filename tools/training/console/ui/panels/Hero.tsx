/** Hero.tsx — 训练状态 hero（一屏焦点）：胜率大数字 + 大走势图；右侧击杀/道具/eval 三格趋势。
 *  数据口径 = /api/state.metrics 最新迭代（latestRow）；「完整指标表 ›」进抽屉。 */

import {
  fmtPct,
  latestRow,
  metricSeries,
  winTone,
  type ConsoleStateView,
  type Series,
} from '../../../ui/view'
import { Sparkline } from '../../../ui/components/Sparkline'

export interface HeroProps {
  stateView: ConsoleStateView | null
  onMore: () => void
}

/** 右侧趋势格：上「标签 + 最新值」下「通栏 sparkline」；数据缺失显示占位。 */
function TrendCell({
  series,
  fmt,
  tone,
}: {
  series: Series | undefined
  fmt: (v: number | null) => string
  tone?: 'g' | 'y' | 'r'
}) {
  const last = series ? (series.vals.filter(Number.isFinite).slice(-1)[0] ?? null) : null
  return (
    <div className="tc-tcell">
      <span className="tc-tcell__hd">
        <span className="tc-tcell__lbl">{series ? series.label : '—'}</span>
        <b className={tone ? `tc-mtrend__val--${tone}` : undefined}>{fmt(last)}</b>
      </span>
      {series ? <Sparkline values={series.vals} width={120} height={26} /> : null}
    </div>
  )
}

export function Hero({ stateView, onMore }: HeroProps) {
  const iters = stateView?.metrics.iters ?? []
  const head = latestRow(iters)
  const series = metricSeries(iters)
  const winSeries = series.find((m) => m.key === 'winRate')
  const killsSeries = series.find((m) => m.key === 'kills')
  const puSeries = series.find((m) => m.key === 'pu')
  const evalSeries = series.find((m) => m.key === 'eval')

  if (!head) {
    return (
      <section className="tc-hero" aria-label="训练状态">
        <div className="tc-hero__kpi">
          <span className="tc-hero__lbl">采样胜率</span>
          <span className="tc-hero__val">—</span>
          <span className="tc-hero__sub">
            {stateView && stateView.metrics.available === false
              ? '该课程暂无迭代记录'
              : '等待最新迭代…'}
          </span>
        </div>
        <div className="tc-hero__right" style={{ alignContent: 'end' }}>
          <button type="button" className="tc-link" onClick={onMore}>
            完整指标表 ›
          </button>
        </div>
      </section>
    )
  }

  const winVal = fmtPct(head.winRate)
  const tone = winTone(head.winRate) === 'g' ? 'ok' : 'danger'
  const killsTxt = head.actuals ? `${head.actuals.totalKills}/${head.actuals.games}局` : '—'
  const evalTxt =
    head.evalData && head.evalData.winRate !== null ? `eval ${fmtPct(head.evalData.winRate)}` : null

  return (
    <section className="tc-hero" aria-label="训练状态">
      <div className="tc-hero__kpi">
        <span className="tc-hero__lbl">采样胜率 · it{head.iter}</span>
        <span className={`tc-hero__val tc-hero__val--${tone}`}>{winVal}</span>
        {winSeries ? <Sparkline values={winSeries.vals} width={170} height={30} /> : null}
        <span className="tc-hero__sub">
          击杀 {killsTxt}
          {evalTxt ? ` · ${evalTxt}` : ''}
        </span>
      </div>
      <div className="tc-hero__right">
        <div className="tc-trends">
          <TrendCell series={killsSeries} fmt={(v) => (v != null ? `${v.toFixed(0)}` : '—')} />
          <TrendCell series={puSeries} fmt={(v) => (v != null ? `${v.toFixed(0)}` : '—')} />
          <TrendCell
            series={evalSeries}
            tone={winTone(evalSeries?.vals.filter(Number.isFinite).slice(-1)[0] ?? 0)}
            fmt={fmtPct}
          />
        </div>
        <button type="button" className="tc-link tc-hero__more" onClick={onMore}>
          完整指标表 ›
        </button>
      </div>
    </section>
  )
}
