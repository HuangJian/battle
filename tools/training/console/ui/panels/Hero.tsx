/** Hero.tsx — 训练状态 hero（一屏焦点）：胜率大数字 + 趋势 sparkline + 最近 5 轮迷你条。
 *  数据口径 = /api/state.metrics 最新迭代（latestRow）；「完整指标表 ›」进抽屉。 */

import {
  fmtPct,
  fmtValue,
  latestRow,
  metricSeries,
  winTone,
  type ConsoleStateView,
} from '../../../ui/view'
import { Sparkline } from '../../../ui/components/Sparkline'

export interface HeroProps {
  stateView: ConsoleStateView | null
  onMore: () => void
}

export function Hero({ stateView, onMore }: HeroProps) {
  const iters = stateView?.metrics.iters ?? []
  const head = latestRow(iters)
  const series = metricSeries(iters)
  const winSeries = series.find((m) => m.key === 'winRate')

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
  const chrono = [...iters].sort((a, b) => a.iter - b.iter)
  const last5 = chrono.slice(-5)
  const maxWin = Math.max(...last5.map((r) => r.winRate), 0.0001)
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
        {last5.map((r) => {
          const isNow = r.iter === head.iter
          const pct = Math.max(2, Math.round((r.winRate / maxWin) * 100))
          return (
            <div key={r.iter} className={`tc-mini${isNow ? ' tc-mini--now' : ''}`}>
              <span>it{r.iter}</span>
              <span className="tc-mini__bar">
                <i style={{ width: `${pct}%` }} />
              </span>
              <b>{fmtValue(r.winRate)}</b>
            </div>
          )
        })}
        <div style={{ justifySelf: 'end' }}>
          <button type="button" className="tc-link" onClick={onMore}>
            完整指标表 ›
          </button>
        </div>
      </div>
    </section>
  )
}
